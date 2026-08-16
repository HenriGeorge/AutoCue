#!/usr/bin/env bash
# cron-run.sh — sync a dedicated origin/main-pinned worktree, then exec a command in it.
#
# For launchd/cron: a scheduled or event-triggered job must run MERGED main code regardless of what
# branch an interactive session parks the primary checkout on — the shared-checkout drift the
# session_start hook warns about. Generalizes dekamer's dekamer-daemon-run.sh (#430) into a template
# primitive: `claude -p "…"` is one caller, a plain daemon (`npm run …`) is another.
#
#   usage: cron-run.sh <cmd> [args...]
#     cron-run.sh claude -p "/docs-sweep"          --model claude-opus-4-8
#     cron-run.sh claude -p "triage the issues"    --model claude-haiku-4-5-20251001
#     cron-run.sh npm run some-daemon
#
# Opt-in (CRON_ENABLE=1 in .claude/worktrees.conf — a billed/standing run is never a default),
# self-provisioning (launchd never fires the WorktreeCreate lifecycle hook), dependency-free.
set -uo pipefail

log() { printf '[cron-run] %s\n' "$*" >&2; }

[ "$#" -ge 1 ] || { log "usage: cron-run.sh <cmd> [args...]"; exit 2; }

# Repo root = one level up from this script (bin/ sits at the repo root).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
REPO="$(cd "$SCRIPT_DIR/.." && pwd)"
CONF="$REPO/.claude/worktrees.conf"

# --- opt-in gate: silent no-op unless CRON_ENABLE=1 (mirrors #301 VERIFY_ADVERSARIAL_STOP) -------
if [ ! -r "$CONF" ]; then
  log "no readable .claude/worktrees.conf — cron disabled, skipping"
  exit 0
fi
if ! grep -qE '^CRON_ENABLE=1[[:space:]]*(#.*)?$' "$CONF"; then
  log "CRON_ENABLE not set in .claude/worktrees.conf — skipping"
  exit 0
fi

WT="$REPO/.claude/worktrees/cron"

# --- self-provision the dedicated worktree if absent --------------------------------------------
# No PORT allocation (unlike worktree_create.py): scheduled batch jobs run no dev server.
# $WT is provisioned ONLY when it is the TOPLEVEL of its own worktree. `rev-parse --is-inside-work-tree`
# is unsafe here: it walks UP to the primary .git and returns true for ANY plain dir nested inside the
# primary checkout — and $WT lives inside it — so a leftover plain dir would fool the guard, skip the
# add, and send the later `reset --hard` UP into the human's primary checkout (data loss). Compare
# fully-resolved paths: `pwd -P` and `--show-toplevel` both yield /private/var/… so this also sidesteps
# the /var-vs-/private/var symlink mismatch. `worktree prune` first self-heals a stale registration
# left by a deleted dir (else `worktree add` would fail forever on an unattended job).
wt_real="$(cd "$WT" 2>/dev/null && pwd -P || true)"
wt_top="$(git -C "$WT" rev-parse --show-toplevel 2>/dev/null || true)"
if [ -z "$wt_top" ] || [ "$wt_real" != "$wt_top" ]; then
  git -C "$REPO" fetch origin --quiet 2>/dev/null || log "fetch failed during provision — using local refs"
  git -C "$REPO" worktree prune --quiet 2>/dev/null
  if wt_add_err=$(git -C "$REPO" worktree add --detach "$WT" origin/main --quiet 2>&1); then
    # carry gitignored env in (parity with worktree_create.py's .env* copy)
    for f in "$REPO"/.env "$REPO"/.env.*; do
      [ -f "$f" ] || continue
      cp -p "$f" "$WT/" 2>/dev/null || log "env copy failed: $f (continuing)"
    done
  else
    log "worktree add failed — aborting: $wt_add_err"; exit 1
  fi
fi

cd "$WT" 2>/dev/null || { log "dedicated worktree $WT missing — aborting"; exit 1; }

# --- sync to latest main (NON-fatal: a stale run beats a missed run) ----------------------------
# Safe: the worktree is dedicated and never human-edited, so a hard reset can lose nothing.
before="$(git rev-parse HEAD 2>/dev/null || echo unknown)"
if git fetch origin --quiet 2>/dev/null; then
  git reset --hard origin/main --quiet 2>/dev/null || log "reset to origin/main failed — running current checkout"
else
  log "fetch failed — running current checkout"
fi
after="$(git rev-parse HEAD 2>/dev/null || echo unknown)"

# --- conditional deps: only when SETUP=npm AND the lockfile actually changed ---------------------
setup="$(sed -n 's/^SETUP=["'\'']\{0,1\}\([^"'\'' ]*\).*/\1/p' "$CONF" 2>/dev/null | head -1)"
if [ "$setup" = npm ] && [ "$before" != unknown ] && [ "$after" != unknown ] \
   && [ "$before" != "$after" ] \
   && ! git diff --quiet "$before" "$after" -- package-lock.json 2>/dev/null; then
  log "package-lock changed ($before -> $after) — npm install"
  npm install --no-audit --no-fund >&2 || log "npm install failed — running with existing node_modules"
fi

log "synced to $after — exec: $*"
exec "$@"
