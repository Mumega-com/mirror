#!/usr/bin/env bash
#
# mirror-update.sh — pull the latest Mirror from upstream and report what
# changed. Migrations are NOT applied automatically; this script only reminds
# you which ones exist so you can review and run them deliberately.
#
# Usage:
#   scripts/mirror-update.sh
#
# Prereqs:
#   - An "upstream" git remote pointing at the canonical repo. If missing, add:
#       git remote add upstream https://github.com/Mumega-com/mirror.git
#
set -euo pipefail

cd "$(dirname "$0")/.."   # repo root

UPSTREAM_URL="https://github.com/Mumega-com/mirror.git"
BRANCH="main"

# 1. Ensure the upstream remote exists.
if ! git remote get-url upstream >/dev/null 2>&1; then
  echo "ERROR: no 'upstream' remote configured." >&2
  echo "Add it with:" >&2
  echo "  git remote add upstream ${UPSTREAM_URL}" >&2
  exit 1
fi

# 2. Fetch upstream.
echo "==> Fetching upstream..."
git fetch upstream --tags

# 3. Report how far behind we are.
BEHIND="$(git rev-list --count "HEAD..upstream/${BRANCH}" 2>/dev/null || echo 0)"
if [ "${BEHIND}" -eq 0 ]; then
  echo "==> Already up to date with upstream/${BRANCH}."
  exit 0
fi
echo "==> ${BEHIND} commit(s) behind upstream/${BRANCH}:"
git --no-pager log --oneline "HEAD..upstream/${BRANCH}"

# 4. Fast-forward only — refuses if local has diverged (safe).
echo "==> Fast-forwarding to upstream/${BRANCH}..."
git merge --ff-only "upstream/${BRANCH}"

# 5. Remind about migrations — do NOT auto-run psql.
echo
echo "==> Update merged. Pending DB migrations to review and apply manually:"
echo "    (run against your Mirror database after reviewing each file)"
echo
for f in migrations/*.sql; do
  [ -e "$f" ] || continue
  echo "    psql \"\$DATABASE_URL\" < $f"
done
echo
echo "Tip: scripts/migrate.py tracks applied migrations — prefer:"
echo "    python scripts/migrate.py --target mirror --status   # see pending"
echo "    python scripts/migrate.py --target mirror            # apply"
echo
echo "==> Done. Restart the service to pick up code changes:"
echo "    systemctl --user restart mirror"
