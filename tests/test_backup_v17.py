#!/usr/bin/env python3
"""[백업 v17] 노션 없이 도는 월요일 자동 백업 — 판정·브릿지·지시문.

지키는 것:
- 판정(`backup_gate.decide`): main 에 델타가 있거나 브릿지가 편집팀 반납을 쓸 수 있으면 백업은 하지 않는다(SKIP).
  편집팀이 스스로 끝냈으면(교열·데스크 `보류`·`실패`) 바로, 아니면 09:40 KST 부터 시작한다. 11:00 이후엔 시작하지 않는다.
  반납 직전(`--final`)에도 같은 두 조건을 다시 본다 — 한 호에 두 반납이 발행 길에 오르지 않게.
- 판정의 "쓸 수 있다"는 브릿지와 같은 함수(`load_git_run`·`load_run_scaffold`)다.
- 브릿지: 편집팀 반납이 채택 불가일 때만 `backup/` 을 같은 조건으로 가져간다. 둘 다 채택 가능하면 편집팀 것.
  편집팀 반납이 검증에서 떨어지면 백업으로 넘어가고, 백업도 없으면 종전처럼 빨간 run(off).
- 지시문이 노션을 읽거나 쓰지 않고, 편집팀 폴더를 고치지 않으며, 반납 전 점검 두 개를 돈다.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "company" / "editorial"))

import backup_gate as bg  # noqa: E402
import delta_bridge as db  # noqa: E402
from test_delta_bridge import _COPYEDIT_HELD, _COPYEDIT_OK, _git_delta, _git_reader  # noqa: E402

DATE = "2026-07-13"
DELTA = "web/data/deltas/delta_2026_07_13.json"
REF = {"date": DATE, "sha256": "a" * 64, "rows": 1}


def _state(**kw):
    base = {"main_delta": False, "handoff": True, "editorial_usable": False, "editorial_reason": "",
            "desk": None, "copyedit": None, "backup_usable": False}
    base.update(kw)
    return base


class GateDecideTest(unittest.TestCase):
    def test_already_on_main_is_skip(self) -> None:
        self.assertEqual(bg.decide(_state(main_delta=True), "10:00")[0], "SKIP")
        self.assertEqual(bg.decide(_state(main_delta=True), "10:00", final=True)[0], "SKIP")

    def test_usable_editorial_return_is_skip_even_late(self) -> None:
        for now in ("09:30", "09:50", "10:59"):
            self.assertEqual(bg.decide(_state(editorial_usable=True), now)[0], "SKIP", now)
        self.assertEqual(bg.decide(_state(editorial_usable=True), "11:30", final=True)[0], "SKIP")

    def test_editorial_finished_runs_immediately(self) -> None:
        for desk, ce in (({"status": "완료"}, {"status": "보류"}), ({"status": "완료"}, {"status": "실패"}),
                         ({"status": "보류"}, None), ({"status": "실패"}, None)):
            with self.subTest(desk=desk, ce=ce):
                self.assertEqual(bg.decide(_state(desk=desk, copyedit=ce), "09:30")[0], "RUN")

    def test_editorial_maybe_still_working_waits_until_grace(self) -> None:
        for desk, ce in ((None, None), ({"status": "완료"}, None), ({"status": "완료"}, {"status": "재작업"})):
            with self.subTest(desk=desk, ce=ce):
                self.assertEqual(bg.decide(_state(desk=desk, copyedit=ce), "09:39")[0], "WAIT")
                self.assertEqual(bg.decide(_state(desk=desk, copyedit=ce), "09:40")[0], "RUN")

    def test_completed_but_unusable_editorial_runs_after_grace(self) -> None:
        # 교열 `완료` 인데 브릿지가 쓸 수 없다(스캐폴드 없음 — 리허설 1120d 꼴) → 09:40 부터 백업.
        st = _state(desk={"status": "완료"}, copyedit={"status": "완료"})
        self.assertEqual(bg.decide(st, "09:35")[0], "WAIT")
        self.assertEqual(bg.decide(st, "09:45")[0], "RUN")

    def test_start_deadline_is_stop(self) -> None:
        self.assertEqual(bg.decide(_state(copyedit={"status": "보류"}), bg.START_DEADLINE)[0], "STOP")
        self.assertEqual(bg.decide(_state(handoff=False), "10:00")[0], "WAIT")
        self.assertEqual(bg.decide(_state(handoff=False), "11:05")[0], "STOP")

    def test_existing_backup_return_is_skip_but_final_still_runs(self) -> None:
        self.assertEqual(bg.decide(_state(backup_usable=True), "09:50")[0], "SKIP")
        self.assertEqual(bg.decide(_state(backup_usable=True), "12:50", final=True)[0], "RUN")

    def test_final_ignores_time(self) -> None:
        self.assertEqual(bg.decide(_state(handoff=False), "13:00", final=True)[0], "RUN")

    def test_rehearsal_ignores_main_and_time(self) -> None:
        st = _state(main_delta=True, desk={"status": "완료"})
        self.assertEqual(bg.decide(st, "20:00", rehearsal=True)[0], "RUN")
        self.assertEqual(bg.decide(_state(handoff=False), "09:00", rehearsal=True)[0], "STOP")

    def test_deadlines_line_up_with_editorial_and_bridge(self) -> None:
        run = (ROOT / "company" / "editorial" / "RUN.md").read_text(encoding="utf-8")
        self.assertIn("09:25", run)  # 교열 마감 — 유예는 그 뒤여야 한다
        self.assertLess("09:25", bg.EDITORIAL_GRACE)
        self.assertLess(bg.EDITORIAL_GRACE, bg.START_DEADLINE)
        wf = (ROOT / ".github" / "workflows" / "grm-delta-bridge.yml").read_text(encoding="utf-8")
        self.assertIn("'7,37 0-4 * * 1'", wf)  # 월 09:07~13:37 KST — 백업 반납 시한 13:15 뒤에 한 회차 남는다
        self.assertLess(bg.FINISH_DEADLINE, "13:37")


class GateUsableTest(unittest.TestCase):
    """판정의 '쓸 수 있다'가 브릿지 채택 조건과 같은가(같은 함수)."""

    def _read(self, files):
        return _git_reader(files)

    def test_editorial_usable_matches_bridge(self) -> None:
        ok, _ = bg.usable(db, DATE, self._read({"delta.json": _git_delta(), "copyedit.json": _COPYEDIT_OK}), "")
        self.assertTrue(ok)
        ok, why = bg.usable(db, DATE, self._read({"delta.json": _git_delta(), "copyedit.json": _COPYEDIT_HELD}), "")
        self.assertFalse(ok)
        self.assertIn("보류", why)

    def test_handoff_ref_without_scaffold_is_not_usable(self) -> None:
        d = _git_delta()
        d["handoff_ref"] = REF
        ok, why = bg.usable(db, DATE, self._read({"delta.json": d, "copyedit.json": _COPYEDIT_OK}), "")
        self.assertFalse(ok)
        self.assertIn("scaffold.json", why)

    def test_backup_subfolder_is_read(self) -> None:
        files = {"backup/delta.json": _git_delta(), "backup/copyedit.json": _COPYEDIT_OK}
        self.assertFalse(bg.usable(db, DATE, self._read(files), "")[0])
        self.assertTrue(bg.usable(db, DATE, self._read(files), db.BACKUP_SUBDIR)[0])


class BridgeBackupTest(unittest.TestCase):
    def setUp(self) -> None:
        self._cwd = os.getcwd()
        self._tmp = tempfile.TemporaryDirectory()
        os.chdir(self._tmp.name)
        self._out = os.path.join(self._tmp.name, "gh_output.txt")
        env = {k: v for k, v in os.environ.items()
               if k not in ("NOTION_TOKEN", db.ENV_DELTA_SOURCE, db.ENV_HANDOFF_BODY_SOURCE)}
        env.update({"GRM_INTAKE_NOTION": "off", "GITHUB_OUTPUT": self._out,
                    "GITHUB_STEP_SUMMARY": os.path.join(self._tmp.name, "summary.md")})
        self._patches = [mock.patch.dict(os.environ, env, clear=True),
                         mock.patch.object(db, "notion_api_request",
                                           side_effect=AssertionError("노션 호출 금지")),
                         mock.patch.object(db, "git_read_handoff", return_value=None)]
        for p in self._patches:
            p.start()

    def tearDown(self) -> None:
        for p in reversed(self._patches):
            p.stop()
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def _run(self, files) -> int:
        with mock.patch.object(db, "git_read_run_file", side_effect=_git_reader(files)):
            return db.main(["--db", "x", "--publish-date", DATE])

    def _outputs(self) -> dict:
        if not os.path.exists(self._out):
            return {}
        with open(self._out, encoding="utf-8") as f:
            return dict(ln.rstrip("\n").split("=", 1) for ln in f if "=" in ln)

    def _written(self) -> dict:
        with open(DELTA, encoding="utf-8") as f:
            return json.load(f)

    def test_backup_used_when_editorial_held(self) -> None:
        rc = self._run({"delta.json": _git_delta("편집팀"), "copyedit.json": _COPYEDIT_HELD,
                        "backup/delta.json": _git_delta("백업"), "backup/copyedit.json": _COPYEDIT_OK})
        self.assertEqual(rc, 0)
        self.assertEqual(self._written()["cards"]["mfds-1"]["summary"], "백업")
        out = self._outputs()
        self.assertEqual((out.get("wrote"), out.get("source"), out.get("by")), ("true", "git", "backup"))

    def test_editorial_wins_when_both_adoptable(self) -> None:
        rc = self._run({"delta.json": _git_delta("편집팀"), "copyedit.json": _COPYEDIT_OK,
                        "backup/delta.json": _git_delta("백업"), "backup/copyedit.json": _COPYEDIT_OK})
        self.assertEqual(rc, 0)
        self.assertEqual(self._written()["cards"]["mfds-1"]["summary"], "편집팀")
        self.assertEqual(self._outputs().get("by"), "editorial")

    def test_backup_only(self) -> None:
        rc = self._run({"backup/delta.json": _git_delta("백업"), "backup/copyedit.json": _COPYEDIT_OK})
        self.assertEqual(rc, 0)
        self.assertEqual(self._written()["cards"]["mfds-1"]["summary"], "백업")

    def test_backup_in_progress_is_clean_skip(self) -> None:
        rc = self._run({"copyedit.json": _COPYEDIT_HELD, "backup/delta.json": _git_delta("백업")})
        self.assertEqual(rc, 0)
        self.assertFalse(os.path.exists(DELTA))
        self.assertEqual(self._outputs().get("wrote"), "false")

    def test_editorial_validation_failure_falls_to_backup(self) -> None:
        bad = _git_delta("편집팀")
        bad["handoff_ref"] = REF  # scaffold.json 없음 → 검증 실패
        rc = self._run({"delta.json": bad, "copyedit.json": _COPYEDIT_OK,
                        "backup/delta.json": _git_delta("백업"), "backup/copyedit.json": _COPYEDIT_OK})
        self.assertEqual(rc, 0)
        self.assertEqual(self._written()["cards"]["mfds-1"]["summary"], "백업")

    def test_editorial_validation_failure_without_backup_is_still_red(self) -> None:
        bad = _git_delta("편집팀")
        bad["handoff_ref"] = REF
        self.assertEqual(self._run({"delta.json": bad, "copyedit.json": _COPYEDIT_OK}), 1)
        self.assertFalse(os.path.exists(DELTA))

    def test_second_return_after_first_written_is_compare_only(self) -> None:
        self.assertEqual(self._run({"backup/delta.json": _git_delta("백업"),
                                    "backup/copyedit.json": _COPYEDIT_OK}), 0)
        os.remove(self._out)
        db._GIT_FETCHED.clear()
        rc = self._run({"delta.json": _git_delta("편집팀"), "copyedit.json": _COPYEDIT_OK,
                        "backup/delta.json": _git_delta("백업"), "backup/copyedit.json": _COPYEDIT_OK})
        self.assertEqual(rc, 0)
        self.assertEqual(self._written()["cards"]["mfds-1"]["summary"], "백업")  # 먼저 기록된 쪽이 남는다
        self.assertEqual(self._outputs().get("wrote"), "false")


class BackupInstructionsTest(unittest.TestCase):
    def _read(self, *parts) -> str:
        return (ROOT.joinpath(*parts)).read_text(encoding="utf-8")

    def test_backup_md_is_git_only_and_runs_both_checks(self) -> None:
        text = self._read("company", "editorial", "backup.md")
        self.assertNotIn("notion-", text.lower())
        self.assertNotIn("collection://", text)
        for must in ("backup_gate.py $D", "backup_gate.py $D --final", "rehearsal_check.py company/run/$D/backup",
                     "merge_parts.py company/run/$D/backup", "assemble_publish_brief.py", "precheck.py terms",
                     "precheck.py deep", "claude/queue-handoff", '"by":"backup"', "git add -A` 금지"):
            self.assertIn(must, text, must)

    def test_backup_writes_only_its_subfolder(self) -> None:
        self.assertEqual(db.BACKUP_SUBDIR, "backup")
        text = self._read("company", "editorial", "backup.md")
        self.assertIn("`F` = `company/run/$D/backup/`", text)
        self.assertIn("`E`(편집팀 파일) 수정", text)

    def test_situation_room_and_routines_know_the_backup(self) -> None:
        prep = self._read("company", "situation-room", "brief-prep.md")
        self.assertIn("company/run/$D/backup/", prep)
        self.assertIn("백업: 반납", prep)
        routines = self._read("company", "ROUTINES.md")
        self.assertIn("`editorial/backup.md`", routines)
        for folder in (("company", "editorial"), ("company", "editorial", "next")):
            self.assertIn("company/editorial/backup.md", self._read(*folder, "RUN.md"))


if __name__ == "__main__":
    unittest.main()
