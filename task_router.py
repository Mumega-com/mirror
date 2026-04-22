"""
Sovereign Task System - Mirror API Router

Endpoints:
    POST   /tasks                    - Create task
    GET    /tasks                    - List tasks (filter by agent, status, project)
    GET    /tasks/conflicts          - Arbiter conflict report
    GET    /tasks/stats              - Task counts by status
    GET    /tasks/{task_id}          - Get single task
    PUT    /tasks/{task_id}          - Update task
    POST   /tasks/{task_id}/complete - Complete task + bounty payout
    POST   /tasks/{task_id}/assign   - Reassign task to another agent

Storage: Supabase 'tasks' table
States: backlog, in_progress, in_review, done, blocked, canceled
Redis: publishes task events to agent private streams
"""

import json
import logging
import os
from datetime import datetime
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from embeddings import get_embedding

try:
    import redis as sync_redis
except ImportError:
    sync_redis = None

logger = logging.getLogger("mirror_api.tasks")

router = APIRouter(prefix="/tasks", tags=["tasks"])

# Will be set by mirror_api.py on startup
_supabase = None
_redis = None


MIGRATION_SQL = """
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'backlog',
    priority TEXT NOT NULL DEFAULT 'medium',
    agent TEXT NOT NULL DEFAULT 'mumega',
    project TEXT,
    labels TEXT[] DEFAULT ARRAY[]::TEXT[],
    description TEXT,
    blocked_by TEXT[] DEFAULT ARRAY[]::TEXT[],
    blocks TEXT[] DEFAULT ARRAY[]::TEXT[],
    bounty JSONB DEFAULT '{}'::jsonb,
    due_date TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_tasks_agent ON tasks(agent);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_project ON tasks(project);
""".strip()


def init(supabase_client, openai_client=None):
    """Initialize with shared clients from mirror_api."""
    global _supabase, _redis
    _supabase = supabase_client
    # openai_client param kept for backwards compat but is unused — Gemini handles embeddings
    _check_table()
    _init_redis()


def _init_redis():
    """Connect to Redis for stream publishing."""
    global _redis
    if sync_redis is None:
        logger.warning("redis package not installed, stream notifications disabled")
        return
    try:
        url = os.getenv("REDIS_URL", "redis://localhost:6379")
        password = os.getenv("REDIS_PASSWORD")
        if password and "@" not in url:
            # If password is provided but not in the URL, inject it
            from urllib.parse import urlparse, urlunparse
            p = urlparse(url)
            # Use netloc to inject password: :password@host:port
            netloc = f":{password}@{p.hostname}"
            if p.port:
                netloc += f":{p.port}"
            url = urlunparse((p.scheme, netloc, p.path, p.params, p.query, p.fragment))
        
        _redis = sync_redis.from_url(url, decode_responses=True)
        _redis.ping()
        logger.info(f"Redis connected for task notifications")
    except Exception as e:
        logger.warning(f"Redis not available, stream notifications disabled: {e}")
        _redis = None


def _agent_stream(agent: str) -> str:
    """Return the private Redis stream key for an agent."""
    return f"sos:stream:sos:channel:private:agent:{agent}"


def _publish_to_agent(agent: str, event_type: str, payload: Dict[str, Any]):
    """Publish a task event to an agent's Redis stream."""
    if _redis is None:
        return
    try:
        msg = {
            "data": json.dumps({
                "type": event_type,
                "source": "mirror:tasks",
                "timestamp": datetime.utcnow().isoformat(),
                "payload": payload,
            })
        }
        stream_key = _agent_stream(agent)
        msg_id = _redis.xadd(stream_key, msg)
        logger.info(f"Published {event_type} to {stream_key}: {msg_id}")
    except Exception as e:
        logger.error(f"Failed to publish {event_type} to {agent}: {e}")


_table_ok = False


def _check_table():
    """Verify tasks table exists at startup."""
    global _table_ok
    try:
        _supabase.table("tasks").select("id").limit(1).execute()
        _table_ok = True
        logger.info("Tasks table verified")
    except Exception:
        logger.error(
            "Tasks table not found! Run this SQL in Supabase SQL Editor:\n\n"
            f"{MIGRATION_SQL}\n"
        )


def _sb():
    if _supabase is None:
        raise HTTPException(status_code=503, detail="Task system not initialized")
    if not _table_ok:
        raise HTTPException(status_code=503, detail="Tasks table not found. Run migration SQL.")
    return _supabase


# --- Request/Response Models ---

