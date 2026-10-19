# omni-api: Architecture and Decisions

One local CLI that downloads, runs and supervises every model an app needs (chat, decisions, embeddings, speech-to-text, text-to-speech) and serves them all on **one port**. First client: Hyperlight (`~/dev/projects/Hyperlight`).

## Core Architecture
- Roles, not model names: apps call `chat`, `decide`, `embed`, `stt`, `tts`. `config.toml` maps role → engine + model. Swapping a model never breaks an app.
- One public port: `127.0.0.1:10000` serves every HTTP and WebSocket endpoint. The gateway routes by path.
- Engines are never exposed. Python workers listen on Unix domain sockets (`~/.omni-api/run/<role>.sock`), so they use no TCP ports. Ollama is the only engine on TCP: `127.0.0.1:10001` when omni-api spawns it, or an already-running Ollama on `:11434` is reused.
- OpenAI wire format wherever one exists, so any OpenAI SDK works by changing only `base_url`.
- Supervisor spawns each worker in its own process group, health-checks it, restarts it with backoff, and kills the group on `down`. A pidfile plus a port probe prevent duplicate instances.
- Workers load their model on the first request and unload it after `idle_unload` minutes. Ollama uses `keep_alive` for the same purpose.

```
apps ──► 127.0.0.1:10000  omni-api gateway (FastAPI + httpx streaming proxy)
           POST /v1/chat/completions        ──► Ollama            127.0.0.1:10001
           POST /v1/systemone               ──► decide worker     unix:decide.sock
           POST /v1/embeddings              ──► embed worker      unix:embed.sock
           POST /v1/audio/transcriptions    ──► stt worker        unix:stt.sock
           POST /v1/audio/speech            ──► tts worker        unix:tts.sock
           WS   /v1/realtime  (later)       ──► wake word + VAD + stt + chat + tts
           GET  /health, /v1/models
```

## Default Models (32 GB Apple Silicon target)
| Role | Model | Engine | Approx. memory |
|---|---|---|---|
| chat / summarize | Gemma 4 E2B (alternative: Qwen 3.5 4B, chosen by `omni bench chat`) | Ollama | ~2–4 GB |
| decide | Vega 0.8B public-intents (`vegaml`; upgrade: Vega 4B; audio decisions: LiquidAI d1-omni-600M) | Python worker | ~2 GB |
| embed | EmbeddingGemma-2 (text, image, audio) | Python worker, sentence-transformers | ~1–2 GB |
| stt | Parakeet TDT 0.6B v3 (fallback: Whisper large-v3-turbo via mlx-whisper) | Python worker, parakeet-mlx | ~2 GB |
| tts | Kokoro-82M | Python worker, kokoro-onnx | <0.5 GB |
Memory figures are estimates; measure them and record real values in `catalog.toml`.

## CLI (`omni`)
- `init`: prompt-based setup. Pick a model per role from the catalog, show RAM fit, write `~/.omni-api/config.toml`.
- `pull [role|all]`: `ollama pull`, or Hugging Face `snapshot_download` with a pinned revision.
- `serve`: foreground supervisor + gateway. Apps such as Hyperlight's Tauri shell spawn this as a sidecar.
- `up` / `down`: background start and stop through the pidfile.
- `status`, `logs <role>`, `test [role]`, `bench <role>`, `models`.

## Layout
New uv project, installed with `uv tool install`. After the 2026-10-16 review it is split by concern: `cli.py` (entry point), `commands/{lifecycle,init}.py`, `config.py`, `errors.py`, `schemas.py`, `gateway/{app,context,proxy,http}.py` + `gateway/routes/*`, `inference/{decide,embed}.py`, `registry/{roles,ollama,huggingface,pulling,memory}.py`, `supervisor/{worker,pool,process}.py`, `workers/{runtime,stt,tts,image,video}.py`, `catalog.toml`, `static/app/`.

## Constraints
- Local only. Bind `127.0.0.1`. Never expose engines directly.
- Pin every Hugging Face model to a commit revision. Remote-code models (`trust_remote_code`) must never float to latest.
- The embed worker serializes encodes behind one lock: Metal aborts the process when two threads encode at once.
- Embed output must match Hyperlight's current in-process vectors (same model, same task prompts, normalized) so the existing LanceDB index needs no reindex.
- Default port 10000, configurable in `config.toml`. Internal TCP (Ollama only) uses port + 1.

