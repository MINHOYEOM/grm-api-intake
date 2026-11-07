#!/usr/bin/env python3
"""수집기 제외 장부(intake exclusion ledger) — 원천에서 받았지만 **의도적으로 버린** 문서의 기록.

왜 있나
  수집기는 원천 목록에서 받은 문서 가운데 관련성·범위 규칙에 걸리는 것을 저장하지 않고 버린다. 버린
  문서는 어디에도 흔적이 없어서 ① "의도적으로 버림"과 "놓침"을 구분할 수 없었고 ② 다음 날 같은 PDF
  를 또 받아 하루 한도를 낭비했다(2026-11-06 FDA 483: 32건 시도, 23건 버림, 9건 수집).
  이 장부는 그 흔적이다. 원천 대조 감시가 "원천 건수 = 수집 + 의도적 제외 + 미뤄짐" 을 셀 수 있게 한다.

★규칙 버전이 핵심이다 — 장부는 영구 블랙리스트가 아니다
  행마다 `rule_version`(판정 규칙의 지문)을 함께 적는다. 다음 날 같은 문서는 **같은 규칙 버전일 때만**
  "이미 판정했다"로 보고 다시 받지 않는다. 키워드 목록·판정 함수가 바뀌면 지문이 달라지고, 그 문서는
  자연히 다시 받아 **다시 판정**된다(오판정을 고치면 그 문서가 자동으로 다시 들어온다).
  지문은 사람이 올리는 숫자가 아니라 규칙 코드에서 계산한다(`rule_fingerprint`): 판정 함수에서 출발해
  그 함수가 참조하는 모듈 전역(키워드 목록·정규식·보조 함수)을 AST 로 따라가 해시한다. 주석·공백 수정은
  지문을 바꾸지 않고, 키워드 한 단어는 바꾼다.

저장 실패는 수집을 멈추지 않는다
  모든 네트워크·파싱 실패는 삼키고 카운터(`summary()`)로만 남긴다. 장부는 비용 최적화이자 관측 장치이지
  정합성 장치가 아니다(정합성은 적재 dedup 이 책임진다). 장부를 못 읽으면 전량 재판정(오늘과 동일 동작).

쓰기 경로
  Supabase RPC `intake_exclusion_record` / `intake_exclusion_release`(migration 130) — service role 키만.
  기존 raw_signals append(`findings_supabase_append`)와 같은 PostgREST + service role 방식이고, 오류 요약은
  예외 문구가 아니라 `timeout`/예외 타입/`http_<status>` 로만 남긴다(키가 로그에 새지 않게).
"""
from __future__ import annotations

import ast
import hashlib
import importlib
import re
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from typing import Any, Iterable

import requests

DEFAULT_TIMEOUT_SECONDS = 15
_MAX_ATTEMPTS = 2            # 최초 1회 + 5xx/타임아웃 재시도 1회
_WRITE_CHUNK = 200
_READ_PAGE = 1000
_READ_MAX_PAGES = 50         # 50,000행 — 넘으면 truncated 로 표시(전량 재판정과 동일 동작)

BASIS_METADATA = "metadata"        # 목록 메타(시설 유형 등)만으로 확정 — 본문이 판정을 뒤집지 않는다
BASIS_TEXT = "text"                # 본문까지 읽고 내린 판정
BASIS_TEXT_MISSING = "text_missing"  # 본문을 못 얻은 채 메타만으로 내린 판정 → 다음 실행에 다시 본다
_BASES = (BASIS_METADATA, BASIS_TEXT, BASIS_TEXT_MISSING)

_REASON_RE = re.compile(r"^[a-z0-9_]{2,60}$")


