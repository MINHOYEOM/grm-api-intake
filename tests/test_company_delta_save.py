"""델타 산출물 저장 CLI는 현재 점유자에게만 최종 파일을 쓸 수 있게 한다."""

import io
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from company.editorial import shard_claim


class DeltaSaveTest(unittest.TestCase):
    def setUp(self):
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.source = self.folder / "draft.json"
        self.data = ('{\n  "status": "완료", "body": "' + "원문" * 5000 + '"\n}\n').encode("utf-8")
        self.source.write_bytes(self.data)
        self.now = 1000
        self.enterContext(mock.patch.object(shard_claim.time, "time", side_effect=lambda: self.now))
        self.enterContext(mock.patch.object(shard_claim, "_repo_root", return_value=self.folder))
        self.git = self.enterContext(mock.patch.object(shard_claim, "_git", side_effect=self._fake_git))

    def _fake_git(self, root, *args, check=True):
        if args[0] == "show":
            path = root / args[1].removeprefix("FETCH_HEAD:")
            return subprocess.CompletedProcess(args, 0, stdout=path.read_text(encoding="utf-8"))
        return subprocess.CompletedProcess(args, 0, stdout="test-branch\n")

    def _save(self, shard, worker, *, apply=True):
        argv = ["shard_claim.py", "save-delta", str(self.folder), shard, worker, str(self.source)]
        if apply:
            argv.append("--apply")
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as err:
            code = shard_claim.main(argv)
        return code, err.getvalue()

    def test_save_without_claim_is_refused(self):
        code, err = self._save("A", "델타-1")
        self.assertEqual(code, 1)
        self.assertIn("유효한 점유가 없습니다", err)
        self.assertFalse((self.folder / "delta.A.json").exists())
        self.git.assert_not_called()

    def test_save_after_claim_preserves_full_bytes_and_defaults_to_dry_run(self):
        self.assertTrue(shard_claim.claim(self.folder, "delta", "T", "델타-1"))
        self.git.reset_mock()
        result = self.folder / "delta.T.json"
        self.assertEqual(self._save("T", "델타-2")[0], 1)
        self.assertEqual(self._save("T", "델타-1", apply=False)[0], 0)
        self.assertFalse(result.exists())
        self.assertEqual(self._save("T", "델타-1")[0], 0)
        self.assertEqual(result.read_bytes(), self.data)
        self.git.assert_not_called()

    def test_save_after_taking_over_a_claim_with_no_result_for_25_minutes(self):
        self.assertTrue(shard_claim.claim(self.folder, "delta", "A", "델타-1"))
        result = self.folder / "delta.A.json"
        self.assertFalse(result.exists())
        self.now += shard_claim.STALE_MIN * 60
        self.assertFalse(shard_claim.claim(self.folder, "delta", "A", "델타-1+"))
        self.now += 1
        self.assertEqual(self._save("A", "델타-1")[0], 1)
        self.assertEqual(self._save("A", "델타-1+")[0], 1)
        self.assertTrue(shard_claim.claim(self.folder, "delta", "A", "델타-1+"))
        self.assertEqual(self._save("A", "델타-1")[0], 1)
        self.assertEqual(self._save("A", "델타-1+")[0], 0)
        self.assertEqual(result.read_bytes(), self.data)
