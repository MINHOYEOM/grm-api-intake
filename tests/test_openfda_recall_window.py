#!/usr/bin/env python3
"""openFDA Recall 수집 창 = enforcement 윈도우 배선 잠금 (2026-08-31).

배경(실측): collect_openfda_recalls(start, end, ...) 가 _run_collection 안에서 기본
7일 창(start)을 받고 있었다. openFDA enforcement 는 report_date 를 지연 일괄 공개해서
2026-08-31 실측 시점 최신 report_date=2026-08-19(12일 전) 배치 23건(Class I 3·Class II
19·미분류 1)이 7일 창 밖에 있었다 — 수집 0건("OpenFDA 404 — 정상 종료"로 침묵). Class I
은 tier 엔진에서 무조건 Tier 3(채택)라 브리프 유실이 실제 발생했다.

같은 파일에 지연공개 대응 30일 창 enf_start 가 이미 있고 MFDS 회수·행정처분, HC, FDA 483,
ISPE, EU/MHRA GMP NCR 이 전부 그 창을 쓴다 — openFDA Recall 만 옮겨지지 않았던 결함을
enf_start 로 교체한 수리를 여기서 잠근다. 다른 소스 collector 는 전부 no-op(호출 시
AssertionError)로 갈아끼워 active={"recall"} 게이팅이 무너져도 네트워크 0 을 보장한다.
"""
import argparse
import os
import sys
import unittest
from datetime import date, timedelta
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import collect_intake as ci  # noqa: E402

RUN_DATE = date(2026, 8, 31)

# _run_collection 이 active 게이팅과 무관하게 호출하면 안 되는 다른 소스 collector 전부.
# (collect_hc/collect_eu_gmp_ncr/collect_mhra_gmp_ncr/collect_mfds* 등은 함수 본문에서
# `from moduleX import funcY` 로 지연 import 되므로, enable_* 플래그가 전부 False 인 이상
# import 문 자체가 실행되지 않는다 — 별도 패치 불필요.)
_OTHER_TOP_LEVEL_COLLECTORS = [
    "collect_federal_register",
    "collect_ema_rss",
    "collect_mhra_rss",
    "collect_mhra_alerts",
    "collect_pics_rss",
    "collect_eca_rss",
    "collect_ispe_rss",
    "collect_fda_warning_letters",
]


def _no_op_raise(name: str):
    def _fn(*args, **kwargs):
        raise AssertionError(
            f"active={{'recall'}} 인데 {name} 이 호출됨 — 게이팅 회귀")
    return _fn


def _build_recall_only_config() -> ci.RunConfig:
    """--sources recall, 그 외 자격증명/플래그는 전부 미설정(clear env)인 최소 RunConfig."""
    args = argparse.Namespace(
        dry_run=True,
        window_days=7,
        sources=["recall"],
        emit_routine_handoff=False,
        handoff_window_days=None,
        handoff_doc_ids=None,
    )
    with patch.dict(os.environ, {}, clear=True):
        return ci.RunConfig.from_env(args)


class OpenfdaRecallEnforcementWindowTest(unittest.TestCase):
    """_run_collection 이 collect_openfda_recalls 를 enf_start(30일)로 호출하는지 잠근다.

    기본 창(start=7일)으로 회귀하면 FAIL — 지연공개 배치(2026-08-19, 23건) 침묵 유실 재발
    잠금."""

    def setUp(self) -> None:
        self.cfg = _build_recall_only_config()
        self.start, self.end = ci.date_window(RUN_DATE, self.cfg.window_days)
        self.enf_start = RUN_DATE - timedelta(days=self.cfg.mfds_enforcement_window_days)
        # 창이 실제로 달라야 이 테스트가 의미 있다(수리 전엔 같은 값이라 통과해버리면 오탐).
        self.assertNotEqual(self.start, self.enf_start)
        self.assertEqual(self.cfg.mfds_enforcement_window_days, 30)

    def test_run_collection_calls_recall_with_enforcement_window(self) -> None:
        with patch.object(ci, "collect_openfda_recalls",
                           return_value=([], None)) as mock_recall, \
             patch.multiple(
                 ci, **{name: _no_op_raise(name)
                        for name in _OTHER_TOP_LEVEL_COLLECTORS}):
            result = ci._run_collection(
                self.cfg, {"recall"}, RUN_DATE, self.start, self.end, self.enf_start)

        mock_recall.assert_called_once()
        called_args = mock_recall.call_args.args
        # 첫 인자가 start(7일)가 아니라 enf_start(enforcement 창, 30일)여야 한다.
        self.assertEqual(called_args[0], self.enf_start)
        self.assertNotEqual(called_args[0], self.start)
        self.assertEqual(called_args[1], self.end)
        self.assertEqual(
            mock_recall.call_args.args, (self.enf_start, self.end, self.cfg.openfda_key))

        stats = result[0]
        self.assertEqual(stats.recall_fetched, 0)
        self.assertFalse(stats.recall_error)

    def test_docstring_reflects_enforcement_window_not_fixed_7_days(self) -> None:
        doc = ci.collect_openfda_recalls.__doc__ or ""
        self.assertNotIn("지난 7 일 전수 수집", doc)
        self.assertIn("enforcement", doc)


if __name__ == "__main__":
    unittest.main()
