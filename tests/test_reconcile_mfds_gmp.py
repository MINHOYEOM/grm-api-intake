"""수집 완전성 N1 — 식약처 GMP 실사(nedrug)를 매일 원천 대조에 더한다.

합격 조건: 원천 목록에 있는데 DB 에 없는 실사 문서(빠짐 주입)는 반드시 '놓침'으로 찍히고(경보 이슈 계획 open),
번호 지정 재수집으로 DB 에 들어온 다음 실행은 '정상'으로 닫힌다. 원천에 못 닿으면 정상으로 닫히지 않는다.
"""
from __future__ import annotations

import unittest
from datetime import date
from unittest.mock import patch

import collect_mfds_gmp_inspection as g
import source_reconcile as sr

RUN = date(2026, 11, 26)
WIN = (date(2026, 11, 19), RUN)
SPEC = sr.SPEC_BY_KEY["mfds_gmp"]


def _row(seq: int, doc: str, maker: str, registered: str, product: str = "완제") -> str:
    cells = [str(seq), "사후", product, "대한민국", maker, "경기도 화성시", "2026-11-01", "2026-11-03"]
    tds = "".join(f"<td>{c}</td>" for c in cells)
    return (f"<tr>{tds}<td><a href=\"#\" onclick=\"downFile('{doc}')\">첨부</a></td>"
            f"<td>{registered}</td></tr>")


def _page(*rows: str) -> bytes:
    return ("<html><body><table><tr><th>No</th></tr>" + "".join(rows) + "</table></body></html>").encode("utf-8")


PAGE1 = _page(
    _row(3, "DOC_A", "가나제약(주)", "2026-11-25"),
    _row(2, "DOC_B", "다라바이오(주)", "2026-11-20"),  # 게시 6일째 — 미뤄짐 유예(3일) 밖
    _row(1, "DOC_GAS", "한빛가스(주)", "2026-11-23", product="의료용 가스"),
)
PAGE2 = _page(_row(0, "DOC_OLD", "마바제약", "2026-11-01"))


def _fetch(pages):
    it = iter(pages)

    def fake_get(url, **_kw):
        val = next(it)
        if isinstance(val, Exception):
            raise val
        return val
    with patch.object(g, "_get_bytes", side_effect=fake_get):
        return sr.fetch_mfds_gmp_inspection(*WIN, sleep=lambda _s: None)


class FetchTest(unittest.TestCase):
    def test_lists_window_docs_with_collector_keys_and_rules(self):
        out = _fetch([PAGE1, PAGE2])
        ids = [d.document_id for d in out.docs]
        self.assertEqual(ids, ["gmpinspect-DOC_A", "gmpinspect-DOC_B"])     # 창 밖·의료용 가스 제외
        self.assertFalse(out.partial)
        self.assertTrue(any("의료용 가스" in n for n in out.notes))

    def test_medical_gas_rule_is_the_collector_function(self):
        raw = {"manufacturer": "한빛가스(주)", "address": "", "product_type": "의료용 가스"}
        self.assertTrue(g._is_medical_gas_gmp_noise(raw))

    def test_first_page_failure_is_source_unreachable(self):
        out = sr.fetch_all([SPEC], {"mfds_gmp": WIN},
                           fetchers={"mfds_gmp": lambda s, e: _fetch([RuntimeError("proxy down")])},
                           env={})
        res = sr.reconcile_source(SPEC, out["mfds_gmp"], run_date=RUN, window=WIN,
                                  collected=set(), ledger={})
        self.assertEqual(res.verdict, sr.V_UNREACHABLE)
        self.assertEqual(res.counts["missed"], 0)

    def test_later_page_failure_is_partial(self):
        page1_full = _page(*[_row(i, f"D{i}", "가나제약", "2026-11-25") for i in range(3)])
        with patch.object(g, "MAX_PAGES", 3):
            out = _fetch([page1_full, RuntimeError("timeout")])
        self.assertTrue(out.partial)
        self.assertEqual(len(out.docs), 3)

    def test_flag_off_is_disabled(self):
        out = sr.fetch_all([SPEC], {"mfds_gmp": WIN}, env={"ENABLE_MFDS_GMP_INSPECTION": "false"})
        self.assertFalse(out["mfds_gmp"].enabled)


class MissedThenFilledTest(unittest.TestCase):
    def test_injected_miss_alarms_then_closes_after_recollect(self):
        # 빠짐 주입: 원천에 A·B, DB 에는 A 만.
        outcome = _fetch([PAGE1, PAGE2])
        res = sr.reconcile_source(SPEC, outcome, run_date=RUN, window=WIN,
                                  collected={"MFDS::gmpinspect-DOC_A"}, ledger={})
        self.assertEqual(res.verdict, sr.V_MISSED)
        self.assertEqual([m["document_id"] for m in res.missed], ["gmpinspect-DOC_B"])
        plan = next(p for p in sr.plan_issues([res]))
        self.assertEqual(plan["action"], "open")
        self.assertIn("gmpinspect-DOC_B", plan["body"])

        # 재수집 뒤 다음 실행 — B 가 DB 에 있으면 정상, 열린 이슈는 닫는 계획.
        outcome2 = _fetch([PAGE1, PAGE2])
        res2 = sr.reconcile_source(SPEC, outcome2, run_date=RUN, window=WIN,
                                   collected={"MFDS::gmpinspect-DOC_A", "MFDS::gmpinspect-DOC_B"},
                                   ledger={})
        self.assertEqual(res2.verdict, sr.V_OK)
        self.assertEqual(next(p for p in sr.plan_issues([res2]))["action"], "close")


if __name__ == "__main__":
    unittest.main()
