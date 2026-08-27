#!/usr/bin/env python3
"""findings_docs_refresh.py — 문서 단위 페이지 정본 생성기.

무네트워크. 이 스크립트가 만드는 것은 **실명 업체의 규제 기록 페이지 3천 장**이라, 조용히
틀리면 피해가 우리 사이트에 그치지 않는다. 그래서 게이트를 값으로 고정한다:

  · 발행일 없는 문서는 페이지를 만들지 않는다(언제인지 모르는 지적은 현재 상태로 오독된다).
  · 원문 링크 없는 문서는 만들지 않는다(원문으로 못 보내면 우리 주장만 남는다).
  · 본문을 자르지 않는다(원문 절단은 이미 두 번 데인 자리다).
  · 제외 사유를 센다(침묵하면 "전부 다뤘다"로 읽힌다).
"""
from __future__ import annotations

import io
import collections
import json
import shutil
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import findings_docs_refresh as fdr  # noqa: E402


def _finding(fid="f1", text_ko="지적 본문입니다.", label="일탈/CAPA/조사"):
    return {"finding_id": fid, "finding_text_ko": text_ko,
            "category_code": "deviation_capa", "category_label_ko": label}


def _doc(doc_id="abc123", n=3, **kw):
    base = {
        "document_id": doc_id,
        "agency": "FDA",
        "source": "FDA 483",
        "firm_name": "Acme Pharma",
        "published_date": "2026-01-15",
        "evidence_url": "https://www.fda.gov/x",
        "findings": [_finding(f"f{i}") for i in range(n)],
    }
    base.update(kw)
    return base


