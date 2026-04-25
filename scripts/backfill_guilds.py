#!/usr/bin/env python3
"""
§13 Guild backfill — promote informal orgs to first-class guild rows.

Idempotent: create_guild uses ON CONFLICT DO NOTHING; add_member uses
ON CONFLICT DO UPDATE so re-runs are safe.

Run from the mirror/ directory:
    python scripts/backfill_guilds.py

Requires MIRROR_DATABASE_URL or DATABASE_URL in the environment (or .env file).
"""
from __future__ import annotations

import sys
from pathlib import Path

# Allow SOS imports
_SOS = str(Path.home() / "SOS")
if _SOS not in sys.path:
    sys.path.insert(0, _SOS)

# Load .env from mirror/ if present
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env", override=False)
except ImportError:
    pass

from sos.contracts.guild import GuildSpec, add_member, create_guild


def backfill() -> None:
    print("§13 Guild backfill — promoting informal orgs to first-class guilds\n")

    # ── 1. Mumega Inc. ────────────────────────────────────────────────────────
    print("Creating guild: mumega-inc")
    mumega = create_guild(
        GuildSpec(
            id="mumega-inc",
            name="Mumega Inc.",
            kind="company",
            governance_tier="principal-only",
        ),
        created_by="hadi",
    )
    print(f"  → {mumega.id} ({mumega.status})")

    for member_id, rank in [
        ("hadi",   "founder"),
        ("loom",   "coordinator"),
        ("kasra",  "builder"),
        ("athena", "quality_gate"),
    ]:
        m = add_member("mumega-inc", member_id, rank, added_by="hadi")
        print(f"  + {m.member_id} [{m.rank}]")

    # ── 2. Digid Inc. ─────────────────────────────────────────────────────────
    print("\nCreating guild: digid-inc")
    digid = create_guild(
        GuildSpec(
            id="digid-inc",
            name="Digid Inc.",
            kind="company",
            governance_tier="principal-only",
        ),
        created_by="hadi",
    )
    print(f"  → {digid.id} ({digid.status})")

    for member_id, rank in [
        ("hadi",  "founder"),
        ("gavin", "partner"),
        ("lex",   "advisor"),
        ("noor",  "operator"),
    ]:
        m = add_member("digid-inc", member_id, rank, added_by="hadi")
        print(f"  + {m.member_id} [{m.rank}]")

    # ── 3. GAF (Grant & Funding) ──────────────────────────────────────────────
    print("\nCreating guild: gaf")
    gaf = create_guild(
        GuildSpec(
            id="gaf",
            name="Grant & Funding (GAF)",
            kind="project",
            parent_guild_id="digid-inc",
            governance_tier="delegated",
        ),
        created_by="hadi",
    )
    print(f"  → {gaf.id} ({gaf.status}, parent={gaf.parent_guild_id})")

    for member_id, rank in [
        ("hadi",  "founder"),
        ("gavin", "partner"),
    ]:
        m = add_member("gaf", member_id, rank, added_by="hadi")
        print(f"  + {m.member_id} [{m.rank}]")

    # ── 4. AgentLink ──────────────────────────────────────────────────────────
    print("\nCreating guild: agentlink")
    agentlink = create_guild(
        GuildSpec(
            id="agentlink",
            name="AgentLink",
            kind="project",
            governance_tier="consensus",
        ),
        created_by="hadi",
    )
    print(f"  → {agentlink.id} ({agentlink.status})")

    for member_id, rank in [
        ("hadi", "founder"),
        ("matt", "founder"),
    ]:
        m = add_member("agentlink", member_id, rank, added_by="hadi")
        print(f"  + {m.member_id} [{m.rank}]")

    print("\n✓ Backfill complete — 4 guilds seeded.")


if __name__ == "__main__":
    backfill()
