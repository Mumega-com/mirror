"""
Budget enforcement for the Mumega agent system.

Ported from Paperclip's budget model (github.com/paperclipai/paperclip)
Adapted for Supabase PostgreSQL + supabase-py.

Functions:
  record_cost()       — log a model call cost event
  check_budget()      — check if an agent is within policy limits
  get_usage_summary() — current month spend by agent/customer
  log_activity()      — append an audit trail entry
  infer_cost_cents()  — compute cost from token counts + model name
"""

from __future__ import annotations

import os
import math
from datetime import datetime, timezone
from typing import Optional
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Env
# ---------------------------------------------------------------------------
load_dotenv("/home/mumega/resident-cms/.env")

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_API_KEY", "")  # service role key

if not SUPABASE_URL or not SUPABASE_KEY:
    raise EnvironmentError(
        "SUPABASE_URL and SUPABASE_API_KEY must be set in /home/mumega/resident-cms/.env"
    )

from supabase import create_client, Client  # noqa: E402

_client: Optional[Client] = None


def _db() -> Client:
    global _client
    if _client is None:
        _client = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _client


# ---------------------------------------------------------------------------
# Cost inference map (per million tokens, USD)
# Source: Paperclip billing.ts pattern, adapted for our model roster
# ---------------------------------------------------------------------------
MODEL_COSTS: dict[str, dict[str, float]] = {
    # Claude
    "claude-opus-4-6":    {"input": 15.0,  "output": 75.0},
    "claude-opus-4":      {"input": 15.0,  "output": 75.0},
    "claude-sonnet-4-6":  {"input": 3.0,   "output": 15.0},
    "claude-sonnet-4":    {"input": 3.0,   "output": 15.0},
    "claude-haiku-3":     {"input": 0.25,  "output": 1.25},
    # Gemini
    "gemini-3-flash":     {"input": 0.10,  "output": 0.40},
    "gemini-3-pro":       {"input": 1.25,  "output": 5.00},
    "gemini-2-flash":     {"input": 0.075, "output": 0.30},
    # OpenAI
    "gpt-4o":             {"input": 2.50,  "output": 10.00},
    "gpt-4o-mini":        {"input": 0.15,  "output": 0.60},
    "gpt-4-turbo":        {"input": 10.0,  "output": 30.00},
    # DeepSeek
    "deepseek-chat":      {"input": 0.14,  "output": 0.28},
    "deepseek-reasoner":  {"input": 0.55,  "output": 2.19},
    # Grok
    "grok-2":             {"input": 2.00,  "output": 10.00},
    # Ollama (local — free)
    "ollama":             {"input": 0.0,   "output": 0.0},
}

_USD_PER_CENT = 0.01
_TOKENS_PER_MILLION = 1_000_000


def infer_cost_cents(
    model: str,
    input_tokens: int,
    output_tokens: int,
) -> int:
    """
    Compute cost in integer cents from token counts.
    Falls back to 0 for unknown models (e.g. local Ollama).
    """
    # Normalize: strip provider prefix like "anthropic/claude-sonnet-4-6"
    key = model.split("/")[-1].lower()
    rates = MODEL_COSTS.get(key)
    if rates is None:
        # Try prefix match (e.g. "claude-sonnet" matches "claude-sonnet-4-6")
        for known_key, known_rates in MODEL_COSTS.items():
            if key.startswith(known_key) or known_key.startswith(key):
                rates = known_rates
                break
    if rates is None:
        return 0

    input_cost_usd = (input_tokens / _TOKENS_PER_MILLION) * rates["input"]
    output_cost_usd = (output_tokens / _TOKENS_PER_MILLION) * rates["output"]
    total_usd = input_cost_usd + output_cost_usd
    return math.ceil(total_usd / _USD_PER_CENT)  # round up — conservative


# ---------------------------------------------------------------------------
# Window helpers
# ---------------------------------------------------------------------------

