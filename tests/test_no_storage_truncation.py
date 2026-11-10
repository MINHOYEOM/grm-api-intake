#!/usr/bin/env python3
"""저장 단계 글자수 자르기 가드 — "저장할 때는 절대 자르지 않는다, 줄이는 건 화면에서만" (대표 원칙 2026-11-10).

왜
  모은 내용을 저장하면서 잘라 버린 일이 반복됐다(FDA 483 상세 1,200자 · 경고서한 위반 480자·6,000자 ·
  경고서한 전문 20,000→30,000자). 매번 "넉넉한 숫자"를 골랐지만 원천이 그보다 길어지면 조용히 잘렸고,
  잘린 뒷부분은 어디에도 남지 않았다. 진짜 외부 한도는 Notion 의 "글 한 덩어리 2,000자"·"선택지 이름 100자"·
  "한 번에 블록 100개" 뿐이고 이것도 자르기가 아니라 **나눠 담기**로 푼다.

무엇을 막나
  수집·적재 코드(collect_*·backfill_*·findings_store·findings_extractors·grm_notion·grm_common 등)에서
  ① 길이 상한 상수 정의(`*_MAX_CHARS`·`*_CHAR_CAP`·`*_EXCERPT_LIMIT` …) ② `truncate(` 호출
  ③ 100자 이상 문자열 자르기(`[:240]`·`[:SOME_MAX_CHARS]`) 를 줄 단위로 찾는다.
  **아래 `ALLOWED` 에 없는 새 줄이 생기면 실패한다.** 로그·오류 문구 자르기는 세지 않는다.

  `ALLOWED` 의 debt 는 2026-11-10 전수 조사(내용 완전성 감시 PR 과 함께 만든 표) 시점의 **고칠 목록**이다 —
  허용이 아니다. 수리안은 대표 승인 뒤 소스별로 고치고, 고친 줄은 여기서 지운다(남은 항목은 실행 때 알려 준다).
  새로 꼭 필요한 상한이면(진짜 외부 한도) `ALLOWED` 에 genuine 으로 이유를 적는다. 화면(렌더·카드 문안)에서
  자르는 것은 이 가드의 대상 파일이 아니다(web/·card_scaffold 등).
"""
from __future__ import annotations

import re
import sys
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCOPE_GLOBS = ("collect_*.py", "backfill_*.py", "findings_store.py", "findings_extractors.py", "findings_supabase*.py",
               "grm_notion.py", "grm_common.py", "grm_findings.py", "library_collect_*.py", "mfds_*.py", "fda483_*.py",
               "eudragmdp_client.py", "mhra_gmdp_client.py", "import_alerts_refresh.py", "deficiency_cases_refresh.py",
               "fda_citation_firms_refresh.py", "findings_translate*.py")

_CONST = re.compile(r"^\s*(?![A-Z0-9_]*MIN_)_?[A-Z][A-Z0-9_]*(?:CHARS|CHAR_CAP|EXCERPT_LIMIT|BODY_LIMIT|TEXT_LIMIT|MAX_LEN)\s*(?::\s*int)?\s*=")
_TRUNC_CALL = re.compile(r"\btruncate\(")
_SLICE = re.compile(r"\[\s*(?:0\s*)?:\s*(?:(\d{3,})|(_?[A-Z][A-Z0-9_]*(?:CHARS|CAP|LIMIT|LEN)[A-Z0-9_]*))\s*\]")
# 상한 상수를 함수에 넘기는 줄(예: `_cap_detail(text, FOO_MAX_CHARS)` — 문장 끝에서 자르면 데이터 감시가 못 본다)
_CAP_REF = re.compile(r"\b(?![A-Z0-9_]*MIN_)_?[A-Z][A-Z0-9_]*(?:CHARS|CHAR_CAP|EXCERPT_LIMIT|BODY_LIMIT|TEXT_LIMIT|MAX_LEN)\b")
# 자르기 함수에 숫자를 바로 넘기는 줄(예: `clip(body, 1200)`)
_CAP_FN = re.compile(r"\b\w*(?:cap|trunc|clip|shorten|squeeze)\w*\([^()]*,\s*\d{3,}\s*\)", re.I)
# 로그·오류·진단 문구 안의 자르기는 저장이 아니다
_NOT_STORAGE = re.compile(r"\b(log|print|logger\.\w+|warn\w*|_emit|raise|RuntimeError|ValueError|SystemExit)\s*\(|"
                          r"\bstr\((e|exc|err)\)|\.text\[|\berror\b|\berr\b|f\"[^\"]*(실패|fail|error|HTTP)")


