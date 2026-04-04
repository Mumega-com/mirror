#!/usr/bin/env python3
"""
GitHub Issues Sync — Mirror ↔ GitHub

Mirror is the brain. GitHub is the window.
Two-way sync between Mirror's sovereign task table and GitHub Issues.

Endpoints:
    POST /github-sync              - Full bidirectional sync cycle
    POST /github-sync/push/{id}    - Push one Mirror task → GitHub issue
    POST /github-sync/pull         - Pull GitHub issues → Mirror (create missing tasks)
    GET  /github-sync/status       - Sync status and stats
    POST /github-webhook           - Receive GitHub issue events (webhook)

Environment:
    GITHUB_TOKEN      Personal access token or oauth token (gho_*)
    GITHUB_REPO       Default repo (owner/repo), e.g. "servathadi/mission-control"
    GITHUB_WEBHOOK_SECRET  HMAC secret for webhook verification
"""

import hashlib
import hmac
import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Request, Query
from pydantic import BaseModel

logger = logging.getLogger("mirror_api.github_sync")

router = APIRouter(prefix="", tags=["github"])

# Will be set by mirror_api.py on startup
_supabase = None

def init(supabase_client):
    global _supabase
    _supabase = supabase_client

def _sb():
    if not _supabase:
        raise HTTPException(500, "Supabase not initialized")
    return _supabase


# ── Config ─────────────────────────────────────────────────────────────────
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
DEFAULT_REPO = os.environ.get("GITHUB_REPO", "servathadi/mission-control")
WEBHOOK_SECRET = os.environ.get("GITHUB_WEBHOOK_SECRET", "")
GH_API = "https://api.github.com"


# ── Status mapping ──────────────────────────────────────────────────────────
# Mirror status → GitHub (state + labels to add/remove)
STATUS_TO_GH = {
    "backlog":     {"state": "open",   "labels": ["backlog"],     "remove": ["in-progress", "in-review", "blocked", "done"]},
    "in_progress": {"state": "open",   "labels": ["in-progress"], "remove": ["backlog", "in-review", "blocked"]},
    "in_review":   {"state": "open",   "labels": ["in-review"],   "remove": ["backlog", "in-progress", "blocked"]},
    "blocked":     {"state": "open",   "labels": ["blocked"],     "remove": ["backlog", "in-progress", "in-review"]},
    "done":        {"state": "closed", "labels": ["done"],        "remove": []},
    "canceled":    {"state": "closed", "labels": ["canceled"],    "remove": []},
}

# Priority → GitHub label
PRIORITY_TO_GH = {
    "urgent": "priority:urgent",
    "high":   "priority:high",
    "medium": "priority:medium",
    "low":    "priority:low",
}

# GitHub → Mirror status (from issue state + labels)
def gh_to_mirror_status(state: str, labels: list[str]) -> str:
    if state == "closed":
        return "done" if "canceled" not in labels else "canceled"
    if "blocked" in labels:
        return "blocked"
    if "in-review" in labels:
        return "in_review"
    if "in-progress" in labels:
        return "in_progress"
    return "backlog"

def gh_to_mirror_priority(labels: list[str]) -> str:
    for label in labels:
        if label == "priority:urgent":
            return "urgent"
        if label == "priority:high":
            return "high"
        if label == "priority:low":
            return "low"
    return "medium"


# ── GitHub API client ───────────────────────────────────────────────────────
def _gh_headers() -> dict:
    token = GITHUB_TOKEN or os.environ.get("GITHUB_TOKEN", "")
    if not token:
        raise HTTPException(500, "GITHUB_TOKEN not configured")
    return {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "mumega-mirror-sync/1.0",
    }


def _gh_get(path: str) -> dict | list:
    url = f"{GH_API}{path}" if path.startswith("/") else path
    resp = httpx.get(url, headers=_gh_headers(), timeout=15)
    if resp.status_code == 404:
        return {}
    resp.raise_for_status()
    return resp.json()