class DocumentViewGateTest(unittest.TestCase):
    def setUp(self):
        self.reject: Counter = Counter()

    def _view(self, doc, min_findings=3):
        return fdr.document_view(doc, min_findings=min_findings, reject=self.reject)

    def test_accepts_well_formed_document(self):
        view = self._view(_doc())
        self.assertIsNotNone(view)
        self.assertEqual(view["slug"], "abc123")
        self.assertEqual(len(view["findings"]), 3)
        self.assertEqual(view["categories"], ["일탈/CAPA/조사"])

    def test_rejects_missing_published_date(self):
        """언제인지 모르는 지적은 현재 상태로 오독된다."""
        self.assertIsNone(self._view(_doc(published_date="")))
        self.assertEqual(self.reject["발행일 없음"], 1)

    def test_rejects_malformed_date(self):
        self.assertIsNone(self._view(_doc(published_date="2026/01/15")))
        self.assertEqual(self.reject["발행일 없음"], 1)

    def test_rejects_missing_evidence_url(self):
        """원문으로 못 보내는 페이지는 우리 주장만 남는다."""
        self.assertIsNone(self._view(_doc(evidence_url="")))
        self.assertEqual(self.reject["원문 링크 없음"], 1)

    def test_rejects_non_http_evidence_url(self):
        self.assertIsNone(self._view(_doc(evidence_url="javascript:alert(1)")))
        self.assertEqual(self.reject["원문 링크 없음"], 1)

    def test_unsafe_document_id_transforms_instead_of_rejecting(self):
        """[2026-08-27] 기각 → 결정론 변환. 종전 기각 규칙이 MHRA 를 전량 침묵
        소실시켰다(문서 id 가 기관 원문 형식 "Insp GMP/GDP/IMP …" 라 공백·슬래시가
        항상 들어 있다) — id 형식은 기관이 정하므로 기각 규칙이 곧 기관 차별이 된다."""
        import re as _re
        for raw in ("a/b", "a b", "../etc", "한글아이디"):
            self.reject.clear()
            view = self._view(_doc(doc_id=raw))
            self.assertIsNotNone(view, raw)
            self.assertRegex(view["slug"], r"^[A-Za-z0-9._-]{1,120}$")
            self.assertEqual(view["document_id"], raw, "원본 id 는 무변형 보존")
            # 같은 id 는 언제나 같은 슬러그(재실행 안정) — 해시 접미가 그 근거다.
            self.assertEqual(view["slug"], fdr._safe_slug(raw))
        # 빈 id 만 기각으로 남는다.
        self.reject.clear()
        self.assertIsNone(self._view(_doc(doc_id="")))
        self.assertEqual(self.reject["URL 로 쓸 수 없는 문서 id"], 1)

    def test_safe_document_id_slug_is_unchanged(self):
        """안전한 기존 id 는 무변형 — 기존 문서 URL 이 바뀌면 안 된다(링크 파손)."""
        view = self._view(_doc(doc_id="3015467542.20240119"))
        self.assertEqual(view["slug"], "3015467542.20240119")


    def test_rejects_numeric_or_empty_firm_name(self):
        """★누구에 대한 기록인지 말할 수 없는 페이지는 만들지 않는다.

        실측 FDA 2건이 `firm_name` 이 7자리 숫자뿐이라(수집 원천의 결손) 제목이
        "1021343 — FDA 483 지적사항"이 되고 있었다.
        """
        for bad in ("1021343", "  ", ""):
            self.reject.clear()
            self.assertIsNone(self._view(_doc(firm_name=bad)), repr(bad))
            self.assertEqual(self.reject["업체명 없음(숫자뿐이거나 빈 값)"], 1)

    def test_keeps_names_that_merely_contain_digits(self):
        self.assertIsNotNone(self._view(_doc(firm_name="3M Health Care")))

    def test_view_rejects_only_zero_korean_findings(self):
        """[2026-08-27] 두께 판정은 document_view 를 떠났다.

        그 소스가 임계를 넘는 문서를 하나라도 갖는지는 **소스 전체 분포**를 봐야 알 수
        있으므로 수집이 끝난 뒤 apply_thickness_gate 가 판정한다. 여기서는 임계와 무관한
        절대 조건(국문 본문 0건)만 남는다."""
        self.assertIsNone(self._view(_doc(n=0)))
        self.assertEqual(self.reject["국문 지적 0건"], 1)
        # 1~2건 문서도 이 단계는 통과한다(두께는 뒤에서 판정).
        self.assertIsNotNone(self._view(_doc(n=2)))
        self.assertEqual(fdr.DEFAULT_MIN_FINDINGS, 3,
                         "임계는 3 유지 — 소스를 통째로 지우는 경우만 게이트가 면제한다")

    def test_findings_without_korean_text_do_not_count(self):
        doc = _doc(n=0)
        doc["findings"] = [_finding("b", text_ko=""), _finding("c", text_ko="  ")]
        self.assertIsNone(self._view(doc))
        self.assertEqual(self.reject["국문 지적 0건"], 1)

    def test_text_is_verbatim(self):
        long_text = "무균공정 " * 400
        doc = _doc(n=0)
        doc["findings"] = [_finding(str(i), text_ko=long_text) for i in range(3)]
        view = self._view(doc)
        self.assertEqual(view["findings"][0]["text_ko"], long_text.strip())

    def test_categories_are_deduped_in_order(self):
        doc = _doc(n=0)
        doc["findings"] = [_finding("a", label="설비/시설"),
                           _finding("b", label="일탈/CAPA/조사"),
                           _finding("c", label="설비/시설")]
        self.assertEqual(self._view(doc)["categories"], ["설비/시설", "일탈/CAPA/조사"])


