# Public Runtime Repository Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve the complete transcription-module development history on local `local-development` while making local `master` a Korean-documented, agent-capable, runtime-only repository ready for an explicitly approved push.

**Architecture:** Keep two long-lived branches with different tracked trees and promote only named runtime paths; never merge `local-development` wholesale into `master`. Keep development tooling in native local-only config files, then verify `master` from a clean local clone using only commands documented in the Korean README.

**Tech Stack:** Git, Python 3.13, uv, Typer, Pydantic v2, yt-dlp, FFmpeg, mlx-whisper, pytest, Ruff, basedpyright

## Global Constraints

- Public execution supports macOS Apple Silicon only.
- `master` is the normal execution and public branch; `local-development` is local-only development history.
- Do not rewrite existing Git history.
- Do not push any branch during this plan; request explicit user approval after local verification.
- Public scope is limited to `.gitignore`, `.python-version`, `README.md`, `pyproject.toml`, `uv.lock`, `AGENTS.md`, `.agents/skills/video-transcription/SKILL.md`, `examples/vision-decisions.json`, and `src/transcription_module/**`.
- Keep `.mcp.json`, `.senpi/`, `.omo/`, `docs/`, `tests/`, development-tool configuration, secrets, caches, logs, `runs/`, media, and model artifacts off `master`.
- Use only standard Git operations; add no sync program, release framework, CI, or Git hook.
- Preserve unrelated dirty files and never stage `.debug-journal.md`.
- Keep the approved transcription behavior unchanged.

---

## File Map

### Shared runtime files

- `src/transcription_module/**`: existing typed CLI and runtime implementation; no behavioral edits.
- `pyproject.toml`: build metadata and runtime dependencies only.
- `uv.lock`: lock generated from runtime-only project metadata.
- `.python-version`: Python 3.13 selector.
- `.agents/skills/video-transcription/SKILL.md`: public agent runbook.
- `examples/vision-decisions.json`: public manifest-backed vision-decision shape.
- `README.md`: complete Korean installation and operation guide.

### Development-only files

- `requirements-dev.txt`: pytest, Ruff, and basedpyright requirements.
- `pytest.ini`: pytest discovery and strict-mode settings.
- `ruff.toml`: current lint settings moved from `pyproject.toml`.
- `pyrightconfig.json`: current strict type-check settings moved from `pyproject.toml`.
- Existing `tests/**`, `docs/**`, `.mcp.json`, and `.senpi/**`: tracked only on `local-development`.

### Branch-specific files

- `.gitignore`: ordinary development ignore rules on `local-development`; public/development exclusion rules on `master`.
- `AGENTS.md`: Ouroboros plus transcription routing on `local-development`; transcription routing only on `master`.

---

### Task 1: Preserve the completed development baseline

**Files:**
- Modify: `.gitignore`
- Add to local history: `.agents/skills/video-transcription/SKILL.md`
- Add to local history: `.python-version`
- Add to local history: `docs/prd/video-transcription-module.md`
- Add to local history: `docs/research/lower-third-transition-experiment.md`
- Add to local history: `docs/research/ouroboros-gen6-investigation.md`
- Add to local history: `docs/youtube-transcription-walkthrough-issues.md`
- Add to local history: `docs/superpowers/plans/2026-08-03-public-runtime-repository.md`
- Add to local history: `pyproject.toml`
- Add to local history: `src/transcription_module/**`
- Add to local history: `tests/**`
- Add to local history: `uv.lock`
- Preserve unstaged: `.debug-journal.md`

**Interfaces:**
- Consumes: the already verified transcription implementation in the current `local-development` working tree.
- Produces: one locally committed baseline that later tasks can modify and `master` can promote by path.

- [ ] **Step 1: Ignore the local debug journal**

Add this development-only entry to `.gitignore`:

```gitignore
# Agent diagnostics
.debug-journal.md
```

