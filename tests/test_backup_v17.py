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
        # 월 09:07~14:37 KST — 백업 반납 시한 13:15 뒤에 브릿지 회차가 셋(13:37·14:07·14:37) 남는다.
        # 한 회차가 지각·누락돼도 반납이 남은 채 끝나지 않게(11-19 검토 6번).
        self.assertIn("'7,37 0-5 * * 1'", wf)
        self.assertEqual(bg.BRIDGE_LAST, "14:37")
        after = [hm for hm in ("13:07", "13:37", "14:07", "14:37") if bg.FINISH_DEADLINE < hm <= bg.BRIDGE_LAST]
        self.assertGreaterEqual(len(after), 3, after)

    def test_bridge_commit_message_names_who_wrote_it(self) -> None:
        # 11-19 검토 5번: 백업 글도 "편집팀 git 반납" 으로 적히던 것을 by 로 가른다(커밋 메시지 + run 요약).
        wf = (ROOT / ".github" / "workflows" / "grm-delta-bridge.yml").read_text(encoding="utf-8")
        self.assertIn('BY="${{ steps.bridge.outputs.by }}"', wf)
        self.assertIn('MSG="백업 v17 git 반납 (by=backup)"', wf)
        self.assertIn('MSG="편집팀 git 반납 (by=editorial)"', wf)
        self.assertIn("by=${BY:-notion}", wf)
        self.assertIn('"$GITHUB_STEP_SUMMARY"', wf.split('MSG="편집팀 git 반납 (by=editorial)"', 1)[1].split("- name:", 1)[0])


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

    def test_return_rejected_by_bridge_validation_is_not_usable(self) -> None:
        # 11-19 검토 1번 재현: 채택 조건(교열 `완료`·코드 검사 0·reject 0)은 채웠지만 브릿지의 뒤 검증
        # (`prepare_git_run` — 호 날짜)이 떨어뜨리는 반납. 예전 판정은 `usable=True` → SKIP 이었고 브릿지는
        # 거절해 아무도 발행하지 않았다. 이제 판정도 "쓸 수 없다" → 09:40 부터 RUN.
        wrong_date = _git_delta()
        wrong_date["publish_date"] = "2026-07-20"  # 브랜치 날짜 2026-07-13 과 다름
        files = {"delta.json": wrong_date, "copyedit.json": _COPYEDIT_OK}
        self.assertTrue(db.load_git_run(DATE, read=self._read(files))["adoptable"])  # 채택 조건은 채움
        with mock.patch.object(db, "git_read_handoff", return_value=None):
            ok, why = bg.usable(db, DATE, self._read(files), "")
            with self.assertRaises(db.DeltaBridgeError):  # 브릿지는 거절한다
                db._prepare_adoptable("", "", db.load_git_run(DATE, read=self._read(files)), self._read(files))
        self.assertFalse(ok)
        self.assertIn("브릿지 검증", why)
        st = _state(editorial_usable=ok, editorial_reason=why, desk={"status": "완료"},
                    copyedit={"status": "완료"})
        self.assertEqual(bg.decide(st, "09:45")[0], "RUN")
        self.assertEqual(bg.decide(st, "12:50", final=True)[0], "RUN")

    def test_gate_and_bridge_agree(self) -> None:
        """판정의 '쓸 수 있다'와 브릿지의 채택이 같은 답을 내는가 — 반납 꼴 여럿으로."""
        bad_env = _git_delta()
        bad_env["cards"] = []  # 봉투 형식 위반
        wrong_date = _git_delta()
        wrong_date["publish_date"] = "2026-07-20"
        no_scaffold = _git_delta()
        no_scaffold["handoff_ref"] = REF
        cases = {"정상": {"delta.json": _git_delta(), "copyedit.json": _COPYEDIT_OK},
                 "교열 보류": {"delta.json": _git_delta(), "copyedit.json": _COPYEDIT_HELD},
                 "봉투 위반": {"delta.json": bad_env, "copyedit.json": _COPYEDIT_OK},
                 "호 날짜 다름": {"delta.json": wrong_date, "copyedit.json": _COPYEDIT_OK},
                 "스캐폴드 없음": {"delta.json": no_scaffold, "copyedit.json": _COPYEDIT_OK}}
        env = {"GRM_INTAKE_NOTION": "off"}
        for name, files in cases.items():
            with self.subTest(name), mock.patch.dict(os.environ, env), \
                    mock.patch.object(db, "git_read_handoff", return_value=None), \
                    mock.patch.object(db, "notion_api_request", side_effect=AssertionError("노션 호출 금지")):
                gate_ok, _ = bg.usable(db, DATE, self._read(files), "")
                run = db.load_git_run(DATE, read=self._read(files))
                try:
                    bridge_ok = run["adoptable"] and db._prepare_adoptable("", "", run, self._read(files)) is not None
                except db.DeltaBridgeError:
                    bridge_ok = False
                self.assertEqual(gate_ok, bridge_ok, name)
                self.assertEqual(gate_ok, name == "정상", name)

    def test_usable_restores_notion_env(self) -> None:
        files = {"delta.json": _git_delta(), "copyedit.json": _COPYEDIT_OK}
        with mock.patch.dict(os.environ, {}, clear=False), \
                mock.patch.object(db, "git_read_handoff", return_value=None):
            os.environ.pop("GRM_INTAKE_NOTION", None)
            bg.usable(db, DATE, self._read(files), "")
            self.assertNotIn("GRM_INTAKE_NOTION", os.environ)

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

    def test_gate_read_failure_has_a_rule(self) -> None:
        # 11-19 검토 4번: 판정 exit 2(원격 읽기 실패) 때 기록 없이 끝나지 않게.
        text = self._read("company", "editorial", "backup.md")
        zero = text.split("## 0.", 1)[1].split("## 1.", 1)[0]
        self.assertIn("exit 2", zero)
        self.assertIn("세 번까지", zero)
        self.assertIn("판정: 읽기 실패", zero)
        self.assertIn("`--final` 이 exit 2", text)

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
