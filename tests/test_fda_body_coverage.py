"""발행 전 FDA 본문 결손 점검(fda_body_coverage) 회귀 — 2026-10-27 FDA 결손 감사.

10-26 호에서 FDA 483 25건이 관찰 본문 없이 "본문 미확보" 묶음 카드로 발행됐다. 결손을
알리는 health 경고는 수집 실행 단위였고, **발행 직전 그 주 브리프 기준으로** 세어 보는
장치가 없었다. 이 테스트는 판정(건수·비율·임계·다음 행동)을 고정한다.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import fda_body_coverage as cov


def _c483(media, *, detail=True, est="Drug Manufacturer", merged=0):
    card = {"id": f"fda483-{media}", "agency": "FDA", "type_tag": "483",
            "card_type": "FDA 483 실사 관찰",
            "facts": [{"label": "시설 · 유형", "value": f"{est} · 483"}]}
    if detail:
        card["deterministic_detail"] = {"type": "fda_483_observations",
                                        "observations": [{"deficiency": "x", "detail": "y"}]}
    if merged:
        card["merged_count"] = merged
    return card


def _wl(cid, body=True):
    card = {"id": cid, "agency": "FDA", "card_type": "Warning Letter", "type_tag": "CGMP"}
    if body:
        card["source_body_captured"] = True
    return card


def _recall(cid, body=True):
    card = {"id": cid, "agency": "FDA", "card_type": "Recall", "type_tag": "Recall"}
    if body:
        card["source_body_captured"] = True
    return card


def _brief(*cards):
    return {"schema_version": "grm-web-card/v1", "brief": {}, "cards": list(cards)}


class MeasureTest(unittest.TestCase):
    def test_incident_week_crosses_483_threshold(self):
        """10-26 스캐폴드 모양: 97장 중 25장 무본문 → 임계 초과, 회수 대상 25건."""
        cards = [_c483(i) for i in range(72)] + [_c483(1000 + i, detail=False) for i in range(25)]
        r = cov.measure(_brief(*cards))
        k = r.kinds[cov.KIND_483]
        self.assertEqual((k.total, k.missing), (97, 25))
        self.assertIn(cov.KIND_483, r.over)
        self.assertEqual(len(k.missing_ids), 25)

    def test_small_weeks_do_not_alarm(self):
        """공개가 적은 주에 무본문 1~2장으로 울리지 않는다(건수·비율 둘 다 넘어야)."""
        r = cov.measure(_brief(_c483(1, detail=False), _c483(2, detail=False), _c483(3)))
        self.assertNotIn(cov.KIND_483, r.over)            # 2건 < 3건
        many = [_c483(i) for i in range(40)] + [_c483(100 + i, detail=False) for i in range(3)]
        self.assertNotIn(cov.KIND_483, cov.measure(_brief(*many)).over)   # 3/43 = 7% < 10%

    def test_out_of_scope_483_is_not_counted(self):
        """조립이 버리는 범위 밖 483(기기 등)은 발행 결손이 아니다."""
        cards = [_c483(i, detail=False, est="Medical Device Manufacturer") for i in range(10)]
        r = cov.measure(_brief(*cards))
        self.assertEqual(r.kinds[cov.KIND_483].total, 0)
        self.assertEqual(r.over, [])

    def test_assembled_digest_counts_members(self):
        """조립본의 묶음 카드는 merged_count 만큼 센다(대표 1장만 남기 때문)."""
        r = cov.measure(_brief(_c483(1, detail=False, merged=12), *[_c483(10 + i) for i in range(5)]))
        k = r.kinds[cov.KIND_483]
        self.assertEqual((k.total, k.missing), (17, 12))

    def test_wl_and_recall_missing_body(self):
        r = cov.measure(_brief(_wl("a"), _wl("b", body=False), _recall("c"), _recall("d", body=False)))
        self.assertEqual(r.kinds[cov.KIND_WL].missing, 1)
        self.assertEqual(r.kinds[cov.KIND_RECALL].missing, 1)
        self.assertEqual(set(r.over), {cov.KIND_WL, cov.KIND_RECALL})

    def test_fr_is_observed_but_never_gated(self):
        fr = {"id": "2026-21669", "agency": "FDA", "card_type": "지침·안내서", "quotes": []}
        r = cov.measure(_brief(fr))
        self.assertEqual(r.kinds[cov.KIND_FR].missing, 1)
        self.assertEqual(r.over, [])

    def test_non_fda_cards_ignored(self):
        r = cov.measure(_brief({"id": "x", "agency": "MFDS", "card_type": "회수·판매중지"}))
        self.assertEqual(sum(k.total for k in r.kinds.values()), 0)


class DecideTest(unittest.TestCase):
    def _over483(self):
        return cov.measure(_brief(*[_c483(i, detail=False) for i in range(5)]))

    def test_ok_when_under_threshold(self):
        action, _ = cov.decide(cov.measure(_brief(_c483(1))), intake_event="schedule", kst_hour=6)
        self.assertEqual(action, "ok")

    def test_recover_before_routine(self):
        action, _ = cov.decide(self._over483(), intake_event="schedule", kst_hour=6, kst_minute=10)
        self.assertEqual(action, "recover")

    def test_alert_after_cutoff(self):
        action, reason = cov.decide(self._over483(), intake_event="schedule", kst_hour=7)
        self.assertEqual(action, "alert")
        self.assertIn("07:00", reason)

    def test_alert_after_recovery_already_tried(self):
        action, _ = cov.decide(self._over483(), intake_event="schedule", kst_hour=6,
                               recovery_attempted=True)
        self.assertEqual(action, "alert")

    def test_manual_intake_output_is_not_regenerated(self):
        action, _ = cov.decide(self._over483(), intake_event="workflow_dispatch", kst_hour=5)
        self.assertEqual(action, "alert")

    def test_wl_only_deficit_alerts_directly(self):
        action, _ = cov.decide(cov.measure(_brief(_wl("a", body=False))),
                               intake_event="schedule", kst_hour=5)
        self.assertEqual(action, "alert")


class OutputTest(unittest.TestCase):
    def test_issue_lines_use_admin_warning_format(self):
        """Admin 운영 상태 센터는 `- [code] …` 줄을 경고로 읽는다(admin-github warningIssueSummary)."""
        lines = cov.issue_lines(cov.measure(_brief(*[_c483(i, detail=False) for i in range(4)])))
        self.assertEqual(len(lines), 1)
        self.assertRegex(lines[0], r"^- \[fda-body-coverage\] FDA 483: 본문 미확보 \*\*4/4건\*\*")

    def test_cli_emits_recover_ids_and_files(self):
        brief = _brief(*[_c483(500 + i, detail=False) for i in range(3)], _c483(9))
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "b.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(brief, fh)
            rep, summ, iss = (os.path.join(d, n) for n in ("r.json", "s.md", "i.md"))
            out = subprocess.run(
                [sys.executable, os.path.join(ROOT, "fda_body_coverage.py"), "check",
                 "--brief", path, "--kst-hour", "6", "--report-json", rep,
                 "--summary-md", summ, "--issue-md", iss],
                capture_output=True, text=True, encoding="utf-8", check=True).stdout
            kv = dict(line.split("=", 1) for line in out.strip().splitlines())
            self.assertEqual(kv["action"], "recover")
            self.assertEqual(kv["recover_ids"].split(), ["fda483-500", "fda483-501", "fda483-502"])
            self.assertEqual(json.load(open(rep, encoding="utf-8"))["kinds"]["483"]["missing"], 3)
            self.assertIn("FDA 483", open(summ, encoding="utf-8").read())
            self.assertIn("[fda-body-coverage]", open(iss, encoding="utf-8").read())


class RealScaffoldShapeTest(unittest.TestCase):
    """발행된 실제 브리프로 판정 — 결손이 있던 주와 없던 주가 갈리는지(골든 아님, 존재할 때만)."""

    def _brief(self, name):
        path = os.path.join(ROOT, "web", "data", "briefs", name)
        if not os.path.exists(path):
            self.skipTest(name)
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)

    def test_known_digest_week_is_flagged(self):
        self.assertIn(cov.KIND_483, cov.measure(self._brief("brief_web_2026_08_03.json")).over)

    def test_clean_week_is_not_flagged(self):
        self.assertEqual(cov.measure(self._brief("brief_web_2026_10_12.json")).over, [])


if __name__ == "__main__":
    unittest.main()


class HandoffStatusTest(unittest.TestCase):
    def test_reads_status_of_that_dates_handoff(self):
        seen = {}

        def finder(token, db, hid):
            seen["hid"] = hid
            return {"properties": {"Status": {"select": {"name": "Processed"}}}}

        self.assertEqual(cov.handoff_status("2026-11-02", token="t", db_id="d", find_page=finder),
                         "Processed")
        self.assertEqual(seen["hid"], "routine-handoff::2026-11-02")

    def test_missing_page(self):
        self.assertEqual(cov.handoff_status("2026-11-02", token="t", db_id="d",
                                            find_page=lambda *a: None), "missing")

    def test_cli_failure_is_reported_not_raised(self):
        env = {k: v for k, v in os.environ.items() if k not in ("NOTION_TOKEN", "NOTION_DATABASE_ID")}
        out = subprocess.run(
            [sys.executable, os.path.join(ROOT, "fda_body_coverage.py"), "handoff-status",
             "--date", "2026-11-02"], capture_output=True, text=True, encoding="utf-8",
            env=env, check=True).stdout
        self.assertIn("handoff_status=error:KeyError", out)
