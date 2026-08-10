#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""PreToolUse hook — protect read-only paths, .env, and block destructive commands."""

import json  # noqa: I001
import os
import re
import shlex
import subprocess
import sys


# Paths that agents cannot write to (customize per project)
READONLY_PATHS = ["/source/"]
BLOCKED_WRITE_PATTERNS = [".env"]
DANGEROUS_DB_OPS = [
    "DROP TABLE", "DROP DATABASE", "TRUNCATE",
    "ALTER TABLE", "DROP INDEX",
]

# H1 — test-lock: deny bare test-runner invocations unless wrapped in `cc-worktrees test -- ...`
# or overridden via CCWT_ALLOW_UNLOCKED_TESTS=1 (crew/DESIGN.md Tier A). Anchored at the START of a
# shell segment (after stripping leading `VAR=val`/`env`/`npx`/path-prefix tokens) so we never
# match e.g. "echo running the test suite" or "npm run testify" (word-boundary + segment-anchored).
CCWT_WRAPPED_RE = re.compile(r"cc-worktrees\s+test\b")
SHELL_SEGMENT_SPLIT_RE = re.compile(r"&&|\|\||;|\n|\|")
# Leading tokens stripped (in a loop, so combos like "env FOO=bar npx ./node_modules/.bin/jest"
# resolve) before anchoring TEST_LOCK_RE — this is a nudge-grade guard, not a shell parser: it does
# NOT attempt to see through `bash -c '...'` or `$(subshell)` wrapping (accepted limitation, see
# tests/test_pretooluse_guards.sh "H1-LIMIT" cases).
LEADING_ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=\S*\s+")
LEADING_ENV_KEYWORD_RE = re.compile(r"^env\s+")
LEADING_NPX_RE = re.compile(r"^npx\s+")
LEADING_PATH_PREFIX_RE = re.compile(r"^(?:\./(?:[\w.-]+/)*|(?:[\w.-]+/)*node_modules/\.bin/)")
LEADING_STRIP_PATTERNS = (
    LEADING_ENV_ASSIGN_RE,
    LEADING_ENV_KEYWORD_RE,
    LEADING_NPX_RE,
    LEADING_PATH_PREFIX_RE,
)
TEST_LOCK_RE = re.compile(
    r"^(npm\s+run\s+test\b|npm\s+test\b|pytest\b|go\s+test\b|cargo\s+test\b|vitest\b|jest\b)"
)


def _strip_leading_tokens(segment: str) -> str:
    """Iteratively strips leading env-assignment / `env` / `npx` / path-prefix tokens so
    `env FOO=bar npx ./node_modules/.bin/jest` resolves down to `jest` before anchoring."""
    seg = segment
    changed = True
    while changed:
        changed = False
        for pattern in LEADING_STRIP_PATTERNS:
            new_seg = pattern.sub("", seg, count=1)
            if new_seg != seg:
                seg = new_seg
                changed = True
    return seg


def _segment_has_leading_override(segment: str, name: str) -> bool:
    """True if `name=1` is a genuine LEADING env-assignment token of THIS shell segment (not
    merely a substring mentioned elsewhere, e.g. inside a commit message or an echo — HIGH fix —
    and not leading a DIFFERENT segment of the same command — MED fix: under real shell semantics
    `VAR=1 true && git commit` only exports VAR to `true`, not to the following segment, so an
    override must be scoped to the exact segment doing the guarded work, never "any segment")."""
    seg = segment.strip()
    while True:
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(\S*)\s*", seg)
        if not m:
            return False
        val = m.group(2)
        # nice-to-have: tolerate a quoted value (CCWT_ALLOW_SECRETS='1') — strip matching quotes.
        if len(val) >= 2 and val[0] in "\"'" and val[-1] == val[0]:
            val = val[1:-1]
        if m.group(1) == name and val == "1":
            return True
        seg = seg[m.end():]


def _process_env_override(name: str) -> bool:
    """The hook process's own env (in case Claude Code invokes it as a child that inherits it) —
    this legitimately applies globally, unlike a command-embedded assignment."""
    return os.environ.get(name) == "1"

# H2 — conventional-commit: permissive `type(scope)!: subject` grammar for an inline `git commit -m`.
# Types are matched case-sensitively lowercase-only per the conventional-commits spec (so "Feat: x"
# is denied, not silently normalized).
CONVENTIONAL_COMMIT_RE = re.compile(
    r"^(feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert)(\([^)]*\))?!?:\s+\S.*$"
)
# Merge-commit default messages are exempt — git generates these, not the agent, and they don't
# follow (nor should be forced into) conventional-commit grammar.
MERGE_COMMIT_MSG_RE = re.compile(r"^Merge (branch|remote-tracking branch|pull request)\b")
GIT_COMMIT_RE = re.compile(r"\bgit\s+commit\b")
COMMIT_M_FLAG_RE = re.compile(
    r'(?:^|\s)(?:-m|--message)(?:=|\s+)("(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'|\S+)'
)
COMMIT_F_FLAG_RE = re.compile(r"(?:^|\s)(?:-F|--file)(?:=|\s+)\S+")