def _gh_post(path: str, body: dict) -> dict:
    url = f"{GH_API}{path}"
    resp = httpx.post(url, headers=_gh_headers(), json=body, timeout=15)
    resp.raise_for_status()
    return resp.json()


def _gh_patch(path: str, body: dict) -> dict:
    url = f"{GH_API}{path}"
    resp = httpx.patch(url, headers=_gh_headers(), json=body, timeout=15)
    resp.raise_for_status()
    return resp.json()


# ── Label Management ─────────────────────────────────────────────────────────
_labels_cache: dict[str, set] = {}  # repo → set of existing label names

def _ensure_labels(repo: str):
    """Create required labels in repo if missing."""
    if repo in _labels_cache:
        return
    owner, name = repo.split("/", 1)
    try:
        existing_raw = _gh_get(f"/repos/{owner}/{name}/labels?per_page=100")
        existing = {l["name"] for l in existing_raw} if isinstance(existing_raw, list) else set()
    except Exception:
        existing = set()
    _labels_cache[repo] = existing

    needed = [
        ("backlog",           "ededed", "Task queued, not started"),
        ("in-progress",       "0075ca", "Currently being worked on"),
        ("in-review",         "e4e669", "Ready for review"),
        ("blocked",           "d93f0b", "Waiting on dependency"),
        ("done",              "0e8a16", "Completed"),
        ("canceled",          "666666", "Canceled"),
        ("priority:urgent",   "b60205", "Urgent — drop everything"),
        ("priority:high",     "e99695", "High priority"),
        ("priority:medium",   "fbca04", "Medium priority"),
        ("priority:low",      "c5def5", "Low priority"),
        ("mirror-task",       "5319e7", "Synced from Mirror task system"),
    ]
    for label_name, color, desc in needed:
        if label_name not in existing:
            try:
                _gh_post(f"/repos/{owner}/{name}/labels", {
                    "name": label_name, "color": color, "description": desc
                })
                _labels_cache[repo].add(label_name)
                logger.info(f"Created label '{label_name}' in {repo}")
            except Exception as e:
                logger.warning(f"Could not create label '{label_name}': {e}")


# ── Issue body builder ────────────────────────────────────────────────────────
def _build_issue_body(task: dict) -> str:
    desc = task.get("description") or ""
    agent = task.get("agent") or "unassigned"
    project = task.get("project") or ""
    task_id = task.get("id", "")
    blocked_by = task.get("blocked_by") or []
    due = task.get("due_date") or ""

    lines = []
    if desc:
        lines.append(desc)
        lines.append("")

    lines += [
        "---",
        f"**Mirror Task ID:** `{task_id}`",
        f"**Agent:** {agent}",
    ]
    if project:
        lines.append(f"**Project:** {project}")
    if due:
        lines.append(f"**Due:** {due}")
    if blocked_by:
        lines.append(f"**Blocked by:** {', '.join(f'`{b}`' for b in blocked_by)}")

    lines += [
        "",
        "_Synced from [Mirror Task System](https://mumega.com) — do not edit the ID line._",
    ]
    return "\n".join(lines)


def _extract_task_id(body: str) -> Optional[str]:
    """Extract Mirror task ID from GitHub issue body."""
    if not body:
        return None
    for line in body.split("\n"):
        if "Mirror Task ID" in line:
            import re
            m = re.search(r"`([^`]+)`", line)
            if m:
                return m.group(1)
    return None


