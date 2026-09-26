# AGENT.md — AI Content Platform

> The main agent file (SPECDOC.md §1): the tool-neutral source of what this
> application is, its architecture, and its conventions. It sits beside a
> `CLAUDE.md`; read that for how work is done (the CR workflow, the test/lint gate,
> the seam invariants).
>
> **This describes the application as built.** Phase 1 is implemented and its
> GPU-free suite is green; the spec is buildable (decisions **D1–D22**, all 16
> original OPENs resolved). Update this file **in place** as the app changes — new
> behaviour arrives as a **CR** (see `.ai/changes/`), never a silent edit.

## What it is

A **self-hosted, open-source** platform that converts audio/video into text —
**Speech-to-Text** and **translation across many languages** — by **wrapping an
existing open-source model** (Whisper family) rather than building one. It exposes a
**REST API** so any external project (Laravel, React, mobile) consumes it over HTTP;
optional thin client SDKs wrap that API. Rationale in `.ai/README.md`.

## Settled decisions (D1–D22)

- **D1** — Wrap a library, do not build a model.
- **D2** — Python is the AI layer.
- **D3** — Primary surface is a REST API; SDKs are thin HTTP wrappers, no AI in them.
- **D4** — Self-hosted; no paid cloud AI API.
- **D5** — Open source, permissive licence.
- **D6** — A web demo page exists; the surface is **API + web UI** (thin client of the
  API, resolves O4). One evaluation page, not a full product SPA.
- **D7** — **Phase 1 = transcription only.** Image/video generation, TTS, voice
  cloning, avatars, design editor are OUT of scope → `.ai/specs/ROADMAP.md`, each its
  own spec.
- **D8** — Video input (MP4/MOV/AVI/MKV/WebM/MPEG/FLV) via **FFmpeg** audio extraction.
- **D9** — Export **TXT/SRT/VTT** + timestamps; translate-to-English optional
  (Whisper-native). *Its "English-only" clause is reversed by **D25** for arbitrary
  language pairs; the English path stays the default and a supported special case.*
- **D10** — **Single operator, no auth.** Trusted network; not for the public internet.
- **D11** — **Async submit→poll** with a background worker.
- **D12** — Minimal states `queued → running → completed | failed`; **no cancel/retry**.
- **D13** — **CPU-first**, GPU opt-in.
- **D14** — Language **auto-detect** with an optional override.
- **D15** — Configurable **upload size limit** (oversized → 413).
- **D16** — Retention: media deleted on completion; transcript kept N days; then **410 Gone**.
- **D17** — Licence is **MIT**.
- **D18** — **Segment** timestamps only; no diarization / word-level.
- **D19** — **FastAPI-only** (no separate Node/TS API).
- **D20** — The model seam is a **skill** (`acp-model-seam`).
- **D21** — Storage seam, **local driver default**.
- **D22** — **Live streaming mic transcription** via `WS /transcribe/stream` (CR-001);
  reverses O9's file-only-input clause.
- **D23** — **Windowed (incremental) streaming cadence** (CR-008): the stream
  transcribes the growing buffer in windows as audio arrives, emitting cumulative
  `partial`s *while the user speaks*; supersedes D-STREAM-3's "buffer then run once"
  (ephemerality unchanged).
- **D24** — **Incremental streaming — transcribe only the new tail** (CR-013; refines
  D23): a committed offset/text is kept and each window runs the model only on
  `buffer[committed:]`, so per-update cost is constant (not O(n²)) and committed text is
  never re-transcribed.
- **D25** — **Arbitrary language-pair translation** (CR-025; reverses D9's English-only
  clause): a second **MT seam** (`app/translate/`, `Translator` contract + registry;
  `argos` real / `fake` test drivers) runs *after* transcription when
  `target_language` is set. On the file **and** streaming paths; `auto` is not a valid
  target; the Whisper-native English `translate` flag is unchanged and independent.
- **D26** — **Atomic job claim + SQLite WAL** (CR-026; refines D11/D12): a job is claimed
  `queued → running` atomically (store `claim`; SQLite CAS `UPDATE … WHERE status='queued'`),
  so the API background task and the worker (separate processes on the shared DB since
  CR-017) can't double-execute — it runs exactly once. SQLite opens in WAL + `busy_timeout`.
