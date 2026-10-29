#!/bin/sh
# Install the `omni` command, the way Ollama installs `ollama`:
#
#   curl -fsSL https://raw.githubusercontent.com/nvkudva/omni-api/main/install.sh | sh
#
# It puts uv in place (the only prerequisite), installs omni as a uv tool, and
# leaves a global `omni` on your PATH, with every runtime the playground can start (speech,
# image, video). Models are not downloaded here: `omni init` downloads chat, decide, embed,
# speech-to-text and text-to-speech; image and video download on first use.
#
# Every failure prints a `fix:` line. Nothing is written until the checks pass.
set -u

REPO="https://github.com/nvkudva/omni-api"
DEFAULT_REF="main"
SOURCE=""
VERSION=""
RUN_INIT=0
MODIFY_PATH=0
MEDIA=0
BIN_DIR="${OMNI_BIN_DIR:-${UV_TOOL_BIN_DIR:-$HOME/.local/bin}}"

say() { printf '%s\n' "$*"; }
warn() { printf 'omni-install: %s\n' "$*" >&2; }
die() {
  message=$1
  shift
  printf 'omni-install: %s\n' "$message" >&2
  for fix do printf '  fix: %s\n' "$fix" >&2; done
  exit 1
}
have() { command -v "$1" >/dev/null 2>&1; }

usage() {
  cat <<'EOF'
Install the omni CLI.

  curl -fsSL https://raw.githubusercontent.com/nvkudva/omni-api/main/install.sh | sh

Options:
  --from SPEC      Install from a local path, a git+ or https URL, or a package
                   name instead of the main branch. Paths and URLs also get the
                   media extra where it applies; not with --version.
  --version TAG    Install the release tag TAG (v0.2.0 counts as 0.2.0).
  --init           Run `omni init` afterwards: install Ollama and pull the
                   chat, decide and embed models (a few GB).
  --media          Also install the speech, image and video workers. Already the
                   default on Apple Silicon; on Linux it is opt-in and needs
                   glibc 2.28 or newer (Ubuntu 22.04, Debian 12 and later).
  --bin-dir DIR    Where the `omni` command lands (default ~/.local/bin).
  --modify-path    Append that directory to your shell's startup file when it
                   is missing from PATH.
  -h, --help       Show this text.

Without --media you get chat and embed through Ollama, and decide on ONNX
Runtime. The image worker renders on Metal, so it runs on macOS only.
EOF
}

# --- arguments --------------------------------------------------------------
while [ $# -gt 0 ]; do
  case "$1" in
    --from)
      [ $# -ge 2 ] || die "--from needs a value" "example: --from /path/to/omni-api"
      SOURCE=$2
      shift 2
      ;;
    --version)
      [ $# -ge 2 ] || die "--version needs a value" "example: --version 0.1.0"
      VERSION=$2
      shift 2
      ;;
    --bin-dir)
      [ $# -ge 2 ] || die "--bin-dir needs a value" "example: --bin-dir \"\$HOME/bin\""
      BIN_DIR=$2
      shift 2
      ;;
    --init) RUN_INIT=1; shift ;;
    --media) MEDIA=1; shift ;;
    --modify-path) MODIFY_PATH=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option: $1" "run with --help to see the options" ;;
  esac
done

