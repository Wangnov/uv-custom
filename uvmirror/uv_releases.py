from __future__ import annotations

import re
from typing import Iterable

UPSTREAM_UV_DOWNLOAD_PREFIX = "https://github.com/astral-sh/uv/releases/download/"
UPSTREAM_GITHUB_API_PREFIX = "https://api.github.com/"
_SAFE_TAG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$")


def _semver_key(tag: str) -> tuple[int, ...]:
    normalized = tag.lstrip("v")
    return tuple(int(part) for part in normalized.split("."))


def prune_uv_tags(tags: Iterable[str], keep: int) -> list[str]:
    sorted_tags = sorted(tags, key=_semver_key, reverse=True)
    return sorted_tags[keep:]


def _rewrite_download_url(url: str, public_base_url: str) -> str:
    if url.startswith(UPSTREAM_UV_DOWNLOAD_PREFIX):
        suffix = url[len(UPSTREAM_UV_DOWNLOAD_PREFIX) :]
        return f"{public_base_url.rstrip('/')}/github/astral-sh/uv/releases/download/{suffix}"
    return url


def _rewrite_api_url(url: str, public_base_url: str) -> str:
    if url.startswith(UPSTREAM_GITHUB_API_PREFIX):
        suffix = url[len(UPSTREAM_GITHUB_API_PREFIX) :]
        return f"{public_base_url.rstrip('/')}/api/v3/{suffix}"
    return url


def rewrite_uv_release_payload(
    release: dict[str, object],
    public_base_url: str,
    *,
    include_body: bool = True,
) -> dict[str, object]:
    """Rewrite a GitHub release object so asset and API URLs target the mirror."""
    base = public_base_url.rstrip("/")
    rewritten = {
        key: value
        for key, value in release.items()
        if key not in {"author", "reactions"} and (include_body or key != "body")
    }
    for api_field in ("url", "assets_url", "upload_url", "tarball_url", "zipball_url"):
        value = rewritten.get(api_field)
        if isinstance(value, str):
            rewritten[api_field] = _rewrite_api_url(value, base)

    assets = release.get("assets")
    if isinstance(assets, list):
        rewritten_assets: list[dict[str, object]] = []
        for asset in assets:
            if not isinstance(asset, dict):
                continue
            cleaned_asset = {k: v for k, v in asset.items() if k != "uploader"}
            download_url = cleaned_asset.get("browser_download_url")
            if isinstance(download_url, str):
                cleaned_asset["browser_download_url"] = _rewrite_download_url(
                    download_url, base
                )
            asset_api_url = cleaned_asset.get("url")
            if isinstance(asset_api_url, str):
                cleaned_asset["url"] = _rewrite_api_url(asset_api_url, base)
            rewritten_assets.append(cleaned_asset)
        rewritten["assets"] = rewritten_assets

    return rewritten


def build_uv_releases_api_bundle(
    releases_list: list[dict[str, object]],
    latest_release: dict[str, object],
    public_base_url: str,
) -> dict[str, object]:
    """Build mirror GitHub Enterprise v3 API JSON payloads keyed by S3 object path."""
    base = public_base_url.rstrip("/")
    rewritten_latest = rewrite_uv_release_payload(
        latest_release, base, include_body=True
    )
    rewritten_list = [
        rewrite_uv_release_payload(entry, base, include_body=False)
        for entry in releases_list
        if isinstance(entry, dict)
    ]

    bundle: dict[str, object] = {
        "github/api/v3/repos/astral-sh/uv/releases.json": rewritten_list,
        "github/api/v3/repos/astral-sh/uv/releases/latest.json": rewritten_latest,
    }
    tag_name = rewritten_latest.get("tag_name")
    if (
        isinstance(tag_name, str)
        and ".." not in tag_name
        and _SAFE_TAG_RE.match(tag_name)
    ):
        bundle[f"github/api/v3/repos/astral-sh/uv/releases/tags/{tag_name}.json"] = (
            rewritten_latest
        )
    return bundle
