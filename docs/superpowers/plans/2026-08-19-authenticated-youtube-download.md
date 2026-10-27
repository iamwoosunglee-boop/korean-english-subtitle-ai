# Authenticated YouTube Download Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make real `protected-run` media preparation use the user's logged-in Chrome YouTube session without exporting or persisting cookie values, then verify a brand-new content run from download through transcription and an unpublished translation trial.

**Architecture:** Thread one typed `BrowserCookieSource.CHROME` value from the CLI boundary into the existing `YtDlpSubprocess`. Both video and Korean subtitle commands pass `--cookies-from-browser chrome`; fixture runs remain unchanged. A failed authenticated download remains fail-closed, with no anonymous, PO Token, HLS, or low-quality fallback.

**Tech Stack:** Python 3.13, Typer, Pydantic v2, yt-dlp, pytest, Ruff, basedpyright, uv.

## Global Constraints

- Use only Chrome; do not add speculative support for other browsers.
- Never export cookie files or persist cookie values, account identity, cookie counts, or session IDs.
- Do not retry anonymously or add PO Token, HLS, low-quality, previous-run media, or shared-cache fallback.
- Do not modify or resume failed run `7eb8587f-aa4a-4a5b-b05d-c2807abbbfcc`.
- Start the final verification with a brand-new run for `https://youtu.be/BBBBBBBBBBB?si=cAG7X9IpYHEL5y7U`.
- Do not modify dashboard or ASS behavior.
- Do not commit, push, or open a PR.

---

### Task 1: Bind Chrome authentication to yt-dlp commands

**Files:**
- Modify: `src/transcription_module/media.py`
- Modify: `src/transcription_module/media_subprocess.py`
- Test: `tests/integration/test_media_asr.py`

**Interfaces:**
- Produces: `BrowserCookieSource(StrEnum)` with `CHROME = "chrome"`.
- Produces: `YtDlpSubprocess(browser: BrowserCookieSource)`.
- Preserves: `YtDlpAdapter.download_video` and `download_subtitles` signatures.

- [ ] **Step 1: Write failing video-command test**

Update `test_yt_dlp_maps_video_failure_to_typed_error` so the expected argv includes:

```python
"--cookies-from-browser",
"chrome",
```

between `--no-playlist` and `--format`.

- [ ] **Step 2: Run the test and confirm RED**

Run:

```bash
uv run pytest -q tests/integration/test_media_asr.py::test_yt_dlp_maps_video_failure_to_typed_error
```

Expected: one assertion failure because the current argv lacks `--cookies-from-browser chrome`.

- [ ] **Step 3: Write failing subtitle-command assertion**

Extend the existing subtitle adapter test to assert that both manual and automatic subtitle commands contain one `("--cookies-from-browser", "chrome")` pair.

- [ ] **Step 4: Run the focused subtitle test and confirm RED**

Run the exact subtitle test selected from `tests/integration/test_media_asr.py`.

Expected: assertion failure because current subtitle argv lacks the browser arguments.

- [ ] **Step 5: Implement the typed browser value and minimal argv changes**

Add to `media.py`:

```python
class BrowserCookieSource(StrEnum):
    CHROME = "chrome"
```

Add a frozen dataclass field to `YtDlpSubprocess`:

```python
browser: BrowserCookieSource = BrowserCookieSource.CHROME
```

Insert the two browser arguments in both yt-dlp argv tuples. Do not add logging, cookie inspection, fallback, or a second process call.

- [ ] **Step 6: Run focused adapter tests and confirm GREEN**

Run:

```bash
uv run pytest -q tests/integration/test_media_asr.py
```

Expected: all tests pass.

### Task 2: Expose the authenticated choice at the protected-run boundary

**Files:**
- Modify: `src/transcription_module/cli.py`
- Modify: `src/transcription_module/stages.py`
- Modify: `.agents/skills/video-transcription/SKILL.md`
- Test: `tests/e2e/test_cli_walking_skeleton.py`
- Test: `tests/integration/test_media_manifest_resume.py`

**Interfaces:**
- Consumes: `BrowserCookieSource`.
- Produces: `protected-run --youtube-browser chrome`.
- Produces: `prepare_media_run(..., youtube_browser: BrowserCookieSource = BrowserCookieSource.CHROME)`.

- [ ] **Step 1: Write failing CLI contract test**

Add an E2E assertion that `protected-run --help` exposes `--youtube-browser` with `chrome` as its only supported value. Keep fixture execution independent of browser access.

- [ ] **Step 2: Run the CLI test and confirm RED**

Run:

```bash
uv run pytest -q tests/e2e/test_cli_walking_skeleton.py
```

Expected: the new help/argument assertion fails because the option does not exist.

- [ ] **Step 3: Thread the typed value through CLI and stage construction**

Add a Typer option:

```python
youtube_browser: Annotated[
    BrowserCookieSource,
    typer.Option("--youtube-browser", help="Logged-in browser used by yt-dlp."),
] = BrowserCookieSource.CHROME
```

Pass it to `prepare_media_run`, then into `_prepare_media_run_locked`, and construct:

