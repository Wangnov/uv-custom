from __future__ import annotations

import json
import mimetypes
import pathlib
import socket
import time
from typing import Callable, Iterable

from uvmirror.metadata import (
    build_state_manifest,
    diff_stale_keys,
    state_manifest_file_sizes,
)

TEXT_LIKE_SUFFIXES = {
    ".json",
    ".ps1",
    ".py",
    ".sha256",
    ".sh",
    ".sum",
    ".toml",
    ".txt",
    ".xml",
    ".yml",
    ".yaml",
}
MAX_SINGLE_PUT_OBJECT_BYTES = 5 * 1024 * 1024 * 1024
RETRYABLE_S3_STATUS_CODES = {403, 408, 409, 429, 500, 502, 503, 504}
RETRYABLE_MULTIPART_STATUS_CODES = {408, 409, 429, 500, 502, 503, 504}
RETRYABLE_TRANSPORT_ERROR_NAMES = {
    "ConnectTimeoutError",
    "ConnectionClosedError",
    "EndpointConnectionError",
    "ReadTimeoutError",
}
DEFAULT_MAX_BACKOFF_SECONDS = 60.0


def _content_type_for_path(path: pathlib.Path) -> str:
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or "application/octet-stream"


def _status_code_from_exception(exc: Exception) -> int | None:
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return None
    metadata = response.get("ResponseMetadata", {})
    status_code = metadata.get("HTTPStatusCode")
    if isinstance(status_code, int):
        return status_code
    return None


def _is_missing_key_error(exc: Exception) -> bool:
    if isinstance(exc, FileNotFoundError):
        return True
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return False
    code = response.get("Error", {}).get("Code")
    return code in {"404", "NoSuchKey", "NotFound"}


def _linked_exceptions(exc: BaseException) -> Iterable[BaseException]:
    seen: set[int] = set()
    pending = [exc]
    while pending:
        current = pending.pop()
        current_id = id(current)
        if current_id in seen:
            continue
        seen.add(current_id)
        yield current
        for attr in ("__cause__", "__context__", "original_error", "reason"):
            nested = getattr(current, attr, None)
            if isinstance(nested, BaseException):
                pending.append(nested)


def _is_retryable_transport_error(exc: Exception) -> bool:
    # botocore and urllib3 use custom timeout/connection exception types.
    for current in _linked_exceptions(exc):
        if isinstance(current, (ConnectionError, TimeoutError, socket.timeout)):
            return True
        if current.__class__.__name__ in RETRYABLE_TRANSPORT_ERROR_NAMES:
            return True
    return False


def _is_retryable_error(exc: Exception) -> bool:
    status_code = _status_code_from_exception(exc)
    return status_code in RETRYABLE_S3_STATUS_CODES or _is_retryable_transport_error(exc)


def _is_retryable_multipart_error(exc: Exception) -> bool:
    status_code = _status_code_from_exception(exc)
    return status_code in RETRYABLE_MULTIPART_STATUS_CODES or _is_retryable_transport_error(exc)


def _should_fallback_to_put_object(exc: Exception, local_path: pathlib.Path) -> bool:
    status_code = _status_code_from_exception(exc)
    if status_code not in {400, 403, 405, 501}:
        return False
    return local_path.stat().st_size <= MAX_SINGLE_PUT_OBJECT_BYTES