def scan(root: Path = ROOT) -> Counter:
    """(파일, 줄 내용) → 개수. 줄 번호는 매번 바뀌므로 쓰지 않는다."""
    hits: Counter = Counter()
    files = sorted({p for g in SCOPE_GLOBS for p in root.glob(g)})
    for p in files:
        for raw in p.read_text(encoding="utf-8").splitlines():
            code = raw.split("#", 1)[0] if not raw.lstrip().startswith(("'", '"')) else raw
            line = code.strip()
            if not line or line.startswith(("#", '"""', "'''", 'f"', "f'")):   # f-문자열 조각 = 보고 문구
                continue
            if _NOT_STORAGE.search(line):
                continue
            if _CONST.search(code):
                hits[(p.name, line)] += 1
                continue
            if _TRUNC_CALL.search(line) and not line.startswith("def "):
                hits[(p.name, line)] += 1
                continue
            m = _SLICE.search(line)
            if line.startswith("def "):   # 함수 머리의 기본값은 상수 정의 줄이 이미 잡는다
                continue
            if (m and (m.group(2) or int(m.group(1)) >= 100)) or _CAP_REF.search(code) or _CAP_FN.search(line):
                hits[(p.name, line)] += 1
    return hits


# 찾은 줄의 분류표. (파일, 줄 내용) → (분류, 이유)
#   debt       = 저장 단계 자르기 — **고칠 목록**(2026-11-10 전수 조사표 번호). 허용이 아니다. 대표 승인 뒤 고치고 지운다.
#   genuine    = 진짜 외부 한도(자르기가 맞는 곳)
#   decision   = 판정·탐색에만 쓰고 저장은 원문
#   diagnostic = 리포트·오류 표본(저장 내용 아님)
#   display    = 사이트용 파생 스냅샷(원본은 DB 에 그대로)
ALLOWED: dict[tuple[str, str], tuple[str, str]] = {
    ('collect_eu_gmp_ncr.py', 'headline=headline[:240],'): ('debt', '#26 EU NCR 제목 240'),
    ('collect_fda_483.py', 'FDA483_EXCERPT_MAX_CHARS = 1500'): ('debt', '#5~9 FDA 483 (483 세션 담당 — 11-10 상세 1,200→8,000 상향·문장 끝 자르기, 상한 자체는 남음)'),
    ('collect_fda_483.py', 'FDA483_OBSERVATION_DETAIL_MAX_CHARS = 8000'): ('debt', '#5~9 FDA 483 (483 세션 담당 — 11-10 상세 1,200→8,000 상향·문장 끝 자르기, 상한 자체는 남음)'),
    ('collect_fda_483.py', 'FDA483_TEXT_MAX_CHARS = 200000'): ('debt', '#5~9 FDA 483 (483 세션 담당 — 11-10 상세 1,200→8,000 상향·문장 끝 자르기, 상한 자체는 남음)'),
    ('collect_fda_483.py', 'firm=company[:200],'): ('debt', '#5~9 FDA 483 (483 세션 담당 — 11-10 상세 1,200→8,000 상향·문장 끝 자르기, 상한 자체는 남음)'),
    ('collect_fda_483.py', 'headline=f"[{label}] {company or media_id}"[:240],'): ('debt', '#5~9 FDA 483 (483 세션 담당 — 11-10 상세 1,200→8,000 상향·문장 끝 자르기, 상한 자체는 남음)'),
    ('collect_fda_483.py', 'if len(text) >= FDA483_TEXT_MAX_CHARS:'): ('debt', '#5~9 FDA 483 (483 세션 담당 — 11-10 상세 1,200→8,000 상향·문장 끝 자르기, 상한 자체는 남음)'),
    ('collect_fda_483.py', 'return _cap_detail(normalize_483_redactions(detail), FDA483_OBSERVATION_DETAIL_MAX_CHARS)'): ('debt', '#5~9 FDA 483 (483 세션 담당 — 11-10 상세 1,200→8,000 상향·문장 끝 자르기, 상한 자체는 남음)'),
    ('collect_fda_483.py', 'return compact[m.start():][:FDA483_EXCERPT_MAX_CHARS].strip()'): ('debt', '#5~9 FDA 483 (483 세션 담당 — 11-10 상세 1,200→8,000 상향·문장 끝 자르기, 상한 자체는 남음)'),
    ('collect_fda_483.py', 'return t[:280].rstrip() + "...", t[280:].strip()'): ('debt', '#5~9 FDA 483 (483 세션 담당 — 11-10 상세 1,200→8,000 상향·문장 끝 자르기, 상한 자체는 남음)'),
    ('collect_fda_483.py', 'text, _status = _extract_pdf_text(pdf_bytes, max_chars=FDA483_TEXT_MAX_CHARS)'): ('debt', '#5~9 FDA 483 (483 세션 담당 — 11-10 상세 1,200→8,000 상향·문장 끝 자르기, 상한 자체는 남음)'),
    ('collect_fda_483.py', '★상한은 483 전용 FDA483_TEXT_MAX_CHARS(200000·≈74쪽)를 **기본**으로 쓴다 — 공유 PDF 엔진의'): ('debt', '#5~9 FDA 483 (483 세션 담당 — 11-10 상세 1,200→8,000 상향·문장 끝 자르기, 상한 자체는 남음)'),
    ('collect_hc.py', 'headline=f"[HC] {title}"[:240],'): ('debt', '#27 HC 제목 240'),
    ('collect_hc_inspection_backfill.py', 'headline=f"{establishment} — Health Canada GMP inspection ({rating_desc})"[:240],'): ('debt', '#27 HC 실사 제목 240'),
    ('collect_ich.py', 'MAX_TITLE_CHARS = 240'): ('debt', '#25 ICH 제목 240'),
    ('collect_ich.py', 'headline=headline[:MAX_TITLE_CHARS],'): ('debt', '#25 ICH 제목 240'),
    ('collect_ich.py', 'out.append(line[:MAX_TITLE_CHARS])'): ('debt', '#25 ICH 제목 240'),
    ('collect_intake.py', 'ECA_ARTICLE_EXCERPT_MAX_CHARS = 1200'): ('debt', '#10·11·13·14·15 WL 본문 1,500/30,000 · MHRA 30,000 · ECA/ISPE 1,200 · ISPE 800'),
    ('collect_intake.py', 'MHRA_ALERT_BODY_MAX_CHARS = WL_BODY_FULL_MAX_CHARS'): ('debt', '#10·11·13·14·15 WL 본문 1,500/30,000 · MHRA 30,000 · ECA/ISPE 1,200 · ISPE 800'),
    ('collect_intake.py', 'WL_BODY_FULL_MAX_CHARS = 30000'): ('debt', '#10·11·13·14·15 WL 본문 1,500/30,000 · MHRA 30,000 · ECA/ISPE 1,200 · ISPE 800'),
    ('collect_intake.py', 'WL_BODY_MAX_CHARS = 1500'): ('debt', '#10·11·13·14·15 WL 본문 1,500/30,000 · MHRA 30,000 · ECA/ISPE 1,200 · ISPE 800'),
    ('collect_intake.py', 'return _extract_wl_body_span(html_text, WL_BODY_FULL_MAX_CHARS)'): ('debt', '#10·11·13·14·15 WL 본문 1,500/30,000 · MHRA 30,000 · ECA/ISPE 1,200 · ISPE 800'),
    ('collect_intake.py', 'return _extract_wl_body_span(html_text, WL_BODY_MAX_CHARS)'): ('debt', '#10·11·13·14·15 WL 본문 1,500/30,000 · MHRA 30,000 · ECA/ISPE 1,200 · ISPE 800'),
    ('collect_intake.py', 'return fallback[:800].strip()'): ('debt', '#10·11·13·14·15 WL 본문 1,500/30,000 · MHRA 30,000 · ECA/ISPE 1,200 · ISPE 800'),
    ('collect_intake.py', 'return inner[:800].strip()'): ('debt', '#10·11·13·14·15 WL 본문 1,500/30,000 · MHRA 30,000 · ECA/ISPE 1,200 · ISPE 800'),
    ('collect_intake.py', 'return joined[:ECA_ARTICLE_EXCERPT_MAX_CHARS].strip()'): ('debt', '#10·11·13·14·15 WL 본문 1,500/30,000 · MHRA 30,000 · ECA/ISPE 1,200 · ISPE 800'),
    ('collect_intake.py', 'return text[:MHRA_ALERT_BODY_MAX_CHARS]'): ('debt', '#10·11·13·14·15 WL 본문 1,500/30,000 · MHRA 30,000 · ECA/ISPE 1,200 · ISPE 800'),
    ('collect_mfds_gmp_inspection.py', 'MAX_ATTACHMENT_BODY_CHARS = 6000'): ('debt', '#21~23 식약처 GMP 실사 첨부 12,000 · 지적 원문 6,000 · 본문 600'),
    ('collect_mfds_gmp_inspection.py', 'MAX_ATTACHMENT_TEXT_CHARS = 12000'): ('debt', '#21~23 식약처 GMP 실사 첨부 12,000 · 지적 원문 6,000 · 본문 600'),
    ('collect_mfds_gmp_inspection.py', 'attachment.deficiency_excerpt[:600],'): ('debt', '#21~23 식약처 GMP 실사 첨부 12,000 · 지적 원문 6,000 · 본문 600'),
    ('collect_mfds_gmp_inspection.py', 'return compact[m.start():][:MAX_ATTACHMENT_BODY_CHARS].strip()'): ('debt', '#21~23 식약처 GMP 실사 첨부 12,000 · 지적 원문 6,000 · 본문 600'),
    ('collect_mfds_gmp_inspection.py', 'return text[:MAX_ATTACHMENT_TEXT_CHARS], "hwpx-ok"'): ('debt', '#21~23 식약처 GMP 실사 첨부 12,000 · 지적 원문 6,000 · 본문 600'),
    ('collect_mfds_law.py', 'BODY_EXCERPT_LIMIT = 4000'): ('debt', '#24 식약처 법령 본문 4,000'),
    ('collect_mfds_law.py', 'if total_len + len(line) + 1 > BODY_EXCERPT_LIMIT:'): ('debt', '#24 식약처 법령 본문 4,000'),
    ('collect_mfds_law.py', 'remaining = max(BODY_EXCERPT_LIMIT - total_len - 4, 0)'): ('debt', '#24 식약처 법령 본문 4,000'),
    ('collect_mhra_gmp_ncr.py', 'headline=headline[:240],'): ('debt', '#26 MHRA NCR 제목 240'),
    ('collect_search.py', 'return truncate(best.strip(), 200)'): ('debt', '#4 Brave 검색 요약 200'),
    ('collect_search.py', 'return truncate(desc.strip(), 200)'): ('debt', '#4 Brave 검색 요약 200'),
    ('collect_who.py', '"outcome": _whopir_cut(outcome, WHOPIR_OUTCOME_MAX_CHARS),'): ('debt', '#16~20 WHO 본문 1,500 · 제목 240 · 업체 200 · WHOPIR 600/1,200/1,500 · 읽기 범위 120,000'),
    ('collect_who.py', 'MAX_TITLE_CHARS = 240'): ('debt', '#16~20 WHO 본문 1,500 · 제목 240 · 업체 200 · WHOPIR 600/1,200/1,500 · 읽기 범위 120,000'),
    ('collect_who.py', 'WHOPIR_EXCERPT_MAX_CHARS = 1500'): ('debt', '#16~20 WHO 본문 1,500 · 제목 240 · 업체 200 · WHOPIR 600/1,200/1,500 · 읽기 범위 120,000'),
    ('collect_who.py', 'WHOPIR_OUTCOME_MAX_CHARS = 1200'): ('debt', '#16~20 WHO 본문 1,500 · 제목 240 · 업체 200 · WHOPIR 600/1,200/1,500 · 읽기 범위 120,000'),
    ('collect_who.py', 'WHOPIR_SECTION_MAX_CHARS = 600'): ('debt', '#16~20 WHO 본문 1,500 · 제목 240 · 업체 200 · WHOPIR 600/1,200/1,500 · 읽기 범위 120,000'),
    ('collect_who.py', 'WHOPIR_TEXT_MAX_CHARS = 120_000'): ('debt', '#16~20 WHO 본문 1,500 · 제목 240 · 업체 200 · WHOPIR 600/1,200/1,500 · 읽기 범위 120,000'),
    ('collect_who.py', 'body=_clean(desc)[:1500],'): ('debt', '#16~20 WHO 본문 1,500 · 제목 240 · 업체 200 · WHOPIR 600/1,200/1,500 · 읽기 범위 120,000'),
    ('collect_who.py', 'firm=manuf[:200],'): ('debt', '#16~20 WHO 본문 1,500 · 제목 240 · 업체 200 · WHOPIR 600/1,200/1,500 · 읽기 범위 120,000'),
    ('collect_who.py', 'firm=t[:200],'): ('debt', '#16~20 WHO 본문 1,500 · 제목 240 · 업체 200 · WHOPIR 600/1,200/1,500 · 읽기 범위 120,000'),
    ('collect_who.py', 'headline=f"[WHO NOC] {t}"[:MAX_TITLE_CHARS],'): ('debt', '#16~20 WHO 본문 1,500 · 제목 240 · 업체 200 · WHOPIR 600/1,200/1,500 · 읽기 범위 120,000'),
    ('collect_who.py', 'headline=f"[WHOPIR] {manuf}"[:MAX_TITLE_CHARS],'): ('debt', '#16~20 WHO 본문 1,500 · 제목 240 · 업체 200 · WHOPIR 600/1,200/1,500 · 읽기 범위 120,000'),
    ('collect_who.py', 'headline=title[:MAX_TITLE_CHARS],'): ('debt', '#16~20 WHO 본문 1,500 · 제목 240 · 업체 200 · WHOPIR 600/1,200/1,500 · 읽기 범위 120,000'),
    ('collect_who.py', 'return _whopir_squeeze(t[i:])[:WHOPIR_EXCERPT_MAX_CHARS].strip()'): ('debt', '#16~20 WHO 본문 1,500 · 제목 240 · 업체 200 · WHOPIR 600/1,200/1,500 · 읽기 범위 120,000'),
    ('collect_who.py', 'return compact[start:][:WHOPIR_EXCERPT_MAX_CHARS].strip()'): ('debt', '#16~20 WHO 본문 1,500 · 제목 240 · 업체 200 · WHOPIR 600/1,200/1,500 · 읽기 범위 120,000'),
    ('collect_who.py', 'seg = _whopir_cut(body[e:end], WHOPIR_SECTION_MAX_CHARS)'): ('debt', '#16~20 WHO 본문 1,500 · 제목 240 · 업체 200 · WHOPIR 600/1,200/1,500 · 읽기 범위 120,000'),
    ('collect_who.py', 'text, status = _extract_pdf_text(data, max_chars=WHOPIR_TEXT_MAX_CHARS)'): ('debt', '#16~20 WHO 본문 1,500 · 제목 240 · 업체 200 · WHOPIR 600/1,200/1,500 · 읽기 범위 120,000'),
    ('findings_extractors.py', '_WL_BLOCK_CHAR_CAP = 6000'): ('debt', '#12 WL 위반 조각 6,000자'),
    ('findings_extractors.py', 'if len(block) <= _WL_BLOCK_CHAR_CAP:'): ('debt', '#12 WL 위반 조각 6,000자'),
    ('findings_extractors.py', 'sentence_cut = _truncate_at_sentence(block, _WL_BLOCK_CHAR_CAP)'): ('debt', '#12 WL 위반 조각 6,000자'),
    ('findings_extractors.py', 'truncated = block[:_WL_BLOCK_CHAR_CAP]'): ('debt', '#12 WL 위반 조각 6,000자'),
    ('grm_notion.py', '"rich_text": _rich_text(truncate(item.raw_excerpt, 200))'): ('debt', '#1 Notion 칸 1,900자 일괄 절단(_rich_text 가 이미 나눠 담는다) — truncate 제거'),
    ('grm_notion.py', '"rich_text": _rich_text(truncate(item.search_query, NOTION_RICH_TEXT_CHUNK))'): ('debt', '#1 Notion 칸 1,900자 일괄 절단(_rich_text 가 이미 나눠 담는다) — truncate 제거'),
    ('grm_notion.py', 'PROP_HEADLINE: {"rich_text": _rich_text(truncate(item.headline, NOTION_RICH_TEXT_CHUNK))},'): ('debt', '#1 Notion 칸 1,900자 일괄 절단(_rich_text 가 이미 나눠 담는다) — truncate 제거'),
    ('grm_notion.py', 'name = f"{prefix} {item.document_id} — {truncate(item.firm or item.headline, 100)}"'): ('debt', '#1 Notion 칸 1,900자 일괄 절단(_rich_text 가 이미 나눠 담는다) — truncate 제거'),
    ('grm_notion.py', 'name = f"{prefix} {item.document_id} — {truncate(item.headline, 100)}"'): ('debt', '#1 Notion 칸 1,900자 일괄 절단(_rich_text 가 이미 나눠 담는다) — truncate 제거'),
    ('grm_notion.py', 'props[PROP_BODY] = {"rich_text": _rich_text(truncate(item.body, NOTION_RICH_TEXT_CHUNK))}'): ('debt', '#1 Notion 칸 1,900자 일괄 절단(_rich_text 가 이미 나눠 담는다) — truncate 제거'),
    ('grm_notion.py', 'props[PROP_DISTRIBUTION] = {"rich_text": _rich_text(truncate(item.distribution, NOTION_RICH_TEXT_CHUNK))}'): ('debt', '#1 Notion 칸 1,900자 일괄 절단(_rich_text 가 이미 나눠 담는다) — truncate 제거'),
    ('grm_notion.py', 'props[PROP_FIRM] = {"rich_text": _rich_text(truncate(item.firm, NOTION_RICH_TEXT_CHUNK))}'): ('debt', '#1 Notion 칸 1,900자 일괄 절단(_rich_text 가 이미 나눠 담는다) — truncate 제거'),
    ('grm_notion.py', 'props[PROP_SITE_COUNTRY] = {"rich_text": _rich_text(truncate(item.site_country, NOTION_RICH_TEXT_CHUNK))}'): ('debt', '#1 Notion 칸 1,900자 일괄 절단(_rich_text 가 이미 나눠 담는다) — truncate 제거'),
    ('backfill_wl_truncation_restore.py', 'DEFAULT_MAX_OLD_CHARS = 500'): ('decision', '옛 480자 절단 행을 찾는 탐지 임계'),
    ('backfill_wl_truncation_restore.py', 'help=f"후보 상한 — 이 길이 이하이고 \'…\' 로 끝나는 행(기본 {DEFAULT_MAX_OLD_CHARS}).")'): ('decision', '옛 480자 절단 행을 찾는 탐지 임계'),
    ('backfill_wl_truncation_restore.py', 'max_old_chars: int = DEFAULT_MAX_OLD_CHARS'): ('decision', '옛 480자 절단 행을 찾는 탐지 임계'),
    ('backfill_wl_truncation_restore.py', 'max_old_chars: int = DEFAULT_MAX_OLD_CHARS,'): ('decision', '옛 480자 절단 행을 찾는 탐지 임계'),
    ('backfill_wl_truncation_restore.py', 'p.add_argument("--max-old-chars", type=int, default=DEFAULT_MAX_OLD_CHARS,'): ('decision', '옛 480자 절단 행을 찾는 탐지 임계'),
    ('collect_deficiency_examples.py', 'ent["sample"] = [{"locator": r.locator, "statement": r.statement[:160], "clauses": r.clauses[:3],'): ('diagnostic', '실행 리포트 표본'),
    ('collect_deficiency_examples.py', 'found.append({"agency": agency, "lang": lang, "url": href, "label": label[:200]})'): ('diagnostic', '자료 목록 탐색 결과 라벨(리포트용)'),
    ('collect_fda_inspections.py', 'snippet = json.dumps(payload, ensure_ascii=False)[:300]'): ('diagnostic', '오류 진단 표본'),
    ('collect_ich.py', 'MAX_LINE_CHARS = 140'): ('decision', '140자 넘는 줄은 제목 후보가 아니라고 거르는 판정'),
    ('collect_ich.py', 'if len(line) > MAX_LINE_CHARS:'): ('decision', '140자 넘는 줄은 제목 후보가 아니라고 거르는 판정'),
    ('collect_intake.py', 'listing_health["detail"] = truncate(f"{type(e).__name__}: {e}", 160)'): ('diagnostic', '건강 점검 오류 문구'),
    ('collect_intake.py', 'self.near_miss_samples.append((source, label, (blob or "")[:110]))'): ('diagnostic', '근접 탈락 표본(리포트)'),
    ('collect_rum_analytics.py', 'lines.append(json.dumps(errors, ensure_ascii=False, indent=2)[:2000])'): ('diagnostic', '오류 리포트'),
    ('collect_who.py', '_WHOPIR_AUTH_MAX_CHARS = 80'): ('decision', '80자 넘으면 기관명이 아니라고 거르는 판정'),
    ('collect_who.py', 'kind = ("reliance" if (_WHOPIR_RELIANCE_RE.search(body[:400])'): ('decision', '보고서 종류 정규식 검사 범위'),
    ('collect_who.py', 'return auth if 0 < len(auth) <= _WHOPIR_AUTH_MAX_CHARS else ""'): ('decision', '80자 넘으면 기관명이 아니라고 거르는 판정'),
    ('deficiency_cases_refresh.py', '"acat": " ".join(_text(r.get("agency_category")).split())[:300],'): ('display', '사이트용 공개 JSON 스냅샷 — 원본은 deficiency_examples 에 그대로'),
    ('deficiency_cases_refresh.py', '"no": _text(r.get("item_no"))[:40], "loc": _text(r.get("locator"))[:120],'): ('display', '사이트용 공개 JSON 스냅샷 — 원본은 deficiency_examples 에 그대로'),
    ('findings_extractors.py', '_WL_CLASSIFY_CHAR_CAP = 480'): ('decision', '분류기 입력 전용(저장은 원문)'),
    ('findings_extractors.py', 'if len(block) <= _WL_CLASSIFY_CHAR_CAP:'): ('decision', '분류기 입력 전용(저장은 원문)'),
    ('findings_extractors.py', 'truncated = block[:_WL_CLASSIFY_CHAR_CAP]'): ('decision', '분류기 입력 전용(저장은 원문)'),
    ('grm_notion.py', 'props[PROP_TYPE_CLASS] = _select(item.type_or_class[:100])'): ('genuine', 'Notion select 옵션 이름 100자 한도'),
}


