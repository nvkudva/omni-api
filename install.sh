#!/bin/sh
# Install the `omni` command, the way Ollama installs `ollama`:
#
#   curl -fsSL https://raw.githubusercontent.com/nvkudva/omni-api/main/install.sh | sh
#
# It puts uv in place (the only prerequisite), installs omni as a uv tool, and
# leaves a global `omni` on your PATH. Models are not downloaded here: `omni init`
# pulls the Ollama ones, and the speech/image/video models download on first use.
set -eu

REPO="https://github.com/nvkudva/omni-api"
REF="main"
SOURCE=""
VERSION=""
BIN_DIR="${OMNI_BIN_DIR:-${UV_TOOL_BIN_DIR:-$HOME/.local/bin}}"
RUN_INIT=0
MODIFY_PATH=0

say() { printf '%s\n' "$*"; }
die() { printf 'omni-install: %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<'EOF'
Install the omni CLI.

  curl -fsSL https://raw.githubusercontent.com/nvkudva/omni-api/main/install.sh | sh

Options:
  --from SPEC      Install from a uv tool spec instead of the main branch
                   (a local path, a git URL, or a package name).
  --version TAG    Install the release tag TAG (v0.2.0 counts as 0.2.0).
  --init           Run `omni init` afterwards: install Ollama and pull the
                   chat, decide and embed models (a few GB).
  --bin-dir DIR    Where the `omni` command lands (default ~/.local/bin).
  --modify-path    Append that directory to your shell's startup file when it
                   is missing from PATH.
  -h, --help       Show this text.
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --from) [ $# -ge 2 ] || die "--from needs a value"; SOURCE="$2"; shift 2 ;;
    --version) [ $# -ge 2 ] || die "--version needs a value"; VERSION="$2"; shift 2 ;;
    --bin-dir) [ $# -ge 2 ] || die "--bin-dir needs a value"; BIN_DIR="$2"; shift 2 ;;
    --init) RUN_INIT=1; shift ;;
    --modify-path) MODIFY_PATH=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option: $1 (try --help)" ;;
  esac
done

have() { command -v "$1" >/dev/null 2>&1; }

# --- platform ---------------------------------------------------------------
os="$(uname -s)"
arch="$(uname -m)"
if [ "$os" != "Darwin" ]; then
  die "omni needs macOS: its runtime is MLX, which has no Linux build. Nothing was changed."
fi
if [ "$arch" != "arm64" ]; then
  die "omni needs Apple Silicon: MLX has no Intel-Mac build. Nothing was changed."
fi

# --- uv ---------------------------------------------------------------------
if ! have uv; then
  say "uv is not installed; installing it first (omni is distributed as a uv tool)."
  if have brew; then
    brew install uv || die "brew install uv failed"
  elif have curl; then
    curl -LsSf https://astral.sh/uv/install.sh | sh || die "could not install uv"
    PATH="$HOME/.local/bin:$PATH"
    export PATH
  else
    die "no brew or curl here; install uv by hand (https://docs.astral.sh/uv/) and rerun"
  fi
fi
have uv || die "uv is still not on PATH; add ~/.local/bin to PATH and rerun"

# --- install ----------------------------------------------------------------
spec="$SOURCE"
if [ -z "$spec" ]; then
  if [ -n "$VERSION" ]; then
    tag="$VERSION"
    case "$tag" in v*) ;; *) tag="v$tag" ;; esac
    spec="git+$REPO@$tag"
  else
    spec="git+$REPO@$REF"
  fi
fi

case "$spec" in
  git+*) have git || die "installing from git needs git; install it or pass --from a local path" ;;
esac

say "installing omni from $spec"
UV_TOOL_BIN_DIR="$BIN_DIR"
export UV_TOOL_BIN_DIR
uv tool install --force --python 3.12 "$spec" || die "uv tool install failed"

# --- check ------------------------------------------------------------------
[ -x "$BIN_DIR/omni" ] || die "uv finished but $BIN_DIR/omni is missing"
installed_version="$("$BIN_DIR/omni" --version)" || die "$BIN_DIR/omni did not run"
say "installed: $installed_version"

# --- PATH -------------------------------------------------------------------
on_path=0
case ":${PATH}:" in *":${BIN_DIR}:"*) on_path=1 ;; esac
if [ "$on_path" -eq 0 ]; then
  line="export PATH=\"$BIN_DIR:\$PATH\""
  if [ "$MODIFY_PATH" -eq 1 ]; then
    case "${SHELL:-}" in
      */zsh) rc="$HOME/.zshrc" ;;
      */bash) rc="$HOME/.bashrc" ;;
      *) rc="" ;;
    esac
    if [ -n "$rc" ] && ! grep -F "$line" "$rc" >/dev/null 2>&1; then
      printf '\n# omni\n%s\n' "$line" >> "$rc"
      say "added $BIN_DIR to PATH in $rc (open a new terminal to use it)"
    else
      say "add this to your shell startup file: $line"
    fi
  else
    say "note: $BIN_DIR is not on PATH yet. Add it, or rerun with --modify-path:"
    say "  $line"
  fi
fi

# --- init -------------------------------------------------------------------
if [ "$RUN_INIT" -eq 1 ]; then
  say ""
  "$BIN_DIR/omni" init || die "omni init failed; fix the step above and run \`omni init\` again"
fi

say ""
say "next:"
say "  omni init     # once: install Ollama and pull chat, decide, embed"
say "  omni up       # start the gateway in the background"
say "  omni status   # what is running"
say "then open http://127.0.0.1:10000/"
say ""
say "uninstall: uv tool uninstall omni-api"