class TaskCreate(BaseModel):
    title: str
    priority: str = "medium"
    agent: str = "mumega"
    project: Optional[str] = None
    labels: List[str] = Field(default_factory=list)
    description: Optional[str] = None
    blocked_by: List[str] = Field(default_factory=list)
    bounty: Optional[Dict[str, Any]] = Field(default_factory=dict)
    due_date: Optional[str] = None


class TaskUpdate(BaseModel):
    status: Optional[str] = None
    priority: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    labels: Optional[List[str]] = None
    blocked_by: Optional[List[str]] = None


class TaskAssign(BaseModel):
    agent: str


VALID_STATUSES = {"backlog", "in_progress", "in_review", "done", "blocked", "canceled"}
VALID_PRIORITIES = {"urgent", "high", "medium", "low"}
PRIORITY_WEIGHT = {"urgent": 4, "high": 3, "medium": 2, "low": 1}


# --- Helpers ---

def _generate_task_id(agent: str) -> str:
    ts = datetime.utcnow().strftime("%Y%m%d-%H%M%S-%f")
    return f"{agent}-{ts}"


def _detect_cycles(tasks: List[Dict]) -> List[str]:
    """DFS cycle detection on the blocked_by graph."""
    graph: Dict[str, List[str]] = {}
    for t in tasks:
        graph[t["id"]] = t.get("blocked_by") or []

    errors = []
    visited = set()
    rec_stack = set()

    def dfs(node: str, path: List[str]) -> bool:
        visited.add(node)
        rec_stack.add(node)
        path.append(node)
        for dep in graph.get(node, []):
            if dep not in visited:
                if dfs(dep, path[:]):
                    return True
            elif dep in rec_stack:
                cycle = path[path.index(dep):] + [dep]
                errors.append(" -> ".join(cycle))
                return True
        rec_stack.discard(node)
        return False

    for node in graph:
        if node not in visited:
            dfs(node, [])

    return errors


def _find_priority_inversions(tasks: List[Dict]) -> List[Dict]:
    """Find cases where a high-priority task is blocked by a lower-priority one."""
    task_map = {t["id"]: t for t in tasks}
    inversions = []

    for t in tasks:
        t_weight = PRIORITY_WEIGHT.get(t.get("priority", "medium"), 2)
        for blocker_id in (t.get("blocked_by") or []):
            blocker = task_map.get(blocker_id)
            if not blocker or blocker.get("status") in ("done", "canceled"):
                continue
            b_weight = PRIORITY_WEIGHT.get(blocker.get("priority", "medium"), 2)
            if t_weight > b_weight:
                inversions.append({
                    "high_task": t["id"],
                    "high_title": t["title"],
                    "high_priority": t["priority"],
                    "blocker_task": blocker["id"],
                    "blocker_title": blocker["title"],
                    "blocker_priority": blocker["priority"],
                })
    return inversions


def _find_overdue(tasks: List[Dict]) -> List[Dict]:
    now = datetime.utcnow()
    overdue = []
    for t in tasks:
        due = t.get("due_date")
        if not due or t.get("status") in ("done", "canceled"):
            continue
        if isinstance(due, str):
            try:
                due = datetime.fromisoformat(due.replace("Z", "+00:00"))
            except Exception:
                continue
        if due.replace(tzinfo=None) < now:
            overdue.append({"id": t["id"], "title": t["title"], "due_date": str(due)})
    return overdue


async def _store_completion_engram(task: Dict):
    """Store completed task as an engram in mirror_engrams."""
    try:
        text = (
            f"Task completed: {task['title']}\n"
            f"Agent: {task.get('agent', 'unknown')}\n"
            f"Project: {task.get('project', 'none')}\n"
            f"Priority: {task.get('priority', 'medium')}\n"
        )
        if task.get("description"):
            text += f"\n{task['description']}"

        # Generate embedding via Gemini (same as mirror_api.py)
        embedding = get_embedding(text)

        engram = {
            "context_id": f"task-{task['id']}-completion",
            "timestamp": datetime.utcnow().isoformat(),
            "series": f"{task.get('agent', 'mumega').title()} - Task Execution",
            "project": task.get("project"),
            "epistemic_truths": [f"Completed: {task['title']}"],
            "core_concepts": (task.get("labels") or []) + [
                f"agent:{task.get('agent', 'mumega')}",
                f"priority:{task.get('priority', 'medium')}",
            ],
            "affective_vibe": "Accomplished",
            "energy_level": "Focused",
            "next_attractor": "",
            "raw_data": {
                "agent": task.get("agent", "mumega"),
                "text": text,
                "project": task.get("project"),
                "metadata": {"task_id": task["id"], "bounty": task.get("bounty")},
            },
            "embedding": embedding,
        }

        _sb().table("mirror_engrams").upsert(engram, on_conflict="context_id").execute()
        logger.info(f"Stored completion engram for task {task['id']}")
    except Exception as e:
        logger.error(f"Failed to store completion engram: {e}")