case "$BIN_DIR" in
  /*) ;;
  *) BIN_DIR="$(pwd)/$BIN_DIR" ;;
esac

WORK=""
cleanup() {
  [ -z "${heartbeat_pid:-}" ] || kill "$heartbeat_pid" 2>/dev/null
  [ -z "$WORK" ] || rm -rf "$WORK"
}
trap cleanup EXIT
trap 'cleanup; exit 130' INT TERM HUP

WORK="$(mktemp -d "${TMPDIR:-/tmp}/omni-install.XXXXXX" 2>/dev/null)" || die \
  "could not create a temporary directory in ${TMPDIR:-/tmp}" \
  "check that ${TMPDIR:-/tmp} exists and is writable, then rerun"
LOG="$WORK/uv.log"

if [ "$(id -u)" -eq 0 ]; then
  warn "running as root, so omni lands in /var/root and only root can run it"
  warn "  fix: rerun without sudo, as the user who will use omni"
fi

# --- platform ---------------------------------------------------------------
os="$(uname -s)"
arch="$(uname -m)"
EXTRAS=""
case "$os" in
  Darwin)
    if [ "$arch" = "arm64" ]; then
      EXTRAS="[media]"
      minimum_kb=2097152
    elif [ "$MEDIA" -eq 1 ]; then
      die "the media extra needs Apple Silicon: torch has no Intel-Mac wheel" \
        "drop --media to install chat, decide and embed only"
    else
      say "Intel Mac: installing chat, decide and embed only."
      say "            image, video and the speech workers need Apple Silicon."
      minimum_kb=524288
    fi
    ;;
  Linux)
    case "$arch" in
      x86_64|aarch64) ;;
      *)
        [ "$MEDIA" -eq 0 ] || die "torch has no Linux wheel for $arch, so --media cannot install here" \
          "drop --media to install chat, decide and embed only"
        ;;
    esac
    if [ "$MEDIA" -eq 1 ]; then
      glibc="$(getconf GNU_LIBC_VERSION 2>/dev/null | awk '{print $2}')"
      if [ -z "${glibc:-}" ]; then
        die "this Linux has no glibc (musl, as in Alpine), and the media wheels need it" \
          "drop --media to install chat, decide and embed only" \
          "or run a glibc distribution in a container"
      fi
      if [ "$(printf '%s\n2.28\n' "$glibc" | sort -V | head -1)" != "2.28" ]; then
        die "the media wheels need glibc 2.28 or newer; this is $glibc" \
          "upgrade to a distribution with glibc 2.28 or newer (Ubuntu 22.04, Debian 12), then rerun" \
          "or drop --media to install chat, decide and embed only"
      fi
      EXTRAS="[media]"
      minimum_kb=2097152
      say "Linux + media: installing every role except image, which needs Metal (macOS)."
    else
      say "Linux: installing chat, decide and embed. Add --media for speech, image"
      say "       and video (torch/diffusers; needs glibc 2.28 or newer)."
      minimum_kb=524288
    fi
    ;;
  *)
    die "omni does not support $os" \
      "run this on macOS (Apple Silicon) or Linux" \
      "chat, decide and embed need only Ollama and Python 3.12"
    ;;
esac

available_kb="$(df -Pk "$HOME" 2>/dev/null | awk 'NR==2 {print $4}')"
if [ -n "${available_kb:-}" ] && [ "$available_kb" -lt "$minimum_kb" ]; then
  available_gb="$(awk -v kb="$available_kb" 'BEGIN {printf "%.1f", kb / 1048576}')"
  die "only ${available_gb} GB free in $HOME" \
    "free up space, then rerun" \
    "the models omni init pulls need another 20 GB afterwards"
fi

if ! mkdir -p "$BIN_DIR" 2>/dev/null; then
  die "cannot create $BIN_DIR" \
    "choose a directory you can write to: --bin-dir \"\$HOME/bin\"" \
    "or check its parent: ls -ld \"$(dirname "$BIN_DIR")\""
fi
if [ ! -w "$BIN_DIR" ]; then
  die "$BIN_DIR is not writable" \
    "rerun with --bin-dir \"\$HOME/bin\"" \
    "or fix the permissions: chmod u+w \"$BIN_DIR\""
fi

# --- what to install --------------------------------------------------------
require_git() {
  have git && git --version >/dev/null 2>&1 && [ "$(git --version 2>&1 | cut -d' ' -f1)" = "git" ] && return 0
  die "git is not usable on this machine" \
    "macOS: xcode-select --install" \
    "Debian/Ubuntu: sudo apt install git" \
    "then rerun this script"
}

[ -z "$SOURCE" ] || [ -z "$VERSION" ] || die "--version cannot be combined with --from" \
  "pin the release inside the source instead, for example git+$REPO@v0.9.0" \
  "or drop --from to install a release tag"

if [ -n "$SOURCE" ]; then
  if [ -d "$SOURCE" ]; then
    [ -f "$SOURCE/pyproject.toml" ] || die "$SOURCE has no pyproject.toml, so it is not an omni-api checkout" \
      "point --from at the repository root" \
      "or drop --from to install the published main branch"
    SOURCE="$(cd "$SOURCE" && pwd)"
    encoded="$(printf '%s' "$SOURCE" | sed 's/%/%25/g; s/ /%20/g; s/#/%23/g; s/?/%3F/g')"
    spec="omni-api$EXTRAS @ file://$encoded"
  else
    case "$SOURCE" in
      git+*|http://*|https://*|file://*) spec="omni-api$EXTRAS @ $SOURCE" ;;
      *)
        spec="$SOURCE"
        if [ -n "$EXTRAS" ]; then
          warn "--from $SOURCE is a package name, so the media extra is not added"
          EXTRAS=""
        fi
        ;;
    esac
  fi
else
  require_git
  if ! git ls-remote --exit-code "$REPO" HEAD > /dev/null 2>&1; then
    die "cannot reach $REPO" \
      "check your network connection (and any proxy or VPN), then rerun" \
      "or install from a local checkout: --from /path/to/omni-api"
  fi
  ref="$DEFAULT_REF"
  if [ -n "$VERSION" ]; then
    ref="$VERSION"
    case "$ref" in v*) ;; *) ref="v$ref" ;; esac
    if ! git ls-remote --exit-code --tags "$REPO" "refs/tags/$ref" > /dev/null 2>&1; then
      released="$(git ls-remote --tags --refs "$REPO" 2>/dev/null | awk -F'refs/tags/' '{print $2}' | sort -V | tail -5 | tr '\n' ' ' | sed 's/ *$//')"
      die "release $ref does not exist" \
        "available releases: ${released:-none yet}" \
        "or drop --version to install the latest main branch"
    fi
  fi
  spec="omni-api$EXTRAS @ git+$REPO@$ref"
fi
# --- uv ---------------------------------------------------------------------
install_uv_via_script() {
  have curl || die "curl is missing, so uv cannot be downloaded" \
    "install it: brew install curl (macOS) or sudo apt install curl (Debian/Ubuntu)" \
    "or install uv by hand: https://docs.astral.sh/uv/getting-started/installation/"
  curl -LsSf https://astral.sh/uv/install.sh -o "$WORK/uv-install.sh" || die \
    "could not download the uv installer from astral.sh" \
    "check your network connection (and any proxy or VPN), then rerun" \
    "or install uv with Homebrew: brew install uv"
  sh "$WORK/uv-install.sh" < /dev/null || die "the uv installer failed" \
    "run it yourself to see why: curl -LsSf https://astral.sh/uv/install.sh | sh"
  PATH="$HOME/.local/bin:$PATH"
  export PATH
}

if ! have uv; then
  say "uv is not installed; installing it first (omni is distributed as a uv tool)."
  if have brew; then
    # uv has no dependencies, so a stale index still installs it; skip brew's slow self-update.
    if ! HOMEBREW_NO_AUTO_UPDATE=1 brew install uv < /dev/null > "$LOG" 2>&1; then
      warn "brew install uv failed; trying the official installer instead"
      tail -n 10 "$LOG" >&2
      install_uv_via_script
    fi
  else
    install_uv_via_script
  fi
  have uv || die "uv installed but is not on PATH" \
    "add it for this shell: export PATH=\"\$HOME/.local/bin:\$PATH\"" \
    "then rerun this script"
fi

# --- ffmpeg -----------------------------------------------------------------
# stt reads audio through ffmpeg and video writes its mp4 with it. Missing is a warning, not a stop:
# the other roles do not need it.
install_ffmpeg() {
  have ffmpeg && return 0
  say "ffmpeg is not installed; the stt and video workers need it."
  ffmpeg_log="$WORK/ffmpeg.log"
  : > "$ffmpeg_log"
  if have brew; then
    HOMEBREW_NO_AUTO_UPDATE=1 brew install ffmpeg < /dev/null >> "$ffmpeg_log" 2>&1 && return 0
  fi
  if have apt-get; then
    if [ "$(id -u)" -eq 0 ]; then
      apt-get install -y ffmpeg < /dev/null >> "$ffmpeg_log" 2>&1 && return 0
    elif have sudo; then
      sudo apt-get install -y ffmpeg < /dev/null >> "$ffmpeg_log" 2>&1 && return 0
    fi
  fi
  tail -n 10 "$ffmpeg_log" >&2
  warn "could not install ffmpeg; stt and video will not work until you do"
  warn "  macOS: brew install ffmpeg    Debian/Ubuntu: sudo apt update && sudo apt install ffmpeg"
}
[ -z "$EXTRAS" ] || install_ffmpeg

# --- install ----------------------------------------------------------------
say "installing omni from $spec"
if [ -n "$EXTRAS" ]; then
  say "  a first install downloads several GB of Python packages (torch, diffusers, ONNX Runtime); this takes a few minutes"
  say "  a reinstall reuses uv's cache ($(uv cache dir 2>/dev/null || echo ~/.cache/uv)) and is much faster"
fi
export UV_TOOL_BIN_DIR="$BIN_DIR"
# torch wheels are large; uv's 30s default read timeout fails on slow links.
export UV_HTTP_TIMEOUT="${UV_HTTP_TIMEOUT:-120}"

# uv applies [tool.uv.sources] only to the root project, and omni-api is a dependency here,
# so the media extra's two pinned sources are repeated. Keep them equal to pyproject.toml.
pinned_sources=""
if [ -n "$EXTRAS" ]; then
  pinned_sources="--with diffusers@git+https://github.com/huggingface/diffusers@4295ee3ec58efa6577bc459e9b84ca3f63aa9a96 --with en-core-web-sm@https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl"
fi

run_uv_install() {
  # shellcheck disable=SC2086
  uv tool install --force --python 3.12 $pinned_sources "$spec" < /dev/null 2>&1
  echo $? > "$WORK/uv.status"
}

# uv's progress bars need a terminal; piped, it still prints these step lines, so echo them live
show_uv_steps() {
  while IFS= read -r line; do
    printf '%s\n' "$line" >> "$LOG"
    case "$line" in
      Resolved*|Prepared*|Installed*|Downloading*|Updating*|" Downloaded"*|" Updated"*|" Built"*) say "  $line" ;;
    esac
  done
}

# Cloning and building the git packages prints nothing for a while; say we are alive.
heartbeat() {
  started=$(date +%s)
  while sleep 20 > /dev/null 2>&1; do
    say "  still working... $(( $(date +%s) - started ))s elapsed (downloads and builds can be quiet)"
  done
}

: > "$LOG"
heartbeat &
heartbeat_pid=$!
run_uv_install | show_uv_steps
kill "$heartbeat_pid" 2>/dev/null
wait "$heartbeat_pid" 2>/dev/null
if [ "$(cat "$WORK/uv.status" 2>/dev/null)" != "0" ]; then
  tail -n 20 "$LOG" >&2
  if grep -qi "couldn't find remote ref\|could not find remote ref\|repository not found" "$LOG"; then
    die "the branch or tag in the install source does not exist" \
      "check what follows @ in: $spec" \
      "for a release, use --version TAG instead"
  elif grep -qi "no python interpreter\|no interpreter found\|failed to download python" "$LOG"; then
    die "uv could not get Python 3.12" \
      "install it once by hand: uv python install 3.12" \
      "then rerun this script"
  elif grep -qi "unexpected argument\|unrecognized\|unknown option" "$LOG"; then
    die "this uv is too old for the install command used here" \
      "update it: uv self update" \
      "then rerun this script"
  elif grep -qi "no solution found\|because there" "$LOG"; then
    die "the dependencies could not be resolved" \
      "install from a checkout to rule out the git source: --from /path/to/omni-api" \
      "if it still fails, open an issue: https://github.com/nvkudva/omni-api/issues"
  elif grep -qi "failed to download\|error sending request\|dns error\|timed out\|connection refused\|connection reset\|connection closed" "$LOG"; then
    die "could not download the Python packages" \
      "check your network connection (and any proxy or VPN), then rerun"
  else
    die "uv tool install failed (the last 20 lines are above)" \
      "retry once: the network or the git host may have been flaky" \
      "then install from a checkout: --from /path/to/omni-api"
  fi
fi

# --- verify -----------------------------------------------------------------
[ -x "$BIN_DIR/omni" ] || die "uv reported success but $BIN_DIR/omni is missing" \
  "see what uv installed: uv tool list" \
  "remove the broken entry and rerun: uv tool uninstall omni-api"

if ! installed_version="$("$BIN_DIR/omni" --version 2>&1)"; then
  die "$BIN_DIR/omni did not run (the error is above)" \
    "see the full message: \"$BIN_DIR/omni --help\"" \
    "reinstall cleanly: uv tool uninstall omni-api, then rerun this script"
fi
say "installed: $installed_version"

if [ "$os" = "Linux" ] && [ "$MEDIA" -eq 1 ]; then
  say ""
  say "Linux + media: torch uses the CPU unless its wheel carries CUDA; the image"
  say "worker stays off here because it renders on Metal (macOS only)."
fi

# --- PATH -------------------------------------------------------------------
on_path=0
case ":${PATH}:" in *":${BIN_DIR}:"*) on_path=1 ;; esac

if [ "$on_path" -eq 0 ]; then
  line="export PATH=\"$BIN_DIR:\$PATH\""
  if [ "$MODIFY_PATH" -eq 1 ]; then
    case "${SHELL:-}" in
      */zsh) rc="$HOME/.zshrc" ;;
      */bash)
        if [ "$os" = "Darwin" ]; then rc="$HOME/.bash_profile"; else rc="$HOME/.bashrc"; fi
        ;;
      */fish) rc="$HOME/.config/fish/config.fish" ;;
      *) rc="" ;;
    esac
    if [ -z "$rc" ]; then
      warn "cannot tell your shell from SHELL=${SHELL:-unset}; add this by hand:"
      warn "  $line"
    else
      case "$rc" in
        */config.fish) fish_line="fish_add_path \"$BIN_DIR\"" ;;
        *) fish_line="$line" ;;
      esac
      mkdir -p "$(dirname "$rc")" 2>/dev/null
      if grep -F "$BIN_DIR" "$rc" > /dev/null 2>&1; then
        say "$rc already mentions $BIN_DIR"
      elif printf '\n# omni\n%s\n' "$fish_line" >> "$rc" 2>/dev/null; then
        say "added $BIN_DIR to PATH in $rc (open a new terminal to use it)"
      else
        warn "could not write to $rc; add this by hand:"
        warn "  $fish_line"
      fi
    fi
  else
    say "note: $BIN_DIR is not on PATH yet. Add it, or rerun with --modify-path:"
    say "  $line"
  fi
