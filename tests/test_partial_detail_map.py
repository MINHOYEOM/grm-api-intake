"""483 '구체 내용' "원문 일부" 대상 표 (UI 2단 2026-11-12).

판정은 content_completeness.partial_detail_notice 한 곳 — 표는 그 결과의 지적 id 사본이다.
"""
from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "web"))

import content_completeness as cc  # noqa: E402
import partial_detail_map as pdm  # noqa: E402
import render  # noqa: E402

CUT = "Review of the preparation re"


class BuildTest(unittest.TestCase):
    def test_only_function_true_rows_survive(self):
        rows = [
            {"source": "FDA 483", "document_id": "fda483-163788", "number": "9", "finding_id": "a", "detail": CUT},
            {"source": "FDA 483", "document_id": "fda483-163788", "number": "9", "finding_id": "b", "detail": CUT + " done."},
            {"source": "FDA 483", "document_id": "fda483-163788", "number": "1", "finding_id": "c", "detail": CUT},
            {"source": "FDA 483", "document_id": "fda483-1", "number": "9", "finding_id": "d", "detail": CUT},
            {"source": "FDA 483", "document_id": "fda483-163788", "number": 14, "finding_id": "e", "detail": CUT},
        ]
        out = pdm.build(rows, "2026-11-12")
        self.assertEqual(out["finding_ids"], ["a", "e"])
        self.assertEqual(out["schema"], "grm-partial-detail/v1")

    def test_committed_table_matches_known_documents(self):
        data = json.loads((ROOT / "web" / "data" / "partial_detail_findings.json").read_text(encoding="utf-8"))
        ids = data["finding_ids"]
        self.assertTrue(ids, "표가 비었다")
        self.assertEqual(ids, sorted(set(ids)))
        n_obs = sum(len(v) for v in cc.KNOWN_PARTIAL_DETAILS["FDA 483"].values())
        self.assertLessEqual(len(ids), n_obs, "판정 목록보다 많은 지적 id")

    def test_loader_degrades_to_empty(self):
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="grm_pd_"))
        self.assertEqual(render.load_partial_detail_ids(tmp / "none.json"), frozenset())
        bad = tmp / "bad.json"
        bad.write_text(json.dumps({"schema": "x", "finding_ids": ["a"]}), encoding="utf-8")
        self.assertEqual(render.load_partial_detail_ids(bad), frozenset())
        good = tmp / "good.json"
        good.write_text(json.dumps({"schema": "grm-partial-detail/v1", "finding_ids": ["a", ""]}), encoding="utf-8")
        self.assertEqual(render.load_partial_detail_ids(good), frozenset({"a"}))

    def test_js_sentence_rule_matches_python(self):
        js = (ROOT / "web" / "assets" / "doc-detail.js").read_text(encoding="utf-8")
        self.assertIn(r"""var SENT_END = /[.!?][)"'”’]?\s*$/;""", js)
        self.assertEqual(cc._SENTENCE_END_RE.pattern, r"[.!?][)\"'”’]?\s*$")