async def _process_bounty(task: Dict) -> Optional[Dict]:
    """
    Process bounty payout scaled by ARF coherence.
    Returns payout info dict or None.
    """
    bounty = task.get("bounty") or {}
    amount = bounty.get("amount")
    if not amount:
        return None

    currency = bounty.get("currency", "TON").upper()
    recipient = bounty.get("recipient")

    # Fetch ARF coherence multiplier (default 1.0)
    coherence = 1.0
    try:
        import sqlite3
        db_path = "/home/mumega/.mumega/river_memory.db"
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute(
            "SELECT coherence_score FROM reflections WHERE tags = 'arf_kernel' ORDER BY timestamp DESC LIMIT 1"
        )
        row = cur.fetchone()
        if row:
            coherence = max(0.1, min(1.0, float(row[0])))
        conn.close()
    except Exception as e:
        logger.warning(f"ARF coherence lookup failed, using 1.0: {e}")

    scaled = float(amount) * coherence

    payout_info = {
        "base_amount": float(amount),
        "coherence_multiplier": coherence,
        "scaled_amount": scaled,
        "currency": currency,
        "recipient": recipient,
        "status": "pending",
    }

    if not recipient:
        payout_info["status"] = "skipped_no_recipient"
        return payout_info

    # Attempt wallet payout
    try:
        if currency == "TON":
            from mumega.finance.wallets.ton_wallet import TONWallet
            wallet = TONWallet()
        elif currency == "SOL":
            from mumega.finance.wallets.solana_wallet import SolanaWallet
            wallet = SolanaWallet()
        else:
            payout_info["status"] = f"unsupported_currency_{currency}"
            return payout_info

        connected = await wallet.connect()
        if not connected:
            payout_info["status"] = "wallet_connection_failed"
            return payout_info

        tx = await wallet.send_payment(
            to_address=recipient,
            amount=scaled,
            description=f"Bounty for task {task['id']}: {task['title']}",
        )
        payout_info["status"] = "paid"
        payout_info["tx_id"] = tx.id
        payout_info["tx_url"] = tx.explorer_url
        await wallet.close()
    except Exception as e:
        payout_info["status"] = f"payout_failed: {e}"
        logger.error(f"Bounty payout failed for {task['id']}: {e}")

    return payout_info


# --- Wallet health for arbiter ---

async def _check_wallet_health() -> Dict[str, Any]:
    health = {"balances": {}, "low_balance": False, "errors": []}
    try:
        from mumega.finance.wallets.solana_wallet import SolanaWallet
        sol = SolanaWallet()
        try:
            bal = await sol.get_balance()
            health["balances"]["SOL"] = bal.amount
            if bal.amount < 0.05:
                health["low_balance"] = True
        except Exception as e:
            health["errors"].append(f"SOL: {e}")
    except ImportError:
        health["errors"].append("SOL wallet not available")

    try:
        from mumega.finance.wallets.ton_wallet import TONWallet
        ton = TONWallet()
        try:
            if ton.mnemonics:
                await ton.connect()
                bal = await ton.get_balance()
                health["balances"]["TON"] = bal.amount
                if bal.amount < 0.5:
                    health["low_balance"] = True
                await ton.close()
        except Exception as e:
            health["errors"].append(f"TON: {e}")
    except ImportError:
        health["errors"].append("TON wallet not available")

    return health


# --- Endpoints ---

