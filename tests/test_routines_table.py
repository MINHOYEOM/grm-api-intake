"""Check that the routine tables agree with their local instructions."""

import re
import unittest
from collections import Counter
from pathlib import Path


COMPANY = Path(__file__).resolve().parents[1] / "company"
ROUTINES = COMPANY / "ROUTINES.md"
EDITORIAL_RUN = COMPANY / "editorial" / "RUN.md"
TIME = r"\b\d{2}:\d{2}\b"
SUMMARY_NAMES = {
    ("브리프편집팀", "월요일 브리프"): "브리프편집팀 (데스크·기자·교열)",
    ("브리프편집팀", "월요일 브리프 백업 v17"): "브리프편집팀 백업 v17",
    ("성장분석팀", "매일 성장 일보 v2 (방문→검색→구독→메일 클릭)"): "성장분석팀 — 일일 성장 일보",
    ("검증실", "월간 검증(사실 일치·개인정보·저작권)"): "검증실 — 월간 검증",
}


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


def schedule_times(cell):
    return re.findall(TIME, re.sub(r"\([^)]*\)", "", cell))


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

    def test_schedules_match_tables_and_instructions(self):
        details = {
            SUMMARY_NAMES.get((row["팀"], row["일"]), f"{row['팀']} — {row['일']}"): row
            for row in self.details
        }
        summary = {row["Routine"]: row for row in self.summary}
        self.assertEqual(len(details), len(self.details), "Multiple detail rows map to the same summary name")
        self.assertEqual(details.keys(), summary.keys(), "ROUTINES.md tables list different routines")
        for name, detail in details.items():
            with self.subTest(routine=name):
                path = COMPANY / detail["지시문"].strip("`")
                instruction = path.read_text(encoding="utf-8") if path.is_file() else ""
                self.assert_schedule_matches(detail, summary[name], instruction)

    def assert_schedule_matches(self, detail, summary, instruction):
        cloud = schedule_times(detail["클라우드"])
        self.assertTrue(cloud, "Missing cloud schedule in detailed table")
        self.assertEqual(cloud, schedule_times(summary["언제"]), "Cloud schedule differs between tables")

        pc_backup = schedule_times(detail["PC 백업"])
        summary_backup = re.search(r"PC 백업\(([^)]*)\)", summary["안 돌면"])
        self.assertEqual(
            pc_backup, re.findall(TIME, summary_backup[1]) if summary_backup else [],
            "PC backup schedule differs between tables",
        )

        heading = instruction.partition("\n")[0]
        instruction_times = schedule_times(heading)
        earliest = re.search(rf"({TIME}) KST 이후", heading)
        if earliest:
            self.assertGreaterEqual(min(cloud), earliest[1], "Cloud schedule precedes instruction limit")
        elif instruction_times:
            self.assertEqual(cloud, instruction_times, "Cloud schedule differs from instruction")

        instruction_backup = re.search(r"PC (?:백업|예약작업 `[^`]+`)\(([^)]*)\)", instruction)
        if instruction_backup:
            self.assertEqual(
                pc_backup, re.findall(TIME, instruction_backup[1]),
                "PC backup schedule differs from instruction",
            )

    def test_editorial_schedule_matches_run(self):
        run = EDITORIAL_RUN.read_text(encoding="utf-8")
        # Multiple workers can share a slot. Use the start time in each schedule row.
        main_times = sorted({row["시각"].split()[0] for row in table_rows(run, "시각")})
        self.assertTrue(main_times, "RUN.md is missing the editorial schedule")
        backup = re.search(
            rf"\*\*백업 v17\*\*\(`company/editorial/backup\.md`, 월 ({TIME})", run
        )
        self.assertIsNotNone(backup, "RUN.md is missing the v17 backup slot")

        for path, expected in {"`editorial/`": main_times, "`editorial/backup.md`": [backup[1]]}.items():
            with self.subTest(instruction=path):
                slots = [row["클라우드"] for row in self.details if row["지시문"] == path]
                self.assertEqual(len(slots), 1, f"Expected one row for {path}")
                self.assertEqual(schedule_times(slots[0]), expected, "ROUTINES.md differs from RUN.md")

    def test_non_editorial_schedule_rejects_mutations(self):
        detail = {"클라우드": "매일 04:00", "PC 백업": "매일 09:10"}
        summary = {"언제": "매일 04:00", "안 돌면": "PC 백업(매일 09:10)"}
        instruction = "# 지적사항 일일 번역 — 매일 04:00 KST\nPC 예약작업 `translate`(매일 09:10)"
        self.assert_schedule_matches(detail, summary, instruction)
        for changed_summary, changed_instruction, message in (
            ({**summary, "언제": "매일 05:00"}, instruction, "Cloud schedule differs between tables"),
            ({**summary, "안 돌면": "PC 백업(매일 09:20)"}, instruction, "PC backup schedule differs between tables"),
            (summary, instruction.replace("04:00", "05:00"), "Cloud schedule differs from instruction"),
            (summary, instruction.replace("09:10", "09:20"), "PC backup schedule differs from instruction"),
        ):
            with self.subTest(message=message), self.assertRaisesRegex(AssertionError, message):
                self.assert_schedule_matches(detail, changed_summary, changed_instruction)

    def test_schedule_rejects_time_before_instruction_limit(self):
        instruction = "# 월간 검증 — 매월 첫 월요일 13:00 KST 이후"
        for time in ("13:00", "13:30"):
            self.assert_schedule_matches(
                {"클라우드": time, "PC 백업": "없음"}, {"언제": time, "안 돌면": "백업 없음"}, instruction
            )
        with self.assertRaisesRegex(AssertionError, "Cloud schedule precedes instruction limit"):
            self.assert_schedule_matches(
                {"클라우드": "12:59", "PC 백업": "없음"}, {"언제": "12:59", "안 돌면": "백업 없음"}, instruction
            )
