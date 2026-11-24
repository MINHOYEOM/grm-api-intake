#!/usr/bin/env python3
"""[본문 교체 후속 2026-11-24] 본문이 바뀐 행은 같은 함수로 재분류·findings 후속을 받는다. 네트워크 없음.

지키는 것:
  · 재수집 도구(backfill_wl_body_recovery)·매일 원문 갱신(raw_update R4)이 본문을 바꾸면 후속이 **반드시** 불린다.
  · 분류(row_json)는 일일 수집과 같은 build_wl_item 판정 — 본문에 cGMP 위반이 생기면 Tier 1→Tier 3.
  · 사람이 정한 값은 덮지 않는다 — 기존 findings 행은 어떤 칸도 쓰지 않고, row_json 의 분류 밖 칸은 그대로.
  · 원문을 바꾸는 모듈이 WL 본문 칸을 다루면 이 후속을 import 해야 한다(정적 가드).
"""
from __future__ import annotations

import ast
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import backfill_wl_body_recovery as rec  # noqa: E402
import collect_intake as ci  # noqa: E402
import findings_store  # noqa: E402
import grm_findings as gf  # noqa: E402
import raw_body_followup as rbf  # noqa: E402
import raw_update as ru  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
WL = "FDA Warning Letter"
_URL = ("https://www.fda.gov/inspections-compliance-enforcement-and-criminal-investigations/"
        "warning-letters/acme-pharma-123")
_BASE_RAW = {
    "firm": "Acme Pharma", "posted_date": "11/18/2026", "letter_date": "11/10/2026",
    "issuing_office": "Center for Drug Evaluation and Research | CDER",
    "subject": "Unapproved New Drugs/Misbranded", "url": _URL,
}
# 옛 본문 = 11-24 결손 형태(회신 부적절 단락의 'Specifically,' 부터 시작). 새 본문 = 편지 처음부터.
_OLD_BODY = "Specifically, your response is inadequate because you did not provide a remediation plan."
_NEW_BODY = (
    "Dear Mr. Doe: The U.S. Food and Drug Administration inspected your drug manufacturing facility. "
    "CGMP Violations: 1. Your firm failed to establish an adequate quality unit with written "
    "responsibilities and procedures (21 CFR 211.22(d)). Specifically, your response is inadequate "
    "because you did not provide a remediation plan. 2. Your firm failed to validate the manufacturing "
    "processes for its drug products (21 CFR 211.100(a)). Conclusion The violations cited in this letter "
    "are not intended to be an all-inclusive list."
)


def _record(body: str, **extra_raw) -> dict:
    raw = dict(_BASE_RAW, wl_body_full=body, **extra_raw)
    item = rbf.rebuild_wl_item(raw, record_tier=False)
    return findings_store.raw_signal_from_intake_item(item, collected_at="2026-11-18T21:00:00Z")


def _tier(row_json) -> str:
    return json.loads(row_json)["signal_tier"]


class ClassificationTest(unittest.TestCase):

    def test_body_with_cgmp_violations_reclassifies_tier1_to_tier3(self):
        old, new = _record(_OLD_BODY), _record(_NEW_BODY)
        self.assertEqual((_tier(old["row_json"]), _tier(new["row_json"])), ("Tier 1", "Tier 3"))
        # 옛 row_json 에 새 본문 → 분류 칸만 새 판정
        got = rbf.reclassified_row_json(WL, json.loads(new["raw_json"]), old["row_json"])
        self.assertEqual(_tier(got), "Tier 3")

    def test_non_classification_keys_are_kept(self):
        """row_json 의 분류 밖 칸(사람·보강이 채운 국가 등)은 그대로 — 분류 칸만 바꾼다."""
        old = _record(_OLD_BODY)
        row = json.loads(old["row_json"])
        row.update(site_country="India", region_jurisdiction="Manually set")
        got = json.loads(rbf.reclassified_row_json(WL, json.loads(_record(_NEW_BODY)["raw_json"]),
                                                   gf.canonical_json(row)))
        keys = set(rbf.CLASSIFICATION_KEYS)
        self.assertEqual({k: v for k, v in got.items() if k not in keys},
                         {k: v for k, v in row.items() if k not in keys})
        self.assertEqual(got["site_country"], "India")

    def test_unsupported_source_returns_none(self):
        self.assertIsNone(rbf.reclassified_row_json("FDA 483", {"fda483_body_full": "x"}, "{}"))

    def test_rebuild_does_not_double_count_tier_observer(self):
        with mock.patch.object(ci, "record_tier_decision") as rec_tier:
            rbf.reclassified_row_json(WL, json.loads(_record(_NEW_BODY)["raw_json"]), "{}")
        rec_tier.assert_not_called()