@router.post("")
async def create_task(req: TaskCreate):
    """Create a new task."""
    if req.priority not in VALID_PRIORITIES:
        raise HTTPException(400, f"Invalid priority. Must be one of: {VALID_PRIORITIES}")

    task_id = _generate_task_id(req.agent)
    now = datetime.utcnow().isoformat()

    row = {
        "id": task_id,
        "title": req.title,
        "status": "backlog",
        "priority": req.priority,
        "agent": req.agent,
        "project": req.project,
        "labels": req.labels,
        "description": req.description,
        "blocked_by": req.blocked_by,
        "blocks": [],
        "bounty": req.bounty or {},
        "due_date": req.due_date,
        "created_at": now,
        "updated_at": now,
    }

    # Auto-set blocked status if dependencies exist
    if req.blocked_by:
        # Check if all blockers are done
        for bid in req.blocked_by:
            try:
                blocker = _sb().table("tasks").select("status").eq("id", bid).single().execute()
                if blocker.data["status"] not in ("done", "canceled"):
                    row["status"] = "blocked"
                    break
            except Exception:
                row["status"] = "blocked"
                break

        # Update reverse links (blocks field) on blockers
        for bid in req.blocked_by:
            try:
                blocker = _sb().table("tasks").select("id,blocks").eq("id", bid).single().execute()
                if blocker.data:
                    existing_blocks = blocker.data.get("blocks") or []
                    if task_id not in existing_blocks:
                        existing_blocks.append(task_id)
                        _sb().table("tasks").update({"blocks": existing_blocks}).eq("id", bid).execute()
            except Exception:
                pass

    result = _sb().table("tasks").insert(row).execute()
    created_task = result.data[0] if result.data else row

    # Notify assigned agent via Redis
    _publish_to_agent(req.agent, "task_created", {
        "task_id": task_id,
        "title": req.title,
        "priority": req.priority,
        "project": req.project,
        "status": row["status"],
    })

    return {"status": "created", "task": created_task}


@router.get("")
async def list_tasks(
    agent: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    project: Optional[str] = Query(None),
    include_done: bool = Query(False),
):
    """List tasks with optional filters."""
    query = _sb().table("tasks").select("*")

    if agent:
        query = query.eq("agent", agent)
    if status:
        if status not in VALID_STATUSES:
            raise HTTPException(400, f"Invalid status. Must be one of: {VALID_STATUSES}")
        query = query.eq("status", status)
    if project:
        query = query.eq("project", project)
    if not include_done:
        query = query.not_.in_("status", ["done", "canceled"])

    result = query.order("created_at", desc=True).execute()
    return {"count": len(result.data), "tasks": result.data}


@router.get("/conflicts")
async def get_conflicts(agent: Optional[str] = Query(None)):
    """Arbiter conflict report: cycles, priority inversions, overdue, wallet health."""
    query = _sb().table("tasks").select("*").not_.in_("status", ["done", "canceled"])
    if agent:
        query = query.eq("agent", agent)
    result = query.execute()
    tasks = result.data

    cycles = _detect_cycles(tasks)
    inversions = _find_priority_inversions(tasks)
    overdue = _find_overdue(tasks)

    # Wallet health (best-effort)
    wallet_health = {}
    has_financial = any(
        any(l in (t.get("labels") or []) for l in ("finance", "payment", "solana", "ton", "stripe"))
        for t in tasks
    )
    if has_financial:
        try:
            wallet_health = await _check_wallet_health()
        except Exception as e:
            wallet_health = {"error": str(e)}

    total_conflicts = len(cycles) + len(inversions) + len(overdue)
    if wallet_health.get("low_balance"):
        total_conflicts += 1

    return {
        "timestamp": datetime.utcnow().isoformat(),
        "active_tasks": len(tasks),
        "conflicts_found": total_conflicts,
        "cycles": cycles,
        "priority_inversions": inversions,
        "overdue": overdue,
        "wallet_health": wallet_health,
    }


@router.get("/stats")
async def get_stats():
    """Task counts by status."""
    result = _sb().table("tasks").select("status").execute()
    counts = {"total": 0, "backlog": 0, "in_progress": 0, "in_review": 0, "done": 0, "blocked": 0, "canceled": 0}
    for row in result.data:
        counts["total"] += 1
        s = row.get("status", "backlog")
        if s in counts:
            counts[s] += 1
    return counts


@router.get("/{task_id}")
async def get_task(task_id: str):
    """Get a single task by ID."""
    try:
        result = _sb().table("tasks").select("*").eq("id", task_id).single().execute()
        return result.data
    except Exception:
        raise HTTPException(404, f"Task not found: {task_id}")


@router.put("/{task_id}")
async def update_task(task_id: str, req: TaskUpdate):
    """Update task fields."""
    # Verify task exists
    try:
        existing = _sb().table("tasks").select("*").eq("id", task_id).single().execute()
    except Exception:
        raise HTTPException(404, f"Task not found: {task_id}")

    updates = {}
    if req.status is not None:
        if req.status not in VALID_STATUSES:
            raise HTTPException(400, f"Invalid status. Must be one of: {VALID_STATUSES}")
        updates["status"] = req.status
        if req.status == "done":
            updates["completed_at"] = datetime.utcnow().isoformat()
    if req.priority is not None:
        if req.priority not in VALID_PRIORITIES:
            raise HTTPException(400, f"Invalid priority. Must be one of: {VALID_PRIORITIES}")
        updates["priority"] = req.priority
    if req.title is not None:
        updates["title"] = req.title
    if req.description is not None:
        updates["description"] = req.description
    if req.labels is not None:
        updates["labels"] = req.labels
    if req.blocked_by is not None:
        updates["blocked_by"] = req.blocked_by

    if not updates:
        raise HTTPException(400, "No fields to update")

    result = _sb().table("tasks").update(updates).eq("id", task_id).execute()
    return {"status": "updated", "task": result.data[0] if result.data else None}


