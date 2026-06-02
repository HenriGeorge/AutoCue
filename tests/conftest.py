"""Shared pytest fixtures — autouse cache clearing prevents test-order contamination."""
import pytest


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
