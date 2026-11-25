"""Bounded predictive telemetry and backend performance profiles."""
from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass

from app.config import IntelligenceConfig


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q * len(ordered)) - 1)] if ordered else 0.0


@dataclass
class Trend:
    points: deque[tuple[float, float]]
    latency_points: deque[tuple[float, float]]
    available: bool = False
    recovered_at: float = 0.0


class Intelligence:
    def __init__(self, config: IntelligenceConfig) -> None:
        self.config = config
        self.trends: dict[str, Trend] = {}
        self.profiles: dict[tuple[str, str], deque[tuple[float, float, bool, int]]] = {}
        self.predictions: dict[str, dict[str, float]] = {}
        self.fingerprints: dict[str, dict] = {}
        self.class_quality: dict[str, dict[str, float]] = {}
        self.client: deque[tuple[float, float, bool]] = deque(maxlen=config.window_size)

    def classify(self, method: str, path: str, headers: dict[str, str]) -> tuple[str, frozenset[str]]:
        if self.config.enabled:
            for rule in self.config.rules:
                if rule.methods and method not in rule.methods:
                    continue
                if not path.startswith(rule.path_prefix):
                    continue
                if any(headers.get(k.lower()) != v for k, v in rule.headers.items()):
                    continue
                return rule.name, frozenset(rule.backend_ids)
        return "default", frozenset()

    def observe(self, backend: str, request_class: str, latency: float,
                success: bool, concurrency: int) -> None:
        if not self.config.enabled:
            return
        now = time.monotonic()
        for name in ("*", request_class):
            key = (backend, name)
            if key not in self.profiles:
                self.profiles[key] = deque(maxlen=self.config.window_size)
            self.profiles[key].append((now, latency, success, concurrency))

    def observe_client(self, latency: float, success: bool) -> None:
        if self.config.enabled:
            self.client.append((time.monotonic(), latency, success))

    def remove(self, backend: str) -> None:
        self.trends.pop(backend, None)
        self.predictions.pop(backend, None)
        self.fingerprints.pop(backend, None)
        for key in list(self.profiles):
            if key[0] == backend:
                del self.profiles[key]
        for quality in self.class_quality.values():
            quality.pop(backend, None)

    def sample(self, backend: str, *, now: float, connections: int, queue: int,
               capacity: float, latency: float, error_rate: float,
               available: bool) -> dict[str, float]:
        if backend not in self.trends:
            self.trends[backend] = Trend(
                deque(maxlen=self.config.trend_points), deque(maxlen=self.config.trend_points)
            )
        trend = self.trends[backend]
        if available and not trend.available:
            trend.recovered_at = now
            trend.points.clear()
            trend.latency_points.clear()
        trend.available = available
        pressure = (connections + queue) / max(1.0, capacity)
        trend.points.append((now, pressure))
        trend.latency_points.append((now, latency))
        points = list(trend.points)
        slope, confidence = 0.0, 0.0
        if len(points) >= 3:
            xs = [t - points[0][0] for t, _ in points]
            ys = [p for _, p in points]
            mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
            variance = sum((x - mx) ** 2 for x in xs)
            if variance > 1e-9:
                slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / variance
                residual = sum((y - my - slope * (x - mx)) ** 2 for x, y in zip(xs, ys))
                total = sum((y - my) ** 2 for y in ys)
                confidence = max(0.0, 1 - residual / total) if total > 1e-9 else 0.0
                confidence *= min(1.0, len(points) / 5)
        forecast = max(0.0, pressure + slope * self.config.horizon_seconds)
        risk = min(1.0, max(0.0, forecast - self.config.congestion_threshold)
                   / (1 - self.config.congestion_threshold)) * confidence
        latency_points = list(trend.latency_points)
        elapsed = latency_points[-1][0] - latency_points[0][0]
        latency_slope = ((latency_points[-1][1] - latency_points[0][1]) / elapsed
                         if elapsed > 0 else 0.0)
        result = {"pressure": pressure, "forecast": forecast, "slope_per_second": slope,
                  "latency_slope_ms_per_second": latency_slope * 1000,
                  "confidence": confidence, "risk": risk, "latency_ms": latency * 1000,
                  "error_rate": error_rate, "recovery_age": max(0.0, now - trend.recovered_at)}
        self.predictions[backend] = result
        return result

    def summarize(self, now: float, backend_ids: set[str]) -> None:
        cutoff = now - self.config.observation_seconds
        for backend in set(self.trends) - backend_ids:
            self.remove(backend)
        fingerprints: dict[str, dict] = {bid: {} for bid in backend_ids}
        qualities: dict[str, dict[str, float]] = {}
        for (bid, name), window in self.profiles.items():
            if bid not in backend_ids:
                continue
            samples = [x for x in window if x[0] >= cutoff]
            if not samples:
                continue
            latency = [x[1] for x in samples]
            success = sum(x[2] for x in samples)
            error = 1 - success / len(samples)
            p95 = percentile(latency, .95)
            fingerprints[bid][name] = {
                "samples": len(samples), "p50_ms": percentile(latency, .5) * 1000,
                "p95_ms": p95 * 1000, "p99_ms": percentile(latency, .99) * 1000,
                "error_rate": error, "observed_peak_concurrency": max(x[3] for x in samples),
                "success_rps_lower_bound": success / self.config.observation_seconds,
                "confidence": min(1.0, len(samples) / 20),
            }
            if name != "*" and len(samples) >= 5:
                qualities.setdefault(name, {})[bid] = max(
                    .25, 1 / (1 + p95 / self.config.p99_target_seconds + 4 * error)
                )
        self.fingerprints, self.class_quality = fingerprints, qualities

    def slo(self, now: float) -> dict[str, float]:
        samples = [x for x in self.client if x[0] >= now - self.config.observation_seconds]
        n = len(samples)
        success = sum(x[2] for x in samples)
        return {"samples": n, "p99_seconds": percentile([x[1] for x in samples], .99),
                "error_rate": 1 - success / n if n else 0.0,
                "availability": success / n if n else 1.0,
                "success_rps_lower_bound": success / self.config.observation_seconds}