class CollectDocumentsTest(unittest.TestCase):
    def setUp(self):
        self._real = fdr.post_search
        # 재시도 백오프는 실제로 자면 스위트가 그만큼 느려진다(영구 실패 한 페이지당
        # 12초). 대기 **여부**가 아니라 재시도 **결과**를 재는 것이 이 클래스의 목적이라
        # 잠은 무력화하고, 호출 횟수만 기록해 둔다.
        self._real_sleep = fdr.time.sleep
        self.slept = []
        fdr.time.sleep = self.slept.append

    def tearDown(self):
        fdr.post_search = self._real
        fdr.time.sleep = self._real_sleep

    def _stub(self, pages, docs_by_page, fail_pages=(), fail_times=None):
        """fail_pages = 영구 실패. fail_times = {페이지: 실패 횟수}(그 뒤엔 성공)."""
        remaining = dict(fail_times or {})
        self.calls = collections.Counter()

        def fake(base_url, anon_key, payload, timeout=120):
            page = payload["p_page"]
            self.calls[page] += 1
            if page in fail_pages:
                raise RuntimeError("boom")
            if remaining.get(page, 0) > 0:
                remaining[page] -= 1
                raise RuntimeError("500 Server Error: statement timeout")
            return {"pages": pages, "documents": docs_by_page.get(page, [])}
        fdr.post_search = fake

    def test_transient_page_failure_is_retried_not_dropped(self):
        """★일시적 실패로 문서 100건이 사라지면 안 된다.

        실측(08-27 실행): `findings_search` 가 statement timeout 으로 62페이지 중 9장을
        토했고, 재시도가 없어 그 페이지들의 문서가 통째로 빠졌다 — 문서 3,301 → 2,718
        (-17.7%). 축소 게이트가 막아 사고는 안 났지만, 직전 08-22 실행은 1페이지만
        실패해 게이트를 안 넘겼고 **문서 약 100건을 흘린 채 그대로 머지됐다**.

        원인은 항구적이지 않다 — 이 호출의 평상시 소요는 약 0.75초로 한계와 여유가 있고,
        62번 연달아 칠 때 부하가 몰리는 구간에서만 넘어간다. 그러니 물러섰다 다시 친다."""
        self._stub(3, {p: [_doc(f"doc{p}")] for p in range(1, 4)},
                   fail_times={2: 2})   # 2페이지가 두 번 실패한 뒤 성공
        docs, reject = fdr.collect_documents("u", "k", min_findings=3, page_size=10,
                                             log=lambda m: None)
        self.assertEqual(len(docs), 3, "재시도로 회복된 페이지의 문서가 빠졌다")
        self.assertEqual(self.calls[2], 3, "2페이지를 세 번 쳤어야 한다")
        self.assertEqual([k for k in reject if "페이지 조회 실패" in k], [],
                         "회복된 페이지를 실패로 세면 안 된다")
        self.assertTrue(self.slept, "재시도 사이에 물러서지 않았다")

    def test_permanent_failure_still_counted_after_retries(self):
        """항구적 실패는 재시도 뒤에도 **반드시 사유로 센다** — 조용히 사라지면 축소
        게이트만이 마지막 방어선이 되고, 그 게이트는 10% 미만을 못 잡는다."""
        self._stub(3, {p: [_doc(f"doc{p}")] for p in range(1, 4)}, fail_pages={2})
        docs, reject = fdr.collect_documents("u", "k", min_findings=3, page_size=10,
                                             log=lambda m: None)
        self.assertEqual(len(docs), 2)
        self.assertEqual(self.calls[2], fdr.PAGE_ATTEMPTS)
        self.assertEqual(sum(v for k, v in reject.items() if "페이지 조회 실패" in k), 1)

    def test_zero_documents_aborts(self):
        """RPC 장애를 '문서가 없다'로 커밋하면 다음 렌더가 페이지 수천 장을 지운다.

        [2026-08-27] 두께 미달로는 0건을 만들 수 없다 — 그 소스가 통째로 지워지는
        상황이면 면제되기 때문이다. 그래서 두께와 무관한 사유(국문 본문 0건)로 0건을
        만든다. 가드가 재는 것은 그대로다."""
        self._stub(1, {1: [_doc(n=0)]})
        with self.assertRaises(SystemExit):
            fdr.collect_documents("u", "k", min_findings=3, page_size=10,
                                  log=lambda m: None)

    def test_majority_exemption_aborts(self):
        """면제가 과반이면 소스 성질이 아니라 상류 데이터 모양이 바뀐 것이다.

        이 안전장치가 없으면, 상류가 findings 배열을 잘라 보낼 때 모든 소스가 얇아져
        전량 면제되고 두께 게이트가 사라진 스냅샷이 조용히 커밋된다(주간 축소 게이트는
        **증가**를 못 잡는다)."""
        reject = collections.Counter()
        docs = ([{"agency": "A", "findings": [0]}] * 3
                + [{"agency": "B", "findings": [0]}] * 3
                + [{"agency": "C", "findings": [0] * 5}] * 3)
        with self.assertRaises(SystemExit):
            fdr.apply_thickness_gate(docs, min_findings=3, reject=reject)

    def test_sorted_by_document_id_for_small_diffs(self):
        self._stub(1, {1: [_doc("zzz"), _doc("aaa"), _doc("mmm")]})
        docs, _ = fdr.collect_documents("u", "k", min_findings=3, page_size=10,
                                        log=lambda m: None)
        self.assertEqual([d["document_id"] for d in docs], ["aaa", "mmm", "zzz"])

    def test_page_failure_is_isolated(self):
        self._stub(5, {p: [_doc(f"doc{p}")] for p in range(1, 6)}, fail_pages={3})
        docs, _ = fdr.collect_documents("u", "k", min_findings=3, page_size=10,
                                        log=lambda m: None)
        self.assertEqual(len(docs), 4)

    def test_too_many_page_failures_aborts(self):
        self._stub(4, {p: [_doc(f"doc{p}")] for p in range(1, 5)},
                   fail_pages={2, 3, 4})
        with self.assertRaises(SystemExit):
            fdr.collect_documents("u", "k", min_findings=3, page_size=10,
                                  log=lambda m: None)


