#!/usr/bin/env python3
"""[원문 갱신 규칙 R1 2026-11-12] 정본 원문(raw_signals)을 함수 밖에서 덮어쓰는 코드를 막는다.

원문을 바꾸는 길은 DB 함수 `raw_signal_apply_version`(148) 하나여야 한다 — 지문 검증·옛 판 보관이
거기서만 일어난다. raw_json 만 PATCH 하던 도구 때문에 11-12 실측으로 483 2,217행·MFDS 168행의 지문이
내용과 어긋났고 옛 판은 남지 않았다.

이 가드는 저장소 루트 .py 에서 raw_signals 에 대한
  ① PATCH(`requests.patch` 근처에 `/rest/v1/raw_signals`)
  ② 제자리 교체 upsert(`"raw_signals"` 근처에 `merge-duplicates`)
를 센다. 지금 있는 곳은 아래 DEBT 로 시작한다 — 도구를 함수 경유로 옮길 때마다(R3) 숫자를 줄인다.
숫자가 늘거나 새 파일이 생기면 실패한다. 줄었는데 DEBT 를 안 줄여도 실패한다(목록이 낡지 않게).
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_WINDOW = 12   # 앞뒤 줄 수

# 파일 → (PATCH 수, merge-duplicates 수). R3(2026-11-12)에서 4개 도구(483 상세·483 OCR·식약처 평가·
# WL 본문)를 raw_signal_apply_version 경유로 옮겨 0 이 됐다. 이제 이 목록은 비어 있어야 한다 —
# DB 쪽도 migration 150 트리거가 함수 밖 원문 변경을 거부한다.
DEBT: dict[str, tuple[int, int]] = {}

# 원문이 아닌 **파생 칸 하나**만 PATCH 하는 도구(원문·지문·row_json 은 건드리지 않는다). 옛 판 보관
# 대상이 아니므로 함수를 거치지 않는다 — 대신 PATCH 본문에 원문 칸이 없는지를 아래 시험이 확인한다.
# 파일 → (PATCH 수, 허용 칸 이름).
DERIVED_COLUMN_WRITERS: dict[str, tuple[int, str]] = {
    "backfill_raw_signals_modality.py": (1, "modality"),   # 제품군 소급(2026-11-12)
}
_RAW_COLUMNS = re.compile(r"[\"'](raw_json|row_json|raw_sha256|record_json)[\"']\s*:")

_RAW_URL = re.compile(r"/rest/v1/raw_signals")
_RAW_TABLE = re.compile(r"[\"']raw_signals[\"']")
_PATCH = re.compile(r"requests\.patch\(")
_MERGE_CALL = re.compile(r"resolution\s*=\s*[\"']merge-duplicates[\"']")


def _near(lines: list[str], i: int, pat: re.Pattern[str]) -> bool:
    lo, hi = max(0, i - _WINDOW), min(len(lines), i + _WINDOW + 1)
    return any(pat.search(x) for x in lines[lo:hi])


def scan(text: str) -> tuple[int, int]:
    lines = text.splitlines()
    patches = sum(1 for i, x in enumerate(lines) if _PATCH.search(x) and _near(lines, i, _RAW_URL))
    merges = sum(1 for i, x in enumerate(lines)
                 if _MERGE_CALL.search(x) and _near(lines, i, _RAW_TABLE))
    return patches, merges


class RawSignalsWriteGuardTest(unittest.TestCase):

    def test_no_new_raw_signals_overwrite_paths(self):
        found = {}
        for path in sorted(ROOT.glob("*.py")):
            got = scan(path.read_text(encoding="utf-8"))
            if path.name in DERIVED_COLUMN_WRITERS and got == (DERIVED_COLUMN_WRITERS[path.name][0], 0):
                continue   # 파생 칸 전용 — 본문 검사는 test_derived_column_writers_touch_no_raw_columns
            if got != (0, 0):
                found[path.name] = got
        self.assertEqual(
            found, DEBT,
            "raw_signals 를 raw_signal_apply_version(148) 밖에서 덮어쓰는 코드가 늘었거나 줄었다. "
            "새 코드는 함수를 쓴다. 도구를 옮겼으면 DEBT 숫자를 줄인다.")

    def test_derived_column_writers_touch_no_raw_columns(self):
        """파생 칸 전용 도구의 PATCH 본문에는 허용 칸만 있고 원문 칸(raw_json 등)은 없다."""
        for name, (count, column) in DERIVED_COLUMN_WRITERS.items():
            lines = (ROOT / name).read_text(encoding="utf-8").splitlines()
            hits = [i for i, x in enumerate(lines) if _PATCH.search(x) and _near(lines, i, _RAW_URL)]
            self.assertEqual(len(hits), count, name)
            for i in hits:
                window = "\n".join(lines[max(0, i - _WINDOW): i + _WINDOW + 1])
                self.assertIsNone(_RAW_COLUMNS.search(window), f"{name}: PATCH 근처에 원문 칸")
                self.assertRegex(window, rf"json=\{{\s*[\"']{column}[\"']\s*:", f"{name}: 허용 칸 없음")

    def test_scanner_catches_patch_and_merge(self):
        bad = ('url = f"{base}/rest/v1/raw_signals"\n'
               'resp = requests.patch(url, json={"raw_json": "{}"})\n'
               '_post_rows(base, key, "raw_signals", rows,\n'
               '           resolution="merge-duplicates")\n')
        self.assertEqual(scan(bad), (1, 1))

    def test_scanner_ignores_other_tables_and_ignore_duplicates(self):
        ok = ('url = f"{base}/rest/v1/findings"\n'
              'resp = requests.patch(url, json={})\n'
              '_post_rows(base, key, "raw_signals", rows, resolution="ignore-duplicates")\n')
        self.assertEqual(scan(ok), (0, 0))


if __name__ == "__main__":
    unittest.main()
