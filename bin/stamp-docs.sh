#!/usr/bin/env bash
# stamp-docs.sh — enforce/backfill the `Last updated: YYYY-MM-DD HH:MM` doc-timestamp convention
# (issue #97; time-of-day #146-T1, rules/doc-conventions.md). Dependency-free: bash + coreutils +
# git only.
#
# Usage:
#   stamp-docs.sh [--check|--check-time|--upgrade] [paths...]
#
#   --check      Report every *.md doc missing a `Last updated:` stamp (LENIENT — presence-only, a
#                date-only stamp still counts as stamped); exit 1 if any are missing, 0 otherwise.
#                Writes nothing — use it as a CI/pre-commit gate that stays compatible with
#                downstream scaffolds that may not have re-stamped with time-of-day yet.
#   --check-time STRICT opt-in: report every *.md doc whose stamp lacks the `HH:MM` payload —
#                missing entirely, OR present but date-only; exit 1 if any. Writes nothing.
#   --upgrade    Rewrite a DATE-ONLY `Last updated: YYYY-MM-DD` stamp to `YYYY-MM-DD HH:MM` IN
#                PLACE, preserving the bare/blockquote style. The bold-list ADR form
#                (`- **Last updated:** YYYY-MM-DD`) is intentionally left alone — hand-update those.
#                Idempotent: a stamp that already carries a time is untouched.
#   (default)    Backfill: insert a `Last updated: <date+time>` line into each doc that lacks a
#                stamp at all, placed right after the file's first `# heading` (or at the top if
#                there is none). Never touches a doc that already has ANY stamp (presence-only
#                idempotency — no churn; use --upgrade to add time to an existing date-only stamp).
#
#   paths...  Files and/or directories to scan. A directory is walked recursively for *.md.
#             With no paths, defaults to `docs` and `rules` under the current directory.
#
# The stamped date+time is the file's LAST COMMIT datetime
# (`git log -1 --date=format:'%Y-%m-%d %H:%M' --format=%cd -- <file>`, 24h, no seconds/timezone —
# commit-local time), so a backfill reflects real history rather than "today". Untracked files (no
# commit yet) fall back to the filesystem mtime. Already-stamped docs are left untouched by the
# default write pass (idempotent).
set -euo pipefail

# Recognizes every stamp style: bare (`Last updated: ...`), blockquote (`> Last updated: ...`), the
# bold-list ADR form (`- **Last updated:** ...`, #146-T1), and markdown-emphasis-wrapped forms
# (`_Last updated: ..._`, `*...*`, `__...__`, #124). The leading-prefix group allows an optional
# blockquote `>` and/or list marker `-`, then any run of emphasis chars `_`/`*` (single or paired)
# before the literal `Last updated:` phrase — so an italicized/bolded hand-written stamp is NOT
# false-flagged as missing and never gets a SECOND (canonical) stamp on backfill (#124 edge case 2).
# It still requires the literal `Last updated:` phrase, so a non-stamp line can't match by accident.
STAMP_RE='^[[:space:]]*(>[[:space:]]*)?(-[[:space:]]*)?[_*]*[[:space:]]*[Ll]ast [Uu]pdated:'
# A stamp line that ALREADY carries the HH:MM payload, in any of the three styles above.
STAMP_TIME_RE='[Ll]ast [Uu]pdated:[[:space:]]*\**[[:space:]]*[0-9]{4}-[0-9]{2}-[0-9]{2}[[:space:]]+[0-9]{2}:[0-9]{2}'
# A DATE-ONLY bare/blockquote stamp (no time yet) — deliberately excludes the bold-list form (that
# form never starts with `>` or nothing; it starts with `- **`, which this pattern does not match).
STAMP_DATEONLY_BQ_RE='^[[:space:]]*>?[[:space:]]*[Ll]ast [Uu]pdated:[[:space:]]*[0-9]{4}-[0-9]{2}-[0-9]{2}[[:space:]]*$'

usage() { sed -n '2,27p' "$0" | sed 's/^# \{0,1\}//'; }

check_only=0
check_time_only=0
upgrade_only=0
paths=()
for arg in "$@"; do
  case "$arg" in
    --check) check_only=1 ;;
    --check-time) check_time_only=1 ;;
    --upgrade) upgrade_only=1 ;;
    -h|--help) usage; exit 0 ;;
    --) ;;
    -*) printf 'stamp-docs.sh: unknown option: %s\n' "$arg" >&2; exit 2 ;;
    *) paths+=("$arg") ;;
  esac
