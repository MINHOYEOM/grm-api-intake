"""Tests for Git delta validation, adoption, and publication."""
from __future__ import annotations

import io
import json
import os
import pathlib
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import delta_bridge as db  # noqa: E402
import grm_handoff as gh  # noqa: E402


def _valid_delta(publish_date: str | None = None) -> dict:
    d = {
        "cards": {"mfds-1": {"title_issue": "제목", "summary": "요약",
                              "key_facts": ["사실1"], "implication": "시사점",
                              "checks": ["점검1", "점검2"]}},
        "tldr": ["가", "나", "다"],
    }
    if publish_date:
        d["publish_date"] = publish_date
    return d


def _handoff_payload(bodies: dict[str, str], *, run_date: str = "2026-07-13") -> dict:
    """handoff v2 payload(실 producer 스키마의 최소 형태) — deep 대상 row + 비대상 row 혼재."""
    rows: list[dict] = [{"web_card_id": "plain-1", "section": "news"}]  # deep 비대상
    for card_id, body in bodies.items():
        rows.append({
            "web_card_id": card_id,
            "card_id": f"MFDS::{card_id}",
            "deep_analysis_ready": True,
            "deep_analysis_input": {"body_full": body},
            "kind": "admin-action",
        })
    return {"schema_version": gh.HANDOFF_SCHEMA_VERSION_V2,
            "handoff_id": gh.handoff_id_for(run_date),
            "run_date_kst": run_date, "row_count": len(rows), "rows": rows}


# 2026-08-17 실측 드리프트의 최소 재현: Routine 이 예치한 source_text 에서 **말미 블록이 통째로
# 빠졌고**, 하필 그 안에 심층분석이 인용한 조항이 있었다(실측 6/6 카드가 51~241자씩 누락).
_BODY_FULL_ADMIN = "기준서 미준수. 적용법령: 약사법 제38조제1항. [별표8] 개별기준."
_DRIFTED_SOURCE = "기준서 미준수."
_GROUNDED_DA = {
    "key_violations": [{
        "citation": "약사법 제38조제1항",
        "original": "기준서 미준수",
        "description": "제조·품질관리기준서를 준수하지 않았다는 지적이 확인됨.",
        "risk": "제품 품질 일관성 저하 위험이 있다.",
    }],
    "disposition_basis": "기준서 미준수를 사유로 제조업무정지 1개월이 부과됐다([별표8] 근거).",
    "required_remediation": {"deadline": "제조업무정지 기간 이행",
                              "items": ["기준서와 실제 작업기록 일치 여부 점검"]},
    "administrative_risks": "재위반 시 가중처분으로 이어질 수 있는 리스크가 있다.",
}


_DEEP_PAYLOAD = {"mfds-1": {"deep_analysis": {"a": 1}, "source_text": "원문"}}


class DeepKeyNamespaceTest(unittest.TestCase):
    def test_prefixed_deep_keys_normalized(self) -> None:
        deep = {"MFDS::mfds-1": {"deep_analysis": {}}}
        self.assertEqual(db.normalize_deep_key_namespace(deep), 1)
        self.assertIn("mfds-1", deep)

    def test_collision_aborts_normalization(self) -> None:
        deep = {"MFDS::x": {}, "x": {}}
        self.assertEqual(db.normalize_deep_key_namespace(deep), 0)
        self.assertIn("MFDS::x", deep)  # 무손실 아니면 손대지 않는다


