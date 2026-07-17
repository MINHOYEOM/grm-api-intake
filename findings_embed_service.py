#!/usr/bin/env python3
"""[FIND-1 S2] unattended CI service that computes item-to-item embeddings for
public findings and upserts them into ``public.finding_embeddings``
(019_findings_embeddings.sql).

This module follows the same split this repo already uses for every other
unattended findings job (findings_translate_apply_service.py, findings_
reclassify_service.py, findings_supabase_backfill.py): "AI judgment" and "DB
write" are separate concerns. Here there is no LLM at all -- embedding
computation is a deterministic function of (model, model revision, input
text), so the whole script is a pure transport + composition layer around a
local sentence-transformers model. It never calls any LLM and never touches
``public.embedding_config`` (the active-version switch) -- 019's comment
documents a 4-step cutover (load new version -> verify -> atomic switch ->
delete old version) that only a human/control-tower performs deliberately;
this script only ever performs step 1 (load).

Target selection mirrors the exact public-gate predicate 006/010/018/019 all
share -- (finding_text_ko <> '' or finding_language = 'KO') and scope_status =
'ok' -- because the service-role key bypasses RLS. Embedding a row the gate
would reject produces a dead vector findings_similar_by_id can never serve
(019's active_version join already filters by embedding_version, but nothing
stops a stray non-public vector from sitting in the table otherwise).

embed_input:
  'A' -- finding_text verbatim.
  'B' -- for FDA 483 rows only, reconstruct deficiency + "\\n" + detail from
    the source raw_signals.raw_json's fda_483_observations[] (see
    resolve_b_text / build_embed_text). Falls back to 'A' whenever the
    reverse-lookup is ambiguous (>=2 observations share the same cleaned
    deficiency text), unmatched, or the matched observation has no detail --
    never guesses. Non-483 sources have no fda_483_observations and so
    always fall back to 'A' automatically.

E5 prefix contract (unbreakable, fixed together with 019's comment): both the
base finding and every candidate in findings_similar_by_id are embedded with
the *same* symmetric "query: " prefix (E5 model card guidance for item-to-
item similarity -- not the asymmetric passage-prefix meant for retrieval).
Breaking this means the corpus is no longer in one vector space.

text_sha256 is computed over the input text *before* the "query: " prefix is
added (019's comment) -- it exists purely to detect when the underlying text
(source edit, or a change from embed_input A<->B) has drifted since the last
embedding, so a re-run only re-embeds what actually changed.

The service-role key is never included in any log line, exception message, or
report field -- only exception type names and HTTP status codes are
surfaced, mirroring findings_supabase_append.py's convention.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import requests

import findings_supabase_backfill as fsb
import grm_findings as gf


DEFAULT_TIMEOUT_SECONDS = fsb.DEFAULT_TIMEOUT_SECONDS
_UPSERT_TIMEOUT_SECONDS = 30
_MAX_ATTEMPTS = 2  # initial try + 1 retry, for 5xx/timeout only
_DEFAULT_PAGE_SIZE = 1000
_RAW_SIGNAL_ID_CHUNK = 150  # keeps `in.(...)` query strings well under URL limits
_DB_UPSERT_BATCH_SIZE = 200  # rows per POST -- keeps JSON bodies to a few MB

MODEL_NAME = "intfloat/multilingual-e5-small"
EMBED_DIM = 384
FDA_483_SOURCE = "FDA 483"

# ★E5 prefix 계약(불가침) -- 아이템-투-아이템은 대칭 유사도이므로 양쪽 모두 이 prefix.
# 019 주석·findings_similar_by_id 와 함께 고정한다. 비대칭 검색용 passage-prefix 아님.
E5_QUERY_PREFIX = "query: "

_FINDING_SELECT = (
    "finding_id,finding_text,finding_text_ko,finding_language,raw_signal_id,source,scope_status"
)


# ---------------------------------------------------------------------------
# Public-gate target selection (pure)
# ---------------------------------------------------------------------------


def is_public_gate_row(row: dict[str, Any]) -> bool:
    """006/010/018/019 공개 술어 복제 -- (번역 있음 OR 원문이 한국어) AND scope_status='ok'.

    Pure function so the predicate is independently testable without a live
    Supabase connection (mirrors 018/019's own migration-file assertions).
    """
    finding_text_ko = str(row.get("finding_text_ko") or "")
    finding_language = str(row.get("finding_language") or "")
    scope_status = str(row.get("scope_status") or "")
    return (finding_text_ko != "" or finding_language == "KO") and scope_status == "ok"


def select_public_findings(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Public-gate rows only, deterministic order (finding_id asc)."""
    kept = [row for row in rows if is_public_gate_row(row)]
    kept.sort(key=lambda row: str(row.get("finding_id") or ""))
    return kept


# ---------------------------------------------------------------------------
# embed_input B: raw_signal reverse-lookup reconstruction (pure)
# ---------------------------------------------------------------------------


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def _observation_header_hints(raw_signal_row: dict[str, Any], raw: dict[str, Any]) -> dict[str, str]:
    """Same header_hints construction as findings_extractors._from_fda_483_observations
    (establishment_type/fei_number from raw_json, firm_name from the raw_signals row with
    a raw_json fallback) -- must match exactly, or the cleaned deficiency text this module
    recomputes will not byte-match the finding_text the extractor originally produced."""
    return {
        "establishment_type": _clean(raw.get("establishment_type")),
        "fei_number": _clean(raw.get("fei_number")),
        "firm_name": _clean(raw_signal_row.get("firm_name")) or _clean(raw.get("firm")),
    }


def _observation_field(observation: dict[str, Any], field: str, header_hints: dict[str, str]) -> str:
    return _clean(gf.strip_fda483_page_header(_clean(observation.get(field)), **header_hints))


def resolve_b_text(
    finding_text: str,
    raw: dict[str, Any],
    raw_signal_row: dict[str, Any],
) -> str | None:
    """Reverse-map one FDA 483 finding_text back to its source observation and
    reconstruct "deficiency\\ndetail". Pure function -- no I/O.

    Returns None (caller falls back to embed_input A) when: no
    fda_483_observations array present (non-483 sources, or malformed raw_json);
    zero or >=2 observations' cleaned deficiency text equals finding_text
    (ambiguous -- never guess which one); or the single match has no detail.

    ★live-measured basis (2026-07-15): exact single-match rate 99.6%, 0
    unmatched, 0.4% ambiguous (2 observations sharing one deficiency), and
    91.8% of matches carry a detail (mean 768 chars vs. 147 for deficiency
    alone).
    """
    observations = raw.get("fda_483_observations")
    if not isinstance(observations, list):
        return None

    header_hints = _observation_header_hints(raw_signal_row, raw)
    matches = [
        obs for obs in observations
        if isinstance(obs, dict)
        and _observation_field(obs, "deficiency", header_hints) == finding_text
    ]
    if len(matches) != 1:
        return None

    detail = _observation_field(matches[0], "detail", header_hints)
    if not detail:
        return None

    deficiency = _observation_field(matches[0], "deficiency", header_hints)
    return f"{deficiency}\n{detail}"


def build_embed_text(
    finding: dict[str, Any],
    embed_input: str,
    raw_signals_by_id: dict[str, dict[str, Any]],
) -> tuple[str, str]:
    """Return (text, actual_mode) where actual_mode is the embed_input value
    that was *actually* used for this row ('A' or 'B') -- embed_input='B' may
    still fall back to 'A' per-row (resolve_b_text contract), so the stored
    finding_embeddings.embed_input reflects what really went into the vector,
    not just the CLI flag.
    """
    finding_text = str(finding.get("finding_text") or "")
    if embed_input != "B":
        return finding_text, "A"

    if str(finding.get("source") or "") != FDA_483_SOURCE:
        return finding_text, "A"  # only 483 raw_json carries fda_483_observations

    raw_signal_row = raw_signals_by_id.get(str(finding.get("raw_signal_id") or ""))
    if raw_signal_row is None:
        return finding_text, "A"

    raw = _json_object(raw_signal_row.get("raw_json"))
    if not raw:
        return finding_text, "A"

    b_text = resolve_b_text(finding_text, raw, raw_signal_row)
    if b_text is None:
        return finding_text, "A"
    return b_text, "B"


# ---------------------------------------------------------------------------
# Sanity checks (pure, no numpy -- keeps this module importable/testable
# without any embedding dependency installed)
# ---------------------------------------------------------------------------


def sanity_check_embeddings(vectors: list[list[float]]) -> list[str]:
    """019 §전환순서 ② -- upsert 전 ⓐ차원 정확히 384 ⓑNaN/Inf 없음 ⓒ벡터 노름 0 아님.
    Returns a list of violation descriptions; empty = all clear. Any violation
    must abort the *entire* run (no partial upsert) -- see run_embed.
    """
    violations: list[str] = []
    for index, vector in enumerate(vectors):
        if len(vector) != EMBED_DIM:
            violations.append(f"row {index}: dimension {len(vector)} != {EMBED_DIM}")
            continue
        if any(not math.isfinite(value) for value in vector):
            violations.append(f"row {index}: contains NaN/Inf")
            continue
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0.0:
            violations.append(f"row {index}: zero-norm vector")
    return violations


def _vector_literal(vector: list[float]) -> str:
    """halfvec text literal for PostgREST -- pgvector/halfvec's input function
    parses "[v1,v2,...]"; PostgREST accepts this as a JSON *string* value for a
    column whose Postgres type is not itself json/jsonb (the same convention
    Supabase's own pgvector examples use). Not live-verified in this session
    (no live DB writes permitted here) -- control-tower dry-run/first-apply
    should confirm the upsert 2xx's before relying on this in production."""
    return "[" + ",".join(repr(float(value)) for value in vector) + "]"


# ---------------------------------------------------------------------------
# Model loading (lazy -- sentence-transformers/torch are NOT repo-wide deps,
# see requirements-embed.txt; importing them at module load time would break
# every other script/test that imports this module transitively)
# ---------------------------------------------------------------------------


def resolve_model_revision(model_name: str) -> str:
    """Resolve the exact HF Hub commit sha for model_name. Raises RuntimeError
    (never returns a blank/placeholder) if this cannot be determined -- the
    revision is the re-embedding-decision anchor recorded in
    finding_embeddings.model, so a silent blank here would be worse than a
    hard failure."""
    try:
        from huggingface_hub import model_info
    except ImportError as exc:
        raise RuntimeError(
            f"findings_embed_service: huggingface_hub unavailable ({type(exc).__name__})"
        ) from exc
    try:
        info = model_info(model_name)
    except Exception as exc:  # network/HF-side errors -- fail closed
        raise RuntimeError(
            f"findings_embed_service: could not resolve model revision for {model_name} "
            f"({type(exc).__name__})"
        ) from exc
    revision = getattr(info, "sha", None)
    if not revision:
        raise RuntimeError(
            f"findings_embed_service: model_info({model_name}) returned no sha"
        )
    return str(revision)


def build_model_tag(model_name: str, revision: str) -> str:
    """Canonical `finding_embeddings.model` value ('<model_name>@<revision>') --
    single source of truth (F-04) so the value written on upsert and the value
    the skip-decision loop compares `existing` rows against can never drift
    apart into two independently-assembled strings."""
    return f"{model_name}@{revision}"


def load_model(model_name: str, revision: str) -> Any:
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(model_name, revision=revision)


def embed_texts(model: Any, texts: list[str], *, batch_size: int) -> list[list[float]]:
    """Encode texts with the mandatory E5_QUERY_PREFIX + normalize_embeddings=True
    (cosine-distance parity with 019's `<=>` operator). Returns plain Python
    float lists so downstream code (sanity_check_embeddings, _vector_literal,
    the report) never needs numpy directly."""
    prefixed = [f"{E5_QUERY_PREFIX}{text}" for text in texts]
    vectors = model.encode(
        prefixed,
        batch_size=batch_size,
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=False,
    )
    return [[float(value) for value in row] for row in vectors]


# ---------------------------------------------------------------------------
# Supabase (PostgREST) transport
# ---------------------------------------------------------------------------


def _normalize_base_url(base_url: str) -> str | None:
    return fsb._normalize_base_url(base_url)


def _get_page(
    base_url: str,
    service_key: str,
    table: str,
    *,
    params: dict[str, str],
    offset: int,
    limit: int,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> tuple[int, list[dict[str, Any]] | None, dict[str, Any], str]:
    """GET one page of a PostgREST resource with arbitrary filter params, via
    Range-header pagination -- same contract as findings_supabase_backfill._get_page,
    generalized to accept caller-supplied filter params (eq./in./order/select)."""
    url = f"{base_url}/rest/v1/{table}"
    headers = {
        "apikey": service_key,
        "Authorization": f"Bearer {service_key}",
        "Range-Unit": "items",
        "Range": f"{offset}-{offset + limit - 1}",
        "Prefer": "count=exact",
    }
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=timeout)
        except requests.exceptions.Timeout:
            if attempt < _MAX_ATTEMPTS:
                continue
            return 0, None, {}, "timeout"
        except requests.exceptions.RequestException as exc:
            return 0, None, {}, type(exc).__name__

        if resp.status_code >= 500:
            if attempt < _MAX_ATTEMPTS:
                continue
            return resp.status_code, None, {}, f"http_{resp.status_code}"
        if resp.status_code >= 400:
            return resp.status_code, None, {}, f"http_{resp.status_code}"

        try:
            data = resp.json()
        except ValueError:
            data = []
        rows = data if isinstance(data, list) else []
        return resp.status_code, rows, dict(resp.headers), ""

    return 0, None, {}, "retry_exhausted"  # unreachable safety net


def _fetch_all_pages(
    base_url: str,
    service_key: str,
    table: str,
    *,
    params: dict[str, str],
    page_size: int = _DEFAULT_PAGE_SIZE,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    offset = 0
    total: int | None = None
    while True:
        _status, page_rows, headers, err = _get_page(
            base_url, service_key, table, params=params, offset=offset, limit=page_size,
        )
        if err:
            raise RuntimeError(f"findings_embed_service: GET {table} failed ({err})")

        page_rows = page_rows or []
        rows.extend(page_rows)

        parsed_total = fsb._parse_content_range(fsb._header_ci(headers, "Content-Range"))
        if parsed_total is not None:
            total = parsed_total

        offset += page_size

        if not page_rows:
            break
        if total is not None:
            if offset >= total or len(rows) >= total:
                break
        elif len(page_rows) < page_size:
            break

    return rows


def fetch_target_findings(base_url: str, service_key: str) -> list[dict[str, Any]]:
    """Every findings row passing scope_status='ok' server-side (cheap, most
    selective single predicate), with the full public-gate predicate
    (select_public_findings) applied client-side afterward. Two-step rather
    than a single PostgREST `or=(...)` filter to avoid fragile empty-string
    quoting in a query param (finding_text_ko <> ''), which would be hard to
    verify without a live DB round-trip -- this session cannot perform live
    writes/queries, so correctness here favors the simpler, testable path."""
    base = _normalize_base_url(base_url)
    if base is None:
        raise ValueError("findings_embed_service: SUPABASE_URL must start with https://")
    rows = _fetch_all_pages(
        base, service_key, "findings",
        params={"select": _FINDING_SELECT, "scope_status": "eq.ok", "order": "finding_id.asc"},
    )
    return select_public_findings(rows)


def fetch_raw_signals_by_ids(
    base_url: str,
    service_key: str,
    raw_signal_ids: list[str],
) -> dict[str, dict[str, Any]]:
    """raw_signal_id -> {"raw_json": ..., "firm_name": ...} for exactly the
    given ids (chunked `in.(...)` filter), never a full-table fetch -- raw_json
    is potentially large and only FDA 483 raw_signals are ever requested here
    (see build_embed_text)."""
    base = _normalize_base_url(base_url)
    if base is None:
        raise ValueError("findings_embed_service: SUPABASE_URL must start with https://")

    ids = sorted({rid for rid in raw_signal_ids if rid})
    result: dict[str, dict[str, Any]] = {}
    for start in range(0, len(ids), _RAW_SIGNAL_ID_CHUNK):
        chunk = ids[start:start + _RAW_SIGNAL_ID_CHUNK]
        params = {
            "select": "raw_signal_id,raw_json,firm_name",
            "raw_signal_id": "in.(" + ",".join(chunk) + ")",
        }
        rows = _fetch_all_pages(base, service_key, "raw_signals", params=params, page_size=len(chunk) or 1)
        for row in rows:
            rid = str(row.get("raw_signal_id") or "")
            if rid:
                result[rid] = row
    return result


def fetch_existing_embeddings(
    base_url: str,
    service_key: str,
    embedding_version: int,
) -> dict[str, tuple[str, str]]:
    """finding_id -> (text_sha256, model) for every row already stored at this
    embedding_version -- the re-embedding-decision baseline.

    ★F-04: both fields are required for that decision. text_sha256 alone
    cannot detect a model-revision bump on a row whose source text hasn't
    changed -- that row would be skipped forever, leaving its old-revision
    vector sitting next to new-revision vectors inside the *same*
    embedding_version (019's one-version-one-space contract broken). Callers
    must compare *both* text_sha256 and model before treating a row as
    already current."""
    base = _normalize_base_url(base_url)
    if base is None:
        raise ValueError("findings_embed_service: SUPABASE_URL must start with https://")
    rows = _fetch_all_pages(
        base, service_key, "finding_embeddings",
        params={
            "select": "finding_id,text_sha256,model",
            "embedding_version": f"eq.{embedding_version}",
        },
    )
    return {
        str(row.get("finding_id") or ""): (
            str(row.get("text_sha256") or ""),
            str(row.get("model") or ""),
        )
        for row in rows
    }


def _upsert_embeddings_batch(
    base_url: str,
    service_key: str,
    rows: list[dict[str, Any]],
    *,
    timeout: int = _UPSERT_TIMEOUT_SECONDS,
) -> tuple[int, list[dict[str, Any]] | None, str]:
    """POST rows to finding_embeddings with an on_conflict upsert (composite PK
    embedding_version,finding_id) -- Prefer: resolution=merge-duplicates so a
    re-run overwrites a stale vector/model/text_sha256 instead of erroring."""
    url = f"{base_url}/rest/v1/finding_embeddings"
    headers = {
        "apikey": service_key,
        "Authorization": f"Bearer {service_key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }
    params = {"on_conflict": "embedding_version,finding_id"}

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            resp = requests.post(url, params=params, json=rows, headers=headers, timeout=timeout)
        except requests.exceptions.Timeout:
            if attempt < _MAX_ATTEMPTS:
                continue
            return 0, None, "timeout"
        except requests.exceptions.RequestException as exc:
            return 0, None, type(exc).__name__

        if resp.status_code >= 500:
            if attempt < _MAX_ATTEMPTS:
                continue
            return resp.status_code, None, f"http_{resp.status_code}"
        if resp.status_code >= 400:
            return resp.status_code, None, f"http_{resp.status_code}"

        try:
            data = resp.json()
        except ValueError:
            return resp.status_code, [], ""
        return resp.status_code, (data if isinstance(data, list) else []), ""

    return 0, None, "retry_exhausted"  # unreachable safety net


# ---------------------------------------------------------------------------
# Error message sanitization (pure) -- Codex Minor 3
#
# report["errors"] 의 키 자체는 §13 의 닫힌 허용목록(ReportKeySetIsClosedAllowlistTest)
# 이 고정하지만, 그 목록은 **키**만 닫았지 **값**은 아무것도 제한하지 않는다. errors[]
# 항목은 대부분 str(exc) -- requests/urllib3 등 우리가 내용을 통제할 수 없는 라이브러리가
# 만드는 유일한 자유 텍스트 경로라서, 예외 메시지에 URL 자격증명(`https://u:p@host/...`)
# 이나 쿼리스트링 토큰(`?token=...`)이 실려 있으면 그대로 _write_report() 의 stdout 까지
# 찍힌다(Codex 실증: `https://u:p@proxy.invalid/token` 주입 재현). 이 함수는 report["errors"]
# 에 들어가는 모든 문자열이 거치는 단일 관문이다.
# ---------------------------------------------------------------------------

_ERROR_MAX_LEN = 500
# ://user:pass@ 형태의 URL 내장 자격증명.
_CRED_URL_RE = re.compile(r"(://)[^/\s@]+:[^/\s@]+@")
# key=value / token=value / secret=value 등 쿼리스트링·헤더류 토큰(대소문자 무시).
_KV_SECRET_RE = re.compile(r"(?i)(key|token|secret|password|apikey|authorization)=[^&\s]+")
# JWT(세 개의 base64url 세그먼트, "eyJ" 로 시작하는 헤더부).
_JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}")


def _sanitize_error(text: str) -> str:
    """report["errors"] 항목 하나를 마스킹한다 -- 순서 고정: URL 자격증명 ->
    key=value 토큰 -> JWT -> 길이 상한. URL 자격증명을 먼저 지워야 그 자리에
    남은 `secret=...` 류가 key=value 규칙에 걸려 통째로 `***` 로 뭉개져도
    무방하다(자격증명이 사라지는 것이 목적이므로 과도 마스킹 쪽으로 실패하는
    것이 안전하다). 길이 상한은 예외 메시지가 HTTP 응답 본문 전체를 물고 오는
    경우(예: requests 의 일부 예외)를 방어한다."""
    masked = _CRED_URL_RE.sub(r"\1***@", text)
    masked = _KV_SECRET_RE.sub(r"\1=***", masked)
    masked = _JWT_RE.sub("***jwt***", masked)
    if len(masked) > _ERROR_MAX_LEN:
        masked = masked[:_ERROR_MAX_LEN] + "…"
    return masked


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def run_embed(
    base_url: str,
    service_key: str,
    *,
    embedding_version: int,
    embed_input: str,
    limit: int | None = None,
    batch_size: int = 64,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Fetch public findings, decide which need (re)embedding at
    embedding_version, compute embeddings (always -- even in dry_run, so the
    report carries the real timing/text-length stats the design needs), then
    (unless dry_run) upsert. Never touches the active-version switch table --
    that cutover step is a deliberate, separate control-tower action (019
    comment)."""
    report: dict[str, Any] = {
        "mode": "dry_run" if dry_run else "apply",
        "embedding_version": embedding_version,
        "embed_input_requested": embed_input,
        "model": "",
        "candidates_total": 0,
        "already_current": 0,
        "to_embed": 0,
        "revision_changed": 0,
        "embedded": 0,
        "upserted": 0,
        "b_input_used": 0,
        "b_fallback_to_a": 0,
        "input_text_len_mean": 0.0,
        "elapsed_seconds": 0.0,
        "errors": [],
    }

    base = _normalize_base_url(base_url)
    if base is None:
        report["errors"].append(_sanitize_error("SUPABASE_URL must start with https://"))
        return report

    t0 = time.monotonic()

    try:
        findings = fetch_target_findings(base, service_key)
    except (RuntimeError, ValueError) as exc:
        report["errors"].append(_sanitize_error(str(exc)))
        return report

    if limit is not None and limit >= 0:
        findings = findings[:limit]
    report["candidates_total"] = len(findings)

    raw_signals_by_id: dict[str, dict[str, Any]] = {}
    if embed_input == "B":
        needed_ids = [
            str(f.get("raw_signal_id") or "") for f in findings
            if str(f.get("source") or "") == FDA_483_SOURCE
        ]
        try:
            raw_signals_by_id = fetch_raw_signals_by_ids(base, service_key, needed_ids)
        except (RuntimeError, ValueError) as exc:
            report["errors"].append(_sanitize_error(str(exc)))
            return report

    try:
        existing = fetch_existing_embeddings(base, service_key, embedding_version)
    except (RuntimeError, ValueError) as exc:
        report["errors"].append(_sanitize_error(str(exc)))
        return report

    # ★F-04 fix: resolve the model revision *before* the skip decision below,
    # not after it. The skip predicate needs today's model tag to tell "same
    # text, same model" (truly current) apart from "same text, different
    # model" (revision bumped -- must re-embed even though the text didn't
    # change, or this row's vector silently stays in the old space forever).
    # Model *loading* (heavy -- instantiates the full sentence-transformers
    # model) is intentionally left below, gated on to_process being non-empty,
    # exactly as before.
    try:
        revision = resolve_model_revision(MODEL_NAME)
    except RuntimeError as exc:
        report["errors"].append(_sanitize_error(str(exc)))
        report["elapsed_seconds"] = round(time.monotonic() - t0, 3)
        return report

    model_tag = build_model_tag(MODEL_NAME, revision)
    report["model"] = model_tag

    to_process: list[tuple[str, str, str, str]] = []  # (finding_id, text, actual_mode, sha256)
    text_lengths: list[int] = []
    for finding in findings:
        finding_id = str(finding.get("finding_id") or "")
        text, actual_mode = build_embed_text(finding, embed_input, raw_signals_by_id)
        text_lengths.append(len(text))
        if actual_mode == "B":
            report["b_input_used"] += 1
        elif embed_input == "B":
            report["b_fallback_to_a"] += 1

        sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
        existing_sha256, existing_model = existing.get(finding_id, ("", ""))
        if existing_sha256 == sha256 and existing_model == model_tag:
            report["already_current"] += 1
            continue
        if existing_sha256 == sha256 and existing_sha256 != "" and existing_model != model_tag:
            # Same input text, different model revision -- text_sha256 alone
            # would have called this "current"; it must be re-embedded.
            report["revision_changed"] += 1
        to_process.append((finding_id, text, actual_mode, sha256))

    report["to_embed"] = len(to_process)
    if text_lengths:
        report["input_text_len_mean"] = round(sum(text_lengths) / len(text_lengths), 1)

    if not to_process:
        report["elapsed_seconds"] = round(time.monotonic() - t0, 3)
        return report

    try:
        model = load_model(MODEL_NAME, revision)
    except RuntimeError as exc:
        report["errors"].append(_sanitize_error(str(exc)))
        report["elapsed_seconds"] = round(time.monotonic() - t0, 3)
        return report

    texts = [item[1] for item in to_process]
    vectors: list[list[float]] = []
    for start in range(0, len(texts), batch_size):
        chunk = texts[start:start + batch_size]
        vectors.extend(embed_texts(model, chunk, batch_size=batch_size))
    report["embedded"] = len(vectors)

    violations = sanity_check_embeddings(vectors)
    if violations:
        # 019 §전환순서 ② -- 하나라도 위반이면 전체 중단(부분 적재 금지).
        report["errors"].extend(_sanitize_error(f"sanity check failed: {v}") for v in violations)
        report["elapsed_seconds"] = round(time.monotonic() - t0, 3)
        return report

    if dry_run:
        report["elapsed_seconds"] = round(time.monotonic() - t0, 3)
        return report

    rows = [
        {
            "embedding_version": embedding_version,
            "finding_id": finding_id,
            "embedding": _vector_literal(vector),
            "model": model_tag,
            "embed_input": actual_mode,
            "text_sha256": sha256,
        }
        for (finding_id, _text, actual_mode, sha256), vector in zip(to_process, vectors)
    ]

    upserted = 0
    for start in range(0, len(rows), _DB_UPSERT_BATCH_SIZE):
        batch = rows[start:start + _DB_UPSERT_BATCH_SIZE]
        _status, _resp_rows, err = _upsert_embeddings_batch(base, service_key, batch)
        if err:
            report["errors"].append(_sanitize_error(f"upsert batch failed at offset {start} ({err})"))
            continue
        upserted += len(batch)

    report["upserted"] = upserted
    report["elapsed_seconds"] = round(time.monotonic() - t0, 3)
    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _resolve_credentials(args: argparse.Namespace) -> tuple[str, str] | None:
    url = (args.supabase_url or os.environ.get("SUPABASE_URL") or "").strip()
    key = (args.service_role_key or os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    if not url or not key:
        return None
    return url, key


def _write_report(path: str | None, report: dict[str, Any]) -> None:
    """리포트를 **항상 로그(stdout)에 출력**하고, path 가 있으면 파일로도 남긴다.

    ★왜 항상 로그인가: 종전에는 --output 이 있으면 파일로만 나가서 job 로그에 카운터가
      전혀 안 보였다. 그래서 dry-run 의 to_embed=0 같은 핵심 사실을 사람이 "로그 정황 +
      DB 상태"로 추론해야 했다(Codex 감사 지적). 리포트를 로그에 직접 찍으면 그 추론이
      관찰로 바뀐다.

    ★시크릿 안전성: 리포트는 카운터·메타뿐이며 자격증명을 담지 않는다. service_key 는
      HTTP 헤더로만 쓰이고 리포트에 들어가는 경로가 없다. errors 의 대다수는
      http_<status>·retry_exhausted 류 고정 문자열이지만, 일부는 str(exc)(라이브러리가
      만드는 자유 텍스트)를 담는다 -- 그 자유 텍스트는 report["errors"].append() 호출부에서
      전부 _sanitize_error() 를 거쳐 URL 자격증명/key=value 토큰/JWT 를 마스킹하고
      길이를 자른 뒤에만 이 dict 에 들어간다(Codex Minor 3, run_embed 참조).
      키 집합 자체는 test_findings_embed_service.py 의 §13 허용목록이 고정한다 — 새 키를
      추가하면 테스트가 깨지므로, 시크릿이 리포트에 스며드는 변경은 CI 에서 막힌다.
    """
    text = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2)
    print(text)
    if path:
        Path(path).write_text(text + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description="[FIND-1 S2] unattended CI service that embeds public findings and "
        "upserts them into finding_embeddings (no LLM, no git writes; READ findings/"
        "raw_signals + finding_embeddings upsert only)."
    )
    parser.add_argument(
        "--supabase-url",
        help="Supabase project URL (falls back to $SUPABASE_URL)",
    )
    parser.add_argument(
        "--service-role-key",
        help="Supabase service-role key (falls back to $SUPABASE_SERVICE_ROLE_KEY)",
    )
    parser.add_argument(
        "--embedding-version",
        type=int,
        required=True,
        help="finding_embeddings.embedding_version to (re)load (019's composite-PK axis)",
    )
    parser.add_argument(
        "--embed-input",
        required=True,
        choices=("A", "B"),
        help="'A' = finding_text verbatim, 'B' = deficiency+detail reconstruction "
        "(falls back to 'A' per-row when ambiguous/unmatched/no detail)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only consider the first N public findings this run (default: all)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=64,
        help="Model encoding batch size (default: 64)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Compute embeddings and report stats, but never upsert to Supabase",
    )
    parser.add_argument("--output", help="Report JSON output path (default: stdout)")
    args = parser.parse_args(argv)

    creds = _resolve_credentials(args)
    if creds is None:
        print(
            "findings_embed_service: --supabase-url/--service-role-key or "
            "$SUPABASE_URL/$SUPABASE_SERVICE_ROLE_KEY are required",
            file=sys.stderr,
        )
        return 2
    base_url, service_key = creds

    report = run_embed(
        base_url,
        service_key,
        embedding_version=args.embedding_version,
        embed_input=args.embed_input,
        limit=args.limit,
        batch_size=args.batch_size,
        dry_run=args.dry_run,
    )
    _write_report(args.output, report)

    if report["errors"]:
        return 1
    return 0


__all__ = [
    "is_public_gate_row",
    "select_public_findings",
    "resolve_b_text",
    "build_embed_text",
    "sanity_check_embeddings",
    "resolve_model_revision",
    "build_model_tag",
    "load_model",
    "embed_texts",
    "fetch_target_findings",
    "fetch_raw_signals_by_ids",
    "fetch_existing_embeddings",
    "run_embed",
    "main",
    "MODEL_NAME",
    "EMBED_DIM",
    "E5_QUERY_PREFIX",
]


if __name__ == "__main__":
    raise SystemExit(main())