- [ ] **Step 2: Stage only the completed module and local development assets**

Run:

```bash
git add -- \
  .gitignore \
  AGENTS.md \
  .agents/skills/video-transcription/SKILL.md \
  .python-version \
  docs/prd/video-transcription-module.md \
  docs/research/lower-third-transition-experiment.md \
  docs/research/ouroboros-gen6-investigation.md \
  docs/superpowers/plans/2026-08-03-public-runtime-repository.md \
  docs/youtube-transcription-walkthrough-issues.md \
  pyproject.toml \
  src \
  tests \
  uv.lock
git status --short
```

Expected: the paths listed under **Files** are staged, while `.debug-journal.md` is absent and no runtime output or secret is staged.

- [ ] **Step 3: Verify the staged baseline**

Run:

```bash
git diff --cached --check
git diff --cached --stat
```

Expected: no whitespace error and no path outside the explicit Task 1 staging set.

- [ ] **Step 4: Commit the local development baseline**

Run:

```bash
git commit -m "Add transcription module implementation"
git log -1 --oneline
```

Expected: one commit on `local-development`; `master` remains at `24f0b1d` and no remote operation occurs.

---

### Task 2: Prepare the runtime-only public assets

**Files:**
- Create: `requirements-dev.txt`
- Create: `pytest.ini`
- Create: `ruff.toml`
- Create: `pyrightconfig.json`
- Create: `examples/vision-decisions.json`
- Create: `README.md`
- Modify: `pyproject.toml`
- Modify: `.agents/skills/video-transcription/SKILL.md`
- Regenerate: `uv.lock`

**Interfaces:**
- Consumes: current runtime metadata, development-tool settings, verified vision fixture, CLI command surface, and agent skill.
- Produces: one verified local-development commit containing all paths that `master` will promote plus native local-only development configs.

- [ ] **Step 1: Create the local development dependency file**

Create `requirements-dev.txt`:

```text
basedpyright>=1.37
pytest>=9
ruff>=0.14
```

- [ ] **Step 2: Move pytest settings to their native file**

Create `pytest.ini`:

```ini
[pytest]
addopts = --strict-config --strict-markers
testpaths = tests
```

- [ ] **Step 3: Move Ruff settings to their native file**

Create `ruff.toml`:

```toml
target-version = "py313"
line-length = 88
src = ["src", "tests"]

[lint]
select = ["ALL"]
ignore = ["COM812", "D203", "D213"]

[lint.per-file-ignores]
"tests/**/*.py" = ["ANN201", "S101", "S603", "S607"]
```

- [ ] **Step 4: Move basedpyright settings to their native file**

Create `pyrightconfig.json`:

```json
{
  "include": ["src", "tests"],
  "exclude": [".venv"],
  "typeCheckingMode": "all",
  "pythonVersion": "3.13",
  "reportMissingTypeStubs": "error",
  "reportUnnecessaryTypeIgnoreComment": "error"
}
```

- [ ] **Step 5: Reduce `pyproject.toml` to package and runtime metadata**

Replace its contents with:

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "transcription-module"
version = "0.1.0"
description = "Agent-driven Korean video transcription workflow"
requires-python = ">=3.13"
dependencies = [
  "anyio>=4.12,<5",
  "mlx-whisper==0.4.3",
  "pydantic>=2.12,<3",
  "typer>=0.21,<1",
  "yt-dlp>=2026.7.4",
]

[project.scripts]
transcription-cli = "transcription_module.cli:app"

[tool.uv]
package = true

