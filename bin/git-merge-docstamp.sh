#!/usr/bin/env bash
# git-merge-docstamp — a surgical git merge driver for the `Last updated:` doc stamp.
#
# WHY: every PR bumps a doc's top-of-file `Last updated:` stamp to its own commit datetime, so two
# PRs touching the same doc collide on that one line at rebase/merge — a mechanical, non-semantic
# conflict. The stamp value is REGENERATED from the commit date by bin/stamp-docs.sh, so the
# committed value is derivable: picking the NEWER of two colliding stamps is always safe.
#
# WHAT: run a normal 3-way merge, then auto-resolve ONLY conflict hunks whose every differing line
# is a stamp line (both sides), collapsing them to the newer stamp. Any hunk with a non-stamp
# difference is left as a standard conflict for a human. This is deliberately narrow — it never
# touches real content, so a blanket merge=ours/union (which would drop or double content) is wrong.
#
# Registered per-clone (see hooks/session_start.* + setup.sh) as:
#   git config merge.docstamp.driver '<repo>/bin/git-merge-docstamp.sh %O %A %B %L %P'
# Named by .gitattributes: `*.md merge=docstamp`. Unregistered clones fall back to git's default
# text merge (identical to pre-driver behaviour) — the feature is additive, never load-bearing.
#
# Args (git placeholders): %O=base  %A=ours(result written here)  %B=theirs  %L=marker-size  %P=path
# Exit: 0 = fully merged (no markers remain); non-zero = genuine conflict markers remain.
# dependency-free: bash + coreutils + git (merge-file) + awk — same toolchain as bin/stamp-docs.sh.
set -u

# %P (pathname, $5) is intentionally not consumed — .gitattributes already scopes this driver to
# `*.md`, so the driver needs no per-path logic. git still passes it; we simply ignore it.
base=${1:?base}; ours=${2:?ours}; theirs=${3:?theirs}; marker=${4:-7}

# The stamp regex — kept in lockstep with STAMP_RE in bin/stamp-docs.sh:41 (bare / `>` blockquote /
# `- **bold**` ADR / `_*emphasis*_` forms). Duplicated (one constant) rather than sourcing stamp-docs
# so the driver stays a standalone single file that git can invoke with no PATH assumptions.
STAMP_RE='^[[:space:]]*(>[[:space:]]*)?(-[[:space:]]*)?[_*]*[[:space:]]*[Ll]ast [Uu]pdated:'

# Standard 3-way merge, markers into $ours in place. rc = #conflicts (0 clean), 255 = error.
git merge-file --marker-size="$marker" "$ours" "$base" "$theirs"
rc=$?
[ "$rc" -eq 0 ] && exit 0                 # clean merge, nothing to post-process
[ "$rc" -eq 255 ] && exit 1               # merge-file error — leave as conflict, don't claim success

# Post-process: collapse stamp-only conflict hunks to the newer stamp; leave every other hunk.
# Content-safety invariant (silent-failure review): the driver NEVER drops a line and NEVER exits 0
# with markers still present. Every line entering a potential hunk is captured verbatim in raw[] and
# either (a) replaced by the newer stamp when the hunk is provably stamp-only, or (b) re-emitted
# byte-for-byte. A hunk left unterminated at EOF (e.g. a literal `<<<<<<<` illustration line in a
# doc-about-conflicts, since *.md is matched broadly) is flushed verbatim and counted as a conflict.
tmp="$(mktemp)" || exit 1
awk -v ml="$marker" -v SRE="$STAMP_RE" '
  BEGIN {
    lt=""; eq=""; gt="";
    for (i=0;i<ml;i++) { lt=lt "<"; eq=eq "="; gt=gt ">" }
    conflict=0; state=0             # 0 outside, 1 in-ours, 2 in-theirs
    no=0; nt=0; nr=0
  }
  # extract "YYYY-MM-DD[ HH:MM]" for lexical comparison; "" if none
  function dof(s,   ok) {
    ok = match(s, /[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]([ ]+[0-9][0-9]:[0-9][0-9])?/)
    return ok ? substr(s, RSTART, RLENGTH) : ""
  }
  # true iff every non-blank line of block[1..n] is a stamp line (>=1 stamp line present)
  function stamp_only(block, n,   i, seen) {
    seen=0
    for (i=1;i<=n;i++) {
      if (block[i] ~ /^[[:space:]]*$/) continue
      if (block[i] ~ SRE) { seen=1 } else { return 0 }
    }
    return seen
  }
  function maxdate(block, n,   i, d, best) {
    best=""
    for (i=1;i<=n;i++) { d=dof(block[i]); if (d>best) best=d }
    return best
  }
  function emit(block, n,   i) { for (i=1;i<=n;i++) print block[i] }
  function flush_raw(   i) { for (i=1;i<=nr;i++) print raw[i] }   # re-emit a hunk byte-for-byte
  {
    line=$0
    if (state==0 && substr(line,1,ml)==lt && substr(line,ml+1,1)!="<") {
      state=1; no=0; nt=0; nr=0; raw[++nr]=line; next
    }
    if (state==1 && substr(line,1,ml)==eq && substr(line,ml+1,1)!="=") { raw[++nr]=line; state=2; next }
    if (state==2 && substr(line,1,ml)==gt && substr(line,ml+1,1)!=">") {
      raw[++nr]=line
      # hunk complete: resolve iff BOTH sides are stamp-only; otherwise re-emit verbatim as a conflict
      if (stamp_only(O,no) && stamp_only(T,nt)) {
        if (maxdate(T,nt) > maxdate(O,no)) emit(T,nt); else emit(O,no)
      } else {
        conflict=1; flush_raw()
      }
      state=0; next
    }
    if (state==1) { O[++no]=line; raw[++nr]=line; next }
    if (state==2) { T[++nt]=line; raw[++nr]=line; next }
    print line
  }
  # An unterminated hunk at EOF: NEVER drop the buffered lines — flush verbatim and flag a conflict.
  END { if (state != 0) { conflict=1; flush_raw() }; exit (conflict ? 1 : 0) }
' "$ours" >"$tmp"
awk_rc=$?

# If the rewrite failed (awk error) or the replace can't land, surface a conflict — never exit 0
# leaving the marker-bearing merge-file output in place claiming a clean merge.
[ "$awk_rc" -gt 1 ] && { rm -f "$tmp"; exit 1; }
mv "$tmp" "$ours" || { rm -f "$tmp"; exit 1; }
exit "$awk_rc"
