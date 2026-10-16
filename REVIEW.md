# Architecture review and remediation plan

Three specialist reviews (backend, frontend, structure/contracts) ran over the tree. This file records the
findings that were acted on and the phased plan used. Severity: **C**ritical, **H**igh, **M**edium, **L**ow.

## Findings

### Backend correctness / robustness

| Sev | Where | Issue | Fix |
|---|---|---|---|
| H | `supervisor.py` `restart`/`hold` vs `_watch` | The watch loop can `_spawn` while `restart` is mid-`_terminate`, orphaning a process and double-spawning | Per-worker `asyncio.Lock`; `_spawn` asserts no live process |
| H | `workers/__init__.py`, gateway | Pydantic `RequestValidationError` returns FastAPI's `{"detail":[...]}` 422, not the OpenAI shape | One shared `RequestValidationError` handler + gateway/worker exception handlers |
| H | `gateway.py` chat/admin bodies | Non-object JSON (`[1,2]`) → `AttributeError` → 500 | Require `dict` before use in every JSON route |
| H | proxy + `stt`/`video` | Request bodies and uploads buffered with no size cap | `Content-Length` cap (413) + capped read in STT + capped base64 in video |
| H | `workers/video.py` | `jobs` dict and mp4s grow without bound; temp frame leaks on error | Queue cap, TTL sweep, `finally` unlink |
| H | `supervisor.py` `_processes` | `psutil.Process` cached per pid forever | Cache per worker, prune dead |
| M | `models.py` `_hf_pull` | Download thread leaks when the NDJSON client disconnects | Cancel the download task in `finally` |
| M | `models.py` | Full HF cache re-walked per request and every 2 s during pull | TTL-memoise `hf_installed`; cheaper progress probe |
| M | `workers/video.py` content path | Path built from unvalidated `job_id` | Validate id format + containment check |
| M | upsteam clients | `timeout=None` everywhere → a hung worker hangs the gateway | Finite connect/read timeouts; `None` only for long streams |
| M | `cli.py` `down`, `kill_leftovers` | Stale/recycled pid could be signalled | Verify pid is ours before signalling |
| M | gateway generic handler | Unexpected exceptions leak a third error shape / internals | Generic 500 handler with the OpenAI shape |
| M | `decide.py` | `reply["logprobs"][0]` unguarded → 500 on odd replies; N× state | Guard logprobs; bound concurrency |
| M | `embed.py` | No cap on number of inputs; silent truncation | Cap input count; surface truncation |
| M | `installed(role)` | Returns every cached HF repo, so any model can be set as a role default | Filter by the role's runtime family |
| L | `config.py` `save_override` | Non-atomic read-modify-write | Temp file + `os.replace` |
| L | `/admin/models/search` | No lower bound on `limit` | Clamp to `[1, 50]` |
| L | `/admin/workers/{name}/{action}` | Bad action returns 404 | 400 for bad action, 404 only for bad name |
| L | `/health` | Never `ok` while a worker is intentionally held | Treat held workers as non-blocking |
| L | `workers/video.py` | Deprecated `on_event("startup")`, unretained task | `lifespan` + retained/cancelled task |
| L | `gateway.py`, `stt.py` | Unused `settings` param / ignored `model` field | Remove |

### Frontend correctness / performance / UX

| Sev | Where | Issue | Fix |
|---|---|---|---|
| C | `tabs/Chat.jsx` | Whole message list re-renders per streamed token; forced scroll | Stream into one signal/ref; memoised bubble; scroll only when near bottom |
| H | `tabs/Video.jsx` | Poll loop never cancelled, bounded, or visibility-gated | Abort on unmount, deadline cap, skip when hidden |
| H | `api.js` | Unguarded `JSON.parse` in SSE/NDJSON kills a stream on one bad frame | try/catch per frame + flush trailing buffer |
| H | `store.js` | `localStorage.setItem` unguarded (Safari/quota) | try/catch like `get` |
| H | `ui.jsx` | Clipboard failure unhandled; timeout not cleared | try/catch + cleanup |
| H | `tabs/Stt.jsx`, `tabs/Tts.jsx` | Object URLs never revoked; module-global signals shared across tabs | Revoke; move `transcribe()` into the API layer |
| H | `tabs/Embed.jsx` | Cosine loop assumes equal-length vectors; unchecked `data[0]` | Validate lengths/emptiness |
| M | `App.jsx` | All tabs stay mounted; health poll ignores visibility; "0 of 0 ready" | Mount active tab only; gate poll; require `> 0` |
| M | `gateway.forward` | `range`/`content-range` dropped → video can't seek | Forward range + content-range/accept-ranges/content-length |
| M | `ui.jsx` | Interactive controls nested in `<label>` | Render `trailing` outside the label |
| M | many | Missing `key`s; missing loading/empty/error states; unvalidated seed/voice | Add keys, states, validation |
| L | `ui.jsx` | `Spinner` `aria-label` on roleless span | `role="status"` |
| L | `App.module.css` | Theme toggle hidden on mobile | Keep it reachable |