# ─────────────────────────────────────────────────────────────────────────────
# 규칙 지문
# ─────────────────────────────────────────────────────────────────────────────
@lru_cache(maxsize=None)
def _module_index(module_name: str) -> dict[str, tuple[ast.AST, ...]]:
    """모듈 최상위의 이름 → 정의 노드들(함수·클래스·대입)."""
    mod = importlib.import_module(module_name)
    path = getattr(mod, "__file__", None)
    if not path:
        return {}
    with open(path, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read())
    index: dict[str, list[ast.AST]] = {}
    for node in tree.body:
        names: list[str] = []
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names = [node.name]
        elif isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)) and isinstance(node.target, ast.Name):
            names = [node.target.id]
        for n in names:
            index.setdefault(n, []).append(node)
    return {k: tuple(v) for k, v in index.items()}


def rule_closure(roots: Iterable[str], modules: Iterable[str]) -> list[tuple[str, str]]:
    """`roots` 에서 transitive 로 닿는 (이름, AST 덤프) 목록(이름순). 지문의 재료이자 시험의 관측창."""
    mods = tuple(modules)
    indexes = [_module_index(m) for m in mods]
    seen: set[str] = set()
    todo = list(roots)
    parts: list[tuple[str, str]] = []
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        for idx in indexes:
            nodes = idx.get(name)
            if not nodes:
                continue
            for node in nodes:
                parts.append((name, ast.dump(node)))
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Name) and sub.id not in seen:
                        todo.append(sub.id)
            break   # 앞 모듈이 정의하면 뒤 모듈은 보지 않는다(임포트 재수출 중복 방지)
    parts.sort()
    return parts


def rule_fingerprint(roots: Iterable[str], modules: Iterable[str], *, salt: str = "") -> str:
    """판정 규칙의 지문(12자리 hex).

    `roots` 에서 출발해 `modules` 안에서 정의된 이름을 transitive 로 따라가며(함수 본문이 읽는 전역
    상수·정규식·보조 함수) 각 정의의 AST 덤프를 이름순으로 해시한다. 행 번호·주석·공백은 AST 에 없어
    지문에 영향이 없다. 어느 모듈에도 정의가 없는 이름(내장·임포트)은 무시한다.
    `salt` 는 AST 로 잡히지 않는 판정 구조(분기 순서 등)를 바꿀 때 사람이 올리는 보조 값이다.
    """
    h = hashlib.sha1(salt.encode("utf-8"))
    for name, dump in rule_closure(roots, modules):
        h.update(name.encode("utf-8"))
        h.update(b"\0")
        h.update(dump.encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest()[:12]


# ─────────────────────────────────────────────────────────────────────────────
# 장부
# ─────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class KnownEntry:
    rule_version: str
    reason_code: str
    basis: str


def _iso_or_none(value: str) -> str | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10]).isoformat()
    except ValueError:
        return None


def _clip(value: str, limit: int) -> str:
    value = " ".join(str(value or "").split())
    return value[:limit]