done
[ "${#paths[@]}" -eq 0 ] && paths=(docs rules)

# --- collect target *.md files (files as-is; directories walked recursively) --------------------
docs=()
for p in "${paths[@]}"; do
  if [ -f "$p" ]; then
    case "$p" in *.md) docs+=("$p") ;; esac
  elif [ -d "$p" ]; then
    while IFS= read -r f; do docs+=("$f"); done < <(find "$p" -type f -name '*.md' | sort)
  fi
done

has_stamp() { grep -qE "$STAMP_RE" "$1"; }
has_time_stamp() { grep -qE "$STAMP_TIME_RE" "$1"; }

# Last-commit DATE+TIME for a tracked file (`YYYY-MM-DD HH:MM`, 24h, commit-local time, no
# seconds/TZ); empty if untracked / not a repo.
git_date() { git log -1 --date=format:'%Y-%m-%d %H:%M' --format=%cd -- "$1" 2>/dev/null || true; }

# Filesystem mtime as `YYYY-MM-DD HH:MM` — GNU (`-d @`) and BSD/macOS (`-r`) date both handled.
mtime_date() {
  local f="$1" epoch
  # GNU form FIRST. `-f` is "format" to BSD stat but `--file-system` to GNU stat, so on Linux
  # `stat -f %m` SUCCEEDS with filesystem info instead of failing — the BSD-first order only
  # recovered here by luck (the subsequent `date -r` chokes on that text and falls through). The
  # same inversion was a hard failure in propagate.sh's _backup_epoch, so don't leave it latent.
  # BSD stat rejects `-c` outright, which makes the fallback a real one in both directions.
  epoch="$(stat -c %Y "$f" 2>/dev/null || stat -f %m "$f" 2>/dev/null)"
  case "$epoch" in ''|*[!0-9]*) epoch="" ;; esac
  if [ -n "$epoch" ]; then
    date -d "@$epoch" +'%F %H:%M' 2>/dev/null && return   # GNU
    date -r "$epoch"  +'%F %H:%M' 2>/dev/null && return   # BSD/macOS
  fi
  # Both stat forms failed (TOCTOU: file vanished between `find` and `stat`, or a platform with
  # neither `-c %Y` nor `-f %m`). Emit a breadcrumb so the caller's freshness stamp is observably
  # fabricated, not silently lied about. Return 0 (not non-zero): a stamp failure must not abort
  # the doc walk under `set -e` — a wrong stamp is better than no stamp run at all.
  printf 'stamp-docs.sh: could not stat %s, using current time as mtime\n' "$f" >&2
  date +'%F %H:%M'  # last-ditch: now
}

doc_date() {
  local f="$1" d
  d="$(git_date "$f")"
  [ -n "$d" ] || d="$(mtime_date "$f")"
  printf '%s' "$d"
}

# Insert `Last updated: <date>` after the first real markdown heading, keeping a blank line so it
# reads as its own block. W3 fix: a leading `---`…`---` YAML front-matter fence is skipped first —
# a `#` line INSIDE front-matter is a YAML comment, not a heading, and the stamp must never land in
# it. If there's no heading, the stamp goes right after the front-matter fence, else at the top.
insert_stamp() {
  local f="$1" date="$2" tmp
  tmp="$(mktemp "${TMPDIR:-/tmp}/stamp.XXXXXX")"
  # Primary pass: skip front-matter, then insert after the first `#`..`######` heading.
  awk -v stamp="Last updated: $date" '
    BEGIN { infm = 0; fmdone = 0; ins = 0 }
    NR == 1 && $0 == "---" { infm = 1; print; next }
    infm == 1 && fmdone == 0 { print; if ($0 == "---") { fmdone = 1 } ; next }
    ins == 0 && /^#+[[:space:]]/ { print; print ""; print stamp; ins = 1; next }
    { print }
  ' "$f" > "$tmp"
  # Fallback: no markdown heading found — insert after the front-matter fence, else prepend.
  if ! grep -qF "Last updated: $date" "$tmp"; then
    awk -v stamp="Last updated: $date" '
      BEGIN { infm = 0; fmdone = 0; ins = 0 }
      NR == 1 && $0 == "---" { infm = 1; print; next }
      infm == 1 && fmdone == 0 {
        print
        if ($0 == "---") { fmdone = 1; print ""; print stamp; ins = 1 }
        next
      }
      ins == 0 { print stamp; print ""; ins = 1; print; next }
      { print }
    ' "$f" > "$tmp"
  fi
  mv "$tmp" "$f"
}

