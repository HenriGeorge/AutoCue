#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""PreToolUse pr-gate — dispatches on the `gh pr …` subcommand.

`gh pr create` on a `feat/*` branch with NO spec/plan ADDED ON THE BRANCH (design-before-code,
GATE 1) → BLOCK. Bypass: `WORKFLOW:no-design` in the command. The check is branch-scoped — it diffs
against the merge-base with the trunk branch, NOT a repo-wide `git ls-files` — so a spec/plan that
was merged to trunk by an earlier PR does not silently satisfy every subsequent branch forever
(fix-round 1, CRITICAL-2).

`gh pr merge` whose CI isn't green or whose PR isn't cleanly mergeable (merge-safety, P7) → BLOCK.
Bypass: `WORKFLOW:force-merge` in the command. Fails OPEN (allows) whenever `gh` is
offline/unauthenticated/unparseable — this gate must never wedge a legitimate merge just because
the network or `gh` auth is unavailable.

`gh pr create` also emits a non-blocking WARN nudge ("dispatch a code-reviewer before merge") on
every successful (non-blocked) create — auto-review-on-pr Layer 2, docs/superpowers/specs/
2026-08-10-auto-review-on-pr-design.md. `gh pr merge` additionally BLOCKS unless the PR's LATEST
code-review verdict is APPROVE: reads `<!-- code-review:VERDICT -->` markers from `comments`
(VERDICT ∈ {APPROVE, CHANGES, BLOCKERS} — any other/malformed token is treated as non-APPROVE,
fail-safe) plus any `reviews` entry with `state == "APPROVED"` (honors a genuine human GitHub
review, not just a marker comment); "latest" = the most-recently-created event across BOTH sources
by timestamp, so a later `:BLOCKERS` overrides an earlier `:APPROVE` and vice versa. Bypass:
`WORKFLOW:no-review`, independent of `WORKFLOW:force-merge` (CI/mergeable and review are
independent axes — a merge failing BOTH needs BOTH bypass tokens; the block reason always lists
every currently-failing condition, not just the first). The gate proves a review marker/approval
IS PRESENT, not that it reviewed the CURRENT diff (v1 does not tie the marker to `headRefOid` —
documented limitation, PG32/G3/G9 pins it) or that a genuinely independent party posted it (the PR
author can self-post `:APPROVE` — G12, no author-binding in v1). Same fail-open-on-`gh`-failure
guarantee as the CI/mergeable checks (reuses the same `gh pr view` fetch, no extra round-trip).

