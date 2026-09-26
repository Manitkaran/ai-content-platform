# CLAUDE.md — AI Content Platform

**Before doing anything, read `.ai/BUILD.md` and follow it. It governs how work is done here.**

> This started as a **spec repository** (how to *write* the specification,
> CLAUDE.md). That job is essentially done: the spec is buildable and **the
> Phase-1 application is built**. So this file now carries **both** — the
> spec-writing rules (still in force if the spec is reopened) *and* the working
> rules for a built repo where new behaviour arrives as a **change request (CR)**.
> `AGENT.md` is the curated description of what the app *is*; read it too.

---

## Current mode — READ THIS FIRST

**The spec is buildable and Phase 1 is built.** The interrogation is complete
(decisions **D1–D22**), all 16 original OPENs are resolved, and the app runs:
FastAPI + `faster-whisper` behind a REST API, async submit→poll, video via FFmpeg,
subtitles, retention, SDKs, a web demo page, **and live-mic streaming** (D22,
`WS /transcribe/stream`, added by **CR-001**).

Because the planned work is done, **new behaviour now arrives as a CR, not as a
spec edit** (see *Post-freeze working model* below). The spec tree records how we
got here; it is not where new features are designed.

**Honest caveats:** this repo is **not a git repository** in this environment (so
CRs are recorded as files, not commits/MRs), and `/finalise` has **not** been run.
`.ai/BUILD.md` now exists (it governs how work is done — read it first), but
`.claude/commands/build-app.md` does **not**, so `/build-app` cannot run yet. The
earlier stale-OPENs cleanup (task files listing OPENs the register marks RESOLVED)
is **now done** — CR-002 fixed the `OPENs` sections and CR-003 the task bodies +
spec log, so `/review` Layers 1–3 sweep clean.

## Post-freeze working model — how new work is done

These four rules are the whole contract once a spec is frozen. **CR-001 followed
them; every future change must too.**

- **`.ai/specs/` is read-only.** If a spec is wrong, ambiguous, or blocks you:
  **stop** and record it in `.ai/decisions.md`. **Never edit a spec to unblock
  yourself.** (The one exception: a spec edit *explicitly authorized by a CR and
  tied to a decision number* — e.g. CR-001 marking O9's streaming clause RESOLVED
  by D22. That is the CR doing its job, not you unblocking yourself.)
- **New behaviour is a CR, written *before* the code.** First check whether the
  feature already exists; if not, write `.ai/changes/CR-<nnn>-<slug>.md` — **what
  changes, why, and what it rules out** — then implement it. Finish the CR with a
  **How it was built** section (files touched, tests added, surprises). Cite the
  id in code (`# CR-001`) wherever the reason is not obvious from reading it.
- **Drift goes in `.ai/decisions.md`**, append-only, one entry per deviation, halt
  or judgement call a later task must know about. Decisions are **globally
  numbered, never deleted**; a later one supersedes/refines an earlier one **in
  place** (leave the old text beneath the annotation). Next free number: after
  **D22**.
- **`AGENT.md` is updated in place** as the app changes — never append-only. It is
  the curated state every session reads. Keep it bounded; it costs tokens on every
  run.

**A CR is required when the change touches product behaviour** (a new endpoint,
input mode, output, or a reversed decision). Pure test-coverage or doc-staleness
fixes are recorded as a decision entry (see M2.6) rather than a full CR — but if in
doubt, write the CR.

## Verifying work — the test/lint gate

A change is not done until **its tests pass, `ruff` is clean, and its verification
loop was run against the running app** (SPECDOC §4: the suite proves the code does
what it was told; the loop proves the feature is *reachable*).

- **Default suite (GPU-free, no model, no mic):** `ACP_MODEL_DRIVER=fake pytest`.
  Everything runs on the **fake** drivers; it needs no GPU and no `faster-whisper`.
  This is the suite that must always be green.
- **Lint:** `ruff check app/ tests/` (line-length 90, target py312). Gates the build.
- **Real-model tests are gated and deselected by default** (`pytest.ini`:
  `-m "not real_model"`). Run them explicitly only with weights + FFmpeg present:
  `ACP_MODEL_DRIVER=faster_whisper pytest -m real_model` — they `skip` without a
  `tests/fixtures/` clip.
