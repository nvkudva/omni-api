# Publishing omni

Three channels, in order of how ready they are today: the install script, the Homebrew tap, and PyPI (not ready — see below).

## 1. The install script

`install.sh` at the repo root is the primary channel. It works as soon as `main` is pushed:

```sh
curl -fsSL https://raw.githubusercontent.com/nvkudva/omni-api/main/install.sh | sh
```

It installs `uv` when missing, then `uv tool install git+https://github.com/nvkudva/omni-api@main`, verifies the binary, and reports whether the target directory is on `PATH`.

Options: `--from` (any uv tool spec: a local path, a git URL, a package), `--version TAG` (a release tag), `--init` (also run `omni init`), `--bin-dir`, `--modify-path`.

Why `uv tool install` and not `pip`: `pyproject.toml` pins four dependencies that PyPI does not serve — the git revisions of `diffusers`, `ltx-core-mlx` and `ltx-pipelines-mlx`, plus the spaCy wheel URL for `en-core-web-sm`. Those live in `[tool.uv.sources]`, which only uv reads. `uv tool install` therefore installs exactly what this repo tests against.

## 2. Homebrew tap

The formula lives in its own repo, [nvkudva/homebrew-omni](https://github.com/nvkudva/homebrew-omni), so the tap name resolves without a URL:

```sh
brew tap nvkudva/omni
brew trust nvkudva/omni      # Homebrew 7+ refuses untrusted third-party taps
brew install omni
```

`brew tap nvkudva/omni` looks for `github.com/nvkudva/homebrew-omni`; `Formula/omni.rb` there is the only file that matters. The formula builds a venv in the Cellar and runs `uv pip install` on the release tarball, so the same source pins apply.

Per release, from this repo:

```sh
script/release --push
```

It tags `vX.Y.Z`, pushes the tag, reads the tarball's sha256 and, when the tap is checked out next to this repo, rewrites `../homebrew-omni/Formula/omni.rb` and commits there. Set `OMNI_TAP_DIR` if the tap lives elsewhere. Push both repos afterwards.

By hand:

```sh
tag=v0.1.0
git tag -a "$tag" -m "$tag" && git push origin "$tag"

url="https://github.com/nvkudva/omni-api/archive/refs/tags/$tag.tar.gz"
sha=$(curl -fsSL "$url" | shasum -a 256 | cut -d' ' -f1)
# put $url and $sha into the tap's Formula/omni.rb, then:
brew audit --strict --formula Formula/omni.rb
```

Before the first tap release the formula still carries `REPLACE_WITH_TARBALL_SHA256`; `script/release --push` fills it. `brew audit --strict` needs that sha256 and the `license "MIT"` line, both now in place.

The formula requires macOS on Apple Silicon: `mlx`'s macOS wheels are arm64-only, and the image worker renders on Metal. It installs the `media` extra, since the tap is macOS-only.

On Linux, `install.sh` installs the base package by default — chat, decide and embed through Ollama. `--media` adds the extra: MLX ships CUDA and CPU wheels for glibc 2.35 and newer (Ubuntu 22.04, Debian 12 and later), so stt, tts and video install there too. Only macOS gets Metal, so the image worker stays out of the supervisor on Linux, and any worker whose extra is missing is left out as well; its endpoints answer 503 with the reason.

## 3. PyPI — blocked on the source pins

`uv build` produces a clean sdist and wheel:

```sh
uv build            # dist/omni_api-0.1.0-py3-none-any.whl
uv publish          # needs a PyPI token
```

Do not publish yet. Three problems, all real:

1. PyPI rejects metadata containing direct URL requirements. `en-core-web-sm` is a wheel URL and the three git packages are git URLs, so the upload would either be refused or, with the URLs stripped, produce metadata naming packages PyPI does not have.
2. Even if it accepted them, `diffusers` is pinned to a revision that carries `QwenImage21Pipeline`. An unpinned PyPI install would get a diffusers without it, and the image role would fail at load.
3. An sdist install is already broken today: `uv tool install dist/omni_api-0.1.0.tar.gz` fails with `no versions of en-core-web-sm`, because `[tool.uv.sources]` is read only from a project directory or a git checkout, never from installed metadata. Installing the same tree as a directory works, which is why the brew formula passes `buildpath` rather than the tarball.

To unblock PyPI you need one of:

- a fork of the pinned diffusers and ltx packages published to PyPI, with `[tool.uv.sources]` dropped;
- or extras: keep the heavy media stack out of the base install (`pip install omni-api[media]`) and accept that the base install serves chat/decide/embed only.

Until then, the install script and the tap are the working distribution path.

## Version

The version lives in `pyproject.toml` and is read at runtime by `omni --version` from the installed package metadata. Bump it, tag the commit `vX.Y.Z`, then update the formula.

## Release checklist

- [ ] bump `version` in `pyproject.toml`
- [ ] `uv run python -m unittest discover -s tests -q` and `cd web && bun test` green
- [ ] `cd web && bun run build` and commit `src/omni/static/app`
- [ ] commit and push `main`
- [ ] `script/release --push` (tags, computes sha256, updates the tap, prints the pushes)
- [ ] push `main` again and push the tap repo
- [ ] refresh the local tap clone before testing: `brew update`, or `git -C "$(brew --repository)/Library/Taps/nvkudva/homebrew-omni" pull` — `brew reinstall` reads that clone, so a freshly pushed formula stays invisible (and reinstalls the old version) until it is fetched
- [ ] `brew audit --strict --formula Formula/omni.rb` in the tap, then `brew reinstall omni && brew test omni`
- [ ] smoke: `curl -fsSL .../main/install.sh | sh` on a clean machine, then `omni --version`, `omni init`, `omni up`