- **D27** — **Incremental streaming decode** (CR-027; refines D23/D24): a stateful per-session
  `StreamDecoder` on the `Transcriber` seam decodes only newly-arrived bytes into a PCM cache
  (persistent Opus codec → bit-exact), so the live-mic **decode** is bounded to new audio too —
  killing the O(n²) whole-buffer re-decode D24 left in front of the (already-bounded) model pass.
- **D28** — **Streaming upload to disk** (CR-028; refines D15): uploads stream to storage under an
  incremental byte cap (new `ObjectStorage.put_stream`/`path_for`) instead of `await file.read()`ing
  the whole (up-to-5 GB) body into RAM then checking the size; the worker reads the media via
  `path_for` (no whole-file round-trip). Same limit (413), enforced honestly; peak RAM ≈ one chunk.

Full text and the supersede/refine history in `.ai/specs/ai-content-platform-spec.md`
and `.ai/decisions.md`.

## Conventions (from the skills — the *how*)

- **Seams, not SDKs.** Anything reaching a GPU, network, or disk sits behind a
  contract, driver-selected by config. The model (`acp-model-seam`), object storage,
  and **translation** (`Translator`, CR-025) are the seams. Network/GPU/model seams
  default to `fake` in tests; local-only seams default to their **real** driver so the
  suite exercises them.
- **Config is the only env surface.** One module reads env; `.env.example` documents
  every key, and `tests/test_config.py` fails on any drift between the two. `README.md`
  also carries the full 15-key `ACP_*` table (kept in sync with `config.py`) — 15 keys as
  of CR-029's docs pass.
- **Types over strings.** Job status, output format, language handling are typed
  enums; illegal transitions are rejected.
- **Failure is a state, not a 500.** A failed job is a `failed` state with a typed
  error; unknown ids are 404.
- **Tests never require a GPU.** The default suite runs on the fake model driver.

## Architecture (as built)

```
  caller ── HTTP ─────▶  FastAPI (API) ──▶  Transcriber seam ──▶  faster-whisper | fake
  browser ─ WS ───────▶       │                 ▲
  (live mic, D22)             ▼                 │
                         Job store + Object storage seam  (retention sweep)
```

- **Surface:** REST API (`POST /transcribe`, `GET /jobs/{id}`, `GET /jobs/{id}/subtitle`)
  **+ `WS /transcribe/stream`** (live mic, D22) **+ a web demo page** at `/` (D6) — the
  demo is a thin client of the API and the WS endpoint.
- **Actors/auth:** **single operator, no auth (D10)** — trusted network only.
- **Lifecycle (file path):** async submit → poll; states
  `queued → running → completed | failed`, **no cancel/retry (D12)**; retention deletes
  media on completion and sweeps transcripts after N days → **410 Gone (D16)**.
- **Live streaming (D22/D23/D24/D27):** `WS /transcribe/stream` over the **same**
  `Transcriber` seam; the session is **ephemeral** — no job row, no stored audio, no retention
  entry. The route owns a per-session **`StreamDecoder`** (D27, CR-027): each chunk is decoded
  incrementally into a PCM cache (only new bytes, persistent Opus codec → bit-exact), and the
  window/`stop` cadence models the cached tail past a committed offset (D24) — so both the
  decode *and* the model pass are bounded to new audio. It emits cumulative `partial`s *as audio
  arrives* (not one burst after `stop`), then finalized `segment`s + a `final`.

## Where the build is

**Phase 1 is built.** All 16 original OPENs are resolved (D1–D21) and every
milestone (M0–M6) is implemented against its task's acceptance criteria. The
application lives under `app/` (FastAPI + the two seams + jobs + output), with the
GPU-free test suite under `tests/` (green), Docker/compose + CI in place, thin
PHP/JS SDKs under `clients/`, and a web demo page at `/`. Run it with
`docker compose up` or, for local dev, `ACP_MODEL_DRIVER=fake pytest`; the
real-model gate is `ACP_MODEL_DRIVER=faster_whisper pytest -m real_model` (see
`DEMO-slice.md`).

**Post-build changes so far:**
- **M2.6** — test-coverage completion (five previously-unreachable behaviours:
  `/subtitle` 404/409, VTT download, auto-detect assertion, video happy path).
