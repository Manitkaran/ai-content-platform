# AI Content Platform

Self-hosted, open-source **speech/video-to-text**. Upload audio or video — or speak
into your microphone — and get back a transcript, an optional translation, and
subtitle files (TXT/SRT/VTT/JSON), all through a simple REST API you run on your own
hardware.

It wraps an open-source Whisper model (`faster-whisper`) behind a clean HTTP API;
your data and compute stay yours (D4/D5). Any project — Laravel, React, Next.js,
mobile — talks to it over HTTP, and thin PHP/JS clients are included.

> Phase 1 is **transcription only**. Image/video generation, TTS, voice cloning and
> avatars are separate future projects — see `.ai/specs/ROADMAP.md`.

## Features

- **Speech-to-text** in 100+ languages, auto-detected (or pin a language).
- **Live microphone dictation** — words appear as you speak (`WS /transcribe/stream`).
- **Translation**: translate-to-English (Whisper-native) *or* into an arbitrary target
  language via an offline MT stage (D25) — both on the file and live-mic paths.
- **Subtitle export**: TXT, SRT, VTT, and machine-readable JSON with segment timestamps.
- **Edit & re-export**: adjust segment timings/text after a job completes, then
  re-download in any format.
- **Audio and video** input — video audio is extracted with FFmpeg (MP4/MOV/MKV/…).
- **Batch upload** — submit many files in one request; one bad file never sinks the rest.
- **Prompt biasing & templates** — steer spelling of names/jargon with an initial prompt
  or a saved, reusable template.
- **Async by design**: submit → poll, so long files never block a request.
- **Self-hosted, no cloud AI API.** CPU by default; GPU is an opt-in flag.
- **A web demo page** at `/` to try everything in a browser.

## Quick start (Docker — recommended)

Prerequisites: Docker + Docker Compose.

```bash
git clone <this-repo> && cd ai-content-platform
cp .env.example .env                 # edit if you like; defaults work
docker compose up -d                 # builds the image, starts app + worker
```

On first run the model weights (default tier `small`, CPU) are downloaded into the
`acp-weights` volume automatically — no manual step. Wait for health:

```bash
curl -s localhost:8000/health        # {"status":"ok","ffmpeg":"ok"}
```

Transcribe a file:

```bash
# submit → returns a job id
JOB=$(curl -s -F file=@hello.mp3 localhost:8000/transcribe \
      | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')

# poll until completed
curl -s localhost:8000/jobs/$JOB
```

Or open the demo page in a browser: <http://localhost:8000/>.

### GPU

CPU is the default so it runs anywhere. To use an NVIDIA GPU (requires the NVIDIA
Container Toolkit) — recommended for non-English/accented speech with `large-v3`:

```bash
ACP_DEVICE=cuda ACP_MODEL_TIER=large-v3 \
  docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d
```

## API

| Method & path                | Purpose                                                    |
|------------------------------|------------------------------------------------------------|
| `GET /health`                | Liveness check; reports FFmpeg availability.               |
| `POST /transcribe`           | Submit media. Returns `{id, status:"queued"}` (202).       |
| `POST /transcribe/batch`     | Submit many files; per-file accepted/rejected rows.        |
| `GET /jobs/{id}`             | Poll status; result when `completed`, error when `failed`. |
| `PUT /jobs/{id}/segments`    | Edit a completed job's segments and re-derive the text.    |
| `GET /jobs/{id}/subtitle`    | Download the rendered file; `?format=txt\|srt\|vtt\|json`.  |
| `WS /transcribe/stream`      | Live microphone transcription (words as you speak).        |
| `GET /templates`             | List saved prompt templates.                               |
| `POST /templates`            | Create or update a prompt template.                        |
| `DELETE /templates/{name}`   | Delete a prompt template.                                  |
| `GET /docs`                  | Interactive OpenAPI docs.                                  |

`POST /transcribe` (and `/transcribe/batch`) accept a multipart `file` (or `files`)
plus optional form fields:

- `language` — `auto` or an ISO code (default `auto`).
- `translate` — `true`/`false`; translate to English, Whisper-native.
- `target_language` — an ISO code to translate the transcript *into* via the MT seam
  (D25); `''` = off, `auto` is not a valid target. Independent of `translate`.
- `output_format` — `text`/`srt`/`vtt`/`json` (default `text`).
- `prompt` — an initial prompt biasing vocabulary (names, jargon).
- `template` — the name of a saved template to apply as the prompt.

Full schema: `openapi.json` (regenerate with `python -m scripts.export_openapi`).
(WebSocket routes are not part of the OpenAPI schema.)

### Live microphone (streaming)

`WS /transcribe/stream` transcribes audio **as you speak** (D22–D27). The client sends a
JSON `{"type":"start","language":"auto","translate":false,"target_language":""}` frame,
then binary audio chunks, then `{"type":"stop"}`; the server streams back `partial`
(interim, cumulative) and `segment` (finalized) frames and a closing `final`. Decoding and
the model pass are both bounded to newly-arrived audio (D27), so latency stays flat over a
long session. The session is ephemeral — nothing is stored. Open the demo page, switch to
the **Speak Live** tab, and press-and-hold the mic to try it. As with the rest of Phase 1
there is **no authentication** — keep it behind your own proxy; do not expose it directly to
the public internet.

### Prompt templates

A **template** is a named, reusable initial prompt (e.g. a glossary of product names).
Save one via `POST /templates` (`{name, prompt, description}`), then pass
`template=<name>` on submit instead of repeating the prompt each time. An explicit
`prompt` always wins over a `template`.

