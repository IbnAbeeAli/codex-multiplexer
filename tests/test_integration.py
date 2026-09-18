import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import codex_mux


REPO = Path(__file__).resolve().parents[1]


FAKE_CODEX = r'''#!/usr/bin/env python3
import json
import os
import sys

args = sys.argv[1:]
if "app-server" in args:
    for line in sys.stdin:
        message = json.loads(line)
        method = message.get("method")
        message_id = message.get("id")
        if method == "initialize":
            result = {"codexHome": os.environ["CODEX_HOME"]}
            print(json.dumps({"id": message_id, "result": result}), flush=True)
        elif method == "account/read":
            account = {
                "type": "chatgpt",
                "email": "fake@example.com",
                "planType": "pro",
            }
            result = {"account": account, "requiresOpenaiAuth": True}
            print(json.dumps({"id": message_id, "result": result}), flush=True)
        elif method == "account/rateLimits/read":
            primary = {
                "usedPercent": 40,
                "windowDurationMins": 300,
                "resetsAt": None,
            }
            result = {
                "rateLimits": {
                    "limitId": "codex",
                    "primary": primary,
                    "secondary": None,
                },
                "rateLimitsByLimitId": None,
                "rateLimitResetCredits": {"availableCount": 3, "credits": None},
            }
            print(json.dumps({"id": message_id, "result": result}), flush=True)
        elif method == "account/usage/read":
            result = {
                "summary": {"lifetimeTokens": 123},
                "dailyUsageBuckets": [],
            }
            print(json.dumps({"id": message_id, "result": result}), flush=True)
        elif method == "thread/list":
            result = {
                "data": [{"id": "thread-1"}],
                "nextCursor": None,
                "backwardsCursor": None,
            }
            print(json.dumps({"id": message_id, "result": result}), flush=True)
elif "--version" in args:
    print("codex-cli fake")
else:
    result = {
        "args": args,
        "codexHome": os.environ.get("CODEX_HOME"),
        "sqliteHome": os.environ.get("CODEX_SQLITE_HOME"),
    }
    print(json.dumps(result))
'''


class CliIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.root = self.base / "data"
        self.fake_codex = self.base / "codex"
        self.fake_codex.write_text(FAKE_CODEX, encoding="utf-8")
        self.fake_codex.chmod(self.fake_codex.stat().st_mode | stat.S_IXUSR)
        registry = codex_mux.default_registry()
        registry["defaultAccount"] = "acc1"
        registry["accounts"] = [
            {
                "name": "acc1",
                "codexHome": "accounts/acc1",
                "aliases": [],
                "expectedEmail": "fake@example.com",
            }
        ]
        codex_mux.save_registry(registry, self.root)
        codex_mux.ensure_layout(registry, root=self.root)
        auth = self.root / "accounts" / "acc1" / "auth.json"
        auth.write_text("{}\n", encoding="utf-8")
        auth.chmod(0o600)
        self.env = os.environ.copy()
        self.env.update(
            {
                "CODEX_MULTIPLEXER_HOME": str(self.root),
                "CODEX_MULTIPLEXER_CODEX_BIN": str(self.fake_codex),
                "PYTHONDONTWRITEBYTECODE": "1",
            }
        )

    def tearDown(self):
        self.temporary.cleanup()

    def run_mux(self, *args):
        return subprocess.run(
            [sys.executable, str(REPO / "codex_mux.py"), *args],
            env=self.env,
            text=True,
            capture_output=True,
            check=True,
        )

    def test_forwards_codex_arguments_and_selected_home(self):
        result = self.run_mux("as", "acc1", "--yolo", "resume", "thread-123")
        payload = json.loads(result.stdout)
        self.assertEqual(payload["codexHome"], str(self.root / "accounts" / "acc1"))
        self.assertEqual(payload["sqliteHome"], str(self.root / "shared"))
        self.assertEqual(payload["args"][-3:], ["--yolo", "resume", "thread-123"])
        self.assertIn('cli_auth_credentials_store="file"', payload["args"])

    def test_smi_uses_structured_app_server_responses(self):
        result = self.run_mux("smi", "--json", "acc1")
        payload = json.loads(result.stdout)
        self.assertEqual(payload[0]["account"]["email"], "fake@example.com")
        self.assertEqual(payload[0]["rateLimits"]["rateLimitResetCredits"]["availableCount"], 3)

    def test_migration_cli_copies_and_reindexes(self):
        source = self.root.parent / "original-codex"
        session = source / "sessions" / "rollout-old.jsonl"
        session.parent.mkdir(parents=True)
        session.write_text(json.dumps({"type": "session_meta", "payload": {
            "id": "old-thread", "cwd": "/original/project"}}) + "\n")
        result = self.run_mux("as", "migrate-sessions", "--from", str(source), "--apply", "--reindex")
        self.assertIn("Copied 1 sessions", result.stdout)
        self.assertIn("Reindexed", result.stdout)
        self.assertEqual((self.root / "shared/sessions/rollout-old.jsonl").read_bytes(),
                         session.read_bytes())

    def test_reindex_scans_active_and_archived_threads(self):
        result = self.run_mux("as", "reindex", "acc1")
        self.assertIn("Reindexed 2 visible threads", result.stdout)


if __name__ == "__main__":
    unittest.main()