- **CR-001 / D22** — **live-mic streaming** (`WS /transcribe/stream`): a new
  `transcribe_stream` on the `Transcriber` seam (fake + real drivers), the WS route
  in `app/api/stream.py`, and a 🎤 mic toggle on the demo page. Tests in
  `tests/test_streaming.py`; the file path is unchanged.
- **CR-002** — **docs reconciliation, no app code:** rewrote the stale `OPENs`
  *sections* in the task files (which listed OPENs the register already marks
  RESOLVED) so `/review` stops failing on them.
- **CR-003** — **docs reconciliation, no app code:** finished what CR-002 started —
  ~40 stale OPEN refs in task **bodies** across 15 files reworded to the settled
  `per D#` statement, **D22 added to the master-spec decision log**, and D15's
  "streaming out of scope" clause annotated **SUPERSEDED by D22** in place. `/review`
  Layers 1–3 now sweep clean.
- **CR-004** — **demo-page rendering only** (no API/protocol/seam/decision change):
  both transcript outputs (file result + live stream) render into editable read-only
  **`<textarea>`s** instead of `<pre>`, the live area auto-scrolls, and overlapping
  segments the real driver re-emits between windows are de-duplicated client-side
  (`dedupeAppend`) so live text no longer repeats. Only `app/web/index.html` changed;
  test in `tests/test_streaming.py`.
- **CR-005** — **dictation UX for the live mic** (demo-page only; no API/protocol/
  seam/decision change): the live-mic area is now a dictation control — an **editable**
  live box, a Web Audio **waveform meter**, an **elapsed timer**, **auto-stop after a
  short silence**, and **✓ Accept / ✕ Discard** actions on the finished dictation.
  All client-side (Web Audio `AnalyserNode`, no new dependency); ✓/✕ never upload or
  persist (the session stays ephemeral). Only `app/web/index.html` changed; test in
  `tests/test_streaming.py`.
