"""483 Observation 발행 게이트(§2026-07-14) 단위 테스트 — `render.validate_483_observations`.

CI(`unittest discover -s tests`)는 `tests/test_web_publish_gate.py` shim 을 통해 이 모듈을
순회한다(test_render.py 와 동일 패턴).

검증 대상은 render.py 의 main() 전용 fail-closed 게이트다(render_site()/build 헬퍼에는
넣지 않음 — 저 골든 픽스처 테스트가 이 게이트에 얽매이지 않게 하기 위함, `validate_483_
observations` 자체는 순수 함수라 여기서 브리프/카드 dict 를 직접 조립해 단위 테스트한다.
"""
from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import unittest

WEB_DIR = pathlib.Path(__file__).resolve().parent.parent      # …/web
REPO_ROOT = WEB_DIR.parent
sys.path.insert(0, str(WEB_DIR))
sys.path.insert(0, str(REPO_ROOT))
import render  # noqa: E402  (web/render.py)

__all__ = ["WebFda483PublishGateTest"]


def _obs(number, deficiency="Some deficiency text.", deficiency_ko="결함 원문 국문 번역.",
         detail="", detail_ko=None):
    o = {"number": number, "deficiency": deficiency, "deficiency_ko": deficiency_ko}
    if detail:
        o["detail"] = detail
        if detail_ko is not None:
            o["detail_ko"] = detail_ko
    return o


def _card(observations, card_id="fda483-1", count=None):
    return {
        "id": card_id,
        "card_type": "fda_483",
        "deterministic_detail": {
            "type": "fda_483_observations",
            "count": count if count is not None else len(observations),
            "observations": observations,
        },
    }


def _brief(cards, publish_date="2026-07-13"):
    return {"brief": {"publish_date": publish_date}, "cards": cards}


class WebFda483PublishGateTest(unittest.TestCase):
    def test_well_formed_card_passes(self):
        card = _card([
            _obs(1, detail="A well-formed detail sentence.", detail_ko="잘 작성된 상세 문장."),
            _obs(2),  # detail 없는 관찰(빈 detail 은 detail_ko 불필요)
        ])
        violations = render.validate_483_observations([_brief([card])])
        self.assertEqual(violations, [])

    def test_missing_deficiency_ko_raises_code(self):
        card = _card([_obs(1, deficiency_ko="")])
        violations = render.validate_483_observations([_brief([card])])
        self.assertTrue(any("MISSING_DEFICIENCY_KO" in v for v in violations), violations)

    def test_missing_deficiency_ko_none_value(self):
        obs = _obs(1)
        obs["deficiency_ko"] = None
        card = _card([obs])
        violations = render.validate_483_observations([_brief([card])])
        self.assertTrue(any("MISSING_DEFICIENCY_KO" in v for v in violations), violations)

    def test_detail_present_without_detail_ko_raises_code(self):
        card = _card([_obs(1, detail="Detail text present but no translation.", detail_ko=None)])
        violations = render.validate_483_observations([_brief([card])])
        self.assertTrue(any("MISSING_DETAIL_KO" in v for v in violations), violations)
        self.assertFalse(any("MISSING_DEFICIENCY_KO" in v for v in violations), violations)

    def test_empty_detail_does_not_require_detail_ko(self):
        card = _card([_obs(1, detail="")])
        violations = render.validate_483_observations([_brief([card])])
        self.assertEqual(violations, [])

    def test_footer_garbage_in_detail_raises_code(self):
        card = _card([_obs(
            1,
            detail=("Specifically, real content here. EMPt..oYEECS) SIGNATURE SEE "
                    "Joohi Castelvetere, Investigator 04/24/2026 R"),
            detail_ko="번역문.",
        )])
        violations = render.validate_483_observations([_brief([card])])
        self.assertTrue(any("FOOTER_GARBAGE" in v for v in violations), violations)

    def test_non_483_card_is_ignored(self):
        card = {
            "id": "wl-1",
            "card_type": "warning_letter",
            "deterministic_detail": {"type": "gmp_deficiencies", "items": []},
        }
        violations = render.validate_483_observations([_brief([card])])
        self.assertEqual(violations, [])

    def test_card_without_deterministic_detail_is_ignored(self):
        card = {"id": "rss-1", "card_type": "rss-news"}
        violations = render.validate_483_observations([_brief([card])])
        self.assertEqual(violations, [])

    def test_violations_are_aggregated_across_all_briefs_and_cards(self):
        good_card = _card([_obs(1)], card_id="fda483-good")
        bad_card_a = _card([_obs(1, deficiency_ko="")], card_id="fda483-bad-a")
        bad_card_b = _card(
            [_obs(1, detail="Some detail sentence.", detail_ko=None)],
            card_id="fda483-bad-b",
        )
        briefs = [
            _brief([good_card, bad_card_a], publish_date="2026-07-06"),
            _brief([bad_card_b], publish_date="2026-07-12"),
        ]
        violations = render.validate_483_observations(briefs)
        self.assertEqual(len(violations), 2, violations)
        self.assertTrue(any("2026-07-06" in v and "MISSING_DEFICIENCY_KO" in v for v in violations),
                        violations)
        self.assertTrue(any("2026-07-12" in v and "MISSING_DETAIL_KO" in v for v in violations),
                        violations)

    def test_validate_briefs_or_raise_wired_into_main_gate(self):
        # main() 이 실제로 부르는 진입점(_validate_briefs_or_raise) — 디스크의 브리프 JSON
        # 파일을 로드해 위반을 raise 하는지까지 end-to-end 로 확인(load_briefs() 경유).
        card = _card([_obs(1, deficiency_ko="")])
        with tempfile.TemporaryDirectory() as td:
            data_dir = pathlib.Path(td)
            (data_dir / "brief_web_2026_07_13.json").write_text(
                json.dumps(_brief([card], publish_date="2026-07-13"), ensure_ascii=False),
                encoding="utf-8",
            )
            with self.assertRaises(render.Fda483ObservationValidationError) as ctx:
                render._validate_briefs_or_raise(data_dir)
            self.assertIn("MISSING_DEFICIENCY_KO", str(ctx.exception))
            self.assertIn("2026-07-13", str(ctx.exception))

    def test_validate_briefs_or_raise_passes_on_clean_data(self):
        card = _card([_obs(1, detail="Fine detail.", detail_ko="괜찮은 상세.")])
        with tempfile.TemporaryDirectory() as td:
            data_dir = pathlib.Path(td)
            (data_dir / "brief_web_2026_07_13.json").write_text(
                json.dumps(_brief([card], publish_date="2026-07-13"), ensure_ascii=False),
                encoding="utf-8",
            )
            render._validate_briefs_or_raise(data_dir)  # raise 없이 통과


if __name__ == "__main__":
    unittest.main()