fi

resolved="$(command -v omni 2>/dev/null || true)"
if [ -n "$resolved" ] && [ "$resolved" != "$BIN_DIR/omni" ]; then
  warn "another omni comes first on PATH: $resolved"
  warn "  fix: \"brew uninstall omni\", or put $BIN_DIR ahead of it; otherwise typing omni runs that one"
fi

# --- init -------------------------------------------------------------------
if [ "$RUN_INIT" -eq 1 ]; then
  say ""
  # a terminal on stdin lets `omni init` ask before it installs Ollama; piped installs have none
  init_input=/dev/null
  if ( : < /dev/tty ) 2>/dev/null; then init_input=/dev/tty; fi
  "$BIN_DIR/omni" init < "$init_input" || die "omni init failed (the failing step is printed above)" \
    "fix that step, then run: \"$BIN_DIR/omni\" init" \
    "the CLI itself is installed; models are only needed to serve them"
fi

# --- done -------------------------------------------------------------------
if [ "$on_path" -eq 1 ]; then run="omni"; else run="$BIN_DIR/omni"; fi
say ""
say "next:"
say "  $run init     # once: install Ollama, download chat, decide, embed, stt, tts (several GB)"
say "                # image and video are tens of GB: first use, or \`$run init --media\`"
say "  $run up       # start the gateway in the background"
say "  $run status   # what is running"
say "then open http://127.0.0.1:10000/"
say ""
say "uninstall: curl -fsSL https://raw.githubusercontent.com/nvkudva/omni-api/main/uninstall.sh | sh"