class PlanFindingsTest(unittest.TestCase):
    """기존 findings 는 쓰지 않는다 — 새 본문에만 있는 글은 넣고, 옛 본문에만 있는 행은 목록만."""

    @staticmethod
    def _extract(sig):
        body = json.loads(sig["raw_json"]).get("wl_body_full") or ""
        return [{"finding_id": f"f-{i}-{abs(hash(t)) % 1000}", "finding_text": t, "raw_signal_id": "r1"}
                for i, t in enumerate(x.strip() for x in body.split("|") if x.strip())]

    def _sig(self, body):
        return {"raw_signal_id": "r1", "source": WL, "raw_json": json.dumps({"wl_body_full": body}),
                "row_json": "{}"}

    def test_insert_new_only_keep_human_rows_and_list_stale(self):
        old, new = self._sig("garbage blob"), self._sig("Violation A | Violation B")
        live = [
            # 사람이 반려한 행 — 새 본문에도 같은 글이 있으면 그대로(다시 넣지도, 바꾸지도 않는다)
            {"finding_id": "keep", "raw_signal_id": "r1", "finding_text": "Violation A",
             "review_status": "rejected", "finding_text_ko": "사람 번역"},
            # 옛 본문에만 있던 번역·승격된 행 — 건드리지 않고 stale 목록에만
            {"finding_id": "old", "raw_signal_id": "r1", "finding_text": "garbage blob",
             "review_status": "accepted", "finding_text_ko": "옛 번역"},
        ]
        p = rbf.plan_findings(old, new, live, extract=self._extract)
        self.assertEqual([f["finding_text"] for f in p["insert"]], ["Violation B"])
        self.assertEqual(p["stale"], ["old"])
        self.assertEqual(p["restore"], [])

    def test_restore_target_is_not_inserted_twice(self):
        old, new = self._sig("Your firm failed"), self._sig("Your firm failed to validate.")
        live = [{"finding_id": "f-0-x", "raw_signal_id": "r1", "finding_text": "Your firm failed",
                 "published_date": "2026-11-01"}]

        def extract(sig):
            body = json.loads(sig["raw_json"])["wl_body_full"]
            return [{"finding_id": "f-0-x" if body == "Your firm failed" else "f-new", "finding_text": body}]
        p = rbf.plan_findings(old, new, live, extract=extract)
        self.assertEqual([it["finding_id"] for it in p["restore"]], ["f-0-x"])
        self.assertEqual((p["insert"], p["stale"]), ([], []))


class FollowupTest(unittest.TestCase):

    def _pair(self, live_text="Specifically, your response is inadequate"):
        old, new = _record(_OLD_BODY), _record(_NEW_BODY)
        return old, new

    def test_apply_appends_only_new_findings_and_never_patches(self):
        old, new = self._pair()
        old_findings = [dict(f, review_status="accepted", finding_text_ko="옛 번역")
                        for f in rbf.fe.findings_from_raw_signal(old)]
        appended, gets = [], []

        def get_rows(b, k, table, select, key, ids):
            gets.append(table)
            return [{"finding_id": f["finding_id"], "raw_signal_id": f["raw_signal_id"],
                     "finding_text": f["finding_text"], "published_date": "2026-11-18"} for f in old_findings]

        def append(b, k, findings):
            appended.extend(findings)
            return len(findings), 0, False, []
        summary, plan = rbf.followup("https://x.supabase.co", "k", [(old, new)], dry_run=False,
                                     get_rows=get_rows, append_fn=append)
        new_texts = {f["finding_text"] for f in rbf.fe.findings_from_raw_signal(new)}
        self.assertTrue(appended)
        self.assertTrue({f["finding_text"] for f in appended} <= new_texts)
        self.assertFalse({f["finding_id"] for f in appended} & {f["finding_id"] for f in old_findings})
        self.assertEqual(gets, ["findings"])            # 조회 1회 · 기존 행 쓰기 경로 없음
        self.assertEqual(summary["inserted"], len(appended))
        self.assertEqual(summary["row_json_stale"], 0)  # 새 행은 이미 새 판정(Tier 3)
        self.assertEqual(summary["tier_transitions"], {"Tier 1→Tier 3": 1})
        self.assertEqual(rbf.unresolved(summary), [])

    def test_dry_run_counts_pending_and_check_blocks(self):
        old, new = self._pair()
        summary, _ = rbf.followup("https://x.supabase.co", "k", [(old, new)], dry_run=True,
                                  get_rows=lambda *a: [],
                                  append_fn=lambda *a: self.fail("dry-run 은 쓰지 않는다"))
        self.assertGreater(summary["pending_insert"], 0)
        self.assertIn("pending_insert", " ".join(rbf.unresolved(summary)))

    def test_stored_tier_not_reclassified_is_flagged(self):
        """본문은 새것인데 row_json 이 옛 판정(Tier 1) 그대로면 row_json_stale — 검사가 막는다."""
        old, new = self._pair()
        stale_new = dict(new, row_json=old["row_json"])
        summary, _ = rbf.followup("https://x.supabase.co", "k", [(old, stale_new)], dry_run=True,
                                  get_rows=lambda *a: [], append_fn=lambda *a: (0, 0, False, []))
        self.assertEqual(summary["row_json_stale"], 1)
        self.assertEqual(summary["per_row"][0]["row_json_stale"]["signal_tier"], ["Tier 1", "Tier 3"])
        self.assertTrue(any(r.startswith("row_json_stale") for r in rbf.unresolved(summary)))

    def test_unchanged_body_is_skipped(self):
        old = _record(_NEW_BODY)
        summary, _ = rbf.followup("https://x.supabase.co", "k", [(old, dict(old))], dry_run=False,
                                  get_rows=lambda *a: self.fail("조회 불필요"),
                                  append_fn=lambda *a: self.fail("쓰기 불필요"))
        self.assertEqual(summary["rows"], 0)


