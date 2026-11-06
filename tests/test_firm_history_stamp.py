#!/usr/bin/env python3
"""firm_history_stamp.py 단위 시험 — 설계서 §7 사례를 케이스로 고정. 네트워크 0(RPC 는 모킹)."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import firm_history_stamp as fh  # noqa: E402


def card483(cid, name, fei, date="2026-10-30", **extra):
    c = {"id": cid, "card_type": fh.CT_483, "merged_count": 1, "merged_items": [],
         "facts": [{"label": "발행일", "value": date}, {"label": "문서번호", "value": cid},
                   {"label": "제조소/업체", "value": f"{name} · FEI {fei}" if fei else name}]}
    c.update(extra)
    return c


def cardmfds(cid, name, ctype="회수·판매중지", date="2026-10-30", **extra):
    c = {"id": cid, "card_type": ctype, "merged_count": 1, "merged_items": [],
         "facts": [{"label": "발행일", "value": date}, {"label": "문서번호", "value": cid},
                   {"label": "업체", "value": name}]}
    c.update(extra)
    return c


def fk(card):
    """테스트용 업체키 — 실제 정본(render)과는 별도로 이름 앞부분 소문자."""
    for f in card.get("facts", []):
        if f["label"] in ("제조소/업체", "업체"):
            v = f["value"].split(" · ")[0].strip()
            return "" if v in ("", "원문 미기재") else v.lower()
    return ""


class FeiExtractTest(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(fh.extract_fei("AGC Biologics S.p.A. · FEI 3020270660"), "3020270660")
        self.assertEqual(fh.extract_fei("X · FEI 1234567"), "1234567")

    def test_eleven_digits_rejected(self):  # Corixa GSK 사례
        self.assertIsNone(fh.extract_fei("Corixa GSK · FEI 30077266770"))

    def test_six_digits_and_missing(self):
        self.assertIsNone(fh.extract_fei("X · FEI 123456"))
        self.assertIsNone(fh.extract_fei("X"))
        self.assertIsNone(fh.extract_fei(""))


class CandidateTest(unittest.TestCase):
    def test_out_of_scope_types(self):
        c = cardmfds("w1", "Foo", ctype="Warning Letter")
        self.assertIsNone(fh.candidate_from_card(c, fk))
        self.assertIsNone(fh.candidate_from_card(cardmfds("g", "Foo", ctype="지침·안내서"), fk))

    def test_mfds_three_types_in_scope(self):
        for t in ("회수·판매중지", "행정처분", "GMP실사"):
            self.assertIsNotNone(fh.candidate_from_card(cardmfds("a", "현진제약", ctype=t), fk), t)

    def test_483_needs_valid_fei(self):
        self.assertIsNone(fh.candidate_from_card(card483("a", "Corixa GSK", "30077266770"), fk))
        self.assertIsNone(fh.candidate_from_card(card483("a", "NoFei Co", None), fk))
        self.assertEqual(fh.candidate_from_card(card483("a", "Ok Co", "3000209996"), fk)["fei"], "3000209996")

    def test_empty_or_placeholder_name_hidden(self):
        self.assertIsNone(fh.candidate_from_card(cardmfds("a", "원문 미기재"), fk))
        self.assertIsNone(fh.candidate_from_card(cardmfds("a", ""), fk))

    def test_multi_firm_merge_hidden(self):
        self.assertIsNone(fh.candidate_from_card(cardmfds("a", "A 외 3개사", merged_multi_firm=True), fk))

    def test_missing_or_bad_date_hidden(self):
        c = cardmfds("a", "Foo")
        c["facts"] = [f for f in c["facts"] if f["label"] != "발행일"]
        self.assertIsNone(fh.candidate_from_card(c, fk))
        self.assertIsNone(fh.candidate_from_card(cardmfds("a", "Foo", date="10/30/2026"), fk))

    def test_merged_483_digest_of_different_sites_hidden(self):  # 11-02 MSD 11건 디제스트
        c = card483("a", "MSD International GmbH", "3002808087", merged_count=2,
                    merged_items=["MSD · FEI 3002808087 · 실사 04/11/2025", "Biomay AG · FEI 3004536648 · 실사 07/27/2023"])
        self.assertIsNone(fh.candidate_from_card(c, fk))

    def test_merged_483_same_site_allowed(self):
        c = card483("a", "Same Co", "3002808087", merged_count=2,
                    merged_items=["Same Co · FEI 3002808087 · 실사 a", "Same Co · FEI 3002808087 · 실사 b"])
        self.assertIsNotNone(fh.candidate_from_card(c, fk))

    def test_merged_483_without_items_hidden(self):
        self.assertIsNone(fh.candidate_from_card(card483("a", "Same Co", "3002808087", merged_count=3), fk))

    def test_merged_recall_same_firm_allowed(self):
        c = cardmfds("a", "제이케이제약(주)", merged_count=2, merged_items=["p1", "p2"])
        self.assertIsNotNone(fh.candidate_from_card(c, fk))

    def test_duplicates_collapsed(self):
        brief = {"cards": [cardmfds("a", "Foo"), cardmfds("a", "Foo")]}
        self.assertEqual(len(fh.collect_candidates(brief, fk)), 1)


class JudgeTest(unittest.TestCase):
    def c483(self, fei="3020270660"):
        return fh.candidate_from_card(card483("fda483-195525", "AGC Biologics S.p.A.", fei), fk)

    def test_agc_italy_fei_zero_hidden_despite_name_group(self):
        row = {"id": "fda483-195525", "fei_n": 0, "fei_last": None, "name_n": 2, "name_last": "2026-10", "name_sites": 1}
        self.assertIsNone(fh.judge_card(self.c483(), row))

    def test_483_fei_match_is_site(self):
        row = {"fei_n": 2, "fei_last": "2026-07", "name_n": 9, "name_last": "2026-10"}
        self.assertEqual(fh.judge_card(self.c483("3000209996"), row), {"basis": "site", "n": 2, "last": "2026-07"})

    def test_mfds_name_basis(self):
        cand = fh.candidate_from_card(cardmfds("recall-1", "현진제약"), fk)
        row = {"fei_n": 0, "fei_last": None, "name_n": 40, "name_last": "2026-10"}
        self.assertEqual(fh.judge_card(cand, row), {"basis": "name", "n": 40, "last": "2026-10"})

    def test_zero_hidden(self):
        cand = fh.candidate_from_card(cardmfds("recall-1", "Foo"), fk)
        self.assertIsNone(fh.judge_card(cand, {"name_n": 0, "name_last": None}))

    def test_missing_row_or_bad_last_hidden(self):
        cand = fh.candidate_from_card(cardmfds("recall-1", "Foo"), fk)
        self.assertIsNone(fh.judge_card(cand, None))
        self.assertIsNone(fh.judge_card(cand, {"name_n": 3, "name_last": None}))
        self.assertIsNone(fh.judge_card(cand, {"name_n": 3, "name_last": "2026-7"}))
        self.assertIsNone(fh.judge_card(cand, {"name_n": True, "name_last": "2026-07"}))

    def test_483_never_uses_name_count(self):
        row = {"fei_n": 0, "fei_last": None, "name_n": 5, "name_last": "2026-10"}
        self.assertIsNone(fh.judge_card(self.c483(), row))


class FetchAndMainTest(unittest.TestCase):
    def brief(self, cards):
        return {"brief": {"publish_date": "2026-11-02"}, "cards": cards}

    def test_payload_shape_chunk_and_exclude(self):
        cands = [fh.candidate_from_card(cardmfds(f"r{i}", f"Firm{i}"), fk) for i in range(450)]
        calls = []

        def post(payload):
            calls.append(payload)
            return [{"id": c["id"], "name_n": 1, "name_last": "2026-01"} for c in payload["p_cards"]]

        rows = fh.fetch_rows(cands, ["x", "y"], post)
        self.assertEqual([len(c["p_cards"]) for c in calls], [200, 200, 50])
        self.assertEqual(len(rows), 450)
        self.assertEqual(calls[0]["p_exclude"], ["x", "y"])
        self.assertNotIn("fei", calls[0]["p_cards"][0])  # 식약처 카드는 FEI 를 보내지 않는다

    def test_483_sends_fei(self):
        cand = fh.candidate_from_card(card483("a", "Foo", "3000209996"), fk)
        seen = []
        fh.fetch_rows([cand], [], lambda p: seen.append(p) or [])
        self.assertEqual(seen[0]["p_cards"][0]["fei"], "3000209996")

    def test_exclude_over_limit_raises(self):
        with self.assertRaises(RuntimeError):
            fh.fetch_rows([], [str(i) for i in range(501)], lambda p: [])

    def test_non_list_response_raises(self):
        cand = fh.candidate_from_card(cardmfds("a", "Foo"), fk)
        with self.assertRaises(RuntimeError):
            fh.fetch_rows([cand], [], lambda p: {"message": "err"})

    def test_sibling_cards_exclude_contains_all_brief_ids(self):
        brief = self.brief([cardmfds("a", "Foo"), cardmfds("b", "Foo"), card483("c", "Bar", "3000209996")])
        sent = []

        def post(payload):
            sent.append(payload)
            return []

        with tempfile.TemporaryDirectory() as d:
            bp = Path(d) / "b.json"
            bp.write_text(json.dumps(brief), encoding="utf-8")
            rc = fh.main(["--brief", str(bp), "--output-dir", str(Path(d) / "out")], post=post, firm_key_fn=fk)
        self.assertEqual(rc, 0)
        self.assertEqual(sent[0]["p_exclude"], ["a", "b", "c"])

    def test_main_writes_schema_and_hides(self):
        brief = self.brief([cardmfds("recall-1", "현진제약"), cardmfds("recall-2", "NoHist"),
                            card483("fda483-1", "AGC S.p.A.", "3020270660"),
                            card483("fda483-2", "AGC Inc", "3000209996")])

        def post(payload):
            return [
                {"id": "recall-1", "name_n": 44, "name_last": "2026-10", "fei_n": 0, "fei_last": None},
                {"id": "recall-2", "name_n": 0, "name_last": None, "fei_n": 0, "fei_last": None},
                {"id": "fda483-1", "fei_n": 0, "fei_last": None, "name_n": 2, "name_last": "2026-10"},
                {"id": "fda483-2", "fei_n": 3, "fei_last": "2024-07", "name_n": 3, "name_last": "2024-07"},
            ]

        with tempfile.TemporaryDirectory() as d:
            bp = Path(d) / "b.json"
            bp.write_text(json.dumps(brief), encoding="utf-8")
            out = Path(d) / "out"
            self.assertEqual(fh.main(["--brief", str(bp), "--output-dir", str(out)], post=post, firm_key_fn=fk), 0)
            written = out / "2026-11-02.json"
            self.assertNotIn(b"\r\n", written.read_bytes())
            data = json.loads(written.read_text(encoding="utf-8"))
        self.assertEqual(data["schema"], "grm-firm-history/v1")
        self.assertEqual(data["as_of"], "2026-11-02")
        self.assertEqual(data["cards"], {
            "recall-1": {"basis": "name", "n": 44, "last": "2026-10"},
            "fda483-2": {"basis": "site", "n": 3, "last": "2024-07"},
        })

    def test_failure_writes_no_file_and_returns_nonzero(self):
        brief = self.brief([cardmfds("a", "Foo")])

        def post(payload):
            raise RuntimeError("RPC HTTP 404")

        with tempfile.TemporaryDirectory() as d:
            bp = Path(d) / "b.json"
            bp.write_text(json.dumps(brief), encoding="utf-8")
            out = Path(d) / "out"
            rc = fh.main(["--brief", str(bp), "--output-dir", str(out)], post=post, firm_key_fn=fk)
            self.assertEqual(rc, 1)
            self.assertFalse(out.exists())

    def test_no_credentials_fails_without_file(self):
        brief = self.brief([cardmfds("a", "Foo")])
        with tempfile.TemporaryDirectory() as d:
            bp = Path(d) / "b.json"
            bp.write_text(json.dumps(brief), encoding="utf-8")
            rc = fh.main(["--brief", str(bp), "--output-dir", str(Path(d) / "o"),
                          "--supabase-url", "", "--supabase-anon-key", ""], firm_key_fn=fk)
            self.assertEqual(rc, 1)

    def test_dry_run_no_network_no_file(self):
        brief = self.brief([cardmfds("a", "Foo")])
        with tempfile.TemporaryDirectory() as d:
            bp = Path(d) / "b.json"
            bp.write_text(json.dumps(brief), encoding="utf-8")
            self.assertEqual(fh.main(["--brief", str(bp), "--output-dir", str(Path(d) / "o"), "--dry-run"],
                                     firm_key_fn=fk), 0)
            self.assertFalse((Path(d) / "o").exists())


class RealBriefTest(unittest.TestCase):
    """실제 11-02 발행본 + 실제 render 업체키: 범위·AGC·병합 디제스트 확인(네트워크 0)."""

    def setUp(self):
        self.brief = json.loads((REPO / "web" / "data" / "briefs" / "brief_web_2026_11_02.json")
                                .read_text(encoding="utf-8"))
        self.key_fn = fh._load_firm_key_fn()

    def test_candidates_scope_and_agc(self):
        cands = {c["id"]: c for c in fh.collect_candidates(self.brief, self.key_fn)}
        self.assertTrue(cands)
        self.assertTrue(all(c["scope"] in ("fda483", "mfds") for c in cands.values()))
        agc = cands["fda483-195525"]
        self.assertEqual(agc["fei"], "3020270660")
        self.assertEqual(agc["firm_key"], "agc biologics spa")  # 별칭(105)으로 미국 AGC 와 묶이는 키
        self.assertNotIn("fda483-195404", cands)  # MSD 11건 병합 디제스트


if __name__ == "__main__":
    unittest.main()
