#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""PreToolUse tdd-gate — V1 commit-time TDD enforcement (issue #150).

Fires on a `git commit` (token-based, flag-tolerant — same segment-split/`-C`-resolution/`-a`
staging pattern as grill_gate.py). Classifies every staged file as TEST / SOURCE / NEUTRAL and
BLOCKs when the staged set has >=1 SOURCE change and 0 TEST changes. Bypass: `WORKFLOW:no-tdd`.

SPLIT RESPONSIBILITIES (see docs/ENFORCEMENT.md): `test-driven-development` (the skill) teaches
RED->GREEN and run-and-observe; a test RUNNER decides pass/fail; THIS gate only enforces that a
test file changed ALONGSIDE a source change — it does NOT run tests and does NOT claim a test was
observed failing (RED). That is a real, intentional limitation of V1, not an oversight — see the
V2->V4 roadmap in docs/ENFORCEMENT.md for stronger checks (test-meaningfully-changed-before-impl,
test-actually-failed, adversarial-agent-validates).

CAVEAT: a strict test-change-required-every-commit rule blocks a legitimate two-commit RED-then-
GREEN split (a test-only "RED" commit followed by a separate impl-only "GREEN" commit) — the second
commit has no test file in ITS OWN staged set. Commit red+green together, or use the bypass for
that specific commit.

Fail-open (allow): `git commit --amend`, a merge commit (`MERGE_HEAD` present), not a git repo, no
staged files, or any git error. Never wedge a legitimate commit over this gate's own bug or a
flaky `git` call.