class ExclusionLedger:
    """프로세스 단위 싱글턴(`LEDGER`)으로 쓴다. 비활성(기본)이면 모든 호출이 no-op 이라 수집기는
    장부 유무를 신경 쓰지 않고 `record` 를 부르면 된다."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.enabled = False
        self._base_url = ""
        self._key = ""
        self._run_date = ""
        self._known: dict[tuple[str, str], KnownEntry] = {}
        self._pending: dict[tuple[str, str], dict[str, Any]] = {}
        self._released: dict[tuple[str, str], None] = {}
        self._skipped_known: dict[str, int] = {}
        self.known_loaded = 0
        self.load_failed = False
        self.load_truncated = False
        self.flushed = 0
        self.flush_failed = 0
        self.released = 0
        self.release_failed = 0
        self.last_error = ""

    # ── 설정·읽기 ─────────────────────────────────────────────────────────
    def configure(self, base_url: str, service_key: str, run_date: date) -> bool:
        """활성화. URL 이 https 가 아니거나 키가 비면 비활성으로 둔다."""
        url = str(base_url or "").strip().rstrip("/")
        if not url.lower().startswith("https://") or not str(service_key or "").strip():
            self.enabled = False
            return False
        self._base_url, self._key = url, str(service_key).strip()
        self._run_date = run_date.isoformat()
        self.enabled = True
        return True

    def _headers(self) -> dict[str, str]:
        return {"apikey": self._key, "Authorization": f"Bearer {self._key}"}

    def load_known(self, sources: Iterable[str]) -> int:
        """이미 장부에 있는 행을 읽는다. 실패하면 비어 있는 채로 진행(전량 재판정)."""
        if not self.enabled:
            return 0
        total = 0
        for source in sources:
            offset = 0
            for _page in range(_READ_MAX_PAGES):
                rows, err = self._get_page(source, offset)
                if err:
                    self.load_failed = True
                    self.last_error = err
                    return total
                for r in rows:
                    try:
                        self._known[(source, str(r["document_id"]))] = KnownEntry(
                            rule_version=str(r.get("rule_version") or ""),
                            reason_code=str(r.get("reason_code") or ""),
                            basis=str(r.get("basis") or BASIS_TEXT))
                        total += 1
                    except (KeyError, TypeError):
                        continue
                if len(rows) < _READ_PAGE:
                    break
                offset += _READ_PAGE
            else:
                self.load_truncated = True
        self.known_loaded = total
        return total

    def _get_page(self, source: str, offset: int) -> tuple[list[dict[str, Any]], str]:
        url = f"{self._base_url}/rest/v1/intake_exclusion"
        params = {"select": "document_id,rule_version,reason_code,basis",
                  "source": f"eq.{source}", "order": "document_id.asc",
                  "limit": str(_READ_PAGE), "offset": str(offset)}
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                resp = requests.get(url, params=params, headers=self._headers(),
                                    timeout=DEFAULT_TIMEOUT_SECONDS)
            except requests.exceptions.Timeout:
                if attempt < _MAX_ATTEMPTS:
                    continue
                return [], "timeout"
            except requests.exceptions.RequestException as exc:
                return [], type(exc).__name__
            if resp.status_code >= 500 and attempt < _MAX_ATTEMPTS:
                continue
            if resp.status_code >= 400:
                return [], f"http_{resp.status_code}"
            try:
                data = resp.json()
            except ValueError:
                return [], "bad_json"
            return (data if isinstance(data, list) else []), ""
        return [], "retry_exhausted"

    def current_entry(self, source: str, document_id: str, rule_version: str) -> KnownEntry | None:
        """**같은 규칙 버전으로 이미 판정한** 제외 행이면 돌려준다 → 호출부는 PDF 를 다시 받지 않는다.

        규칙 버전이 다르면 None(재판정). 본문을 못 얻은 채 내린 판정(text_missing)도 None —
        그 입력으로는 다시 봐야 한다."""
        if not self.enabled:
            return None
        ent = self._known.get((source, document_id))
        if ent is None or not rule_version or ent.rule_version != rule_version:
            return None
        if ent.basis == BASIS_TEXT_MISSING:
            return None
        self._skipped_known[source] = self._skipped_known.get(source, 0) + 1
        return ent

    # ── 쓰기 ──────────────────────────────────────────────────────────────
    def record(self, *, source: str, document_id: str, official_url: str = "",
               title: str = "", published_date: str = "", reason_code: str,
               rule_version: str, basis: str = BASIS_TEXT) -> bool:
        """제외 한 건을 메모리에 쌓는다(flush 에서 한 번에 저장). 비활성이거나 입력이 비정상이면 False."""
        if not self.enabled:
            return False
        source = (source or "").strip()
        document_id = (document_id or "").strip()
        rule_version = (rule_version or "").strip()
        if (not source or not document_id or not rule_version
                or not _REASON_RE.match(reason_code or "") or basis not in _BASES):
            return False
        self._pending[(source, document_id)] = {
            "source": source, "document_id": document_id[:300],
            "official_url": _clip(official_url, 500) or None,
            "title": _clip(title, 300) or None,
            "published_date": _iso_or_none(published_date),
            "reason_code": reason_code, "rule_version": rule_version[:64],
            "basis": basis, "seen_on": self._run_date,
        }
        return True

    def note_included(self, source: str, document_id: str) -> None:
        """이 문서는 이번에 **수집 대상**이 됐다 — 예전 제외 행이 있으면 장부에서 푼다(규칙이 바뀌어
        다시 들어온 경우). 같은 실행에서 제외로 기록한 것은 건드리지 않는다."""
        if not self.enabled:
            return
        key = (source, (document_id or "").strip())
        if key in self._known and key not in self._pending:
            self._released[key] = None

    def flush(self) -> None:
        """쌓은 제외 행 저장 + 풀린 행 삭제. 어떤 실패도 예외로 올리지 않는다."""
        if not self.enabled:
            return
        rows = list(self._pending.values())
        for i in range(0, len(rows), _WRITE_CHUNK):
            chunk = rows[i:i + _WRITE_CHUNK]
            err = self._rpc("intake_exclusion_record", {"p_rows": chunk})
            if err:
                self.flush_failed += len(chunk)
                self.last_error = err
            else:
                self.flushed += len(chunk)
        keys = [{"source": s, "document_id": d} for (s, d) in self._released]
        for i in range(0, len(keys), _WRITE_CHUNK):
            chunk = keys[i:i + _WRITE_CHUNK]
            err = self._rpc("intake_exclusion_release", {"p_keys": chunk})
            if err:
                self.release_failed += len(chunk)
                self.last_error = err
            else:
                self.released += len(chunk)

    def _rpc(self, fn: str, body: dict[str, Any]) -> str:
        """성공이면 "", 실패면 오류 요약(예외 문구 아님)."""
        url = f"{self._base_url}/rest/v1/rpc/{fn}"
        headers = {**self._headers(), "Content-Type": "application/json"}
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                resp = requests.post(url, json=body, headers=headers,
                                     timeout=DEFAULT_TIMEOUT_SECONDS)
            except requests.exceptions.Timeout:
                if attempt < _MAX_ATTEMPTS:
                    continue
                return "timeout"
            except requests.exceptions.RequestException as exc:
                return type(exc).__name__
            if resp.status_code >= 500 and attempt < _MAX_ATTEMPTS:
                continue
            if resp.status_code >= 400:
                return f"http_{resp.status_code}"
            return ""
        return "retry_exhausted"

    # ── 요약 ──────────────────────────────────────────────────────────────
    def summary(self) -> dict[str, Any]:
        """실행 요약용(health JSON·step summary). 소스별 excluded 가 핵심 값이다."""
        by_source: dict[str, dict[str, Any]] = {}
        for (source, _doc), row in self._pending.items():
            s = by_source.setdefault(source, {"excluded": 0, "skipped_known": 0, "by_reason": {}})
            s["excluded"] += 1
            s["by_reason"][row["reason_code"]] = s["by_reason"].get(row["reason_code"], 0) + 1
        for source, n in self._skipped_known.items():
            by_source.setdefault(source, {"excluded": 0, "skipped_known": 0, "by_reason": {}})
            by_source[source]["skipped_known"] = n
        return {
            "enabled": self.enabled,
            "by_source": by_source,
            "known_loaded": self.known_loaded,
            "load_failed": self.load_failed,
            "load_truncated": self.load_truncated,
            "flushed": self.flushed,
            "flush_failed": self.flush_failed,
            "released": self.released,
            "release_failed": self.release_failed,
            "last_error": self.last_error,
        }


LEDGER = ExclusionLedger()


def record(**kwargs: Any) -> bool:
    """수집기용 짧은 진입점 — `LEDGER.record`."""
    return LEDGER.record(**kwargs)
