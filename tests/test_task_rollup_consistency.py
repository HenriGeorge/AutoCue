"""Guard against TASK rollup/per-step/notes drift in .agent/tasks.performance.json.

Issue #109: TASK-024 was rolled up as ``passes: true`` while its per-step
``pass`` flags were ``false`` and ``.agent/prd/PERFORMANCE_NOTES.md`` explicitly
marks the task ``deferred``. This test asserts the invariant that a task whose
spec carries any ``pass: false`` step AND whose ID appears under a "deferred"
marker in ``PERFORMANCE_NOTES.md`` must NOT roll up as ``passes: true``.

The test is intentionally property-style, not value-specific: it doesn't
hardcode ``TASK-024``. Any future task that drifts the same way will trip it.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
TASKS_FILE = REPO_ROOT / ".agent" / "tasks.performance.json"
NOTES_FILE = REPO_ROOT / ".agent" / "prd" / "PERFORMANCE_NOTES.md"


def _load_rollup() -> list[dict]:
    return json.loads(TASKS_FILE.read_text())


def _deferred_task_ids() -> set[str]:
    """Parse PERFORMANCE_NOTES.md and return task IDs marked deferred.

    A task is "deferred" if it has a heading like ``## TASK-NNN: ...`` whose
    body block contains the literal word ``deferred`` (case-insensitive) before
    the next ``## `` heading.
    """
    text = NOTES_FILE.read_text()
    deferred: set[str] = set()
    # Split into heading-anchored blocks.
    blocks = re.split(r"(?m)^## ", text)
    for block in blocks:
        m = re.match(r"(TASK-\d+)", block)
        if not m:
            continue
        if re.search(r"\bdeferred\b", block, re.IGNORECASE):
            deferred.add(m.group(1))
    return deferred


def _step_passes(task_id: str, rollup_entry: dict) -> bool | None:
    """Return AND of per-step ``pass`` flags from the spec, or ``None`` if no spec/steps."""
    spec_rel = rollup_entry.get("specFilePath")
    if not spec_rel:
        return None
    spec_path = REPO_ROOT / spec_rel
    if not spec_path.exists():
        return None
    spec = json.loads(spec_path.read_text())
    steps = spec.get("steps") or []
    pass_flags = [s.get("pass") for s in steps if "pass" in s]
    if not pass_flags:
        return None
    return all(pass_flags)


def test_rollup_passes_true_implies_all_steps_pass_for_deferred_tasks():
    """A task that PERFORMANCE_NOTES.md marks deferred must not roll up as passing.

    Regression for issue #109: without this guard, TASK-024 quietly carried
    ``passes: true`` even though its per-step ``pass: false`` flags and the
    deferral note both contradicted that rollup.
    """
    rollup = _load_rollup()
    deferred_ids = _deferred_task_ids()
    assert deferred_ids, (
        "expected at least one deferred task marker in PERFORMANCE_NOTES.md "
        "(test setup invariant — if every deferred task ships, delete this test)"
    )

    drifted: list[str] = []
    for entry in rollup:
        task_id = entry.get("id")
        if task_id not in deferred_ids:
            continue
        # Spec per-step rollup must NOT be all-pass for a deferred task.
        steps_pass = _step_passes(task_id, entry)
        if steps_pass is False and entry.get("passes") is True:
            drifted.append(task_id)

    assert not drifted, (
        f"tasks rolled up as passes:true while deferred + per-step pass:false present: {drifted}. "
        "Either complete the deferred work and flip the per-step pass flags, "
        "or set passes:false in tasks.performance.json."
    )


@pytest.mark.parametrize(
    "task_id",
    ["TASK-024"],
)
def test_known_deferred_tasks_roll_up_false(task_id):
    """Boundary check at the exact threshold for issue #109.

    TASK-024 is the canonical drift case. Even if the more general invariant
    above is loosened, this specific row must not regress without an explicit
    decision (i.e. a benchmark landing).
    """
    rollup = _load_rollup()
    entry = next((e for e in rollup if e.get("id") == task_id), None)
    assert entry is not None, f"{task_id} missing from tasks.performance.json"
    deferred = _deferred_task_ids()
    if task_id not in deferred:
        pytest.skip(
            f"{task_id} no longer marked deferred in PERFORMANCE_NOTES.md — "
            "benchmark must have landed; safe to delete this case."
        )
    assert entry.get("passes") is False, (
        f"{task_id} is still deferred in PERFORMANCE_NOTES.md but rolls up "
        f"as passes={entry.get('passes')!r}. Flip to false until the "
        "benchmark in tests/perf/test_tracks_sql.py lands with timings."
    )
