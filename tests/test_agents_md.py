from pathlib import Path

import pytest


AGENTS_MD = Path(__file__).resolve().parents[1] / "AGENTS.md"


def test_agents_md_exists():
    assert AGENTS_MD.is_file()


@pytest.mark.parametrize(
    "required_rule",
    ("통째로 읽지 말 것", "머지 금지", "`git add -A` 금지"),
)
def test_agents_md_contains_required_rule(required_rule):
    assert required_rule in AGENTS_MD.read_text(encoding="utf-8")
