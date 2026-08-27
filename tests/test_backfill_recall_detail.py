#!/usr/bin/env python3
"""회수 결정론 상세 소급 CLI 의 순수 부분(네트워크 없음).

★이 파일이 생긴 계기 — #806 이 MHRA 알림 본문을 배선하면서 상한을 4000자로 뒀는데, 그 값은
**발췌 상한의 숫자**였다. 전건 6장 실측(2026-08-27)에서 4장이 잘렸고 그중 2장은 단어 중간에서
끊겼다. 잡히지 않은 이유는 단순하다 — 이 CLI 도, 상한도 테스트가 하나도 없었다.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backfill_recall_detail as b  # noqa: E402
import card_scaffold as cs  # noqa: E402
import assemble_publish_brief as apb  # noqa: E402
import collect_intake as ci  # noqa: E402

_BODY = ("DMRC reference number DMRC - 39754868 Marketing Authorisation Holder Example Ltd "
         "Medicine Details PL: 17780/0858 Affected Lot Batch No. 4L01372H Background "
         "The company is recalling one batch as a precautionary measure.")


def _card(cid, card_type, **extra):
    card = {"id": cid, "card_type": card_type, "checks": ["점검1"]}
    card.update(extra)
    return card


class MhraRetroScopeTest(unittest.TestCase):
    """대상 확대(#806 소급)가 **의도한 카드에만** 닿는지 — 양성·음성 양쪽."""

    def test_recall_uk_card_gets_detail(self):
        brief = {"cards": [_card("aaa111", "Recall(UK)")]}
        details, no_raw, no_detail, multi = b.build_details(
            brief, {"aaa111": {"mhra_alert_body": _BODY}})
        self.assertEqual(list(details), ["aaa111"])
        self.assertEqual(details["aaa111"]["type"], "mhra_recall_alert")
        self.assertEqual(details["aaa111"]["body"], _BODY)
        self.assertEqual((no_raw, no_detail), ([], []))

    def test_pre_399_news_card_gets_detail(self):
        """#399(2026-07-22) 이전 발행분은 card_type 이 규제 소식이다 — 문서번호로 구제."""
        cid = sorted(b._PRE_399_MHRA_RECALL_IDS)[0]
        brief = {"cards": [_card(cid, "규제 소식")]}
        details, _, _, _ = b.build_details(brief, {cid: {"mhra_alert_body": _BODY}})
        self.assertEqual(list(details), [cid])

    def test_other_news_card_never_gets_recall_detail(self):
        """★음성 검사 — 허용목록 밖 규제 소식은 본문이 있어도 상세가 나면 안 된다.

        '규제 소식'을 _PRODUCERS 에 넣었다면 이 테스트가 빨개진다(무관한 뉴스 카드 오염)."""
        brief = {"cards": [_card("zzz999", "규제 소식")]}
        details, no_raw, no_detail, multi = b.build_details(
            brief, {"zzz999": {"mhra_alert_body": _BODY}})
        self.assertEqual(details, {})
        self.assertEqual((no_raw, no_detail), ([], []))

    def test_existing_detail_is_not_overwritten(self):
        brief = {"cards": [_card("aaa111", "Recall(UK)",
                                 deterministic_detail={"type": "already", "body": "keep"})]}
        details, _, _, _ = b.build_details(brief, {"aaa111": {"mhra_alert_body": _BODY}})
        self.assertEqual(details, {})

    def test_missing_body_yields_no_empty_block(self):
        brief = {"cards": [_card("aaa111", "Recall(UK)")]}
        details, no_raw, no_detail, multi = b.build_details(brief, {"aaa111": {"title": "no body"}})
        self.assertEqual(details, {})
        self.assertEqual(no_detail, ["aaa111"])

    def test_detail_lands_right_after_checks(self):
        brief = {"cards": [_card("aaa111", "Recall(UK)", sources={"official_url": "u"})]}
        details, _, _, _ = b.build_details(brief, {"aaa111": {"mhra_alert_body": _BODY}})
        self.assertEqual(b.apply_details(brief, details), 1)
        keys = list(brief["cards"][0])
        self.assertEqual(keys[keys.index("checks") + 1], "deterministic_detail")

    def test_producer_is_the_operational_one(self):
        """별도 구현을 두면 라이브와 갈라진다 — 운영 producer 동일성을 못박는다."""
        self.assertIs(b._PRODUCERS["Recall(UK)"], cs._detail_mhra_recall)


class MhraBodyCapTest(unittest.TestCase):
    """#806 의 절단 결함 재발 방지 — 상한이 '발췌'가 아니라 '전문' 계열인지."""

    #  gov.uk Content API 전건 실측(2026-08-27, 6/6): 최대 5222자.
    OBSERVED_MAX = 5222

    def test_cap_is_full_text_class_not_excerpt(self):
        self.assertEqual(ci.MHRA_ALERT_BODY_MAX_CHARS, ci.WL_BODY_FULL_MAX_CHARS)

    def test_cap_clears_observed_corpus_with_headroom(self):
        """상한이 관측 최대치에 근접하면 다음 장문 한 건이 다시 잘린다."""
        self.assertGreaterEqual(ci.MHRA_ALERT_BODY_MAX_CHARS, self.OBSERVED_MAX * 2)

    def test_excerpt_caps_stay_separate(self):
        """표시 상한과 전문 상한을 도로 합치지 않는다(경고서한 480자 절단 계열)."""
        self.assertLess(ci.WL_BODY_MAX_CHARS, ci.MHRA_ALERT_BODY_MAX_CHARS)


class MhraDetailTypeClassificationTest(unittest.TestCase):
    """★회수 4종이 **같은 칸에 들어가지 않는다** — 이 판정을 코드 밖에 못박는다.

    형제 3종은 로트·수량 같은 사실표라 상세를 싣고도 "당국이 세부 일탈 사유를 공개하지
    않았다"가 여전히 참이다. MHRA 알림만 공고 전문이라 회수 사유 서술(Background)을 손에
    쥔다 — 그래서 부재 서술 게이트의 검사 대상이어야 한다. 넷을 한 칸에 몰면 둘 중 하나가
    반드시 틀린다(사실표를 서사로 보면 발행이 잘못 막히고, 서사를 사실표로 보면 거짓 문장이
    그대로 나간다 — 후자가 2026-07-27 WHOPIR 11장 사고다)."""

    def test_mhra_alert_is_body_not_facts(self):
        self.assertIn("mhra_recall_alert", apb._BODY_DETAIL_TYPES)
        self.assertNotIn("mhra_recall_alert", apb._FACTS_DETAIL_TYPES)

    def test_sibling_recall_types_stay_facts(self):
        for t in ("openfda_recall_detail", "mfds_recall_detail", "hc_recall_detail"):
            with self.subTest(t=t):
                self.assertIn(t, apb._FACTS_DETAIL_TYPES)
                self.assertNotIn(t, apb._BODY_DETAIL_TYPES)

    def test_body_classification_reaches_the_absence_gate(self):
        """분류가 실제로 게이트에 닿는지 — 집합 등재만 보고 배선을 단정하지 않는다."""
        card = {"id": "aaa111",
                "deterministic_detail": {"type": "mhra_recall_alert", "body": _BODY}}
        self.assertTrue(apb._card_has_source_body(card))
        facts = {"id": "bbb222",
                 "deterministic_detail": {"type": "hc_recall_detail", "action": "quarantine"}}
        self.assertFalse(apb._card_has_source_body(facts))


class MultiItemCardGuardTest(unittest.TestCase):
    """★한 장이 여러 품목을 덮는 카드에 단일 레코드 상세를 붙이지 않는다.

    식약처 상세는 "품목·업체 식별코드"(ITEM_SEQ·STD_CD)라는 품목 단위 주장을 화면에 낸다.
    실측: 카드 recall-709cff0f6b75 는 글로틴듀오정 3용량을 한 장으로 덮는데 원천은 품목마다
    별개 행이고 코드가 전부 다르다 — 하나를 붙이면 나머지 두 품목은 그 코드로 조회되지 않는다.
    카드 최상위 merged_count 는 1 이라 그것으로는 못 거른다(병합이 상류에서 일어났다)."""

    _RAW = {"ENFRC_YN": "N", "ITEM_SEQ": "202001274", "STD_CD": "8806625045509",
            "BIZRNO": "3018125427"}

    def test_multi_item_card_is_skipped_and_reported(self):
        brief = {"cards": [_card("multi1", "회수·판매중지",
                                 headline_target="글로틴듀오정2.5/500mg 외 2품목",
                                 merged_count=1)]}
        details, no_raw, no_detail, multi = b.build_details(brief, {"multi1": self._RAW})
        self.assertEqual(details, {})
        self.assertEqual(multi, ["multi1"])          # ★침묵 skip 금지
        self.assertEqual((no_raw, no_detail), ([], []))

    def test_multi_item_detected_from_product_fact_too(self):
        brief = {"cards": [_card("multi2", "회수·판매중지", headline_target="(주)넥스팜코리아",
                                 facts=[{"label": "제품", "value": "글로틴듀오정 외 2품목"}])]}
        _, _, _, multi = b.build_details(brief, {"multi2": self._RAW})
        self.assertEqual(multi, ["multi2"])

    def test_single_item_card_still_gets_detail(self):
        """음성 검사 — 가드가 정상 카드를 훔치지 않는가(과잉 차단 방지)."""
        brief = {"cards": [_card("solo1", "회수·판매중지",
                                 headline_target="클린방수밴드(1회용)",
                                 facts=[{"label": "제품", "value": "클린방수밴드(1회용)"}])]}
        details, _, _, multi = b.build_details(brief, {"solo1": self._RAW})
        self.assertEqual(multi, [])
        self.assertEqual(details["solo1"]["type"], "mfds_recall_detail")

    def test_plain_number_in_title_is_not_multi(self):
        """'외 N품목' 어형만 잡는다 — 용량 숫자(2.5/500mg)에 걸리면 안 된다."""
        brief = {"cards": [_card("solo2", "회수·판매중지",
                                 headline_target="글로틴듀오정2.5/500mg(리나글립틴)")]}
        _, _, _, multi = b.build_details(brief, {"solo2": self._RAW})
        self.assertEqual(multi, [])


if __name__ == "__main__":
    unittest.main()
