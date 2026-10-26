"""CI 전용: 격리 PostgreSQL에서 정책 위반을 주입해 FAIL → 원복 PASS를 확인한다."""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "web/migrations/094_mfds_inspector_profiles.sql"
COMMAND = ["sudo", "-u", "postgres", "psql", "-X", "-v", "ON_ERROR_STOP=1",
           "-d", "mfds_attendees_test", "-f", "tests/sql/mfds_attendees_ingest.sql"]


def run():
    return subprocess.run(COMMAND, cwd=ROOT, capture_output=True, text=True, encoding="utf-8")


def main():
    original = MIGRATION.read_text(encoding="utf-8")
    mutations = [
        ("index count injection", "'display_name',inspector_key) order by", "'display_name',inspector_key,'documents',99) order by"),
        ("four-document cohort", "having count(distinct a.document_id) >= 5", "having count(distinct a.document_id) >= 4"),
        ("index volume ordering", 'order by inspector_key collate "C"',
         'order by (select count(distinct a.document_id) from public.mfds_inspection_attendees a where a.inspector_name_norm=cohort.inspector_key) desc, inspector_key collate "C"'),
        ("department volume ordering", 'order by u.agency collate "C",u.dept collate "C"',
         'order by u.documents desc,u.agency collate "C",u.dept collate "C"'),
        ("FDA response change", "do $$\nbegin\n  if not exists (select 1 from pg_temp.mfds_fda_rpc_guard_before b",
         "create or replace function public.findings_inspector_profile(text) returns jsonb language sql as $x$ select '{\"changed\":true}'::jsonb $x$;\ndo $$\nbegin\n  if not exists (select 1 from pg_temp.mfds_fda_rpc_guard_before b"),
    ]
    try:
        baseline = run()
        if baseline.returncode:
            raise RuntimeError("Unmodified SQL failed:\n" + baseline.stderr)
        for label, old, new in mutations:
            if old not in original:
                raise RuntimeError("Mutation anchor missing: " + label)
            MIGRATION.write_text(original.replace(old, new, 1), encoding="utf-8", newline="\n")
            result = run()
            if not result.returncode or "assertion failed:" not in result.stderr and "FDA inspector RPC response/definition md5 changed" not in result.stderr:
                raise RuntimeError("Mutation did not fail a policy guard: " + label + "\n" + result.stderr)
            print(label + ": FAIL as expected", flush=True)
    finally:
        MIGRATION.write_text(original, encoding="utf-8", newline="\n")
    restored = run()
    if restored.returncode:
        raise RuntimeError("Restored SQL failed:\n" + restored.stderr)
    print("Restored SQL: PASS", flush=True)


if __name__ == "__main__":
    main()
