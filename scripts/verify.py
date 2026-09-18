#!/usr/bin/env python3
"""Run dependency-free repository and installation verification."""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
SENSITIVE_PATTERNS = {
    "OpenAI API token": re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    "GitHub token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    "private key": re.compile(r"-----BEGIN (?:EC |OPENSSH |RSA )?PRIVATE KEY-----"),
}
EMAIL_PATTERN = re.compile(r"\b[A-Z0-9._%+-]+@([A-Z0-9.-]+\.[A-Z]{2,})\b", re.IGNORECASE)
SAFE_EMAIL_DOMAINS = {"example.com", "example.test", "example.invalid"}
SAFE_EMAIL_SUFFIXES = (".example", ".test", ".invalid")


def run(command: list[str], *, env: dict[str, str] | None = None) -> None:
    subprocess.run(command, cwd=REPO, env=env, check=True)


def verify_python_sources() -> None:
    paths = [
        REPO / "codex_mux.py",
        *REPO.glob("tests/*.py"),
        *REPO.glob("scripts/*.py"),
    ]
    for path in sorted(paths):
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def verify_json_documents() -> None:
    schema = json.loads((REPO / "schema" / "registry.schema.json").read_text(encoding="utf-8"))
    example = json.loads((REPO / "examples" / "registry.example.json").read_text(encoding="utf-8"))
    if schema.get("properties", {}).get("version", {}).get("const") != example.get("version"):
        raise RuntimeError("example registry version does not match its schema")


def verify_omarchy_plugin() -> None:
    manifest_path = REPO / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    required = {"schemaVersion", "id", "name", "version", "kinds", "entryPoints"}
    if not required.issubset(manifest):
        raise RuntimeError("Omarchy plugin manifest is missing required fields")
    if manifest["schemaVersion"] != 1:
        raise RuntimeError("Omarchy plugin manifest schemaVersion must be 1")
    if str(manifest["id"]).startswith("omarchy."):
        raise RuntimeError("third-party Omarchy plugin uses the reserved namespace")
    if "bar-widget" not in manifest["kinds"]:
        raise RuntimeError("Omarchy plugin must declare the bar-widget kind")
    entry = manifest.get("entryPoints", {}).get("barWidget")
    if not isinstance(entry, str) or entry.startswith("/") or ".." in entry:
        raise RuntimeError("Omarchy bar widget entry point is unsafe")
    entry_path = REPO / entry
    if not entry_path.is_file():
        raise RuntimeError("Omarchy bar widget entry point is missing")
    qml = entry_path.read_text(encoding="utf-8")
    for expected in ('["codex-mux", "omarchy"', "remainingPercent", "resetsAt"):
        if expected not in qml:
            raise RuntimeError(f"Omarchy panel is missing required integration: {expected}")


def verify_omarchy_installer() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        base = Path(temporary)
        fake_bin = base / "bin"
        fake_bin.mkdir()
        fake_omarchy = fake_bin / "omarchy"
        fake_omarchy.write_text(
            """#!/bin/sh
if [ "$1" = plugin ] && [ "$2" = list ]; then
  printf '[{"id":"ibnabeeali.codex-multiplexer"}]\\n'
fi
exit 0
""",
            encoding="utf-8",
        )
        fake_shell = fake_bin / "omarchy-shell"
        fake_shell.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        fake_mux = fake_bin / "codex-mux"
        fake_mux.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        for script in (fake_omarchy, fake_shell, fake_mux):
            script.chmod(0o755)

        env = os.environ.copy()
        env["HOME"] = str(base / "home")
        env["XDG_CONFIG_HOME"] = str(base / "config")
        env["PATH"] = str(fake_bin) + os.pathsep + env.get("PATH", "")
        installer = REPO / "install-omarchy-plugin.sh"
        run(["sh", str(installer)], env=env)
        run(["sh", str(installer)], env=env)

        target = base / "config" / "omarchy" / "plugins" / "ibnabeeali.codex-multiplexer"
        if (target / "manifest.json").read_bytes() != (REPO / "manifest.json").read_bytes():
            raise RuntimeError("Omarchy installer did not install the current manifest")
        if (target / "omarchy" / "Panel.qml").read_bytes() != (
            REPO / "omarchy" / "Panel.qml"
        ).read_bytes():
            raise RuntimeError("Omarchy installer did not install the current panel")


def verify_versions() -> None:
    source = (REPO / "codex_mux.py").read_text(encoding="utf-8")
    project = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    manifest = json.loads((REPO / "manifest.json").read_text(encoding="utf-8"))
    source_match = re.search(r'^TOOL_VERSION = "([^"]+)"$', source, re.MULTILINE)
    project_match = re.search(r'^version = "([^"]+)"$', project, re.MULTILINE)
    if not source_match or not project_match or source_match.group(1) != project_match.group(1):
        raise RuntimeError("codex_mux.py and pyproject.toml versions do not match")
    if source_match.group(1) != manifest.get("version"):
        raise RuntimeError("codex_mux.py and manifest.json versions do not match")


def verify_no_credentials() -> None:
    forbidden_names = {"auth.json", "credentials.json"}
    for path in REPO.rglob("*"):
        if not path.is_file() or ".git" in path.parts:
            continue
        if path.name in forbidden_names:
            raise RuntimeError(f"credential file must not be committed: {path}")
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for label, pattern in SENSITIVE_PATTERNS.items():
            if pattern.search(text):
                raise RuntimeError(f"possible {label} in repository: {path}")
        for domain in EMAIL_PATTERN.findall(text):
            normalized_domain = domain.casefold()
            if (
                normalized_domain not in SAFE_EMAIL_DOMAINS
                and not normalized_domain.endswith(SAFE_EMAIL_SUFFIXES)
            ):
                raise RuntimeError(f"possible personal email address in repository: {path}")


def verify_installation() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        prefix = Path(temporary) / "custom-prefix"
        env = os.environ.copy()
        env["CODEX_MULTIPLEXER_INSTALL_ROOT"] = str(prefix)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        run(["sh", str(REPO / "install.sh")], env=env)
        run([str(prefix / "bin" / "codex-as"), "--version"], env=env)
        run([str(prefix / "bin" / "codex-smi"), "--version"], env=env)
        installed_schema = (
            prefix
            / "share"
            / "doc"
            / "codex-multiplexer"
            / "schema"
            / "registry.schema.json"
        )
        if not installed_schema.is_file():
            raise RuntimeError("installer did not install the registry schema")
        security_guide = prefix / "share" / "doc" / "codex-multiplexer" / "SECURITY.md"
        if not security_guide.is_file():
            raise RuntimeError("installer did not install the security guidance")
        license_file = prefix / "share" / "doc" / "codex-multiplexer" / "LICENSE"
        if not license_file.is_file():
            raise RuntimeError("installer did not install the license")


def main() -> int:
    verify_python_sources()
    verify_json_documents()
    verify_omarchy_plugin()
    verify_versions()
    verify_no_credentials()
    for script in [
        REPO / "install.sh",
        REPO / "install-omarchy-plugin.sh",
        *sorted((REPO / "bin").iterdir()),
    ]:
        run(["sh", "-n", str(script)])
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"], env=env)
    verify_installation()
    verify_omarchy_installer()
    print("All repository verification checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
