#!/usr/bin/env python3
"""
River → FRC Daily Content Generator

Creates one new draft per day (blog/topic/article) in the FRC repo inbox.
Optionally autopublishes by committing/pushing to the configured branch.

Safe defaults:
- Writes to content/inbox/ only
- Does NOT autopublish unless RIVER_FRC_AUTOPUBLISH=1

Run:
  python3 /home/mumega/mirror/river_frc_daily_content.py --frc-repo /home/mumega/frc --kind auto --lang en
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple


def _load_env():
    try:
        from dotenv import load_dotenv
    except Exception:
        return
    # Prefer mirror keys (Gemini/OpenRouter/XAI) for content generation.
    load_dotenv("/home/mumega/mirror/.env", override=False)
    # SOS sometimes has Google API key; keep as supplemental.
    load_dotenv("/home/mumega/SOS/.env", override=False)


def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _slugify(text: str) -> str:
    s = text.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:80] or "untitled"


def _extract_title(md: str) -> Optional[str]:
    m = re.search(r"^#\s+(.+?)\s*$", md, flags=re.M)
    return m.group(1).strip() if m else None


def _extract_frontmatter(md: str) -> Tuple[Optional[str], str]:
    m = re.match(r"^(?:\ufeff)?---\s*\n([\s\S]*?)\n---\s*\n?", md)
    if not m:
        return None, md
    return m.group(1), md[m.end() :]


def _parse_fm_value(fm: str, key: str) -> Optional[str]:
    for line in (fm or "").splitlines():
        mm = re.match(rf"^{re.escape(key)}:\s*(.+?)\s*$", line)
        if mm:
            return mm.group(1).strip().strip('"')
    return None


def _to_frontmatter(meta: dict) -> str:
    lines = ["---"]
    for k, v in meta.items():
        if v is None:
            continue
        if isinstance(v, list):
            safe = ", ".join(json.dumps(x) for x in v)
            lines.append(f"{k}: [{safe}]")
        else:
            lines.append(f"{k}: {json.dumps(v) if isinstance(v, str) else v}")
    lines.append("---\n")
    return "\n".join(lines)


def _ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)


def _get_keys() -> List[str]:
    keys: List[str] = []
    for name in ["GEMINI_API_KEY", "GOOGLE_API_KEY"]:
        v = os.getenv(name)
        if v and v not in keys:
            keys.append(v)
    for prefix in ["GEMINI_API_KEY_", "GOOGLE_API_KEY_"]:
        for i in range(1, 11):
            v = os.getenv(f"{prefix}{i}")
            if v and v not in keys:
                keys.append(v)
    return keys


def _state_path() -> Path:
    return Path("/home/mumega/.mumega/river_daily_state.json")

def _read_state() -> dict:
    p = _state_path()
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}

def _write_state(state: dict) -> None:
    p = _state_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    except Exception:
        pass


def _choose_key(keys: List[str]) -> str:
    if not keys:
        raise RuntimeError("No Gemini API keys available (expected in /home/mumega/mirror/.env)")

    data = _read_state()
    idx = int(data.get("gemini_key_index", 0) or 0)

    key = keys[idx % len(keys)]
    next_idx = (idx + 1) % len(keys)
    data["gemini_key_index"] = next_idx
    data["updated_at"] = _today_utc()
    _write_state(data)

    return key


@dataclass(frozen=True)
class DraftPlan:
    kind: str  # blog|topic|article
    id_prefix: str
    out_dir: str
    voice: str
    perspective: str


def _kind_plan(kind: str) -> DraftPlan:
    k = kind.lower().strip()
    if k == "blog":
        return DraftPlan(kind="blog", id_prefix="river-daily", out_dir="blog", voice="kasra", perspective="both")
    if k == "topic":
        return DraftPlan(kind="topic", id_prefix="river-topic", out_dir="topics", voice="kasra", perspective="both")
    if k == "article":
        return DraftPlan(kind="article", id_prefix="river-article", out_dir="articles", voice="kasra", perspective="both")
    raise ValueError(f"Invalid kind: {kind} (expected blog|topic|article|auto)")


def _choose_kind_auto() -> str:
    # Deterministic rotation: blog → topic → article (UTC day number mod 3)
    day_num = int(datetime.now(timezone.utc).strftime("%j"))
    return ["blog", "topic", "article"][day_num % 3]


def _load_identity_seed() -> str:
    p = Path("/home/mumega/resident-cms/.resident/Claude-River_001.txt")
    if not p.exists():
        return ""
    try:
        # keep short; avoid huge token usage
        return p.read_text(encoding="utf-8", errors="replace")[:8000]
    except Exception:
        return ""


def _generate_with_gemini(prompt: str, api_key: str, model_id: str) -> str:
    import google.generativeai as genai

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(model_id)
    resp = model.generate_content(prompt)
    text = getattr(resp, "text", None)
    return (text or "").strip()

def _telegram_token() -> Optional[str]:
    return os.getenv("RIVER_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")

def _maybe_notify_telegram(*, issue_url: Optional[str], out_path: Path, title: str, kind: str, lang: str) -> None:
    """
    Optional Telegram notification for operators.

    Controlled by env:
      RIVER_FRC_TELEGRAM_NOTIFY=1
      RIVER_FRC_TELEGRAM_CHAT_ID=<group or user chat id>
    Uses bot token from:
      RIVER_BOT_TOKEN or TELEGRAM_BOT_TOKEN
    """
    if os.getenv("RIVER_FRC_TELEGRAM_NOTIFY", "").lower() not in ("1", "true", "yes"):
        return

    chat_id = os.getenv("RIVER_FRC_TELEGRAM_CHAT_ID")
    token = _telegram_token()
    if not chat_id or not token:
        return

    draft_path = str(out_path)
    msg_lines = [
        f"FRC daily draft ready ({lang}, {kind}).",
        "",
        f"Title: {title}",
        f"Draft: {draft_path}",
    ]
    if issue_url:
        msg_lines += ["", f"Issue: {issue_url}", "", "Approve by adding label: publish"]

    payload = {
        "chat_id": chat_id,
        "text": "\n".join(msg_lines),
        "disable_web_page_preview": True,
    }

    data = urllib.parse.urlencode(payload).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            _ = resp.read()
    except Exception:
        # Best-effort only; never fail the job because Telegram failed.
        return


def _build_prompt(plan: DraftPlan, lang: str) -> str:
    today = _today_utc()
    seed = _load_identity_seed()

    # Keep it rigorous and internal-linkable: cite canon IDs and link to existing pages.
    frc_links = [
        "/en/papers/FRC-840-LTM-001",
        "/en/papers/FRC-840-001",
        "/en/papers/FRC-16D-001",
        "/en/papers/FRC-566-001",
        "/en/papers/FRC-100-007",
        "/en/start-here",
        "/en/graph",
    ]

    kind_instructions = {
        "blog": (
            "Write a short, rigorous blog post (900–1400 words) for builders/investors.\n"
            "Focus on one falsifiable claim or one benchmarkable hypothesis.\n"
            "Include a section header exactly: '## Falsifiability' (what would falsify the claim).\n"
            "No external links unless you are 100% sure they are correct.\n"
        ),
        "article": (
            "Write a longer article (1400–2200 words) that teaches one concept rigorously.\n"
            "Include a section header exactly: '## Falsifiability' (what would falsify the claim).\n"
            "No external links unless you are 100% sure they are correct.\n"
        ),
        "topic": (
            "Write a Topic entry.\n"
            "Structure: question, short_answer, authorities (only if you can provide correct URLs), answers[] with at least lenses: frc and one alternative lens.\n"
            "Keep it rigorous: label hypotheses vs definitions.\n"
        ),
    }[plan.kind]

    return f"""