```python
YtDlpSubprocess(browser=youtube_browser)
```

Fixture runs must not read browser cookies.

- [ ] **Step 4: Update stage fakes without widening production behavior**

Update affected tests so their fake `YtDlpSubprocess` constructors accept the browser argument. Do not change checkpoint, ASR, frame, or final-review behavior.

- [ ] **Step 5: Update the transcription skill command**

Change the real protected-run example to include:

```bash
--youtube-browser chrome
```

State that the browser must already be logged into YouTube, cookie values are read directly by yt-dlp, and no cookie file is written.

- [ ] **Step 6: Run focused CLI and media-stage tests**

Run:

```bash
uv run pytest -q \
  tests/e2e/test_cli_walking_skeleton.py \
  tests/integration/test_media_manifest_resume.py \
  tests/integration/test_media_resume.py \
  tests/integration/test_evidence_resume.py \
  tests/integration/test_media_asr.py \
  tests/unit/test_protected_execution.py \
  tests/unit/test_agent_skill.py
```

Expected: all tests pass.

### Task 3: Run static and behavioral verification

**Files:**
- Verify all files changed by Tasks 1–2.

**Interfaces:**
- Consumes: authenticated protected-run implementation.
- Produces: evidence that only the approved behavior changed.

- [ ] **Step 1: Run diagnostics**

Run LSP diagnostics on every changed Python file. Expected: no errors.

- [ ] **Step 2: Run strict static checks**

Run:

```bash
uv run ruff check \
  src/transcription_module/media.py \
  src/transcription_module/media_subprocess.py \
  src/transcription_module/cli.py \
  src/transcription_module/stages.py \
  tests/integration/test_media_asr.py \
  tests/e2e/test_cli_walking_skeleton.py

uv run basedpyright \
  src/transcription_module/media.py \
  src/transcription_module/media_subprocess.py \
  src/transcription_module/cli.py \
  src/transcription_module/stages.py
```

Expected: zero errors and warnings.

- [ ] **Step 3: Run the transcription regression slice**

Run:

```bash
uv run pytest -q tests/unit tests/integration tests/e2e
```

Expected: all tests pass; unrelated pre-existing failures are reported without expansion.

### Task 4: Verify a brand-new run from authenticated download

**Files:**
- Create: a new run under `runs/` through the CLI only.

**Interfaces:**
- Consumes: the original YouTube URL and the previously confirmed episode context.
- Produces: protected media, ASR, Korean subtitles, PTS frames, and source fingerprints under the new run.

- [ ] **Step 1: Start a new run**

Run:

```bash
uv run transcription-cli start \
  "https://youtu.be/BBBBBBBBBBB?si=cAG7X9IpYHEL5y7U" \
  --runs-dir runs
```

Capture the returned run ID. Do not reuse the failed run.

- [ ] **Step 2: Run authenticated protected preparation**

Use the previously confirmed context and run:

```bash
uv run transcription-cli protected-run "$NEW_RUN_ID" \
  --context "$CONFIRMED_CONTEXT" \
  --youtube-browser chrome \
  --runs-dir runs
```

Expected: exit 0; video, audio, Korean subtitle tracks, stored PTS frames, ASR, optional diarization, and equal source fingerprints exist in the new run.

- [ ] **Step 3: Check the real media manifest**

Verify that media duration is 2064374 ms, the source video has one 1920×1080 video stream and one audio stream, and no cookie value or account identity appears in run JSON files.

### Task 5: Complete fresh transcription and translation verification

**Files:**
- Create: fresh transcription artifacts under the new run.
- Create: isolated unpublished translation trial artifacts under `.omo/evidence/`.

**Interfaces:**
- Consumes: only the new run's protected evidence.
- Produces: fresh current-version transcript/source artifacts and a validated unpublished translation-document.v2 trial, or a real evidence-backed user confirmation gate.

- [ ] **Step 1: Run fresh window drafting and assembly**

Follow `.agents/skills/video-transcription/SKILL.md`. Use stored ASR, Korean VTT, PTS frames, and optional diarization; do not use any old run's transcript or source.

- [ ] **Step 2: Complete confirmation and current final review**

If a real unresolved item exists, ask exactly one evidence-backed question through the consented confirmation flow. Otherwise complete full frame-observation review and validation.

- [ ] **Step 3: Validate the fresh source**

Expected: the new run is `finalized` + `valid`, and fresh `transcript.json`, `transcript.csv`, `context.input.json`, and `source.json` exist.

- [ ] **Step 4: Run the unpublished translation trial**

Follow `.agents/skills/video-translation/SKILL.md` using only the fresh source. Require full speaker ownership preflight, append-only attempts, per-utterance Korean rationales, 20 CPS/continuity/duplication/terminology preflight, a separate reviewer, and deterministic validation. Do not finalize or publish a draft-source trial.

- [ ] **Step 5: Report the actual stop state**

Report PASS only if both fresh transcription and the unpublished translation trial satisfy their full gates. If a user confirmation or implementation defect blocks progress, report the exact stage, evidence, and next answer required.
