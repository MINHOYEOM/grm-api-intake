#!/usr/bin/env python3
"""Regression tests for the Git publication and DB coverage paths."""

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

import delta_bridge as db  # noqa: E402
import fda_body_coverage as fbc  # noqa: E402
from test_delta_bridge import (  # noqa: E402
    _COPYEDIT_HELD, _COPYEDIT_OK, _git_delta, _git_reader)

DATE = "2026-07-13"
DELTA = "web/data/deltas/delta_2026_07_13.json"
SCAFFOLD = "web/data/scaffolds/brief_web_2026_07_13.json"
REF = {"date": DATE, "sha256": "a" * 64, "rows": 1}


class BridgeNotionOffTest(unittest.TestCase):
    def setUp(self) -> None:
        self._cwd = os.getcwd()
        self._tmp = tempfile.TemporaryDirectory()
        os.chdir(self._tmp.name)
        self._out = os.path.join(self._tmp.name, "gh_output.txt")
        env = {"GITHUB_OUTPUT": self._out, "GITHUB_STEP_SUMMARY": os.path.join(self._tmp.name, "summary.md")}
        self._env = mock.patch.dict(os.environ, env)
        self._env.start()
        self._mirror = mock.patch.object(db, "git_read_handoff", return_value=None)
        self._mirror.start()
        self._git = None

    def tearDown(self) -> None:
        for p in (self._git, self._mirror, self._env):
            if p is not None:
                p.stop()
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def _files(self, files) -> None:
        self._git = mock.patch.object(db, "git_read_run_file", side_effect=_git_reader(files))
        self._git.start()

    def _outputs(self) -> dict:
        if not os.path.exists(self._out):
            return {}
        with open(self._out, encoding="utf-8") as f:
            return dict(ln.rstrip("\n").split("=", 1) for ln in f if "=" in ln)

    def _scaffold_text(self) -> str:
        return json.dumps({"schema_version": "grm-web-card/v1", "brief": {"run_date_kst": DATE},
                           "cards": [{"id": "mfds-1"}]}, ensure_ascii=False, indent=1) + "\n"


    def test_git_return_is_written_without_token_or_notion(self) -> None:
        g = dict(_git_delta("in the room"), handoff_ref=REF, deferred=[])
        self._files({"delta.json": g, "copyedit.json": _COPYEDIT_OK, "scaffold.json": self._scaffold_text()})
        rc = db.main(["--publish-date", DATE])
        self.assertEqual(rc, 0)
        out = self._outputs()
        self.assertEqual((out.get("wrote"), out.get("source"), out.get("date")), ("true", "git", DATE))
        written = json.loads(pathlib.Path(DELTA).read_text(encoding="utf-8"))
        self.assertEqual(written["cards"]["mfds-1"]["summary"], "in the room")
        self.assertTrue(pathlib.Path(SCAFFOLD).exists())


    def test_no_return_yet_is_a_clean_skip_not_a_notion_lookup(self) -> None:
        for files in ({}, {"delta.json": _git_delta("git"), "copyedit.json": _COPYEDIT_HELD}):
            with self.subTest(files=sorted(files)):
                if self._git is not None:
                    self._git.stop()
                self._files(files)
                rc = db.main(["--publish-date", DATE])
                self.assertEqual(rc, 0)
                self.assertEqual(self._outputs().get("wrote"), "false")
                self.assertFalse(pathlib.Path(DELTA).exists())
                os.remove(self._out)

    def test_adoptable_return_that_fails_validation_is_red(self) -> None:
        # git handoff 로 만든 호인데 스캐폴드가 없다 — 노션 모드라면 노션 예비 길로 넘어갔을 경우.
        g = dict(_git_delta("git"), handoff_ref=REF)
        self._files({"delta.json": g, "copyedit.json": _COPYEDIT_OK})
        rc = db.main(["--publish-date", DATE])
        self.assertEqual(rc, 1)
        self.assertFalse(pathlib.Path(DELTA).exists())
        bad = _git_delta("git")
        bad["publish_date"] = "2026-07-06"
        if self._git is not None:
            self._git.stop()
        self._files({"delta.json": bad, "copyedit.json": _COPYEDIT_OK})
        self.assertEqual(db.main(["--publish-date", DATE]), 1)

    def test_copyedit_done_but_unrecorded_is_red_not_a_clean_skip(self) -> None:
        # [11-23 사전 점검 B × 노션 off] 교열은 `완료`·published 로 마쳤는데 반납을 채택할 수 없다(델타 파일 없음).
        # 노션 예비 길이 없으니 초록 '반납 없음' 으로 끝나면 그 주가 조용히 발행되지 않는다 → exit 1.
        self._files({"copyedit.json": _COPYEDIT_OK})
        rc = db.main(["--publish-date", DATE])
        self.assertEqual(rc, 1)
        self.assertEqual(self._outputs().get("wrote"), "false")
        with open(os.environ["GITHUB_STEP_SUMMARY"], encoding="utf-8") as f:
            self.assertIn("교열 반납 미기록", f.read())
        # 이미 main 에 그 주 델타가 있으면(브릿지가 앞 회차에 가져갔다) 해당 없음 — 초록.
        os.remove(self._out)
        pathlib.Path(DELTA).parent.mkdir(parents=True, exist_ok=True)
        pathlib.Path(DELTA).write_text("{}", encoding="utf-8")
        self.assertEqual(db.main(["--publish-date", DATE]), 0)




class HandoffStatusNotionOffTest(unittest.TestCase):
    def test_status_uses_the_database(self) -> None:
        env = {"SUPABASE_URL": "https://x.supabase.co", "SUPABASE_SERVICE_ROLE_KEY": "K"}
        with mock.patch.dict(os.environ, env), \
                mock.patch.object(fbc, "handoff_status_db", return_value="New") as dbq, \
                mock.patch.object(fbc, "_emit") as emit:
            self.assertEqual(fbc.main(["handoff-status", "--date", "2026-11-30"]), 0)
        dbq.assert_called_once_with("2026-11-30", base_url=env["SUPABASE_URL"], service_key="K")
        emit.assert_called_once_with({"handoff_status": "New"})


class WiringAndInstructionsTest(unittest.TestCase):
    def _read(self, *parts) -> str:
        return ROOT.joinpath(*parts).read_text(encoding="utf-8")


    def test_editorial_instructions_do_not_wait_on_notion(self) -> None:
        for folder in (("company", "editorial"), ("company", "editorial", "next")):
            run = self._read(*folder, "RUN.md")
            desk = self._read(*folder, "desk.md")
            copyedit = self._read(*folder, "copyedit.md")
            for text, name in ((run, "RUN.md"), (desk, "desk.md"), (copyedit, "copyedit.md")):
                with self.subTest(folder=folder, name=name):
                    self.assertNotIn("OPEN(Status=New)", text)                 # 데스크의 노션 OPEN 확인
                    self.assertNotIn("백업 Routine 이 발행한다", text)
                    self.assertNotIn("CONSUMED` 로 바꾼다", text)               # 교열의 노션 마감
            self.assertIn("**Notion 마감은 없다.**", copyedit)

    def test_brief_prep_reads_git_not_notion(self) -> None:
        prep = self._read("company", "situation-room", "brief-prep.md")
        self.assertNotIn("collection://", prep)
        self.assertNotIn("백업 Routine '지금 실행'", prep)
        self.assertIn("claude/company-$D", prep)


if __name__ == "__main__":
    unittest.main()
