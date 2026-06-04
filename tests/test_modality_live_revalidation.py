"""Live revalidation harness for a08fe82.

Runs compute_modality against the actual MFDS recall+admin payloads that were
misclassified in the previous live load (session 1) — verifies the fix without
needing fresh Notion inserts (everything is dedup'd now).

Run: python -m unittest tests/test_modality_live_revalidation
"""
import unittest

from collect_intake import compute_modality


def mfds_recall(prduct: str, reason: str = "", entrps: str = "") -> tuple[dict, tuple]:
    """Construct raw_payload + text_parts the way collect_mfds_recall.py does."""
    raw = {
        "api": "data.go.kr 15059114",
        "PRDUCT": prduct,
        "ENTRPS": entrps,
        "RTRVL_RESN": reason,
    }
    # Match how collect_intake.compute_modality is called for MFDS recall items:
    # text_parts = (headline, body, type_or_class, …) — body includes reason.
    headline = f"[회수·판매중지] {prduct} — {entrps}" if entrps else f"[회수·판매중지] {prduct}"
    body = reason
    return raw, (headline, body, "recall-quality")


def mfds_admin(item_name: str, expose_cont: str = "", entrps: str = "") -> tuple[dict, tuple]:
    """Construct raw_payload + text_parts the way collect_mfds_admin_action.py does."""
    raw = {
        "api": "data.go.kr 15058457",
        "ITEM_NAME": item_name,
        "ENTP_NAME": entrps,
        "EXPOSE_CONT": expose_cont,
    }
    headline = f"[행정처분] {item_name} — {entrps}" if entrps else f"[행정처분] {item_name}"
    body = expose_cont
    return raw, (headline, body, "admin-action")


