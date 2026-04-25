#!/usr/bin/env python3
"""Backfill docs_relations from canonical dependency signals in doc bodies.

Derives three types of edges:

  sequences   — Section N → Section N+1 (ordered by section number)
  derives_from — A depends on B (from **Depends on:** lines)
  specced_in  — Sprint/Burst/Roadmap doc → the section that specifies it

All inserts are ON CONFLICT DO NOTHING so re-running is safe.

Usage:
    python3 scripts/backfill_docs_relations.py --target mirror [--dry-run]
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import psycopg2

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("backfill_relations")

_KNOWN_TARGETS = {
    "mirror": "postgresql://mirror:mirror_local_2026@localhost:5432/mirror",
    "supabase": "postgresql://postgres:UnnamedTao%408%40@db.nnolqgvuvoxkofbitunb.supabase.co:5432/postgres",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dsn(target: str) -> str:
    if target not in _KNOWN_TARGETS:
        logger.error("Unknown --target %r. Choices: %s", target, ", ".join(_KNOWN_TARGETS))
        sys.exit(1)
    return _KNOWN_TARGETS[target]


def _section_number(title: str) -> int | None:
    """Extract the leading section number from a title like 'Section 7 — ...'."""
    m = re.match(r"Section\s+(\d+)", title, re.IGNORECASE)
    return int(m.group(1)) if m else None


def _find_node_by_prefix(nodes_by_title: dict[str, str], fragment: str) -> str | None:
    """Return node id whose title starts with *fragment* (case-insensitive)."""
    frag_lower = fragment.strip().lower()
    for title, nid in nodes_by_title.items():
        if title.lower().startswith(frag_lower):
            return nid
    return None


def _parse_depends_on(body: str) -> list[str]:
    """Extract comma-separated dependency tokens from **Depends on:** lines."""
    tokens: list[str] = []
    for line in body.splitlines():
        if re.search(r"\*\*Depends on[:\*]", line, re.IGNORECASE):
            # strip markup and split on commas
            plain = re.sub(r"\*+", "", line)
            plain = re.sub(r".*Depends on[:\s]*", "", plain, flags=re.IGNORECASE)
            parts = [p.strip() for p in plain.split(",")]
            tokens.extend(p for p in parts if p)
    return tokens


def _short_prefix(token: str) -> str:
    """Normalise a dependency token to a matchable prefix.

    E.g.:
      'Section 1A (role registry)' → 'Section 1'
      'Burst 2B-2 (audit)'         → 'Burst 2B-2'
      'Section 10 (metabolic ...)'  → 'Section 10'
    """
    # Strip trailing parenthetical
    token = re.sub(r"\s*\(.*", "", token).strip()
    # Normalise section sub-letters: 'Section 1A' → 'Section 1'
    token = re.sub(r"(Section\s+\d+)[A-Za-z]", r"\1", token)
    return token


# ---------------------------------------------------------------------------
# Main logic
# ---------------------------------------------------------------------------

def build_edges(
    nodes: list[dict],
) -> list[tuple[str, str, str, float]]:
    """Return list of (from_node, to_node, edge_type, weight) tuples."""
    edges: list[tuple[str, str, str, float]] = []

    # Build lookup: lower title → id
    nodes_by_title: dict[str, str] = {n["title"].lower(): n["id"] for n in nodes}

    # 1. sequences — Section N → Section N+1
    sections: dict[int, str] = {}
    for n in nodes:
        num = _section_number(n["title"])
        if num is not None:
            sections[num] = n["id"]

    for num in sorted(sections):
        if num + 1 in sections:
            edges.append((sections[num], sections[num + 1], "sequences", 1.0))

    # 2. derives_from — parse **Depends on:** lines in each node body
    for n in nodes:
        body = n.get("body") or ""
        tokens = _parse_depends_on(body)
        for raw_token in tokens:
            prefix = _short_prefix(raw_token)
            if not prefix or len(prefix) < 5:
                continue
            # Try exact prefix match first, then lower
            target_id = _find_node_by_prefix(nodes_by_title, prefix)
            if target_id and target_id != n["id"]:
                edges.append((n["id"], target_id, "derives_from", 0.9))

    # 3. specced_in — Roadmap/Plan/Full-Stack-Plan docs reference Section docs
    roadmap_keywords = re.compile(
        r"\b(roadmap|full.stack.plan|map.of.meaning|full.phase.roadmap|sprint\s+0\d+|burst\s+2[ab]-\d+)\b",
        re.IGNORECASE,
    )
    for n in nodes:
        if not roadmap_keywords.search(n["title"]):
            continue
        body = n.get("body") or ""
        for m in re.finditer(r"Section\s+(\d+)", body, re.IGNORECASE):
            sec_num = int(m.group(1))
            if sec_num in sections and sections[sec_num] != n["id"]:
                edges.append((n["id"], sections[sec_num], "specced_in", 0.8))

    # Deduplicate (from, to, type) keeping first occurrence
    seen: set[tuple[str, str, str]] = set()
    unique: list[tuple[str, str, str, float]] = []
    for from_node, to_node, edge_type, weight in edges:
        key = (from_node, to_node, edge_type)
        if key not in seen:
            seen.add(key)
            unique.append((from_node, to_node, edge_type, weight))

    return unique


def run(target: str, dry_run: bool) -> None:
    dsn = _dsn(target)
    logger.info("TARGET: %s  [%s]", target, "DRY RUN" if dry_run else "LIVE")

    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, title, body FROM docs_nodes")
            nodes = [{"id": r[0], "title": r[1], "body": r[2]} for r in cur.fetchall()]

        logger.info("Loaded %d nodes", len(nodes))
        edges = build_edges(nodes)
        logger.info("Derived %d candidate edges", len(edges))

        if dry_run:
            for from_node, to_node, edge_type, weight in edges:
                from_title = next((n["title"] for n in nodes if n["id"] == from_node), from_node)
                to_title = next((n["title"] for n in nodes if n["id"] == to_node), to_node)
                print(f"  {edge_type:14}  {from_title[:45]:<45}  →  {to_title[:45]}")
            return

        inserted = 0
        skipped = 0
        with conn:
            with conn.cursor() as cur:
                for from_node, to_node, edge_type, weight in edges:
                    cur.execute(
                        """
                        INSERT INTO docs_relations (from_node, to_node, edge_type, weight)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (from_node, to_node, edge_type) DO NOTHING
                        """,
                        (from_node, to_node, edge_type, weight),
                    )
                    if cur.rowcount:
                        inserted += 1
                    else:
                        skipped += 1

        logger.info("Done — %d inserted, %d already existed", inserted, skipped)
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill docs_relations from doc bodies")
    parser.add_argument("--target", choices=list(_KNOWN_TARGETS), default="mirror",
                        help="Database target (default: mirror)")
    parser.add_argument("--dry-run", action="store_true", help="Print edges, no writes")
    args = parser.parse_args()
    run(args.target, args.dry_run)


if __name__ == "__main__":
    main()
