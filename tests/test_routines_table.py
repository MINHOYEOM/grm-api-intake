"""Check that the routine tables agree with their local instructions."""

import re
import unittest
from collections import Counter
from pathlib import Path


COMPANY = Path(__file__).resolve().parents[1] / "company"
ROUTINES = COMPANY / "ROUTINES.md"
EDITORIAL_RUN = COMPANY / "editorial" / "RUN.md"
TIME = r"\b\d{2}:\d{2}\b"


def table_rows(markdown, first_column):
    """Read rows from every table with this first column, including pasted copies."""
    columns = []
    for line in markdown.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            columns = []
            continue

        cells = [cell.strip() for cell in re.split(r"(?<!\\)\|", line[1:].removesuffix("|"))]
        if cells[0] == first_column:
            columns = cells
        elif columns and cells[0].strip(":-"):
            yield dict(zip(columns, cells, strict=True))


class RoutinesTableTest(unittest.TestCase):
    def setUp(self):
        markdown = ROUTINES.read_text(encoding="utf-8")
        self.details = list(table_rows(markdown, "팀"))
        self.summary = list(table_rows(markdown, "Routine"))

    def test_instruction_paths_exist_under_company(self):
        self.assertTrue(self.details, "ROUTINES.md is missing the detailed table")
        for row in self.details:
            with self.subTest(routine=row["일"]):
                cell = row["지시문"]
                match = re.fullmatch(r"`([^`]+)`", cell)
                self.assertIsNotNone(match, f"Expected an instruction path in {cell!r}")
                target = (COMPANY / match[1]).resolve()
                self.assertIn(COMPANY, target.parents, f"Instruction escapes company/: {cell}")
                self.assertTrue(target.is_file() or target.is_dir(), f"Missing instruction: {cell}")

    def test_routines_are_unique_in_each_table(self):
        self.assertTrue(self.details, "ROUTINES.md is missing the detailed table")
        self.assertTrue(self.summary, "ROUTINES.md is missing the Routine summary table")
        for names in (
            ((row["팀"], row["일"]) for row in self.details),
            (row["Routine"] for row in self.summary),
        ):
            duplicates = [name for name, count in Counter(names).items() if count > 1]
            self.assertEqual(duplicates, [], "Duplicate routines in ROUTINES.md")

    def test_editorial_schedule_matches_run(self):
        run = EDITORIAL_RUN.read_text(encoding="utf-8")
        # Multiple workers can share a slot. Use the start time in each schedule row.
        main_times = sorted({row["시각"].split()[0] for row in table_rows(run, "시각")})
        self.assertTrue(main_times, "RUN.md is missing the editorial schedule")
        backup = re.search(
            rf"\*\*백업 v17\*\*\(`company/editorial/backup\.md`, 월 ({TIME})", run
        )
        self.assertIsNotNone(backup, "RUN.md is missing the v17 backup slot")

        for rows, name_column, time_column, schedules in (
            (self.details, "지시문", "클라우드", {
                "`editorial/`": main_times,
                "`editorial/backup.md`": [backup[1]],
            }),
            (self.summary, "Routine", "언제", {
                "브리프편집팀 (데스크·기자·교열)": main_times,
                "브리프편집팀 백업 v17": [backup[1]],
            }),
        ):
            for name, expected in schedules.items():
                with self.subTest(column=time_column, routine=name):
                    slots = [row[time_column] for row in rows if row[name_column] == name]
                    self.assertEqual(len(slots), 1, f"Expected one row for {name}")
                    scheduled_times = re.sub(r"\([^)]*\)", "", slots[0])
                    self.assertEqual(
                        re.findall(TIME, scheduled_times), expected, "ROUTINES.md differs from RUN.md"
                    )
