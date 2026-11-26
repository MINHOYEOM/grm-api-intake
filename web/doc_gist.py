"""[구글 품질 2단계 · 2026-11-20] 문서 페이지 첫머리의 'GRM 요지' — 덧씌우기·검사(순수 함수).

왜: 구글에 남긴 국문 문서 2,034장 가운데 1,319장(FDA 483 743·캐나다 HC 485·식약처 91)은 지적 본문이 합쳐 800자도
안 된다. 지적 제목만 줄지어 있어 "무슨 실사였나"가 한눈에 안 들어온다. 문서 첫머리에 그 문서의 지적만으로 쓴
두세 문장 요지를 둔다(범위안 2026-11-19 §3 보강표).

GRM 요지의 정의(지침 docs/specs/doc_gist_brief.md):
  · 짧다 — 60~300자, 세 문장 이하.
  · 사실만 — 문서 종류·지적 수·지적이 몰린 분야와 그 내용·지적 문장에 적힌 조항. 평가·추측·권고·전망 금지.
  · 그 문서의 글만 — 숫자·영문 낱말·조항은 그 문서(지적 문장·메타)에 있는 것만 쓴다. 다른 문서·배경지식으로 보태지 않는다.

흐름(비용 0 — 조항 번역 키트와 같은 꼴):
  내보내기 `doc_gist_kit.py export` → 세션·Codex(구독 경로)가 `gist_ko` 를 채움 → `doc_gist_kit.py qa` + CI 검사
  → `translations/doc_gist_outbox/*.json` 이 main 에 들어가면 빌드가 국문 문서 페이지에 싣는다(DB 쓰기 없음).

outbox 항목: {"document_id", "src_sha": 요지를 쓸 때의 지적 본문 해시, "gist_ko", "basis": [근거 지적 번호(1부터)]}
  · 지적 본문이 바뀌면(src_sha 불일치) 그 요지는 싣지 않는다 → 다시 큐로 간다.
  · 빌드도 같은 `check` 를 돌려 실격 요지는 싣지 않는다(이중 안전).
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

OUTBOX_DIR = Path(__file__).resolve().parent.parent / "translations" / "doc_gist_outbox"
#: 화면 표지 — 국문 트리 전용(영문 트리에는 요지가 없다).
LABEL = "GRM 요지"
NOTE = "이 문서의 지적 {n}건 문장만으로 GRM 이 정리했습니다. 판단의 근거는 원문입니다."

MIN_CHARS, MAX_CHARS, MAX_SENTENCES = 60, 300, 3

_HANGUL_RE = re.compile(r"[가-힣]")
_HANJA_RE = re.compile(r"[一-鿿㐀-䶿]")
_NUM_RE = re.compile(r"\d+(?:\.\d+)*")
_LATIN_RE = re.compile(r"[A-Za-z][A-Za-z0-9\-]*[A-Za-z0-9]")
_SENT_END_RE = re.compile(r"[다요음임][.。]")
#: 문서가 쓰지 않았는데 요지가 쓰면 안 되는 말 — 평가·추측·권고·강조. 지적 문장 자체에 있으면 허용한다.
JUDGEMENT_WORDS = (
    "추정", "가능성", "우려", "보인다", "보여 준다", "보여준다", "시사", "심각", "중대", "반드시", "해야", "권고",
    "바람직", "필요하다", "취약", "부실", "허술", "엄중", "강력", "위험하다", "대표적", "전형적", "주목", "눈에 띈",
    "핵심", "의심", "드러냈", "방증", "하십시오", "하세요", "바랍니다", "귀사",
)
#: 메타에 없어도 쓸 수 있는 영문 표기(기관·문서 종류 이름).
LATIN_ALLOW = {"fda", "gmp", "mfds", "cfr", "grm", "hc"}


def src_sha(doc: dict[str, Any]) -> str:
    """요지가 기대는 원천 — 지적 id·국문 본문. 하나라도 바뀌면 해시가 달라진다."""
    rows = [[f.get("finding_id") or "", (f.get("text_ko") or "").strip()] for f in doc.get("findings") or []]
    return hashlib.sha256(json.dumps(rows, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def _refs(f: dict[str, Any]) -> list[str]:
    r = f.get("cfr_refs") or []
    return [str(x) for x in r] if isinstance(r, list) else [str(r)]


def source_blob(doc: dict[str, Any]) -> str:
    """요지가 기댈 수 있는 글 전부(메타 + 지적 국문·원문·조항·분류)."""
    parts = [doc.get("firm_name") or "", doc.get("source") or "", doc.get("agency") or "",
             doc.get("published_date") or "", doc.get("inspection_date") or "", " ".join(doc.get("categories") or [])]
    for f in doc.get("findings") or []:
        parts += [f.get("text_ko") or "", f.get("text_orig") or "", f.get("category_label_ko") or "", " ".join(_refs(f))]
    return "\n".join(parts)


def allowed_numbers(doc: dict[str, Any]) -> set[str]:
    blob = source_blob(doc)
    nums = set(_NUM_RE.findall(blob))
    nums |= {n.lstrip("0") or "0" for n in list(nums)}        # 2024-01-17 → 1월 17일
    nums |= {str(i) for i in range(1, len(doc.get("findings") or []) + 1)}   # 지적 수·분야별 건수
    nums.add("483")
    return nums


def check(doc: dict[str, Any], item: dict[str, Any]) -> list[str]:
    """요지 검사. 빈 목록이면 통과."""
    g = (item.get("gist_ko") or "").strip()
    probs: list[str] = []
    if not _HANGUL_RE.search(g):
        return ["no_hangul"]
    if _HANJA_RE.search(g):
        probs.append("hanja")
    if not MIN_CHARS <= len(g) <= MAX_CHARS:
        probs.append(f"length:{len(g)}")
    n_sent = len(_SENT_END_RE.findall(g))
    if n_sent > MAX_SENTENCES:
        probs.append(f"sentences:{n_sent}")
    if "\n" in g:
        probs.append("newline")
    n = len(doc.get("findings") or [])
    if f"{n}건" not in g:
        probs.append(f"count_missing:{n}건")
    allowed = allowed_numbers(doc)
    bad_nums = sorted({x for x in _NUM_RE.findall(g) if x not in allowed and x.lstrip("0") not in allowed})
    if bad_nums:
        probs.append(f"numbers_not_in_doc:{','.join(bad_nums[:6])}")
    blob = source_blob(doc)
    low = blob.lower()
    bad_latin = sorted({w for w in _LATIN_RE.findall(g) if w.lower() not in LATIN_ALLOW and w.lower() not in low})
    if bad_latin:
        probs.append(f"latin_not_in_doc:{','.join(bad_latin[:6])}")
    bad_words = [w for w in JUDGEMENT_WORDS if w in g and w not in blob]
    if bad_words:
        probs.append(f"judgement:{','.join(bad_words)}")
    basis = item.get("basis")
    if not isinstance(basis, list) or not basis:
        probs.append("basis_missing")
    elif any(not isinstance(b, int) or not 1 <= b <= n for b in basis) or len(set(basis)) != len(basis):
        probs.append(f"basis_invalid:{basis}")
    for f in doc.get("findings") or []:
        t = (f.get("text_ko") or "").strip()
        if len(t) >= 40 and t in g:
            probs.append("copies_finding")   # 요지는 지적 한 줄을 그대로 옮긴 것이 아니다
            break
    return probs


def load_outbox(outbox_dir: "Path | None" = None) -> dict[str, dict[str, Any]]:
    """document_id → 항목(이름순 뒤 파일이 이긴다)."""
    d = OUTBOX_DIR if outbox_dir is None else outbox_dir
    out: dict[str, dict[str, Any]] = {}
    if not d.is_dir():
        return out
    for p in sorted(d.glob("*.json")):
        try:
            items = json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if not isinstance(items, list):
            continue
        for it in items:
            if isinstance(it, dict) and it.get("document_id") and (it.get("gist_ko") or "").strip():
                out[it["document_id"]] = {**it, "_file": p.name}
    return out


def view(doc: dict[str, Any], overlay: dict[str, dict[str, Any]]) -> "dict[str, str] | None":
    """문서 페이지에 실을 요지(없거나 낡았거나 실격이면 None)."""
    it = overlay.get(doc.get("document_id") or "")
    if not it or it.get("src_sha") != src_sha(doc) or check(doc, it):
        return None
    return {"label": LABEL, "text": it["gist_ko"].strip(),
            "note": NOTE.format(n=len(doc.get("findings") or []))}
