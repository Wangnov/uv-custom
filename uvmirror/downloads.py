from __future__ import annotations

import hashlib
import json
import pathlib
import shutil
import socket
import time
import urllib.error
import urllib.request
from typing import Callable

RETRYABLE_HTTP_STATUS_CODES = {408, 409, 425, 429, 500, 502, 503, 504}
DEFAULT_DOWNLOAD_TIMEOUT_SECONDS = 300
DEFAULT_MAX_BACKOFF_SECONDS = 60.0

DownloadCallable = Callable[[str, pathlib.Path], None]
LogCallable = Callable[[str], None]
SleepCallable = Callable[[float], None]


def _sha256_for_path(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download_url_to_path(
    url: str,
    destination: pathlib.Path,
    timeout_seconds: float = DEFAULT_DOWNLOAD_TIMEOUT_SECONDS,
) -> None:
    temp_path = destination.with_name(f"{destination.name}.part")
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "agentsmirror-uv-sync/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            with temp_path.open("wb") as handle:
                shutil.copyfileobj(response, handle)
        temp_path.replace(destination)
    except Exception:  # noqa: BLE001
        if temp_path.exists():
            temp_path.unlink()
        raise


def _is_retryable_download_error(exc: Exception) -> bool:
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in RETRYABLE_HTTP_STATUS_CODES
    if isinstance(exc, urllib.error.URLError):
        return True
    return isinstance(
        exc,
        (
            ConnectionError,
            TimeoutError,
            socket.timeout,
        ),
    )


def download_python_assets(
    manifest_path: pathlib.Path,
    stage_dir: pathlib.Path,
    max_attempts: int,
    backoff_seconds: float,
    request_interval: float,
    sleep: SleepCallable = time.sleep,
    downloader: DownloadCallable = _download_url_to_path,
    logger: LogCallable | None = None,
    max_backoff_seconds: float = DEFAULT_MAX_BACKOFF_SECONDS,
) -> None:
    assets = json.loads(manifest_path.read_text(encoding="utf-8"))
    total = len(assets)
    stage_dir.mkdir(parents=True, exist_ok=True)

    for index, asset in enumerate(assets, start=1):
        source_url = asset["source_url"]
        mirror_path = asset["mirror_path"]
        destination = stage_dir / mirror_path
        destination.parent.mkdir(parents=True, exist_ok=True)

        if logger is not None:
            logger(f"[{index}/{total}] downloading {source_url}")

        attempt = 0
        while True:
            try:
                downloader(source_url, destination)
                expected_sha256 = asset.get("sha256")
                if expected_sha256:
                    digest = _sha256_for_path(destination)
                    if digest != expected_sha256:
                        raise SystemExit(
                            f"sha256 mismatch for {source_url}: {digest} != {expected_sha256}"
                        )
                break
            except Exception as exc:  # noqa: BLE001
                attempt += 1
                if attempt >= max_attempts or not _is_retryable_download_error(exc):
                    raise SystemExit(
                        f"failed to download {source_url} to {mirror_path} after {attempt} attempts: {exc}"
                    ) from exc
                delay = min(
                    backoff_seconds * float(2 ** (attempt - 1)),
                    max_backoff_seconds,
                )
                if logger is not None:
                    logger(
                        f"retrying {source_url} after attempt {attempt}/{max_attempts} "
                        f"in {delay:.1f}s: {exc}"
                    )
                sleep(delay)

        if request_interval > 0 and index < total:
            sleep(request_interval)
