#!/usr/bin/env python3
"""[노션 은퇴 — 11-23 호 편집팀 발행 성공 뒤] GRM_INTAKE_NOTION=off 면 발행 길이 노션 없이 이어지는가.

지키는 것:
- 발행 브릿지: 노션 API 를 한 번도 부르지 않는다. NOTION_TOKEN 이 없어도 돈다. 정본은 git 반납 하나.
  · 반납이 아직 없거나 교열 `완료` 가 아니면 wrote=false, exit 0(30분 뒤 회차가 다시 본다).
  · 채택 조건을 채운 반납이 검증에서 떨어지면 노션으로 넘기지 않고 exit 1(조용히 끝나지 않게).
  · --consume(CONSUMED 마감)은 할 일이 없다. 노션 길(--source notion)을 요구해도 git 으로 간다.
- FDA 본문 회수 뒤 handoff 재생성의 안전 조건: off 면 노션이 아니라 DB 기록에 묻는다
  (노션 handoff 페이지가 없으니 노션에 물으면 늘 `missing` 이라 재생성이 조용히 꺼진다).
- 워크플로가 변수를 넘기고, CONSUMED 단계가 off 에서 돌지 않는다.
- 편집팀 지시문(실발행·next/)과 종합상황실 확인이 노션 OPEN 확인·CONSUMED 마감에 기대지 않는다.
기본(변수 없음·on)은 종전과 같다 — 그쪽은 tests/test_delta_bridge.py 가 지킨다.
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

import delta_bridge as db  # noqa: E402
import fda_body_coverage as fbc  # noqa: E402
from test_delta_bridge import (  # noqa: E402
    _COPYEDIT_HELD, _COPYEDIT_OK, _git_delta, _git_reader)

DATE = "2026-07-13"
DELTA = "web/data/deltas/delta_2026_07_13.json"
SCAFFOLD = "web/data/scaffolds/brief_web_2026_07_13.json"
REF = {"date": DATE, "sha256": "a" * 64, "rows": 1}


def _no_notion(*a, **k):
    raise AssertionError(f"노션 호출 금지(GRM_INTAKE_NOTION=off): {a[:2]}")


class BridgeNotionOffTest(unittest.TestCase):
    def setUp(self) -> None:
        self._cwd = os.getcwd()
        self._tmp = tempfile.TemporaryDirectory()
        os.chdir(self._tmp.name)
        self._out = os.path.join(self._tmp.name, "gh_output.txt")
        env = {k: v for k, v in os.environ.items()
               if k not in ("NOTION_TOKEN", db.ENV_DELTA_SOURCE, db.ENV_HANDOFF_BODY_SOURCE)}
        env.update({"GRM_INTAKE_NOTION": "off", "GITHUB_OUTPUT": self._out,
                    "GITHUB_STEP_SUMMARY": os.path.join(self._tmp.name, "summary.md")})
        self._env = mock.patch.dict(os.environ, env, clear=True)
        self._env.start()
        self._notion = mock.patch.object(db, "notion_api_request", side_effect=_no_notion)
        self._notion.start()
        # 노션 handoff 원문 조회도 막는다(handoff_ref 없는 호의 원문 정본화 경로).
        self._body = mock.patch.object(db, "fetch_handoff_body_full", side_effect=_no_notion)
        self._body.start()
        self._mirror = mock.patch.object(db, "git_read_handoff", return_value=None)
        self._mirror.start()
        self._git = None

    def tearDown(self) -> None:
        for p in (self._git, self._mirror, self._body, self._notion, self._env):
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

    def test_switch_reads_the_same_variable_as_the_collector(self) -> None:
        self.assertTrue(db.notion_off())
        for v in ("", "on", "false", "OFFX"):
            with self.subTest(v=v), mock.patch.dict(os.environ, {"GRM_INTAKE_NOTION": v}):
                self.assertFalse(db.notion_off())
        with mock.patch.dict(os.environ, {"GRM_INTAKE_NOTION": " OFF "}):
            self.assertTrue(db.notion_off())

    def test_git_return_is_written_without_token_or_notion(self) -> None:
        g = dict(_git_delta("in the room"), handoff_ref=REF, deferred=[])
        self._files({"delta.json": g, "copyedit.json": _COPYEDIT_OK, "scaffold.json": self._scaffold_text()})
        rc = db.main(["--db", "dbid", "--publish-date", DATE])      # 변수 GRM_DELTA_SOURCE 없음 = 기본 notion
        self.assertEqual(rc, 0)
        out = self._outputs()
        self.assertEqual((out.get("wrote"), out.get("source"), out.get("date")), ("true", "git", DATE))
        written = json.loads(pathlib.Path(DELTA).read_text(encoding="utf-8"))
        self.assertEqual(written["cards"]["mfds-1"]["summary"], "in the room")
        self.assertTrue(pathlib.Path(SCAFFOLD).exists())

    def test_explicit_notion_source_still_goes_git(self) -> None:
        self._files({"delta.json": _git_delta("git"), "copyedit.json": _COPYEDIT_OK})
        rc = db.main(["--db", "dbid", "--source", "notion", "--publish-date", DATE])
        self.assertEqual(rc, 0)
        self.assertEqual(self._outputs().get("source"), "git")

    def test_no_return_yet_is_a_clean_skip_not_a_notion_lookup(self) -> None:
        for files in ({}, {"delta.json": _git_delta("git"), "copyedit.json": _COPYEDIT_HELD}):
            with self.subTest(files=sorted(files)):
                if self._git is not None:
                    self._git.stop()
                self._files(files)
                rc = db.main(["--db", "dbid", "--publish-date", DATE])
                self.assertEqual(rc, 0)
                self.assertEqual(self._outputs().get("wrote"), "false")
                self.assertFalse(pathlib.Path(DELTA).exists())
                os.remove(self._out)

    def test_adoptable_return_that_fails_validation_is_red(self) -> None:
        # git handoff 로 만든 호인데 스캐폴드가 없다 — 노션 모드라면 노션 예비 길로 넘어갔을 경우.
        g = dict(_git_delta("git"), handoff_ref=REF)
        self._files({"delta.json": g, "copyedit.json": _COPYEDIT_OK})
        rc = db.main(["--db", "dbid", "--publish-date", DATE])
        self.assertEqual(rc, 1)
        self.assertFalse(pathlib.Path(DELTA).exists())
        bad = _git_delta("git")
        bad["publish_date"] = "2026-07-06"
        if self._git is not None:
            self._git.stop()
        self._files({"delta.json": bad, "copyedit.json": _COPYEDIT_OK})
        self.assertEqual(db.main(["--db", "dbid", "--publish-date", DATE]), 1)

    def test_copyedit_done_but_unrecorded_is_red_not_a_clean_skip(self) -> None:
        # [11-23 사전 점검 B × 노션 off] 교열은 `완료`·published 로 마쳤는데 반납을 채택할 수 없다(델타 파일 없음).
        # 노션 예비 길이 없으니 초록 '반납 없음' 으로 끝나면 그 주가 조용히 발행되지 않는다 → exit 1.
        self._files({"copyedit.json": _COPYEDIT_OK})
        rc = db.main(["--db", "dbid", "--publish-date", DATE])
        self.assertEqual(rc, 1)
        self.assertEqual(self._outputs().get("wrote"), "false")
        with open(os.environ["GITHUB_STEP_SUMMARY"], encoding="utf-8") as f:
            self.assertIn("교열 반납 미기록", f.read())
        # 이미 main 에 그 주 델타가 있으면(브릿지가 앞 회차에 가져갔다) 해당 없음 — 초록.
        os.remove(self._out)
        pathlib.Path(DELTA).parent.mkdir(parents=True, exist_ok=True)
        pathlib.Path(DELTA).write_text("{}", encoding="utf-8")
        self.assertEqual(db.main(["--db", "dbid", "--publish-date", DATE]), 0)

    def test_consume_is_a_no_op(self) -> None:
        self.assertEqual(db.main(["--db", "dbid", "--consume", "--publish-date", DATE]), 0)

    def test_default_on_still_requires_the_token(self) -> None:
        with mock.patch.dict(os.environ, {"GRM_INTAKE_NOTION": "on"}):
            self.assertEqual(db.main(["--db", "dbid"]), 1)


class HandoffStatusNotionOffTest(unittest.TestCase):
    def _run(self, env):
        out = []
        with mock.patch.dict(os.environ, env, clear=False), \
                mock.patch.object(fbc, "handoff_status_db", return_value="New") as dbq, \
                mock.patch.object(fbc, "handoff_status", return_value="missing") as nq, \
                mock.patch.object(fbc, "_emit", side_effect=out.append):
            self.assertEqual(fbc.main(["handoff-status", "--date", "2026-11-30"]), 0)
        return out[0]["handoff_status"], dbq, nq

    BASE = {"SUPABASE_URL": "https://x.supabase.co", "SUPABASE_SERVICE_ROLE_KEY": "K",
            "NOTION_TOKEN": "t", "NOTION_DATABASE_ID": "d"}

    def test_off_asks_the_db_even_if_the_status_variable_says_notion(self) -> None:
        for src in ("", "notion", "db"):
            with self.subTest(src=src):
                status, dbq, nq = self._run(dict(self.BASE, GRM_INTAKE_NOTION="off",
                                                 GRM_HANDOFF_STATUS_SOURCE=src))
                self.assertEqual(status, "New")
                dbq.assert_called_once()
                nq.assert_not_called()

    def test_on_keeps_the_old_default(self) -> None:
        status, dbq, nq = self._run(dict(self.BASE, GRM_INTAKE_NOTION="on", GRM_HANDOFF_STATUS_SOURCE=""))
        self.assertEqual(status, "missing")
        nq.assert_called_once()
        dbq.assert_not_called()


class WiringAndInstructionsTest(unittest.TestCase):
    def _read(self, *parts) -> str:
        return ROOT.joinpath(*parts).read_text(encoding="utf-8")

    def test_workflows_pass_the_switch(self) -> None:
        bridge = self._read(".github", "workflows", "grm-delta-bridge.yml")
        self.assertIn("GRM_INTAKE_NOTION: ${{ vars.GRM_INTAKE_NOTION || 'on' }}", bridge)
        # [노션 은퇴 ③] CONSUMED 마감 단계는 지웠다. 대신 잡 첫머리에서 off 가 아니면 빨갛게 멈춘다
        #   (노션 길로 기록하고 닫지 못하는 일이 없게).
        self.assertNotIn("--consume", bridge)
        guard = bridge[bridge.index("- name: 노션 off 확인"):]
        guard = guard[:guard.index("- name: 델타 브릿지 실행")]
        self.assertIn("if: vars.GRM_INTAKE_NOTION != 'off'", guard)
        self.assertIn("exit 1", guard)
        gate = self._read(".github", "workflows", "grm-fda-coverage-gate.yml")
        step = gate[gate.index("- name: handoff 상태 확인"):]
        step = step[:step.index("run:")]
        self.assertIn("GRM_INTAKE_NOTION: ${{ vars.GRM_INTAKE_NOTION || 'on' }}", step)

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
            self.assertIn("GRM_INTAKE_NOTION=off", run)
            self.assertIn("**Notion 마감은 없다.**", copyedit)

    def test_brief_prep_reads_git_not_notion(self) -> None:
        prep = self._read("company", "situation-room", "brief-prep.md")
        self.assertNotIn("collection://", prep)
        self.assertNotIn("백업 Routine '지금 실행'", prep)
        self.assertIn("claude/company-$D", prep)


if __name__ == "__main__":
    unittest.main()
