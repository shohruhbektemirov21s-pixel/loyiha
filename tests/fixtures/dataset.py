"""Labeled evaluation dataset interface for detector metric tests.

Two implementations:
    MockLabeledDataset   — deterministic synthetic data; runs without GPU or
                           real images; used for CI smoke-testing the metric gate
                           logic itself.

    FileLabeledDataset   — loads real labeled X-ray crops from a directory.
                           Activated when XRAY_TEST_DATASET_PATH is set.
                           Used for nightly / pre-release metric gate runs.

Dataset format on disk (XRAY_TEST_DATASET_PATH/):
    labels.jsonl        — one JSON object per image:
        {
          "image_path": "crops/firearm_001.png",
          "category": "firearm",
          "difficulty": "easy"|"medium"|"hard",
          "has_threat": true
        }
    crops/              — image files
    metadata.json       — dataset version, source, date
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from contracts.v1.common import ThreatCategory


@dataclass(frozen=True)
class LabeledSample:
    """One labeled crop / scan for evaluation."""

    image_path: str | None          # None in mock mode
    category: ThreatCategory | None # None = confirmed negative
    has_threat: bool
    difficulty: str = "medium"      # easy | medium | hard
    metadata: dict = field(default_factory=dict)


class LabeledDataset:
    """Abstract interface. Subclass or use the factory function below."""

    def __iter__(self) -> Iterator[LabeledSample]:
        raise NotImplementedError

    def __len__(self) -> int:
        raise NotImplementedError

    @property
    def version(self) -> str:
        return "unknown"

    def positives(self) -> list[LabeledSample]:
        return [s for s in self if s.has_threat]

    def negatives(self) -> list[LabeledSample]:
        return [s for s in self if not s.has_threat]

    def by_category(self, cat: ThreatCategory) -> list[LabeledSample]:
        return [s for s in self if s.category == cat]

    def hard_samples(self) -> list[LabeledSample]:
        return [s for s in self if s.difficulty == "hard"]


# ---------------------------------------------------------------------------
# Mock dataset — synthetic, no real images
# ---------------------------------------------------------------------------

class MockLabeledDataset(LabeledDataset):
    """Deterministic synthetic dataset.

    Produces a fixed mix of threat categories and negatives so the metric gate
    machinery can be unit-tested without real data. A synthetic detector that
    perfectly recalls all synthetic positives will pass; one that drops a category
    will fail the per-category gate.
    """

    SAMPLES_PER_CATEGORY = 20   # positives per category
    NEGATIVES            = 80   # confirmed-negative samples

    def __init__(self) -> None:
        self._samples = self._build()

    @staticmethod
    def _build() -> list[LabeledSample]:
        samples: list[LabeledSample] = []
        difficulties = ["easy", "easy", "medium", "medium", "hard"]
        for cat in ThreatCategory:
            if cat == ThreatCategory.UNKNOWN:
                continue
            for i in range(MockLabeledDataset.SAMPLES_PER_CATEGORY):
                diff = difficulties[i % len(difficulties)]
                samples.append(LabeledSample(
                    image_path=None,
                    category=cat,
                    has_threat=True,
                    difficulty=diff,
                    metadata={"synthetic": True, "index": i},
                ))
        for i in range(MockLabeledDataset.NEGATIVES):
            samples.append(LabeledSample(
                image_path=None,
                category=None,
                has_threat=False,
                difficulty="easy",
                metadata={"synthetic": True, "index": i},
            ))
        return samples

    def __iter__(self) -> Iterator[LabeledSample]:
        return iter(self._samples)

    def __len__(self) -> int:
        return len(self._samples)

    @property
    def version(self) -> str:
        return "mock-v1"


# ---------------------------------------------------------------------------
# File-backed dataset (real labeled data)
# ---------------------------------------------------------------------------

class FileLabeledDataset(LabeledDataset):
    """Loads from XRAY_TEST_DATASET_PATH."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)
        self._samples = self._load()
        meta_path = self._root / "metadata.json"
        self._version = (
            json.loads(meta_path.read_text())["version"]
            if meta_path.exists()
            else "unknown"
        )

    def _load(self) -> list[LabeledSample]:
        labels_path = self._root / "labels.jsonl"
        if not labels_path.exists():
            raise FileNotFoundError(f"labels.jsonl not found in {self._root}")
        samples = []
        for line in labels_path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            cat_str = obj.get("category")
            cat = ThreatCategory(cat_str) if cat_str else None
            samples.append(LabeledSample(
                image_path=str(self._root / obj["image_path"]),
                category=cat,
                has_threat=obj.get("has_threat", cat is not None),
                difficulty=obj.get("difficulty", "medium"),
                metadata={k: v for k, v in obj.items()
                           if k not in ("image_path", "category", "has_threat", "difficulty")},
            ))
        return samples

    def __iter__(self) -> Iterator[LabeledSample]:
        return iter(self._samples)

    def __len__(self) -> int:
        return len(self._samples)

    @property
    def version(self) -> str:
        return self._version


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def get_evaluation_dataset() -> LabeledDataset:
    """Return the appropriate dataset based on environment."""
    path = os.environ.get("XRAY_TEST_DATASET_PATH", "")
    if path:
        return FileLabeledDataset(path)
    return MockLabeledDataset()


REAL_DATASET_AVAILABLE = bool(os.environ.get("XRAY_TEST_DATASET_PATH", ""))