[tool.hatch.build.targets.wheel]
packages = ["src/transcription_module"]
```

- [ ] **Step 6: Regenerate the runtime lock**

Run:

```bash
uv lock
```

Expected: `uv.lock` resolves the runtime-only project without a development dependency group.

---

#### Part B: Publish the vision example through the agent skill

- [ ] **Step 7: Create the public example**

Create `examples/vision-decisions.json` with the validated JSON object currently stored in `tests/fixtures/vision/vfr.json`, preserving these top-level keys and values exactly:

```json
{
  "manifest": {
    "duration_ms": 6000,
    "frames": [
      {"frame_id": "f000001", "frame_index": 0, "pts_ms": 0, "path": "frames/f000001.jpg"},
      {"frame_id": "f000002", "frame_index": 1, "pts_ms": 2400, "path": "frames/f000002.jpg"},
      {"frame_id": "f000003", "frame_index": 2, "pts_ms": 4100, "path": "frames/f000003.jpg"},
      {"frame_id": "f000004", "frame_index": 3, "pts_ms": 5900, "path": "frames/f000004.jpg"}
    ]
  },
  "audit_segments": [
    {"start_tc_ms": 0, "end_tc_ms": 2400, "owned_cue_sequences": [1], "context_cue_sequences": [2]},
    {"start_tc_ms": 2400, "end_tc_ms": 6000, "owned_cue_sequences": [2], "context_cue_sequences": [1]}
  ],
  "request": {
    "start_tc_ms": 2000,
    "end_tc_ms": 4200,
    "frame_ids": ["f000002", "f000003"]
  },
  "sol": {
    "scene": "발화자와 희미한 하단 문구가 함께 보인다.",
    "rough_lower_third": "희미한 하단 문구",
    "resolution": "unresolved",
    "confidence": 0.55,
    "first_visible_frame_id": "f000002",
    "first_not_visible_frame_id": "f000003"
  },
  "terra": {
    "scene": "발화자 하단의 문구가 명확히 식별된다.",
    "rough_lower_third": "하단 문구",
    "resolution": "resolved",
    "confidence": 0.92,
    "first_visible_frame_id": "f000002",
    "first_not_visible_frame_id": "f000003"
  },
  "ocr": {
    "scene": "OCR 보조 결과",
    "rough_lower_third": "하단 문구",
    "resolution": "resolved",
    "confidence": 0.8,
    "first_visible_frame_id": "f000002",
    "first_not_visible_frame_id": "f000003"
  },
  "cue_sequence": 2,
  "estimated_trust": "low",
  "trust_override": "high",
  "meaning_matches": true,
  "audio_boundary_uncertain": true
}
```

- [ ] **Step 8: Point the skill at the public example**

In `.agents/skills/video-transcription/SKILL.md`, change only the example reference:

```markdown
7. Write `$RUN_DIR/vision-decisions.json` using the typed structure demonstrated
   by `examples/vision-decisions.json`, replacing every example ID and PTS with
   this run's manifest-backed evidence.
