## v0.7.0

**Added**
- Gate cold model loads: idle Python models unload when RAM is tight, and a cold model that still cannot fit returns 503.
- Cap queued inference: image, STT and TTS requests beyond the limit are rejected with 429, and workers report their activity.
- Playground keeps the request visible, greyed out, until the model can serve.

**Fixed**
- Survive a missing Hugging Face cache.
- Refuse cross-site inference posts.
- A client that hangs up mid-body now gets a quiet 400.
- Flaky retention test and web render tests in CI.

**Changed**
- Split admin routes, dropped duplicate rules, added CI.

**Install**

```sh
curl -fsSL https://raw.githubusercontent.com/nvkudva/omni-api/main/install.sh | sh
# or
brew tap nvkudva/omni && brew install omni
```

Requires macOS on Apple Silicon.
