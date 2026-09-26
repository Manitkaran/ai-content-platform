# Test fixtures

Place a short real audio clip named **`hello.wav`** here to run the gated
real-model slice (`pytest -m real_model`, M2.4). A 1–3 second English clip saying
"hello world" is enough. It is intentionally not committed (see `.gitignore`) —
media files are not stored in the repo.

Generate one locally, e.g. with a TTS tool or:

```bash
# say + ffmpeg (macOS) or espeak (linux) → wav
espeak "hello world" -w tests/fixtures/hello.wav
```