```

---

#### Part C: Write the complete Korean user README

- [ ] **Step 9: Create `README.md` with the approved user guide**

Create the following complete document:

````markdown
# 한국어 영상 전사 모듈

YouTube 영상에서 한국어 원어 자막 초본을 만드는 로컬 실행 도구입니다.
에이전트 스킬이 회차 맥락, 전체 영상 비전 감사, VAD 누락 복원과 사용자
확인을 조정하고, Python CLI가 미디어·ASR·파일 처리를 재현 가능하게 수행합니다.

## 지원 환경

- macOS Apple Silicon
- Python 3.13
- `uv`
- FFmpeg와 ffprobe
- 모델과 영상을 받을 네트워크 및 충분한 로컬 디스크

Intel Mac, Linux와 Windows는 현재 지원 범위가 아닙니다.

## 설치

```bash
brew install uv ffmpeg
uv python install 3.13
git clone https://github.com/iamwoosunglee-boop/Transcription-Translation-Module.git
cd Transcription-Translation-Module
uv sync --frozen
uv run transcription-cli --help
```

`mlx-whisper` 모델은 첫 전사 때 내려받으므로 처음 한 번은 시간이 더 걸립니다.

## 에이전트로 빠르게 시작

이 저장소를 에이전트 환경에서 연 뒤 다음처럼 요청합니다.

```text
https://www.youtube.com/watch?v=jNQXAC9IVRw 영상을 한국어 원어 자막으로 전사해 주세요.
```

에이전트는 먼저 실행 ID를 만들고 `waiting_context`에서 멈춥니다. 등장인물의
이름·의상·외형과 화면 자막 신뢰도를 자유롭게 설명하거나 `없음`이라고
답해야 ASR과 비전 처리를 시작합니다.

## CLI 처리 흐름

아래 URL은 형식 예시입니다. 실제 작업에서는 전사할 공개 YouTube URL로
바꾸십시오.

```bash
export URL='https://www.youtube.com/watch?v=jNQXAC9IVRw'
export RUNS_DIR="$PWD/runs"
export START_OUTPUT="$(
  uv run transcription-cli start "$URL" --runs-dir "$RUNS_DIR"
)"
printf '%s\n' "$START_OUTPUT"
```

출력에서 `run_id`를 읽어 환경 변수로 설정합니다.

```bash
export RUN_ID="$(
  printf '%s\n' "$START_OUTPUT" |
    uv run python -c 'import json, sys; print(json.load(sys.stdin)["run_id"])'
)"
uv run transcription-cli context "$RUN_ID" --text '없음' --runs-dir "$RUNS_DIR"
uv run transcription-cli prepare "$URL" --run-id "$RUN_ID" --runs-dir "$RUNS_DIR"
uv run transcription-cli transcribe "$RUN_ID" --runs-dir "$RUNS_DIR"
```

비전 에이전트는 `$RUNS_DIR/$RUN_ID/media.json`의 실제 frame ID와 PTS를 사용해
`vision-decisions.json`을 만듭니다. `examples/vision-decisions.json`은 구조
예시이며 그대로 실행하는 입력이 아닙니다.

```bash
uv run transcription-cli apply-vision "$RUN_ID" \
  --decisions "$RUNS_DIR/$RUN_ID/vision-decisions.json" \
  --runs-dir "$RUNS_DIR"
