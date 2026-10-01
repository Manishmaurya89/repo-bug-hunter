"""SWE-bench Verified tasks and a reproducible subset of them."""

from __future__ import annotations

import random
import re

DATASET = "SWE-bench/SWE-bench_Verified"


def load_tasks(dataset: str = DATASET) -> list[dict]:
    from datasets import load_dataset
    return [dict(row) for row in load_dataset(dataset, split="test")]


def select(tasks: list[dict], n: int, seed: int = 0) -> list[dict]:
    """The same n and seed always give the same tasks, so two variants run on identical sets."""
    ordered = sorted(tasks, key=lambda t: t["instance_id"])
    picked = random.Random(seed).sample(ordered, min(n, len(ordered)))
    return sorted(picked, key=lambda t: t["instance_id"])


def patch_files(patch: str) -> list[str]:
    return re.findall(r"^diff --git a/(\S+) b/", patch or "", re.M)
