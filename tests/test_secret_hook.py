import importlib.util
import unittest
from pathlib import Path

PATH = Path(__file__).resolve().parent.parent / ".claude" / "hooks" / "check_secrets.py"
spec = importlib.util.spec_from_file_location("check_secrets", PATH)
hook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hook)


class SecretHookTest(unittest.TestCase):
    def test_blocked_files(self):
        names = [".env", ".env.local", "data/koe.sqlite3", "voice.wav", "photos/1.jpg"]
        found = hook.problems(names, [], [])
        for name in names:
            self.assertTrue(any(f.startswith(name + "：") for f in found), name)

    def test_allowed_files(self):
        names = [".env.example", "app/inbox.py", "app/static/display.html", "tests/test_watch.py"]
        self.assertEqual(hook.problems(names, [], []), [])

    def test_secret_contents(self):
        lines = [
            "ANTHROPIC_API_KEY=sk-ant-api03-" + "x" * 30,
            "LINE_GROUP_ID=C" + "0123456789abcdef" * 2,
            "token = '" + "A1b2" * 43 + "='",
        ]
        self.assertEqual(len(hook.problems([], lines, [])), 3)

    def test_env_value_itself(self):
        self.assertTrue(hook.problems([], ["secret = 'my-own-secret-123'"], ["my-own-secret-123"]))

    def test_normal_code_passes(self):
        lines = ['PUSH_URL = "https://api.line.me/v2/bot/message/push"', "def flush(db, dry_run=None):"]
        self.assertEqual(hook.problems([], lines, ["value-not-present"]), [])

    def test_only_git_commit_and_push_are_checked(self):
        self.assertIsNone(hook.re.search(r"\bgit\b.*\b(commit|push)\b", "git status"))
        self.assertIsNotNone(hook.re.search(r"\bgit\b.*\b(commit|push)\b", 'git commit -m "x"'))