class WriteDeltaTest(unittest.TestCase):
    def setUp(self) -> None:
        self._cwd = os.getcwd()
        self._tmp = tempfile.TemporaryDirectory()
        os.chdir(self._tmp.name)

    def tearDown(self) -> None:
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def test_writes_new_delta_file(self) -> None:
        delta = _valid_delta()
        wrote = db.write_delta(delta, None, "2026-07-13")
        self.assertTrue(wrote)
        path = pathlib.Path("web/data/deltas/delta_2026_07_13.json")
        self.assertTrue(path.exists())
        raw = path.read_bytes()
        self.assertTrue(raw.endswith(b"\n"))
        self.assertNotIn(b"\r", raw)
        self.assertEqual(json.loads(raw.decode("utf-8")), delta)

    def test_writes_deep_delta_when_present(self) -> None:
        delta = _valid_delta()
        deep = {"mfds-1": {"deep_analysis": {"a": "b"}, "source_text": "원문"}}
        wrote = db.write_delta(delta, deep, "2026-07-13")
        self.assertTrue(wrote)
        self.assertTrue(pathlib.Path("web/data/deltas/delta_2026_07_13.json").exists())
        self.assertTrue(pathlib.Path("web/data/deltas/deep_2026_07_13.json").exists())

    def test_idempotent_same_content_noop(self) -> None:
        delta = _valid_delta()
        first = db.write_delta(delta, None, "2026-07-13")
        second = db.write_delta(delta, None, "2026-07-13")
        self.assertTrue(first)
        self.assertFalse(second)

    def test_duplicate_conflicting_content_raises(self) -> None:
        delta = _valid_delta()
        db.write_delta(delta, None, "2026-07-13")
        other = _valid_delta()
        other["tldr"] = ["다른", "내용", "입니다"]
        with self.assertRaises(db.DeltaBridgeError):
            db.write_delta(other, None, "2026-07-13")

    def test_serialization_matches_fixture_style(self) -> None:
        """indent=1·ensure_ascii=False·후행개행 — tests/fixtures/delta_2026_07_06.json 관례."""
        delta = _valid_delta()
        db.write_delta(delta, None, "2026-07-13")
        raw = pathlib.Path("web/data/deltas/delta_2026_07_13.json").read_text(encoding="utf-8")
        expected = json.dumps(delta, ensure_ascii=False, indent=1) + "\n"
        self.assertEqual(raw, expected)


class MainIntegrationTest(unittest.TestCase):
    """main() end-to-end — GITHUB_OUTPUT 방출 + no-OPEN 클린 skip 포함."""

    def setUp(self) -> None:
        cwd = os.getcwd()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        os.chdir(tmp.name)
        self.addCleanup(os.chdir, cwd)
        self._tmp = tmp
        self._out = os.path.join(tmp.name, "gh_output.txt")
        env = mock.patch.dict(os.environ, {"GITHUB_OUTPUT": self._out, "GITHUB_STEP_SUMMARY": ""})
        env.start()
        self.addCleanup(env.stop)
        self._git_patch = mock.patch.object(db, "git_read_run_file", return_value=None)
        self._git_patch.start()
        self.addCleanup(lambda: self._git_patch.stop())
        mirror = mock.patch.object(db, "git_read_handoff", return_value=None)
        mirror.start()
        self.addCleanup(mirror.stop)

    def _read_outputs(self) -> dict:
        if not os.path.isfile(self._out):
            return {}
        return dict(line.split("=", 1) for line in
                    pathlib.Path(self._out).read_text(encoding="utf-8").splitlines() if "=" in line)

class NormalizeCardKeyNamespaceTest(unittest.TestCase):
    """[2026-07-27] `Source::document_id` 키 회귀 자동 정규화.

    2026-07-13·07-27 두 번, Routine 이 handoff 의 `card_id`(=`source::document_id`)를 델타
    키로 예치해 발행이 전건 거부됐다(07-27 = 103장). 두 번 다 사람이 **완전히 동일한**
    순수 rename 패치를 손으로 만들어 수습했다. `::` 는 정상 카드 id 에 없으므로(전 발행본+
    스캐폴드 371개 중 0개) 접두사 유무만으로 회귀를 확정할 수 있고 변환은 무손실이다.
    """

    def _delta(self, cards):
        return {"publish_date": "2026-07-27", "tldr": ["a", "b", "c"], "cards": cards}

    def test_prefixed_keys_are_normalized(self):
        d = self._delta({"FDA 483::fda483-193813": {"summary": "s"},
                         "MFDS::admin-2026002715": {"summary": "t"}})
        n = db.normalize_card_key_namespace(d)
        self.assertEqual(n, 2)
        self.assertEqual(set(d["cards"]), {"fda483-193813", "admin-2026002715"})

    def test_clean_delta_untouched(self):
        d = self._delta({"fda483-193813": {"summary": "s"}})
        before = dict(d["cards"])
        self.assertEqual(db.normalize_card_key_namespace(d), 0)
        self.assertEqual(d["cards"], before)

    def test_slash_and_space_ids_are_not_touched(self):
        """MHRA NCR id 처럼 공백·슬래시가 있어도 `::` 가 없으면 정상 id 다."""
        d = self._delta({"Insp GMP 52165/19076958-0001": {"summary": "s"}})
        self.assertEqual(db.normalize_card_key_namespace(d), 0)
        self.assertIn("Insp GMP 52165/19076958-0001", d["cards"])

    def test_deep_delta_keys_follow(self):
        """deep 델타를 같이 안 고치면 심층분석이 조용히 유실된다."""
        d = self._delta({"FDA 483::fda483-193813": {"summary": "s"}})
        deep = {"FDA 483::fda483-193813": {"deep_analysis": {}, "source_text": "x"}}
        db.normalize_card_key_namespace(d, deep)
        self.assertEqual(set(deep), {"fda483-193813"})

    def test_collision_aborts_normalization(self):
        """접두사 제거 시 충돌하면 **아무것도 고치지 않는다**(무손실 보장 안 되면 포기)."""
        d = self._delta({"FDA 483::x1": {"summary": "a"}, "MFDS::x1": {"summary": "b"}})
        self.assertEqual(db.normalize_card_key_namespace(d), 0)
        self.assertEqual(set(d["cards"]), {"FDA 483::x1", "MFDS::x1"})

    def test_collision_with_existing_bare_key_aborts(self):
        d = self._delta({"FDA 483::x1": {"summary": "a"}, "x1": {"summary": "b"}})
        self.assertEqual(db.normalize_card_key_namespace(d), 0)


