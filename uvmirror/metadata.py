from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable
from urllib.parse import unquote


PYTHON_BUILD_STANDALONE_PREFIX = (
    "https://github.com/astral-sh/python-build-standalone/releases/download/"
)
PYPY_PREFIX = "https://downloads.python.org/pypy/"
GRAALPY_PREFIX = "https://github.com/oracle/graalpython/releases/download/"


def _version_key(raw: str) -> tuple[object, ...]:
    parts = []
    for piece in raw.replace("-", ".").split("."):
        if piece.isdigit():
            parts.append((0, int(piece)))
        else:
            parts.append((1, piece))
    return tuple(parts)


def keep_latest_runtime_builds(entries: Iterable[dict]) -> list[dict]:
    latest_by_name: dict[str, str] = {}
    selected: list[dict] = []

    materialized = list(entries)
    for entry in materialized:
        name = entry["name"]
        build = entry["build"]
        if name not in latest_by_name or _version_key(build) > _version_key(latest_by_name[name]):
            latest_by_name[name] = build

    for entry in materialized:
        if latest_by_name.get(entry["name"]) == entry["build"]:
            selected.append(entry)

    return selected


def build_state_manifest(
    keys: Iterable[str] | Iterable[dict[str, object]],
) -> dict[str, object]:
    manifest_keys: set[str] = set()
    manifest_files: dict[str, int] = {}

    for item in keys:
        if isinstance(item, str):
            manifest_keys.add(item)
            continue

        key = item.get("key")
        size = item.get("size")
        if not isinstance(key, str):
            raise TypeError(f"state manifest entry is missing a string key: {item!r}")
        if not isinstance(size, int):
            raise TypeError(f"state manifest entry is missing an integer size: {item!r}")
        manifest_keys.add(key)
        manifest_files[key] = size

    payload: dict[str, object] = {"keys": sorted(manifest_keys)}
    if manifest_files:
        payload["files"] = [
            {"key": key, "size": size}
            for key, size in sorted(manifest_files.items())
        ]
    return payload


def diff_stale_keys(
    previous_manifest: dict[str, list[str]] | None,
    current_keys: Iterable[str],
) -> list[str]:
    previous_keys = set((previous_manifest or {}).get("keys", []))
    current_key_set = set(current_keys)
    return sorted(previous_keys - current_key_set)


def state_manifest_file_sizes(
    manifest: dict[str, object] | None,
) -> dict[str, int | None]:
    if manifest is None:
        return {}

    files = manifest.get("files")
    if isinstance(files, list):
        sizes: dict[str, int | None] = {}
        for entry in files:
            if not isinstance(entry, dict):
                continue
            key = entry.get("key")
            size = entry.get("size")
            if not isinstance(key, str):
                continue
            sizes[key] = size if isinstance(size, int) else None
        if sizes:
            return sizes

    keys = manifest.get("keys", [])
    if not isinstance(keys, list):
        return {}
    return {key: None for key in keys if isinstance(key, str)}


def mirror_path_for_python_download_url(url: str) -> str:
    def sanitize(path: str) -> str:
        return unquote(path).replace("%", "-pct-").replace("+", "-plus-")

    if url.startswith(PYTHON_BUILD_STANDALONE_PREFIX):
        return (
            "python-build-standalone/releases/download/"
            f"{sanitize(url[len(PYTHON_BUILD_STANDALONE_PREFIX):])}"
        )
    if url.startswith(PYPY_PREFIX):
        return f"pypy/{sanitize(url[len(PYPY_PREFIX):])}"
    if url.startswith(GRAALPY_PREFIX):
        return f"graalpython/releases/download/{sanitize(url[len(GRAALPY_PREFIX):])}"
    raise ValueError(f"unsupported python download url: {url}")


def rewrite_python_download_url(url: str, public_base_url: str) -> str:
    base = public_base_url.rstrip("/")
    return f"{base}/{mirror_path_for_python_download_url(url)}"


@dataclass(frozen=True)
class RewrittenEntry:
    key: str
    payload: dict


def build_rewritten_python_metadata(
    raw_metadata: dict[str, dict],
    public_base_url: str,
) -> dict[str, dict]:
    rewritten: dict[str, dict] = {}
    for key, entry in raw_metadata.items():
        rewritten_entry = dict(entry)
        rewritten_entry["url"] = rewrite_python_download_url(
            rewritten_entry["url"], public_base_url
        )
        rewritten[key] = rewritten_entry
    return rewritten


def build_python_asset_manifest(entries: Iterable[dict]) -> list[dict]:
    manifest: list[dict] = []
    seen_paths: set[str] = set()
    for entry in entries:
        mirror_path = mirror_path_for_python_download_url(entry["url"])
        if mirror_path in seen_paths:
            continue
        seen_paths.add(mirror_path)
        manifest.append(
            {
                "source_url": entry["url"],
                "mirror_path": mirror_path,
                "sha256": entry.get("sha256"),
            }
        )
    return manifest
