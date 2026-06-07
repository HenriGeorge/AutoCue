"""Shared pytest fixtures — autouse cache clearing prevents test-order contamination."""
import os
import pytest


def pytest_collection_modifyitems(config, items):
    """TASK-048 — gate ``@pytest.mark.perf`` tests behind ``RUN_PERF=1``.

    Without this, perf benchmarks ship the full library through hot paths
    on every CI run; they're intentionally opt-in.
    """
    if os.environ.get("RUN_PERF") == "1":
        return
    skip_perf = pytest.mark.skip(reason="RUN_PERF=1 not set; perf benchmarks skipped")
    for item in items:
        if "perf" in item.keywords:
            item.add_marker(skip_perf)


@pytest.fixture(autouse=True)
def clear_analysis_caches():
    """Clear all module-level analysis caches before each test."""
    from autocue.analysis import energy, classify, score
    from autocue.analysis.similar import clear_index as _clear_similar
    energy._cache.clear()
    classify._class_cache.clear()
    score._mixability_cache.clear()
    _clear_similar()
    yield
