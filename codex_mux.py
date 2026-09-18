#!/usr/bin/env python3
"""Portable multi-account launcher and usage monitor for the Codex CLI."""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fcntl
import json
import os
import queue
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Iterable


APP_NAME = "codex-multiplexer"
TOOL_VERSION = "1.2.0"
REGISTRY_VERSION = 1
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
DEFAULT_TIMEOUT = 15.0
SMI_INFO = """Codex Multiplexer quick reference

Add a new account (recommended for SSH/headless machines):
  codex-as add NAME --expect EMAIL --device-auth

Start a new Codex session:
  codex-as NAME

Resume from the shared session picker:
  codex-as NAME resume

Resume a specific chat, including under a different account:
  codex-as NAME resume CHAT_ID

Safer start with Codex sandboxing and approvals enabled:
  codex-as NAME --sandbox workspace-write --ask-for-approval on-request

Useful checks:
  codex-as accounts
  codex-as doctor
  codex-smi

Replace NAME with an account selector such as acc1, work, or personal.
The --yolo option disables Codex approvals and sandboxing; use it only when intended."""


class MuxError(RuntimeError):
    """An error that should be shown without a traceback."""


def data_root() -> Path:
    override = os.environ.get("CODEX_MULTIPLEXER_HOME") or os.environ.get("CODEX_MUX_HOME")
    if override:
        return Path(override).expanduser().resolve()
    xdg_data = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg_data).expanduser() if xdg_data else Path.home() / ".local" / "share"
    return (base / APP_NAME).resolve()


def registry_path(root: Path | None = None) -> Path:
    return (root or data_root()) / "registry.json"


def default_registry() -> dict[str, Any]:
    return {
        "version": REGISTRY_VERSION,
        "defaultAccount": None,
        "sharedState": "shared",
        "accounts": [],
    }


def _expand_path(value: str, root: Path) -> Path:
    expanded = os.path.expandvars(os.path.expanduser(value))
    path = Path(expanded)
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def _portable_path(path: Path, root: Path) -> str:
    path = path.expanduser().resolve()
    try:
        return str(path.relative_to(root.resolve()))
    except ValueError:
        pass
    try:
        return "~/" + str(path.relative_to(Path.home().resolve()))
    except ValueError:
        return str(path)


def validate_registry(payload: Any, root: Path) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("version") != REGISTRY_VERSION:
        raise MuxError(f"{registry_path(root)} is not a version {REGISTRY_VERSION} registry")
    allowed_registry_keys = {"$schema", "version", "defaultAccount", "sharedState", "accounts"}
    unknown_registry_keys = set(payload) - allowed_registry_keys
    if unknown_registry_keys:
        raise MuxError(f"unknown registry fields: {', '.join(sorted(unknown_registry_keys))}")
    if not isinstance(payload.get("sharedState"), str) or not payload["sharedState"].strip():
        raise MuxError("registry sharedState must be a non-empty path")
    accounts = payload.get("accounts")
    if not isinstance(accounts, list):
        raise MuxError("registry accounts must be a list")

    seen: set[str] = set()
    for raw in accounts:
        if not isinstance(raw, dict):
            raise MuxError("registry account entries must be objects")
        allowed_account_keys = {"name", "codexHome", "aliases", "expectedEmail"}
        unknown_account_keys = set(raw) - allowed_account_keys
        if unknown_account_keys:
            raise MuxError(
                f"unknown fields for account {raw.get('name')!r}: "
                f"{', '.join(sorted(unknown_account_keys))}"
            )
        name = raw.get("name")
        home = raw.get("codexHome")
        aliases = raw.get("aliases", [])
        if not isinstance(name, str) or not SAFE_NAME.fullmatch(name):
            raise MuxError(f"invalid account name in registry: {name!r}")
        if not isinstance(home, str) or not home.strip():
            raise MuxError(f"account {name!r} has no codexHome")
        if not isinstance(aliases, list) or any(
            not isinstance(item, str) or not item for item in aliases
        ):
            raise MuxError(f"account {name!r} aliases must be non-empty strings")
        for selector in [name, *aliases]:
            folded = selector.casefold()
            if folded in seen:
                raise MuxError(f"duplicate account selector in registry: {selector!r}")
            seen.add(folded)
        email = raw.get("expectedEmail")
        if email is not None and (
            not isinstance(email, str) or not email or "@" not in email
        ):
            raise MuxError(f"account {name!r} has an invalid expectedEmail")

    default = payload.get("defaultAccount")
    if default is not None and not any(item["name"] == default for item in accounts):
        raise MuxError(f"default account {default!r} is not configured")
    return payload


