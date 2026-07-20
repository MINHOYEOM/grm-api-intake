"""발행된 **전 브리프**에 대한 상시 무결성 스윕 — 네트워크 0·결정론.

조립 게이트(`lint_false_absence_claims`)는 **그 주에 조립되는 브리프**만 본다. 이미 발행돼
사이트에 떠 있는 과거 브리프는 아무도 다시 보지 않으므로, 한 번 새어 나간 거짓 서술은
영영 라이브에 남는다(2026-07-20 전수 점검에서 06-26·07-06 발행분 4건이 그렇게 발견됐다).
그래서 CI 가 매 실행마다 `web/data/briefs/` 전체를 다시 훑는다.

여기 있는 검사는 전부 저장소 안에서 끝난다(네트워크 필요 없음). 원문을 실제로 다시 받아
대조하는 층은 별도 워크플로(`.github/workflows/grm-source-verification.yml`)가 담당한다 —
수집 실패로 **처음부터 못 받은** 누락은 저장소 안에서는 알 수 없기 때문이다.
"""

from __future__ import annotations

import json
import pathlib
import re
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import assemble_publish_brief as apb  # noqa: E402

BRIEFS = sorted((ROOT / "web" / "data" / "briefs").glob("brief_web_*.json"))

# PDF 서브셋 폰트 합자 잔재(collect_fda_483.normalize_pdf_ligatures 가 복원하는 문자들).
# 발행물에 남아 있으면 "iniƟal receipt of the informaƟon" 같은 깨진 영문이 노출된 것이다.
_LIGATURE_RE = re.compile(r"[ƟƩʖﬀ-ﬆ]")


class PublishedBriefsPresent(unittest.TestCase):
    def test_briefs_found(self):
        """스윕이 0건을 훑고 조용히 통과하는 상황을 막는다(침묵 구멍 방지)."""
        self.assertGreater(len(BRIEFS), 0, "web/data/briefs 에 발행본이 없다 — 스윕이 무의미")


class NoFalseAbsenceInPublishedBriefs(unittest.TestCase):
    """원문을 확보한 카드가 '원문에 없다'고 주장하는 발행본이 하나도 없어야 한다."""

    def test_no_false_absence_claims(self):
        for path in BRIEFS:
            with self.subTest(brief=path.name):
                brief = json.loads(path.read_text(encoding="utf-8"))
                errs = apb.lint_false_absence_claims(brief.get("cards") or [])
                self.assertEqual(errs, [], f"{path.name}: 거짓 부재 서술\n  - "
                                           + "\n  - ".join(errs))


class NoLigatureArtifactsInPublishedBriefs(unittest.TestCase):
    """PDF 합자 잔재(Ɵ=ti·Ʃ=tt·ﬁ=fi 등)가 발행물에 남아 있지 않아야 한다."""

    def test_no_ligature_artifacts(self):
        for path in BRIEFS:
            with self.subTest(brief=path.name):
                hits = sorted(set(_LIGATURE_RE.findall(path.read_text(encoding="utf-8"))))
                self.assertEqual(hits, [], f"{path.name}: 합자 잔재 {hits} — "
                                           "collect_fda_483.normalize_pdf_ligatures 미적용분")


class NoUnverifiedAbsenceVocabularyInPublishedBriefs(unittest.TestCase):
    """발행물 어디에도 `"원문 미기재"` 가 없어야 한다 — 근본 원인의 단일 불변식.

    이 문자열은 *원문에 대한 단정*인데, 우리 파이프라인에는 원문을 필드 단위로 확인하는 경로가
    없다. 그래서 어떤 계층(코드 facts·LLM 산문·디제스트 목록)에서 나오든 근거가 없다. 값이
    비었다는 우리 상태는 `card_scaffold.VALUE_UNKNOWN`("미확인")으로 말한다.

    문자열 하나를 금지하는 조잡해 보이는 검사지만, 2026-07-20 근본원인 조사에서 확인된 사실이
    정확히 이것이다 — 이 한 어휘가 코드→명세→프롬프트→LLM 전 계층에 복제되며 거짓을 낳았다.
    """

    def test_no_unverified_absence_vocabulary(self):
        for path in BRIEFS:
            with self.subTest(brief=path.name):
                text = path.read_text(encoding="utf-8")
                self.assertNotIn(
                    "원문 미기재", text,
                    f"{path.name}: 근거 없는 원문 부재 단정 표기 — "
                    "card_scaffold.VALUE_UNKNOWN(\"미확인\") 을 쓸 것")


class NoEmptyProseSlotsInPublishedBriefs(unittest.TestCase):
    """발행 카드에 빈 산문 슬롯이 없어야 한다(조립 게이트 2 의 사후 스윕).

    2026-06-22 발행본은 36장 전원이 제목·요약·핵심사실·시사점이 통째로 빈 채 라이브다
    (`assemble_publish_brief` 의 빈슬롯 게이트가 생기기 전에 나갔다). 그 브리프는 별도
    트랙으로 정정하기로 했으므로 그때까지만 예외로 둔다 — 예외 목록에 새 날짜가 추가되는
    일은 없어야 한다.
    """

    KNOWN_EMPTY = {"brief_web_2026_06_22.json"}   # 정정 대기(별도 트랙). 추가 금지.

    def test_no_empty_prose_slots(self):
        for path in BRIEFS:
            if path.name in self.KNOWN_EMPTY:
                continue
            with self.subTest(brief=path.name):
                brief = json.loads(path.read_text(encoding="utf-8"))
                empty = [c.get("id") for c in (brief.get("cards") or [])
                         if not str(c.get("summary") or "").strip()]
                self.assertEqual(empty, [], f"{path.name}: 빈 summary 카드 {empty}")

    def test_known_empty_list_is_still_accurate(self):
        """예외 목록이 낡으면(그 브리프가 고쳐졌으면) 목록에서 빼도록 알린다."""
        for name in self.KNOWN_EMPTY:
            path = ROOT / "web" / "data" / "briefs" / name
            if not path.exists():
                continue
            brief = json.loads(path.read_text(encoding="utf-8"))
            empty = [c for c in (brief.get("cards") or [])
                     if not str(c.get("summary") or "").strip()]
            self.assertTrue(empty, f"{name}: 이제 빈 카드가 없다 — KNOWN_EMPTY 에서 제거할 것")


if __name__ == "__main__":
    unittest.main()