uv run transcription-cli recover "$RUN_ID" --runs-dir "$RUNS_DIR"
uv run transcription-cli validate "$RUN_ID" --runs-dir "$RUNS_DIR"
```

확인이 남아 있으면 한 항목씩 조회하고 반영합니다.

```bash
uv run transcription-cli next-confirmation "$RUN_ID" --runs-dir "$RUNS_DIR"
uv run transcription-cli confirm "$RUN_ID" --answer '확정한 내용' --runs-dir "$RUNS_DIR"
uv run transcription-cli validate "$RUN_ID" --runs-dir "$RUNS_DIR"
```

## 명령

| 명령 | 역할 |
| --- | --- |
| `transcription-cli start` | 실행을 만들고 회차 맥락을 기다립니다. |
| `transcription-cli context` | 회차 맥락을 저장합니다. |
| `transcription-cli prepare` | 영상·선택 자막·오디오·전체 frame을 준비합니다. |
| `transcription-cli transcribe` | mlx-whisper로 음성 초본을 만듭니다. |
| `transcription-cli apply-vision` | manifest 기반 비전 결정을 적용합니다. |
| `transcription-cli recover` | VAD로 누락 발화를 찾아 복원하거나 확인을 만듭니다. |
| `transcription-cli next-confirmation` | 다음 미확정 항목 하나를 표시합니다. |
| `transcription-cli confirm` | 답변 하나를 append-only 기록에 반영합니다. |
| `transcription-cli validate` | JSON·CSV·필드·순서·타임코드를 검증합니다. |

## 결과 파일

결과는 `runs/<run_id>/`에 저장되고 Git에 포함되지 않습니다.

| 파일 | 내용 |
| --- | --- |
| `state.json` | 실행과 검증 상태 |
| `media.json` | 미디어·frame·PTS manifest |
| `asr.json` | 로컬 ASR 결과 |
| `vision.json` | 적용된 비전 증거 |
| `recovery.json` | VAD 누락 복원 결과 |
| `confirmations.jsonl` | append-only 확인 기록 |
| `transcript.json` | 정본 전사 초본 |
| `transcript.csv` | JSON에서 생성한 CSV |
| `validation.json` | 최종 검증 결과 |

## 비전과 화면 자막

전체 timeline을 감사하며 기본 관찰은 Sol, 애매한 frame 판단은 Terra,
그래도 해결되지 않은 문구만 OCR로 보냅니다. 하단 자막 구간은 평균 FPS가
아니라 `[처음 보이는 frame PTS, 처음 보이지 않는 frame PTS)`로 계산합니다.
화면 문구는 `screen_text`로 보존하며 실제 발화문을 덮어쓰지 않습니다.

## 문제 해결

### FFmpeg를 찾지 못함

`brew install ffmpeg` 후 `ffmpeg -version`과 `ffprobe -version`을 확인하십시오.

### 영상을 사용할 수 없음

삭제·비공개·지역 제한 영상은 준비할 수 없습니다. 브라우저에서 공개 재생이
가능한지 확인하고 다른 URL로 새 실행을 만드십시오.

### 첫 전사가 오래 걸림

mlx-whisper 모델을 처음 내려받는 중일 수 있습니다. 네트워크와 디스크 여유를
확인하십시오.

### `needs_confirmation`에서 멈춤

오류가 아니라 사람이 판단해야 할 항목이 남은 상태입니다.
`next-confirmation`과 `confirm`을 차례로 실행하거나 현재 JSON·CSV 초본을
그대로 사용할 수 있습니다.

### 검증 실패

`validation.json`과 CLI의 구조화된 오류를 확인하십시오. 정본인
`transcript.json`을 기준으로 문제를 해결하고 다시 `validate`를 실행합니다.
````

- [ ] **Step 10: Run the single local-development gate**

Run:

```bash
uv run --with-requirements requirements-dev.txt ruff check --no-cache .
uv run --with-requirements requirements-dev.txt basedpyright
PYTHONDONTWRITEBYTECODE=1 uv run --with-requirements requirements-dev.txt pytest -p no:cacheprovider -q
```

Expected:

```text
All checks passed!
0 errors, 0 warnings, 0 notes
33 passed
```

- [ ] **Step 11: Commit the prepared public assets and local tool split**

Run:

```bash
git add -- \
  pyproject.toml uv.lock \
  requirements-dev.txt pytest.ini ruff.toml pyrightconfig.json \
  examples/vision-decisions.json \
  .agents/skills/video-transcription/SKILL.md \
  README.md
git diff --cached --check
git commit -m "Prepare runtime-only public package"
```

Expected: one atomic commit on `local-development`.

---

### Task 3: Build and verify the local public `master`

**Files on `master`:**
- Modify: `.gitignore`
- Modify: `AGENTS.md`
- Remove from tracking: `.mcp.json`
- Remove from tracking: `.senpi/**`
- Promote: `.python-version`
- Promote: `README.md`
- Promote: `pyproject.toml`
- Promote: `uv.lock`
- Promote: `.agents/skills/video-transcription/SKILL.md`
- Promote: `examples/vision-decisions.json`
- Promote: `src/transcription_module/**`

**Interfaces:**
- Consumes: verified runtime paths from `local-development`.
- Produces: one locally committed, clean-clone-verified `master`; no push.

- [ ] **Step 1: Confirm that all planned local-development changes are committed**

Run on `local-development`:

```bash
git status --short
```

Expected: no planned path is dirty. If an unrelated dirty path appears, preserve it
and do not stage it in any later step.

- [ ] **Step 2: Switch to `master` and remove development settings from its index**

Run:

```bash
git ls-tree -r --name-only master
git switch master
git rm -r --cached --ignore-unmatch -- \
  .mcp.json .senpi .omo docs tests \
  requirements-dev.txt pytest.ini ruff.toml pyrightconfig.json
```

Expected: the pre-switch listing shows the known baseline tree, and every known
development path is absent from the `master` index. Locally present ignored files
remain on disk. If the listing contains any other path outside the public scope,
stop and add that exact path to this removal command before continuing.

- [ ] **Step 3: Promote only runtime-identical paths**

Run:

```bash
git restore --source local-development --staged --worktree -- \
  .python-version README.md pyproject.toml uv.lock src \
  .agents/skills/video-transcription/SKILL.md \
  examples/vision-decisions.json
```

Expected: only the named runtime paths are added or updated on `master`.

- [ ] **Step 4: Replace `master` `.gitignore` with the public denylist**

Use this complete content:

```gitignore
# macOS
.DS_Store
**/.DS_Store

