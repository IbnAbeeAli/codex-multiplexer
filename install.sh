#!/bin/sh
set -eu

repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
install_root=${CODEX_MULTIPLEXER_INSTALL_ROOT:-"$HOME/.local"}
bin_dir="$install_root/bin"
libexec_dir="$install_root/libexec/codex-multiplexer"
doc_dir="$install_root/share/doc/codex-multiplexer"

if ! command -v python3 >/dev/null 2>&1; then
  printf 'error: Python 3.10 or newer is required.\n' >&2
  exit 1
fi
python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' || {
  printf 'error: Python 3.10 or newer is required.\n' >&2
  exit 1
}
if ! command -v codex >/dev/null 2>&1; then
  printf 'warning: Codex CLI is not currently on PATH; install it before adding accounts.\n' >&2
fi

install -d -m 755 \
  "$bin_dir" \
  "$libexec_dir" \
  "$doc_dir/schema" \
  "$doc_dir/docs" \
  "$doc_dir/examples"
install -m 755 "$repo_dir/codex_mux.py" "$libexec_dir/codex_mux.py"
install -m 755 "$repo_dir/bin/codex-as" "$bin_dir/codex-as"
install -m 755 "$repo_dir/bin/codex-smi" "$bin_dir/codex-smi"
install -m 755 "$repo_dir/bin/codex-lb" "$bin_dir/codex-lb"
install -m 755 "$repo_dir/bin/codex-mux" "$bin_dir/codex-mux"
install -m 644 "$repo_dir/schema/registry.schema.json" "$doc_dir/schema/registry.schema.json"
install -m 644 "$repo_dir/examples/registry.example.json" "$doc_dir/examples/registry.example.json"
install -m 644 "$repo_dir/README.md" "$doc_dir/README.md"
install -m 644 "$repo_dir/SECURITY.md" "$doc_dir/SECURITY.md"
install -m 644 "$repo_dir/LICENSE" "$doc_dir/LICENSE"
for doc in "$repo_dir"/docs/*.md; do
  install -m 644 "$doc" "$doc_dir/docs/$(basename "$doc")"
done

"$bin_dir/codex-as" --version >/dev/null
"$bin_dir/codex-smi" --version >/dev/null
"$bin_dir/codex-lb" --version >/dev/null
"$bin_dir/codex-mux" omarchy --version >/dev/null

printf 'Installed codex-as, codex-smi, codex-lb, and codex-mux in %s\n' "$bin_dir"
printf 'Installed documentation and registry schema in %s\n' "$doc_dir"
case ":$PATH:" in
  *":$bin_dir:"*) ;;
  *) printf 'Add %s to PATH before using the commands.\n' "$bin_dir" ;;
esac