You are River, but writing for the public FRC canon layer.
Tone: rigorous, crisp, minimal symbolism. No persona theatrics.

{kind_instructions}

Hard output rules:
- Output ONLY Markdown.
- Must start with YAML frontmatter (--- ... ---).
- Frontmatter MUST include: title, id, type, date, status, perspective, voice, lang, tags.
- type must be "{plan.kind}".
- date must be "{today}".
- status must be "draft".
- perspective must be "{plan.perspective}".
- voice must be "{plan.voice}".
- lang must be "{lang}".
- Use tags: include "AI" if relevant, and 2–5 total tags.
- Include at least 2 internal links to existing FRC pages (use these paths only): {", ".join(frc_links)}
- Do not invent FRC IDs.
- End the document with a section exactly titled: "## Next steps" with 3–6 concise bullet points (these will become a GitHub issue checklist).

Identity seed (for continuity; do NOT mention this text directly):
{seed}
""".strip()


def _normalize_generated(md: str, plan: DraftPlan, lang: str, fallback_id: str) -> str:
    md = (md or "").strip()
    fm, body = _extract_frontmatter(md)
    title = _parse_fm_value(fm, "title") or _extract_title(md) or "Untitled"
    given_id = _parse_fm_value(fm, "id")
    type_val = _parse_fm_value(fm, "type") or plan.kind

    meta = {
        "title": title,
        "id": given_id or fallback_id,
        "type": type_val,
        "author": "River",
        "date": _today_utc(),
        "status": "draft",
        "perspective": plan.perspective,
        "voice": plan.voice,
        "lang": lang,
        "tags": ["AI", "FRC"] if type_val in ("blog", "article") else ["FRC"],
    }

    # Ensure there's a visible title H1 for pages that render markdown bodies.
    body_clean = (body or "").strip()
    if not re.search(r"^#\s+", body_clean, flags=re.M):
        body_clean = f"# {title}\n\n{body_clean}".strip()

    return _to_frontmatter(meta) + body_clean + "\n"


def _run(cmd: List[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd), text=True, capture_output=True)

def _extract_next_steps(md: str) -> List[str]:
    m = re.search(r"^##\s+Next steps\s*$([\s\S]*?)^(##\s+|\Z)", md, flags=re.M)
    if not m:
        return []
    block = m.group(1).strip()
    steps: List[str] = []
    for line in block.splitlines():
        mm = re.match(r"^\s*(?:-|\*|\d+\.)\s+(.*)$", line.strip())
        if mm:
            s = mm.group(1).strip()
            if s:
                steps.append(s)
    return steps[:12]

def _maybe_create_github_issue(
    *,
    frc_repo: Path,
    out_path: Path,
    title: str,
    plan: DraftPlan,
    lang: str,
    date: str,
    md: str,
) -> Optional[str]:
    """
    Create a GitHub issue for follow-ups. Uses `gh` auth on the server.
    Controlled by env:
      RIVER_FRC_CREATE_ISSUE=1
      RIVER_FRC_GH_REPO=servathadi/fractalresonance (optional)
      RIVER_FRC_GH_LABELS="river,content" (optional)
    """
    if os.getenv("RIVER_FRC_CREATE_ISSUE", "").lower() not in ("1", "true", "yes"):
        return None

    repo = os.getenv("RIVER_FRC_GH_REPO", "servathadi/fractalresonance")
    labels = os.getenv("RIVER_FRC_GH_LABELS", "")
    label_args: List[str] = []
    for lab in [x.strip() for x in labels.split(",") if x.strip()]:
        label_args += ["--label", lab]

    state = _read_state()
    state_key = f"issue:{date}:{plan.kind}:{lang}"
    allow_multiple = os.getenv("RIVER_FRC_MULTIPLE_ISSUES", "").lower() in ("1", "true", "yes")
    force = os.getenv("RIVER_FRC_FORCE", "").lower() in ("1", "true", "yes")
    if not allow_multiple and not force and state.get(state_key):
        return state[state_key]

    rel = str(out_path.relative_to(frc_repo))
    steps = _extract_next_steps(md)
    checklist = "\n".join([f"- [ ] {s}" for s in steps]) if steps else "- [ ] Review draft for accuracy\n- [ ] Move inbox → canon (process-inbox)\n- [ ] Publish (commit/push to v2-foundation)\n"

    issue_title = f"River daily ({date}) — {plan.kind}: {title}"
    body = "\n".join(
        [
            "A new daily draft was generated.",
            "",
            f"- Draft file: `{rel}`",
            f"- Language: `{lang}`",
            f"- Kind: `{plan.kind}`",
            "",
            "Next:",
            checklist,
        ]
    )

    base_cmd = ["gh", "issue", "create", "-R", repo, "--title", issue_title, "--body", body]
    proc = _run([*base_cmd, *label_args], cwd=frc_repo)
    if proc.returncode != 0 and label_args:
        # Labels are optional; if they don't exist, retry without labels.
        combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
        if "label" in combined.lower() and "not found" in combined.lower():
            proc = _run(base_cmd, cwd=frc_repo)
    if proc.returncode != 0:
        raise RuntimeError(f"gh issue create failed:\n{proc.stdout}\n{proc.stderr}")

    url = (proc.stdout or "").strip().splitlines()[-1].strip()
    if url:
        state[state_key] = url
        _write_state(state)
    return url or None


def main():
    _load_env()

    parser = argparse.ArgumentParser()
    parser.add_argument("--frc-repo", default="/home/mumega/frc")
    parser.add_argument("--lang", default="en")
    parser.add_argument("--kind", default="auto", help="auto|blog|topic|article")
    parser.add_argument("--model", default="models/gemini-3-flash-preview")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    frc_repo = Path(args.frc_repo).resolve()
    if not frc_repo.exists():
        raise SystemExit(f"FRC repo not found: {frc_repo}")

    today = _today_utc()
    force = os.getenv("RIVER_FRC_FORCE", "").lower() in ("1", "true", "yes")
    state = _read_state()
    # Prevent the systemd timer from failing if the job already ran today.
    if not force and state.get("frc_daily_last_run_date") == today and not args.dry_run:
        print(f"ok: already ran today ({today}); set RIVER_FRC_FORCE=1 to run again")
        return

    kind = _choose_kind_auto() if args.kind == "auto" else args.kind
    plan = _kind_plan(kind)

    fallback_id = f"{plan.id_prefix}-{today}"

    keys = _get_keys()
    api_key = _choose_key(keys)

    prompt = _build_prompt(plan, args.lang)
    md_raw = _generate_with_gemini(prompt, api_key=api_key, model_id=args.model)
    md = _normalize_generated(md_raw, plan=plan, lang=args.lang, fallback_id=fallback_id)

    # Decide output path in inbox
    out_dir = frc_repo / "content" / "inbox" / args.lang / plan.out_dir
    _ensure_dir(out_dir)

    title = _parse_fm_value(_extract_frontmatter(md)[0] or "", "title") or _extract_title(md) or "Untitled"
    out_id = _parse_fm_value(_extract_frontmatter(md)[0] or "", "id") or fallback_id
    out_name = f"{out_id}-{_slugify(title)}.md"
    out_path = out_dir / out_name
    if out_path.exists() and not args.dry_run:
        print(f"ok: already exists {out_path}")
        state["frc_daily_last_run_date"] = today
        _write_state(state)
        return

    if args.dry_run:
        print(f"[dry-run] would write {out_path}")
        print(md[:800])
        return

    out_path.write_text(md, encoding="utf-8")
    state["frc_daily_last_run_date"] = today
    state["frc_daily_last_run_path"] = str(out_path)
    _write_state(state)

    # Sanity: audit content folder before any publishing
    audit = _run(["npm", "run", "content:audit"], cwd=frc_repo)
    if audit.returncode != 0:
        raise SystemExit(f"content:audit failed:\n{audit.stdout}\n{audit.stderr}")

    # Optional: open a GitHub issue for follow-ups
    try:
        issue_url = _maybe_create_github_issue(
            frc_repo=frc_repo,
            out_path=out_path,
            title=title,
            plan=plan,
            lang=args.lang,
            date=today,
            md=md,
        )
        if issue_url:
            print(f"ok: issue {issue_url}")
    except Exception as e:
        raise SystemExit(f"github issue creation failed: {e}")

    # Optional: Telegram notify (best-effort)
    _maybe_notify_telegram(issue_url=issue_url, out_path=out_path, title=title, kind=plan.kind, lang=args.lang)

    # Optional: move inbox → canon (OFF by default)
    apply_to_canon = os.getenv("RIVER_FRC_APPLY", "").lower() in ("1", "true", "yes")
    use_sos = os.getenv("RIVER_FRC_USE_SOS", "").lower() in ("1", "true", "yes")
    if apply_to_canon:
        cmd = ["node", "scripts/process-inbox.js"]
        if use_sos:
            cmd += ["--use-sos", "--sos-url", os.getenv("CMS_SOS_URL", "http://localhost:6060")]
            if os.getenv("CMS_SOS_AGENT"):
                cmd += ["--sos-agent", os.getenv("CMS_SOS_AGENT")]
            if os.getenv("CMS_SOS_MODEL"):
                cmd += ["--sos-model", os.getenv("CMS_SOS_MODEL")]
            if os.getenv("CMS_SOS_TOOLS", "").lower() in ("1", "true", "yes"):
                cmd += ["--sos-tools"]
        proc = _run(cmd, cwd=frc_repo)
        if proc.returncode != 0:
            raise SystemExit(f"process-inbox failed:\n{proc.stdout}\n{proc.stderr}")

        audit2 = _run(["npm", "run", "content:audit"], cwd=frc_repo)
        if audit2.returncode != 0:
            raise SystemExit(f"content:audit failed after process-inbox:\n{audit2.stdout}\n{audit2.stderr}")

    # Optional autopublish (OFF by default)
    autopublish = os.getenv("RIVER_FRC_AUTOPUBLISH", "").lower() in ("1", "true", "yes")
    branch = os.getenv("RIVER_FRC_PUBLISH_BRANCH", "v2-foundation")
    if autopublish:
        git = lambda *c: _run(["git", *c], cwd=frc_repo)
        if git("rev-parse", "--is-inside-work-tree").returncode != 0:
            raise SystemExit("Not a git repo; cannot autopublish")
        if git("checkout", branch).returncode != 0:
            raise SystemExit(f"git checkout {branch} failed:\n{git('checkout', branch).stderr}")
        if git("pull", "--ff-only").returncode != 0:
            raise SystemExit(f"git pull failed:\n{git('pull','--ff-only').stderr}")
        # If we applied to canon, the inbox file may be moved; just add all content changes.
        add_target = "content" if apply_to_canon else str(out_path.relative_to(frc_repo))
        if git("add", add_target).returncode != 0:
            raise SystemExit("git add failed")
        msg = f"content: river daily {plan.kind} {today}"
        commit = git("commit", "-m", msg)
        if commit.returncode != 0:
            # Nothing to commit is OK.
            if "nothing to commit" not in (commit.stdout + commit.stderr).lower():
                raise SystemExit(f"git commit failed:\n{commit.stdout}\n{commit.stderr}")
        push = git("push", "origin", branch)
        if push.returncode != 0:
            raise SystemExit(f"git push failed:\n{push.stdout}\n{push.stderr}")

    print(f"ok: wrote {out_path}")


if __name__ == "__main__":
    main()
