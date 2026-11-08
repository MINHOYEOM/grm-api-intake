#!/usr/bin/env python3
"""FDA 수입경보 PC 중계 경로 — 표적 시험(네트워크·DB·gh 0).

- collect_fda_import_alert: manifest 계약(load_relay_dir), 멱등·역행 가드, main --html-dir
- fda_import_alert_relay: 검증 실패 시 업로드 0, manifest 마지막 업로드, 같은 날 재실행 = 업로드 생략
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import collect_fda_import_alert as ia  # noqa: E402
import fda_import_alert_relay as relay  # noqa: E402
from tests.test_fda_import_alert import _FIX, _FakeHttp, _big_page  # noqa: E402

_RUN = "2026-11-08"
_ONLY_6640 = {"66-40": dict(ia.ALERTS["66-40"])}


def _big_bytes() -> bytes:
    return _big_page(300).encode("utf-8")


def _write_relay(d: Path, data: bytes, *, run_date: str = _RUN, alert: str = "66-40") -> dict:
    manifest = ia.build_relay_manifest(run_date, "2026-11-08T07:20:00+09:00",
                                       {alert: (data, {"last_modified": "LM", "url": "U"})})
    (d / ia.relay_file_name(alert)).write_bytes(gzip.compress(data, mtime=0))
    (d / ia.RELAY_MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")
    return manifest


class LoadRelayDirTest(unittest.TestCase):
    def test_round_trip_returns_original_bytes(self):
        data = _FIX.read_bytes()
        with tempfile.TemporaryDirectory() as t:
            _write_relay(Path(t), data)
            manifest, fetch = ia.load_relay_dir(Path(t))
            got, meta = fetch("66-40")
        self.assertEqual(manifest["run_date"], _RUN)
        self.assertEqual(got, data)
        self.assertEqual(meta["last_modified"], "LM")

    def test_tampered_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            _write_relay(d, _FIX.read_bytes())
            (d / "66-40.html.gz").write_bytes(gzip.compress(_FIX.read_bytes()[:-10], mtime=0))
            _, fetch = ia.load_relay_dir(d)
            with self.assertRaises(ia.RelayError):
                fetch("66-40")

    def test_missing_alert_and_bad_schema(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            m = _write_relay(d, _FIX.read_bytes())
            _, fetch = ia.load_relay_dir(d)
            with self.assertRaises(ia.RelayError):
                fetch("66-41")
            m["schema"] = "other/v0"
            (d / ia.RELAY_MANIFEST).write_text(json.dumps(m), encoding="utf-8")
            with self.assertRaises(ia.RelayError):
                ia.load_relay_dir(d)


class IdempotencyGuardTest(unittest.TestCase):
    def _run(self, latest_rows):
        http = _FakeHttp(snapshot_rows=latest_rows, listing_rows=[])
        store = ia.Store("https://example.invalid", "k", http=http)
        rep = ia.run_alert("66-40", run_date=_RUN, fetch=lambda a: (_big_bytes(), {}),
                           store=store, write=True, raw_dir=None)
        return rep, http

    def test_same_day_same_sha_is_skipped(self):
        sha = hashlib.sha256(_big_bytes()).hexdigest()
        rep, http = self._run([{"run_date": _RUN, "sha256": sha}])
        self.assertEqual(rep.get("skipped"), "already_applied")
        self.assertEqual(rep["failures"], [])
        self.assertEqual(http.posts, [])

    def test_older_than_latest_is_skipped(self):
        rep, http = self._run([{"run_date": "2026-11-09", "sha256": "x"}])
        self.assertTrue(rep.get("skipped", "").startswith("older_than_latest"))
        self.assertEqual(http.posts, [])

    def test_same_day_new_content_is_applied(self):
        rep, http = self._run([{"run_date": _RUN, "sha256": "different"}])
        self.assertTrue(rep.get("written"))
        self.assertEqual(len(http.posts), 1)


class MainHtmlDirTest(unittest.TestCase):
    def test_run_date_comes_from_manifest(self):
        with tempfile.TemporaryDirectory() as t, mock.patch.object(ia, "fetch_page") as net:
            d = Path(t)
            _write_relay(d, _big_bytes(), run_date="2026-11-07")
            out = d / "r.json"
            code = ia.main(["--html-dir", str(d), "--no-db", "--alerts", "66-40", "--output", str(out)])
            rep = json.loads(out.read_text(encoding="utf-8"))
        net.assert_not_called()                          # 원천에는 접속하지 않는다
        self.assertEqual(code, 0)
        self.assertEqual(rep["run_date"], "2026-11-07")
        self.assertEqual(rep["source"], "relay")

    def test_conflicting_run_date_refused(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            _write_relay(d, _big_bytes(), run_date="2026-11-07")
            code = ia.main(["--html-dir", str(d), "--no-db", "--run-date", "2026-11-08",
                            "--output", str(d / "r.json")])
        self.assertEqual(code, 2)

    def test_missing_manifest_is_fetch_failure(self):
        with tempfile.TemporaryDirectory() as t:
            out = Path(t) / "r.json"
            code = ia.main(["--html-dir", t, "--no-db", "--output", str(out)])
            rep = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(code, 2)
        self.assertEqual(rep["failure_kind"], "fetch")


class _FakeGh:
    """gh 호출을 기록한다. existing = 이미 있는 경로 집합."""

    def __init__(self, existing=()):
        self.existing = set(existing)
        self.calls: list[list[str]] = []
        self.puts: list[tuple[str, dict]] = []

    def __call__(self, args, stdin):
        self.calls.append(args)
        if args[:1] == ["api"] and "-X" not in args:
            path = args[1].split("/contents/", 1)[1]
            return (0, b"blobsha\n", b"") if path in self.existing else (1, b"", b"gh: Not Found (HTTP 404)")
        if args[:3] == ["api", "-X", "PUT"]:
            path = args[3].split("/contents/", 1)[1]
            self.puts.append((path, json.loads(stdin)))
            self.existing.add(path)
            return 0, b"{}", b""
        if args[:2] == ["workflow", "run"]:
            return 0, b"", b""
        return 1, b"", b"unexpected"


@mock.patch.dict(ia.ALERTS, _ONLY_6640, clear=True)
class RelayScriptTest(unittest.TestCase):
    def _relay(self, fake, fetch=None):
        return relay.relay(_RUN, gh=relay.Gh(fake), fetch=fetch or (lambda a: (_big_bytes(), {"url": "U"})))

    def test_upload_order_manifest_last_then_dispatch(self):
        fake = _FakeGh()
        self.assertEqual(self._relay(fake), 0)
        paths = [p for p, _ in fake.puts]
        self.assertEqual(paths, [f"import_alert_raw/{_RUN}/66-40.html.gz",
                                 f"import_alert_raw/{_RUN}/manifest.json"])
        import base64
        gz = base64.b64decode(fake.puts[0][1]["content"])
        self.assertEqual(gzip.decompress(gz), _big_bytes())
        self.assertEqual(gz, gzip.compress(_big_bytes(), mtime=0))      # 결정적 바이트
        manifest = json.loads(base64.b64decode(fake.puts[1][1]["content"]))
        self.assertEqual(manifest["schema"], ia.RELAY_SCHEMA)
        self.assertEqual(manifest["files"]["66-40"]["sha256"], hashlib.sha256(_big_bytes()).hexdigest())
        run = fake.calls[-1]
        self.assertEqual(run[:3], ["workflow", "run", relay.WORKFLOW])
        self.assertIn(f"ops_date={_RUN}", run)
        self.assertIn("source=ops", run)

    def test_second_run_same_day_skips_upload_but_dispatches(self):
        fake = _FakeGh(existing={f"import_alert_raw/{_RUN}/manifest.json"})
        self.assertEqual(self._relay(fake), 0)
        self.assertEqual(fake.puts, [])
        self.assertEqual(fake.calls[-1][:2], ["workflow", "run"])

    def test_structure_failure_uploads_nothing(self):
        fake = _FakeGh()
        code = self._relay(fake, fetch=lambda a: (_FIX.read_bytes(), {}))   # 하한 미달 조각
        self.assertEqual(code, 2)
        self.assertEqual(fake.calls, [])

    def test_blocked_source_uploads_nothing(self):
        def blocked(a):
            raise RuntimeError("HTTP 404 for x")
        fake = _FakeGh()
        self.assertEqual(self._relay(fake, fetch=blocked), 2)
        self.assertEqual(fake.calls, [])

    def test_existing_partial_file_is_overwritten_with_sha(self):
        fake = _FakeGh(existing={f"import_alert_raw/{_RUN}/66-40.html.gz"})
        self.assertEqual(self._relay(fake), 0)
        self.assertEqual(fake.puts[0][1].get("sha"), "blobsha")


if __name__ == "__main__":
    unittest.main()