class RecoveryCallsFollowupTest(unittest.TestCase):
    """재수집 도구가 본문을 바꾸면 후속이 **반드시** 불린다 — 안 불리면 이 시험이 실패한다."""

    def _row(self):
        old = _record(_OLD_BODY)
        return {"raw_signal_id": old["raw_signal_id"], "document_id": old["document_id"],
                "collected_at": "2026-11-18T21:00:00Z", "raw_sha256": old["raw_sha256"],
                "raw_json": old["raw_json"], "row_json": old["row_json"]}

    def _run(self, dry_run):
        calls = []

        def followup(base, key, pairs, dry_run):
            calls.append((list(pairs), dry_run))
            return {"rows": len(calls[-1][0]), "write_errors": 0}, {"items": [{"finding_id": "f"}]}
        plans = []
        report, code = rec.run(
            base_url="https://x.supabase.co", service_key="k", dry_run=dry_run, delay_seconds=0,
            fetch_raw_signals=lambda base, key, **kw: [self._row()],
            fetch_letter=lambda url: ("Dear Mr. Doe", _NEW_BODY, "ok"),
            upsert_raw_signal=mock.MagicMock(return_value=(200, [{}], "")),
            sleeper=lambda _s: None, rebuild_body_start=True, followup_fn=followup,
            plan_sink=plans.append)
        return report, code, calls, plans

    def test_apply_calls_followup_with_old_and_new_rows(self):
        report, code, calls, plans = self._run(dry_run=False)
        self.assertEqual(code, 0)
        self.assertEqual(report.recovered, 1)
        self.assertEqual(len(calls), 1, "본문을 바꿨는데 재분류·findings 후속이 불리지 않았다")
        (old, new), = calls[0][0]
        self.assertEqual(calls[0][1], False)
        self.assertEqual(json.loads(old["raw_json"])["wl_body_full"], _OLD_BODY)
        self.assertEqual(json.loads(new["raw_json"])["wl_body_full"], _NEW_BODY)
        self.assertEqual(new["source"], WL)                     # 후속이 소스를 알아야 거르지 않는다
        self.assertEqual((_tier(old["row_json"]), _tier(new["row_json"])), ("Tier 1", "Tier 3"))
        self.assertEqual(report.restore_plan_items, 1)
        self.assertEqual(len(plans), 1)

    def test_dry_run_also_reports_followup(self):
        report, _code, calls, _plans = self._run(dry_run=True)
        self.assertEqual([c[1] for c in calls], [True])
        self.assertEqual(report.followup["rows"], 1)

    def test_followup_write_error_fails_the_run(self):
        def followup(base, key, pairs, dry_run):
            return {"write_errors": 1}, {"items": []}
        _report, code = rec.run(
            base_url="https://x.supabase.co", service_key="k", dry_run=False, delay_seconds=0,
            fetch_raw_signals=lambda base, key, **kw: [self._row()],
            fetch_letter=lambda url: ("Dear Mr. Doe", _NEW_BODY, "ok"),
            upsert_raw_signal=mock.MagicMock(return_value=(200, [{}], "")),
            sleeper=lambda _s: None, rebuild_body_start=True, followup_fn=followup)
        self.assertEqual(code, 1)

    def test_followup_with_real_rows_from_recovery_is_not_filtered_out(self):
        """조회 칸이 좁아(source 없음) 후속이 모든 행을 걸러 버리던 결함 재발 방지 — 실제 후속 함수로."""
        captured = {}

        def followup(base, key, pairs, dry_run):
            return rbf.followup(base, key, pairs, dry_run=dry_run,
                                get_rows=lambda *a: captured.setdefault("rows", []),
                                append_fn=lambda *a: (0, 0, False, []))
        report, _code = rec.run(
            base_url="https://x.supabase.co", service_key="k", dry_run=True, delay_seconds=0,
            fetch_raw_signals=lambda base, key, **kw: [self._row()],
            fetch_letter=lambda url: ("Dear Mr. Doe", _NEW_BODY, "ok"),
            upsert_raw_signal=mock.MagicMock(), sleeper=lambda _s: None,
            rebuild_body_start=True, followup_fn=followup)
        self.assertEqual(report.followup["rows"], 1)
        self.assertGreater(report.followup["pending_insert"], 0)


