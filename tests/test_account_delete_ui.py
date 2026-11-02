#!/usr/bin/env python3
"""Account-delete UI (reactions.js /me modal) -- offline text-contract tests."""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_JS = (_ROOT / "web" / "assets" / "reactions.js").read_text(encoding="utf-8")
_EN = json.loads((_ROOT / "web" / "data" / "i18n" / "en.json").read_text(encoding="utf-8"))


def _block() -> str:
    i = _JS.index("function openDeleteAccount")
    return _JS[i:_JS.index("renderMeGuestHead", i)]


class AccountDeleteUiTest(unittest.TestCase):
    def test_calls_account_delete_with_own_token(self) -> None:
        b = _block()
        self.assertIn("/functions/v1/account-delete", b)
        self.assertIn("access_token", b)
        self.assertIn("signInWithPassword", b)

    def test_guards_before_submit(self) -> None:
        b = _block()
        for tok in ("grm-del-ok", "grm-del-email", "disabled", "toLowerCase"):
            self.assertIn(tok, b)

    def test_no_backspace_byte_and_button_wired(self) -> None:
        self.assertNotIn("\x08", _JS)
        self.assertIn('grm-me-del', _JS)

    def test_every_new_string_has_english(self) -> None:
        for m in re.finditer(r'_t\("([^"]+)"\)', _block()):
            self.assertIn(m.group(1), _EN, m.group(1))


if __name__ == "__main__":
    unittest.main()
