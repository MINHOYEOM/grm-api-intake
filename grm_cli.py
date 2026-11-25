#!/usr/bin/env python3
"""Shared deterministic helpers for GRM command-line and PostgREST tools.

This module provides local helpers and coordinates bulk writes through
caller-provided callbacks. It does not open network or database connections.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar


BULK_BATCH_MAX = 500
BULK_RUN_MAX = 2_000
_BULK_PAUSE_SECONDS = 1.0
_BULK_FAILURE_LIMIT = 2
_Row = TypeVar("_Row")


class BulkWriteError(RuntimeError):
    """The run exhausted its row budget or reached the failure limit."""


class BulkWriteRun:
    """Share one budget across related writes in a command invocation.

    A callback sends a single batch and returns False on a failed batch
    that the caller wants to record and continue past. Exceptions stop
    immediately. The budget counts attempted rows, including failed batches.
    """

    def __init__(self) -> None:
        self.attempted = 0
        self._consecutive_failures = 0

    @property
    def remaining(self) -> int:
        return BULK_RUN_MAX - self.attempted

    def require_capacity(self, count: int) -> None:
        if count > self.remaining:
            raise BulkWriteError(
                f"bulk write needs {count} rows, only {self.remaining} remain in this run"
            )

    def write(
        self,
        rows: list[_Row],
        send: Callable[[list[_Row], int], bool | None],
        *,
        batch_size: int = BULK_BATCH_MAX,
    ) -> None:
        if batch_size < 1:
            raise ValueError("bulk write batch_size must be positive")
        if self._consecutive_failures >= _BULK_FAILURE_LIMIT:
            raise BulkWriteError("bulk write stopped after consecutive batch failures")
        self.require_capacity(len(rows))
        size = min(batch_size, BULK_BATCH_MAX)
        for offset in range(0, len(rows), size):
            batch = rows[offset:offset + size]
            if self.attempted:
                time.sleep(_BULK_PAUSE_SECONDS)
            self.attempted += len(batch)
            try:
                sent = send(batch, offset)
            except Exception:
                self._consecutive_failures = _BULK_FAILURE_LIMIT
                raise
            if sent is False:
                self._consecutive_failures += 1
                if self._consecutive_failures >= _BULK_FAILURE_LIMIT:
                    raise BulkWriteError("bulk write stopped after consecutive batch failures")
            else:
                self._consecutive_failures = 0


def load_json_object(path: str | Path) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: JSON root must be an object")
    return data


def write_json(path: str | Path, data: dict[str, Any], *, pretty: bool) -> None:
    text = json.dumps(
        data,
        ensure_ascii=False,
        sort_keys=True,
        indent=2 if pretty else None,
        separators=None if pretty else (",", ":"),
    )
    Path(path).write_text(text + "\n", encoding="utf-8")


def parse_input_spec(spec: str) -> tuple[str, Path]:
    if "=" in spec:
        name, path = spec.split("=", 1)
        return name.strip() or Path(path).stem, Path(path)
    path = Path(spec)
    return path.stem, path


def normalize_supabase_url(base_url: str) -> str | None:
    text = str(base_url or "").strip()
    if not text.lower().startswith("https://"):
        return None
    return text.rstrip("/")


def header_ci(headers: dict[str, Any], name: str) -> str:
    """Return a header value from a plain or case-insensitive mapping."""
    name_lower = name.lower()
    for key, value in headers.items():
        if str(key).lower() == name_lower:
            return str(value)
    return ""


def parse_content_range(value: str) -> int | None:
    """Return the total from a PostgREST Content-Range value when exact."""
    if "/" not in value:
        return None
    total_part = value.rsplit("/", 1)[-1]
    if not total_part.isdigit():
        return None
    return int(total_part)


def resolve_supabase_service_credentials(
    args: argparse.Namespace,
) -> tuple[str, str] | None:
    url = (args.supabase_url or os.environ.get("SUPABASE_URL") or "").strip()
    key = (args.service_role_key or os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    if not url or not key:
        return None
    return url, key
