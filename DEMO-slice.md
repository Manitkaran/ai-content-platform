# DEMO-slice.md — the M2 vertical slice, by hand

This is the manual walkthrough of the thin vertical slice (M2.4): upload one file,
poll, get a transcript back from the running app. It is the **milestone gate** —
it must pass on the running app before M3+ work is trusted.

## On the fake driver (no GPU, no weights — the default)

```bash
cp .env.example .env
# .env already defaults to ACP_MODEL_DRIVER=faster_whisper; for the fake slice:
export ACP_MODEL_DRIVER=fake
uvicorn app.main:app --reload
```

In another shell:

```bash
# 1. health
curl -s localhost:8000/health
# {"status":"ok"}

# 2. submit any small audio-shaped file → a job id, status queued
JOB=$(curl -s -F file=@some.mp3 localhost:8000/transcribe | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')
echo "job: $JOB"

# 3. poll until completed
curl -s localhost:8000/jobs/$JOB
# {"status":"completed","result":{"text":"[FAKE-TRANSCRIBER] ...", ...}}
```

The fake transcript is clearly marked `[FAKE-TRANSCRIBER]` so it can never be
mistaken for a real one.

## On the real model (gated — the true M2.4 gate)

```bash
pip install -r requirements-model.txt        # faster-whisper
export ACP_MODEL_DRIVER=faster_whisper
export ACP_MODEL_TIER=base ACP_DEVICE=cpu     # CPU-first (D13)
uvicorn app.main:app
```

```bash
# hello.wav = a short clip saying "hello world"
JOB=$(curl -s -F file=@hello.wav localhost:8000/transcribe | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')

# poll until completed
curl -s localhost:8000/jobs/$JOB
# → status completed, result.text contains "hello", detected_language "en"
```

Automated equivalent:

```bash
ACP_MODEL_DRIVER=faster_whisper pytest -m real_model
```

## What the slice proves

- Async submit → poll works end to end (D11).
- The model seam resolves the configured driver by config (D20).
- A completed job returns text + detected language + timestamped segments (D18).
- The source media is deleted on completion; the transcript is retained (D16).

If any step fails, fix and re-run from step 1. **This gate must be green before
M3+.**