class HandoffWebCardIdTest(unittest.TestCase):
    """[2026-07-27] handoff row 가 모호하지 않은 `web_card_id`(=bare document_id)를 노출한다.

    `card_id` 는 `source::document_id` 라 델타 키로 쓰면 발행이 거부된다. 이름만으로
    구분되게 같은 값을 별도 필드로 한 번 더 낸다(프롬프트가 이 필드명을 그대로 지시).
    """

    def test_web_card_id_is_bare_document_id(self):
        import collect_intake as ci
        from datetime import date, datetime
        rows = [{
            "source": "FDA Warning Letter", "document_id": "WL-CMS-660124",
            "date": "2026-05-20", "type_or_class": "CDER", "firm": "Acme",
            "headline": "CGMP", "page_id": "p1", "signal_tier": "Tier 3",
            "modality": "Chemical", "language": "", "raw_fetch_ok": True,
            "raw": {"firm": "Acme", "subject": "CGMP"},
        }]
        payload = ci.build_routine_handoff_payload_v2(
            rows, date(2026, 6, 5), 7, datetime(2026, 6, 5, 3, 17))
        row = payload["rows"][0]
        self.assertEqual(row["web_card_id"], "WL-CMS-660124")
        # 두 필드가 나란히 있고 **서로 다르다** — 이름으로 구분해야 한다는 사실 자체를 고정.
        self.assertEqual(row["card_id"], "FDA Warning Letter::WL-CMS-660124")
        self.assertNotEqual(row["web_card_id"], row["card_id"])


# ── [2026-08-17] deep `source_text` 정본 = handoff `deep_analysis_input.body_full` ──────────
#
# 종전 계약은 클라우드 Routine 이 카드 원문(483 은 12~14k자)을 델타에 **옮겨 적게** 했다.
# 그 문자열은 장식이 아니라 근거 대조의 기준선이라(D2/D4/D5b + 조립 시점 결정론 재추출),
# 옮겨 적다 흘린 만큼이 그대로 "원문에 없음"이 된다. 2026-08-17 실측에서 예치된 6건이
# **6건 전부** body_full 과 달랐다. 무인 실행에는 대조할 사람이 없으므로 전사 경로를 없앤다.


class ExtractHandoffBodyFullTest(unittest.TestCase):
    """payload → {web_card_id: body_full} 순수 추출."""

    def test_deep_rows_extracted_by_web_card_id(self) -> None:
        bodies = db.extract_handoff_body_full(_handoff_payload({"admin-1": "원문A", "x-2": "원문B"}))
        self.assertEqual(bodies, {"admin-1": "원문A", "x-2": "원문B"})

    def test_non_deep_rows_ignored(self) -> None:
        """deep 비대상 row(`plain-1`)는 body_full 이 없다 — 섞여 들어오면 안 된다."""
        self.assertNotIn("plain-1", db.extract_handoff_body_full(_handoff_payload({"a": "b"})))

    def test_v1_payload_yields_empty(self) -> None:
        """v1 payload·심층분석 없는 주는 그 키가 아예 없다 — 버전 문자열이 아니라 **키**로 판정."""
        v1 = {"schema_version": gh.HANDOFF_SCHEMA_VERSION, "rows": [{"document_id": "a"}]}
        self.assertEqual(db.extract_handoff_body_full(v1), {})
        self.assertEqual(db.extract_handoff_body_full({}), {})

    def test_blank_body_is_not_harvested(self) -> None:
        """빈 body_full 을 주우면 예치된 진짜 원문을 공백으로 덮어 카드를 죽인다."""
        p = _handoff_payload({"a": "   "})
        self.assertEqual(db.extract_handoff_body_full(p), {})

    def test_card_id_prefixed_key_is_not_used(self) -> None:
        """`card_id`(source::document_id)를 주우면 델타 키 공간과 어긋나 전건 미스가 된다."""
        bodies = db.extract_handoff_body_full(_handoff_payload({"admin-1": "원문"}))
        self.assertEqual(list(bodies), ["admin-1"])


