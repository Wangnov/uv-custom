#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import pathlib
import shlex
import shutil
import subprocess
import tempfile
from typing import Sequence

DEFAULT_PUBLIC_BASE_URL = "https://uv.agentsmirror.com"
DEFAULT_PYTHON_VERSION = "3.12"
EXPECTED_VERSIONS = {
    "orjson": "3.11.7",
    "pillow": "12.1.1",
    "numpy": "2.4.3",
    "torch": "2.11.0",
}

# Keep the chain split so we exercise repeated `uv add` updates, including a
# heavyweight wheel download for `torch`.
DEFAULT_ADD_STEPS: tuple[tuple[str, ...], ...] = (
    (
        f"pillow=={EXPECTED_VERSIONS['pillow']}",
        f"orjson=={EXPECTED_VERSIONS['orjson']}",
    ),
    (f"torch=={EXPECTED_VERSIONS['torch']}",),
    (f"numpy=={EXPECTED_VERSIONS['numpy']}",),
)

SMOKE_PROBE = """
import numpy as np
import orjson
import PIL
import torch

tensor = torch.tensor([1.0, 2.0])
array = tensor.numpy()
versions = {
    "orjson": orjson.__version__,
    "pillow": PIL.__version__,
    "numpy": np.__version__,
    "torch": torch.__version__.split("+")[0],
}
expected = {
    "orjson": "3.11.7",
    "pillow": "12.1.1",
    "numpy": "2.4.3",
    "torch": "2.11.0",
}
assert versions == expected, (versions, expected)
print(
    {
        **versions,
        "numpy_sum": float(array.sum()),
        "mps_built": torch.backends.mps.is_built(),
    }
)
""".strip()


def _command_string(args: Sequence[str]) -> str:
    return " ".join(shlex.quote(arg) for arg in args)


def _run_command(
    args: Sequence[str],
    *,
    cwd: pathlib.Path,
    env: dict[str, str],
) -> None:
    print(f"$ {_command_string(args)}", flush=True)
    subprocess.run(
        list(args),
        check=True,
        cwd=cwd,
        env=env,
    )


def _default_index_url(public_base_url: str) -> str:
    return f"{public_base_url.rstrip('/')}/pypi/simple"


def _python_downloads_json_url(public_base_url: str) -> str:
    return f"{public_base_url.rstrip('/')}/metadata/python-downloads.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--uv-bin", default="uv")
    parser.add_argument("--public-base-url", default=DEFAULT_PUBLIC_BASE_URL)
    parser.add_argument("--default-index-url")
    parser.add_argument("--python-downloads-json-url")
    parser.add_argument("--python", default=DEFAULT_PYTHON_VERSION)
    parser.add_argument("--project-dir", type=pathlib.Path)
    parser.add_argument("--cache-dir", type=pathlib.Path)
    parser.add_argument("--keep-project", action="store_true")
    parser.add_argument("--skip-python-install", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if shutil.which(args.uv_bin) is None:
        raise SystemExit(f"uv executable not found: {args.uv_bin}")

    public_base_url = args.public_base_url.rstrip("/")
    default_index_url = args.default_index_url or _default_index_url(public_base_url)
    python_downloads_json_url = (
        args.python_downloads_json_url
        or _python_downloads_json_url(public_base_url)
    )

    auto_project_dir = args.project_dir is None
    project_dir = (
        pathlib.Path(tempfile.mkdtemp(prefix="uv-smoke."))
        if auto_project_dir
        else args.project_dir
    )
    assert project_dir is not None

    if not auto_project_dir:
        if project_dir.exists() and any(project_dir.iterdir()):
            raise SystemExit(f"project directory must be empty: {project_dir}")
        project_dir.mkdir(parents=True, exist_ok=True)

    cache_dir = args.cache_dir or (project_dir / ".uv-cache")

    env = os.environ.copy()
    env["UV_DEFAULT_INDEX"] = default_index_url
    env["UV_PYTHON_DOWNLOADS_JSON_URL"] = python_downloads_json_url

    keep_project = args.keep_project or not auto_project_dir
    succeeded = False

    try:
        print(f"Smoke project: {project_dir}", flush=True)
        print(f"Default index: {default_index_url}", flush=True)
        print(f"Python downloads JSON: {python_downloads_json_url}", flush=True)
        print(f"Python version: {args.python}", flush=True)

        _run_command(
            [
                args.uv_bin,
                "init",
                "--bare",
                "--no-workspace",
                "--python",
                args.python,
                str(project_dir),
                "--color",
                "never",
                "--no-progress",
            ],
            cwd=project_dir,
            env=env,
        )

        if not args.skip_python_install:
            _run_command(
                [
                    args.uv_bin,
                    "python",
                    "install",
                    args.python,
                    "--color",
                    "never",
                    "--no-progress",
                ],
                cwd=project_dir,
                env=env,
            )

        for packages in DEFAULT_ADD_STEPS:
            _run_command(
                [
                    args.uv_bin,
                    "add",
                    *packages,
                    "--python",
                    args.python,
                    "--cache-dir",
                    str(cache_dir),
                    "--color",
                    "never",
                    "--no-progress",
                ],
                cwd=project_dir,
                env=env,
            )

        _run_command(
            [
                args.uv_bin,
                "sync",
                "--reinstall",
                "--python",
                args.python,
                "--cache-dir",
                str(cache_dir),
                "--color",
                "never",
                "--no-progress",
            ],
            cwd=project_dir,
            env=env,
        )

        _run_command(
            [
                args.uv_bin,
                "run",
                "--python",
                args.python,
                "--color",
                "never",
                "--no-progress",
                "python",
                "-c",
                SMOKE_PROBE,
            ],
            cwd=project_dir,
            env=env,
        )

        _run_command(
            [
                args.uv_bin,
                "tree",
                "--python",
                args.python,
                "--color",
                "never",
                "--no-progress",
            ],
            cwd=project_dir,
            env=env,
        )

        succeeded = True
        print("Smoke validation completed successfully.", flush=True)
    finally:
        if keep_project or not succeeded:
            print(f"Smoke project preserved at: {project_dir}", flush=True)
        else:
            shutil.rmtree(project_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