# [11-12 대표 결정] 상세는 끝맺음과 무관하게 싣고, 끝의 483 양식 바닥글 잡음만 화면에서 걷는다.
# 고정값은 공개 상세 10,992행 실측 표본에서 그대로 옮겼다(OCR 표기 그대로).
TRIM_CUT = [  # (원문 꼬리, 걷힌 뒤 끝, 걷힌 규칙 하나)
    ("mixing blades stored as clean and ready for use in production. SEE REVERSEOF THIS PAGE",
     "ready for use in production.", "see_reverse"),
    ("Records of the aseptic process simulation were not reviewed by the unit. THIS DOCUMENT LISTS "
     "OBSERVATIONS MADE BY THE FDA REPRESENTATIVE(S) DURING THE INSPECTION OF YOUR FACILITY. THEY ARE",
     "were not reviewed by the unit.", "fda_rep"),
    ("No written procedure covers the [redacted] cleaning step. Ths document lists O!ISl:RVATIONS MADE BY THE",
     "covers the [redacted] cleaning step.", "doc_lists"),
    ("Trend limits for APIs were not calculated from the [redacted] batches. FACILITIES AND EQUIPMENT SYSTEM",
     "calculated from the [redacted] batches.", "system_heading"),
    ("Your firm did not prepare to determine that the fill weight for each capsule is the same. "
     "Employee(s) Signature: Joanne E. Kind",
     "for each capsule is the same.", "signature"),
    ("Product sterilized when produced can be [redacted] to ensure sterility remains. Liatte Krueger, Invest igator,",
     "to ensure sterility remains.", "investigator"),
    ("The 45 day notification was sent to the Baltimore District Office on June 5, 2007. SEE",
     "Office on June 5, 2007.", "stray_see"),
    ("Monitoring was not performed in the area of the [redacted]. 15340 Vantage Pkwy E Ste 220 CITY, STATE, "
     "ZIP CODE, COUNTRY TYPE ESTABLISHMENT INSPECTED Houston, TX 77032-1901 Producer of Sterile Drugs",
     "15340 Vantage Pkwy E Ste 220", "address"),
    ("These methods are capable of accurately quantitating non-viable particulates. - ‘",
     "quantitating non-viable particulates.", "trailing_junk"),    # [11-12 2차] 국문 번역된 바닥글 · 지방청 머리글 줄
    ("실사 중 파티클 모니터링 기록이 검토되지 않았고 일탈 조사도 수행되지 않았다. -·------· "
     "이 문서는 실사 중 FDA 담당자가 작성한 관찰사항을 기재한 것이다. 이는",
     "일탈 조사도 수행되지 않았다.", "fda_rep"),
    ("출하 시험 중 OOT 처리 방법을 설명하는 절차가 추세 한도 없이 운영되고 있다. "
     "시설 및 장비 시스템(FACILITIES AND EQUIPMENT SYSTEM)",
     "추세 한도 없이 운영되고 있다.", "system_heading"),
    ("입자 시험 결과가 제품과 관련됨을 추가로 시사하는 자료가 검토되지 않았다. "
     "시설(FACILITY) 및 장비 시스템(SYSTEM)",
     "검토되지 않았다.", "system_heading"),
    ("The firm did not perform adequate environmental monitoring of the aseptic area during production. "
     "DISTRICT ADDRESS ANO PHONE NUMBER 6th & Kipling St. (P. 0. Box 25087) Denver, co 80225- 0087 "
     "FEJNUMBER (303) 236- 3000 Fax: (303) 236- 3100 3002946837",
     "aseptic area during production.", "district"),
    ("The firm did not perform adequate environmental monitoring of the aseptic area during production. "
     "DEP ART!\\fi:NT OF HEALTH AND HUMAi'l SERVICES FOODAND DRUG ADMINISTRATION D",
     "aseptic area during production.", "district"),
]
TRIM_KEEP = [  # 걷으면 안 되는 것 — 정상 문장 끝·본문이 뒤에 이어진 바닥글·짧은 본문
    "Ophthalmic Solution, 0.01%, Lot 022621A was compounded on 2/26/2021 using the LF [redacted]",
    "The deviation was not entered into the quality system",
    "CITY. STATE. ZIP CODE. COUNTRY TYPE ESTABUSI-NENT INSPECTED Laurel, MS 39440-4226 Producer of "
    "Non-Sterile Drugs A.Triple H cream lot AJCECAB [redacted] 6 that contains testosterone B. "
    "Progesterone capsules 100 mg lot",
    "The firm validated the process. COUNTRY TYPE ESTABLISHMENT INSPECTED Outsourcing Facility being "
    "conducted as described in SOP-SC-PR-4347, Version 12.0 dated October 31, 2022.",
    "Short note. SEE REVERSE OF THIS PAGE",
    "Colic Calm® Plus is a homeopathic product marketed for infants.",    # 국문 문장 끝도 본문 — 주소·지방청 칸 뒤에 국문 문장이 이어지면 걷지 않는다
    "본 시설은 규정을 준수하지 않았다. DISTRICT ADDRESS AND PHONE NUMBER (214)253-5200 이 실패는 다음 충전 라인에도 영향을 미친다.",
    "Examples of non-sterile powders include Hydromorphone, Morphine, Fentanyl, Bupivacaine and Clonidine.",
    "The deviation was not recorded in the quality system",
]


@unittest.skipUnless(shutil.which("node"), "node 없음")
class FormFooterTrimTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        src = (ROOT / "web" / "assets" / "doc-detail.js").read_text(encoding="utf-8")
        cls.block = src[src.index("// <form-footer-trim>"):src.index("// </form-footer-trim>")]

    def run_trim(self, texts):
        prog = (self.block + "\nconst fs=require('fs');const xs=JSON.parse(fs.readFileSync(0,'utf8'));"
                "process.stdout.write(JSON.stringify(xs.map(trimFormFooter)));")
        out = subprocess.run(["node", "-e", prog], input=json.dumps(texts).encode("utf-8"),
                             capture_output=True, check=True)
        return json.loads(out.stdout.decode("utf-8"))

    def test_cuts_footer_tails(self):
        got = self.run_trim([t for t, _, _ in TRIM_CUT])
        for (src, end, rule), g in zip(TRIM_CUT, got):
            self.assertTrue(g["text"].endswith(end), (rule, g["text"][-80:]))
            self.assertIn(rule, g["cuts"], (rule, g["cuts"]))
            self.assertTrue(src.startswith(g["text"]), "앞부분은 한 글자도 바뀌면 안 된다")

    def test_keeps_body_text(self):
        got = self.run_trim(TRIM_KEEP)
        for src, g in zip(TRIM_KEEP, got):
            self.assertEqual(g["text"], src, src[-80:])
            self.assertEqual(g["cuts"], [])

    def test_display_shows_unfinished_details(self):
        js = (ROOT / "web" / "assets" / "doc-detail.js").read_text(encoding="utf-8")
        self.assertNotIn("if (!whole && !partial[fid]) return;", js)
        self.assertIn("text = trimFormFooter(text).text;", js)
        self.assertIn("if (!whole && partial[fid]) {", js)


if __name__ == "__main__":
    unittest.main()