# H3 — secret-scan: obvious secrets in a staged diff (AWS key, private-key header, generic token
# assignment). Overridable via CCWT_ALLOW_SECRETS=1 (see `_segment_has_leading_override` /
# `_process_env_override` — leading env-assignment token of the COMMIT's own segment, or real
# process env; never an unanchored substring, never leaking from a different segment).
AWS_KEY_RE = re.compile(r"AKIA[0-9A-Z]{16}")
PRIVATE_KEY_RE = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
# TODO(secret-scan): GENERIC_SECRET_RE only catches a QUOTED `key = "value"` assignment; an
# unquoted `KEY=value` (e.g. a bare .env-style line) is currently missed. Left as-is this pass —
# broadening risks false positives on ordinary code (`token = getToken()`-shaped lines, etc.) and
# needs its own design pass, not a drive-by widen.
GENERIC_SECRET_RE = re.compile(
    r'(?i)\b(token|secret|api[_-]?key)\b\s*[:=]\s*["\']([A-Za-z0-9+/=_-]{16,})["\']'
)

# H5 — PR-review reminder: INJECT-ONLY (PreToolUse can block, but this guard never does — a nudge,
# not a gate). Never calls _block; only ever appends to the reminders list.
PR_MERGE_RE = re.compile(r"\bgh\s+pr\s+merge\b")

# H6 — docs-staleness reminder: INJECT-ONLY, same rationale as H5. Paths under these prefixes don't
# count as "source" for this heuristic — infra/config/docs dirs, not the kind of change that would
# make docs stale. `tests/`/`test/` included: a test-only commit is pure noise here — it never
# blocks either way, but nudging "check the docs" on a change that touched no product code is a
# false positive the reminder shouldn't produce.
DOCS_STALENESS_EXCLUDED_PREFIXES = ("docs/", "crew/", ".claude/", ".github/", "tests/", "test/")

# H7 — danger-guard: force-push to main/master, `git reset --hard <remote>/<branch>`, and `rm -rf`
# on sensitive/parent paths (extends the existing READONLY_PATHS rm-rf check above). Nudge-grade —
# same heuristic-not-shell-parser limitation as H1. Override CCWT_ALLOW_DANGER=1 — SEGMENT-SCOPED
# via `_segment_has_leading_override` (the #1 rule from the H1/H3 override-bypass saga: never a
# bare substring, never leak across a different shell segment).
SENSITIVE_RM_RF_TARGETS = (
    "/", "/*", "~", "~/", "$HOME", "..", "../", "/etc", "/etc/", "/usr", "/usr/",
    "/bin", "/bin/", "/var", "/var/", "/System", "/System/", "/Users", "/Users/",
    "/home", "/home/",
)
GIT_PUSH_RE = re.compile(r"\bgit\s+push\b")
RESET_HARD_REMOTE_RE = re.compile(r"\bgit\s+reset\s+--hard\s+(\S+)")
# issue #111 — more working-tree/branch destroyers that discard uncommitted or unpushed work with no
# reflog recovery: `git clean -f[d]` (deletes untracked files), `git branch -D` (force-delete an
# unmerged branch), `git checkout .` / `git restore .` (discard ALL working-tree changes). Each is a
# block-with-safe-alternative, same nudge-grade heuristic as the rest of H7. We do NOT block ordinary
# `git push`. `-C <path>` is tolerated between `git` and the subcommand so it's not a bypass.
GIT_CLEAN_RE = re.compile(r"\bgit\s+(?:-C\s+\S+\s+)?clean\b")
GIT_BRANCH_RE = re.compile(r"\bgit\s+(?:-C\s+\S+\s+)?branch\b")
GIT_CHECKOUT_REST_RE = re.compile(r"\bgit\s+(?:-C\s+\S+\s+)?checkout\b(.*)$")
GIT_RESTORE_REST_RE = re.compile(r"\bgit\s+(?:-C\s+\S+\s+)?restore\b(.*)$")


def _has_short_flag(segment: str, ch: str) -> bool:
    """True if a clustered short flag containing `ch` is present (e.g. `-f`, `-fd`, `-xf`)."""
    return bool(re.search(r"(?:^|\s)-[A-Za-z]*" + re.escape(ch) + r"[A-Za-z]*(?:\s|$)", segment))


def _has_dot_pathspec(rest: str) -> bool:
    """True if a bare `.` / `./` pathspec token appears in the args after the subcommand — the
    'discard EVERYTHING in the working tree' form of checkout/restore."""
    return any(tok in (".", "./") for tok in rest.split())


def _destructive_worktree_op(segment: str):
    """(label, safe-alternative) for a #111 destroyer in THIS segment, or None. `git clean` only
    acts with a force flag (and not in `-n`/--dry-run preview mode); `git branch -D` (or an explicit
    `--delete --force`) force-deletes; `git checkout .`/`git restore .` discard the whole worktree."""
    if GIT_CLEAN_RE.search(segment):
        forced = _has_short_flag(segment, "f") or "--force" in segment
        preview = _has_short_flag(segment, "n") or "--dry-run" in segment
        if forced and not preview:
            return ("git clean -f", "irrecoverably deletes untracked files — preview with "
                    "`git clean -n` or save them with `git stash -u` first")
    if GIT_BRANCH_RE.search(segment):
        if _has_short_flag(segment, "D") or ("--delete" in segment and "--force" in segment):
            return ("git branch -D", "force-deletes a branch even if unmerged — use `git branch -d` "
                    "(refuses to drop unmerged work) or confirm it's merged/pushed first")
    m = GIT_CHECKOUT_REST_RE.search(segment)
    if m and _has_dot_pathspec(m.group(1)):
        return ("git checkout .", "discards ALL uncommitted working-tree changes — stash them "
                "(`git stash`) or restore specific files by name instead")
    m = GIT_RESTORE_REST_RE.search(segment)
    if m and _has_dot_pathspec(m.group(1)):
        rest = m.group(1)
        staged = "--staged" in rest or _has_short_flag(rest, "S")
        worktree = "--worktree" in rest or _has_short_flag(rest, "W")
        # S1 fix (#111): `git restore` defaults to the WORKING TREE unless --staged/-S is given.
        # `git restore --staged .` only unstages (index-only, working tree untouched) — the standard
        # "unstage everything", NOT destructive. It's destructive only when the working tree is
        # actually touched: an explicit --worktree/-W, or the default (no --staged).
        if worktree or not staged:
            return ("git restore .", "discards ALL uncommitted working-tree changes — stash them "
                    "(`git stash`) or restore specific files by name instead")
    return None