### Structure / contracts

- `gateway.py` (376 lines) mixes app assembly, the streaming proxy, five route families and admin.
- `models.py` mixes role constants, Ollama control, HF cache/search, pull streaming and memory math.
- `supervisor.py` mixes the `Worker` record, the supervision loop and low-level process helpers.
- `cli.py` mixes arg parsing with lifecycle and Ollama install.
- `api.js` mixes transport, formatters, curl builders and endpoint loaders; `Stt`/`Tts` are coupled via a tab import.
- Contract drifts: three error shapes; usage vocabulary differs (`input_tokens` vs `prompt_tokens`);
  `capabilities` missing on non-chat models; 409 `type` differs; `/admin/models/pull` reports failure in-band
  with HTTP 200; image/video echo request fields inconsistently; silent size/seconds re-alignment.

## Target layout

```
src/omni/
  cli.py                     entry point: dispatches to omni.commands
  commands/{__init__,lifecycle,init}.py
  config.py                  paths, catalog load/merge, atomic save, ollama_url, idle_seconds
  errors.py                  shared OpenAI error shape + FastAPI handlers
  gateway/{__init__,app,context,proxy,http}.py
  gateway/routes/{__init__,system,chat,model_calls,media,speech,admin}.py
  inference/{__init__,decide,embed}.py
  registry/{__init__,roles,ollama,huggingface,pulling,memory}.py
  supervisor/{__init__,worker,pool,process}.py
  schemas.py                 shared request models + bounds
  workers/{__init__,runtime,stt,tts,image,video}.py   # omni.workers.<role>:app unchanged
  catalog.toml  static/app/
web/src/
  main.jsx  App.jsx  store.js
  api/{client,endpoints,models,health}.js
  lib/{format,curl}.js
  components/{ui.jsx,Icon.jsx,GatewayStatus.jsx}
  hooks/{useTheme.js,usePolling.js}
  tabs/*.jsx  tabs/tabs.module.css
tests/                       stdlib unittest; pure logic + gateway route tests + worker validation
```

## Phases

1. **Backend hardening** — shared errors, body/size validation, supervisor locking, job lifecycle,
   timeouts, pid safety, atomic config.
2. **Backend modularization** — split `gateway`, `models`, `supervisor`, `cli`; add `schemas`, `errors`.
3. **Frontend fixes** — streaming perf, resource cleanup, error/loading states, a11y, keys.
4. **Frontend modularization** — `api/`, `lib/`, `components/`, `hooks/`.
5. **Tests + verification** — unit + TestClient tests, `ruff`, `compileall`, web build, smoke import.

## Status (2026-10-16)

All five phases are done.

- **Backend** — packages above replace `gateway.py`, `models.py`, `supervisor.py`, `decide.py`, `embed.py` and the monolithic `cli.py`; every finding in the backend table is addressed.
- **Frontend** — fixes and the `api/ lib/ components/ hooks/` split are in `web/src`; `bun run build` is green and the built assets are in `src/omni/static/app/`.
- **Tests** — `tests/` holds 91 stdlib `unittest` tests. Gates: `ruff check src tests` (clean), `python -m compileall src tests`, `python -m unittest discover -s tests` (all pass).
- **Smoke** — `omni up`, `/health`, `/playground`, `/` redirect, a 404 error shape and `omni down` were exercised under an isolated `OMNI_HOME`; `down` left no orphan workers.

Bugs found and fixed while implementing (each caught by the new tests or the smoke run):

- `config.validate` rejected the shipped `idle_unload_minutes = 10` (a TOML integer) as "not a float", so the app could not start. It now accepts an int where a float is expected.
- Route return annotations of `StreamingResponse | JSONResponse` made FastAPI refuse to build the app; they are now `Response`.
- Routing 404/405 bypassed the OpenAI error shape (`{"detail": "Not Found"}`) because the handler was registered on `fastapi.HTTPException`; it is now on the starlette base, so `/nope` returns `{"error": {...}}`.
- A missing worker binary (e.g. `ollama` not installed) raised out of `spawn` and took the whole gateway down; it now logs, marks the worker failed, and retries after a delay.
- `up` assumed one gateway per `OMNI_HOME` but printed the requested port even when another instance ran elsewhere; the pidfile now records host and port, so the message is accurate and a port mismatch is reported.