# ── Core sync operations ─────────────────────────────────────────────────────
def push_task_to_github(task: dict, repo: str = DEFAULT_REPO) -> dict:
    """
    Push a Mirror task to GitHub as an issue.
    Creates a new issue if not linked, updates if already linked.
    Returns updated github_issue metadata.
    """
    owner, repo_name = repo.split("/", 1)
    _ensure_labels(repo)

    status = task.get("status", "backlog")
    priority = task.get("priority", "medium")
    gh_spec = STATUS_TO_GH.get(status, STATUS_TO_GH["backlog"])

    labels = (
        gh_spec["labels"]
        + [PRIORITY_TO_GH.get(priority, "priority:medium")]
        + ["mirror-task"]
        + [f"agent:{task.get('agent')}" if task.get("agent") else ""]
    )
    labels = [l for l in labels if l]  # remove empty

    existing_gh = task.get("github_issue") or {}
    issue_number = existing_gh.get("number") if existing_gh else None

    title = task.get("title", "Untitled task")
    body = _build_issue_body(task)

    if issue_number:
        # Update existing issue
        current = _gh_get(f"/repos/{owner}/{repo_name}/issues/{issue_number}")
        if not current:
            issue_number = None  # Issue was deleted, recreate
        else:
            current_labels = [l["name"] for l in current.get("labels", [])]
            # Merge labels: keep non-sync labels, replace sync labels
            non_sync = [l for l in current_labels if not any(
                l == sl for sl in ["backlog", "in-progress", "in-review", "blocked", "done",
                                   "canceled", "priority:urgent", "priority:high",
                                   "priority:medium", "priority:low", "mirror-task"]
            )]
            final_labels = list(set(labels + non_sync))

            _gh_patch(f"/repos/{owner}/{repo_name}/issues/{issue_number}", {
                "title": title,
                "body": body,
                "state": gh_spec["state"],
                "labels": final_labels,
            })
            logger.info(f"Updated GH issue #{issue_number} for task {task['id'][:16]}")
            return {"number": issue_number, "repo": repo, "url": f"https://github.com/{repo}/issues/{issue_number}"}

    if issue_number is None:
        # Create new issue
        result = _gh_post(f"/repos/{owner}/{repo_name}/issues", {
            "title": title,
            "body": body,
            "labels": labels,
        })
        issue_number = result["number"]
        logger.info(f"Created GH issue #{issue_number} for task {task['id'][:16]}")

        # Close immediately if task is done/canceled
        if gh_spec["state"] == "closed":
            _gh_patch(f"/repos/{owner}/{repo_name}/issues/{issue_number}", {"state": "closed"})

        return {
            "number": issue_number,
            "repo": repo,
            "url": result["html_url"],
        }

    return existing_gh