class RawUpdateCallsFollowupTest(unittest.TestCase):
    """매일 원문 갱신(R4)도 본문이 바뀌면 같은 분류 함수로 row_json 을 바꾸고 같은 후속을 부른다."""

    def test_r4_reclassifies_and_calls_followup(self):
        old = _record(_OLD_BODY)
        stored = dict(old)
        today_item = rbf.rebuild_wl_item(dict(_BASE_RAW, wl_body_full=_NEW_BODY), record_tier=False)

        class _Resp:
            status_code = 200

            def __init__(self, rows):
                self._rows = rows

            def json(self):
                return self._rows

        def get(url, params=None, headers=None, timeout=None):
            return _Resp([stored] if url.endswith("/raw_signals") else [])
        applied, called = [], []

        def apply_fn(base, key, **kw):
            applied.append(kw)
            return {"status": "applied"}
        real = rbf.followup

        def spy(*a, **kw):
            called.append(len(a[2]))
            return real(*a, **kw)
        with mock.patch.object(ru.rbf, "followup", spy):
            summary, _plan = ru.apply_updates("https://x.supabase.co", "k", [today_item],
                                              collected_at="2026-11-18T21:00:00Z", get=get,
                                              apply_fn=apply_fn, changed_by="t",
                                              append_fn=lambda b, k, f: (len(f), 0, False, []))
        self.assertEqual(summary["applied"], 1)
        self.assertEqual(_tier(applied[0]["record"]["row_json"]), "Tier 3")
        self.assertEqual(summary["reclassified"], 1)
        self.assertEqual(called, [1], "R4 가 본문을 바꿨는데 후속이 불리지 않았다")
        self.assertGreater(summary["findings_inserted"], 0)


class WriterGuardTest(unittest.TestCase):
    """원문을 바꾸는 모듈(raw_versioning 경유)이 후속 대상 소스의 본문 칸을 다루면 raw_body_followup 을 import 한다.

    예외는 이유와 함께 여기 적는다 — 새 수리 도구가 본문을 바꾸고도 후속을 잊으면 이 시험이 막는다."""

    # 본문 칸을 읽기만 하고 바꾸지 않는 도구(그 도구의 docstring 에 근거).
    EXEMPT = {
        "backfill_wl_violations_reextract.py": "wl_violations·wl_violations_status 만 바꾼다(본문 불변)",
        "raw_versioning.py": "쓰기 함수 자체",
        "raw_body_followup.py": "후속 함수 자체",
    }

    def test_body_writers_import_followup(self):
        body_fields = {f for fields in rbf.BODY_FIELDS.values() for f in fields}
        offenders = []
        for path in sorted(ROOT.glob("*.py")):
            if path.name in self.EXEMPT:
                continue
            text = path.read_text(encoding="utf-8")
            writes = any(tok in text for tok in ("rv.apply_record", "rv.replace_raw", "rv.apply_version",
                                                 "raw_versioning.apply", "raw_versioning.replace_raw"))
            if not writes or not any(f in text for f in body_fields):
                continue
            tree = ast.parse(text)
            imported = {a.name for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))
                        for a in n.names} | {n.module for n in ast.walk(tree)
                                             if isinstance(n, ast.ImportFrom) and n.module}
            if "raw_body_followup" not in imported:
                offenders.append(path.name)
        self.assertEqual(offenders, [], "본문을 바꾸는 모듈은 raw_body_followup 을 불러야 한다")

    def test_guard_catches_a_writer_that_forgets(self):
        """가드 자체 뮤테이션 검사 — 후속 import 를 지운 사본은 걸린다."""
        text = (ROOT / "backfill_wl_body_recovery.py").read_text(encoding="utf-8")
        mutated = text.replace("import raw_body_followup as rbf\n", "")
        self.assertNotEqual(text, mutated)
        tree = ast.parse(mutated)
        imported = {a.name for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))
                    for a in n.names}
        self.assertNotIn("raw_body_followup", imported)


if __name__ == "__main__":
    unittest.main()
