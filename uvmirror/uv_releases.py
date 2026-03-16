from __future__ import annotations

from typing import Iterable


def _semver_key(tag: str) -> tuple[int, ...]:
    normalized = tag.lstrip("v")
    return tuple(int(part) for part in normalized.split("."))


def prune_uv_tags(tags: Iterable[str], keep: int) -> list[str]:
    sorted_tags = sorted(tags, key=_semver_key, reverse=True)
    return sorted_tags[keep:]
