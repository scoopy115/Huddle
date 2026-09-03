"""Voice-profile arithmetic: cosine similarity, running means, and matching a cluster's
embedding against the known speakers. Pure Python; vectors are plain lists of floats so they
round-trip through SQLite as JSON."""
from __future__ import annotations

import math


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def running_mean(old: list[float], n: int, new: list[float]) -> list[float]:
    """Fold one more sample into a mean of `n` samples."""
    if not old:
        return list(new)
    if not new or len(new) != len(old):
        return old
    return [(o * n + x) / (n + 1) for o, x in zip(old, new)]


def match(embedding: list[float], profiles: list[dict], threshold: float = 0.75) -> tuple[str, float] | None:
    """Best profile (name, similarity) above `threshold`, else None. `profiles` items carry
    `name` and `embedding`."""
    best_name, best = None, -1.0
    for p in profiles:
        s = cosine(embedding, p.get("embedding") or [])
        if s > best:
            best, best_name = s, p.get("name")
    return (best_name, best) if best_name is not None and best >= threshold else None