## Rejected Alternatives
- Go supervisor (from the "Unified Local AI Model Manager" thread): every worker model here is a Python library, so Go would still need Python. One language is simpler. Revisit for the gateway only if start-up time matters.
- Raw llama.cpp for chat: Ollama is already installed and already handles downloads, unloading and OpenAI-compatible chat.
- One TCP port per engine exposed to apps: apps would need to know every port. One gateway port plus Unix sockets internally.
- tev1 for `decide`: license not found; Vega has Apache-2.0, an abstain flag, image input and cheap `fit()` adapters.
- d1-omni-600M as the default `decide`: lower published accuracy (Decision Index 15.95), `lfm1.0` license; kept only as an optional audio-decision worker.
- Search index inside omni-api: search data is app-specific. Apps keep their own index (Hyperlight: LanceDB) and call `/v1/embeddings`.
- TUI wizard in v1: plain prompts first; TUI later.
- Port 1000: macOS lets a non-root user bind ports below 1024 only on `0.0.0.0`, which exposes every model to the LAN. Binding `127.0.0.1:1000` fails with EACCES (tested 2026-10-15). Linux needs root or `CAP_NET_BIND_SERVICE`.

## Open Risks
- Vega on Apple Silicon MPS is unconfirmed (card example uses CUDA; Mac timing was fp32).
- parakeet-mlx streaming and server behavior unconfirmed; v1 needs only file transcription.
- Embeddings over HTTP may slow bulk indexing (base64 images). Hyperlight switches only if indexing is no more than ~15% slower.
- Gateway's own `/v1/systemone` shim must match the published request and response schema.

