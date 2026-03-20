from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from time import perf_counter
from typing import Iterator


def monotonic_seconds() -> float:
    return perf_counter()


def duration_ms(seconds: float) -> int:
    return int(max(0.0, seconds) * 1000)


@dataclass
class Stopwatch:
    started_at: float
    stopped_at: float | None = None

    @classmethod
    def start(cls) -> Stopwatch:
        return cls(started_at=monotonic_seconds())

    def stop(self) -> float:
        if self.stopped_at is None:
            self.stopped_at = monotonic_seconds()
        return self.elapsed_seconds

    @property
    def elapsed_seconds(self) -> float:
        end = self.stopped_at if self.stopped_at is not None else monotonic_seconds()
        return max(0.0, end - self.started_at)

    @property
    def elapsed_ms(self) -> int:
        return duration_ms(self.elapsed_seconds)


@contextmanager
def measure() -> Iterator[Stopwatch]:
    stopwatch = Stopwatch.start()
    try:
        yield stopwatch
    finally:
        stopwatch.stop()


@dataclass
class TimingCollector:
    totals: dict[str, float] = field(default_factory=dict)

    def add(self, name: str, seconds: float) -> float:
        total = self.totals.get(name, 0.0) + seconds
        self.totals[name] = total
        return total

    def seconds(self, name: str) -> float:
        return self.totals.get(name, 0.0)

    def ms(self, name: str) -> int:
        return duration_ms(self.seconds(name))

    def as_milliseconds(self) -> dict[str, int]:
        return {
            name: duration_ms(seconds)
            for name, seconds in self.totals.items()
        }
