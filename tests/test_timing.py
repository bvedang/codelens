import time

import pytest

from codelens.timing import TimingCollector, duration_ms, measure


def test_measure_returns_stable_non_negative_duration():
    with measure() as stopwatch:
        time.sleep(0.01)

    first = stopwatch.elapsed_seconds
    time.sleep(0.01)
    second = stopwatch.elapsed_seconds

    assert first >= 0.0
    assert second == first
    assert stopwatch.elapsed_ms == duration_ms(first)


def test_timing_collector_aggregates_named_durations():
    collector = TimingCollector()

    collector.add("parse", 0.125)
    collector.add("parse", 0.25)
    collector.add("embed", 0.05)

    assert collector.seconds("parse") == pytest.approx(0.375)
    assert collector.seconds("embed") == pytest.approx(0.05)
    assert collector.seconds("persist") == 0.0
    assert collector.ms("parse") == 375
    assert collector.ms("embed") == 50
    assert collector.ms("persist") == 0
    assert collector.as_milliseconds() == {
        "parse": 375,
        "embed": 50,
    }