def _window_start_utc() -> str:
    """ISO timestamp for start of current UTC calendar month."""
    now = datetime.now(timezone.utc)
    return datetime(now.year, now.month, 1, tzinfo=timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

def record_cost(
    agent_id: str,
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cost_cents: Optional[int] = None,
    customer_id: Optional[str] = None,
    project: Optional[str] = None,
    run_id: Optional[str] = None,
) -> dict:
    """
    Log a model call as a cost_event row.

    If cost_cents is None, it is inferred from MODEL_COSTS.
    Returns the inserted row.
    """
    if cost_cents is None:
        cost_cents = infer_cost_cents(model, input_tokens, output_tokens)

    payload: dict = {
        "agent_id": agent_id,
        "provider": provider,
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_cents": cost_cents,
    }
    if customer_id:
        payload["customer_id"] = customer_id
    if project:
        payload["project"] = project
    if run_id:
        payload["run_id"] = run_id

    result = _db().table("cost_events").insert(payload).execute()
    row = result.data[0] if result.data else {}

    # After recording, check budgets and raise incidents if needed
    _check_and_incident(agent_id=agent_id, customer_id=customer_id, project=project)

    return row


def _month_spend(*, agent_id: str | None = None, customer_id: str | None = None, project: str | None = None) -> int:
    """
    Sum cost_cents for the current calendar month UTC.
    At least one filter must be provided.
    """
    window = _window_start_utc()
    q = _db().table("cost_events").select("cost_cents").gte("occurred_at", window)

    if agent_id:
        q = q.eq("agent_id", agent_id)
    if customer_id:
        q = q.eq("customer_id", customer_id)
    if project:
        q = q.eq("project", project)

    result = q.execute()
    return sum(row["cost_cents"] for row in (result.data or []))


def check_budget(
    agent_id: str,
    cost_cents: int = 0,
) -> dict:
    """
    Check whether agent_id is within its budget policies.

    Checks:
      - agent-scoped policy for agent_id
      - global policy for 'mumega'

    Returns:
      {
        "allowed": bool,         # False = hard stop hit
        "warning": bool,         # True = warn threshold crossed
        "remaining_cents": int,  # Cents remaining on tightest policy
        "policies_checked": int,
      }
    """
    policies_res = (
        _db()
        .table("budget_policies")
        .select("*")
        .eq("enabled", True)
        .in_("scope_type", ["agent", "global"])
        .execute()
    )

    policies = [
        p for p in (policies_res.data or [])
        if (p["scope_type"] == "agent" and p["scope_id"] == agent_id)
        or (p["scope_type"] == "global")
    ]

    if not policies:
        # No policy = unrestricted
        return {"allowed": True, "warning": False, "remaining_cents": -1, "policies_checked": 0}

    current_spend = _month_spend(agent_id=agent_id)
    projected = current_spend + cost_cents

    allowed = True
    warning = False
    tightest_remaining = float("inf")

    for policy in policies:
        limit = policy["amount_cents"]
        warn_at = math.floor(limit * policy["warn_percent"] / 100)

        if projected >= limit and policy.get("hard_stop", True):
            allowed = False
        if projected >= warn_at:
            warning = True

        remaining = limit - current_spend
        if remaining < tightest_remaining:
            tightest_remaining = remaining

    return {
        "allowed": allowed,
        "warning": warning,
        "remaining_cents": int(tightest_remaining) if tightest_remaining != float("inf") else -1,
        "policies_checked": len(policies),
    }


def _check_and_incident(
    agent_id: str,
    customer_id: Optional[str] = None,
    project: Optional[str] = None,
) -> None:
    """
    After recording a cost event, check all applicable policies and
    create budget_incidents for any threshold crossings.
    Internal use only.
    """
    policies_res = (
        _db()
        .table("budget_policies")
        .select("*")
        .eq("enabled", True)
        .execute()
    )

    for policy in (policies_res.data or []):
        scope = policy["scope_type"]
        sid = policy["scope_id"]

        # Determine spend for this policy's scope
        if scope == "agent" and sid == agent_id:
            spend = _month_spend(agent_id=agent_id)
        elif scope == "customer" and customer_id and sid == customer_id:
            spend = _month_spend(customer_id=customer_id)
        elif scope == "project" and project and sid == project:
            spend = _month_spend(project=project)
        elif scope == "global":
            # Global: sum all cost_events this month
            window = _window_start_utc()
            res = _db().table("cost_events").select("cost_cents").gte("occurred_at", window).execute()
            spend = sum(r["cost_cents"] for r in (res.data or []))
        else:
            continue

        limit = policy["amount_cents"]
        warn_at = math.floor(limit * policy["warn_percent"] / 100)

        # Check if open incident already exists for this policy + type
        def _open_incident_exists(threshold_type: str) -> bool:
            res = (
                _db()
                .table("budget_incidents")
                .select("id")
                .eq("policy_id", policy["id"])
                .eq("threshold_type", threshold_type)
                .eq("status", "open")
                .execute()
            )
            return bool(res.data)

        if spend >= limit and not _open_incident_exists("hard_stop"):
            _db().table("budget_incidents").insert({
                "policy_id": policy["id"],
                "threshold_type": "hard_stop",
                "amount_limit": limit,
                "amount_observed": spend,
                "status": "open",
            }).execute()
            log_activity(
                actor_type="system",
                actor_id="budget_enforcer",
                action="hard_stop_triggered",
                entity_type="budget_policy",
                entity_id=policy["id"],
                details={"agent_id": agent_id, "spend": spend, "limit": limit},
            )
        elif spend >= warn_at and spend < limit and not _open_incident_exists("warning"):
            _db().table("budget_incidents").insert({
                "policy_id": policy["id"],
                "threshold_type": "warning",
                "amount_limit": warn_at,
                "amount_observed": spend,
                "status": "open",
            }).execute()
            log_activity(
                actor_type="system",
                actor_id="budget_enforcer",
                action="warning_triggered",
                entity_type="budget_policy",
                entity_id=policy["id"],
                details={"agent_id": agent_id, "spend": spend, "warn_at": warn_at},
            )


def get_usage_summary(
    agent_id: Optional[str] = None,
    customer_id: Optional[str] = None,
    project: Optional[str] = None,
) -> dict:
    """
    Current month spend summary.

    At least one of agent_id, customer_id, project should be provided.
    If none, returns global totals.

    Returns:
      {
        "period": "2026-03",
        "total_cents": int,
        "total_usd": float,
        "breakdown": [{"model": str, "input_tokens": int, "output_tokens": int, "cost_cents": int}, ...]
      }
    """
    window = _window_start_utc()
    q = _db().table("cost_events").select("model,input_tokens,output_tokens,cost_cents").gte("occurred_at", window)

    if agent_id:
        q = q.eq("agent_id", agent_id)
    if customer_id:
        q = q.eq("customer_id", customer_id)
    if project:
        q = q.eq("project", project)

    result = q.execute()
    rows = result.data or []

    # Aggregate by model
    by_model: dict[str, dict] = {}
    total_cents = 0
    for row in rows:
        m = row["model"]
        if m not in by_model:
            by_model[m] = {"model": m, "input_tokens": 0, "output_tokens": 0, "cost_cents": 0}
        by_model[m]["input_tokens"] += row.get("input_tokens", 0)
        by_model[m]["output_tokens"] += row.get("output_tokens", 0)
        by_model[m]["cost_cents"] += row.get("cost_cents", 0)
        total_cents += row.get("cost_cents", 0)

    now = datetime.now(timezone.utc)
    return {
        "period": f"{now.year}-{now.month:02d}",
        "total_cents": total_cents,
        "total_usd": round(total_cents * _USD_PER_CENT, 4),
        "breakdown": sorted(by_model.values(), key=lambda r: r["cost_cents"], reverse=True),
    }


def log_activity(
    actor_type: str,
    actor_id: str,
    action: str,
    entity_type: Optional[str] = None,
    entity_id: Optional[str] = None,
    details: Optional[dict] = None,
) -> dict:
    """
    Append an immutable audit trail entry.

    actor_type: 'system' | 'user' | 'agent'
    Returns the inserted row.
    """
    payload: dict = {
        "actor_type": actor_type,
        "actor_id": actor_id,
        "action": action,
        "details": details or {},
    }
    if entity_type:
        payload["entity_type"] = entity_type
    if entity_id:
        payload["entity_id"] = entity_id

    result = _db().table("activity_log").insert(payload).execute()
    return result.data[0] if result.data else {}


# ---------------------------------------------------------------------------
# CLI smoke test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import json

    print("--- record_cost (kasra, 1000 in / 500 out, claude-sonnet-4-6) ---")
    row = record_cost(
        agent_id="kasra",
        provider="anthropic",
        model="claude-sonnet-4-6",
        input_tokens=1000,
        output_tokens=500,
    )
    print(f"  inserted: {row.get('id')} | cost_cents={row.get('cost_cents')}")

    print("\n--- check_budget (kasra, 0 additional) ---")
    status = check_budget("kasra", cost_cents=0)
    print(f"  {json.dumps(status, indent=2)}")

    print("\n--- get_usage_summary (kasra) ---")
    summary = get_usage_summary(agent_id="kasra")
    print(f"  {json.dumps(summary, indent=2)}")

    print("\n--- log_activity ---")
    entry = log_activity(
        actor_type="agent",
        actor_id="kasra",
        action="budget_smoke_test",
        entity_type="cost_event",
        entity_id=row.get("id"),
        details={"note": "smoke test from budget.py __main__"},
    )
    print(f"  logged: {entry.get('id')}")

    print("\nAll checks passed.")
