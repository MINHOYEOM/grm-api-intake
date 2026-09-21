"""발행 브리프 제품군 소급 재분류 스크립트의 **기준(baseline)** 가드.

2026-09-21 사고: `--verify-replay` 가 "구 분류기"를 `origin/main` 에서 꺼냈다. 수리가
main 에 머지된 직후에 돌리자 그 자리에 **새 분류기**가 들어왔고, 재생값이 전부 ""(무배지)
로 나와 466장 중 308장이 불일치로 찍혔다. 게이트는 멈췄지만(그건 제 역할을 했다) 메시지가
"데이터가 틀렸다"를 가리켜 사람을 엉뚱한 곳으로 보냈다.

★재생 기준은 **고정 커밋**이어야 한다. 움직이는 ref 는 언젠가 반드시 자기 자신을 가리킨다.

여기 검사들은 git 을 건드리지 않는다 — CI 체크아웃이 얕은 클론(fetch-depth=1)이라
`git show <오래된 SHA>` 가 러너에서 성립하지 않기 때문이다. 대신 **성질**만 잰다.
"""
from __future__ import annotations

import importlib.util
import os
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)


def _read_script() -> str:
    path = os.path.join(_ROOT, "backfill_modality_published_briefs.py")
    with open(path, encoding="utf-8") as f:
        return f.read()


def _load_backfill():
    path = os.path.join(_ROOT, "backfill_modality_published_briefs.py")
    spec = importlib.util.spec_from_file_location("backfill_modality_mod", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class OldClassifierBaselineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.bf = _load_backfill()

    def test_baseline_is_a_pinned_commit_not_a_moving_ref(self) -> None:
        """★기준은 40자리 SHA 여야 한다 — 브랜치·태그 이름을 쓰면 안 된다.

        'origin/main'·'HEAD'·'main' 같은 이름은 시간이 지나면 수리 이후를 가리킨다.
        """
        ref = self.bf.OLD_CLASSIFIER_REF
        self.assertRegex(ref, r"^[0-9a-f]{40}$",
                         "재생 기준이 고정 커밋 SHA 가 아니다 — 움직이는 ref 금지")

    def test_anchor_actually_discriminates_old_from_new(self) -> None:
        """★앵커는 구/신 분류기를 **실제로 가른다**.

        앵커 입력을 현재(수리된) 분류기에 넣으면 앵커 기대값과 달라야 한다. 같다면
        앵커가 아무것도 구분하지 못한다는 뜻이고, 잘못된 기준 커밋을 그대로 통과시킨다
        (선명해 보이지만 아무것도 가르지 못하는 경계 — 대표적인 죽은 가드).
        """
        from grm_taxonomy import compute_modality

        raw, parts, expected_old = self.bf._OLD_CLASSIFIER_ANCHOR
        self.assertEqual(expected_old, "Chemical")
        self.assertNotEqual(
            compute_modality(raw, *parts), expected_old,
            "앵커가 구 분류기와 새 분류기를 구분하지 못한다 — 기준이 틀려도 통과한다")

    def test_anchor_input_is_the_reported_defect(self) -> None:
        """앵커 입력은 제보된 그 케이스여야 한다 — 'Sterile Drug Manufacturer'.

        (임의의 입력으로 바꾸면 '구 분류기인지'는 확인해도 '이 사건의 구 분류기인지'는
        확인하지 못한다.)
        """
        raw, _parts, _expected = self.bf._OLD_CLASSIFIER_ANCHOR
        self.assertIn("sterile", str(raw.get("product_type", "")).lower())
        self.assertIn("drug", str(raw.get("product_type", "")).lower())

    def test_verify_replay_maps_empty_verdict_to_no_badge(self) -> None:
        """빈 판정("")은 '배지 없음'(None)으로 대조돼야 한다.

        badge dict 에 "" 키가 우연히 있고 없고에 따라 결과가 달라지면 안 된다.
        """
        src = _read_script()
        self.assertIn("badge.get(replayed_value) if replayed_value else None", src,
                      "빈 판정을 명시적으로 '배지 없음'에 대응시키지 않는다")
        self.assertNotIn("badge.get(old_mod(", src,
                         "구 구현(badge.get 에 빈 문자열을 그대로 넘김)이 남아 있다")


class NormativeBadgeSuppressionTest(unittest.TestCase):
    """★규범 문서에는 소급도 제품군 배지를 달지 않는다.

    렌더(`to_web_card`)는 규범 유형의 배지를 억제한다. 소급 스크립트는 렌더를
    거치지 않고 카드 JSON 을 직접 고치므로 그 규칙을 다시 지켜야 한다 —
    첫 dry-run 이 '(배지 없음) → 바이오' 12장을 낸 자리가 정확히 여기다.
    """

    def setUp(self) -> None:
        self.bf = _load_backfill()

    def test_normative_labels_match_the_renderer(self) -> None:
        import card_scaffold as cs
        self.assertEqual(self.bf._normative_card_types(),
                         frozenset(cs._kind_meta(k)[1] for k in cs._NORMATIVE_KINDS))

    def test_no_non_normative_kind_shares_a_normative_label(self) -> None:
        """★라벨로 가르는 것이 안전하다는 **성질**을 고정한다.

        카드 JSON 에는 내부 kind 가 없고 화면 라벨(card_type)만 있다. 나중에
        비-규범 kind 가 규범 라벨을 같이 쓰면 그 카드가 통째로 소급에서 빠진다.
        """
        import card_scaffold as cs
        norm = self.bf._normative_card_types()
        clash = sorted(k for k in cs._REGISTRY
                       if k not in cs._NORMATIVE_KINDS and cs._kind_meta(k)[1] in norm)
        self.assertEqual(clash, [],
                         f"비-규범 kind 가 규범 라벨을 공유한다: {clash}")

    def test_loop_skips_normative_cards(self) -> None:
        src = _read_script()
        self.assertIn('if (card.get("card_type") or "") in normative:', src,
                      "소급 루프가 규범 문서를 건너뛰지 않는다")


class BackfillSafetyTest(unittest.TestCase):
    """raw_signals 는 읽기 전용이라는 계약."""

    def test_script_never_writes_to_raw_signals(self) -> None:
        src = _read_script()
        for verb in ("PATCH", "POST", "DELETE", "upsert", "_patch", "_post"):
            self.assertNotIn(verb, src,
                             f"백필 스크립트에 쓰기 동작으로 보이는 '{verb}' 이 있다")
        self.assertIn("select=\"document_id,raw_json,row_json\"", src)


if __name__ == "__main__":
    unittest.main()
