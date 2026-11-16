#!/usr/bin/env python3
"""실사 대비 노트 (2026-11-15) — /inspection-notes/ · /inspection-notes/<id>/ (ko·en).

  · 로더·검증(inspection_notes_page) — 패턴 5개·인용 2개씩·필수 필드·한·영 쌍·금지어·파일 이름
  · 뷰 — 언어별 필드, 말줄임, 문서 링크 규칙(그 트리에 문서 페이지가 없으면 링크 없음), 복사 글
  · 커밋된 노트 — 전부 검증 통과, 금지어 없음, 경고서한 인용문이 문서 정본 본문에 글자 그대로 있음
  · 실제 렌더 — ko·en 목록·노트 페이지, 영어판 한글 없음, 깨진 문서 링크 없음, sitemap·hreflang,
    찾아보기 진입, 상단 내비게이션에는 없음

CI 는 `tests/test_web_inspection_notes_page.py` shim 으로 순회한다. 직접:
  python web/tests/test_inspection_notes_page.py
"""
from __future__ import annotations

import copy
import json
import pathlib
import re
import shutil
import sys
import tempfile
import unittest

WEB_DIR = pathlib.Path(__file__).resolve().parent.parent
REPO_ROOT = WEB_DIR.parent
sys.path.insert(0, str(WEB_DIR))
sys.path.insert(0, str(REPO_ROOT))
import render  # noqa: E402
import inspection_notes_page as inp  # noqa: E402

HANGUL = re.compile(r"[ㄱ-ㆎ가-힣]")


def _quote(i: int, j: int, *, source: str = "FDA Warning Letter", doc: str = "") -> dict:
    return {"finding_id": f"finding-{i}{j}", "raw_signal_id": f"rawsig-{i}{j}",
            "document_id": doc or f"doc{i}{j}", "source": source,
            "firm": f"Firm {i}{j} & Co.", "date": "2026-01-02",
            "quote_ko": f"귀사는 절차 {i}-{j} 를 따르지 않았다.",
            "quote_en": f"Your firm failed to follow procedure {i}-{j}."}


def make_note(note_id: str = "sample-topic", **over) -> dict:
    note = {
        "schema": inp.SCHEMA, "id": note_id, "as_of": "2026-11-15",
        "area_ko": "품질시스템", "area_en": "Quality system",
        "title_ko": "표본 주제", "title_en": "Sample topic",
        "glossary_ids": ["capa"],
        "aliases_ko": ["견본"], "aliases_en": ["sample topic", "specimen"],
        "search_q_ko": "표본 주제", "search_q_en": "sample topic",
        "lead_ko": "FDA 는 절차가 지켜졌는지를 봅니다. 아래는 경고서한에 적힌 지적입니다.",
        "lead_en": "FDA looks at whether the procedure was followed. The findings below are quoted.",
        "patterns": [{"title_ko": f"절차 {i} 을 지키지 않았다", "title_en": f"Procedure {i} was not followed",
                      "quotes": [_quote(i, 1), _quote(i, 2, source="FDA 483")]}
                     for i in range(1, 6)],
        "clauses": [
            {"key": "21 CFR 211.192", "finding_id": "finding-11", "firm": "Firm 11 & Co.",
             "date": "2026-01-02", "quote_ko": "귀사는 철저히 조사하지 않았다",
             "quote_en": "Your firm failed to thoroughly investigate", "trailing_ellipsis": True,
             "note_ko": "다른 사례도 이 조항 아래 적혔습니다.", "note_en": "Other findings were cited here too."},
            {"key": "21 CFR 212.50", "note_ko": "PET 의약품에 적용됩니다.", "note_en": "Applies to PET drugs."},
        ],
        "checks_ko": ["절차가 문서로 있습니까?", "기록이 남습니까?"],
        "checks_en": ["Is the procedure documented?", "Are records kept?"],
        "further_reading": [{"label_ko": "FDA 가이던스", "label_en": "FDA guidance",
                             "url": "https://www.fda.gov/media/71026/download"}],
        "related": ["other-topic", "missing-topic"],
    }
    note["patterns"][0]["quotes"][0]["trailing_ellipsis"] = True
    note.update(over)
    return note