def load_registry(*, create: bool = False, root: Path | None = None) -> dict[str, Any]:
    root = root or data_root()
    path = registry_path(root)
    if not path.exists():
        if create:
            return default_registry()
        raise MuxError(
            "no accounts are configured; start with `codex-as add acc1` "
            "or import this machine with `codex-as import-omarchy`"
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MuxError(f"cannot read {path}: {exc}") from exc
    return validate_registry(payload, root)


def save_registry(payload: dict[str, Any], root: Path | None = None) -> None:
    root = root or data_root()
    validate_registry(payload, root)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        root.chmod(0o700)
    path = registry_path(root)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
        path.chmod(0o600)
    finally:
        with contextlib.suppress(FileNotFoundError):
            temp.unlink()


@contextlib.contextmanager
def registry_lock(root: Path | None = None):
    root = root or data_root()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock_path = root / "registry.lock"
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    with os.fdopen(fd, "a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def shared_state_path(registry: dict[str, Any], root: Path | None = None) -> Path:
    return _expand_path(registry["sharedState"], root or data_root())


def account_home(account: dict[str, Any], root: Path | None = None) -> Path:
    return _expand_path(account["codexHome"], root or data_root())


def resolve_account(registry: dict[str, Any], selector: str) -> dict[str, Any]:
    wanted = selector.casefold()
    for account in registry["accounts"]:
        selectors = [account["name"], *account.get("aliases", [])]
        expected = account.get("expectedEmail")
        if expected:
            selectors.append(expected)
        if any(wanted == item.casefold() for item in selectors):
            return account
    choices = ", ".join(item["name"] for item in registry["accounts"]) or "none"
    raise MuxError(f"unknown account {selector!r}; configured accounts: {choices}")


def codex_binary() -> str:
    override = os.environ.get("CODEX_MULTIPLEXER_CODEX_BIN") or os.environ.get(
        "CODEX_MUX_CODEX_BIN"
    )
    command = override or shutil.which("codex")
    if not command:
        raise MuxError("Codex CLI is not installed or is not in PATH")
    return command


def codex_config_args(registry: dict[str, Any], root: Path | None = None) -> list[str]:
    state = shared_state_path(registry, root)
    return [
        "-c",
        f"sqlite_home={json.dumps(str(state))}",
        "-c",
        'cli_auth_credentials_store="file"',
    ]


def account_env(
    account: dict[str, Any],
    root: Path | None = None,
    registry: dict[str, Any] | None = None,
) -> dict[str, str]:
    env = os.environ.copy()
    env["CODEX_HOME"] = str(account_home(account, root))
    if registry is not None:
        env["CODEX_SQLITE_HOME"] = str(shared_state_path(registry, root))
    return env


def ensure_layout(
    registry: dict[str, Any], account: dict[str, Any] | None = None, root: Path | None = None
) -> list[str]:
    """Create safe directories and share Codex's cross-account runtime paths."""
    root = root or data_root()
    state = shared_state_path(registry, root)
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        state.chmod(0o700)
    shared_paths = {
        "sessions": state / "sessions",
        "shell_snapshots": state / "shell_snapshots",
        "thread-writer-locks": state / "thread-writer-locks",
    }
    for shared_path in shared_paths.values():
        shared_path.mkdir(mode=0o700, parents=True, exist_ok=True)
    warnings: list[str] = []
    targets = [account] if account is not None else registry["accounts"]
    for item in targets:
        home = account_home(item, root)
        created = not home.exists()
        home.mkdir(mode=0o700, parents=True, exist_ok=True)
        if created:
            with contextlib.suppress(OSError):
                home.chmod(0o700)
        for path_name, shared_path in shared_paths.items():
            local_path = home / path_name
            relative_target = Path(os.path.relpath(shared_path, local_path.parent))
            try:
                if local_path.resolve() == shared_path.resolve():
                    # Links inside the managed data tree must remain valid when the
                    # whole tree moves to a different absolute path.
                    if (
                        local_path.is_symlink()
                        and local_path.readlink().is_absolute()
                        and (home == root or root in home.parents)
                        and (shared_path == root or root in shared_path.parents)
                    ):
                        local_path.unlink()
                        local_path.symlink_to(relative_target, target_is_directory=True)
                    continue
            except OSError:
                pass
            if local_path.is_symlink():
                # A copied managed tree can retain a now-broken absolute link from
                # its previous location. Repair only broken links inside our tree;
                # a live link to different data remains user-owned and is warned.
                if (
                    not local_path.exists()
                    and (home == root or root in home.parents)
                    and (shared_path == root or root in shared_path.parents)
                ):
                    local_path.unlink()
                    local_path.symlink_to(relative_target, target_is_directory=True)
                else:
                    warnings.append(f"{item['name']}: {local_path} points somewhere else")
            elif local_path.exists():
                warning = f"{item['name']}: {local_path} is not shared"
                if path_name == "thread-writer-locks":
                    warning += "; do not resume the same thread concurrently from two accounts"
                elif path_name == "sessions":
                    warning += "; its older chats may be absent from other accounts' pickers"
                warnings.append(warning)
            else:
                local_path.symlink_to(relative_target, target_is_directory=True)
    return warnings


def _account_process_lock(account: dict[str, Any], root: Path | None = None):
    lock_dir = (root or data_root()) / "account-locks"
    lock_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = lock_dir / f"{account['name']}.lock"
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    return os.fdopen(fd, "a+")


class AppServerClient:
    def __init__(
        self,
        registry: dict[str, Any],
        account: dict[str, Any],
        *,
        timeout: float = DEFAULT_TIMEOUT,
        root: Path | None = None,
    ) -> None:
        self.registry = registry
        self.account = account
        self.timeout = timeout
        self.root = root or data_root()
        self.process: subprocess.Popen[str] | None = None
        self.lines: queue.Queue[str | None] = queue.Queue()

    def __enter__(self) -> "AppServerClient":
        command = [codex_binary(), "app-server", *codex_config_args(self.registry, self.root)]
        self.process = subprocess.Popen(
            command,
            env=account_env(self.account, self.root, self.registry),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        threading.Thread(target=self._reader, daemon=True).start()
        try:
            self.send(
                {
                    "method": "initialize",
                    "id": 0,
                    "params": {
                        "clientInfo": {
                            "name": "codex_multiplexer",
                            "title": "Codex Multiplexer",
                            "version": TOOL_VERSION,
                        }
                    },
                }
            )
            response = self.wait_for({0}, self.timeout).get(0)
            if not response or "error" in response:
                raise MuxError(f"{self.account['name']}: Codex App Server initialization failed")
            self.send({"method": "initialized", "params": {}})
            return self
        except Exception:
            self.__exit__(None, None, None)
            raise

    def _reader(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        try:
            for line in self.process.stdout:
                self.lines.put(line)
        finally:
            self.lines.put(None)

    def send(self, message: dict[str, Any]) -> None:
        if not self.process or not self.process.stdin:
            raise MuxError("Codex App Server is not running")
        self.process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
        self.process.stdin.flush()

    def wait_for(self, ids: set[int], timeout: float | None = None) -> dict[int, dict[str, Any]]:
        deadline = time.monotonic() + (timeout if timeout is not None else self.timeout)
        found: dict[int, dict[str, Any]] = {}
        while ids - found.keys():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                missing = ", ".join(str(item) for item in sorted(ids - found.keys()))
                raise MuxError(
                    f"{self.account['name']}: timed out waiting for App Server response {missing}"
                )
            try:
                line = self.lines.get(timeout=remaining)
            except queue.Empty as exc:
                raise MuxError(f"{self.account['name']}: Codex App Server timed out") from exc
            if line is None:
                raise MuxError(f"{self.account['name']}: Codex App Server exited early")
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            message_id = message.get("id") if isinstance(message, dict) else None
            if isinstance(message_id, int) and message_id in ids:
                found[message_id] = message
        return found

    def request_many(self, requests: Iterable[dict[str, Any]]) -> dict[int, dict[str, Any]]:
        ids: set[int] = set()
        for request in requests:
            request_id = request.get("id")
            if not isinstance(request_id, int):
                raise ValueError("App Server requests need integer ids")
            ids.add(request_id)
            self.send(request)
        return self.wait_for(ids)

    def __exit__(self, exc_type, exc, traceback) -> None:
        if not self.process:
            return
        if self.process.stdin:
            with contextlib.suppress(OSError):
                self.process.stdin.close()
        if self.process.poll() is None:
            self.process.terminate()
        try:
            self.process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=2)


def _result_or_error(message: dict[str, Any], label: str) -> dict[str, Any]:
    if "error" in message:
        error = message["error"]
        detail = error.get("message") if isinstance(error, dict) else str(error)
        raise MuxError(f"{label}: {detail or 'request failed'}")
    result = message.get("result")
    if not isinstance(result, dict):
        raise MuxError(f"{label}: malformed response")
    return result


def probe_account(
    registry: dict[str, Any],
    account: dict[str, Any],
    *,
    include_usage: bool = False,
    refresh_token: bool = True,
    timeout: float = DEFAULT_TIMEOUT,
    root: Path | None = None,
) -> dict[str, Any]:
    output: dict[str, Any] = {
        "name": account["name"],
        "expectedEmail": account.get("expectedEmail"),
    }
    try:
        with AppServerClient(registry, account, timeout=timeout, root=root) as client:
            requests = [
                {
                    "method": "account/read",
                    "id": 1,
                    "params": {"refreshToken": refresh_token},
                },
                {"method": "account/rateLimits/read", "id": 2},
            ]
            if include_usage:
                requests.append({"method": "account/usage/read", "id": 3})
            responses = client.request_many(requests)
        output["account"] = _result_or_error(
            responses[1], f"{account['name']} identity"
        ).get("account")
        try:
            output["rateLimits"] = _result_or_error(
                responses[2], f"{account['name']} rate limits"
            )
        except MuxError as exc:
            output["rateLimitsError"] = str(exc)
        if include_usage:
            try:
                output["usage"] = _result_or_error(responses[3], f"{account['name']} usage")
            except MuxError as exc:
                output["usageError"] = str(exc)
    except Exception as exc:
        output["error"] = str(exc)
    actual = output.get("account")
    actual_email = actual.get("email") if isinstance(actual, dict) else None
    expected = account.get("expectedEmail")
    output["emailMatchesExpected"] = not (
        expected and actual_email and expected.casefold() != str(actual_email).casefold()
    )
    return output


def login_account(
    registry: dict[str, Any],
    account: dict[str, Any],
    login_args: list[str],
    *,
    root: Path | None = None,
) -> None:
    root = root or data_root()
    warnings = ensure_layout(registry, account, root)
    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)
    command = [codex_binary(), "login", *codex_config_args(registry, root), *login_args]
    with _account_process_lock(account, root) as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        print(f"Signing in account {account['name']}...", flush=True)
        result = subprocess.run(command, env=account_env(account, root, registry))
        if result.returncode:
            raise MuxError(f"login for {account['name']} did not complete")
        probe = probe_account(registry, account, refresh_token=False, root=root)
        identity = probe.get("account")
        if not isinstance(identity, dict):
            raise MuxError(
                f"Codex reported a successful login, but {account['name']} could not be verified: "
                f"{probe.get('error') or 'identity unavailable'}"
            )
        actual_email = identity.get("email")
        expected = account.get("expectedEmail")
        if expected and actual_email and expected.casefold() != str(actual_email).casefold():
            subprocess.run(
                [codex_binary(), "logout", *codex_config_args(registry, root)],
                env=account_env(account, root, registry),
                stdout=subprocess.DEVNULL,
            )
            raise MuxError(
                f"expected {expected}, but the browser authenticated {actual_email}; "
                "the incorrect login was cleared"
            )
        if actual_email and not expected:
            account["expectedEmail"] = actual_email
            with registry_lock(root):
                latest = load_registry(root=root)
                latest_account = resolve_account(latest, account["name"])
                latest_account["expectedEmail"] = actual_email
                save_registry(latest, root)
        plan = identity.get("planType") or "unknown plan"
        print(
            f"Signed in {account['name']} as "
            f"{actual_email or identity.get('type')} ({plan})."
        )


def add_account(args: list[str], root: Path | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="codex-as add", description="Add and sign in a Codex account"
    )
    parser.add_argument("name")
    parser.add_argument(
        "--expect", metavar="EMAIL", help="Refuse a browser login for a different email"
    )
    parser.add_argument(
        "--device-auth", action="store_true", help="Use Codex device-code login"
    )
    options = parser.parse_args(args)
    if not SAFE_NAME.fullmatch(options.name):
        raise MuxError("account names may contain letters, numbers, dot, underscore, and hyphen")
    if options.expect and "@" not in options.expect:
        raise MuxError("--expect must be an email address")
    root = root or data_root()
    with registry_lock(root):
        registry = load_registry(create=True, root=root)
        existing = next(
            (item for item in registry["accounts"] if item["name"] == options.name),
            None,
        )
        if existing:
            account = existing
            if options.expect:
                account["expectedEmail"] = options.expect
        else:
            account = {
                "name": options.name,
                "codexHome": f"accounts/{options.name}",
                "aliases": [],
            }
            if options.expect:
                account["expectedEmail"] = options.expect
            registry["accounts"].append(account)
            if not registry.get("defaultAccount"):
                registry["defaultAccount"] = options.name
        save_registry(registry, root)
    login_account(
        registry,
        account,
        ["--device-auth"] if options.device_auth else [],
        root=root,
    )
    return 0


def login_command(args: list[str], root: Path | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="codex-as login", description="Sign in an existing account"
    )
    parser.add_argument("account")
    parser.add_argument("--device-auth", action="store_true")
    options = parser.parse_args(args)
    root = root or data_root()
    registry = load_registry(root=root)
    account = resolve_account(registry, options.account)
    login_account(
        registry,
        account,
        ["--device-auth"] if options.device_auth else [],
        root=root,
    )
    return 0


def logout_command(args: list[str], root: Path | None = None) -> int:
    if len(args) != 1:
        raise MuxError("usage: codex-as logout ACCOUNT")
    root = root or data_root()
    registry = load_registry(root=root)
    account = resolve_account(registry, args[0])
    with _account_process_lock(account, root) as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        result = subprocess.run(
            [codex_binary(), "logout", *codex_config_args(registry, root)],
            env=account_env(account, root, registry),
        )
    return result.returncode


def list_accounts(root: Path | None = None) -> int:
    registry = load_registry(root=root)
    default = registry.get("defaultAccount")
    print("ACCOUNT\tEXPECTED EMAIL\tCODEX HOME")
    for account in registry["accounts"]:
        marker = " *" if account["name"] == default else ""
        print(
            f"{account['name']}{marker}\t{account.get('expectedEmail') or '-'}\t"
            f"{account_home(account, root)}"
        )
    return 0


def set_default(args: list[str], root: Path | None = None) -> int:
    if len(args) != 1:
        raise MuxError("usage: codex-as default ACCOUNT")
    root = root or data_root()
    with registry_lock(root):
        registry = load_registry(root=root)
        account = resolve_account(registry, args[0])
        registry["defaultAccount"] = account["name"]
        save_registry(registry, root)
    print(f"Default account: {account['name']}")
    return 0


def _short_import_name(account_id: str) -> str:
    match = re.fullmatch(r"account[-_](\d+)", account_id, re.IGNORECASE)
    return f"acc{match.group(1)}" if match else account_id


def import_omarchy(args: list[str], root: Path | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="codex-as import-omarchy",
        description="Import account homes from Omarchy without copying credentials",
    )
    parser.add_argument(
        "--registry",
        default=str(Path.home() / ".config" / "omarchy" / "agents" / "codex-accounts.json"),
    )
    options = parser.parse_args(args)
    source_path = Path(options.registry).expanduser().resolve()
    try:
        source = json.loads(source_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MuxError(f"cannot read Omarchy account registry {source_path}: {exc}") from exc
    source_accounts = source.get("accounts") if isinstance(source, dict) else None
    if not isinstance(source_accounts, list) or not source_accounts:
        raise MuxError(f"no accounts found in {source_path}")

    root = root or data_root()
    imported: list[str] = []
    with registry_lock(root):
        registry = load_registry(create=True, root=root)
        if not registry["accounts"]:
            default_source = str(source.get("defaultAccount") or source_accounts[0].get("id"))
            default_entry = next(
                (item for item in source_accounts if str(item.get("id")) == default_source),
                source_accounts[0],
            )
            registry["sharedState"] = str(default_entry.get("codexHome") or "~/.codex")
        existing_selectors = {
            selector.casefold()
            for item in registry["accounts"]
            for selector in [item["name"], *item.get("aliases", [])]
        }
        name_map: dict[str, str] = {}
        for raw in source_accounts:
            if not isinstance(raw, dict):
                continue
            old_id = str(raw.get("id") or "").strip()
            home = str(raw.get("codexHome") or "").strip()
            email = str(raw.get("email") or "").strip()
            if not SAFE_NAME.fullmatch(old_id) or not home:
                raise MuxError(f"invalid account in {source_path}: {old_id!r}")
            if old_id.casefold() in existing_selectors:
                existing = resolve_account(registry, old_id)
                name_map[old_id] = existing["name"]
                continue
            name = _short_import_name(old_id)
            base_name = name
            suffix = 2
            while name.casefold() in existing_selectors:
                name = f"{base_name}-{suffix}"
                suffix += 1
            aliases = [] if name == old_id else [old_id]
            account = {
                "name": name,
                "codexHome": home,
                "aliases": aliases,
            }
            if email:
                account["expectedEmail"] = email
            registry["accounts"].append(account)
            existing_selectors.update(item.casefold() for item in [name, *aliases])
            imported.append(name)
            name_map[old_id] = name
        old_default = str(source.get("defaultAccount") or "")
        if not registry.get("defaultAccount") and old_default in name_map:
            registry["defaultAccount"] = name_map[old_default]
        save_registry(registry, root)

    warnings = ensure_layout(registry, root=root)
    print(f"Imported: {', '.join(imported) if imported else 'no new accounts'}")
    print(f"Shared chat state and rollouts: {shared_state_path(registry, root)}")
    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)
    return 0


def _latest_state_db(state: Path) -> Path | None:
    candidates: list[tuple[int, Path]] = []
    for path in state.glob("state_*.sqlite"):
        match = re.fullmatch(r"state_(\d+)\.sqlite", path.name)
        if match:
            candidates.append((int(match.group(1)), path))
    return max(candidates, default=(0, None))[1]


def doctor(root: Path | None = None) -> int:
    root = root or data_root()
    registry = load_registry(root=root)
    problems = 0
    print(f"Registry:     {registry_path(root)}")
    print(f"Shared state: {shared_state_path(registry, root)}")
    registry_mode = registry_path(root).stat().st_mode & 0o777
    root_mode = root.stat().st_mode & 0o777
    print(f"Permissions:  data={root_mode:04o}, registry={registry_mode:04o}")
    if root_mode & 0o077 or registry_mode & 0o077:
        print("WARNING: multiplexer metadata is accessible to other local users")
        problems += 1
    try:
        version = subprocess.run(
            [codex_binary(), "--version"], text=True, capture_output=True, timeout=5, check=True
        ).stdout.strip()
        print(f"Codex:        {version}")
    except Exception as exc:
        print(f"Codex:        ERROR ({exc})")
        problems += 1
    warnings = ensure_layout(registry, root=root)
    for account in registry["accounts"]:
        home = account_home(account, root)
        auth = home / "auth.json"
        status = "auth file present" if auth.is_file() else "NOT LOGGED IN"
        print(f"{account['name']}: {status}; {home}")
        if not home.is_dir() or not auth.is_file():
            problems += 1
        elif auth.stat().st_mode & 0o077:
            print(f"  WARNING: {auth} should not be accessible to other local users")
            problems += 1
    for warning in warnings:
        print(f"WARNING: {warning}")
    problems += len(warnings)

    state_db = _latest_state_db(shared_state_path(registry, root))
    if state_db is None:
        print("Threads:      none yet")
    else:
        try:
            connection = sqlite3.connect(f"file:{state_db}?mode=ro", uri=True)
            rows = connection.execute("SELECT id, rollout_path FROM threads").fetchall()
            connection.close()
            missing = [(thread_id, path) for thread_id, path in rows if not Path(path).is_file()]
            print(f"Threads:      {len(rows)} indexed; {len(missing)} missing rollout files")
            if missing:
                problems += 1
                for thread_id, path in missing[:10]:
                    print(f"  MISSING {thread_id}: {path}")
        except (OSError, sqlite3.Error) as exc:
            print(f"Threads:      ERROR ({exc})")
            problems += 1
    managed_paths = [
        shared_state_path(registry, root),
        *[account_home(item, root) for item in registry["accounts"]],
    ]
    self_contained = all(path == root or root in path.parents for path in managed_paths)
    portability = "self-contained" if self_contained else "uses imported/external paths"
    print(f"Portable data: {portability}")
    return 1 if problems else 0


def reindex_command(args: list[str], root: Path | None = None) -> int:
    """Ask Codex to rescan shared rollout files and repair its thread index."""
    parser = argparse.ArgumentParser(
        prog="codex-as reindex",
        description="Rescan shared Codex rollouts after copying data to another machine",
    )
    parser.add_argument("account", nargs="?", help="Logged-in account used to start App Server")
    parser.add_argument("--timeout", type=float, default=60.0)
    options = parser.parse_args(args)
    if options.timeout <= 0:
        raise MuxError("--timeout must be positive")
    root = root or data_root()
    registry = load_registry(root=root)
    selector = options.account or registry.get("defaultAccount")
    if not selector:
        raise MuxError("no default account is configured")
    account = resolve_account(registry, selector)
    ensure_layout(registry, account, root)
    if not (account_home(account, root) / "auth.json").is_file():
        raise MuxError(f"{account['name']} is not logged in")

    total = 0
    with AppServerClient(registry, account, timeout=options.timeout, root=root) as client:
        request_id = 100
        for archived in (False, True):
            cursor: str | None = None
            while True:
                params: dict[str, Any] = {
                    "archived": archived,
                    "limit": 100,
                    "useStateDbOnly": False,
                }
                if cursor:
                    params["cursor"] = cursor
                client.send({"method": "thread/list", "id": request_id, "params": params})
                response = client.wait_for({request_id}, options.timeout)[request_id]
                result = _result_or_error(response, f"{account['name']} thread scan")
                data = result.get("data")
                if isinstance(data, list):
                    total += len(data)
                cursor_value = result.get("nextCursor")
                if not isinstance(cursor_value, str) or not cursor_value:
                    break
                cursor = cursor_value
                request_id += 1
            request_id += 1
    sessions = shared_state_path(registry, root) / "sessions"
    print(f"Reindexed {total} visible threads from {sessions}")
    return 0


def launch(selector: str, codex_args: list[str], root: Path | None = None) -> int:
    root = root or data_root()
    registry = load_registry(root=root)
    account = resolve_account(registry, selector)
    warnings = ensure_layout(registry, account, root)
    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)
    home = account_home(account, root)
    if not (home / "auth.json").is_file():
        raise MuxError(
            f"{account['name']} is not logged in; "
            f"run `codex-as login {account['name']}`"
        )
    command = [codex_binary(), *codex_config_args(registry, root), *codex_args]
    os.execvpe(command[0], command, account_env(account, root, registry))
    return 127


def _balanced_candidate(alias: str, probe: dict[str, Any]) -> dict[str, Any]:
    candidate: dict[str, Any] = {
        "alias": alias,
        "status": "unavailable",
        "remaining_percent": None,
        "longest_window_remaining_percent": None,
        "reason": None,
    }
    if probe.get("error"):
        candidate["reason"] = str(probe["error"])
        return candidate
    identity = probe.get("account")
    if not isinstance(identity, dict):
        candidate.update(status="authentication", reason="ChatGPT login is unavailable")
        return candidate
    if not probe.get("emailMatchesExpected", True):
        candidate.update(status="authentication", reason="authenticated identity does not match")
        return candidate
    rate_response = probe.get("rateLimits")
    if not isinstance(rate_response, dict):
        candidate["reason"] = str(
            probe.get("rateLimitsError") or "Codex rate limits are unavailable"
        )
        return candidate
    bucket = _main_rate_limit(rate_response)
    windows = []
    if isinstance(bucket, dict):
        for name in ("primary", "secondary"):
            window = bucket.get(name)
            used = window.get("usedPercent") if isinstance(window, dict) else None
            if isinstance(used, (int, float)) and not isinstance(used, bool):
                duration = window.get("windowDurationMins")
                windows.append(
                    (
                        float(duration) if isinstance(duration, (int, float)) else -1.0,
                        max(0.0, min(100.0, 100.0 - float(used))),
                    )
                )
    if not windows:
        candidate["reason"] = "Codex quota windows are unavailable"
        return candidate
    candidate["remaining_percent"] = min(remaining for _, remaining in windows)
    candidate["longest_window_remaining_percent"] = max(windows)[1]
    if candidate["remaining_percent"] <= 0:
        candidate.update(status="exhausted", reason="a Codex quota window is exhausted")
    else:
        candidate.update(status="eligible", reason="authenticated with available quota")
    return candidate


def _write_balanced_route(
    path_value: str | None,
    *,
    pool: list[str],
    mode: str,
    selected_alias: str | None,
    selection_reason: str,
    candidates: list[dict[str, Any]],
) -> None:
    if not path_value:
        return
    path = Path(path_value).expanduser()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    payload = {
        "format": "codex-multiplexer.route",
        "schema_version": 1,
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "mode": mode,
        "pool": pool,
        "selected_alias": selected_alias,
        "selection_reason": selection_reason,
        "candidates": candidates,
    }
    data = (json.dumps(payload, indent=2, sort_keys=False) + "\n").encode()
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except OSError as exc:
        raise MuxError(f"configuration_error: cannot create route record {path}: {exc}") from exc
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        with contextlib.suppress(OSError):
            path.unlink()
        raise


def balanced_main(argv: list[str], root: Path | None = None) -> int:
    if argv == ["--version"]:
        print(f"codex-multiplexer {TOOL_VERSION}")
        return 0
    if argv and argv[0] in {"-h", "--help", "help"}:
        print(
            "Usage: codex-lb [codex arguments...]\n\n"
            "Selects an authenticated account with the most available quota."
        )
        return 0
    root = root or data_root()
    registry = load_registry(root=root)
    accounts = list(registry["accounts"])
    pool = [str(account["name"]) for account in accounts]
    mode = "resume" if "resume" in argv else "fresh"
    route_path = os.environ.get("CODEX_MUX_ROUTE_RECORD")
    exclusion_value = os.environ.get(
        "CODEX_MULTIPLEXER_EXCLUDE_ACCOUNTS",
        os.environ.get("CODEX_MUX_EXCLUDE_ALIASES", ""),
    )
    excluded_selectors = {
        item.strip()
        for item in exclusion_value.split(",")
        if item.strip()
    }
    if not accounts:
        reason = "no accounts are configured"
        _write_balanced_route(
            route_path,
            pool=pool,
            mode=mode,
            selected_alias=None,
            selection_reason=reason,
            candidates=[],
        )
        raise MuxError(f"configuration_error: {reason}; run `codex-as add NAME`")
    excluded = set()
    unknown_exclusions = []
    for selector in sorted(excluded_selectors):
        try:
            excluded.add(resolve_account(registry, selector)["name"])
        except MuxError:
            unknown_exclusions.append(selector)
    if unknown_exclusions:
        reason = f"unknown exclusions: {', '.join(unknown_exclusions)}"
        candidates = [
            {
                "alias": name,
                "status": "configuration_error",
                "remaining_percent": None,
                "longest_window_remaining_percent": None,
                "reason": reason,
            }
            for name in pool
        ]
        _write_balanced_route(
            route_path,
            pool=pool,
            mode=mode,
            selected_alias=None,
            selection_reason="invalid account exclusion",
            candidates=candidates,
        )
        raise MuxError(f"configuration_error: {reason}")
    ensure_layout(registry, root=root)
    probes_by_name: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=min(len(accounts), 8)) as executor:
        futures = {
            executor.submit(
                probe_account,
                registry,
                account,
                refresh_token=False,
                timeout=DEFAULT_TIMEOUT,
                root=root,
            ): name
            for name, account in zip(pool, accounts)
        }
        for future in as_completed(futures):
            name = futures[future]
            try:
                probes_by_name[name] = future.result()
            except Exception as exc:
                probes_by_name[name] = {"name": name, "error": str(exc)}
    candidates = []
    for name in pool:
        candidate = _balanced_candidate(name, probes_by_name[name])
        if name in excluded:
            candidate.update(status="excluded", reason="excluded by the calling workflow")
        candidates.append(candidate)
    rank = sorted(
        (
            (index, candidate)
            for index, candidate in enumerate(candidates)
            if candidate["status"] == "eligible"
        ),
        key=lambda item: (
            -float(item[1]["remaining_percent"]),
            -float(item[1]["longest_window_remaining_percent"]),
            item[0],
        ),
    )
    selected: tuple[dict[str, Any], Any] | None = None
    for index, candidate in rank:
        lock = _account_process_lock(accounts[index], root)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock.close()
            candidate.update(status="busy", reason="another Codex process holds this account")
            continue
        os.set_inheritable(lock.fileno(), True)
        selected = (accounts[index], lock)
        candidate.update(status="selected", reason="highest available quota in the account pool")
        break
    if selected is None:
        if any(item["status"] == "authentication" for item in candidates):
            category = "authentication"
        elif all(item["status"] in {"exhausted", "excluded"} for item in candidates):
            category = "usage_limit"
        else:
            category = "service_unavailable"
        reason = "no configured account is currently selectable"
        _write_balanced_route(
            route_path,
            pool=pool,
            mode=mode,
            selected_alias=None,
            selection_reason=reason,
            candidates=candidates,
        )
        raise MuxError(f"{category}: {reason}")
    account, lock = selected
    alias = account["name"]
    _write_balanced_route(
        route_path,
        pool=pool,
        mode=mode,
        selected_alias=alias,
        selection_reason="selected the available account with the most remaining quota",
        candidates=candidates,
    )
    command = [codex_binary(), *codex_config_args(registry, root), *argv]
    env = account_env(account, root, registry)
    for name in ("OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN"):
        env.pop(name, None)
    os.execvpe(command[0], command, env)
    lock.close()
    return 127


