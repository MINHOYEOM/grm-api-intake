#!/usr/bin/env python3
"""[166 2026-11-24] 옛 본문에만 남은 findings 를 '대체됨'으로 숨기는 판정 — 네트워크 없음.

지키는 것:
  · 새 글이 모두 들어가 정리(번역·반려·범위 밖)되고 하나 이상 공개된 문서에서만 숨긴다.
  · 지금 공개 중(scope ok)인 옛 행만 대상 — 반려·범위 밖·이미 숨은 행, 사람 결정 칸은 건드리지 않는다.
  · 166 미적용이면 경고만, DB 재확인과 어긋나면(건너뜀) 검사가 막는다.
  · 코드와 migration 의 계약(함수 이름·공개 조건·허용값)이 같다. 앞으로 scope 일괄 재계산 migration 은 superseded 를 제외한다.
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import findings_review_queue as frq  # noqa: E402
import raw_body_followup as rbf  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
MIG = ROOT / "web" / "migrations" / "166_findings_superseded.sql"


def _row(fid, text, *, scope="ok", review="accepted", ko="번역", lang="EN"):
    return {"finding_id": fid, "raw_signal_id": "r1", "finding_text": text, "scope_status": scope,
            "review_status": review, "finding_text_ko": ko, "finding_language": lang,
            "published_date": "2026-11-18"}


def _plan(stale, new_texts, restore=()):
    return {"stale": list(stale), "restore": list(restore),
            "new_extracted": [{"finding_text": t} for t in new_texts]}


class PlanSupersedeTest(unittest.TestCase):

    def test_hides_public_stale_when_all_new_are_settled_and_one_public(self):
        live = [_row("old", "Specifically, blob"), _row("old-rej", "Specifically, x", review="rejected"),
                _row("n1", "1. A"), _row("n2", "2. B", review="rejected", ko="")]
        items, why = rbf.plan_supersede("r1", _plan(["old", "old-rej"], ["1. A", "2. B"]), live)
        self.assertEqual(why, "")
        self.assertEqual([it["finding_id"] for it in items], ["old"])      # 반려 행은 이미 비공개 — 대상 아님
        self.assertEqual(items[0]["superseded_by"], ["n1", "n2"])
        self.assertEqual(items[0]["raw_signal_id"], "r1")

    def test_waits_while_new_findings_not_inserted(self):
        live = [_row("old", "Specifically, blob"), _row("n1", "1. A")]
        self.assertEqual(rbf.plan_supersede("r1", _plan(["old"], ["1. A", "2. B"]), live), ([], "new_not_inserted"))

    def test_waits_while_new_findings_untranslated(self):
        live = [_row("old", "Specifically, blob"), _row("n1", "1. A"), _row("n2", "2. B", review="needs_review", ko="")]
        self.assertEqual(rbf.plan_supersede("r1", _plan(["old"], ["1. A", "2. B"]), live), ([], "new_untranslated"))

    def test_waits_when_no_new_finding_is_public(self):
        live = [_row("old", "Specifically, blob"), _row("n1", "1. A", scope="non_pharma")]
        self.assertEqual(rbf.plan_supersede("r1", _plan(["old"], ["1. A"]), live), ([], "no_public_replacement"))

    def test_waits_while_restore_pending(self):
        live = [_row("old", "Specifically, blob"), _row("n1", "1. A")]
        plan = _plan(["old"], ["1. A"], restore=[{"finding_id": "x"}])
        self.assertEqual(rbf.plan_supersede("r1", plan, live), ([], "restore_pending"))

    def test_already_hidden_or_out_of_scope_stale_is_not_a_target(self):
        live = [_row("old", "Specifically, blob", scope="superseded"), _row("old2", "y", scope="non_pharma"),
                _row("n1", "1. A")]
        self.assertEqual(rbf.plan_supersede("r1", _plan(["old", "old2"], ["1. A"]), live), ([], ""))

    def test_korean_original_counts_as_public(self):
        live = [_row("old", "옛"), _row("n1", "1. 새", ko="", lang="KO")]
        items, why = rbf.plan_supersede("r1", _plan(["old"], ["1. 새"]), live)
        self.assertEqual((len(items), why), (1, ""))


class FollowupSupersedeTest(unittest.TestCase):
    """followup(supersede=True) — 조회 칸·호출·미적용 경고·어긋남 검사."""

    def _sig(self, body):
        import json
        # 분류 재구성이 없는 소스(483)로 — 이 시험은 숨김 판정만 본다(분류는 test_raw_body_followup).
        return {"raw_signal_id": "r1", "source": "FDA 483",
                "raw_json": json.dumps({"fda483_body_full": body}), "row_json": "{}"}

    @staticmethod
    def _extract(sig):
        import json
        body = json.loads(sig["raw_json"])["fda483_body_full"]
        return [{"finding_id": f"x-{t}", "finding_text": t} for t in body.split("|")]

    def _run(self, supersede_fn, dry_run=False):
        live = [_row("old", "Specifically, blob"), _row("n1", "1. A")]
        selects = []

        def get_rows(b, k, table, select, key, ids):
            selects.append(select)
            return live
        return rbf.followup("https://x.supabase.co", "k",
                            [(self._sig("Specifically, blob"), self._sig("1. A"))], dry_run=dry_run,
                            get_rows=get_rows, append_fn=lambda *a: (0, 0, False, []),
                            extract=self._extract, supersede=True, supersede_fn=supersede_fn,
                            changed_by="t")[0], selects

    def test_calls_rpc_with_items_and_counts(self):
        calls = []

        def fn(base, key, items, changed_by):
            calls.append((items, changed_by))
            return {"superseded": len(items), "skipped": []}
        summary, selects = self._run(fn)
        for col in ("scope_status", "review_status", "finding_text_ko", "finding_language"):
            self.assertIn(col, selects[0])
        self.assertEqual([it["finding_id"] for it in calls[0][0]], ["old"])
        self.assertEqual((summary["supersede_due"], summary["superseded"]), (1, 1))
        self.assertEqual(rbf.unresolved(summary), [])

    def test_dry_run_counts_without_calling(self):
        summary, _ = self._run(lambda *a: self.fail("dry-run 은 숨기지 않는다"), dry_run=True)
        self.assertEqual((summary["supersede_due"], summary["superseded"]), (1, 0))

    def test_unavailable_before_166_is_a_warning_not_a_failure(self):
        summary, _ = self._run(lambda *a: {"unavailable": True})
        self.assertTrue(summary["supersede_unavailable"])
        self.assertEqual(rbf.unresolved(summary), [])

    def test_db_disagreement_is_blocked_by_check(self):
        summary, _ = self._run(lambda *a: {"superseded": 0,
                                           "skipped": [{"finding_id": "old", "why": "replacement_not_public"}]})
        self.assertTrue(any(r.startswith("supersede_skipped") for r in rbf.unresolved(summary)))

    def test_rpc_error_is_a_write_error(self):
        summary, _ = self._run(lambda *a: {"error": "http_500"})
        self.assertEqual(summary["write_errors"], 1)


class ContractTest(unittest.TestCase):
    """코드와 migration 166·공개 게이트가 같은 계약을 쓴다."""

    def setUp(self):
        self.sql = MIG.read_text(encoding="utf-8")

    def test_rpc_name_and_value(self):
        self.assertIn(f"function public.{rbf.SUPERSEDE_RPC}(p_items jsonb", self.sql)
        self.assertIn("'superseded'", self.sql)
        self.assertRegex(self.sql, r"check \(scope_status in \('ok', 'non_pharma', 'fragment', 'superseded'\)\)")

    def test_public_condition_matches_034_gate(self):
        """DB 함수의 '공개' 조건 = 034 공개 정책 = rbf.is_public."""
        gate = (ROOT / "web" / "migrations" / "034_findings_hide_rejected.sql").read_text(encoding="utf-8")
        for frag in ("finding_text_ko <> ''", "finding_language = 'KO'", "scope_status = 'ok'", "review_status <> 'rejected'"):
            self.assertIn(frag, gate)
        for frag in ("f.scope_status = 'ok'", "f.review_status <> 'rejected'", "f.finding_text_ko <> ''", "f.finding_language = 'KO'"):
            self.assertIn(frag, self.sql)
        self.assertTrue(rbf.is_public(_row("a", "t")))
        self.assertFalse(rbf.is_public(_row("a", "t", ko="")))
        self.assertTrue(rbf.is_public(_row("a", "t", ko="", lang="KO")))
        self.assertFalse(rbf.is_public(_row("a", "t", review="rejected")))
        self.assertFalse(rbf.is_public(_row("a", "t", scope="superseded")))

    def test_function_never_writes_human_decision_columns(self):
        body = self.sql.split("create or replace function public.grm_findings_supersede", 1)[1].split("$$;", 1)[0]
        for col in ("review_status =", "finding_text_ko =", "translation_method ="):
            self.assertNotIn(f"set {col}", body)
        self.assertIn("update public.findings set scope_status = 'superseded'", body)

    def test_anon_and_authenticated_revoked_explicitly(self):
        for fn in ("grm_findings_supersede(jsonb, text)", "grm_findings_unsupersede(text[])"):
            for role in ("anon", "authenticated"):
                self.assertIn(f"revoke all on function public.{fn} from {role};", self.sql)


class FutureMigrationGuardTest(unittest.TestCase):
    """166 이후 scope_status 를 일괄 재계산하는 migration 은 'superseded' 행을 제외해야 한다(되살리면 옛 덩어리가 다시 공개)."""

    def test_later_scope_rewrites_exclude_superseded(self):
        offenders = []
        for path in sorted((ROOT / "web" / "migrations").glob("*.sql")):
            m = re.match(r"(\d+)_", path.name)
            if not m or int(m.group(1)) <= 166:
                continue
            text = path.read_text(encoding="utf-8")
            if re.search(r"set\s+scope_status\s*=", text, re.I) and "superseded" not in text:
                offenders.append(path.name)
        self.assertEqual(offenders, [])


class ReviewQueueTest(unittest.TestCase):

    def test_superseded_rows_are_not_review_backlog(self):
        from datetime import datetime, timedelta, timezone
        now = datetime(2026, 12, 31, tzinfo=timezone.utc)
        old = (now - timedelta(days=60)).isoformat()
        rows = [{"finding_id": "a", "source": "FDA Warning Letter", "scope_status": "superseded",
                 "document_id": "d", "firm_name": "f", "ingested_at": old}]
        report = frq.summarize(rows, now=now, max_age_days=3)
        self.assertEqual(report["overdue"], 0)
        self.assertEqual(report["by_scope"], {"superseded": 1})


if __name__ == "__main__":
    unittest.main()
