"""Tests for autocue.perf — ring-buffer instrumentation.

See .agent/prd/PERFORMANCE_PRD.md TASK-044.
"""
from __future__ import annotations

import time

import pytest

from autocue import perf


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    """Reset module state between tests."""
    perf.clear()
    monkeypatch.setattr(perf, "_enabled", True)
    monkeypatch.setattr(perf, "_sample_rate", 1.0)
    yield
    perf.clear()


def test_disabled_no_op_records_nothing(monkeypatch):
    monkeypatch.setattr(perf, "_enabled", False)
    with perf.perf_span("noop"):
        pass
    assert perf.recent_spans() == []


def test_enabled_records_duration():
    with perf.perf_span("work"):
        time.sleep(0.005)
    spans = perf.recent_spans()
    assert len(spans) == 1
    name, _start, dur_ms = spans[0]
    assert name == "work"
    assert dur_ms >= 4.0  # 5ms sleep, some slack for timer resolution


def test_records_propagate_exceptions():
    with pytest.raises(ValueError):
        with perf.perf_span("boom"):
            raise ValueError("test")
    spans = perf.recent_spans()
    assert len(spans) == 1
    assert spans[0][0] == "boom"


def test_get_stats_returns_percentiles():
    for i in range(100):
        with perf.perf_span("repeated"):
            time.sleep(0.001)
    stats = perf.get_stats("repeated")
    assert stats is not None
    assert stats["count"] == 100.0
    assert stats["p50"] >= 0.5
    assert stats["p95"] >= stats["p50"]
    assert stats["p99"] >= stats["p95"]


def test_get_stats_unknown_name_returns_none():
    with perf.perf_span("known"):
        pass
    assert perf.get_stats("unknown") is None


def test_buffer_eviction_at_max():
    # Buffer maxlen is 1000; insert 1500 spans and confirm only last 1000 kept.
    for i in range(1500):
        with perf.perf_span(f"span-{i}"):
            pass
    spans = perf.recent_spans(limit=10000)
    assert len(spans) == 1000
    # First retained span should be 500 (we evicted 0..499).
    names = {s[0] for s in spans}
    assert "span-1499" in names
    assert "span-0" not in names


def test_sample_rate_zero_records_nothing(monkeypatch):
    monkeypatch.setattr(perf, "_sample_rate", 0.0)
    # Force the random branch to always return 1.0 > 0.0, which skips.
    monkeypatch.setattr(perf.random, "random", lambda: 1.0)
    for _ in range(50):
        with perf.perf_span("sampled-out"):
            pass
    assert perf.recent_spans() == []


def test_sample_rate_partial(monkeypatch):
    """sample_rate=0.5 + deterministic random keeps about half."""
    monkeypatch.setattr(perf, "_sample_rate", 0.5)
    seq = iter([0.4, 0.6] * 50)
    monkeypatch.setattr(perf.random, "random", lambda: next(seq))
    for _ in range(100):
        with perf.perf_span("partial"):
            pass
    # 50 of 100 random values are <0.5 → recorded.
    assert len(perf.recent_spans(limit=200)) == 50


def test_recent_spans_limit():
    for i in range(20):
        with perf.perf_span(f"span-{i}"):
            pass
    last5 = perf.recent_spans(limit=5)
    assert len(last5) == 5
    names = [s[0] for s in last5]
    assert names == [f"span-{i}" for i in range(15, 20)]


def test_concurrent_writes_thread_safe():
    import threading
    def _worker(prefix):
        for i in range(100):
            with perf.perf_span(f"{prefix}-{i}"):
                pass
    threads = [threading.Thread(target=_worker, args=(f"t{n}",)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    # Buffer should hold the last 1000 records; 400 written total so all retained.
    assert len(perf.recent_spans(limit=10000)) == 400