def _duration(minutes: Any) -> str:
    if not isinstance(minutes, int):
        return "-"
    if minutes % 10080 == 0:
        return f"{minutes // 10080}w"
    if minutes % 1440 == 0:
        return f"{minutes // 1440}d"
    if minutes % 60 == 0:
        return f"{minutes // 60}h"
    return f"{minutes}m"


def _reset_time(timestamp: Any) -> str:
    if not isinstance(timestamp, int):
        return "-"
    delta = timestamp - int(time.time())
    if delta <= 0:
        return "now"
    days, remainder = divmod(delta, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes = remainder // 60
    if days:
        relative = f"{days}d {hours}h"
    elif hours:
        relative = f"{hours}h {minutes}m"
    else:
        relative = f"{minutes}m"
    local = dt.datetime.fromtimestamp(timestamp).astimezone().strftime("%b %d %H:%M")
    return f"{relative} ({local})"


def _reset_remaining(timestamp: Any) -> str:
    """Format a reset timestamp as a compact relative duration."""
    if not isinstance(timestamp, int):
        return "-"
    delta = timestamp - int(time.time())
    if delta <= 0:
        return "now"
    days, remainder = divmod(delta, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes = remainder // 60
    if days:
        return f"{days}d {hours}h" if hours else f"{days}d"
    if hours:
        return f"{hours}h {minutes}m" if minutes else f"{hours}h"
    return f"{minutes}m"


def _table(headers: list[str], rows: list[list[str]]) -> str:
    widths = [len(header) for header in headers]
    for row in rows:
        for index, value in enumerate(row):
            widths[index] = max(widths[index], len(value))
    lines = ["  ".join(value.ljust(widths[index]) for index, value in enumerate(headers))]
    lines.append("  ".join("-" * width for width in widths))
    for row in rows:
        lines.append("  ".join(value.ljust(widths[index]) for index, value in enumerate(row)))
    return "\n".join(lines)


def _remaining_usage_bar(used_percent: Any, width: int = 10) -> str:
    """Format the available percentage as a compact, partially filled bar."""
    if (
        not isinstance(used_percent, (int, float))
        or isinstance(used_percent, bool)
        or width <= 0
    ):
        return "-"
    remaining = max(0.0, min(100.0, 100.0 - float(used_percent)))
    eighths = int((remaining * width * 8 / 100) + 0.5)
    full_cells, partial_eighths = divmod(eighths, 8)
    partial_blocks = ("", "▏", "▎", "▍", "▌", "▋", "▊", "▉")
    bar = "█" * min(full_cells, width)
    if full_cells < width and partial_eighths:
        bar += partial_blocks[partial_eighths]
    bar += "░" * (width - len(bar))
    return f"[{bar}] {remaining:g}%"


def _main_rate_limit(rate_response: dict[str, Any]) -> dict[str, Any] | None:
    buckets = rate_response.get("rateLimitsByLimitId")
    if isinstance(buckets, dict):
        preferred = buckets.get("codex")
        if isinstance(preferred, dict):
            return preferred
        for bucket in buckets.values():
            if isinstance(bucket, dict) and str(bucket.get("limitId", "")).casefold() == "codex":
                return bucket
        return next((bucket for bucket in buckets.values() if isinstance(bucket, dict)), None)
    single = rate_response.get("rateLimits")
    return single if isinstance(single, dict) else None


def _smi_rows(probes: list[dict[str, Any]]) -> list[list[str]]:
    """Build the compact, one-row-per-account usage table."""
    rows: list[list[str]] = []
    for probe in probes:
        name = probe["name"]
        if probe.get("error"):
            rows.append([name, "-", "-", "-", f"ERROR: {probe['error']}"])
            continue
        identity = probe.get("account")
        if not isinstance(identity, dict):
            rows.append([name, "not logged in", "-", "-", "LOGIN REQUIRED"])
            continue
        email = str(identity.get("email") or identity.get("type") or "-")
        if not probe.get("emailMatchesExpected", True):
            email += " [MISMATCH]"
        rate_response = probe.get("rateLimits")
        if not isinstance(rate_response, dict):
            rows.append([name, email, "-", "-", "ERROR"])
            continue
        bucket = _main_rate_limit(rate_response)
        window = bucket.get("primary") if isinstance(bucket, dict) else None
        if not isinstance(window, dict) and isinstance(bucket, dict):
            window = bucket.get("secondary")
        window = window if isinstance(window, dict) else {}
        used = window.get("usedPercent")
        reset_summary = rate_response.get("rateLimitResetCredits")
        reset_count = (
            str(reset_summary.get("availableCount")) if isinstance(reset_summary, dict) else "-"
        )
        rows.append(
            [
                name,
                email,
                _remaining_usage_bar(used),
                reset_count,
                _reset_remaining(window.get("resetsAt")),
            ]
        )
    return rows


def _smi_detailed_rows(probes: list[dict[str, Any]]) -> list[list[str]]:
    rows: list[list[str]] = []
    for probe in probes:
        name = probe["name"]
        if probe.get("error"):
            rows.append([name, "-", "-", "ERROR", "-", "-", "-", str(probe["error"])])
            continue
        identity = probe.get("account")
        if not isinstance(identity, dict):
            rows.append([name, "not logged in", "-", "-", "-", "-", "-", "LOGIN REQUIRED"])
            continue
        email = str(identity.get("email") or identity.get("type") or "-")
        plan = str(identity.get("planType") or "-")
        mismatch = "EMAIL MISMATCH" if not probe.get("emailMatchesExpected", True) else "ok"
        rate_response = probe.get("rateLimits")
        if not isinstance(rate_response, dict):
            rows.append(
                [
                    name,
                    email,
                    plan,
                    "-",
                    "-",
                    "-",
                    "-",
                    probe.get("rateLimitsError") or mismatch,
                ]
            )
            continue
        buckets = rate_response.get("rateLimitsByLimitId")
        if isinstance(buckets, dict) and buckets:
            bucket_items = list(buckets.items())
        else:
            single = rate_response.get("rateLimits")
            bucket_items = (
                [(str(single.get("limitId") or "codex"), single)]
                if isinstance(single, dict)
                else []
            )
        reset_summary = rate_response.get("rateLimitResetCredits")
        reset_count = (
            str(reset_summary.get("availableCount")) if isinstance(reset_summary, dict) else "-"
        )
        if not bucket_items:
            rows.append([name, email, plan, "-", "-", "-", reset_count, mismatch])
            continue
        first = True
        for limit_id, bucket in bucket_items:
            if not isinstance(bucket, dict):
                continue
            display_name = str(bucket.get("limitName") or bucket.get("limitId") or limit_id)
            windows = [("primary", bucket.get("primary")), ("secondary", bucket.get("secondary"))]
            present_windows = [
                (label, window) for label, window in windows if isinstance(window, dict)
            ]
            if not present_windows:
                present_windows = [("-", {})]
            for window_label, window in present_windows:
                used = window.get("usedPercent")
                rows.append(
                    [
                        name if first else "",
                        email if first else "",
                        plan if first else "",
                        display_name,
                        f"{window_label}:{_duration(window.get('windowDurationMins'))}",
                        _remaining_usage_bar(used),
                        reset_count if first else "",
                        mismatch if first else _reset_time(window.get("resetsAt")),
                    ]
                )
                if first:
                    rows[-1][-1] = f"{mismatch}; reset {_reset_time(window.get('resetsAt'))}"
                first = False
    return rows


def smi_main(argv: list[str], root: Path | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="codex-smi", description="Show Codex account identities and limits"
    )
    parser.add_argument(
        "--version", action="version", version=f"codex-multiplexer {TOOL_VERSION}"
    )
    parser.add_argument("accounts", nargs="*", help="Account names or emails (default: all)")
    parser.add_argument("--json", action="store_true", help="Emit structured JSON")
    parser.add_argument(
        "--info", action="store_true", help="Show account and session command examples"
    )
    parser.add_argument(
        "--details", action="store_true", help="Show plan and every quota limit window"
    )
    parser.add_argument("--usage", action="store_true", help="Also query token-activity summaries")
    parser.add_argument("--no-refresh", action="store_true", help="Do not refresh ChatGPT tokens")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    options = parser.parse_args(argv)
    if options.info:
        print(SMI_INFO)
        return 0
    if options.timeout <= 0:
        raise MuxError("--timeout must be positive")
    root = root or data_root()
    registry = load_registry(root=root)
    selected = (
        [resolve_account(registry, selector) for selector in options.accounts]
        if options.accounts
        else list(registry["accounts"])
    )
    ensure_layout(registry, root=root)
    probes_by_name: dict[str, dict[str, Any]] = {}
    workers = min(max(1, len(selected)), 8)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(
                probe_account,
                registry,
                account,
                include_usage=options.usage,
                refresh_token=not options.no_refresh,
                timeout=options.timeout,
                root=root,
            ): account["name"]
            for account in selected
        }
        for future in as_completed(futures):
            name = futures[future]
            try:
                probes_by_name[name] = future.result()
            except Exception as exc:
                probes_by_name[name] = {"name": name, "error": str(exc)}
    probes = [probes_by_name[item["name"]] for item in selected]
    if options.json:
        print(json.dumps(probes, indent=2, sort_keys=False))
    elif options.details:
        headers = [
            "ACCOUNT",
            "EMAIL",
            "PLAN",
            "LIMIT",
            "WINDOW",
            "REMAINING USAGE",
            "RESETS",
            "STATUS / NEXT RESET",
        ]
        print(_table(headers, _smi_detailed_rows(probes)))
    else:
        headers = ["ACCOUNT", "EMAIL", "REMAINING USAGE", "RESETS", "RESET IN"]
        print(_table(headers, _smi_rows(probes)))
    return 1 if any(item.get("error") for item in probes) else 0


def as_usage() -> None:
    print(
        """Usage:
  codex-as add ACCOUNT [--expect EMAIL] [--device-auth]
  codex-as login ACCOUNT [--device-auth]
  codex-as logout ACCOUNT
  codex-as accounts
  codex-as default ACCOUNT
  codex-as doctor
  codex-as reindex [ACCOUNT]
  codex-as import-omarchy
  codex-as ACCOUNT [codex arguments...]

Examples:
  codex-as add acc1
  codex-as add acc2 --device-auth
  codex-as acc1 --yolo
  codex-as acc2 --yolo resume SESSION_ID
  codex-smi

Credentials remain in separate CODEX_HOME directories. Chat indexes and runtime
state use one shared sqlite_home, so every configured account can resume every
indexed local session."""
    )


def as_main(argv: list[str], root: Path | None = None) -> int:
    if argv == ["--version"]:
        print(f"codex-multiplexer {TOOL_VERSION}")
        return 0
    if not argv or argv[0] in {"-h", "--help", "help"}:
        as_usage()
        return 0
    command, rest = argv[0], argv[1:]
    if command == "add":
        return add_account(rest, root)
    if command == "login":
        return login_command(rest, root)
    if command == "logout":
        return logout_command(rest, root)
    if command in {"accounts", "list"}:
        if rest:
            raise MuxError(f"{command} takes no arguments")
        return list_accounts(root)
    if command == "default":
        return set_default(rest, root)
    if command == "doctor":
        if rest:
            raise MuxError("doctor takes no arguments")
        return doctor(root)
    if command == "reindex":
        return reindex_command(rest, root)
    if command == "import-omarchy":
        return import_omarchy(rest, root)
    return launch(command, rest, root)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    invocation = Path(sys.argv[0]).name
    try:
        if invocation == "codex-smi":
            if argv == ["--version"]:
                print(f"codex-multiplexer {TOOL_VERSION}")
                return 0
            return smi_main(argv)
        if invocation == "codex-as":
            return as_main(argv)
        if invocation == "codex-lb":
            return balanced_main(argv)
        if argv and argv[0] in {"as", "smi", "lb"}:
            mode = argv.pop(0)
            if mode == "as":
                return as_main(argv)
            if mode == "smi":
                return smi_main(argv)
            return balanced_main(argv)
        print("Usage: codex-mux {as|smi|lb} ...", file=sys.stderr)
        return 2
    except MuxError as exc:
        print(f"{invocation}: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print(file=sys.stderr)
        return 130


def as_entry() -> None:
    raise SystemExit(_console_entry(as_main, "codex-as"))


def smi_entry() -> None:
    raise SystemExit(_console_entry(smi_main, "codex-smi"))


def lb_entry() -> None:
    raise SystemExit(_console_entry(balanced_main, "codex-lb"))


def mux_entry() -> None:
    raise SystemExit(main())


def _console_entry(handler, invocation: str) -> int:
    try:
        return handler(list(sys.argv[1:]))
    except MuxError as exc:
        print(f"{invocation}: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print(file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