# Local secrets
.env
.env.*
!.env.example

# Python
.venv/
__pycache__/
*.py[cod]
.pytest_cache/
.ruff_cache/
.mypy_cache/
.basedpyright/

# Local development
.mcp.json
.senpi/
.omo/
docs/
tests/
requirements-dev.txt
pytest.ini
ruff.toml
pyrightconfig.json
.debug-journal.md

# Local transcription artifacts
runs/
```

- [ ] **Step 5: Reduce public `AGENTS.md` to runtime skill routing**

Use this complete content:

```markdown
# Transcription Skill Routing

- When the user supplies a YouTube URL and asks for transcription, Korean
  source subtitles, or an original-language subtitle draft, load and follow
  `.agents/skills/video-transcription/SKILL.md`.
- The skill's context gate is mandatory: do not start ASR or vision work until
  the user provides episode context or explicitly answers `없음`.
```

- [ ] **Step 6: Verify the staged public tree before committing**

Run:

```bash
git add -- .gitignore AGENTS.md
git diff --cached --check
git ls-files
```

Expected: the index contains only the paths listed under **Files on `master`**; no `docs/`, `tests/`, agent-development config, secret, cache, run, media, or model artifact is tracked.

- [ ] **Step 7: Commit the runtime-only public tree**

Run:

```bash
git commit -m "Publish runtime-only transcription module"
git log -1 --oneline
```

Expected: one local commit on `master`; no remote operation occurs.

- [ ] **Step 8: Verify a clean local clone of `master`**

Run:

```bash
SOURCE_REPO="$(git rev-parse --show-toplevel)"
PUBLIC_QA_CLONE="$(mktemp -d)"
git clone --branch master --single-branch "file://$SOURCE_REPO" "$PUBLIC_QA_CLONE"
PUBLIC_QA_RUNS="$(mktemp -d)"
(
  cd "$PUBLIC_QA_CLONE"
  uv sync --frozen
  uv run transcription-cli --help
  uv run transcription-cli start 'https://www.youtube.com/watch?v=jNQXAC9IVRw' --runs-dir "$PUBLIC_QA_RUNS"
  git status --short
)
rm -rf "$PUBLIC_QA_CLONE" "$PUBLIC_QA_RUNS"
```

Expected:

```text
uv sync: exit 0
transcription-cli --help: exit 0
start: workflow_status is waiting_context
git status --short: no output
```

- [ ] **Step 9: Confirm branch and remote safety**

Run:

```bash
SOURCE_REPO="$(git rev-parse --show-toplevel)"
git -C "$SOURCE_REPO" branch --show-current
git -C "$SOURCE_REPO" branch -vv
git -C "$SOURCE_REPO" status --short
```

Expected: current source branch is `master`, `local-development` remains local,
no planned path is dirty, any unrelated dirty path remains untouched, and no push
has happened. Report the local commit hashes and ask for explicit approval before
any `git push origin master`.
