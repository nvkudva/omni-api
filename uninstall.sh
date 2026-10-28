#!/bin/sh
# Remove the `omni` command installed by install.sh:
#
#   curl -fsSL https://raw.githubusercontent.com/nvkudva/omni-api/main/uninstall.sh | sh
#
# Stops the gateway, then removes the uv tool. Your config and state in ~/.omni-api are kept
# unless you pass --purge. Downloaded models (Ollama, Hugging Face) are never touched.
# A Homebrew install is removed with `brew uninstall omni`, not with this script.
set -u

PURGE=0
OMNI_STATE="${OMNI_HOME:-$HOME/.omni-api}"
BIN_DIR="${OMNI_BIN_DIR:-${UV_TOOL_BIN_DIR:-$HOME/.local/bin}}"

say() { printf '%s\n' "$*"; }
die() {
  printf 'omni-uninstall: %s\n' "$1" >&2
  shift
  for fix do printf '  fix: %s\n' "$fix" >&2; done
  exit 1
}
have() { command -v "$1" >/dev/null 2>&1; }

while [ $# -gt 0 ]; do
  arg=$1
  shift
  case "$arg" in
    --purge) PURGE=1 ;;
    --bin-dir)
      [ $# -ge 1 ] || die "--bin-dir needs a value" "example: --bin-dir \"\$HOME/bin\""
      BIN_DIR=$1
      shift
      ;;
    -h|--help)
      cat <<'USAGE'
Uninstall the omni CLI.

  --bin-dir DIR  Where install.sh put `omni` (default ~/.local/bin).
  --purge    Also delete ~/.omni-api (or $OMNI_HOME): config, pins, sockets, logs.
  -h, --help Show this text.

Models stay on disk: Ollama keeps them in ~/.ollama, Hugging Face in ~/.cache/huggingface.
USAGE
      exit 0
      ;;
    *) die "unknown option: $arg" "run with --help to see the options" ;;
  esac
done

omni_bin=""
if have omni; then
  omni_bin="$(command -v omni)"
elif [ -x "$BIN_DIR/omni" ]; then
  omni_bin="$BIN_DIR/omni"
fi
if [ -n "$omni_bin" ]; then
  say "stopping omni..."
  "$omni_bin" down < /dev/null >/dev/null 2>&1 || true
fi

if have brew && brew list --formula omni >/dev/null 2>&1; then
  say "omni is installed by Homebrew; remove it with: brew uninstall omni"
fi

if have uv; then
  if uv tool list 2>/dev/null | grep -q '^omni-api '; then
    uv tool uninstall omni-api || die "uv could not remove omni-api" "run: uv tool uninstall omni-api"
    say "removed the omni command"
  else
    say "no uv-installed omni-api found; nothing to remove"
  fi
else
  say "uv not found; if omni was installed as a uv tool, install uv and rerun"
  say "  https://docs.astral.sh/uv/"
fi

if [ "$PURGE" -eq 1 ]; then
  case "$OMNI_STATE" in
    /*) ;;
    *) die "refusing to delete '$OMNI_STATE'" "OMNI_HOME must be omni's state directory, an absolute path (default ~/.omni-api)" ;;
  esac
  if [ -d "$OMNI_STATE" ]; then
    resolved_state="$(cd "$OMNI_STATE" && pwd -P)"
    resolved_home="$(cd "$HOME" && pwd -P)"
    case "$resolved_state" in
      /|"$resolved_home"|"$(dirname "$resolved_home")") die "refusing to delete '$OMNI_STATE'" "point OMNI_HOME at omni's state directory, not your home or a parent of it" ;;
    esac
  fi
  rm -rf "$OMNI_STATE"
  say "deleted $OMNI_STATE"
else
  say "kept $OMNI_STATE (config and state); rerun with --purge to delete it"
fi