class StorageTruncationGuardTest(unittest.TestCase):
    def test_no_new_storage_truncation(self):
        hits = scan()
        allowed = set(ALLOWED)
        new = sorted(k for k in hits if k not in allowed)
        msg = ("\n저장 단계에서 글자수를 자르는 새 코드가 생겼습니다. 원칙: 저장할 때는 자르지 않고 화면에서만 줄인다.\n"
               "진짜 외부 한도(예: Notion 글 한 덩어리 2,000자)는 자르지 말고 나눠 담으세요. 꼭 필요하면 "
               "tests/test_no_storage_truncation.py 의 ALLOWED 에 분류(genuine·decision·diagnostic·display)와 이유를 적으세요.\n"
               + "\n".join(f"  {f}: {l}" for f, l in new))
        self.assertEqual(new, [], msg)

    def test_report_remaining_debt(self):
        """고쳐서 없어진 항목은 실패가 아니라 안내만 한다(다른 세션이 상한을 걷어도 이 시험이 막지 않게)."""
        hits = scan()
        debt = {k for k, (cat, _) in ALLOWED.items() if cat == "debt"}
        gone = sorted(k for k in ALLOWED if k not in hits)
        if gone:
            print(f"\n[storage-truncation] 고쳐져 목록에서 지워도 되는 항목 {len(gone)}개:", *gone, sep="\n  ")
        print(f"\n[storage-truncation] 남은 저장 단계 자르기(고칠 목록) {sum(1 for k in debt if k in hits)}줄")

    def test_scanner_catches_the_known_patterns(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            Path(d, "collect_x.py").write_text(
                "FOO_MAX_CHARS = 1200\n"
                "detail = text[:FOO_MAX_CHARS]\n"
                "headline = title[:240]\n"
                "body = truncate(desc, 1900)\n"
                "log('WARN', f'fetch 실패 {str(e)[:120]}')\n"
                "raise RuntimeError(r.text[:300])\n"
                "items = rows[:20]\n"
                "return _cap_detail(clean(d), FOO_MAX_CHARS)\n"
                "body = clip_text(desc, 1200)\n"
                "BAR_MIN_CHARS = 24\n"
                "if len(x) < BAR_MIN_CHARS:\n"
                "def f(t, max_chars=FOO_MAX_CHARS):\n", encoding="utf-8")
            got = sorted(l for _, l in scan(Path(d)))
        self.assertEqual(got, ["FOO_MAX_CHARS = 1200", "body = clip_text(desc, 1200)", "body = truncate(desc, 1900)",
                               "detail = text[:FOO_MAX_CHARS]", "headline = title[:240]",
                               "return _cap_detail(clean(d), FOO_MAX_CHARS)"])


if __name__ == "__main__":
    unittest.main()