class CommittedDataTest(unittest.TestCase):
    """커밋된 정본이 게이트를 실제로 지키는지 — 손으로 고친 데이터가 섞이면 잡힌다."""

    @classmethod
    def setUpClass(cls):
        path = ROOT / "web" / "data" / "findings_docs.json"
        if not path.exists():
            raise unittest.SkipTest("findings_docs.json 미존재")
        cls.data = json.loads(path.read_text(encoding="utf-8"))

    def test_schema_and_totals(self):
        self.assertEqual(self.data["schema_version"], fdr.SCHEMA_VERSION)
        self.assertEqual(self.data["totals"]["documents"], len(self.data["documents"]))
        self.assertEqual(self.data["totals"]["findings"],
                         sum(len(d["findings"]) for d in self.data["documents"]))

    def test_every_document_meets_the_gates(self):
        floor = self.data["min_findings"]
        # [2026-08-27] 임계가 통째로 지우는 소스는 면제된다 — 그 소스는 최대 지적 수가
        # 임계 미만이라는 사실 자체가 면제 근거다(파일 상단 docstring). 여기서도 같은
        # 규칙으로 판정한다: 손열거를 두면 새 소스가 들어올 때 이 가드가 먼저 낡는다.
        peak = collections.Counter()
        for d in self.data["documents"]:
            peak[d["agency"]] = max(peak[d["agency"]], len(d["findings"]))
        exempt = {a for a, mx in peak.items() if mx < floor}
        self.assertLess(len(exempt) * 2, len(peak),
                        f"면제가 과반이다({exempt}) — 데이터 모양이 바뀌었을 수 있다")
        for d in self.data["documents"]:
            self.assertFalse(d["firm_name"].strip().isdigit(),
                             f'업체명이 숫자뿐: {d["slug"]}')
            self.assertRegex(d["slug"], r"^[A-Za-z0-9._-]{1,120}$")
            self.assertRegex(d["published_date"], r"^\d{4}-\d{2}-\d{2}$")
            self.assertTrue(d["evidence_url"].startswith(("http://", "https://")))
            if d["agency"] not in exempt:
                self.assertGreaterEqual(len(d["findings"]), floor, d["slug"])
            self.assertTrue(d["firm_name"].strip(), d["slug"])

    def test_slugs_unique_and_sorted(self):
        slugs = [d["slug"] for d in self.data["documents"]]
        self.assertEqual(len(slugs), len(set(slugs)), "문서 슬러그 중복")
        self.assertEqual(slugs, sorted(slugs), "정렬이 흐트러지면 주간 diff 가 전 파일이 된다")

    def test_exclusions_are_recorded(self):
        """제외를 침묵시키지 않는다 — 사유별 건수가 남아 있어야 한다."""
        self.assertTrue(self.data["excluded"], "제외 기록이 비어 있다")
        for ex in self.data["excluded"]:
            self.assertTrue(ex["reason"].strip())
            self.assertGreater(ex["documents"], 0)

    # ★상류(수집·번역 단계)에 남아 있는 480자 절단의 잔존분 기준선.
    #
    # 이 스크립트는 본문을 자르지 않는다(위 test_text_is_verbatim 이 그것을 고정한다).
    # 그런데 커밋 데이터에는 말줄임표로 끝나는 본문이 1,337건 있고, 전수 확인 결과 **전부
    # FDA 경고서한**이며 영문 원문 길이가 470~485자에 몰려 문장 중간에서 끊긴다 — 즉
    # `grm-wl-fragment-truncation`(#653)에서 고친 그 결함의 **소급되지 않은 잔존분**이다
    # (#653 은 소급을 26건에만 적용했다). 483 은 10,162건 중 4건뿐이라 사실상 깨끗하다.
    #
    # 여기서 0 을 요구하면 이 PR 이 남의 결함에 발목 잡히고, 검사를 지우면 그 결함이 다시
    # 조용해진다. 그래서 **기준선으로 고정해 표면화**한다 — 늘면 실패하고, 상류가 고쳐지면
    # 이 숫자를 내리라고 알려준다.
    # [2026-08-27] 1337 → 1338. EU·영국 GMP 비준수 86건이 면제로 편입되며 1건 늘었다
    # (모집단 정의가 넓어진 만큼의 정직한 증가 — 상류 절단 자체는 그대로다).
    BASELINE_UPSTREAM_TRUNCATED = 1338

    def test_upstream_truncation_does_not_grow(self):
        bad = [f["finding_id"] for d in self.data["documents"] for f in d["findings"]
               if f["text_ko"].rstrip().endswith(("…", "..."))]
        self.assertLessEqual(
            len(bad), self.BASELINE_UPSTREAM_TRUNCATED,
            f"상류 절단이 늘었다({len(bad)} > {self.BASELINE_UPSTREAM_TRUNCATED}): {bad[:5]}")
        if len(bad) < self.BASELINE_UPSTREAM_TRUNCATED:
            print(f"\n[NOTICE] 상류 절단 {len(bad)}건 — 기준선"
                  f"({self.BASELINE_UPSTREAM_TRUNCATED})을 내리세요.")