- **CR-006** — **voice's own language + translate controls** (demo-page only; no
  API/protocol/seam/decision change): the voice box owns a **Language picker** and a
  **Translate-to-English toggle** (like Claude's chat voice), and the live mic reads
  *those* instead of borrowing the file-upload form's checkbox — so translating speech
  no longer means scrolling up to a control that looks like the uploader's. The
  translate backend was already correct (`task="translate"`); this closed the UX gap.
  Controls lock while recording. Only `app/web/index.html` changed; test in
  `tests/test_streaming.py`.
- **CR-007** — **chat-box voice input** (demo-page only; no API/protocol/seam/decision
  change): **replaced** CR-005's two-state dictation box with a **Claude-style chat
  input box** (owner's mockup) — a message `<textarea>` (`#chatInput`) with a bottom
  bar. The **🎙 mic streams live voice → text into the textarea** (keeping CR-004's
  overlap-dedup and CR-006's language/translate); the bar's other controls are
  **decorative chrome** (no chat/LLM backend, D7). *(The bottom bar was itself replaced
  by CR-008's single-box press-and-hold UI.)*
- **CR-008 / D23** — **incremental live streaming + press-and-hold mic.** The real fix
  for "live text not live": the WS route buffered *all* audio and transcribed once at
  `stop`, so text arrived in a burst. Now it transcribes the **growing buffer in
  windows** (new `transcribe_window` on the seam; fake grows word count with buffer size,
  real re-decodes) and emits **cumulative `partial`s as you speak**, then `segment`s + a
  `final`. UI is the owner's mockup — a **single input box with a press-and-hold SVG
  mic** (pointer events, red pulse). Touches `app/api/stream.py`, the seam + both
  drivers, `app/service.py`, `app/web/index.html`; tests in `tests/test_streaming.py`.
- **CR-009** — **fix press-and-hold mic (voice wrote no text)** (demo-page bug fix; no
  API/protocol/seam/decision change): CR-008's `endHold` only stopped `if (recording)`,
  but `startMic` is async — a normal quick press released before the socket opened, so
  the stop was dropped, the recorder ran forever, no `stop` frame was sent, and no
  transcript came back. Fixed with a `stopRequested` flag (stop as soon as the recorder
  is live), a `requestData()` flush + 250 ms interval so short taps send audio, and
  `setPointerCapture` so a drift off the button doesn't stop mid-sentence. Only
  `app/web/index.html` changed; test in `tests/test_streaming.py`.
- **CR-010** — **streaming latency & accuracy** (backend; no protocol/seam/decision
  change): CR-008 re-transcribed the whole growing buffer every ~1 s **synchronously on
  the event loop**, so response time drifted up as you spoke and short-buffer decodes made
  text jumpy. Now each window runs **off-thread** (`asyncio.to_thread`) so receiving audio
  never stalls, a **`busy` guard** prevents overlapping windows (drop-latest), and the
  window is **wider** (`_WINDOW_CHUNKS` 2→6, ~1.5 s) for steadier, cheaper passes. Only
  `app/api/stream.py` changed; tests in `tests/test_streaming.py`.
- **CR-011** — **PyAV decode fallback** (real-driver robustness; no protocol/seam/decision
  change): live voice→text failed where `faster-whisper` was installed but the standalone
  **`ffmpeg` binary** was not (the default `.env` uses the real driver). `_decode_to_wav`
  now prefers the ffmpeg binary if present, else decodes WebM/Opus → 16 kHz mono WAV with
  **PyAV (`av`)** in-process — which `faster-whisper` already installs — else raises the
  typed error. So real transcription works without a system `ffmpeg` or root. `av` stays
  imported only in `app/model/drivers/` (boundary intact). Only the real driver changed;
  tests in `tests/test_streaming.py` (typed-error) + `tests/test_real_model.py` (gated
  PyAV decode).
- **CR-012** — **model tuning + VAD for streaming accuracy** (real-driver quality; no
  protocol/seam/decision change): the real `transcribe()` passed **no** decoding params,
  so streamed partials hallucinated on silence and **repeated/flipped** between windows.
  Now it calls faster-whisper with `vad_filter=True` (skip silence), `beam_size=1`
  (greedy, faster on CPU), `temperature=0` (deterministic), and
  `condition_on_previous_text=False` (anti-repetition). Big accuracy win, small latency
  cost. The remaining latency cause (whole-buffer re-transcribe per window) was then
  fixed by CR-013. Only the real driver changed; test in `tests/test_streaming.py`.
- **CR-013 / D24** — **incremental streaming (the real latency fix).** CR-008/D23
  re-transcribed the *whole* growing buffer every window (O(n²)); CR-010/CR-012 only
  softened it. Now the WS route keeps a **committed offset + text** and each window runs
  the model **only on the new tail** past it (`transcribe_window` gained `start_time`; the
  real driver decodes to a PCM ndarray and slices `pcm[start_time:]`, shifting timestamps;
  the fake honours it). Per-update cost is **constant**, not growing with recording length,
  and committed text is never re-transcribed (steadier accuracy). Touches the seam, both
  drivers, `app/service.py`, `app/api/stream.py`; tests in `tests/test_streaming.py`.
- **CR-014** — **default model tier `base`→`small` + English-first voice** (accuracy for
  accented English + non-English names; no architecture/protocol change): `base` (~74M)
  mangles non-English names (Manajit, Dyutiman) and fast Indian-accented English — a
  **model-capacity** limit, not a bug. `small` (~244M) is the CPU default now; `medium`
  is better-but-slower on CPU, **`large-v3` is best but needs a GPU** (`ACP_DEVICE=cuda`).
  Only the `ACP_MODEL_TIER` default moved (`config.py` + `.env*`); the demo voice picker
  defaults to English (`auto` mis-detects accented English). Tests in `test_config.py` +
  `test_streaming.py`. *(The English voice default was reverted by CR-015 — see below.)*
- **CR-015** — **multilingual: auto-detect + large-v3/GPU target** (partly reverts
  CR-014's language default): the owner needs **Bengali/Hindi** too. CR-014's English
  default **forced** non-English speech to English (garbage) — reverted the voice picker
  to **Auto-detect**. The model supports 100 languages (`hi`/`bn` verified), but `small`/
  `base` are weak at Indic languages on CPU; the real fix is **`large-v3` on a GPU**
  (`ACP_MODEL_TIER=large-v3`, `ACP_DEVICE=cuda`) — documented in `.env*` as the
  multilingual/accent target. Default tier stays `small` (CPU installs keep working).
  Demo default + `.env*` + docs; test in `tests/test_streaming.py`.
- **CR-016** — **FFmpeg preflight in `/health`**: `GET /health` gains an additive typed
  `ffmpeg` field (`ok`/`missing`, `FfmpegStatus` in `app/media.py`) so a missing FFmpeg
  CLI — needed for video input (D8) — is visible *before* a video job fails deep in
  extraction. Liveness semantics unchanged: still 200 and `status: ok` when FFmpeg is
  absent (audio works without it, CR-011), so the compose healthcheck is unaffected.
  Tests in `tests/test_health.py`.
- **CR-017** — **SQLite-backed job store (jobs survive restart)**: added
  `SqliteJobStore` (subclass of `JobStore`, same 6-method contract) persisting jobs to
  `ACP_DATABASE_URL` via a JSON `payload` column + indexed `status`. `get_job_store()`
  selects it by default; `:memory:`/unparseable URLs fall back to the in-memory store.
  The whole suite now exercises the real SQLite store (tests already point the URL at a
  temp file), so parity is proven. Lifecycle/D11/D12 unchanged — execution stays in the
  API's background tasks (owner picked *store only*). Tests in
  `tests/test_job_store_sqlite.py`.
- **CR-018** — **subtitle editing + re-export**: `PUT /jobs/{id}/segments` replaces a
  completed job's segments and re-derives `result.text` (pure data edit — no model call,
  no state change, D18 segment grain), persisted durably (CR-017). `GET
  /jobs/{id}/subtitle` now **honours `?format=srt|vtt|txt`** (was silently ignored;
  `txt`→`text` alias); absent → the job's own format. Demo page gains an editable segment
  table (edit start/end/text → Save → Download in any format). 409 on non-completed, 404
  unknown, 422 on empty/`end<start`. Tests in `tests/test_subtitle_edit.py`.
- **CR-019** — **batch / multi-file upload**: `POST /transcribe/batch` accepts
  `files: list[UploadFile]` + the same options, returns **per-file rows** (accepted
  `{filename,id,status}` or rejected `{filename,error}`) — one bad file never sinks the
  batch (partial success). Each accepted file is a normal `Job` (no batch entity, D12
  unchanged); bounded by new `ACP_MAX_BATCH_FILES` (default 20, over → 413) and per-file
  `ACP_MAX_UPLOAD_BYTES` (D15). Single `/transcribe` untouched. Demo page: `multiple`
  file input → polling job list. Tests in `tests/test_batch_upload.py`.
- **CR-020** — **JSON output format**: `OutputFormat` gains `json`, a machine-readable
  export of the same DTO (`text` + `detected_language` + segment `start/end/text`, D18 —
  no new content). Works everywhere the other formats do: `output_format=json` on
  submit/batch and `GET /jobs/{id}/subtitle?format=json` (`application/json`, `.json`).
  Default stays `text`. `render()`/content-type/extension maps + demo dropdowns updated;
  OpenAPI regenerated. Tests in `tests/test_json_output.py`.

**Phase 2 (Whisper-transcription upgrades):**
- **CR-021** — **5 GB upload limit**: `ACP_MAX_UPLOAD_BYTES` default 500 MB → 5 GB
  (`5368709120`). Still a hard limit (D15, oversized → 413); just a higher ceiling.
  `.env*` + `tests/test_config.py` updated.
- **CR-022** — **Advanced Prompting & Templates** (self-hosted, no LLM, D4 intact):
  `TranscribeOptions.prompt` flows to faster-whisper's `initial_prompt` (biases spelling
  of names/jargon) via `prompt`/`template` form fields on `/transcribe`(+`/batch`); capped
  by `ACP_MAX_PROMPT_CHARS` (default 2000, over → 422). **Templates** are named reusable
  prompt presets in a new SQLite `templates` table (same DB as jobs, CR-017) with CRUD at
  `GET/POST /templates` + `DELETE /templates/{name}`; `template=<name>` on submit resolves
  to its prompt (explicit `prompt` wins). `app/templates_store.py` (new). Tests in
  `tests/test_prompting.py` + `tests/test_templates.py`.
- **CR-023** — **WhisperAI-style demo UI redesign** (demo-page only; no API/protocol/
  seam/decision change): rewrote `app/web/index.html` to a light, purple-accented
  **two-panel** layout — left marketing/feature column, right "Transcribe a File" card
  with a **drag-and-drop drop-zone**, the options, the **prompt + template picker**
  (CR-022, loaded from `GET /templates`), and the result/edit/batch views. The entire
  existing `<script>` (upload, poll, batch CR-019, subtitle editor CR-018, live-mic
  CR-008) is preserved; only markup/CSS changed + drop-zone/template wiring added. Still
  a thin client of the public API (D6). Tests in `tests/test_demo_page_phase2.py`.
- **CR-024** — **tabbed demo panel + headline tweak** (demo-page only; no API/seam/
  decision change): merged the stacked "Transcribe a File" form card and "or speak live"
  mic card into **one card with two tabs** ("Transcribe a File" / "Speak Live"), and
  removed the engine name **"Whisper"** from the hero headline (now "The Most Advanced
  Transcription."). Pure show/hide of two `.tabpanel` bodies; every element id and every
  existing handler is preserved verbatim. Still a thin client of the public API (D6);
  `tests/test_demo_page_phase2.py` unchanged and green.
- **CR-025 / D25** — **arbitrary language-pair translation** (reverses D9's English-only
  clause): a **new `Translator` seam** (`app/translate/` — `contract.py` + `registry.py`,
  drivers `argos` real / `fake` test, mirroring the model/storage seams) runs a **second MT
  stage after transcription** when `TranscribeOptions.target_language` is set. Wired on
  **both** paths: `target_language` form field on `POST /transcribe`(+`/batch`) and in the
  WS `start` frame (the stage runs per-window on the tail, so D24's incremental cadence
  holds). `validate_target_language` rejects unknown codes and **`auto`** (a target must be
  concrete) → 422 / a `bad`-start error frame. A missing pair / seam failure is a typed
  `TranslationError` → a `failed` job or a stream error frame, never a 500. `argostranslate`
  joins the import-boundary rule (drivers-only). New `ACP_TRANSLATE_DRIVER` (default `argos`,
  tests `fake`). Demo page gains a **"Translate to"** picker on the file card and the voice
  box (locked while recording). The Whisper-native English `translate` flag is unchanged and
  independent; they can combine. **Surprise fixed:** `app/jobs/store.py`'s `_deserialize` had
  a hardcoded `TranscribeOptions` field list, so the new option round-tripped to `""` through
  SQLite until taught the field. OpenAPI regenerated. Tests in `tests/test_translation.py`
  (+ gated Argos tests in `tests/test_real_model.py`).
- **CR-026 / D26** — **atomic job claim (no double-execution) + SQLite WAL**: the shipped
  compose runs the API and worker as separate processes on the shared SQLite DB (since
  CR-017), and both called `service.execute` for a queued job with a non-atomic
  check-then-act guard — a real cross-process race that ran a job twice (the second failing,
  since media is deleted on first completion, D16). Added a store-level **`claim(job_id)`**
  that atomically moves `queued → running` (SQLite CAS `UPDATE … WHERE status='queued'`,
  `rowcount==1` wins; in-memory does the same under its lock, at parity); `execute` claims
  first and no-ops if it didn't win → the job runs **exactly once**. `SqliteJobStore` now
  opens in `PRAGMA journal_mode=WAL` + `busy_timeout=5000` (removes cross-process
  `database is locked` → `internal_error` failures). Lifecycle unchanged (D11/D12); the
  BackgroundTasks-plus-worker model is untouched — only the claim became atomic. Stale
  `app/worker.py` docstring corrected. Tests in `tests/test_job_claim.py` (16-thread
  single-winner CAS; double-execute-runs-once). Verified against real file DBs (WAL on; two
  store instances × 20 claimers → one winner).
- **CR-027 / D27** — **incremental streaming decode (kills the O(n²) re-decode)**: D24 bounded
  the live-mic *model* pass to the new tail, but the *decode* stayed whole-buffer — every ~1.5 s
  window re-decoded all audio so far (WebM/Opus isn't sliceable mid-stream), so per-window
  latency climbed with session length. Added a stateful per-session **`StreamDecoder`** on the
  `Transcriber` seam (`new_stream_decoder()`): `feed(new_bytes)` decodes only newly-arrived bytes
  into a persistent PCM cache (real driver keeps a persistent Opus `CodecContext` so incremental
  decode is **bit-exact** with a full decode); `transcribe_tail` models `pcm[start_time:]` of the
  cache with no re-decode. The WS route owns one decoder per connection; the fake mirrors it at
  parity. Cadence, commit logic, `busy` guard, wire protocol, and the file path all unchanged;
  `av`/`faster_whisper` stay inside `drivers/`. Measured on a real 60 s stream: per-window decode
  17→142 ms (rising) → 5.9→13 ms (flat), ~8× less total decode, late-window latency 142→13 ms.
  Tests in `tests/test_streaming.py` (fake byte-count parity + real PyAV bit-exact, ungated).
- **CR-028 / D28** — **streaming upload to disk (bounded memory)**: the upload path buffered a
  whole file in RAM ~3× — `await file.read()` (the entire up-to-5 GB body) *then* the size check
  (so the D15 limit didn't bound memory), then `execute` read it **back** via `storage.get()` and
  `_write_temp` wrote another copy. Added two additive `ObjectStorage` methods at parity:
  **`put_stream(key, stream, max_bytes)`** (streams in 1 MiB chunks, raises `UploadTooLarge` the
  moment the cap is crossed — oversized never lands) and **`path_for(key)`** (on-disk path for
  local, `None` for the fake). `/transcribe`(+`/batch`) now read only the 32-byte head to sniff,
  then `submit_stream` the body (oversized → 413 / a `too_large` batch row); `execute` prefers
  `path_for` (hands the model the file directly, no whole-file `get()`), falling back to
  `get()`+`_write_temp` when it's `None`. `_media_key` now `media/{job_id}/{stem}.{ext}` so the fake
  driver's stem-based determinism survives without the temp rewrite. Same limit (D15), enforced
  honestly; peak RAM ≈ one chunk. WS/streaming path untouched. Tests in `tests/test_storage.py` +
  `tests/test_streaming_upload.py` (incl. `get()` called 0× on the local path). *(Not fixed here:
  the sync-model-call-in-threadpool stall of the poll routes, V6 — a separate CR.)*

- **CR-029** — **SDK coverage for the post-M5 options/endpoints** (client parity, no
  API/protocol/seam/decision change): the thin PHP/JS clients were frozen at the M5
  `transcribe`+`getJob` surface, so callers couldn't reach translation (CR-025), batch
  (CR-019), prompts/templates (CR-022), JSON output (CR-020) or subtitle editing
  (CR-018) without hand-rolling requests. Both clients now mirror the full REST surface:
  `transcribe`/`transcribeBatch` forward `target_language`/`prompt`/`template` and accept
  `json`; added `editSegments`, `downloadSubtitle(format?)`, and `listTemplates`/
  `saveTemplate`/`deleteTemplate`. No new capability the API lacks (D3); streaming stays
  out (browser-only). Both clients bumped `0.1.0 → 0.2.0` (additive → minor). Only
  `clients/js/{index.js,index.d.ts,package.json}` + `clients/php/{src/Client.php,composer.json}`
  changed; parity assertions in `tests/test_docs_and_sdks.py`. OpenAPI regenerated as a
  **no-op** (client-only change → no server route/schema change).

**Known-stale text to ignore, not build to:** the `§12 OPEN register` and the "Not
buildable" footers inside `.ai/specs/ai-content-platform-spec.md` are scaffold
remnants — build to the decisions (D1–D22). The earlier stale-OPENs cleanup (task
files listing OPENs the register marks RESOLVED) is **now done** — CR-002 fixed the
`OPENs` sections and CR-003 fixed the task bodies + spec log, so `/review` Layers 1–3
sweep clean. The remaining step before `/build-app` exists is running `/finalise`.

## Working notes

Per-developer notes would live in `.ai/agents/AGENT-<NS>.md` (`<NS>` = the developer's
namespace) — the directory exists but is **currently empty**; no such file has been
needed yet. This `AGENT.md` is the shared file and is updated in place; a wrong rule is
fixed where it lives, never contradicted in a personal file.