def pull_issues_from_github(repo: str = DEFAULT_REPO, state: str = "all") -> dict:
    """
    Pull GitHub issues → Mirror.
    - Issues with Mirror Task ID: update existing task
    - Issues without Mirror Task ID: create new Mirror task (if not already tracked)
    Returns sync stats.
    """
    owner, repo_name = repo.split("/", 1)

    # Fetch all issues (paginated)
    issues = []
    page = 1
    while True:
        batch = _gh_get(f"/repos/{owner}/{repo_name}/issues?state={state}&per_page=50&page={page}&sort=updated&direction=desc")
        if not isinstance(batch, list) or not batch:
            break
        issues.extend(batch)
        if len(batch) < 50:
            break
        page += 1
        if page > 10:  # Safety limit: 500 issues
            break

    stats = {"pulled": 0, "updated": 0, "created": 0, "skipped": 0}

    for issue in issues:
        # Skip pull requests (GitHub returns them in issues API)
        if "pull_request" in issue:
            continue

        body = issue.get("body") or ""
        task_id = _extract_task_id(body)
        labels = [l["name"] for l in issue.get("labels", [])]
        state_str = issue.get("state", "open")
        mirror_status = gh_to_mirror_status(state_str, labels)
        mirror_priority = gh_to_mirror_priority(labels)
        issue_number = issue["number"]
        gh_meta = {"number": issue_number, "repo": repo, "url": issue["html_url"]}

        if task_id:
            # Known task — update its status from GitHub
            try:
                existing = _sb().table("tasks").select("id,status,github_issue").eq("id", task_id).single().execute()
                if existing.data:
                    current_status = existing.data.get("status", "")
                    # Only sync if GitHub is more recent / authoritative for closed state
                    updates = {"github_issue": gh_meta}
                    if state_str == "closed" and current_status not in ("done", "canceled"):
                        updates["status"] = mirror_status
                    elif state_str == "open" and current_status == "done":
                        updates["status"] = mirror_status  # Re-opened
                    _sb().table("tasks").update(updates).eq("id", task_id).execute()
                    stats["updated"] += 1
                    logger.debug(f"Updated task {task_id[:16]} from GH #{issue_number}")
            except Exception as e:
                logger.warning(f"Could not update task {task_id}: {e}")
                stats["skipped"] += 1
        else:
            # Unknown issue — check if we already have it tracked by github_issue.number
            try:
                existing = _sb().table("tasks").select("id").contains(
                    "github_issue", {"number": issue_number, "repo": repo}
                ).execute()
                if existing.data:
                    stats["skipped"] += 1
                    continue
            except Exception:
                pass

            # Skip issues that aren't Mirror tasks unless they're in mirror-task label
            if "mirror-task" not in labels:
                stats["skipped"] += 1
                continue

            # Create new Mirror task from GitHub issue
            now = datetime.now(timezone.utc).isoformat()
            title = issue.get("title", "Untitled")
            desc = body.split("---")[0].strip() if "---" in body else body[:500]

            # Generate an ID
            import uuid
            task_id = f"github-{issue_number}-{uuid.uuid4().hex[:8]}"
            row = {
                "id": task_id,
                "title": title,
                "status": mirror_status,
                "priority": mirror_priority,
                "agent": "mumega",
                "description": desc or None,
                "labels": labels,
                "github_issue": gh_meta,
                "source": "github",
                "created_at": now,
                "updated_at": now,
                "blocked_by": [],
                "blocks": [],
                "bounty": {},
            }
            try:
                _sb().table("tasks").insert(row).execute()
                stats["created"] += 1
                logger.info(f"Created task {task_id} from GH #{issue_number}")
            except Exception as e:
                logger.warning(f"Could not create task from GH #{issue_number}: {e}")
                stats["skipped"] += 1

        stats["pulled"] += 1

    return stats


# ── Sync Endpoints ───────────────────────────────────────────────────────────
class SyncRequest(BaseModel):
    repo: Optional[str] = None
    push: bool = True    # Mirror → GitHub
    pull: bool = True    # GitHub → Mirror


@router.post("/github-sync")
async def full_sync(req: SyncRequest = None):
    """Full bidirectional sync: Mirror tasks ↔ GitHub issues."""
    repo = (req.repo if req else None) or DEFAULT_REPO
    do_push = req.push if req else True
    do_pull = req.pull if req else True

    if not GITHUB_TOKEN and not os.environ.get("GITHUB_TOKEN"):
        raise HTTPException(500, "GITHUB_TOKEN not configured")

    stats = {"repo": repo, "push": {}, "pull": {}}

    if do_push:
        # Push all tasks that don't have a GitHub issue yet (or need update)
        result = _sb().table("tasks").select("*").not_.in_("status", ["done", "canceled"]).execute()
        tasks = result.data or []
        pushed = 0
        errors = 0
        for task in tasks:
            try:
                gh_meta = push_task_to_github(task, repo)
                # Store github_issue back on the task
                existing_gh = task.get("github_issue") or {}
                if not existing_gh or existing_gh.get("number") != gh_meta.get("number"):
                    _sb().table("tasks").update({"github_issue": gh_meta}).eq("id", task["id"]).execute()
                pushed += 1
            except Exception as e:
                logger.warning(f"Push failed for {task.get('id', '?')}: {e}")
                errors += 1
        stats["push"] = {"pushed": pushed, "errors": errors}

    if do_pull:
        pull_stats = pull_issues_from_github(repo)
        stats["pull"] = pull_stats

    logger.info(f"Sync complete: {stats}")
    return {"status": "synced", "stats": stats}