Detection is TOKEN-based, not a rigid `\\bgh\\s+pr\\s+(create|merge)\\b` regex — a global `gh` flag
between the program and the subcommand (`gh --repo org/repo pr merge 123`, `gh -R org/repo pr
create`) is common (routine in a multi-worktree setup where cwd's tracking is ambiguous) and must
not slip past detection (fix-round 1, CRITICAL-1). Any `--repo`/`-R`/`--hostname` flag found before
`pr` is preserved and forwarded to the internal `gh pr view` lookup so the merge-safety check
queries the SAME repo the merge itself targets.

Mirrors hooks/grill_gate.py's segment-split + fail-open patterns.
"""
import json
import re
import shlex
import subprocess
import sys

SHELL_SEGMENT_SPLIT_RE = re.compile(r"&&|\|\||;|\n|\|")
NO_DESIGN_BYPASS = "WORKFLOW:no-design"
FORCE_MERGE_BYPASS = "WORKFLOW:force-merge"
NO_REVIEW_BYPASS = "WORKFLOW:no-review"
SPEC_PATHS = ("docs/superpowers/specs", "docs/superpowers/plans")
CODE_REVIEW_MARKER_RE = re.compile(r"<!--\s*code-review:(\w+)\s*-->")
CREATE_NUDGE = (
    "PR opened — dispatch a code-reviewer (+ silent-failure-hunter) before merge "
    "(rolling-quality-pipeline). Reminder only, never blocks — the merge itself is gated on an "
    "APPROVE review marker."
)
FEAT_BRANCH_RE = re.compile(r"^feat/")
CI_FAILING_CONCLUSIONS = {
    "FAILURE", "CANCELLED", "TIMED_OUT", "ERROR", "STARTUP_FAILURE", "ACTION_REQUIRED",
}
# statusCheckRollup entries reporting one of these (in either `conclusion` or the legacy
# commit-status `state` field) are still IN PROGRESS — not green, but not "failing" either. Without
# this, a check with conclusion="" + state="PENDING" fell through both existing branches and was
# silently treated as OK (fix-round 1, MEDIUM).
CI_NON_TERMINAL_STATES = {"PENDING", "QUEUED", "IN_PROGRESS", "REQUESTED", "WAITING"}
GH_VALUE_FLAGS = {"-R", "--repo", "--hostname"}


def _tokenize(segment: str) -> list:
    try:
        return shlex.split(segment)
    except ValueError:
        return segment.split()


def _skip_flags(tokens: list, i: int, value_flags: set) -> int:
    """Advance past a run of recognized flag tokens starting at i (never past a non-flag token)."""
    while i < len(tokens) and tokens[i].startswith("-") and tokens[i] != "-":
        tok = tokens[i]
        if "=" in tok:
            i += 1
        elif tok in value_flags:
            i += 2
        else:
            i += 1
    return i


def _subcommand_match(tokens: list, prog: str, path: tuple, value_flags: set):
    """Find `prog` (optionally preceded/followed by recognized flags) → `path` tokens in order.

    Returns (prefix_flags, end_index) for the first match — prefix_flags are the flag tokens found
    between `prog` and the FIRST path token (e.g. `["--repo", "org/x"]`), end_index is the token
    index right after the last matched path token. Returns None if no match.
    """
    for i, t in enumerate(tokens):
        if t != prog:
            continue
        j = i + 1
        prefix_start = j
        prefix_end = j
        ok = True
        for pos, expected in enumerate(path):
            j = _skip_flags(tokens, j, value_flags)
            if pos == 0:
                prefix_end = j
            if j >= len(tokens) or tokens[j] != expected:
                ok = False
                break
            j += 1
        if ok:
            return tokens[prefix_start:prefix_end], j
    return None


def _run(cwd: str, args: list, timeout: int = 8) -> str | None:
    try:
        r = subprocess.run(
            args, cwd=cwd or None, capture_output=True, text=True, timeout=timeout, check=False,
        )
    except Exception:  # noqa: BLE001 - fail open
        return None
    return r.stdout if r.returncode == 0 else None


def _current_branch(cwd: str) -> str | None:
    out = _run(cwd, ["git", "rev-parse", "--abbrev-ref", "HEAD"])
    return out.strip() if out else None


def _resolve_trunk(cwd: str) -> str | None:
    """Best-effort trunk ref to diff against: origin's default branch, else local main/master.

    Widened (fix-round 2, #142(b)) for repos whose trunk isn't `main`/`master` and have no
    `origin/HEAD` set (common when a remote's default-branch ref was never fetched/configured) — a
    repo trunked on e.g. `develop` used to resolve to None here, and `_has_spec_or_plan` fails OPEN
    on an unknown trunk, silently disabling the design-before-code gate for every `gh pr create`
    forever. Two additional, best-effort signals, tried in order:
      1. the CURRENT branch's own configured upstream (`@{u}`) — a feature branch created with
         tracking against the real trunk (`git checkout -b feat/x --track develop`) names it exactly;
      2. the repo-configured default branch name (`git config init.defaultBranch`), tried as both a
         remote-tracking ref and a local branch.
    Still returns None (fail open) if none of these resolve — see docs/ENFORCEMENT.md "Known
    limitations" for that residual case.
    """
    ref = _run(cwd, ["git", "symbolic-ref", "refs/remotes/origin/HEAD"])
    if ref:
        parts = ref.strip().split("/")
        if len(parts) >= 2:
            return "/".join(parts[-2:])
    for candidate in ("origin/main", "origin/master", "main", "master"):
        if _run(cwd, ["git", "rev-parse", "--verify", "--quiet", candidate]) is not None:
            return candidate
    upstream = _run(cwd, ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"])
    if upstream and upstream.strip():
        upstream = upstream.strip()
        # Reject a SELF push-upstream: `git push -u origin <branch>` (by far the most common way
        # @{u} gets set) makes @{u} resolve to the branch's OWN remote-tracking ref
        # (origin/<branch>), not the repo's trunk. Using it as trunk diffs HEAD against a ref
        # that's normally in sync with it (merge-base == HEAD), so the spec/plan diff is always
        # empty regardless of what the branch actually contains — a fail-CLOSED false-block on the
        # single most common git workflow (adversarial review, fix-round 2 CRITICAL). Strip the
        # leading remote-name segment and compare to the current branch to detect this case.
        branch = _current_branch(cwd)
        upstream_tail = upstream.split("/", 1)[1] if "/" in upstream else upstream
        if not branch or upstream_tail != branch:
            return upstream
    default_branch = _run(cwd, ["git", "config", "init.defaultBranch"])
    if default_branch and default_branch.strip():
        default_branch = default_branch.strip()
        for candidate in (f"origin/{default_branch}", default_branch):
            if _run(cwd, ["git", "rev-parse", "--verify", "--quiet", candidate]) is not None:
                return candidate
    return None


def _has_spec_or_plan(cwd: str, branch: str) -> bool:
    """Branch-scoped: did THIS branch add a spec/plan since it diverged from trunk?

    Diffs against the merge-base with trunk — NOT a repo-wide `git ls-files` — so a spec/plan
    merged to trunk by an earlier PR doesn't silently satisfy every later branch forever
    (fix-round 1, CRITICAL-2). Fails open (True — don't block on an unknown) whenever trunk can't be
    resolved or the diff can't be computed.
    """
    trunk = _resolve_trunk(cwd)
    if not trunk or trunk == branch:
        return True  # can't determine a distinct trunk -> fail open
    base = _run(cwd, ["git", "merge-base", "HEAD", trunk])
    if not base:
        return True  # unrelated histories / can't compute -> fail open
    base = base.strip()
    out = _run(cwd, ["git", "diff", "--name-only", f"{base}..HEAD", "--", *SPEC_PATHS])
    if out is None:
        return True
    return any(p.strip() for p in out.splitlines())


def _block(reason: str):
    print(json.dumps({"decision": "block", "reason": reason}))  # noqa: T201
    sys.exit(2)


def _nudge_create():
    print(json.dumps({"systemMessage": CREATE_NUDGE}))  # noqa: T201


def _check_create(command: str, cwd: str) -> None:
    if NO_DESIGN_BYPASS in command:
        _nudge_create()
        return
    branch = _current_branch(cwd)
    if not branch or not FEAT_BRANCH_RE.match(branch):
        _nudge_create()
        return
    if _has_spec_or_plan(cwd, branch):
        _nudge_create()
        return
    _block(
        f"Blocked: `gh pr create` on '{branch}' with no design artifact ADDED ON THIS BRANCH — "
        "GATE 1 requires a spec (docs/superpowers/specs/**) or plan (docs/superpowers/plans/**) "
        "from brainstorming/writing-plans before a PR. For a genuinely trivial change, add "
        f"'{NO_DESIGN_BYPASS}' to the command."
    )


def _collect_repo_flags(tokens: list, value_flags: set) -> list:
    """Collect every `--repo`/`-R`/`--hostname` flag(+value) anywhere in `tokens`, in order.

    Unlike `prefix_flags` (only flags found BEFORE the matched subcommand), this scans the whole
    segment — a `--repo`/`-R` placed AFTER `pr merge` (e.g. `gh pr merge --repo org/x 12 --squash`)
    is just as valid a persistent `gh` flag and must reach the internal `gh pr view` safety lookup,
    or merge-safety silently evaluates the CWD's default repo instead (fix-round 2, MEDIUM).
    """
    out = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in value_flags:
            if i + 1 < len(tokens):
                out.extend([tok, tokens[i + 1]])
            i += 2
        elif any(tok.startswith(f + "=") for f in value_flags):
            out.append(tok)
            i += 1
        else:
            i += 1
    return out


def _pr_json(cwd: str, prefix_flags: list, tokens: list, end: int) -> dict | None:
    args = ["gh", *_collect_repo_flags(tokens, GH_VALUE_FLAGS), "pr", "view"]
    j = _skip_flags(tokens, end, GH_VALUE_FLAGS)
    if j < len(tokens) and not tokens[j].startswith("-"):
        args.append(tokens[j])
    args += ["--json", "statusCheckRollup,mergeable,baseRefName,comments,reviews"]
    out = _run(cwd, args)
    if out is None:
        return None
    try:
        return json.loads(out)
    except (json.JSONDecodeError, ValueError):
        return None


def _ci_ok(pr: dict) -> bool:
    rollup = pr.get("statusCheckRollup") or []
    if not rollup:
        return True
    for check in rollup:
        conclusion = (check.get("conclusion") or "").upper()
        state = (check.get("state") or "").upper()
        if conclusion in CI_FAILING_CONCLUSIONS or state in CI_FAILING_CONCLUSIONS:
            return False
        if state in CI_NON_TERMINAL_STATES:
            return False
        if not conclusion and not state:
            return False  # still pending → not yet green
    return True


def _mergeable_ok(pr: dict) -> bool:
    mergeable = (pr.get("mergeable") or "").upper()
    return mergeable != "CONFLICTING"


def _review_events(pr: dict) -> list:
    """Every (timestamp, VERDICT) event from comment markers + APPROVED human reviews.

    A marker comment contributes its own verdict token (upper-cased; an unrecognized token is kept
    as-is and simply never equals "APPROVE" downstream — G8, fail-safe). A `reviews` entry with
    `state == "APPROVED"` contributes an implicit "APPROVE" event at its `submittedAt` timestamp
    (spec: honor a genuine human GitHub review, not just a marker comment). Timestamps are
    ISO-8601 strings from the GitHub API, which sort correctly as plain strings — no date parsing
    needed. Events with no usable timestamp are skipped (can't be ordered).
    """
    events = []
    for c in pr.get("comments") or []:
        body = c.get("body") or ""
        m = CODE_REVIEW_MARKER_RE.search(body)
        if not m:
            continue
        ts = c.get("createdAt") or ""
        if ts:
            events.append((ts, m.group(1).upper()))
    for r in pr.get("reviews") or []:
        if (r.get("state") or "").upper() != "APPROVED":
            continue
        ts = r.get("submittedAt") or ""
        if ts:
            events.append((ts, "APPROVE"))
    return events


def _review_ok(pr: dict) -> bool:
    """True iff the LATEST review event (by timestamp, across comments + reviews) is APPROVE.

    No events at all (no marker, no human approval) → not ok. This is the auto-review-on-pr
    Layer-2 backstop — it proves a review verdict is present and current-latest is APPROVE, not
    that the review was thorough or that it covers the current head SHA (v1 limitation, PG32).
    """
    events = _review_events(pr)
    if not events:
        return False
    events.sort(key=lambda e: e[0])
    return events[-1][1] == "APPROVE"


def _check_merge(command: str, prefix_flags: list, tokens: list, end: int, cwd: str) -> None:
    force_bypass = FORCE_MERGE_BYPASS in command
    review_bypass = NO_REVIEW_BYPASS in command
    if force_bypass and review_bypass:
        return  # both axes bypassed — no need to even fetch
    pr = _pr_json(cwd, prefix_flags, tokens, end)
    if pr is None:
        return  # gh offline/unauthenticated/unparseable → fail open
    ci_ok = _ci_ok(pr) or force_bypass
    mergeable_ok = _mergeable_ok(pr) or force_bypass
    review_ok = _review_ok(pr) or review_bypass
    if ci_ok and mergeable_ok and review_ok:
        return
    reasons = []
    if not ci_ok:
        reasons.append("CI checks are not all green")
    if not mergeable_ok:
        reasons.append("the PR is not cleanly mergeable (conflicting with its base)")
    if not review_ok:
        reasons.append(
            "no APPROVE code review found (latest marker/review is not APPROVE — "
            "auto-review-on-pr Layer 2)"
        )
    _block(
        "Blocked: `gh pr merge` — " + " and ".join(reasons) + ". Fix the underlying issue, or "
        f"if this merge is genuinely safe, add '{FORCE_MERGE_BYPASS}' (CI/mergeable) and/or "
        f"'{NO_REVIEW_BYPASS}' (review) to the command as appropriate."
    )


def main():
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)
    if data.get("tool_name", "") != "Bash":
        sys.exit(0)
    command = data.get("tool_input", {}).get("command", "")
    cwd = data.get("cwd", "")
    try:
        for segment in SHELL_SEGMENT_SPLIT_RE.split(command):
            tokens = _tokenize(segment)
            m = _subcommand_match(tokens, "gh", ("pr", "create"), GH_VALUE_FLAGS)
            if m is not None:
                _check_create(command, cwd)
                continue
            m = _subcommand_match(tokens, "gh", ("pr", "merge"), GH_VALUE_FLAGS)
            if m is not None:
                prefix_flags, end = m
                _check_merge(command, prefix_flags, tokens, end, cwd)
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001 - never brick a session
        sys.exit(0)
    sys.exit(0)


if __name__ == "__main__":
    main()