# Rewrite a DATE-ONLY bare/blockquote stamp to date+time IN PLACE, preserving the line's PREFIX
# (leading whitespace, an optional `>` prefix) but REPLACING the entire date value with the fresh
# `doc_date` — date AND time sourced from the SAME commit. Never append a new time to the OLD date
# already on the line: if that date has drifted from the file's actual last-commit date (the doc
# was re-committed since the stamp was last touched), appending would weld together a date and a
# time from two different commits — a "2020-01-01 10:30" stamp that never actually occurred
# (adversarial re-review CRITICAL). The bold-list ADR form never matches STAMP_DATEONLY_BQ_RE (it
# starts with `- **`, not `>`/nothing), so it's untouched by construction — no separate exclusion
# logic needed. Returns 0 if a line was upgraded, 1 if nothing matched (already has a time, or has
# no stamp at all — both are "no-op", not errors).
upgrade_doc() {
  local f="$1" ln full tmp
  ln="$(grep -nE "$STAMP_DATEONLY_BQ_RE" "$f" | head -1 | cut -d: -f1)"
  [ -n "$ln" ] || return 1
  full="$(doc_date "$f")"     # "YYYY-MM-DD HH:MM" -- date+time from the SAME commit
  tmp="$(mktemp "${TMPDIR:-/tmp}/stamp-upg.XXXXXX")"
  awk -v n="$ln" -v full="$full" '
    NR == n {
      idx = match($0, /[Ll]ast [Uu]pdated:/)
      prefix = substr($0, 1, idx + RLENGTH - 1)
      print prefix " " full
      next
    }
    { print }
  ' "$f" > "$tmp"
  mv "$tmp" "$f"
  return 0
}

if [ "$check_time_only" -eq 1 ]; then
  missing_time=()
  # `${arr[@]+"${arr[@]}"}` — iterate safely when the array is EMPTY. Under `set -u` on bash 3.2
  # (macOS default), a bare `"${arr[@]}"` on an empty array is an "unbound variable" error, which
  # bites when a target set has no docs (or, for `missing` below, when every doc is already stamped
  # — the common fleet case a propagate re-stamp hits). The `+` form expands to nothing when unset.
  for f in ${docs[@]+"${docs[@]}"}; do
    has_time_stamp "$f" && continue
    missing_time+=("$f")
  done
  if [ "${#missing_time[@]}" -gt 0 ]; then
    printf 'Missing time-of-day ("HH:MM") in "Last updated:" stamp:\n' >&2
    for f in "${missing_time[@]}"; do printf '  %s\n' "$f"; done
    exit 1
  fi
  printf 'All %d docs carry a date+time "Last updated:" stamp.\n' "${#docs[@]}"
  exit 0
fi

if [ "$upgrade_only" -eq 1 ]; then
  upgraded=0
  for f in ${docs[@]+"${docs[@]}"}; do
    if upgrade_doc "$f"; then
      printf 'upgraded %s\n' "$f"
      upgraded=$((upgraded + 1))
    fi
  done
  printf 'Upgraded: %d docs (date-only -> date+time), %d unchanged.\n' "$upgraded" "$(( ${#docs[@]} - upgraded ))"
  exit 0
fi

missing=()
for f in ${docs[@]+"${docs[@]}"}; do
  has_stamp "$f" && continue
  missing+=("$f")
done

if [ "$check_only" -eq 1 ]; then
  if [ "${#missing[@]}" -gt 0 ]; then
    printf 'Missing "Last updated:" stamp:\n' >&2
    for f in "${missing[@]}"; do printf '  %s\n' "$f"; done
    exit 1
  fi
  printf 'All %d docs carry a "Last updated:" stamp.\n' "${#docs[@]}"
  exit 0
fi

stamped=0
for f in ${missing[@]+"${missing[@]}"}; do
  insert_stamp "$f" "$(doc_date "$f")"
  printf 'stamped %s\n' "$f"
  stamped=$((stamped + 1))
done
printf 'Done: %d stamped, %d already had a stamp.\n' "$stamped" "$(( ${#docs[@]} - stamped ))"