## Revisions
- 2026-10-15 prototype: `decide` runs ollajev as its own TCP process on gateway port+2 (`/v1/systemone`) instead of a vegaml unix-socket worker; ollajev already owns the schema, normalisation and model presets.
- 2026-10-15: chat reuses a running Ollama on :11434; omni starts its own on port+1 only when none is running.
- 2026-10-15: tts uses Kokoro through mlx-audio (American and British voices only, as misaki[en] covers just those). The first call downloads its spaCy model and voice weights.
- 2026-10-15: `torchvision` is a dependency because the EmbeddingGemma-2 processor imports it.
- 2026-10-15: workers build their app with `workers.app()`, so errors use the OpenAI shape `{"error":{"message","type"}}`, the same as the gateway's.
- 2026-10-15: the gateway serves a static playground at `/playground` (`/` redirects there). It has tabs for chat, decide, embed, stt (mic and upload), tts (with an stt round trip) and health. No build step.
- 2026-10-15: noul questions take no `criteria`; ollajev rejects `[]`.
- 2026-10-16: ollajev removed. `decide` is gateway code (`decide.py`) on the chat Ollama: lettered options, one token, `top_logprobs`, option-letter probabilities. Score questions use the list layout, not decider's per-level rows. Empty `[decide] model` means the chat model. Replaces the 2026-10-15 ollajev entries.
- 2026-10-16: Ollama address is the machine's own (`OLLAMA_HOST`, else `127.0.0.1:11434`), for both reuse and spawn; no port+1. Replaces the 2026-10-15 port+1 entry.
- 2026-10-16: `omni init` installs Ollama when missing (`brew install ollama`, else the official install script) and pulls the chat and decide models when Ollama is reachable.
- 2026-10-16: Ollama is the default runner. decide = `hf.co/mradermacher/OneJev1.5-0.8B-GGUF:Q8_0`, with the OneJev `qev-labels-v2` prompt rendered as raw ChatML (`<think>\n\n</think>` prefilled). Vega has no GGUF, so Ollama can't run it.
- 2026-10-16: embed moves to Ollama `embeddinggemma-2` (`/api/embed`, `truncate:true`). Task prompts are added in the gateway (`embed.py`). The sentence-transformers worker is removed. This drops image and audio embeddings: Ollama's `/api/embed` is text only. `omni init` pulls the embed model.
- 2026-10-16: new `image` role: `/v1/images/generations` (OpenAI shape, `b64_json` only, plus `steps`, `seed` and `negative_prompt`). It is served by a diffusers worker running `Qwen/Qwen-Image-2.1` on MPS bf16. diffusers is pinned to git 4295ee3 because the 2.1 pipeline isn't released. Ollama has no Qwen-Image; it only has experimental z-image/flux2-klein.
- 2026-10-16: on Ollama routes, `model` equal to the role name (`"embed"`, `"decide"`) means that role's configured model.
- 2026-10-16: new `video` role: LTX-2.5 distilled (`dgrauet/ltx-2.5-mlx-q4`, ~51 GB; q8 ~75 GB) through `ltx_pipelines_mlx.DistilledPipeline` with `low_memory=True`. It uses the OpenAI video job shape: `POST /v1/videos` returns a queued job, `GET /v1/videos/{id}` polls it and `/content` serves the mp4. Jobs run one at a time and live in worker memory. Ollama has no video models, so this does not run on Ollama.
- 2026-10-16: runners are fixed per role: Ollama for chat, decide and embed; the Hugging Face cache for stt, tts, image and video. `/admin/models` lists, installs (NDJSON progress) and deletes models; it refuses to delete a role's default.
- 2026-10-16: `/admin/models/default` writes the role's model to `~/.omni-api/config.toml` and restarts that Python worker. It returns fit warnings (disk, RAM) based on the weights' size. `/admin/memory` reports RAM, swap, disk, Ollama-loaded models and worker RSS, with low-memory and swap warnings.
- 2026-10-16: chat image input is passed through to Ollama as OpenAI `image_url` data URLs. The playground shows the attach button only when `/api/show` lists `vision`.
- 2026-10-16: the playground is Preact + `@preact/signals` + CSS modules, built by Vite from `web/` (bun) into `src/omni/static/app/` with base `/static/app/`; `/playground` serves its `index.html`. Build output is kept in the package so serving needs no Node. Rejected: keeping the single hand-written HTML/JS page (no components, hard to polish), React (heavier for the same API).
- 2026-10-16: model management. `/admin/models/search` queries the Hugging Face API with a fixed per-role filter (Ollama roles: `gguf` library, returned as `hf.co/<repo>`; stt: parakeet MLX; tts: MLX Kokoro/TTS; image: `qwen-image-2` family; video: LTX MLX). `/admin/models/{load,unload}` uses Ollama `keep_alive` (`10m` / `0`) or the worker's `/load` and `/unload`. Python workers serve only their configured model, so other models must become the default first. A load with fit or overload warnings (RAM, swap, CPU ≥ 90 %, load average above core count, model not downloaded) returns 409 unless `force: true`. `/admin/workers/{name}/{start,stop,restart}` uses `Supervisor.restart`/`hold`, and a held worker is not respawned. Ollama is external, so omni does not start or stop it. `/admin/memory` adds CPU %, core count, load average and per-worker status.
- 2026-10-16: architecture review and refactor (see `REVIEW.md`). Hardening: one OpenAI error shape for every failure including routing 404/405 and Pydantic 422; capped request/upload bodies (413); a per-worker lock so the watch loop and an admin restart cannot double-spawn; worker pidfiles record pid + command line, so a recycled pid is never signalled; a missing worker binary no longer takes the gateway down; video jobs have a queue cap and TTL sweep; upstream reads are bounded except for long streams; `config.save_override` is atomic; HF cache walks are memoised; `installed_for` filters cached repos by the role's runtime family. Modularization: `gateway`, `inference`, `registry`, `supervisor`, `commands` packages replace the former flat `gateway.py`, `models.py`, `supervisor.py`, `decide.py`, `embed.py` and a monolithic `cli.py`. `omni.workers.<role>:app` keeps its import path.
- 2026-10-16: tests. `tests/` holds stdlib `unittest` suites (91 tests) for decide/embed logic, schemas, config validation and overrides, supervisor pid safety, gateway routes via `TestClient` (process spawning stubbed) and worker-app validation that never loads a model. `ruff check src tests` and `python -m unittest discover -s tests` are the gates.
- 2026-10-19: image defaults to `Qwen/Qwen-Image-2.1-Turbo` at 8 steps. Same `QwenImage21Pipeline`, so no worker rewrite; but the pinned diffusers (git 4295ee3) ignores the checkpoint's `sample_sigmas`, so the worker reads it from `model_index.json` after load and passes it as `sigmas` when its length matches the step count, else the pipeline default. Turbo's scheduler config (`shift: 1.0`, no dynamic shifting) applies no transform to passed sigmas. Negative prompt stays inert (`true_cfg_scale` defaults to 1.0, pre-existing for the base model too).
