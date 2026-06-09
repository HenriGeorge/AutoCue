# AutoCue developer entry points.
#
# Targets are intentionally thin wrappers — they exist so contributors don't
# need to remember env-var incantations. Each target is one line; complex
# logic belongs in scripts under .claude/ or tests/.

.PHONY: perf

# TASK-048 — run the gated performance benchmark suite.
# RUN_PERF=1 unlocks tests marked @pytest.mark.perf (see tests/conftest.py).
perf:
	RUN_PERF=1 pytest -m perf -v