# MED fix: `@{upstream}`/`@{u}` are remote-tracking shorthand too, not just an explicit `origin/x`.
RESET_HARD_UPSTREAM_SHORTHAND = ("@{upstream}", "@{u}")

# H8 — protected-branch: `git commit` blocks unconditionally when the CURRENT branch (via `git
# rev-parse --abbrev-ref HEAD` in the payload's cwd, or a `-C <path>` repo if given) is
# main/master — a commit always lands on the current branch. `git push` is TARGET-aware (2nd
# fix-up, HIGH-1 still open after the 1st pass): it blocks ONLY when the push's actual
# DESTINATION resolves to main/master, not merely because you happen to be sitting on main —
# `git push origin feature-x` while on main must ALLOW. Fail-open if not a repo / detached HEAD /
# the subprocess errors. Override CCWT_ALLOW_PROTECTED=1 (segment-scoped).
PROTECTED_BRANCHES = ("main", "master")
# `git -C <path> ...` targets a DIFFERENT repo than the payload's own cwd — honor it when present
# so `git -C other-repo commit` is checked against other-repo's branch, not cwd's.
GIT_DASH_C_RE = re.compile(r"\bgit\s+-C\s+(\S+)")
# The plain GIT_COMMIT_RE/GIT_PUSH_RE (`\bgit\s+commit\b` / `\bgit\s+push\b`) don't match when a
# `-C <path>` sits between "git" and the subcommand — H8 needs its OWN trigger checks that
# tolerate it, else the -C fix above is never actually reached.
GIT_COMMIT_OR_PUSH_WITH_DASH_C_RE = re.compile(r"\bgit\s+(?:-C\s+\S+\s+)?(?:commit|push)\b")
GIT_COMMIT_WITH_DASH_C_RE = re.compile(r"\bgit\s+(?:-C\s+\S+\s+)?commit\b")
GIT_PUSH_WITH_DASH_C_RE = re.compile(r"\bgit\s+(?:-C\s+\S+\s+)?push\b(.*)$")

# H9 — giant-file: block staging a file over CCWT_MAX_FILE_MB (default 10) via `git add`/`git
# commit`. Override CCWT_ALLOW_BIGFILE=1 (segment-scoped). HIGH fix: the hook fires PRE-execution —
# at hook-check time NOTHING has actually been staged yet, so `git add -A`/`.`/`-u` (no explicit
# path on the command line) must be resolved to what `git status` says WOULD be staged, not just
# the literal path arguments (which for -A/-u is nothing, and for a bare `.` is a directory H9's
# os.path.isfile check silently skips) — closes the `git add -A && git commit` bypass.
GIT_ADD_RE = re.compile(r"\bgit\s+add\b")
ADD_STAGES_EVERYTHING_FLAGS = ("-A", "--all", "-u", "--update")
DEFAULT_MAX_FILE_MB = 10.0

# H10 — secret-file staging: block staging an obviously-secret FILE by NAME (complements H3, which
# scans staged diff CONTENT — H10 catches the file itself). MED fix: `.env*` is now an ALLOW-LIST
# (deny everything starting with `.env`, case-insensitively, EXCEPT these known-safe suffixes) —
# was an allow-by-default denylist that missed `.env.local`/`.env.production`/`.ENV`. Override
# CCWT_ALLOW_SECRET_FILE=1 (segment-scoped).
SECRET_FILENAME_ALLOW = (".env.example", ".env.sample", ".env.test")


def _resolve_git_cwd(segment: str, cwd: str) -> str:
    """MED fix (H8): `git -C <path> ...` targets a DIFFERENT repo than the payload's own cwd —
    resolve it (relative to cwd if not absolute) and use THAT for the branch check, instead of
    checking cwd's own repo (or failing open because cwd itself isn't a repo at all)."""
    m = GIT_DASH_C_RE.search(segment)
    if not m:
        return cwd
    target = m.group(1).strip("'\"")
    return target if os.path.isabs(target) else os.path.join(cwd or ".", target)


