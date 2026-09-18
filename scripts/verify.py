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


def verify_versions() -> None:
    source = (REPO / "codex_mux.py").read_text(encoding="utf-8")
    project = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    source_match = re.search(r'^TOOL_VERSION = "([^"]+)"$', source, re.MULTILINE)
    project_match = re.search(r'^version = "([^"]+)"$', project, re.MULTILINE)
    if not source_match or not project_match or source_match.group(1) != project_match.group(1):
        raise RuntimeError("codex_mux.py and pyproject.toml versions do not match")


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
    verify_versions()
    verify_no_credentials()
    for script in [REPO / "install.sh", *sorted((REPO / "bin").iterdir())]:
        run(["sh", "-n", str(script)])
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"], env=env)
    verify_installation()
    print("All repository verification checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