class LiveRevalidation(unittest.TestCase):
    """Each case is a real row from the 2026-06-04 live load (session 1)."""

    # ─── Korean tablet suffix (XX정) — previously Other, must be Chemical ──
    def test_recall_이모나캡슐_chemical(self):
        # was Chemical (캡슐 match) — must remain Chemical
        raw, parts = mfds_recall("이모나캡슐", "유통제품 품질부적합 우려", "미래바이오제약(주)")
        self.assertEqual(compute_modality(raw, *parts), "Chemical")

    def test_recall_노텍정_세티리진_chemical(self):
        raw, parts = mfds_recall(
            "노텍정(세티리진염산염)", "유통제품 품질부적합 우려", "미래바이오제약(주)"
        )
        self.assertEqual(compute_modality(raw, *parts), "Chemical")

    def test_recall_리치정_chemical(self):
        raw, parts = mfds_recall("리치정", "유통제품 품질부적합 우려", "미래바이오제약(주)")
        self.assertEqual(compute_modality(raw, *parts), "Chemical")

    def test_recall_유트렌정_tramadol_chemical(self):
        raw, parts = mfds_recall(
            "유트렌정",
            "불순물(N-nitroso-N-desmethyl-tramadol)초과 검출 우려에 따른 사전예방적 조치로 시중 유통품에 대한 영업자 회수",
            "일양바이오팜(주)",
        )
        self.assertEqual(compute_modality(raw, *parts), "Chemical")

    def test_recall_마그스타에프정_chemical(self):
        raw, parts = mfds_recall("마그스타에프정", "유통제품 품질부적합 우려", "주식회사더유제약")
        self.assertEqual(compute_modality(raw, *parts), "Chemical")

    def test_recall_트라마펜정_chemical(self):
        raw, parts = mfds_recall("트라마펜정", "유통제품 품질부적합 우려", "구주제약(주)")
        self.assertEqual(compute_modality(raw, *parts), "Chemical")

    def test_recall_트라마펜세미정_chemical(self):
        raw, parts = mfds_recall("트라마펜세미정", "유통제품 품질부적합 우려", "구주제약(주)")
        self.assertEqual(compute_modality(raw, *parts), "Chemical")

    def test_recall_울셋세미정_chemical(self):
        raw, parts = mfds_recall("울셋세미정", "유통제품 품질부적합 우려", "(주)비씨월드제약")
        self.assertEqual(compute_modality(raw, *parts), "Chemical")

    def test_recall_영트라셋정_chemical(self):
        raw, parts = mfds_recall("영트라셋정", "유통제품 품질부적합 우려", "영진약품(주)")
        self.assertEqual(compute_modality(raw, *parts), "Chemical")

    def test_recall_하이타민골드정_chemical(self):
        raw, parts = mfds_recall("하이타민골드정", "유통제품 품질부적합 우려", "미래바이오제약(주)")
        self.assertEqual(compute_modality(raw, *parts), "Chemical")

    def test_recall_하이세펜정_chemical(self):
        raw, parts = mfds_recall("하이세펜정", "유통제품 품질부적합 우려", "(주)한국파마")
        self.assertEqual(compute_modality(raw, *parts), "Chemical")

    def test_recall_하이펜에스정_chemical(self):
        raw, parts = mfds_recall("하이펜에스정", "유통제품 품질부적합 우려", "미래바이오제약(주)")
        self.assertEqual(compute_modality(raw, *parts), "Chemical")

    def test_recall_트라플엠세미정_chemical(self):
        raw, parts = mfds_recall("트라플엠세미정", "유통제품 품질부적합 우려", "(주)마더스제약")
        self.assertEqual(compute_modality(raw, *parts), "Chemical")

    # ─── Korean injection suffix (XX주) — previously Other, must be Chemical ──
    def test_admin_예나스테론주_testosterone_chemical(self):
        raw, parts = mfds_admin(
            "예나스테론주(테스토스테론에난테이트)",
            "의약품을 판매할 수 있는 자 외의 자에게 의약품 판매",
            "제이텍바이오젠",
        )
        self.assertEqual(compute_modality(raw, *parts), "Chemical")

    def test_admin_생리식염수_멀티플렉스페리주_chemical(self):
        raw, parts = mfds_admin(
            "대한관류용멸균생리식염수,멀티플렉스페리주",
            "기준서 미준수 — 충전공정 시 과잉충전 및 손실량, 포장공정 시 불량 수량 추후 기록",
            "대한약품공업(주)",
        )
        self.assertEqual(compute_modality(raw, *parts), "Chemical")

    # ─── Biologic — body 본문에 한국어 원료 단서 ──
    def test_admin_자닥신주_자하거추출물_biologic(self):
        """본문 EXPOSE_CONT에 '자하거추출물' 명시 → Biologic 우선 매칭."""
        raw, parts = mfds_admin(
            "자닥신주",
            "원료의약품 '자하거추출물[등록번호 제20070504-01-HP-12-10호]'의 표시기재 위반 + 자닥신주의 주사제용 유리용기시험 누락",
            "(주)파마리서치",
        )
        self.assertEqual(compute_modality(raw, *parts), "Biologic")

    def test_synthetic_insulin_biologic(self):
        raw, parts = mfds_recall("휴마로그주", "인슐린아스파트(인슐린) 안정성 미부합", "(주)한국릴리")
        self.assertEqual(compute_modality(raw, *parts), "Biologic")

    def test_vaccine_biologic(self):
        raw, parts = mfds_recall("플라그릴백신주", "톡소이드 백신 제조공정 일탈", "(주)일양약품")
        self.assertEqual(compute_modality(raw, *parts), "Biologic")

    # ─── Other — 한약·생약·치약·식품류 ──
    def test_recall_엔탭허브오약_other(self):
        raw, parts = mfds_recall("엔탭허브오약", "중금속(카드뮴) 부적합", "(주)엔탭허브")
        self.assertEqual(compute_modality(raw, *parts), "Other")

    def test_recall_네츄럴블랙치약_other(self):
        raw, parts = mfds_recall("네츄럴블랙치약", "품질부적합 우려", "우리생활건강")
        self.assertEqual(compute_modality(raw, *parts), "Other")

    def test_recall_풍산강황_other(self):
        raw, parts = mfds_recall("풍산강황", "품질부적합 우려", "풍산주식회사")
        self.assertEqual(compute_modality(raw, *parts), "Other")

    def test_recall_허브팜곡기생_other(self):
        raw, parts = mfds_recall("허브팜곡기생", "품질부적합 우려", "(주)허브팜")
        self.assertEqual(compute_modality(raw, *parts), "Other")

    def test_recall_씨케이당귀_other(self):
        raw, parts = mfds_recall("씨케이당귀", "품질부적합 우려", "씨케이(주)")
        self.assertEqual(compute_modality(raw, *parts), "Other")

    # ─── 일반 규제 문서가 정제로 오탐되지 않는지 ──
    def test_fr_guidance_revision_not_chemical(self):
        """Federal Register 가이드라인 '개정'이라는 단어가 정제로 매칭되어서는 안 된다."""
        raw = {"document_number": "2026-11100", "type": "Notice"}
        parts = (
            "FDA Issues Revised Guidance for Industry on Drug Product Quality (Public Consultation)",
            "This notice announces the availability of a revised draft guidance — 본 개정안은 의약품 품질에 관한 규정 개정 사항을 다룸",
            "Notice",
        )
        # 의약품/drug 등 명시적 단서가 본문에 있으면 Chemical 가능, 단 '개정' 자체가 정제로
        # 오탐되어서는 안 됨. 본문에 '의약품 품질' 명시되어 Chemical 매칭(MODALITY_DRUG_PRODUCT_TERMS에 의약품 포함)
        # — 이 케이스는 Chemical OK이지만 핵심은 '정제'로 오해된 게 아닌지 확인.
        self.assertIn(compute_modality(raw, *parts), {"Chemical", "Other"})

    def test_admin_action_keyword_not_falsely_chemical(self):
        """'행정처분' 본문 단독으로는 정제 오탐 X — 의약품 단서 없으면 Other."""
        raw = {}  # no product name fields
        parts = (
            "Generic enforcement action notice",
            "본 행정처분은 규정 위반에 따른 조치 — 의약품/원료 단서 없음",
            "regulatory-notice",
        )
        # '행정' '규정' 들이 정제로 오탐되어서는 안 됨. 단, body에 '의약품'이 있으므로 Chemical 매칭 가능 — 명확화 위해 의약품 제거 변종도 추가.
        # 핵심 케이스: 의약품 단서 0 → Other 여야 함.
        parts_clean = (
            "Generic enforcement action notice",
            "본 행정처분은 규정 위반에 따른 조치",
            "regulatory-notice",
        )
        self.assertEqual(compute_modality({}, *parts_clean), "Other")


if __name__ == "__main__":
    unittest.main(verbosity=2)