def _current_branch(cwd: str) -> str | None:
    """Best-effort current branch name; None on ANY failure (not a repo, detached HEAD, git
    missing, timeout) — shared by H7 (implicit force-push target) and H8 (protected-branch check);
    both callers fail OPEN when this returns None."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=cwd or None, capture_output=True, text=True, timeout=3, check=False,
        )
    except Exception:  # noqa: BLE001
        return None
    if result.returncode != 0:
        return None
    branch = result.stdout.strip()
    if not branch or branch == "HEAD":  # detached HEAD prints the literal string "HEAD"
        return None
    return branch


def _staged_names(cwd: str) -> list:
    """`git diff --cached --name-only`, fail-open (empty list) on any error. Shared by H6 (docs-
    staleness), H9 (giant-file at commit time), and H10 (secret-file at commit time)."""
    try:
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            cwd=cwd or None, capture_output=True, text=True, timeout=5, check=False,
        )
    except Exception:  # noqa: BLE001
        return []
    if result.returncode != 0:
        return []
    return [p.strip() for p in result.stdout.splitlines() if p.strip()]


def _is_rm_rf(segment: str) -> bool:
    """Best-effort rm -rf / -fr / --recursive+--force detector — a nudge-grade guard, not a shell
    parser; doesn't try to see through `bash -c` or aliases (same class of limitation as H1)."""
    if not re.search(r"\brm\b", segment):
        return False
    has_r = bool(re.search(r"(?:^|\s)-[a-zA-Z]*r[a-zA-Z]*(?:\s|$)", segment)) or "--recursive" in segment
    has_f = bool(re.search(r"(?:^|\s)-[a-zA-Z]*f[a-zA-Z]*(?:\s|$)", segment)) or "--force" in segment
    return has_r and has_f


def _rm_rf_sensitive_target(segment: str):
    if not _is_rm_rf(segment):
        return None
    for target in SENSITIVE_RM_RF_TARGETS:
        if re.search(r"(?:^|\s)" + re.escape(target) + r"(?:\s|$)", segment):
            return target
    return None


def _push_candidates(segment: str, cwd: str):
    """ALL candidate destination branches a `git push` in THIS segment could update, each paired
    with whether THAT specific ref is force-pushed — `[(branch, is_forced), ...]`. SHARED by H7
    (only cares about FORCED candidates) and H8 (2nd fix-up: cares about ALL candidates, forced or
    not — direct-to-protected-branch is the concern regardless of force). 3rd fix-up (narrow
    refspec bypasses the auditor's deeper probe found):
      1. A single push can update MULTIPLE refs at once (`git push origin main feature`) — only
         checking the LAST non-flag token missed an EARLIER protected one
         (`git push origin main feature` let `main` through). Every non-flag token AFTER the
         remote is now evaluated as its own candidate.
      2. git's short per-ref force marker — a leading `+` directly on a ref, e.g. `+main` — is
         EXACTLY as dangerous as `--force ... main` but carries no `--force`/`-f` flag anywhere in
         the command at all, so the old "is there a force flag present" check alone missed it
         (`+refs/heads/main`, the LONG form, was accidentally caught because splitting on `/` and
         taking the last segment happened to still land on `main`; the SHORT form `+main` was
         not — `+` was never stripped before the comparison). Each token's leading `+` is now
         stripped before deriving the branch name, and that per-ref `+` ALSO counts as forced for
         THAT ref even with no `--force`/`-f` flag anywhere else in the command.
    `HEAD` resolves to the CURRENT branch; a `src:dst` refspec is evaluated on its `dst` side.
    Returns `[]` if this isn't a `git push` at all, or if it's a tags/all-branches push with no
    single identifiable branch destination (`--tags`/`-t`/`--all` and no other ref arg)."""
    m = GIT_PUSH_WITH_DASH_C_RE.search(segment)
    if not m:
        return []
    rest = m.group(1)
    tokens = rest.split()
    non_flag_tokens = [tok for tok in tokens if not tok.startswith("-")]
    if not non_flag_tokens and any(f in tokens for f in ("--tags", "-t", "--all")):
        return []  # pushes tags / all branches — no single branch destination to protect
    global_force = bool(
        re.search(r"--force(?:-with-lease)?\b|(?:^|\s)-[a-zA-Z]*f[a-zA-Z]*(?:\s|$)", rest)
    )
    if len(non_flag_tokens) >= 2:
        # non_flag_tokens[0] is the remote; EVERY token after it is a candidate ref destination —
        # a push can update more than one ref at once, and each must be checked independently.
        candidates = []
        for tok in non_flag_tokens[1:]:
            forced = global_force or tok.startswith("+")
            ref = tok[1:] if tok.startswith("+") else tok  # strip the short force-refspec marker
            name = ref.split(":")[-1].split("/")[-1]
            branch = _current_branch(cwd) if name == "HEAD" else name
            candidates.append((branch, forced))
        return candidates
    # 0 or 1 non-flag tokens (no explicit branch/refspec — just maybe a bare remote name) — this
    # push targets the CURRENT branch by default (real git push.default semantics).
    return [(_current_branch(cwd), global_force)]


def _reset_hard_remote_target(segment: str):
    m = RESET_HARD_REMOTE_RE.search(segment)
    if not m:
        return None
    target = m.group(1)
    if target in RESET_HARD_UPSTREAM_SHORTHAND:  # MED fix: @{upstream} / @{u}
        return target
    return target if re.match(r"^[\w.-]+/[\w.-]+$", target) else None


def _extract_add_paths(segment: str):
    """Non-flag argument tokens after `git add` — shlex-tokenized so a quoted path with spaces
    round-trips; falls back to a plain split on a shlex error (unbalanced quote, etc.)."""
    m = re.search(r"\bgit\s+add\b(.*)$", segment)
    if not m:
        return []
    try:
        tokens = shlex.split(m.group(1))
    except ValueError:
        tokens = m.group(1).split()
    return [t for t in tokens if t and not t.startswith("-")]


