import json
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import codex_mux


REPO = Path(__file__).resolve().parents[1]


class RegistryTests(unittest.TestCase):
    def test_default_registry_round_trip_and_permissions(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            registry = codex_mux.default_registry()
            codex_mux.save_registry(registry, root)
            loaded = codex_mux.load_registry(root=root)
            self.assertEqual(loaded, registry)
            mode = stat.S_IMODE(codex_mux.registry_path(root).stat().st_mode)
            self.assertEqual(mode, 0o600)

    def test_example_registry_matches_runtime_contract(self):
        example = json.loads(
            (REPO / "examples" / "registry.example.json").read_text(encoding="utf-8")
        )
        with tempfile.TemporaryDirectory() as temporary:
            self.assertIs(codex_mux.validate_registry(example, Path(temporary)), example)

    def test_registry_rejects_unknown_fields(self):
        registry = codex_mux.default_registry()
        registry["unexpected"] = True
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(codex_mux.MuxError):
                codex_mux.validate_registry(registry, Path(temporary))

    def test_account_layout_shares_runtime_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            registry = codex_mux.default_registry()
            registry["accounts"] = [
                {"name": "acc1", "codexHome": "accounts/acc1", "aliases": []},
                {"name": "acc2", "codexHome": "accounts/acc2", "aliases": []},
            ]
            registry["defaultAccount"] = "acc1"
            warnings = codex_mux.ensure_layout(registry, root=root)
            self.assertEqual(warnings, [])
            for account in registry["accounts"]:
                for path_name in ("sessions", "shell_snapshots", "thread-writer-locks"):
                    link = codex_mux.account_home(account, root) / path_name
                    expected = (root / "shared" / path_name).resolve()
                    self.assertTrue(link.is_symlink())
                    self.assertFalse(link.readlink().is_absolute())
                    self.assertEqual(link.resolve(), expected)

    def test_moved_tree_repairs_legacy_absolute_links(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            original = base / "original"
            moved = base / "moved"
            registry = codex_mux.default_registry()
            registry["accounts"] = [
                {"name": "acc1", "codexHome": "accounts/acc1", "aliases": []}
            ]
            registry["defaultAccount"] = "acc1"
            codex_mux.ensure_layout(registry, root=original)

            link = original / "accounts" / "acc1" / "sessions"
            link.unlink()
            link.symlink_to(original / "shared" / "sessions", target_is_directory=True)
            original.rename(moved)
            moved_link = moved / "accounts" / "acc1" / "sessions"
            self.assertFalse(moved_link.exists())

            warnings = codex_mux.ensure_layout(registry, root=moved)
            self.assertEqual(warnings, [])
            self.assertFalse(moved_link.readlink().is_absolute())
            self.assertEqual(moved_link.resolve(), (moved / "shared" / "sessions").resolve())

    def test_resolve_by_alias_or_email(self):
        registry = codex_mux.default_registry()
        account = {
            "name": "acc1",
            "codexHome": "accounts/acc1",
            "aliases": ["account-1"],
            "expectedEmail": "me@example.com",
        }
        registry["accounts"] = [account]
        for selector in ("acc1", "ACCOUNT-1", "ME@example.com"):
            self.assertIs(codex_mux.resolve_account(registry, selector), account)

    def test_omarchy_import_uses_short_names_and_existing_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "mux"
            source = base / "codex-accounts.json"
            account_one = base / "legacy-one"
            account_two = base / "legacy-two"
            account_one.mkdir()
            account_two.mkdir()
            source.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "defaultAccount": "account-1",
                        "accounts": [
                            {
                                "id": "account-1",
                                "email": "one@example.com",
                                "codexHome": str(account_one),
                            },
                            {
                                "id": "account-2",
                                "email": "two@example.com",
                                "codexHome": str(account_two),
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            result = codex_mux.import_omarchy(["--registry", str(source)], root=root)
            self.assertEqual(result, 0)
            registry = codex_mux.load_registry(root=root)
            self.assertEqual([item["name"] for item in registry["accounts"]], ["acc1", "acc2"])
            self.assertEqual(registry["defaultAccount"], "acc1")
            self.assertEqual(codex_mux.shared_state_path(registry, root), account_one.resolve())


class FormattingTests(unittest.TestCase):
    def test_balancer_uses_all_registered_accounts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            registry = codex_mux.default_registry()
            registry["accounts"] = [
                {"name": "acc1", "codexHome": "accounts/acc1", "aliases": []},
                {"name": "acc2", "codexHome": "accounts/acc2", "aliases": []},
            ]
            registry["defaultAccount"] = "acc1"
            codex_mux.save_registry(registry, root)

            def probe(_registry, account, **_kwargs):
                used = 10 if account["name"] == "acc1" else 40
                return {
                    "name": account["name"],
                    "account": {"type": "chatgpt"},
                    "emailMatchesExpected": True,
                    "rateLimits": {
                        "rateLimits": {
                            "limitId": "codex",
                            "primary": {"usedPercent": used, "windowDurationMins": 300},
                        }
                    },
                }

            with (
                mock.patch.object(codex_mux, "probe_account", side_effect=probe),
                mock.patch.object(codex_mux, "codex_binary", return_value="/usr/bin/codex"),
                mock.patch.object(codex_mux.os, "execvpe") as execvpe,
            ):
                self.assertEqual(codex_mux.balanced_main([], root=root), 127)

            selected_env = execvpe.call_args.args[2]
            self.assertEqual(selected_env["CODEX_HOME"], str(root / "accounts" / "acc1"))

    def test_balanced_candidate_uses_the_most_constrained_window(self):
        candidate = codex_mux._balanced_candidate(
            "acc1",
            {
                "account": {"type": "chatgpt", "email": "one@example.com"},
                "emailMatchesExpected": True,
                "rateLimits": {
                    "rateLimitsByLimitId": {
                        "codex": {
                            "primary": {"usedPercent": 25, "windowDurationMins": 300},
                            "secondary": {"usedPercent": 80, "windowDurationMins": 10080},
                        }
                    }
                },
            },
        )
        self.assertEqual(candidate["status"], "eligible")
        self.assertEqual(candidate["remaining_percent"], 20.0)
        self.assertEqual(candidate["longest_window_remaining_percent"], 20.0)

    def test_balanced_candidate_requires_chatgpt_authentication(self):
        candidate = codex_mux._balanced_candidate("acc1", {"account": None})
        self.assertEqual(candidate["status"], "authentication")

    def test_smi_info_documents_account_and_session_commands(self):
        self.assertIn("codex-as add NAME --expect EMAIL --device-auth", codex_mux.SMI_INFO)
        self.assertIn("  codex-as NAME\n", codex_mux.SMI_INFO)
        self.assertIn("codex-as NAME resume", codex_mux.SMI_INFO)
        self.assertIn("codex-as NAME resume CHAT_ID", codex_mux.SMI_INFO)

    def test_smi_compacts_main_limit_to_one_row(self):
        probes = [
            {
                "name": "acc1",
                "account": {"type": "chatgpt", "email": "one@example.com", "planType": "pro"},
                "emailMatchesExpected": True,
                "rateLimits": {
                    "rateLimitsByLimitId": {
                        "codex": {
                            "limitId": "codex",
                            "primary": {
                                "usedPercent": 35,
                                "windowDurationMins": 300,
                                "resetsAt": None,
                            },
                            "secondary": None,
                        }
                    },
                    "rateLimitResetCredits": {"availableCount": 2, "credits": None},
                },
            }
        ]
        rows = codex_mux._smi_rows(probes)
        self.assertEqual(
            rows,
            [["acc1", "one@example.com", "[██████▌░░░] 65%", "2", "-"]],
        )

    def test_remaining_usage_bar_shows_available_quota(self):
        self.assertEqual(codex_mux._remaining_usage_bar(5), "[█████████▌] 95%")
        self.assertEqual(codex_mux._remaining_usage_bar(98), "[▎░░░░░░░░░] 2%")
        self.assertEqual(codex_mux._remaining_usage_bar(93), "[▊░░░░░░░░░] 7%")
        self.assertEqual(codex_mux._remaining_usage_bar(None), "-")

    def test_detailed_smi_keeps_multi_bucket_limits(self):
        probes = [
            {
                "name": "acc1",
                "account": {"type": "chatgpt", "email": "one@example.com", "planType": "pro"},
                "emailMatchesExpected": True,
                "rateLimits": {
                    "rateLimitsByLimitId": {
                        "codex": {
                            "limitId": "codex",
                            "primary": {"usedPercent": 35, "windowDurationMins": 300},
                            "secondary": None,
                        }
                    },
                    "rateLimitResetCredits": {"availableCount": 2, "credits": None},
                },
            }
        ]
        rows = codex_mux._smi_detailed_rows(probes)
        self.assertEqual(rows[0][5], "[██████▌░░░] 65%")
        self.assertEqual(rows[0][6], "2")

    def test_config_args_include_shared_sqlite_and_file_auth(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            registry = codex_mux.default_registry()
            args = codex_mux.codex_config_args(registry, root)
            self.assertIn(f'sqlite_home="{root / "shared"}"', args)
            self.assertIn('cli_auth_credentials_store="file"', args)
            env = codex_mux.account_env(
                {"name": "acc1", "codexHome": "accounts/acc1", "aliases": []},
                root,
                registry,
            )
            self.assertEqual(env["CODEX_SQLITE_HOME"], str(root / "shared"))


if __name__ == "__main__":
    unittest.main()
