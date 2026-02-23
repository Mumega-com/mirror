#!/usr/bin/env python3
"""
River → FRC Issue Publisher

Watches GitHub issues for an approval label (default: `publish`).
When found, it:
  1) Locates the referenced draft file in the issue body (content/inbox/...)
  2) Moves it into canon (content/<lang>/<typeDir>/<id>.md) and marks status=published
  3) Runs content audit
  4) Commits + pushes to the publish branch
  5) Comments on the issue and optionally closes it

Safe by default:
- Does nothing unless explicitly labeled `publish`.

Requires:
- `gh` authenticated for the `mumega` user
- git push access to the repo
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
from typing import Dict, List, Optional, Tuple


TYPE_TO_DIR = {
    "paper": "papers",
    "papers": "papers",
    "topic": "topics",
    "topics": "topics",
    "concept": "concepts",
    "concepts": "concepts",
    "article": "articles",
    "articles": "articles",
    "blog": "blog",
    "blogs": "blog",
    "book": "books",
    "books": "books",
    "note": "articles",
    "notes": "articles",
}


def _load_env():
    try:
        from dotenv import load_dotenv
    except Exception:
        return
    load_dotenv("/home/mumega/mirror/.env", override=False)
    load_dotenv("/home/mumega/SOS/.env", override=False)


def _run(cmd: List[str], cwd: Optional[Path] = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd) if cwd else None, text=True, capture_output=True)


def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _state_path() -> Path:
    return Path("/home/mumega/.mumega/river_publish_state.json")


def _read_state() -> dict:
    try:
        return json.loads(_state_path().read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_state(state: dict) -> None:
    p = _state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def _parse_frontmatter(md: str) -> Tuple[Dict[str, str], str]:
    m = re.match(r"^(?:\ufeff)?---\s*\n([\s\S]*?)\n---\s*\n?", md)
    if not m:
        return {}, md
    fm_raw = m.group(1)
    body = md[m.end() :]
    fm: Dict[str, str] = {}
    for line in fm_raw.splitlines():
        mm = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", line.strip())
        if not mm:
            continue
        k = mm.group(1)
        v = mm.group(2).strip().strip('"')
        fm[k] = v
    return fm, body

def _extract_frontmatter_block(md: str) -> str:
    m = re.match(r"^(?:\ufeff)?---\s*\n([\s\S]*?)\n---\s*\n?", md)
    return m.group(1) if m else ""

def _count_next_steps(md: str) -> int:
    m = re.search(r"^##\s+Next steps\s*$([\s\S]*?)^(##\s+|\Z)", md, flags=re.M)
    if not m:
        return 0
    block = m.group(1)
    count = 0
    for line in block.splitlines():
        if re.match(r"^\s*(?:-|\*|\d+\.)\s+\S", line.strip()):
            count += 1
    return count

def _count_internal_links(md: str, lang: str) -> int:
    # Markdown links: ](/en/...) or ](/<lang>/...) or ](https://fractalresonance.com/<lang>/...)
    rel = len(re.findall(r"\]\(/(?:en|es|fr|fa)/", md))
    rel += len(re.findall(rf"\]\(/{re.escape(lang)}/", md))
    abs_ = len(re.findall(rf"\]\(https://fractalresonance\.com/(?:{re.escape(lang)}|en|es|fr|fa)/", md))
    wiki = len(re.findall(r"\[\[[^\]]+\]\]", md))
    return rel + abs_ + wiki

def _list_external_domains(md: str) -> List[str]:
    domains: List[str] = []
    for m in re.finditer(r"https?://([A-Za-z0-9.-]+)", md):
        d = m.group(1).lower()
        if d not in domains:
            domains.append(d)
    return domains

def _validate_draft_for_publish(draft_md: str, draft_rel: str) -> Tuple[bool, str]:
    fm, _ = _parse_frontmatter(draft_md)
    fm_block = _extract_frontmatter_block(draft_md)
    lang = (fm.get("lang") or "en").lower()
    typ = (fm.get("type") or "").lower()
    status = (fm.get("status") or "").lower()

    required = ["title", "id", "type", "date", "status", "perspective", "voice", "lang"]
    missing = [k for k in required if not (fm.get(k) or "").strip()]
    if missing:
        return False, f"Missing required frontmatter fields: {', '.join(missing)}"

    if status not in ("draft", "published"):
        return False, f"Unexpected status `{status}` (expected draft)"

    next_steps = _count_next_steps(draft_md)
    if next_steps < 3:
        return False, "Missing `## Next steps` (need 3–6 bullets) so work stays incremental."

    internal_links = _count_internal_links(draft_md, lang=lang)
    if internal_links < 2:
        return False, "Too few internal links (need ≥2 links into existing FRC pages)."

    # Placeholders
    if "example.com" in draft_md.lower():
        return False, "Contains placeholder link `example.com`."
    if re.search(r"\bTODO\b", draft_md):
        return False, "Contains TODO placeholders."

    # External links: allow only a small allowlist (otherwise force manual review)
    allowed = {"fractalresonance.com", "mumega.com", "github.com", "doi.org", "arxiv.org"}
    external = [d for d in _list_external_domains(draft_md) if d not in allowed]
    if external:
        return False, f"External link domains not allowed for auto-publish: {', '.join(external)}"

    if typ == "topic":
        for k in ["question:", "short_answer:", "answers:"]:
            if k not in fm_block:
                return False, f"Topic missing `{k}` in frontmatter."
        if "lens:" not in draft_md:
            return False, "Topic answers should include at least one `lens:` entry."
    elif typ in ("blog", "article"):
        if not re.search(r"^##\s+(Falsifiability|What would falsify this\??)\s*$", draft_md, flags=re.M | re.I):
            return False, "Missing `## Falsifiability` section (or `## What would falsify this?`)."

    return True, "ok"


def _render_frontmatter(fm: Dict[str, object]) -> str:
    lines = ["---"]
    for k, v in fm.items():
        if v is None:
            continue
        if isinstance(v, list):
            safe = ", ".join(json.dumps(x) for x in v)
            lines.append(f"{k}: [{safe}]")
        elif isinstance(v, bool):
            lines.append(f"{k}: {'true' if v else 'false'}")
        else:
            lines.append(f"{k}: {json.dumps(v) if isinstance(v, str) else v}")
    lines.append("---\n")
    return "\n".join(lines)


def _extract_draft_paths(issue_body: str) -> List[str]:
    # Expected format from daily generator:
    # - Draft file: `content/inbox/en/.../foo.md`
    paths: List[str] = []
    for m in re.finditer(r"Draft file:\s*`([^`]+)`", issue_body or ""):
        p = m.group(1).strip()
        if p and p not in paths:
            paths.append(p)
    return paths


def _ensure_label(repo: str, name: str, color: str = "1f6feb") -> None:
    if not name:
        return
    # Use `gh api` because older `gh` builds (like Ubuntu's) may not ship `gh label ...`.
    # Check exists
    getp = _run(["gh", "api", f"repos/{repo}/labels/{name}"], cwd=None)
    if getp.returncode == 0:
        return
    # Create best-effort
    _run(
        [
            "gh",
            "api",
            "-X",
            "POST",
            f"repos/{repo}/labels",
            "-f",
            f"name={name}",
            "-f",
            f"color={color}",
            "-f",
            "description=Auto-managed by River",
        ],
        cwd=None,
    )

def _telegram_token() -> Optional[str]:
    return os.getenv("RIVER_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")

def _maybe_notify_telegram(text: str) -> None:
    """
    Optional Telegram notification.
    Env:
      RIVER_FRC_TELEGRAM_NOTIFY=1
      RIVER_FRC_TELEGRAM_CHAT_ID=...
    """
    if os.getenv("RIVER_FRC_TELEGRAM_NOTIFY", "").lower() not in ("1", "true", "yes"):
        return
    chat_id = os.getenv("RIVER_FRC_TELEGRAM_CHAT_ID")
    token = _telegram_token()
    if not chat_id or not token:
        return
    payload = {
        "chat_id": chat_id,
        "text": text,
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
        return


@dataclass(frozen=True)
class PublishResult:
    ok: bool
    message: str
    commit: Optional[str] = None


def _canonize_one(frc_repo: Path, draft_rel: str) -> Tuple[Optional[Path], Optional[Path], str]:
    draft_path = (frc_repo / draft_rel).resolve()
    if not draft_path.exists():
        return None, None, f"Draft file not found: {draft_rel}"

    md = draft_path.read_text(encoding="utf-8", errors="replace")
    ok, why = _validate_draft_for_publish(md, draft_rel)
    if not ok:
        return None, None, f"Draft failed publish gate: {why}"

    fm, body = _parse_frontmatter(md)
    lang = (fm.get("lang") or "en").lower()
    type_raw = (fm.get("type") or "article").lower()
    id_val = fm.get("id") or ""

    out_dir_name = TYPE_TO_DIR.get(type_raw, "articles")
    if not id_val:
        return None, None, f"Missing frontmatter id in {draft_rel}"

    out_path = frc_repo / "content" / lang / out_dir_name / f"{id_val}.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        return None, None, f"Canon already exists: {out_path.relative_to(frc_repo)}"

    fm_out: Dict[str, object] = dict(fm)
    fm_out["status"] = "published"
    fm_out.setdefault("published_at", _today_utc())

    out_md = _render_frontmatter(fm_out) + (body or "").lstrip()
    out_path.write_text(out_md, encoding="utf-8")

    processed_dir = frc_repo / "content" / "inbox" / "_processed" / Path(draft_rel).parent.relative_to("content/inbox")
    processed_dir.mkdir(parents=True, exist_ok=True)
    processed_path = processed_dir / Path(draft_rel).name
    draft_path.replace(processed_path)

    return out_path, processed_path, "ok"


def _git_publish(frc_repo: Path, branch: str, msg: str) -> str:
    def git(*args: str) -> subprocess.CompletedProcess:
        return _run(["git", *args], cwd=frc_repo)

    if git("rev-parse", "--is-inside-work-tree").returncode != 0:
        raise RuntimeError("Not a git repo")
    if git("checkout", branch).returncode != 0:
        raise RuntimeError(f"git checkout {branch} failed:\n{git('checkout', branch).stderr}")
    pull = git("pull", "--ff-only")
    if pull.returncode != 0:
        raise RuntimeError(f"git pull failed:\n{pull.stdout}\n{pull.stderr}")

    add = git("add", "content")
    if add.returncode != 0:
        raise RuntimeError(f"git add failed:\n{add.stderr}")

    commit = git("commit", "-m", msg)
    combined = (commit.stdout or "") + "\n" + (commit.stderr or "")
    if commit.returncode != 0 and "nothing to commit" not in combined.lower():
        raise RuntimeError(f"git commit failed:\n{combined}")

    head = git("rev-parse", "HEAD")
    if head.returncode != 0:
        raise RuntimeError("git rev-parse HEAD failed")
    sha = (head.stdout or "").strip()

    push = git("push", "origin", branch)
    if push.returncode != 0:
        raise RuntimeError(f"git push failed:\n{push.stdout}\n{push.stderr}")

    return sha


def main():
    _load_env()

    parser = argparse.ArgumentParser()
    parser.add_argument("--frc-repo", default="/home/mumega/frc")
    parser.add_argument("--repo", default="servathadi/fractalresonance")
    parser.add_argument("--label", default=os.getenv("RIVER_FRC_PUBLISH_LABEL", "publish"))
    parser.add_argument("--branch", default=os.getenv("RIVER_FRC_PUBLISH_BRANCH", "v2-foundation"))
    parser.add_argument("--close", action="store_true", default=os.getenv("RIVER_FRC_CLOSE_ISSUE", "").lower() in ("1", "true", "yes"))
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    frc_repo = Path(args.frc_repo).resolve()
    if not frc_repo.exists():
        raise SystemExit(f"FRC repo not found: {frc_repo}")

    # Ensure label exists (best effort)
    _ensure_label(args.repo, args.label, color="d4c5f9")
    _ensure_label(args.repo, "published", color="0e8a16")
    _ensure_label(args.repo, "needs-fix", color="d73a4a")

    state = _read_state()
    published = set(state.get("published_issues", []))

    # Get issues queued for publish
    proc = _run(
        [
            "gh",
            "issue",
            "list",
            "-R",
            args.repo,
            "--label",
            args.label,
            "--state",
            "open",
            "--limit",
            "20",
            "--json",
            "number,title,url,body",
        ],
        cwd=frc_repo,
    )
    if proc.returncode != 0:
        raise SystemExit(f"gh issue list failed:\n{proc.stdout}\n{proc.stderr}")

    issues = json.loads(proc.stdout or "[]")
    if not issues:
        print("ok: no publish issues")
        return

    for it in issues:
        num = int(it["number"])
        if num in published:
            continue

        body = it.get("body") or ""
        draft_paths = _extract_draft_paths(body)
        if not draft_paths:
            # Comment and skip
            _run(["gh", "issue", "comment", "-R", args.repo, str(num), "--body", "I can't find a `Draft file: ...` line to publish."], cwd=frc_repo)
            continue

        canon_paths: List[str] = []
        processed_paths: List[str] = []
        blocked = False
        for p in draft_paths:
            out_path, processed_path, msg = _canonize_one(frc_repo, p)
            if not out_path:
                _run(["gh", "issue", "comment", "-R", args.repo, str(num), "--body", f"Publish blocked: {msg}"], cwd=frc_repo)
                blocked = True
                continue
            canon_paths.append(str(out_path.relative_to(frc_repo)))
            processed_paths.append(str(processed_path.relative_to(frc_repo)))

        if blocked:
            # Remove publish label to avoid repeated retries; mark needs-fix.
            _run(["gh", "issue", "edit", "-R", args.repo, str(num), "--remove-label", args.label], cwd=frc_repo)
            _run(["gh", "issue", "edit", "-R", args.repo, str(num), "--add-label", "needs-fix"], cwd=frc_repo)
            continue

        if not canon_paths:
            continue

        audit = _run(["npm", "run", "content:audit"], cwd=frc_repo)
        if audit.returncode != 0:
            _run(["gh", "issue", "comment", "-R", args.repo, str(num), "--body", f"Publish failed: content:audit failed.\n\n```\n{audit.stdout}\n{audit.stderr}\n```"], cwd=frc_repo)
            continue

        try:
            sha = _git_publish(frc_repo, args.branch, msg=f"content: publish from issue #{num}")
        except Exception as e:
            _run(["gh", "issue", "comment", "-R", args.repo, str(num), "--body", f"Publish failed: {e}"], cwd=frc_repo)
            continue

        # Mark issue as published
        _run(["gh", "issue", "edit", "-R", args.repo, str(num), "--remove-label", args.label], cwd=frc_repo)
        _run(["gh", "issue", "edit", "-R", args.repo, str(num), "--add-label", "published"], cwd=frc_repo)
        comment = "\n".join(
            [
                f"Published to `{args.branch}`.",
                "",
                f"- Commit: `{sha}`",
                *[f"- Canon: `{p}`" for p in canon_paths],
            ]
        )
        _run(["gh", "issue", "comment", "-R", args.repo, str(num), "--body", comment], cwd=frc_repo)
        _maybe_notify_telegram(f"FRC published issue #{num} to {args.branch}.\nCommit: {sha}\n{it.get('url','')}")
        if args.close:
            _run(["gh", "issue", "close", "-R", args.repo, str(num)], cwd=frc_repo)

        published.add(num)
        state["published_issues"] = sorted(published)
        state["updated_at"] = _today_utc()
        _write_state(state)

        print(f"ok: published issue #{num} -> {sha}")
        if args.once:
            return


if __name__ == "__main__":
    main()