class S3MirrorUploader:
    def __init__(
        self,
        client,
        bucket: str,
        multipart_threshold: int = 64 * 1024 * 1024,
        part_size: int = 64 * 1024 * 1024,
        enable_multipart: bool = True,
        max_attempts: int = 8,
        backoff_seconds: float = 1.0,
        max_backoff_seconds: float = DEFAULT_MAX_BACKOFF_SECONDS,
        request_interval: float = 0.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.client = client
        self.bucket = bucket
        self.multipart_threshold = multipart_threshold
        self.part_size = part_size
        self.enable_multipart = enable_multipart
        self.max_attempts = max_attempts
        self.backoff_seconds = backoff_seconds
        self.max_backoff_seconds = max_backoff_seconds
        self.request_interval = request_interval
        self.sleep = sleep

    def upload_file(
        self,
        local_path: pathlib.Path,
        key: str,
        cache_control: str,
    ) -> None:
        if self.enable_multipart and self._should_use_multipart(local_path):
            try:
                self._multipart_upload(local_path, key, cache_control)
                return
            except Exception as exc:  # noqa: BLE001
                if not _should_fallback_to_put_object(exc, local_path):
                    raise
        self._retry(lambda: self._put_object(local_path, key, cache_control))

    def upload_directory(
        self,
        local_dir: pathlib.Path,
        remote_prefix: str,
        cache_control: str,
    ) -> list[str]:
        uploaded_keys: list[str] = []
        for file_path, key, _ in self._iter_local_files(local_dir, remote_prefix):
            self.upload_file(file_path, key, cache_control)
            uploaded_keys.append(key)
        return uploaded_keys

    def load_state_manifest(self, key: str) -> dict[str, list[str]] | None:
        try:
            response = self._retry(
                lambda: self._call("get_object", Bucket=self.bucket, Key=key)
            )
        except Exception as exc:  # noqa: BLE001
            if _is_missing_key_error(exc):
                return None
            raise
        payload = response["Body"].read()
        return json.loads(payload.decode("utf-8"))

    def save_state_manifest(
        self,
        key: str,
        keys: Iterable[str] | Iterable[dict[str, object]],
        cache_control: str = "public, max-age=300",
    ) -> None:
        body = json.dumps(build_state_manifest(keys), indent=2, sort_keys=True).encode("utf-8") + b"\n"
        self._retry(
            lambda: self._call(
                "put_object",
                Bucket=self.bucket,
                Key=key,
                Body=body,
                CacheControl=cache_control,
                ContentType="application/json",
            )
        )

    def delete_keys(self, keys: Iterable[str]) -> None:
        for key in keys:
            self._retry(
                lambda key=key: self._call(
                    "delete_object",
                    Bucket=self.bucket,
                    Key=key,
                )
            )

    def sync_directory_with_state(
        self,
        local_dir: pathlib.Path,
        remote_prefix: str,
        cache_control: str,
        state_key: str,
    ) -> list[str]:
        previous_manifest = self.load_state_manifest(state_key)
        previous_sizes = state_manifest_file_sizes(previous_manifest)
        current_entries: list[dict[str, object]] = []
        current_keys: list[str] = []

        # These prefixes only hold immutable upstream artifacts, so an existing
        # key with the same recorded size can be treated as already synced.
        for file_path, key, size in self._iter_local_files(local_dir, remote_prefix):
            current_entries.append({"key": key, "size": size})
            current_keys.append(key)
            previous_size = previous_sizes.get(key)
            if key in previous_sizes and (previous_size is None or previous_size == size):
                continue
            self.upload_file(file_path, key, cache_control)

        self.save_state_manifest(state_key, current_entries)
        stale_keys = diff_stale_keys(previous_manifest, current_keys)
        self.delete_keys(stale_keys)
        return current_keys

    def _put_object(self, local_path: pathlib.Path, key: str, cache_control: str):
        with local_path.open("rb") as body:
            return self._call(
                "put_object",
                Bucket=self.bucket,
                Key=key,
                Body=body,
                CacheControl=cache_control,
                ContentType=_content_type_for_path(local_path),
            )

    def _multipart_upload(self, local_path: pathlib.Path, key: str, cache_control: str):
        upload = self._retry(
            lambda: self._call(
                "create_multipart_upload",
                Bucket=self.bucket,
                Key=key,
                CacheControl=cache_control,
                ContentType=_content_type_for_path(local_path),
            ),
            is_retryable=_is_retryable_multipart_error,
        )
        upload_id = upload["UploadId"]
        parts: list[dict[str, object]] = []
        try:
            with local_path.open("rb") as handle:
                part_number = 1
                while True:
                    chunk = handle.read(self.part_size)
                    if not chunk:
                        break
                    response = self._retry(
                        lambda chunk=chunk, part_number=part_number: self._call(
                            "upload_part",
                            Bucket=self.bucket,
                            Key=key,
                            UploadId=upload_id,
                            PartNumber=part_number,
                            Body=chunk,
                        ),
                        is_retryable=_is_retryable_multipart_error,
                    )
                    parts.append({"ETag": response["ETag"], "PartNumber": part_number})
                    part_number += 1

            return self._retry(
                lambda: self._call(
                    "complete_multipart_upload",
                    Bucket=self.bucket,
                    Key=key,
                    UploadId=upload_id,
                    MultipartUpload={"Parts": parts},
                ),
                is_retryable=_is_retryable_multipart_error,
            )
        except Exception:  # noqa: BLE001
            try:
                self._call(
                    "abort_multipart_upload",
                    Bucket=self.bucket,
                    Key=key,
                    UploadId=upload_id,
                )
            except Exception:  # noqa: BLE001
                pass
            raise

    def _iter_local_files(
        self,
        local_dir: pathlib.Path,
        remote_prefix: str,
    ) -> Iterable[tuple[pathlib.Path, str, int]]:
        prefix = remote_prefix.rstrip("/")
        for file_path in sorted(path for path in local_dir.rglob("*") if path.is_file()):
            relative = file_path.relative_to(local_dir).as_posix()
            yield file_path, f"{prefix}/{relative}", file_path.stat().st_size

    def _should_use_multipart(self, local_path: pathlib.Path) -> bool:
        content_type = _content_type_for_path(local_path)
        if local_path.suffix in TEXT_LIKE_SUFFIXES:
            return False
        if content_type.startswith("text/"):
            return False
        if content_type in {"application/json", "application/xml"}:
            return False
        return local_path.stat().st_size >= self.multipart_threshold

    def _call(self, method_name: str, **kwargs):
        response = getattr(self.client, method_name)(**kwargs)
        if self.request_interval > 0:
            self.sleep(self.request_interval)
        return response

    def _retry(self, operation, is_retryable: Callable[[Exception], bool] = _is_retryable_error):
        attempt = 0
        while True:
            try:
                return operation()
            except Exception as exc:  # noqa: BLE001
                attempt += 1
                if attempt >= self.max_attempts or not is_retryable(exc):
                    raise
                delay = min(
                    self.backoff_seconds * float(2 ** (attempt - 1)),
                    self.max_backoff_seconds,
                )
                self.sleep(delay)
