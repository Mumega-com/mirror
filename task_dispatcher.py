#!/usr/bin/env python3
"""
Task Dispatcher — Auto-assigns unblocked tasks to idle agents via tmux.

Runs every N minutes (via cron or systemd timer).
Flow:
  1. Check task board for unblocked backlog tasks
  2. Check which agents (tmux sessions) are idle
  3. Pick highest-priority unblocked task
  4. Send it to the idle agent via tmux
  5. Mark task as in_progress
  6. Log to Redis stream

This is Calcifer's baby version — the autonomous loop.
"""

import json
import logging
import subprocess
import sys
import time
from datetime import datetime
from typing import Optional, Dict, List

try:
    import requests
except ImportError:
    print("pip install requests")
    sys.exit(1)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [dispatcher] %(levelname)s %(message)s'
)
logger = logging.getLogger("task-dispatcher")

MIRROR_URL = "http://localhost:8844"
AGENTS = {
    "kasra": {
        "tmux_session": "kasra",
        "idle_pattern": "❯",  # Claude Code prompt
        "skills": ["backend", "frontend", "infrastructure", "nginx", "api"],
    },
}


def get_unblocked_tasks() -> List[Dict]:
    """Get backlog tasks that aren't blocked."""
    try:
        resp = requests.get(f"{MIRROR_URL}/tasks", timeout=5)
        if resp.status_code != 200:
            return []
        raw = resp.json()
        tasks = raw.get("tasks", []) if isinstance(raw, dict) else raw

        # STRICT filter: only backlog tasks (not done, not blocked, not in_progress, not canceled)
        backlog = [t for t in tasks if t.get("status") == "backlog"]

        # Filter unblocked
        unblocked = []
        for t in backlog:
            blocked_by = t.get("blocked_by") or []
            if not blocked_by:
                unblocked.append(t)
                continue
            # Check if all blockers are done
            all_done = True
            for bid in blocked_by:
                try:
                    bresp = requests.get(f"{MIRROR_URL}/tasks/{bid}", timeout=3)
                    if bresp.status_code == 200:
                        bstatus = bresp.json().get("status", "backlog")
                        if bstatus not in ("done", "canceled"):
                            all_done = False
                            break
                except Exception:
                    all_done = False
                    break
            if all_done:
                unblocked.append(t)
        
        # Sort by priority
        prio_order = {"urgent": 0, "high": 1, "medium": 2, "low": 3}
        unblocked.sort(key=lambda t: prio_order.get(t.get("priority", "low"), 4))
        return unblocked
    except Exception as e:
        logger.error(f"Failed to fetch tasks: {e}")
        return []


def is_agent_idle(agent_id: str) -> bool:
    """Check if agent's tmux session is idle (at prompt)."""
    agent = AGENTS.get(agent_id)
    if not agent:
        return False
    
    try:
        result = subprocess.run(
            ["tmux", "capture-pane", "-t", agent["tmux_session"], "-p"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0:
            return False
        
        lines = result.stdout.strip().split("\n")
        # Check last 5 lines for idle prompt
        tail = "\n".join(lines[-5:])
        return agent["idle_pattern"] in tail and "Transmuting" not in tail and "Churning" not in tail and "Baking" not in tail and "Warping" not in tail
    except Exception:
        return False


def get_in_progress_count(agent_id: str) -> int:
    """Count in-progress tasks for an agent."""
    try:
        resp = requests.get(f"{MIRROR_URL}/tasks", params={"agent": agent_id, "status": "in_progress"}, timeout=5)
        if resp.status_code == 200:
            return resp.json().get("count", 0)
    except Exception:
        pass
    return 0


def send_task_to_agent(agent_id: str, task: Dict) -> bool:
    """Send task to agent via tmux."""
    agent = AGENTS.get(agent_id)
    if not agent:
        return False
    
    title = task["title"]
    desc = task.get("description", "")
    task_id = task["id"]
    priority = task.get("priority", "medium")
    
    prompt = f"TASK [{priority.upper()}] {task_id}:\n{title}\n\n{desc}\n\nWhen done, report what you did. Do not ask for clarification — execute."
    
    try:
        # Send via tmux
        subprocess.run(
            ["tmux", "send-keys", "-t", agent["tmux_session"], "-l", "--", prompt],
            timeout=5
        )
        time.sleep(0.2)
        subprocess.run(
            ["tmux", "send-keys", "-t", agent["tmux_session"], "Enter"],
            timeout=5
        )
        
        # Mark as in_progress
        requests.put(
            f"{MIRROR_URL}/tasks/{task_id}",
            json={"status": "in_progress"},
            timeout=5
        )
        
        # Notify via Redis
        try:
            subprocess.run([
                "redis-cli", "XADD",
                f"sos:stream:sos:channel:private:agent:{agent_id}", "*",
                "data", json.dumps({
                    "type": "task_dispatched",
                    "source": "dispatcher",
                    "payload": {"task_id": task_id, "title": title, "priority": priority}
                })
            ], timeout=5)
        except Exception:
            pass
        
        logger.info(f"Dispatched [{priority}] '{title}' → {agent_id}")
        return True
    except Exception as e:
        logger.error(f"Failed to dispatch to {agent_id}: {e}")
        return False


def run_dispatch_cycle():
    """One dispatch cycle."""
    tasks = get_unblocked_tasks()
    if not tasks:
        logger.info("No unblocked backlog tasks.")
        return
    
    for agent_id in AGENTS:
        # Skip if agent already has work
        if get_in_progress_count(agent_id) > 0:
            logger.info(f"{agent_id}: already has in_progress task, skipping")
            continue
        
        if not is_agent_idle(agent_id):
            logger.info(f"{agent_id}: busy (not at prompt), skipping")
            continue
        
        # Find task for this agent — only tasks explicitly assigned to them
        for task in tasks:
            task_agent = task.get("agent", "")
            if task_agent == agent_id:
                if send_task_to_agent(agent_id, task):
                    tasks.remove(task)
                    break
    
    logger.info("Dispatch cycle complete.")


if __name__ == "__main__":
    logger.info("Task Dispatcher running...")
    run_dispatch_cycle()