## Client SDKs

Thin wrappers over the REST API (no AI in them):

- **PHP** — `clients/php` (`AiContentPlatform\Client`). Packagist-shaped.
- **JS/TS** — `clients/js` (`@ai-content-platform/client`). npm-shaped.

```js
import { Client } from "@ai-content-platform/client";
const acp = new Client("http://localhost:8000");
const { id } = await acp.transcribe(fileBlob, {
  language: "auto",
  target_language: "hi",     // translate the transcript into Hindi (CR-025)
  output_format: "srt",
  template: "product-names",  // bias spelling via a saved template (CR-022)
});
const job = await acp.waitForResult(id);
const srt = await acp.downloadSubtitle(id, "srt");
```

Both SDKs mirror the full REST surface (CR-029): `transcribe` / `transcribeBatch`,
`getJob` / `waitForResult`, `editSegments`, `downloadSubtitle`, and template CRUD
(`listTemplates` / `saveTemplate` / `deleteTemplate`). Live streaming stays
browser-only (the demo page is the reference client).

## Configuration

All configuration is via environment variables, documented in **`.env.example`**
(a test fails if code and that file drift). Every key the app reads:

| Variable                        | Default                    | Meaning                                       |
|---------------------------------|----------------------------|-----------------------------------------------|
| `ACP_APP_NAME`                  | `ai-content-platform`      | Application name/identifier.                   |
| `ACP_ENV`                       | `development`              | `development`/`testing`/`production`.         |
| `ACP_MODEL_DRIVER`              | `faster_whisper`           | `faster_whisper` (real) or `fake`.            |
| `ACP_MODEL_TIER`                | `small`                    | `tiny`/`base`/`small`/`medium`/`large-v3`.    |
| `ACP_DEVICE`                    | `cpu`                      | `cpu` or `cuda`.                              |
| `ACP_STORAGE_DRIVER`            | `local`                    | `local` (real disk) or `fake`.                |
| `ACP_DATA_DIR`                  | `./data`                   | Root dir for local storage, DB, and volumes.  |
| `ACP_DATABASE_URL`              | `sqlite:///./data/acp.db`  | Job/template store (SQLite path or DSN).      |
| `ACP_TRANSLATE_DRIVER`          | `argos`                    | `argos` (offline MT) or `fake`.               |
| `ACP_MAX_UPLOAD_BYTES`          | `5368709120`               | Max upload size (~5 GB); over → 413.          |
| `ACP_MAX_BATCH_FILES`           | `20`                       | Max files per batch request; over → 413.      |
| `ACP_MAX_PROMPT_CHARS`          | `2000`                     | Max prompt/template length; over → 422.       |
| `ACP_TRANSCRIPT_RETENTION_DAYS` | `30`                       | How long transcripts are kept.                |
| `ACP_HOST`                      | `0.0.0.0`                  | Bind address for the server.                  |
| `ACP_PORT`                      | `8000`                     | Port the server listens on.                   |

For non-English/heavily-accented speech (Bengali, Hindi, names), `small` on CPU is
weak — use **`large-v3` on a GPU** (`ACP_MODEL_TIER=large-v3`, `ACP_DEVICE=cuda`).
See `.env.example` for the full multilingual notes and the Argos language-pair setup.

**Retention & privacy:** uploaded media is deleted the moment transcription
completes; the transcript is kept for `ACP_TRANSCRIPT_RETENTION_DAYS`, then swept.
A request for a deleted/expired job returns cleanly (410 Gone, no dangling data).
Live-mic sessions are ephemeral and never stored.

**Security note:** Phase 1 has **no authentication** — it is built for a single
operator on a trusted network. Do not expose it directly to the public internet;
put it behind your own auth/proxy if you need to.

## Local development (without Docker)

```bash
pip install -r requirements-dev.txt          # light: no real model
ACP_MODEL_DRIVER=fake pytest                  # the GPU-free suite

pip install -r requirements-model.txt         # add the real model
ACP_MODEL_DRIVER=faster_whisper uvicorn app.main:app --reload

pip install -r requirements-translate.txt     # add real translation (CR-025)
# then download the language pair(s) you need, e.g. en->hi (see .env.example)
```

The GPU-free suite runs entirely on the **fake** drivers — no GPU, model, mic, or
FFmpeg required. Lint with `ruff check app/ tests/`. Real-model tests are gated:
`ACP_MODEL_DRIVER=faster_whisper pytest -m real_model` (they skip without a fixture
clip). The full manual vertical-slice walkthrough is in `DEMO-slice.md`.

## Architecture

```
  caller ── HTTP ──▶  FastAPI (app)  ──▶  Transcriber seam ──▶  faster-whisper | fake
  browser ─ WS ───▶       │                     │
  (live mic)              │                     ▼
                          │              Translator seam ──▶  argos | fake  (D25)
                          ▼
                    Job store (SQLite)   Object-storage seam (local disk | fake)
                          │
                    Worker (retention sweep, same image)
```

Three seams (`Transcriber`, `ObjectStorage`, `Translator`) are chosen by config;
feature code never imports a model/MT library directly. The default test suite runs on
fakes and needs no GPU. See `AGENT.md` and `.ai/specs/` for the full design.

## Contributing

Fork, branch, add tests, ensure `ruff check` and `pytest` are green, open a PR.

## License

MIT — see [LICENSE](LICENSE).
