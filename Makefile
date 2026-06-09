.PHONY: perf

perf:
	RUN_PERF=1 pytest -m perf -v
