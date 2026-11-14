#!/usr/bin/env python3
"""[노션 끄기 2026-11-13] 수집기 밖에서 노션 Intake DB 를 읽고 쓰는 곳도 같은 변수(GRM_INTAKE_NOTION)를 따른다.

지키는 것:
- 원천 대조: off 면 '수집됨' 키를 정본(raw_signals)에서 읽는다 — 노션을 읽지 않는다. 기본(on)은 종전.
  (수집기가 노션에 행을 만들지 않는데 노션 기준으로 대조하면 전부 '놓침'으로 보인다.)
- 483·WL 노션 원문 동기화, 노션 행 Status 기록: off 면 워크플로 단계가 돌지 않는다. 기본은 종전 조건 그대로.
"""

from __future__ import annotations

import re
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import source_reconcile as sr  # noqa: E402

RUN = date(2026, 12, 1)             # 유예(2026-11-22) 이후 — 놓침이 놓침으로 판정되는 날
WF = ROOT / ".github" / "workflows"


def _doc(doc_id: str) -> "sr.SourceDoc":
    return sr.SourceDoc(document_id=doc_id, title=f"t {doc_id}", url=f"https://example.org/{doc_id}",
                        published="2026-11-25")


class ReconcileBasisTest(unittest.TestCase):
    ENV = {"NOTION_TOKEN": "t", "NOTION_DATABASE_ID": "d", "SUPABASE_URL": "https://x.supabase.co",
           "SUPABASE_SERVICE_ROLE_KEY": "KEY"}

    def _run(self, env_extra, *, notion_ids=("a",), db_ids=("a", "b"), db_error=None):
        spec = sr.SPEC_BY_KEY["wl"]
        env = dict(self.ENV, **env_extra)

        def db_keys(*_a, **_k):
            if db_error:
                raise db_error
            return {f"{spec.source}::{i}" for i in db_ids}, set()
        with patch.object(sr, "load_collected_keys",
                          return_value=({f"{spec.source}::{i}" for i in notion_ids}, set())) as notion, \
                patch.object(sr, "load_ledger", return_value={spec.source: {}}), \
                patch.object(sr, "load_collected_keys_supabase", side_effect=db_keys) as db:
            rep = sr.run(run_date=RUN, base_days=7, enf_days=30, specs=[spec],
                         fetchers={"wl": lambda s, e: sr.FetchOutcome(docs=[_doc("a"), _doc("b")])},
                         env=env)
        return rep, notion, db

    def test_default_reads_notion_and_has_no_new_key(self):
        for extra in ({}, {"GRM_INTAKE_NOTION": "on"}, {"GRM_INTAKE_NOTION": "false"}):
            with self.subTest(extra=extra):
                rep, notion, db = self._run(extra)
                notion.assert_called()
                db.assert_not_called()
                self.assertNotIn("collected_basis", rep)
                self.assertEqual(rep["sources"][0]["verdict"], sr.V_MISSED)   # 노션에는 a 뿐

    def test_off_reads_the_canonical_store_and_never_notion(self):
        rep, notion, db = self._run({"GRM_INTAKE_NOTION": "off"})
        notion.assert_not_called()
        db.assert_called_once()
        self.assertEqual(rep["collected_basis"], "supabase")
        self.assertEqual(rep["sources"][0]["verdict"], sr.V_OK)               # 정본에는 a·b 둘 다
        self.assertEqual(rep["totals"]["missed"], 0)

    def test_off_still_finds_what_the_store_does_not_have(self):
        rep, _notion, _db = self._run({"GRM_INTAKE_NOTION": "off"}, notion_ids=("a", "b"), db_ids=("a",))
        self.assertEqual(rep["sources"][0]["verdict"], sr.V_MISSED)
        self.assertEqual(rep["totals"]["missed"], 1)

    def test_off_needs_no_notion_credentials(self):
        rep, notion, _db = self._run({"GRM_INTAKE_NOTION": "off", "NOTION_TOKEN": "",
                                      "NOTION_DATABASE_ID": ""})
        notion.assert_not_called()
        self.assertEqual(rep["sources"][0]["verdict"], sr.V_OK)

    def test_off_store_failure_is_unreachable_not_missed(self):
        rep, notion, _db = self._run({"GRM_INTAKE_NOTION": "off"},
                                     db_error=sr.StoreReadError("정본(raw_signals) 조회 HTTP 500"))
        notion.assert_not_called()                                            # 노션으로 되돌아가지 않는다
        self.assertEqual(rep["sources"][0]["verdict"], sr.V_STORE_UNREACHABLE)
        self.assertEqual(rep["totals"]["missed"], 0)

    def test_off_without_supabase_settings_is_unreachable(self):
        rep, _notion, db = self._run({"GRM_INTAKE_NOTION": "off", "SUPABASE_SERVICE_ROLE_KEY": ""})
        db.assert_not_called()
        self.assertEqual(rep["sources"][0]["verdict"], sr.V_STORE_UNREACHABLE)

    def test_off_does_not_run_the_shadow_against_itself(self):
        rep, _notion, db = self._run({"GRM_INTAKE_NOTION": "off",
                                      "ENABLE_RECONCILE_SUPABASE_SHADOW": "true"})
        self.assertNotIn("shadow_supabase", rep)
        db.assert_called_once()

    def test_workflow_passes_the_switch_default_on(self):
        wf = (WF / "grm-source-reconcile.yml").read_text(encoding="utf-8")
        self.assertIn("GRM_INTAKE_NOTION: ${{ vars.GRM_INTAKE_NOTION || 'on' }}", wf)


def _step(workflow: str, name_part: str) -> str:
    text = (WF / workflow).read_text(encoding="utf-8")
    blocks = re.split(r"\n(?=      - name: )", text)
    found = [b for b in blocks if b.lstrip().startswith("- name: ") and name_part in b.splitlines()[0]]
    assert len(found) == 1, (workflow, name_part, len(found))
    return found[0]


def _if_line(step: str) -> str:
    lines = [ln.strip() for ln in step.splitlines() if ln.strip().startswith("if:")]
    assert len(lines) == 1, lines
    return lines[0]


class WorkflowStepGateTest(unittest.TestCase):
    OFF = "vars.GRM_INTAKE_NOTION != 'off'"

    def test_notion_raw_sync_steps_skip_when_off_and_keep_their_old_condition(self):
        for name in ("Sync empty Notion 483 rows", "Sync Notion WL rows"):
            with self.subTest(step=name):
                cond = _if_line(_step("grm-fda483-ocr-recovery.yml", name))
                self.assertEqual(
                    cond, "if: ${{ (github.event_name == 'schedule' || inputs.apply) && " + self.OFF + " }}")

    def test_the_recovery_itself_is_not_gated(self):
        text = (WF / "grm-fda483-ocr-recovery.yml").read_text(encoding="utf-8")
        self.assertEqual(text.count(self.OFF), 2)        # 노션 동기화 두 단계뿐 — 회수·findings 는 그대로

    def test_notion_status_sync_step_skips_when_off(self):
        cond = _if_line(_step("grm-delta-bridge.yml", "노션 행 Status 기록"))
        self.assertEqual(cond, "if: steps.bridge.outputs.wrote == 'true' && "
                               "steps.bridge.outputs.source == 'git' && " + self.OFF)
        text = (WF / "grm-delta-bridge.yml").read_text(encoding="utf-8")
        self.assertEqual(text.count(self.OFF), 1)        # 브릿지의 다른 단계는 건드리지 않는다


if __name__ == "__main__":
    unittest.main()