def view(note, lang="ko", **over):
    args = dict(doc_slugs={}, notes_by_id={note["id"]: note}, glossary_terms={},
                source_labels={"FDA Warning Letter": "FDA 경고서한" if lang == "ko" else "FDA Warning Letter",
                               "FDA 483": "FDA 483"},
                search_href="findings/index.html?q=x&text=1",
                abs_url=lambda p: "https://example.org/" + ("" if lang == "ko" else "en/") + p,
                copy_labels={"name": "실사 대비 노트", "as_of": "기준일"})
    args.update(over)
    return inp.note_view(note, lang, **args)


class InspectionNoteValidateTest(unittest.TestCase):
    def test_sample_is_valid(self):
        inp.validate(make_note())

    def _bad(self, mutate):
        note = make_note()
        mutate(note)
        with self.assertRaises(inp.NoteError):
            inp.validate(note)

    def test_schema_and_id(self):
        self._bad(lambda n: n.update(schema="grm-inspection-note/v0"))
        self._bad(lambda n: n.update(id="Bad Id"))
        self._bad(lambda n: n.update(as_of="2026/11/15"))

    def test_exactly_five_patterns(self):
        self._bad(lambda n: n["patterns"].pop())
        self._bad(lambda n: n["patterns"].append(copy.deepcopy(n["patterns"][0])))

    def test_exactly_two_quotes_per_pattern(self):
        self._bad(lambda n: n["patterns"][2]["quotes"].pop())
        self._bad(lambda n: n["patterns"][2]["quotes"].append(_quote(9, 9)))

    def test_required_quote_fields(self):
        for key in ("finding_id", "document_id", "firm", "date", "quote_ko", "quote_en", "source"):
            self._bad(lambda n, k=key: n["patterns"][0]["quotes"][1].pop(k))
        self._bad(lambda n: n["patterns"][0]["quotes"][1].update(source="MHRA"))

    def test_ko_en_pairs(self):
        for key in ("title_en", "lead_en", "area_en", "search_q_en", "aliases_en", "checks_en"):
            self._bad(lambda n, k=key: n.pop(k))
        self._bad(lambda n: n["patterns"][0].pop("title_en"))
        self._bad(lambda n: n["checks_en"].pop())
        self._bad(lambda n: n["clauses"][0].pop("quote_en"))
        self._bad(lambda n: n["clauses"][1].pop("note_en"))
        self._bad(lambda n: n["further_reading"][0].pop("label_en"))

    def test_english_fields_hold_no_hangul(self):
        self._bad(lambda n: n.update(title_en="표본"))
        self._bad(lambda n: n["patterns"][0]["quotes"][0].update(quote_en="귀사는 failed"))
        self._bad(lambda n: n["checks_en"].__setitem__(0, "절차?"))

    def test_clause_may_carry_only_a_note_but_not_nothing(self):
        inp.validate(make_note(clauses=[{"key": "21 CFR 212.50", "note_ko": "가", "note_en": "a"}]))
        self._bad(lambda n: n.update(clauses=[{"key": "21 CFR 212.50"}]))

    def test_further_reading_may_be_empty(self):
        inp.validate(make_note(further_reading=[]))
        self._bad(lambda n: n["further_reading"][0].update(url="http://insecure.example"))

    def test_forbidden_word_is_rejected_anywhere(self):
        self._bad(lambda n: n.update(lead_ko="챗봇에게 물어보세요. 아래는 지적입니다."))
        self._bad(lambda n: n["checks_en"].__setitem__(0, "Ask the Chatbot?"))