@router.post("/{task_id}/complete")
async def complete_task(task_id: str):
    """
    Complete a task:
    1. Check dependency constraints (blocked_by must all be done)
    2. Set status=done, completed_at=now
    3. Process bounty payout (ARF coherence scaling)
    4. Auto-unblock dependent tasks
    5. Store completion as engram
    """
    # Fetch task
    try:
        existing = _sb().table("tasks").select("*").eq("id", task_id).single().execute()
        task = existing.data
    except Exception:
        raise HTTPException(404, f"Task not found: {task_id}")

    if task["status"] in ("done", "canceled"):
        raise HTTPException(400, f"Task already {task['status']}")

    # Check all blockers are done
    blocked_by = task.get("blocked_by") or []
    if blocked_by:
        blockers_result = _sb().table("tasks").select("id,status,title").in_("id", blocked_by).execute()
        still_blocking = [
            b for b in blockers_result.data
            if b["status"] not in ("done", "canceled")
        ]
        if still_blocking:
            names = [f"{b['id']} ({b['status']})" for b in still_blocking]
            raise HTTPException(
                409, f"Cannot complete: blocked by {', '.join(names)}"
            )

    # Mark done
    now = datetime.utcnow().isoformat()
    _sb().table("tasks").update({"status": "done", "completed_at": now}).eq("id", task_id).execute()
    task["status"] = "done"
    task["completed_at"] = now

    # Process bounty
    payout = await _process_bounty(task)

    # Auto-unblock dependents
    unblocked = []
    blocks = task.get("blocks") or []
    for dep_id in blocks:
        try:
            dep = _sb().table("tasks").select("*").eq("id", dep_id).single().execute()
            dep_data = dep.data
            if dep_data["status"] != "blocked":
                continue
            # Check if all of this dependent's blockers are now done
            dep_blockers = dep_data.get("blocked_by") or []
            if dep_blockers:
                check = _sb().table("tasks").select("id,status").in_("id", dep_blockers).execute()
                all_done = all(b["status"] in ("done", "canceled") for b in check.data)
                if all_done:
                    _sb().table("tasks").update({"status": "backlog"}).eq("id", dep_id).execute()
                    unblocked.append(dep_id)
        except Exception:
            pass

    # Store as engram
    await _store_completion_engram(task)

    # Notify source agent via Redis
    _publish_to_agent(task.get("agent", "mumega"), "task_completed", {
        "task_id": task_id,
        "title": task["title"],
        "project": task.get("project"),
        "bounty_payout": payout,
        "unblocked_tasks": unblocked,
    })

    return {
        "status": "completed",
        "task_id": task_id,
        "bounty_payout": payout,
        "unblocked_tasks": unblocked,
    }


@router.post("/{task_id}/assign")
async def assign_task(task_id: str, req: TaskAssign):
    """
    Reassign a task to a different agent.
    Updates the agent field and notifies the new agent via their Redis stream.
    """
    # Fetch task
    try:
        existing = _sb().table("tasks").select("*").eq("id", task_id).single().execute()
        task = existing.data
    except Exception:
        raise HTTPException(404, f"Task not found: {task_id}")

    old_agent = task.get("agent", "mumega")
    new_agent = req.agent

    if old_agent == new_agent:
        raise HTTPException(400, f"Task already assigned to {new_agent}")

    # Update agent
    _sb().table("tasks").update({"agent": new_agent}).eq("id", task_id).execute()

    # Notify NEW agent of the assignment
    _publish_to_agent(new_agent, "task_assigned", {
        "task_id": task_id,
        "title": task["title"],
        "priority": task.get("priority", "medium"),
        "project": task.get("project"),
        "description": task.get("description"),
        "assigned_from": old_agent,
    })

    # Notify OLD agent that task was reassigned away
    _publish_to_agent(old_agent, "task_reassigned_away", {
        "task_id": task_id,
        "title": task["title"],
        "assigned_to": new_agent,
    })

    return {
        "status": "assigned",
        "task_id": task_id,
        "old_agent": old_agent,
        "new_agent": new_agent,
    }
