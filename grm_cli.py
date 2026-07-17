#!/usr/bin/env python3
"""Shared deterministic helpers for GRM command-line and PostgREST tools.

This module only normalizes inputs and reads/writes local JSON. It has no
network or database side effects, so service modules can share byte-identical
boundary behavior without importing one another.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any


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
