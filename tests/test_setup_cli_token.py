"""supabase/setup-cli 는 github-token 을 넘겨야 한다.

11-24 Admin 서버 함수 배포(grm-admin-backend-deploy)가 'Failed to resolve latest Supabase CLI
release: rate limit exceeded' 로 두 번 연속 실패했다. 토큰 없이 최신 버전을 조회하면 러너 공용 IP 의
미인증 GitHub API 한도에 걸린다. 모든 사용처가 인증 토큰을 넘기는지 매 CI 에서 확인한다.
"""
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
USES = re.compile(r"^(?P<ind>[ \t]*)- uses: supabase/setup-cli@", re.M)


def setup_cli_steps(text: str):
    """setup-cli 스텝마다 그 스텝 본문(다음 '- ' 스텝 전까지)을 돌려준다."""
    for m in USES.finditer(text):
        ind = m.group("ind")
        rest = text[m.end():]
        nxt = re.search(rf"^{re.escape(ind)}- ", rest, re.M)
        yield rest[: nxt.start()] if nxt else rest


class SetupCliTokenTest(unittest.TestCase):
    def test_every_setup_cli_passes_github_token(self):
        found = 0
        for wf in sorted(WORKFLOWS.glob("*.y*ml")):
            for body in setup_cli_steps(wf.read_text(encoding="utf-8")):
                found += 1
                with self.subTest(workflow=wf.name):
                    self.assertRegex(
                        body,
                        r"github-token:\s*\$\{\{\s*(github\.token|secrets\.GITHUB_TOKEN)\s*\}\}",
                        "supabase/setup-cli 에 with: github-token: ${{ github.token }} 가 필요하다",
                    )
        self.assertGreater(found, 0, "setup-cli 사용처를 하나도 못 찾았다 — 정규식이 낡았다")

    def test_detector_catches_missing_token(self):
        bad = "      - uses: supabase/setup-cli@abc # v2\n\n      - name: next\n"
        good = bad.replace("# v2\n", "# v2\n        with:\n          github-token: ${{ github.token }}\n")
        self.assertNotRegex(next(setup_cli_steps(bad)), r"github-token")
        self.assertRegex(next(setup_cli_steps(good)), r"github-token")


if __name__ == "__main__":
    unittest.main()