class ApplyHandoffSourceTextTest(unittest.TestCase):
    """정본화 규약 — 채움/교체/동일/미보유, 그리고 **하지 않는 것들**."""

    def test_drifted_source_text_is_replaced(self) -> None:
        deep = {"admin-1": {"deep_analysis": {}, "source_text": _DRIFTED_SOURCE}}
        stats = db.apply_handoff_source_text(deep, {"admin-1": _BODY_FULL_ADMIN})
        self.assertEqual(deep["admin-1"]["source_text"], _BODY_FULL_ADMIN)
        self.assertEqual(stats["replaced"], 1)

    def test_missing_source_text_is_filled(self) -> None:
        """프롬프트가 예치를 그만두면 이 경로가 정상 경로가 된다."""
        deep = {"admin-1": {"deep_analysis": {}}}
        stats = db.apply_handoff_source_text(deep, {"admin-1": _BODY_FULL_ADMIN})
        self.assertEqual(deep["admin-1"]["source_text"], _BODY_FULL_ADMIN)
        self.assertEqual(stats["filled"], 1)

    def test_identical_is_noop(self) -> None:
        deep = {"admin-1": {"source_text": _BODY_FULL_ADMIN}}
        stats = db.apply_handoff_source_text(deep, {"admin-1": _BODY_FULL_ADMIN})
        self.assertEqual((stats["identical"], stats["replaced"], stats["filled"]), (1, 0, 0))

    def test_card_absent_from_handoff_keeps_deposited_text(self) -> None:
        """소급 복구 경로(`fda483_ocr_backfill` OCR 판독본)는 handoff 에 짝이 없다 — 지우면 안 된다."""
        deep = {"fda483-1": {"source_text": "OCR 판독본", "source_text_status": "pdf-ok-ocr"}}
        stats = db.apply_handoff_source_text(deep, {"admin-9": _BODY_FULL_ADMIN})
        self.assertEqual(deep["fda483-1"]["source_text"], "OCR 판독본")
        self.assertEqual(stats["absent"], 1)

    def test_does_not_create_cards(self) -> None:
        """분석 없는 카드에 원문만 실으면 조립이 '원문은 있는데 분석이 없는 카드'를 새로 본다."""
        deep = {"admin-1": {"deep_analysis": {}}}
        db.apply_handoff_source_text(deep, {"admin-1": "a", "admin-2": "b", "admin-3": "c"})
        self.assertEqual(set(deep), {"admin-1"})

    def test_other_keys_untouched(self) -> None:
        deep = {"fda483-1": {"source_text": "옛것", "source_text_status": "pdf-ok",
                             "observations_ko": [{"number": "1"}], "deep_analysis": {"a": 1}}}
        db.apply_handoff_source_text(deep, {"fda483-1": _BODY_FULL_ADMIN})
        self.assertEqual(deep["fda483-1"]["source_text_status"], "pdf-ok")
        self.assertEqual(deep["fda483-1"]["observations_ko"], [{"number": "1"}])
        self.assertEqual(deep["fda483-1"]["deep_analysis"], {"a": 1})


class DeepKeyNamespaceWiringTest(unittest.TestCase):
    def test_clean_delta_with_prefixed_deep_keys_is_normalized(self):
        delta = _valid_delta()
        deep = {"MFDS::mfds-1": {"deep_analysis": {}, "source_text": "text"}}
        _, normalized, _ = db.finalize_delta(delta, deep, "2026-07-13")
        self.assertEqual(list(normalized), ["mfds-1"])


_COPYEDIT_OK = {"status": "완료", "published": True,
                "code_gate": {"ran": True, "exit": 0, "message": ""},
                "cards": [{"web_card_id": "mfds-1", "verdict": "pass", "fails": []}],
                "counts": {"cards": 1, "pass": 1, "reject": 0, "fails_total": 0}}
# 11-09 실제 copyedit.json 의 모양(보류 · exit 2).
_COPYEDIT_HELD = {"status": "보류", "published": False,
                  "code_gate": {"ran": True, "exit": 2, "message": "조립 거부"},
                  "cards": [], "counts": {"cards": 63, "pass": 0, "reject": 0, "fails_total": 0}}


def _git_delta(summary: str = "요약") -> dict:
    d = _valid_delta()
    d["cards"]["mfds-1"]["summary"] = summary
    return d


