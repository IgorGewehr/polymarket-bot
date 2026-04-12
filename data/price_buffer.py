"""
Buffer circular de alta performance para ticks de preço.
Usa numpy arrays pré-alocados para evitar alocação dinâmica no hot path.
"""
import time
import numpy as np
from dataclasses import dataclass, field


@dataclass
class Tick:
    timestamp: float
    price: float
    delta: float = 0.0


class PriceBuffer:
    """Buffer circular lock-free para ticks de preço."""

    def __init__(self, maxlen: int = 400):
        self.maxlen = maxlen
        self.timestamps = np.zeros(maxlen, dtype=np.float64)
        self.prices = np.zeros(maxlen, dtype=np.float64)
        self.deltas = np.zeros(maxlen, dtype=np.float64)
        self._head = 0
        self._count = 0

    def append(self, timestamp: float, price: float, delta: float = 0.0):
        idx = self._head % self.maxlen
        self.timestamps[idx] = timestamp
        self.prices[idx] = price
        self.deltas[idx] = delta
        self._head += 1
        self._count = min(self._count + 1, self.maxlen)

    def get_prices(self, n: int | None = None) -> np.ndarray:
        """Retorna os últimos n preços em ordem cronológica."""
        if self._count == 0:
            return np.array([])
        count = min(n or self._count, self._count)
        indices = self._get_indices(count)
        return self.prices[indices]

    def get_timestamps(self, n: int | None = None) -> np.ndarray:
        count = min(n or self._count, self._count)
        indices = self._get_indices(count)
        return self.timestamps[indices]

    def get_deltas(self, n: int | None = None) -> np.ndarray:
        count = min(n or self._count, self._count)
        indices = self._get_indices(count)
        return self.deltas[indices]

    def latest_price(self) -> float | None:
        if self._count == 0:
            return None
        return self.prices[(self._head - 1) % self.maxlen]

    def latest_delta(self) -> float | None:
        if self._count == 0:
            return None
        return self.deltas[(self._head - 1) % self.maxlen]

    def _get_indices(self, count: int) -> np.ndarray:
        start = (self._head - count) % self.maxlen
        if start + count <= self.maxlen:
            return np.arange(start, start + count)
        return np.concatenate([
            np.arange(start, self.maxlen),
            np.arange(0, (start + count) - self.maxlen)
        ])

    @property
    def count(self) -> int:
        return self._count

    def clear(self):
        self._head = 0
        self._count = 0


class CycleTracker:
    """Rastreia deltas máximos dos últimos N ciclos para detecção de regime."""

    def __init__(self, max_cycles: int = 10):
        self.max_cycles = max_cycles
        self.cycle_max_deltas: list[float] = []
        self.current_cycle_max_delta: float = 0.0

    def update_tick(self, delta: float):
        abs_delta = abs(delta)
        if abs_delta > self.current_cycle_max_delta:
            self.current_cycle_max_delta = abs_delta

    def end_cycle(self):
        self.cycle_max_deltas.append(self.current_cycle_max_delta)
        if len(self.cycle_max_deltas) > self.max_cycles:
            self.cycle_max_deltas.pop(0)
        self.current_cycle_max_delta = 0.0

    def get_recent_max_deltas(self, n: int = 5) -> list[float]:
        return self.cycle_max_deltas[-n:]

    def avg_max_delta(self, n: int = 5) -> float:
        recent = self.get_recent_max_deltas(n)
        return sum(recent) / len(recent) if recent else 0.0