class NarrowConsoleEncodingTest(unittest.TestCase):
    """요약 출력이 죽어도 수천 건을 긁은 산출물을 잃지 않는다.

    findings_facets_refresh.py 와 같은 계열의 결함(제외 요약의 em-dash + 뒤에 오는
    파일 쓰기)이 이 파일에도 그대로 있었다. 형제 스크립트는 함께 고치고 함께 잠근다.
    """

    def setUp(self):
        self._real = fdr.collect_documents
        self.tmp = tempfile.mkdtemp()
        self.out = Path(self.tmp) / "sub" / "findings_docs.json"

        def fake(base_url, anon_key, *, min_findings, page_size, log):
            # 제외 사유에 em-dash 가 붙어 나가는 경로를 그대로 태운다.
            return ([_doc(n=3)], Counter({"발행일 없음": 2}))
        fdr.collect_documents = fake

    def tearDown(self):
        fdr.collect_documents = self._real
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, stream, extra=()):
        real = sys.stdout
        sys.stdout = stream
        try:
            return fdr.main(["--supabase-url", "https://x.supabase.co",
                             "--supabase-anon-key", "k", "--out", str(self.out), *extra])
        finally:
            sys.stdout = real

    def test_summary_survives_cp949_stdout_and_file_is_written(self):
        buf = io.BytesIO()
        rc = self._run(io.TextIOWrapper(buf, encoding="cp949", errors="strict"))
        self.assertEqual(rc, 0)
        self.assertTrue(self.out.exists(), "요약 출력이 죽어 산출물이 유실됐다")

    def test_file_is_written_before_the_summary_log(self):
        class Exploding(io.StringIO):
            def write(self, s):                      # noqa: D102
                if "제외" in s:
                    raise RuntimeError("요약 출력 실패")
                return super().write(s)

        with self.assertRaises(RuntimeError):
            self._run(Exploding())
        self.assertTrue(self.out.exists(),
                        "요약이 죽자 산출물이 함께 사라졌다 — 쓰기가 로그보다 뒤에 있다")

    def test_dry_run_still_writes_nothing(self):
        rc = self._run(io.StringIO(), extra=["--dry-run"])
        self.assertEqual(rc, 0)
        self.assertFalse(self.out.exists(), "dry-run 인데 파일을 썼다")


if __name__ == "__main__":                                       # pragma: no cover
    unittest.main()
