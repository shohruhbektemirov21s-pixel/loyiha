"""Per-category confidence threshold business logic.

Thresholds are loaded from ``threshold_configs`` at startup and cached with
a short TTL. The admin API (``/v1/admin/thresholds``) invalidates the cache
on update.

Three zones per category (tuned by the admin, defaults in schema.sql):

    score < auto_clear_threshold     → AUTO-CLEAR  (no operator alert)
    auto_clear_threshold ≤ score
                         < alert_threshold → MONITOR (queue, low priority)
    score ≥ alert_threshold          → ALERT    (mandatory operator review)

The auto-clear zone is conservative by design: explosives have a 0.15 floor,
meaning a score of 0.14 still triggers MONITOR, not AUTO-CLEAR.  The intent
is: never silently discard a detection — only auto-clear when we are
very confident the detector is wrong.

ThresholdDecision drives what the operator console shows:
  AUTO_CLEAR → scan goes to archive without operator review
  MONITOR    → scan appears in the regular queue
  ALERT      → scan is pushed to the front of the queue + audible alert
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import ClassVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import ThresholdConfig
from contracts.v1 import ThreatCategory
from contracts.v1.detection import Detection

log = logging.getLogger("xray.state.thresholds")

_CACHE_TTL_S = 60.0  # seconds before a re-read from the DB


class ThresholdDecision(str, Enum):
    AUTO_CLEAR = "auto_clear"  # below noise floor; no operator review
    MONITOR    = "monitor"     # queue, normal priority
    ALERT      = "alert"       # front of queue, audible alert


@dataclass(frozen=True)
class CategoryThreshold:
    category: str
    alert_threshold: float
    auto_clear_threshold: float

    def decide(self, score: float) -> ThresholdDecision:
        if score >= self.alert_threshold:
            return ThresholdDecision.ALERT
        if score >= self.auto_clear_threshold:
            return ThresholdDecision.MONITOR
        return ThresholdDecision.AUTO_CLEAR


# Conservative built-in defaults — used if DB row is missing.
_DEFAULTS: dict[str, CategoryThreshold] = {
    "narcotics":        CategoryThreshold("narcotics",        0.60, 0.20),
    "firearm":          CategoryThreshold("firearm",          0.55, 0.20),
    "bladed_weapon":    CategoryThreshold("bladed_weapon",    0.55, 0.20),
    "explosive":        CategoryThreshold("explosive",        0.50, 0.15),
    "currency":         CategoryThreshold("currency",         0.65, 0.25),
    "organic_anomaly":  CategoryThreshold("organic_anomaly",  0.70, 0.30),
    "metallic_anomaly": CategoryThreshold("metallic_anomaly", 0.70, 0.30),
    "contraband_other": CategoryThreshold("contraband_other", 0.65, 0.25),
    "unknown":          CategoryThreshold("unknown",          0.80, 0.50),
}


class ThresholdCache:
    """In-memory cache of active threshold rows. Invalidated on admin write."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = session_factory
        self._cache: dict[str, CategoryThreshold] = {}
        self._loaded_at: float = 0.0
        self._lock = asyncio.Lock()

    def invalidate(self) -> None:
        self._loaded_at = 0.0

    async def _load(self) -> None:
        async with self._factory() as session:
            rows = (await session.execute(
                select(ThresholdConfig).where(ThresholdConfig.is_active.is_(True))
            )).scalars().all()

        self._cache = {
            row.category: CategoryThreshold(
                category=row.category,
                alert_threshold=row.alert_threshold,
                auto_clear_threshold=row.auto_clear_threshold,
            )
            for row in rows
        }
        self._loaded_at = time.monotonic()
        log.debug("threshold cache refreshed: %d categories", len(self._cache))

    async def get(self, category: str) -> CategoryThreshold:
        async with self._lock:
            if time.monotonic() - self._loaded_at > _CACHE_TTL_S:
                await self._load()
        return self._cache.get(category) or _DEFAULTS.get(category) or CategoryThreshold(category, 0.80, 0.50)

    async def get_all(self) -> dict[str, CategoryThreshold]:
        async with self._lock:
            if time.monotonic() - self._loaded_at > _CACHE_TTL_S:
                await self._load()
        return dict(self._cache or _DEFAULTS)


@dataclass
class DetectionAlert:
    detection: Detection
    decision: ThresholdDecision
    threshold: CategoryThreshold


async def evaluate_detections(
    detections: list[Detection],
    cache: ThresholdCache,
) -> list[DetectionAlert]:
    """Return one ``DetectionAlert`` per detection, sorted: ALERT first."""
    results: list[DetectionAlert] = []
    for det in detections:
        thr = await cache.get(det.category.value)
        decision = thr.decide(float(det.score))
        results.append(DetectionAlert(detection=det, decision=decision, threshold=thr))

    results.sort(key=lambda a: (
        0 if a.decision == ThresholdDecision.ALERT else
        1 if a.decision == ThresholdDecision.MONITOR else 2
    ))
    return results


__all__ = [
    "ThresholdDecision", "CategoryThreshold",
    "ThresholdCache", "DetectionAlert", "evaluate_detections",
]