def _git_reader(files: dict, date_str: str = "2026-07-13"):
    """`git_read_run_file(date, name)` 대역 — 그 날짜 브랜치에 있는 파일만 돌려준다."""
    def read(d, name):
        if d != date_str or name not in files:
            return None
        v = files[name]
        return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, separators=(",", ":"))
    return read


class HandoffBodySourceTest(unittest.TestCase):
    """심층분석 원문 정본을 Notion handoff 대신 DB handoff 의 git 사본에서 읽는 길(노션 은퇴 준비)."""

    DATE = "2026-07-13"

    def setUp(self) -> None:
        self.payload = {"run_date_kst": self.DATE, "generated_at_kst": "t", "rows": [
            {"web_card_id": "fda483-1", "deep_analysis_input": {"body_full": "GIT BODY"}}]}
        self.text = json.dumps(self.payload, ensure_ascii=False)
        import grm_queue
        self.sha = grm_queue.payload_sha256(self.payload)
    def _run(self, *, git_text, handoff_ref=None):
        deep = {"fda483-1": {"deep_analysis": {}, "source_text": "DEPOSITED"}}
        with mock.patch.object(db, "git_read_handoff", return_value=git_text):
            db._authoritative_source_text(self.DATE, deep, handoff_ref=handoff_ref)
        return deep["fda483-1"]["source_text"]

    def test_reads_the_git_mirror_without_a_ref(self) -> None:
        self.assertEqual(self._run(git_text=self.text), "GIT BODY")

    def test_git_handoff_issue_reads_git_even_without_the_variable(self) -> None:
        ref = {"date": self.DATE, "sha256": self.sha, "rows": 1}
        self.assertEqual(self._run(git_text=self.text, handoff_ref=ref), "GIT BODY")

    def test_git_handoff_issue_never_substitutes_the_notion_handoff(self) -> None:
        ref = {"date": self.DATE, "sha256": "0" * 64, "rows": 1}
        for git_text in (self.text, None, "not json",
                         json.dumps(dict(self.payload, run_date_kst="2026-07-06"))):
            with self.subTest(git=str(git_text)[:20]):
                got = self._run(git_text=git_text, handoff_ref=ref)
                self.assertEqual(got, "DEPOSITED")       # 반납된 원문 그대로

    def test_a_crash_in_the_git_path_does_not_block_the_bridge(self) -> None:
        deep = {"fda483-1": {"deep_analysis": {}, "source_text": "DEPOSITED"}}
        with mock.patch.object(db, "git_read_handoff", side_effect=RuntimeError("unavailable")):
            db._authoritative_source_text(self.DATE, deep)
        self.assertEqual(deep["fda483-1"]["source_text"], "DEPOSITED")

    def test_mirror_branch_name_matches_the_mirror_workflow(self) -> None:
        root = pathlib.Path(db.__file__).resolve().parent
        wf = (root / ".github" / "workflows" / "grm-queue-handoff-mirror.yml").read_text(encoding="utf-8")
        self.assertIn(f"MIRROR_BRANCH: {db.HANDOFF_MIRROR_BRANCH}", wf)
        self.assertIn('cp "$f" "handoff_${d}.json"', wf)

    def test_prepare_git_run_passes_the_ref(self) -> None:
        seen = {}

        def fake(_date, _deep, handoff_ref=None):
            seen["ref"] = handoff_ref
        ref = {"date": self.DATE, "sha256": self.sha, "rows": 1}
        run = {"date": self.DATE, "branch": "b",
               "delta": dict(_git_delta(), handoff_ref=ref),
               "deep": {"mfds-1": {"deep_analysis": {"x": 1}, "source_text": "s"}}}
        with mock.patch.object(db, "_authoritative_source_text", side_effect=fake), \
                mock.patch.object(db, "_gate_deep_analysis", side_effect=lambda d: d), \
                mock.patch.object(db, "log"):
            db.prepare_git_run(run)
        self.assertEqual(seen["ref"], ref)