def _add_segment_stages_everything(segment: str) -> bool:
    """True for `git add -A`/`--all`/`-u`/`--update`, or a bare `.`/`./` path — any form that
    stages more than the literal path arguments on the command line."""
    m = re.search(r"\bgit\s+add\b(.*)$", segment)
    if not m:
        return False
    try:
        tokens = shlex.split(m.group(1))
    except ValueError:
        tokens = m.group(1).split()
    if any(t in ADD_STAGES_EVERYTHING_FLAGS for t in tokens):
        return True
    non_flag = [t for t in tokens if t and not t.startswith("-")]
    return any(p in (".", "./") for p in non_flag)


def _would_be_staged_by_add_all(cwd: str):
    """Enumerates what `git status --porcelain` reports as changed (tracked-modified + all
    untracked, following .gitignore) — the set of paths a `git add -A`/`.`/`-u` WOULD stage.
    Fail-open ([]) on any error. HIGH fix: the hook fires PRE-execution, so at hook-check time
    nothing has actually been staged by a `git add -A` in the SAME command yet — checking only
    literal path arguments (empty for -A/-u, a bare directory for `.`) missed this entirely,
    letting `git add -A && git commit ...` evade both H9 (giant-file) and H10 (secret-file)."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=cwd or None, capture_output=True, text=True, timeout=5, check=False,
        )
    except Exception:  # noqa: BLE001
        return []
    if result.returncode != 0:
        return []
    paths = []
    for line in result.stdout.splitlines():
        if len(line) < 4:
            continue
        rest = line[3:].strip()
        if " -> " in rest:  # rename: "old -> new" — the NEW path is what add would (re-)stage
            rest = rest.split(" -> ", 1)[1].strip()
        if len(rest) >= 2 and rest[0] == '"' and rest[-1] == '"':
            rest = rest[1:-1]
        if rest:
            paths.append(rest)
    return paths


def _add_command_targets(segment: str, cwd: str):
    """Files a `git add` in THIS segment would actually stage — either the explicit path
    arguments, or (for -A/--all/-u/--update/a bare '.') everything `git status` reports as
    changed. The single call site H9 and H10 both use instead of `_extract_add_paths` directly."""
    if _add_segment_stages_everything(segment):
        return _would_be_staged_by_add_all(cwd)
    return _extract_add_paths(segment)


def _staged_blob_size_mb(cwd: str, path: str):
    """Size (MB) of PATH as staged in the git index (`git cat-file -s :path`) — robust regardless
    of working-tree state (the file may have changed or been deleted since it was staged). None on
    any failure (fail open)."""
    try:
        result = subprocess.run(
            ["git", "cat-file", "-s", f":{path}"],
            cwd=cwd or None, capture_output=True, text=True, timeout=5, check=False,
        )
    except Exception:  # noqa: BLE001
        return None
    if result.returncode != 0:
        return None
    try:
        return int(result.stdout.strip()) / (1024 * 1024)
    except ValueError:
        return None


def _max_file_mb() -> float:
    try:
        return float(os.environ.get("CCWT_MAX_FILE_MB", DEFAULT_MAX_FILE_MB))
    except (TypeError, ValueError):
        return DEFAULT_MAX_FILE_MB


def _is_secret_filename(path: str) -> bool:
    """Case-insensitive throughout (MED fix — `.ENV` must deny same as `.env`)."""
    base_lower = os.path.basename(path).lower()
    if base_lower.startswith(".env"):
        # MED fix: ALLOW-LIST, not a denylist — every `.env*` is secret EXCEPT these known-safe
        # suffixes, so `.env.local`/`.env.production`/etc. (anything not explicitly allow-listed)
        # denies. The old version only denied the bare `.env` name, missing every real-world
        # per-environment variant.
        return base_lower not in SECRET_FILENAME_ALLOW
    if base_lower.endswith(".pem"):
        return True
    if base_lower == "id_rsa":
        return True
    if base_lower.endswith(".key"):
        return True
    if base_lower == "credentials.json":
        return True
    if "service-account" in base_lower and base_lower.endswith(".json"):
        return True
    return False


def main():
    # Bad/empty stdin must not crash a PreToolUse hook (exit 1 is an unspecified outcome that can
    # disrupt the session). Fail OPEN — allow the tool — on unparseable input, like the Node hook.
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)
    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input", {})

    if tool_name == "Bash":
        command = tool_input.get("command", "")

        # Block rm -rf on read-only paths
        for path in READONLY_PATHS:
            if "rm -rf" in command and path in command:
                _block(f"Blocked: cannot rm -rf inside {path} — read-only.")

        # Block writing to .env
        for pattern in BLOCKED_WRITE_PATTERNS:
            if any(op in command for op in [f"> {pattern}", f">> {pattern}", f"cat > {pattern}"]):
                _block(f"Blocked: do not overwrite {pattern} via shell.")

        # Block destructive DB operations — match on WORD BOUNDARIES (prose "trunca-TED"/"ALTERed"
        # no longer trips; every executed op still matches) after scrubbing quoted strings inside a
        # `git commit …` segment ONLY (a commit message cannot execute; the scrub stops at the first
        # ;|&| separator so `git commit -m "x" && psql -c "DROP TABLE y"` still trips). Deliberately
        # NO heredoc stripping: heredocs are an execution path (`psql <<SQL`) — a live probe showed
        # stripping them lets a real prod DROP through (ADR 0008: never narrow toward false
        # negatives; the workaround for citing SQL in prose is the Edit/Write tool, not Bash).
        scrubbed = re.sub(
            r"\bgit\s+commit\b[^\n;&|]*",
            lambda m: re.sub(r"(['\"]).*?\1", " ", m.group(0)),
            command,
        )
        for pattern in DANGEROUS_DB_OPS:
            if "--dry-run" in command:
                break
            if re.search(r"\b" + pattern.replace(" ", r"\s+") + r"\b", scrubbed, re.IGNORECASE):
                _block(f"Blocked: destructive DB operation '{pattern}' — use --dry-run first.")

        # H1/H2/H3 — each wrapped so a bug in one guard fails OPEN, never crashes the hook.
        try:
            _check_test_lock(command)
        except Exception:  # noqa: BLE001 - fail open, never brick a session
            pass
        try:
            _check_conventional_commit(command)
        except Exception:  # noqa: BLE001
            pass
        try:
            _check_secret_scan(command, data.get("cwd", ""))
        except Exception:  # noqa: BLE001
            pass

        # H7/H8/H9/H10 — each wrapped so a bug in one guard fails OPEN, never crashes the hook.
        try:
            _check_danger_guard(command, data.get("cwd", ""))
        except Exception:  # noqa: BLE001
            pass
        try:
            _check_protected_branch(command, data.get("cwd", ""))
        except Exception:  # noqa: BLE001
            pass
        try:
            _check_giant_file(command, data.get("cwd", ""))
        except Exception:  # noqa: BLE001
            pass
        try:
            _check_secret_file_staging(command, data.get("cwd", ""))
        except Exception:  # noqa: BLE001
            pass

        # H5/H6 — INJECT-ONLY reminders. Run AFTER every block-capable check above (any of which
        # may already have exited via _block/sys.exit(2)); collected into ONE additionalContext
        # payload rather than one JSON blob per reminder.
        reminders = []
        try:
            r = _check_pr_review_reminder(command)
            if r:
                reminders.append(r)
        except Exception:  # noqa: BLE001
            pass
        try:
            r = _check_docs_staleness_reminder(command, data.get("cwd", ""))
            if r:
                reminders.append(r)
        except Exception:  # noqa: BLE001
            pass
        if reminders:
            print(json.dumps({  # noqa: T201
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "additionalContext": "\n".join(reminders),
                }
            }))

    elif tool_name in ("Write", "Edit"):
        file_path = tool_input.get("file_path", "")
        for path in READONLY_PATHS:
            if path in file_path or file_path.endswith(path.rstrip("/")):
                _block(f"Blocked: {path} is read-only. Cannot write to {file_path}")


def _check_test_lock(command: str):
    if not command.strip():
        return
    if _process_env_override("CCWT_ALLOW_UNLOCKED_TESTS"):
        return
    if CCWT_WRAPPED_RE.search(command):
        return
    for segment in SHELL_SEGMENT_SPLIT_RE.split(command):
        seg = _strip_leading_tokens(segment.strip()).strip()
        if TEST_LOCK_RE.match(seg):
            # MED fix: the override must lead THIS segment (the one that matched), not merely
            # "any segment of the whole command" — else `VAR=1 true && npm test` would wrongly
            # inherit an override that real shell semantics never actually gives to `npm test`.
            if _segment_has_leading_override(segment, "CCWT_ALLOW_UNLOCKED_TESTS"):
                continue
            _block(
                "Blocked: bare test-runner invocation. Re-run via "
                "`cc-worktrees test -- <cmd>` to hold the per-repo test lock, or set "
                "CCWT_ALLOW_UNLOCKED_TESTS=1 to override."
            )


def _check_conventional_commit(command: str):
    if not GIT_COMMIT_RE.search(command):
        return
    if COMMIT_F_FLAG_RE.search(command):
        return  # -F/--file path — not our concern here
    m = COMMIT_M_FLAG_RE.search(command)
    if not m:
        return  # no inline -m → interactive/editor commit, don't block
    msg = m.group(1)
    if len(msg) >= 2 and msg[0] in "\"'" and msg[-1] == msg[0]:
        msg = msg[1:-1]
    if MERGE_COMMIT_MSG_RE.match(msg.strip()):
        return  # merge-commit default message — exempt
    if not CONVENTIONAL_COMMIT_RE.match(msg.strip()):
        _block(
            f"Blocked: commit message {msg!r} doesn't match conventional-commit grammar "
            "`type(scope)!: subject` (types: feat|fix|docs|style|refactor|perf|test|build|"
            "ci|chore|revert)."
        )


def _check_secret_scan(command: str, cwd: str):
    # KNOWN LIMITATION (Increment-4 audit, documented not silently left inconsistent): unlike H6/
    # H9/H10, this does NOT see a brand-new secret introduced by a SAME-COMMAND chained
    # `git add -A && git commit ...` — this reads `git diff --cached` at hook-check time, which is
    # PRE-execution (nothing in the chain has actually run yet), so a file `git add -A` would
    # newly stage isn't reflected here. H6/H9/H10 closed the equivalent gap cheaply because they
    # only need FILENAMES (`git status` enumeration); closing it here would mean reading and
    # regex-scanning the CONTENT of every about-to-be-staged file (raw untracked-file bytes +
    # working-tree diffs for modified-tracked files) — a real cost/complexity increase (binary
    # files, huge files, encoding) that's its own design pass, not a drive-by fix — same class of
    # call as the existing GENERIC_SECRET_RE TODO below. Pinned by
    # tests/test_pretooluse_guards.sh's "H3-LIMIT" case so this gap can't silently regress further
    # or be mistaken for "already handled".
    #
    # Find the segment that actually does the `git commit` (MED fix: the override must be scoped
    # to THIS segment, not "any segment of the whole command" — see _check_test_lock for the same
    # reasoning). If more than one segment matches, the first is used — multiple chained commits
    # in one Bash call is an edge case outside this guard's stated scope.
    commit_segment = next(
        (seg for seg in SHELL_SEGMENT_SPLIT_RE.split(command) if GIT_COMMIT_RE.search(seg)), None
    )
    if commit_segment is None:
        return
    if _process_env_override("CCWT_ALLOW_SECRETS"):
        return
    if _segment_has_leading_override(commit_segment, "CCWT_ALLOW_SECRETS"):
        return
    try:
        result = subprocess.run(
            ["git", "diff", "--cached"],
            cwd=cwd or None,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except Exception:  # noqa: BLE001 - fail open (no git, not a repo, etc.)
        return
    if result.returncode != 0:
        return  # fail open
    diff = result.stdout
    if AWS_KEY_RE.search(diff) or PRIVATE_KEY_RE.search(diff) or GENERIC_SECRET_RE.search(diff):
        _block(
            "Blocked: staged diff appears to contain a secret (AWS key / private-key header / "
            "token-like assignment). Review `git diff --cached`, remove it, or set "
            "CCWT_ALLOW_SECRETS=1 to override."
        )


def _check_danger_guard(command: str, cwd: str):
    # KNOWN LIMITATION, deliberately NOT solved (Increment-4 audit, LOW-MED): `cd / && rm -rf *`
    # evades `_rm_rf_sensitive_target` — we don't track cwd across `&&`-joined segments, and a
    # bare glob (`*`) isn't in SENSITIVE_RM_RF_TARGETS at all. Solving the first half needs a real
    # cwd-tracking state machine across segments (out of scope for a nudge-grade guard); solving
    # the second half by treating a bare `*` as sensitive would false-positive on completely
    # ordinary cleanup like `rm -rf build/*` or `rm -rf dist/*`. Pinned by
    # tests/test_pretooluse_guards.sh's "H7-LIMIT" case (asserts this is NOT detected) so the gap
    # stays a documented, intentional choice rather than a silent regression waiting to be found.
    if _process_env_override("CCWT_ALLOW_DANGER"):
        return
    for segment in SHELL_SEGMENT_SPLIT_RE.split(command):
        reason = None
        rm_target = _rm_rf_sensitive_target(segment)
        if rm_target is not None:
            reason = (
                f"Blocked: 'rm -rf' targeting a sensitive/parent path ('{rm_target}') looks "
                "catastrophic, not a normal cleanup."
            )
        else:
            forced_protected = next(
                (b for b, forced in _push_candidates(segment, cwd) if forced and b in PROTECTED_BRANCHES),
                None,
            )
            if forced_protected is not None:
                reason = (
                    f"Blocked: force-push to protected branch '{forced_protected}'. Force-pushing "
                    "to main/master rewrites shared history."
                )
            else:
                reset_target = _reset_hard_remote_target(segment)
                if reset_target is not None:
                    reason = (
                        f"Blocked: 'git reset --hard {reset_target}' discards local commits "
                        "relative to a remote-tracking ref — likely destroys unpushed work."
                    )
                else:
                    dop = _destructive_worktree_op(segment)  # issue #111
                    if dop is not None:
                        label, advice = dop
                        reason = f"Blocked: '{label}' {advice}."
        if reason is None:
            continue
        if _segment_has_leading_override(segment, "CCWT_ALLOW_DANGER"):
            continue
        _block(reason + " Set CCWT_ALLOW_DANGER=1 to override.")


def _check_protected_branch(command: str, cwd: str):
    if _process_env_override("CCWT_ALLOW_PROTECTED"):
        return
    for segment in SHELL_SEGMENT_SPLIT_RE.split(command):
        if not GIT_COMMIT_OR_PUSH_WITH_DASH_C_RE.search(segment):
            continue
        if _segment_has_leading_override(segment, "CCWT_ALLOW_PROTECTED"):
            continue
        effective_cwd = _resolve_git_cwd(segment, cwd)
        if GIT_PUSH_WITH_DASH_C_RE.search(segment):
            # 2nd fix-up (HIGH-1 still open after the 1st pass): TARGET-aware, not "current
            # branch is protected" unconditionally — `git push origin feature-x` while on main
            # must ALLOW; only the push's actual DESTINATION(S) matter here (3rd fix-up: a push
            # can update MULTIPLE refs at once — `git push origin main feature` — so ANY candidate
            # resolving to a protected branch blocks the whole push, not just the last one).
            branch = next(
                (b for b, _ in _push_candidates(segment, effective_cwd) if b in PROTECTED_BRANCHES),
                None,
            )
            action = "push"
        else:
            # `git commit` — a commit always lands on the CURRENT branch, so this stays
            # unconditional (unchanged from the 1st pass).
            branch = _current_branch(effective_cwd)
            action = "commit"
        if branch in PROTECTED_BRANCHES:
            _block(
                f"Blocked: direct git {action} on protected branch '{branch}'. Use a feature "
                "branch + PR, or set CCWT_ALLOW_PROTECTED=1 to override."
            )


def _check_giant_file(command: str, cwd: str):
    if _process_env_override("CCWT_ALLOW_BIGFILE"):
        return
    max_mb = _max_file_mb()
    for segment in SHELL_SEGMENT_SPLIT_RE.split(command):
        if GIT_ADD_RE.search(segment):
            # Pre-staging: the file isn't in the index yet at hook-check-time, so check its size
            # on disk — from the command's own path arguments, OR (HIGH fix) from `git status` if
            # this is a stages-everything form (-A/-u/`.`) that names no explicit paths.
            for path in _add_command_targets(segment, cwd):
                full = path if os.path.isabs(path) else os.path.join(cwd or ".", path)
                try:
                    if not os.path.isfile(full):
                        continue  # a directory / doesn't exist — nudge-grade, not a shell parser
                    size_mb = os.path.getsize(full) / (1024 * 1024)
                except OSError:
                    continue
                if size_mb > max_mb:
                    if _segment_has_leading_override(segment, "CCWT_ALLOW_BIGFILE"):
                        continue
                    _block(
                        f"Blocked: '{path}' is {size_mb:.1f}MB, over the {max_mb:g}MB limit "
                        "(CCWT_MAX_FILE_MB). Set CCWT_ALLOW_BIGFILE=1 to override."
                    )
        elif GIT_COMMIT_RE.search(segment):
            # Post-staging: the file(s) were staged by an EARLIER `git add` call, so check the
            # index's own blob size (robust even if the working-tree file since changed/gone).
            for path in _staged_names(cwd):
                size_mb = _staged_blob_size_mb(cwd, path)
                if size_mb is not None and size_mb > max_mb:
                    if _segment_has_leading_override(segment, "CCWT_ALLOW_BIGFILE"):
                        continue
                    _block(
                        f"Blocked: staged file '{path}' is {size_mb:.1f}MB, over the {max_mb:g}MB "
                        "limit (CCWT_MAX_FILE_MB). Set CCWT_ALLOW_BIGFILE=1 to override."
                    )


def _check_secret_file_staging(command: str, cwd: str):
    if _process_env_override("CCWT_ALLOW_SECRET_FILE"):
        return
    for segment in SHELL_SEGMENT_SPLIT_RE.split(command):
        if GIT_ADD_RE.search(segment):
            for path in _add_command_targets(segment, cwd):
                if not _is_secret_filename(path):
                    continue
                if _segment_has_leading_override(segment, "CCWT_ALLOW_SECRET_FILE"):
                    continue
                _block(
                    f"Blocked: '{path}' looks like a secret file (.env/*.pem/id_rsa/*.key/"
                    "credentials.json/*service-account*.json) — don't stage it. Set "
                    "CCWT_ALLOW_SECRET_FILE=1 to override."
                )
        elif GIT_COMMIT_RE.search(segment):
            for path in _staged_names(cwd):
                if not _is_secret_filename(path):
                    continue
                if _segment_has_leading_override(segment, "CCWT_ALLOW_SECRET_FILE"):
                    continue
                _block(
                    f"Blocked: staged file '{path}' looks like a secret file (.env/*.pem/id_rsa/"
                    "*.key/credentials.json/*service-account*.json). Set "
                    "CCWT_ALLOW_SECRET_FILE=1 to override."
                )


def _check_pr_review_reminder(command: str) -> str | None:
    """H5 — never blocks. A `gh pr merge` anywhere in the command gets a /review nudge."""
    if PR_MERGE_RE.search(command):
        return "Reminder: run /review on this PR before merging (rules/workflow.md REVIEW phase)."
    return None


def _check_docs_staleness_reminder(command: str, cwd: str) -> str | None:
    """H6 — never blocks. If a `git commit`'s staged diff touches source but NOT docs/, nudge a
    docs-impact-agent check. Uses `_staged_names` (shared with H9/H10) — same event/trigger as
    H3's secret scan, but a separate call since H3 needs the diff CONTENT, this needs file PATHS.

    Broader-fix (Increment-4 audit): unlike H3 (which needs file CONTENT and is documented as a
    known gap for this same scenario — see `_check_secret_scan`), this is CHEAP to close with the
    same H9/H10 enumeration since it only needs filenames — merge in what a same-command chained
    `git add -A`/`.`/`-u` WOULD stage so the reminder isn't silently skipped."""
    if not GIT_COMMIT_RE.search(command):
        return None
    paths = list(_staged_names(cwd))
    for segment in SHELL_SEGMENT_SPLIT_RE.split(command):
        if _add_segment_stages_everything(segment):
            paths.extend(_would_be_staged_by_add_all(cwd))
            break
    if not paths:
        return None  # nothing staged — nothing to flag
    touches_docs = any(p.startswith("docs/") for p in paths)
    touches_source = any(not p.startswith(DOCS_STALENESS_EXCLUDED_PREFIXES) for p in paths)
    if touches_source and not touches_docs:
        return (
            "Reminder: this commit touches source but not docs/ — consider running "
            "docs-impact-agent to check for stale docs."
        )
    return None


def _block(reason: str):
    print(json.dumps({"decision": "block", "reason": reason}))  # noqa: T201
    sys.exit(2)


if __name__ == "__main__":
    main()
