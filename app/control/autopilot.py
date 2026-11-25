"""Deterministic SLO feedback and opt-in bounded contextual exploration."""
from __future__ import annotations

from collections import deque
import random

from app.config import IntelligenceConfig

KEYS = ("latency", "connections", "queue", "errors", "resources")
PRESETS = {
    "balanced": (.35, .25, .15, .20, .05),
    "latency": (.55, .18, .10, .12, .05),
    "throughput": (.15, .35, .25, .15, .10),
    "reliability": (.15, .15, .10, .55, .05),
}


class Autopilot:
    def __init__(self, config: IntelligenceConfig) -> None:
        self.config = config
        self.objective = config.objective
        self.enabled, self.learning = config.autopilot, config.learning
        self.history: deque[dict] = deque(maxlen=100)
        self.last_change = float("-inf")
        self.cooldown_until = float("-inf")
        self.previous: dict[str, float] | None = None
        self.last_loss: float | None = None
        self.last_arm: tuple[str, str] | None = None
        self.arms: dict[tuple[str, str], tuple[int, float]] = {}
        self.rng = random.Random(config.learning_seed)
        self.reason = "manual"

    def configure(self, objective: str, enabled: bool, learning: bool) -> None:
        if objective not in {*PRESETS, "custom"} or (learning and not enabled):
            raise ValueError("invalid objective or feature flags")
        self.objective, self.enabled, self.learning = objective, enabled, learning
        self.last_loss = self.previous = self.last_arm = None
        self.last_change = float("-inf")

    def update(self, now: float, current: dict[str, float], base: dict[str, float],
               slo: dict[str, float], pressure: float) -> dict[str, float]:
        cfg = self.config
        if (not self.enabled or slo["samples"] < cfg.minimum_samples
                or now < self.cooldown_until or now - self.last_change < cfg.policy_cooldown):
            return current
        latency = slo["p99_seconds"] / cfg.p99_target_seconds
        errors = slo["error_rate"] / cfg.error_target
        availability = max(0.0, cfg.availability_target - slo["availability"])
        throughput = max(0.0, 1 - slo["success_rps_lower_bound"] / cfg.throughput_target) if cfg.throughput_target else 0.0
        loss = max(0.0, latency - 1) + max(0.0, errors - 1) + 10 * availability + throughput
        context = "congested" if pressure >= .8 else "normal"
        if self.last_arm is not None:
            n, reward = self.arms.get(self.last_arm, (0, 0.0))
            self.arms[self.last_arm] = (n + 1, reward + (-loss - reward) / (n + 1))
        self.last_arm = None
        if self.last_loss is not None and self.previous is not None and loss > self.last_loss + cfg.rollback_margin:
            result = self.previous.copy()
            self.reason = "rollback: observed SLO loss increased"
            self.cooldown_until = now + 2 * cfg.policy_cooldown
            self.last_loss = self.previous = None
        else:
            objective = self.objective
            if errors > 1 or availability > 0:
                objective = "reliability"
            elif latency > 1 and objective != "custom":
                objective = "latency"
            elif (pressure >= .8 or throughput > 0) and objective != "custom":
                objective = "throughput"
            elif self.learning and objective != "custom" and loss == 0:
                choices = tuple(PRESETS)
                if self.rng.random() < cfg.learning_exploration:
                    objective = self.rng.choice(choices)
                elif any((context, arm) in self.arms for arm in choices):
                    objective = max(choices, key=lambda arm: self.arms.get((context, arm), (0, -1e9))[1])
                self.last_arm = (context, objective)
            target = ({key: base[key] / sum(base.values()) for key in KEYS}
                      if objective == "custom" else dict(zip(KEYS, PRESETS[objective])))
            previous = {key: current[key] / sum(current.values()) for key in KEYS}
            result = {key: (1 - cfg.weight_step) * previous[key] + cfg.weight_step * target[key]
                      for key in KEYS}
            self.previous, self.last_loss = previous, loss
            self.reason = f"{context}: blend toward {objective}"
        self.last_change = now
        self.history.append({"time": now, "reason": self.reason, "weights": result,
                             "slo": dict(slo), "loss": loss})
        return result

    def to_dict(self) -> dict:
        return {"enabled": self.enabled, "learning": self.learning, "objective": self.objective,
                "reason": self.reason, "history": list(self.history),
                "arms": [{"context": c, "objective": a, "samples": n, "mean_reward": r}
                         for (c, a), (n, r) in self.arms.items()]}