Classification is intentionally conservative (this template ships to many stacks) and overridable
per-project via `TDD_GATE_TEST_GLOBS` / `TDD_GATE_SOURCE_GLOBS` (comma-separated extra glob
patterns, checked ADDITIVELY alongside the defaults below) — set as an env var, or a
`TDD_GATE_TEST_GLOBS="..."` / `TDD_GATE_SOURCE_GLOBS="..."` line in `.claude/worktrees.conf` (env
var wins if both are present). An unrecognized file extension defaults to NEUTRAL (never SOURCE) —
uncertainty biases toward not blocking, consistent with every other hook in this directory.
"""
import fnmatch
import json
import os
import re
import shlex
import subprocess
import sys

SHELL_SEGMENT_SPLIT_RE = re.compile(r"&&|\|\||;|\n|\|")
GIT_COMMIT_RE = re.compile(r"\bgit\s+commit\b")
GIT_DASH_C_RE = re.compile(r"\bgit\s+-C\s+(\S+)")
BYPASS_SENTINEL = "WORKFLOW:no-tdd"
COMMIT_ALL_LONG = "--all"
ADD_STAGES_EVERYTHING_FLAGS = ("-A", "--all", "-u", "--update")

# fnmatch's `*` already crosses `/` (unlike shell globbing), so "tests/*" matches
# "tests/sub/test_x.py" too — no special "**" handling needed. A pattern anchored to the START of
# the string (no leading `*`) only matches a TOP-LEVEL dir of that name — "tests/*" does NOT match
# a NESTED "myapp/tests/test_x.py" (Django's own per-app convention); that needs a leading `*/`
# variant too (fix-round, adversarial audit CRITICAL — the original list missed nested tests/,
# Ruby's spec/ + _spec.rb, and JS/TS's __tests__/, all mainstream layouts). `*_spec.rb` is
# extension-SCOPED (not a bare "*_spec.*") — a bare wildcard extension over-matched non-test files
# that merely end in "_spec" (e.g. an OpenAPI schema named api_spec.json), a false-negative that
# would let a source-only commit slip through as if it had a real test (fix-round 2, adversarial
# re-check). The dotted `.spec.js`/`.spec.ts` forms are already covered by `*.spec.*` above.
#
# DIRECTORY-NAME globs classify EVERY file under that dir as TEST, regardless of extension/content
# — safe ONLY for a strongly test-exclusive dir name. "spec/"/"*/spec/*" were DROPPED (fix-round 3,
# adversarial re-audit): "spec" is a heavily overloaded word (an OpenAPI/Swagger spec doc, a
# JSON-schema dir, a design-spec doc dir all commonly live under "spec/") — TEST is checked BEFORE
# NEUTRAL, so a plain `spec/openapi.yaml` was wrongly swept into TEST purely by directory name.
# `*_spec.rb` alone already fully covers RSpec regardless of directory (fnmatch's `*` crosses `/`),
# so dropping the dir globs loses zero real coverage. "test"/"tests"/"__tests__" as directory names
# are near-universally test-code across every ecosystem (no comparable "specification" collision),
# so those are kept as directory globs.
TEST_GLOBS_DEFAULT = (
    "*.test.*", "*.spec.*", "*_test.*", "*_spec.rb", "test_*.py",
    "tests/*", "*/tests/*", "test/*", "*/test/*",
    "__tests__/*", "*/__tests__/*",
    "e2e/*", "*.spec.ts",
)
NEUTRAL_GLOBS_DEFAULT = (
    "*.md", "*.json", "*.yml", "*.yaml", "*.toml", "*.lock", "*.cfg", "*.ini",
    "*.css", "*.scss", "*.png", "*.jpg", "*.jpeg", "*.gif", "*.svg", "*.ico", "docs/*",
)
SOURCE_EXTS_DEFAULT = (
    ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".py", ".go", ".rs", ".java",
    ".rb", ".php", ".c", ".cpp", ".h", ".hpp", ".sh",
)


def _run_git(cwd: str, *args) -> str | None:
    try:
        r = subprocess.run(
            ["git", *args], cwd=cwd or None, capture_output=True, text=True,
            timeout=5, check=False,
        )
    except Exception:  # noqa: BLE001 - fail open
        return None
    return r.stdout if r.returncode == 0 else None


def _resolve_git_cwd(segment: str, cwd: str) -> str:
    m = GIT_DASH_C_RE.search(segment)
    if not m:
        return cwd
    target = m.group(1).strip("'\"")
    return target if os.path.isabs(target) else os.path.join(cwd or ".", target)


def _staged_names(cwd: str) -> list:
    out = _run_git(cwd, "diff", "--cached", "--name-only")
    return [p.strip() for p in out.splitlines() if p.strip()] if out else []


def _add_segment_stages_everything(segment: str) -> bool:
    m = re.search(r"\bgit\s+add\b(.*)$", segment)
    if not m:
        return False
    try:
        tokens = shlex.split(m.group(1))
    except ValueError:
        tokens = m.group(1).split()
    if any(t in ADD_STAGES_EVERYTHING_FLAGS for t in tokens):
        return True
    return any(t in (".", "./") for t in tokens if not t.startswith("-"))


def _explicit_add_paths(segment: str) -> list:
    m = re.search(r"\bgit\s+add\b(.*)$", segment)
    if not m:
        return []
    try:
        tokens = shlex.split(m.group(1))
    except ValueError:
        tokens = m.group(1).split()
    return [t for t in tokens if t and not t.startswith("-")]


def _would_be_staged_everything(cwd: str) -> list:
    out = _run_git(cwd, "status", "--porcelain", "--untracked-files=all")
    if not out:
        return []
    paths = []
    for line in out.splitlines():
        if len(line) < 4:
            continue
        rest = line[3:].strip()
        if " -> " in rest:
            rest = rest.split(" -> ", 1)[1].strip()
        if len(rest) >= 2 and rest[0] == '"' and rest[-1] == '"':
            rest = rest[1:-1]
        if rest:
            paths.append(rest)
    return paths


def _collect_staged(command: str, cwd: str) -> list:
    """Everything already staged, PLUS what a same-command chained `git add` would stage."""
    paths = list(_staged_names(cwd))
    for segment in SHELL_SEGMENT_SPLIT_RE.split(command):
        if not re.search(r"\bgit\s+add\b", segment):
            continue
        if _add_segment_stages_everything(segment):
            paths.extend(_would_be_staged_everything(cwd))
        else:
            paths.extend(_explicit_add_paths(segment))
    return paths


def _commit_segment(command: str) -> str:
    return next(
        (s for s in SHELL_SEGMENT_SPLIT_RE.split(command) if GIT_COMMIT_RE.search(s)),
        command,
    )


def _commit_tokens(segment: str) -> list:
    m = re.search(r"\bcommit\b(.*)$", segment)
    if not m:
        return []
    try:
        return shlex.split(m.group(1))
    except ValueError:
        return m.group(1).split()


def _commit_stages_all(command: str) -> bool:
    """True if the `git commit` command auto-stages modified tracked files (-a / --all / -am)."""
    tokens = _commit_tokens(_commit_segment(command))
    for t in tokens:
        if t == COMMIT_ALL_LONG:
            return True
        if t.startswith("--"):
            continue
        if t.startswith("-") and len(t) > 1:
            for ch in t[1:]:
                if ch == "a":
                    return True
                if ch == "m":  # -m consumes the remainder of the cluster as the message
                    break
    return False


def _commit_is_amend(command: str) -> bool:
    return "--amend" in _commit_tokens(_commit_segment(command))


def _modified_tracked(cwd: str) -> list:
    out = _run_git(cwd, "diff", "--name-only")
    return [p.strip() for p in out.splitlines() if p.strip()] if out else []


def _is_merge_in_progress(cwd: str) -> bool:
    return _run_git(cwd, "rev-parse", "-q", "--verify", "MERGE_HEAD") is not None


def _extra_globs(cwd: str, env_name: str, conf_key: str) -> list:
    val = os.environ.get(env_name)
    if val is None:
        conf_path = os.path.join(cwd or ".", ".claude", "worktrees.conf")
        try:
            with open(conf_path, encoding="utf-8") as fh:
                text = fh.read()
        except OSError:
            text = ""
        m = re.search(rf'^{re.escape(conf_key)}\s*=\s*"?([^"\n]*)"?', text, re.MULTILINE)
        val = m.group(1) if m else ""
    return [g.strip() for g in val.split(",") if g.strip()]


def _classify(path: str, test_globs: list, source_globs: list) -> str:
    norm = path.replace("\\", "/")
    if any(fnmatch.fnmatch(norm, g) for g in TEST_GLOBS_DEFAULT) or any(
        fnmatch.fnmatch(norm, g) for g in test_globs
    ):
        return "TEST"
    if any(fnmatch.fnmatch(norm, g) for g in NEUTRAL_GLOBS_DEFAULT):
        return "NEUTRAL"
    if any(fnmatch.fnmatch(norm, g) for g in source_globs):
        return "SOURCE"
    ext = os.path.splitext(norm)[1]
    if ext in SOURCE_EXTS_DEFAULT:
        return "SOURCE"
    return "NEUTRAL"  # unknown extension -> never block on uncertainty


def _block(reason: str):
    print(json.dumps({"decision": "block", "reason": reason}))  # noqa: T201
    sys.exit(2)


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
        if not GIT_COMMIT_RE.search(command):
            sys.exit(0)
        if BYPASS_SENTINEL in command:
            sys.exit(0)
        if _commit_is_amend(command):
            sys.exit(0)
        commit_seg = _commit_segment(command)
        git_cwd = _resolve_git_cwd(commit_seg, cwd)
        if _is_merge_in_progress(git_cwd):
            sys.exit(0)
        stages_all = _commit_stages_all(command)
        paths = list(_collect_staged(command, git_cwd))
        if stages_all:
            paths.extend(_modified_tracked(git_cwd))
        # de-dup, preserve order
        seen = set()
        unique_paths = []
        for p in paths:
            if p not in seen:
                seen.add(p)
                unique_paths.append(p)
        if not unique_paths:
            sys.exit(0)  # nothing staged (also covers "not a git repo") -> fail open
        test_globs = _extra_globs(git_cwd, "TDD_GATE_TEST_GLOBS", "TDD_GATE_TEST_GLOBS")
        source_globs = _extra_globs(git_cwd, "TDD_GATE_SOURCE_GLOBS", "TDD_GATE_SOURCE_GLOBS")
        classes = [_classify(p, test_globs, source_globs) for p in unique_paths]
        has_source = "SOURCE" in classes
        has_test = "TEST" in classes
        if has_source and not has_test:
            source_files = [p for p, c in zip(unique_paths, classes) if c == "SOURCE"]
            _block(
                "Blocked: implementation changed with no accompanying test change (#TDD) — "
                f"staged source file(s): {', '.join(source_files[:5])}"
                f"{'…' if len(source_files) > 5 else ''}. Write/adjust a test, or add "
                f"'{BYPASS_SENTINEL}' to the commit command."
            )
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001 - never brick a session
        sys.exit(0)
    sys.exit(0)


if __name__ == "__main__":
    main()
