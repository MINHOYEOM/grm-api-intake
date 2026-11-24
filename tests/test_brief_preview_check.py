import brief_preview_check as preview


ISSUE = "brief_web_2026_11_23.json"
LABELS = ("개인 실명", "영문 한글", "빈 칸", "옛 실사 표시", "묶음 카드")


def test_published_issue_cli(capsys):
    assert preview.main(["2026-11-23"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert "5유형 점검" in lines[0]
    rows = {line.split("|")[1].strip(): line for line in lines[4:]}
    assert tuple(rows) == LABELS
    assert "누락·불일치 0건" in rows["옛 실사 표시"]
    assert "ko 0장" not in rows["옛 실사 표시"]
    assert "ko 0상자" not in rows["묶음 카드"]


def test_preview_warns_on_mutations(monkeypatch):
    doc = preview.read_brief(preview.DATA / "briefs" / ISSUE)
    scaffold = preview.read_brief(preview.DATA / "scaffolds" / ISSUE)
    card = next(card for card in doc["cards"] if card["agency"] == "FDA")
    source = next(source for source in scaffold["cards"] if source["id"] == card["id"])
    source["quotes"] = [{"original": "Dear Mr. Testerson,"}]
    card["en"]["summary"] = "Testerson 한글"
    card["summary"] = ""

    annotate = preview.render._annotate_firm_groups

    def omit_display(views, cards, tr):
        annotate(views, cards, tr)
        for view in views:
            view.pop("old_insp", None)
            view.pop("fgroup_key", None)

    monkeypatch.setattr(preview.render, "_annotate_firm_groups", omit_display)
    rows = {label: (warned, details) for label, warned, details in preview.check_preview(doc, scaffold)}
    for label in LABELS:
        assert rows[label][0], label
    assert f"{card['id']}.en.summary" in rows["개인 실명"][1]
    assert "Testerson" not in rows["개인 실명"][1]
    assert "en.summary" in rows["영문 한글"][1]
    assert f"{card['id']}.summary" in rows["빈 칸"][1]
    assert "예상 그룹 상자와 불일치" in rows["묶음 카드"][1]