class InspectionNoteLoadTest(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="grm_in_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, name, note):
        (self.tmp / name).write_text(json.dumps(note, ensure_ascii=False), encoding="utf-8")

    def test_missing_dir_is_empty_and_declares_no_routes(self):
        self.assertEqual(inp.load_notes(self.tmp / "nope"), [])
        self.assertEqual(inp.routes_present([]), set())
        self.assertEqual(inp.routes_present(None), set())

    def test_loads_sorted_and_declares_index_plus_each(self):
        self._write("b-topic.json", make_note("b-topic"))
        self._write("a-topic.json", make_note("a-topic"))
        notes = inp.load_notes(self.tmp)
        self.assertEqual([n["id"] for n in notes], ["a-topic", "b-topic"])
        self.assertEqual(inp.routes_present(notes),
                         {"inspection-notes/", "inspection-notes/a-topic/", "inspection-notes/b-topic/"})

    def test_id_must_match_file_name(self):
        self._write("other.json", make_note("a-topic"))
        with self.assertRaises(inp.NoteError):
            inp.load_notes(self.tmp)

    def test_broken_note_stops_the_build(self):
        note = make_note("a-topic")
        note["patterns"].pop()
        self._write("a-topic.json", note)
        with self.assertRaises(inp.NoteError):
            inp.load_notes(self.tmp)


class InspectionNoteViewTest(unittest.TestCase):
    def test_language_is_decided_by_the_view(self):
        note = make_note()
        ko, en = view(note, "ko"), view(note, "en")
        self.assertEqual(ko["title"], "표본 주제")
        self.assertEqual(en["title"], "Sample topic")
        self.assertEqual(ko["patterns"][1]["quotes"][0]["text"], "귀사는 절차 2-1 를 따르지 않았다.")
        self.assertEqual(en["patterns"][1]["quotes"][0]["text"], "Your firm failed to follow procedure 2-1.")
        self.assertFalse(HANGUL.search(json.dumps(
            {k: v for k, v in en.items() if k != "copy_text"}, ensure_ascii=False)))
        self.assertEqual([p["n"] for p in ko["patterns"]], [1, 2, 3, 4, 5])
        self.assertEqual(ko["checks"], note["checks_ko"])
        self.assertEqual(en["checks"], note["checks_en"])

    def test_trailing_ellipsis_on_quotes_and_clauses(self):
        v = view(make_note())
        self.assertTrue(v["patterns"][0]["quotes"][0]["text"].endswith("따르지 않았다. …"))
        self.assertFalse(v["patterns"][0]["quotes"][1]["text"].endswith("…"))
        self.assertEqual(v["clauses"][0]["quote"], "귀사는 철저히 조사하지 않았다 …")

    def test_clause_without_quote_shows_key_and_note_only(self):
        c = view(make_note())["clauses"][1]
        self.assertEqual((c["key"], c["quote"], c["firm"], c["date"], c["note"]),
                         ("21 CFR 212.50", "", "", "", "PET 의약품에 적용됩니다."))

    def test_document_link_only_when_the_page_exists_in_this_tree(self):
        note = make_note()
        v = view(note, doc_slugs={"doc11": "slug-11"})
        q1, q2 = v["patterns"][0]["quotes"]
        self.assertEqual(q1["doc_path"], "findings/doc/slug-11/")
        self.assertEqual(q2["doc_path"], "")          # 문서 페이지가 없으면 링크 없이 글자만
        self.assertEqual((q2["source"], q2["firm"], q2["date"]), ("FDA 483", "Firm 12 & Co.", "2026-01-02"))
        self.assertEqual(q1["source"], "FDA 경고서한")

    def test_aliases_ko_carries_english_too_and_drops_the_title(self):
        note = make_note()
        self.assertEqual(view(note, "ko")["aliases"], ["견본", "sample topic", "specimen"])
        self.assertEqual(view(note, "en")["aliases"], ["specimen"])   # 'sample topic' = 제목

    def test_related_and_terms_skip_what_does_not_exist(self):
        note, other = make_note(), make_note("other-topic", title_ko="다른 주제")
        v = view(note, notes_by_id={note["id"]: note, "other-topic": other},
                 glossary_terms={"capa": "CAPA"})
        self.assertEqual(v["related"], [{"title": "다른 주제", "path": "inspection-notes/other-topic/"}])
        self.assertEqual(v["terms"], [{"label": "CAPA", "path": "glossary/capa/"}])
        self.assertEqual(view(note)["terms"], [])

    def test_further_reading_empty_stays_empty(self):
        self.assertEqual(view(make_note(further_reading=[]))["further"], [])

    def test_copy_text_is_plain_and_carries_quotes_sources_urls(self):
        v = view(make_note(), doc_slugs={"doc11": "slug-11"})
        text = v["copy_text"]
        self.assertTrue(text.startswith("실사 대비 노트: 표본 주제 (기준일 2026-11-15)\n"
                                        "https://example.org/inspection-notes/sample-topic/\n"))
        self.assertIn('- "귀사는 절차 1-1 를 따르지 않았다. …" (FDA 경고서한 · Firm 11 & Co. · 2026-01-02 · '
                      'https://example.org/findings/doc/slug-11/)', text)
        self.assertIn('- "귀사는 절차 1-2 를 따르지 않았다." (FDA 483 · Firm 12 & Co. · 2026-01-02)', text)
        self.assertEqual(text.count('- "'), 10)
        self.assertNotIn("<", text)

    def test_index_groups_by_area_with_first_sentence(self):
        a = make_note("a-topic", title_ko="나", area_ko="시험실")
        b = make_note("b-topic", title_ko="가", area_ko="시험실")
        c = make_note("c-topic", area_ko="무균·오염 관리")
        groups = inp.index_view([a, b, c], "ko")
        self.assertEqual([g["area"] for g in groups], ["무균·오염 관리", "시험실"])
        self.assertEqual([n["title"] for n in groups[1]["notes"]], ["가", "나"])
        self.assertEqual(groups[0]["notes"][0]["summary"], "FDA 는 절차가 지켜졌는지를 봅니다.")
        self.assertEqual(groups[0]["notes"][0]["path"], "inspection-notes/c-topic/")

    def test_index_area_order_is_fixed_and_same_in_ko_and_en(self):
        # 분야 순서는 AREA_ORDER 한 곳 — 한·영이 같은 순서로 나온다(글자순 아님).
        want_ko = ["무균·오염 관리", "시험실", "데이터와 기록", "품질시스템", "공급·제조·포장"]
        want_en = ["Sterility and contamination control", "Laboratory", "Data and records",
                   "Quality system", "Supply, manufacturing and packaging"]
        self.assertEqual([ko for ko, _ in inp.AREA_ORDER], want_ko)
        self.assertEqual([en for _, en in inp.AREA_ORDER], want_en)
        notes = inp.load_notes()
        self.assertEqual([g["area"] for g in inp.index_view(notes, "ko")], want_ko)
        self.assertEqual([g["area"] for g in inp.index_view(notes, "en")], want_en)
        # 목록에 없는 분야는 맨 끝에 이름순으로 붙는다(빌드는 깨지지 않는다).
        extra = [make_note(f"x{i}-topic", area_ko=ko, area_en=en)
                 for i, (ko, en) in enumerate([("하 새 분야", "Zeta area"), ("가 새 분야", "Alpha area")])]
        self.assertEqual([g["area"] for g in inp.index_view(notes + extra, "ko")],
                         want_ko + ["가 새 분야", "하 새 분야"])
        self.assertEqual([g["area"] for g in inp.index_view(notes + extra, "en")],
                         want_en + ["Alpha area", "Zeta area"])

    def test_glossary_links_by_term(self):
        links = inp.glossary_links([make_note()], "en")
        self.assertEqual(links, {"capa": [{
            "title": "Sample topic", "path": "inspection-notes/sample-topic/",
            "summary": "FDA looks at whether the procedure was followed."}]})


class CommittedNotesTest(unittest.TestCase):
    """커밋된 노트 정본(web/data/inspection_notes) — 형식·금지어·인용 대조."""

    @classmethod
    def setUpClass(cls):
        cls.notes = inp.load_notes()          # 검증 실패면 여기서 터진다
        cls.docs = {d["document_id"]: d
                    for d in (render.load_findings_docs() or {}).get("documents", [])}

    def test_notes_exist_and_follow_the_fixed_shape(self):
        self.assertTrue(self.notes, "노트가 하나도 없다")
        for n in self.notes:
            self.assertEqual(len(n["patterns"]), 5, n["id"])
            for p in n["patterns"]:
                self.assertEqual(len(p["quotes"]), 2, n["id"])

    def test_forbidden_word_absent(self):
        for path in sorted(inp.NOTES_DIR.glob("*.json")):
            low = path.read_text(encoding="utf-8").lower()
            for word in inp.FORBIDDEN_WORDS:
                self.assertNotIn(word, low, path.name)

    def test_related_and_glossary_ids_point_at_real_things(self):
        ids = {n["id"] for n in self.notes}
        terms = {t["id"] for t in (render.load_glossary() or [])}
        for n in self.notes:
            self.assertLessEqual(set(n["related"]), ids, n["id"])
            self.assertLessEqual(set(n["glossary_ids"]), terms, n["id"])

    def test_quotes_agree_with_the_committed_document(self):
        """인용의 업체·날짜·출처종류는 문서 정본과 같고, **경고서한** 인용문은 그 문서의 지적 본문에
        글자 그대로 들어 있다(한국어는 번역문, 영어는 원문).

        ★FDA 483 인용문은 여기서 대조하지 못한다 — 483 인용은 '구체 내용'(detail_text)에서 오는데
          findings_docs.json 은 그 본문을 싣지 않는다(지적 한 줄만 있다). 483 인용의 글자 대조는
          노트를 지을 때 운영 DB 로 한다. 문서 정본에 아직 없는 문서(최근 공개분)도 대조 대상이 아니다."""
        checked = 0
        for n in self.notes:
            for p in n["patterns"]:
                for q in p["quotes"]:
                    doc = self.docs.get(q["document_id"])
                    if not doc:
                        continue
                    where = f"{n['id']} · {q['firm']} · {q['date']}"
                    self.assertEqual(doc["firm_name"], q["firm"], where)
                    self.assertEqual(doc["published_date"], q["date"], where)
                    self.assertEqual(doc["source"], q["source"], where)
                    if q["source"] != "FDA Warning Letter":
                        continue
                    ko = "\n".join(f.get("text_ko") or "" for f in doc["findings"])
                    en = "\n".join(f.get("text_orig") or "" for f in doc["findings"])
                    self.assertIn(q["quote_ko"], ko, where)
                    self.assertIn(q["quote_en"], en, where)
                    checked += 1
        self.assertGreater(checked, 0, "대조한 인용이 0건 — 가드가 아무것도 보지 않았다")


class InspectionNotesRenderTest(unittest.TestCase):
    """실제 사이트 빌드(문서 페이지는 끈 채) — 커밋된 노트로 ko·en 페이지가 선다."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = pathlib.Path(tempfile.mkdtemp(prefix="grm_in_site_"))
        cls.out = cls.tmp / "dist"
        cls.meta = render.render_site(out_dir=cls.out, render_doc_pages=False)
        cls.notes = inp.load_notes()
        docs = (render.load_findings_docs() or {}).get("documents", [])
        cls.ko_doc_slugs = {d["slug"] for d in docs}
        cls.en_doc_slugs = {d["slug"] for d in docs if render.doc_is_english(d)}
        cls.sitemap = (cls.out / "sitemap.xml").read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _html(self, rel):
        return (self.out / rel / "index.html").read_text(encoding="utf-8")

    def _main(self, rel):
        html = self._html(rel)
        return html[html.index("<main>"):html.index("</main>")]

    def test_every_route_is_written_in_both_trees(self):
        for route in sorted(inp.routes_present(self.notes)):
            for prefix in ("", "en/"):
                self.assertIn(f"{prefix}{route}index.html", self.meta["written"])

    def test_note_page_structure_ko(self):
        for n in self.notes:
            main = self._main(f"inspection-notes/{n['id']}")
            self.assertIn(f'<h1 class="in-h1">{render._escape(n["title_ko"])}</h1>', main)
            self.assertEqual(main.count('<section class="in-pat">'), 5, n["id"])
            self.assertEqual(main.count('<blockquote class="in-q">'), 10, n["id"])
            for p in n["patterns"]:
                for q in p["quotes"]:
                    shown = q["quote_ko"] + (" …" if q.get("trailing_ellipsis") else "")
                    self.assertIn(f'<blockquote class="in-q">{render._escape(shown)}</blockquote>', main)
                    self.assertIn(str(render._escape(q["firm"])), main)
            self.assertIn(f"기준일 {n['as_of']}.", main)
            self.assertIn("AI 가 공개 지적을 묶어 정리하고 사람이 확인했습니다. 인용문은 지적 번역문 그대로이며", main)
            self.assertIn("findings/index.html?q=" + render._url_quote(n["search_q_ko"], safe="") + "&amp;text=1", main)
            self.assertIn('id="in-copy"', main)
            self.assertEqual(len(re.findall(r"<li>", main[main.index('<ol class="in-check">'):
                                                         main.index("</ol>")])), len(n["checks_ko"]))

    def test_source_label_by_language(self):
        ko = self._main("inspection-notes/media-fill")
        en = self._main("en/inspection-notes/media-fill")
        self.assertIn("FDA 경고서한 &middot; Turbare Manufacturing &middot; 2025-12-02", ko)
        self.assertIn("FDA Warning Letter &middot; Turbare Manufacturing &middot; 2025-12-02", en)

    def test_english_pages_hold_no_hangul(self):
        for rel in ["en/inspection-notes"] + [f"en/inspection-notes/{n['id']}" for n in self.notes]:
            main = self._main(rel)
            m = HANGUL.search(main)
            self.assertIsNone(m, f"{rel}: {main[max(0, (m.start() if m else 0) - 60):(m.end() if m else 0) + 60]!r}")
        en = self._main("en/inspection-notes/media-fill")
        self.assertIn("Compiled by AI from public findings and checked by a person. Quotations are "
                      "verbatim from the original documents; (b)(4) marks text redacted in the original. "
                      "As of 2026-11-15.", en)

    def test_document_links_point_only_at_pages_that_exist(self):
        """문서 링크는 그 트리에 문서 페이지가 서는 slug 만 — 그리고 정본에 있는 문서는 빠짐없이 링크된다."""
        docs = {d["document_id"]: d["slug"]
                for d in (render.load_findings_docs() or {}).get("documents", [])}
        for prefix, slugs in (("", self.ko_doc_slugs), ("en/", self.en_doc_slugs)):
            linked_total = 0
            for n in self.notes:
                main = self._main(f"{prefix}inspection-notes/{n['id']}")
                linked = re.findall(r'href="\.\./\.\./findings/doc/([^/"]+)/"', main)
                self.assertLessEqual(set(linked), slugs, f"{prefix}{n['id']}: 없는 문서로 가는 링크")
                want = [docs[q["document_id"]] for p in n["patterns"] for q in p["quotes"]
                        if docs.get(q["document_id"]) in slugs]
                self.assertEqual(sorted(linked), sorted(want), f"{prefix}{n['id']}")
                self.assertEqual(main.count("<blockquote"), 10)
                linked_total += len(linked)
            self.assertGreater(linked_total, 0)

    def test_related_notes_and_terms_link_inside_the_tree(self):
        main = self._main("inspection-notes/media-fill")
        self.assertIn('href="../../inspection-notes/environmental-monitoring/"', main)
        self.assertIn('href="../../glossary/aseptic-process-simulation/"', main)

    def test_index_lists_every_note(self):
        for prefix, key in (("", "title_ko"), ("en/", "title_en")):
            main = self._main(f"{prefix}inspection-notes")
            for n in self.notes:
                self.assertIn(f'href="../inspection-notes/{n["id"]}/"', main)
                self.assertIn(f"<b>{render._escape(n[key])}</b>", main)

    def test_sitemap_canonical_hreflang(self):
        base = render.SITE_BASE_URL
        for route in sorted(inp.routes_present(self.notes)):
            self.assertIn(f"<loc>{base}/{route}</loc>", self.sitemap)
            self.assertIn(f"<loc>{base}/en/{route}</loc>", self.sitemap)
            for prefix in ("", "en/"):
                html = self._html(f"{prefix}{route}".rstrip("/"))
                self.assertIn(f'<link rel="canonical" href="{base}/{prefix}{route}"', html)
                self.assertIn(f'hreflang="ko" href="{base}/{route}"', html)
                self.assertIn(f'hreflang="en" href="{base}/en/{route}"', html)

    def test_reachable_from_browse_but_not_in_top_nav(self):
        for prefix in ("", "en/"):
            browse = self._html(f"{prefix}browse")
            self.assertIn('href="../inspection-notes/', browse[browse.index("<main"):])
            nav = browse[browse.index('<nav id="navmenu">'):]
            nav = nav[:nav.index("</nav>")]
            self.assertNotIn("inspection-notes", nav)

    def test_no_remote_call_and_no_forbidden_word_on_pages(self):
        for prefix in ("", "en/"):
            for n in self.notes:
                html = self._html(f"{prefix}inspection-notes/{n['id']}")
                script = html[html.index('getElementById("in-copy")'):]
                script = script[:script.index("</script>")]
                for banned in ("fetch(", "XMLHttpRequest", "innerHTML", "sendBeacon"):
                    self.assertNotIn(banned, script)
                main = self._main(f"{prefix}inspection-notes/{n['id']}").lower()
                for word in inp.FORBIDDEN_WORDS:
                    self.assertNotIn(word, main)

    def test_glossary_term_pages_link_to_their_note(self):
        """노트의 glossary_ids 에 든 용어 페이지마다 링크 블록 하나 — 노트 제목 + lead 첫 문장(ko·en)."""
        seen = 0
        for n in self.notes:
            for gid in n["glossary_ids"]:
                for prefix, sfx in (("", "_ko"), ("en/", "_en")):
                    main = self._main(f"{prefix}glossary/{gid}")
                    want = (f'<a class="gt-inote" href="../../inspection-notes/{n["id"]}/">'
                            f'<b>{render._escape(n["title" + sfx])}</b>'
                            f'<span>{render._escape(inp.first_sentence(n["lead" + sfx]))}</span></a>')
                    self.assertIn(want, main, f"{prefix}glossary/{gid}")
                    if prefix:
                        block = main[main.index('class="gt-inote"'):]
                        self.assertIsNone(HANGUL.search(block[:block.index("</section>")]), gid)
                    seen += 1
        self.assertGreater(seen, 0)

    def test_glossary_terms_without_a_note_get_no_block(self):
        with_note = {g for n in self.notes for g in n["glossary_ids"]}
        others = [t["id"] for t in (render.load_glossary() or []) if t["id"] not in with_note][:20]
        self.assertTrue(others)
        for gid in others:
            for prefix in ("", "en/"):
                self.assertNotIn('class="gt-inote"', self._html(f"{prefix}glossary/{gid}"), gid)

    def test_style_keeps_the_hangul_rules(self):
        css = (WEB_DIR / "partials" / "inspection_notes_style.html").read_text(encoding="utf-8")
        self.assertNotIn("letter-spacing", css)
        self.assertNotIn("text-transform", css)
        self.assertNotIn("var(--band", css)      # 검은 띠 금지


class NoNotesNoPagesTest(unittest.TestCase):
    def test_without_notes_nothing_is_declared(self):
        orig = inp.load_notes
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="grm_in_none_"))
        try:
            inp.load_notes = lambda *a, **k: []
            meta = render.render_site(out_dir=tmp / "dist", render_doc_pages=False)
        finally:
            inp.load_notes = orig
        try:
            self.assertFalse([w for w in meta["written"] if "inspection-notes" in w])
            self.assertNotIn("inspection-notes", (tmp / "dist" / "sitemap.xml").read_text(encoding="utf-8"))
            self.assertNotIn("inspection-notes",
                             (tmp / "dist" / "browse" / "index.html").read_text(encoding="utf-8"))
            for rel in ("glossary/capa", "en/glossary/capa"):
                self.assertNotIn('class="gt-inote"',
                                 (tmp / "dist" / rel / "index.html").read_text(encoding="utf-8"))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