class LoadGitRunTest(unittest.TestCase):
    def test_adoptable_when_copyedit_passed(self) -> None:
        run = db.load_git_run("2026-07-13", read=_git_reader(
            {"delta.json": _git_delta(), "copyedit.json": _COPYEDIT_OK}))
        self.assertTrue(run["found"])
        self.assertTrue(run["adoptable"], run["reason"])
        self.assertEqual(run["branch"], "claude/company-2026-07-13")

    def test_pass_with_notes_still_adoptable(self) -> None:
        """[11-21] 교열 '주의'(pass + notes · counts.notes_total)는 반려가 아니다 — 채택을 막지 않는다."""
        ce = json.loads(json.dumps(_COPYEDIT_OK))
        ce["cards"][0]["notes"] = [{"item": "같은 이름", "slot": "deep.key_violations", "evidence": "한 줄"}]
        ce["counts"]["notes_total"] = 1
        run = db.load_git_run("2026-07-13", read=_git_reader(
            {"delta.json": _git_delta(), "copyedit.json": ce}))
        self.assertTrue(run["adoptable"], run["reason"])

    def test_held_copyedit_not_adoptable_but_delta_kept_for_compare(self) -> None:
        run = db.load_git_run("2026-07-13", read=_git_reader(
            {"delta.json": _git_delta(), "copyedit.json": _COPYEDIT_HELD}))
        self.assertFalse(run["adoptable"])
        self.assertIn("보류", run["reason"])
        self.assertIn("exit=2", run["reason"])
        self.assertIsNotNone(run["delta"])  # 비교에는 쓴다

    def test_missing_copyedit_not_adoptable(self) -> None:
        run = db.load_git_run("2026-07-13", read=_git_reader({"delta.json": _git_delta()}))
        self.assertFalse(run["adoptable"])
        self.assertIn("copyedit.json 없음", run["reason"])

    def test_reject_not_adoptable(self) -> None:
        ce = json.loads(json.dumps(_COPYEDIT_OK))
        ce["cards"][0]["verdict"] = "reject"
        run = db.load_git_run("2026-07-13", read=_git_reader(
            {"delta.json": _git_delta(), "copyedit.json": ce}))
        self.assertFalse(run["adoptable"])
        self.assertIn("reject", run["reason"])

    def test_missing_delta_not_adoptable(self) -> None:
        run = db.load_git_run("2026-07-13", read=_git_reader({"copyedit.json": _COPYEDIT_OK}))
        self.assertFalse(run["adoptable"])
        self.assertIn("delta.json 없음", run["reason"])

    def test_broken_deep_not_adoptable(self) -> None:
        run = db.load_git_run("2026-07-13", read=_git_reader(
            {"delta.json": _git_delta(), "deep_delta.json": "{broken",
             "copyedit.json": _COPYEDIT_OK}))
        self.assertFalse(run["adoptable"])

    def test_no_branch(self) -> None:
        run = db.load_git_run("2026-07-13", read=_git_reader({}))
        self.assertFalse(run["found"])
        self.assertFalse(run["adoptable"])


class CompareDeltasTest(unittest.TestCase):
    def test_identical(self) -> None:
        r = db.compare_deltas(_valid_delta(), None, _valid_delta(), None)
        self.assertTrue(r["identical"])
        self.assertEqual(r["cards"]["changed"], [])

    def test_whitespace_loss_is_flagged(self) -> None:
        """노션 왕복의 알려진 결함 — 코드 블록 경계에서 공백이 깎인다(`in the`→`inthe`)."""
        r = db.compare_deltas(_git_delta("found inthe room"), None,
                              _git_delta("found in the room"), None)
        self.assertFalse(r["identical"])
        self.assertEqual(r["cards"]["changed"],
                         [{"id": "mfds-1", "slots": ["summary"], "whitespace_only": True}])

    def test_card_sets_differ(self) -> None:
        g = _valid_delta()
        g["cards"]["mfds-2"] = g["cards"].pop("mfds-1")
        r = db.compare_deltas(_valid_delta(), None, g, None)
        self.assertEqual(r["cards"]["only_notion"], ["mfds-1"])
        self.assertEqual(r["cards"]["only_git"], ["mfds-2"])
        md = db.render_compare_md(r, {"date": "2026-07-13", "source": "notion"})
        self.assertIn("mfds-2", md)


