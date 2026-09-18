import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


FAKE_REMOTE_CODEX = r'''#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

args = sys.argv[1:]
home = Path(os.environ.get("CODEX_HOME", "."))

if args and args[0] == "app-server":
    for line in sys.stdin:
        message = json.loads(line)
        method = message.get("method")
        message_id = message.get("id")
        if method == "initialize":
            result = {"codexHome": str(home)}
        elif method == "account/read":
            result = {
                "account": {
                    "type": "chatgpt",
                    "email": f"{home.name}@example.test",
                    "planType": "pro",
                },
                "requiresOpenaiAuth": True,
            }
        elif method == "account/rateLimits/read":
            result = {
                "rateLimits": {
                    "limitId": "codex",
                    "primary": {
                        "usedPercent": 25,
                        "windowDurationMins": 300,
                        "resetsAt": None,
                    },
                    "secondary": None,
                },
                "rateLimitsByLimitId": None,
                "rateLimitResetCredits": {"availableCount": 1, "credits": None},
            }
        elif method == "thread/list":
            result = {"data": [], "nextCursor": None, "backwardsCursor": None}
        else:
            continue
        print(json.dumps({"id": message_id, "result": result}), flush=True)
elif args and args[0] == "login":
    home.mkdir(mode=0o700, parents=True, exist_ok=True)
    auth = home / "auth.json"
    auth.write_text("{}\n", encoding="utf-8")
    auth.chmod(0o600)
elif args and args[0] == "logout":
    (home / "auth.json").unlink(missing_ok=True)
elif "--version" in args:
    print("codex-cli remote-fake")
else:
    print(json.dumps({"args": args, "codexHome": str(home)}))
'''


class RemoteDeploymentTests(unittest.TestCase):
    def test_clean_remote_home_install_login_and_launch(self):
        with tempfile.TemporaryDirectory() as temporary:
            remote_home = Path(temporary) / "home" / "remote-user"
            remote_bin = remote_home / ".local" / "bin"
            remote_bin.mkdir(mode=0o755, parents=True)
            fake_codex = remote_bin / "codex"
            fake_codex.write_text(FAKE_REMOTE_CODEX, encoding="utf-8")
            fake_codex.chmod(fake_codex.stat().st_mode | stat.S_IXUSR)

            env = os.environ.copy()
            env.update(
                {
                    "HOME": str(remote_home),
                    "PATH": f"{remote_bin}:/usr/bin:/bin",
                    "PYTHONDONTWRITEBYTECODE": "1",
                }
            )
            for name in (
                "CODEX_MULTIPLEXER_HOME",
                "CODEX_MUX_HOME",
                "XDG_DATA_HOME",
            ):
                env.pop(name, None)

            subprocess.run(["sh", str(REPO / "install.sh")], env=env, check=True)
            data_root = remote_home / ".local" / "share" / "codex-multiplexer"
            docs_root = remote_home / ".local" / "share" / "doc" / "codex-multiplexer"
            self.assertFalse(data_root.exists())
            self.assertTrue((docs_root / "schema" / "registry.schema.json").is_file())

            codex_as = remote_bin / "codex-as"
            codex_smi = remote_bin / "codex-smi"
            for account in ("acc1", "acc2"):
                subprocess.run(
                    [str(codex_as), "add", account, "--device-auth"],
                    env=env,
                    check=True,
                )

            registry = json.loads((data_root / "registry.json").read_text(encoding="utf-8"))
            self.assertEqual([item["name"] for item in registry["accounts"]], ["acc1", "acc2"])
            for account in ("acc1", "acc2"):
                link = data_root / "accounts" / account / "sessions"
                self.assertTrue(link.is_symlink())
                self.assertFalse(link.readlink().is_absolute())

            launch = subprocess.run(
                [str(codex_as), "acc2", "--yolo", "resume", "remote-thread"],
                env=env,
                text=True,
                capture_output=True,
                check=True,
            )
            forwarded = json.loads(launch.stdout)
            self.assertEqual(forwarded["args"][-3:], ["--yolo", "resume", "remote-thread"])

            smi = subprocess.run(
                [str(codex_smi), "--json", "--no-refresh"],
                env=env,
                text=True,
                capture_output=True,
                check=True,
            )
            identities = json.loads(smi.stdout)
            self.assertEqual(len(identities), 2)
            self.assertEqual(identities[1]["account"]["email"], "acc2@example.test")

            doctor = subprocess.run(
                [str(codex_as), "doctor"],
                env=env,
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertIn("Portable data: self-contained", doctor.stdout)


if __name__ == "__main__":
    unittest.main()