@router.post("/github-sync/push/{task_id}")
async def push_single_task(task_id: str, repo: Optional[str] = Query(None)):
    """Push a single Mirror task to GitHub."""
    repo = repo or DEFAULT_REPO
    try:
        result = _sb().table("tasks").select("*").eq("id", task_id).single().execute()
        task = result.data
    except Exception:
        raise HTTPException(404, f"Task not found: {task_id}")

    gh_meta = push_task_to_github(task, repo)
    _sb().table("tasks").update({"github_issue": gh_meta}).eq("id", task_id).execute()
    return {"status": "pushed", "github_issue": gh_meta}


@router.post("/github-sync/pull")
async def pull_from_github(repo: Optional[str] = Query(None)):
    """Pull GitHub issues into Mirror."""
    repo = repo or DEFAULT_REPO
    stats = pull_issues_from_github(repo)
    return {"status": "pulled", "stats": stats}


@router.get("/github-sync/status")
async def sync_status(repo: Optional[str] = Query(None)):
    """Show sync coverage: how many Mirror tasks have GitHub issues."""
    repo = repo or DEFAULT_REPO
    all_tasks = _sb().table("tasks").select("id,status,github_issue").execute().data or []
    synced = [t for t in all_tasks if t.get("github_issue")]
    unsynced = [t for t in all_tasks if not t.get("github_issue") and t.get("status") not in ("done", "canceled")]
    return {
        "repo": repo,
        "total_tasks": len(all_tasks),
        "synced_to_github": len(synced),
        "unsynced_active": len(unsynced),
        "coverage_pct": round(len(synced) / max(len(all_tasks), 1) * 100, 1),
    }


# ── Webhook Handler ──────────────────────────────────────────────────────────
@router.post("/github-webhook")
async def github_webhook(request: Request):
    """
    Receive GitHub issue events and update Mirror tasks in real-time.

    To configure: In your GitHub repo → Settings → Webhooks
    - Payload URL: https://mumega.com/mirror/github-webhook
    - Content type: application/json
    - Secret: GITHUB_WEBHOOK_SECRET env var
    - Events: Issues
    """
    body_bytes = await request.body()

    # Verify signature if secret is configured
    if WEBHOOK_SECRET:
        signature = request.headers.get("X-Hub-Signature-256", "")
        expected = "sha256=" + hmac.new(
            WEBHOOK_SECRET.encode(), body_bytes, hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise HTTPException(401, "Invalid webhook signature")

    event_type = request.headers.get("X-GitHub-Event", "")
    if event_type != "issues":
        return {"status": "ignored", "event": event_type}

    payload = await request.json()
    action = payload.get("action", "")
    issue = payload.get("issue", {})

    if action not in ("opened", "closed", "reopened", "labeled", "unlabeled", "edited"):
        return {"status": "ignored", "action": action}

    body = issue.get("body") or ""
    task_id = _extract_task_id(body)

    if not task_id:
        # New issue from GitHub, not a Mirror task
        return {"status": "no_task_id", "issue": issue.get("number")}

    labels = [l["name"] for l in issue.get("labels", [])]
    state = issue.get("state", "open")
    mirror_status = gh_to_mirror_status(state, labels)
    mirror_priority = gh_to_mirror_priority(labels)
    issue_number = issue["number"]
    repo = payload.get("repository", {}).get("full_name", DEFAULT_REPO)

    updates = {
        "status": mirror_status,
        "priority": mirror_priority,
        "github_issue": {
            "number": issue_number,
            "repo": repo,
            "url": issue["html_url"],
        },
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if action == "closed":
        updates["completed_at"] = datetime.now(timezone.utc).isoformat()

    try:
        _sb().table("tasks").update(updates).eq("id", task_id).execute()
        logger.info(f"Webhook: task {task_id[:16]} → {mirror_status} (GH #{issue_number} {action})")
        return {"status": "updated", "task_id": task_id, "mirror_status": mirror_status}
    except Exception as e:
        logger.error(f"Webhook update failed for {task_id}: {e}")
        raise HTTPException(500, str(e))