class GitReturnMainTest(MainIntegrationTest):
    """main() — 두 길 모드. 부모의 setUp(임시 cwd·환경·git 대역)과 부모 시험을 그대로 다시 돈다."""

    _DELTA = "web/data/deltas/delta_2026_07_13.json"

    def setUp(self) -> None:
        super().setUp()
        self._summary = os.path.join(self._tmp.name, "summary.md")
        os.environ["GITHUB_STEP_SUMMARY"] = self._summary

    def _git(self, files):
        self._git_patch.stop()
        self._git_patch = mock.patch.object(db, "git_read_run_file", side_effect=_git_reader(files))
        self._git_patch.start()

    def _written(self) -> dict:
        return json.loads(pathlib.Path(self._DELTA).read_text(encoding="utf-8"))

    # ── 스캐폴드 동결 (3단계 — git handoff 로 만든 호) ─────────────────────────
    _SCAFFOLD = "web/data/scaffolds/brief_web_2026_07_13.json"
    _REF = {"date": "2026-07-13", "sha256": "a" * 64, "rows": 1}

    @staticmethod
    def _scaffold_text(ids=("mfds-1",), run_date="2026-07-13") -> str:
        # 일부러 들여쓰기·공백을 넣는다 — 다시 직렬화하지 않고 원문 그대로 동결하는지 본다.
        return json.dumps({"schema_version": "grm-web-card/v1", "brief": {"run_date_kst": run_date},
                           "cards": [{"id": i} for i in ids]}, ensure_ascii=False, indent=1) + "\n"

    def _run_git(self, files, rc_expected=0):
        self._git(files)
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            rc = db.main(["--publish-date", "2026-07-13"])
        self.assertEqual(rc, rc_expected)
        return self._read_outputs()

    def test_git_handoff_issue_freezes_the_checked_scaffold_verbatim(self) -> None:
        g = dict(_git_delta("git"), handoff_ref=self._REF, deferred=[])
        text = self._scaffold_text(ids=("mfds-1", "other"))
        out = self._run_git({"delta.json": g, "copyedit.json": _COPYEDIT_OK, "scaffold.json": text})
        self.assertEqual((out.get("wrote"), out.get("source")), ("true", "git"))
        self.assertEqual(pathlib.Path(self._SCAFFOLD).read_bytes(), text.encode("utf-8"))
        self.assertEqual(self._written()["handoff_ref"], self._REF)

    def test_git_handoff_issue_without_scaffold_is_not_published_from_git(self) -> None:
        g = dict(_git_delta("git"), handoff_ref=self._REF)
        # 교열은 완료인데 git 으로도 노션으로도 못 썼다 → 조용한 skip 대신 실패(11-23 사전 점검 B)
        out = self._run_git({"delta.json": g, "copyedit.json": _COPYEDIT_OK}, rc_expected=1)
        self.assertNotIn("wrote", out)
        self.assertFalse(pathlib.Path(self._DELTA).exists())
        self.assertFalse(pathlib.Path(self._SCAFFOLD).exists())

    def test_scaffold_must_be_that_date_and_hold_every_delta_card(self) -> None:
        g = dict(_git_delta("git"), handoff_ref=self._REF)
        for bad in (self._scaffold_text(ids=("other",)),
                    self._scaffold_text(run_date="2026-07-06"),
                    "{\"cards\": \"x\"}", "not json"):
            with self.subTest(bad=bad[:30]):
                out = self._run_git({"delta.json": g, "copyedit.json": _COPYEDIT_OK,
                                     "scaffold.json": bad}, rc_expected=1)
                self.assertNotIn("wrote", out)
                self.assertFalse(pathlib.Path(self._DELTA).exists())
                self.assertFalse(pathlib.Path(self._SCAFFOLD).exists())

    def test_notion_handoff_issue_ignores_a_scaffold_file(self) -> None:
        out = self._run_git({"delta.json": _git_delta("git"), "copyedit.json": _COPYEDIT_OK,
                             "scaffold.json": self._scaffold_text()})
        self.assertEqual(out.get("wrote"), "true")
        self.assertFalse(pathlib.Path(self._SCAFFOLD).exists())   # handoff_ref 없는 호는 종전 그대로

    def test_frozen_scaffold_is_never_overwritten(self) -> None:
        os.makedirs(os.path.dirname(self._SCAFFOLD))
        pathlib.Path(self._SCAFFOLD).write_bytes(b"first")
        g = dict(_git_delta("git"), handoff_ref=self._REF)
        self._run_git({"delta.json": g, "copyedit.json": _COPYEDIT_OK,
                       "scaffold.json": self._scaffold_text()})
        self.assertEqual(pathlib.Path(self._SCAFFOLD).read_bytes(), b"first")

    def test_workflows_carry_the_frozen_scaffold(self) -> None:
        root = pathlib.Path(db.__file__).resolve().parent
        bridge = (root / ".github" / "workflows" / "grm-delta-bridge.yml").read_text(encoding="utf-8")
        publish = (root / ".github" / "workflows" / "grm-web-publish.yml").read_text(encoding="utf-8")
        self.assertEqual(db._scaffold_path("2026-07-13").replace(os.sep, "/"), self._SCAFFOLD)
        self.assertIn('git add "web/data/scaffolds/brief_web_${U}.json"', bridge)
        self.assertIn('FROZEN="web/data/scaffolds/brief_web_${{ steps.r.outputs.under }}.json"', publish)
        # 동결본이 있으면 아티팩트를 받지 않고, 사람이 run id 를 주면 그쪽이 우선한다.
        self.assertLess(publish.index('if [ -z "$RID" ] && [ -f "$FROZEN" ]; then'),
                        publish.index('elif [ -z "$RID" ]; then'))
        self.assertLess(publish.index('elif [ -z "$RID" ]; then'), publish.index("gh run list -R"))

    def test_git_mode_first_writer_wins_without_red_run(self) -> None:
        """노션 길이 먼저 기록한 뒤 git 이 다른 내용으로 오면 — 오류 대신 유지 + 차이표."""
        os.makedirs("web/data/deltas")
        pathlib.Path(self._DELTA).write_bytes(db._dumps(_git_delta("inthe room")).encode("utf-8"))
        self._git({"delta.json": _git_delta("in the room"), "copyedit.json": _COPYEDIT_OK})
        rc = db.main(["--publish-date", "2026-07-13"])
        self.assertEqual(rc, 0)
        self.assertEqual(self._read_outputs().get("wrote"), "false")
        self.assertEqual(self._written()["cards"]["mfds-1"]["summary"], "inthe room")
        self.assertTrue(pathlib.Path("_delta_compare/compare_2026_07_13.json").exists())

    def test_compare_only_writes_nothing(self) -> None:
        os.makedirs("web/data/deltas")
        pathlib.Path(self._DELTA).write_bytes(db._dumps(_valid_delta()).encode("utf-8"))
        before = pathlib.Path(self._DELTA).read_bytes()
        self._git({"delta.json": _valid_delta(), "copyedit.json": _COPYEDIT_OK})
        rc = db.main(["--compare-only", "--publish-date", "2026-07-13"])
        self.assertEqual(rc, 0)
        self.assertEqual(self._read_outputs().get("wrote"), "false")
        self.assertEqual(pathlib.Path(self._DELTA).read_bytes(), before)
        rep = json.loads(pathlib.Path("_delta_compare/compare_2026_07_13.json")
                         .read_text(encoding="utf-8"))
        self.assertTrue(rep["report"]["identical"])

    # ── 11-23 사전 점검 B: 교열 완료인데 아무 길로도 못 쓰면 실패 ───────────────────
    def test_copyedit_done_with_bad_gate_also_fails(self) -> None:
        bad = dict(_COPYEDIT_OK, code_gate={"ran": True, "exit": 1})
        out = self._run_git({"delta.json": _git_delta("git"), "copyedit.json": bad}, rc_expected=1)
        self.assertEqual(out.get("wrote"), "false")
        self.assertIn("채택 안 함", pathlib.Path(self._summary).read_text(encoding="utf-8"))

    def test_copyedit_held_keeps_the_clean_skip(self) -> None:
        out = self._run_git({"delta.json": _git_delta("git"), "copyedit.json": _COPYEDIT_HELD})
        self.assertEqual(out.get("wrote"), "false")

    def test_no_git_return_keeps_the_clean_skip(self) -> None:
        out = self._run_git({})
        self.assertEqual(out.get("wrote"), "false")

    def test_compare_only_keeps_old_behaviour_even_if_git_unrecordable(self) -> None:
        g = _git_delta("git")
        g["publish_date"] = "2026-07-06"
        self._git({"delta.json": g, "copyedit.json": _COPYEDIT_OK})
        with redirect_stdout(io.StringIO()):
            rc = db.main(["--compare-only",
                          "--publish-date", "2026-07-13"])
        self.assertEqual(rc, 0)

    def test_compare_only_requires_date(self) -> None:
        with redirect_stderr(io.StringIO()):
            rc = db.main(["--compare-only"])
        self.assertEqual(rc, 1)


class DeltaBridgeWorkflowGuardTest(unittest.TestCase):
    """월요일 발행 흐름 가드 — 워크플로 배선이 11-16 을 깨지 않는지 텍스트로 고정한다."""

    WF = ROOT / ".github" / "workflows" / "grm-delta-bridge.yml"

    def setUp(self) -> None:
        self.text = self.WF.read_text(encoding="utf-8")


    def test_compare_dir_is_never_committed(self) -> None:
        adds = [ln for ln in self.text.splitlines() if ln.strip().startswith("git add")]
        self.assertTrue(adds)
        for line in adds:
            self.assertNotIn(db.COMPARE_DIR, line)
            # 커밋 대상은 그 호의 델타와, git handoff 로 만든 호의 동결 스캐폴드 한 파일뿐이다.
            self.assertTrue("web/data/deltas/" in line
                            or 'web/data/scaffolds/brief_web_${U}.json' in line, line)


if __name__ == "__main__":
    unittest.main(verbosity=2)