- **Config drift is enforced:** every `ACP_*` key must appear in **both**
  `app/config.py` (`ENV_KEYS`) and `.env.example`, or `tests/test_config.py` fails.
  Don't add a key nothing reads, and never read `env` outside `app/config.py`.

## Architecture invariants — a wrong-but-plausible choice here spreads

These are the load-bearing conventions (the reason two skills exist). Get one wrong
and it is a hundred files, not one.

- **Two seams, config-selected — feature code depends on the contract, never a
  driver.** `Transcriber` (`app/model/`) and `ObjectStorage` (`app/storage/`), each
  with a `contract.py` + a `registry.py`. Add a driver via the registry; consumers
  never change. Streaming rides the **same** `Transcriber` seam
  (`transcribe_stream`, CR-001) — additive, the file path is untouched.
- **`faster_whisper` is imported in `app/model/drivers/` and nowhere else** —
  enforced by `tests/test_import_boundary.py`. Library types are mapped into our
  DTOs at the seam and never cross it.
- **The fake drivers are the test default and must stay at parity** with the real
  ones (same typed errors, same shapes) so the whole app is provable without a GPU,
  a model, a mic, or FFmpeg.
- **Failure is a state, not a 500.** A driver/media failure becomes a `failed` job
  (or, for streaming, an `{"type":"error",…}` frame + a clean close), never a raw
  traceback. Typed errors (`TranscriptionError`/`UnsupportedMediaError`) carry a
  stable `code`.
- **Types over strings** — every enumerable value is a typed enum (`OutputFormat`,
  `JobStatus`, `ModelDriver`, `StreamEventType`, …), never a magic string.
- **No auth (D10).** Single operator on a trusted network — do not add auth
  silently; that is a decision reversal and needs a CR. Keep the "don't expose to
  the public internet" caveat wherever a new surface is documented.

---

## Spec-writing rules (still in force if the spec is reopened)

The human's only jobs are to **describe the project, answer questions, run
`/finalise`, and say when to push** — everything else (skills, milestones, task
files, decision numbering) is the agent's. If the human finds themselves choosing
structure, a question went unasked.

### The three comprehension rules — for the whole conversation

**1. Ask until nothing is ambiguous.** No question budget. A question now costs a
sentence; unasked, it costs a milestone discovered weeks later with nobody to ask.

**2. Play back what you understood — short, plain, in your own words — and wait.**
A compression, not a restatement. It exposes a misreading while it is still cheap.
Nothing is built on an unconfirmed reading.

**3. Cover every scenario, not the happy path.** Empty state, the second user, the
refused permission, the failed job, the abandoned half-finished thing, deletion —
and a stale link to something deleted. Anything not covered is **asked about or
written down as an OPEN**. Never quietly assumed.

If the human says *"I don't know"*, *"later"*, or *"whatever you think"*, that is
**not** a licence for a sensible default. It is an **OPEN**, recorded with the exact
question, for the person who does know to answer before dependent code is written.

### DECISION / OPEN grammar

- **DECISION (`Dn`)** — settled, globally numbered, never deleted; superseded/refined
  **in place**.
- **OPEN (`On`)** — unresolved, captured so it is never silently guessed.

Both live in `.ai/specs/ai-content-platform-spec.md`; drift and post-freeze
decisions also go in `.ai/decisions.md`.

## Where things live

- `.ai/specs/ai-content-platform-spec.md` — the master spec (§0–§n)
- `.ai/specs/ai-content-platform-decomposition.md` — milestones & tasks
- `.ai/specs/build/M**/` — one folder per milestone, one file per task
- `.ai/specs/skills/**` — the governing skills (the *how*): `acp-architecture`,
  `acp-model-seam`
- `.ai/manifest.md` — build state, one row per task (24 tasks incl. M2.6, all `done`)
  plus a *Change requests* section (CR rows added on request; CR-029 so far)
- `.ai/decisions.md` — append-only decision + drift log (through D22)
- `.ai/changes/` — CR files (`CR-<nnn>-<slug>.md`), written before the code
- `AGENT.md` — the curated description of what the app is (read it)
