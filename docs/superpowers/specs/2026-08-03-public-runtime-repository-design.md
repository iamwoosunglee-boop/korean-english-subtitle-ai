# 실행 전용 공개 저장소 설계

## 목적

전사 모듈의 개발 자산은 로컬 Git 이력에 보존하면서, 원격 `master`
브랜치에는 macOS Apple Silicon에서 모듈을 실행하는 데 필요한 파일과
한글 사용 설명서만 공개한다.

## 확정 결정

- 공개 실행 환경은 macOS Apple Silicon이다.
- 원격 공개 범위에는 CLI 실행과 에이전트 실행을 모두 포함한다.
- README는 한글로 된 완전한 사용자 안내서로 작성한다.
- 기존에 공개된 개발 설정은 최신 트리에서 제거하되 과거 Git 이력은
  재작성하지 않는다.
- `master`와 `local-development`는 장기 브랜치로 유지한다.
- 기능 브랜치와 추가 worktree는 격리가 필요할 때만 선택적으로 사용한다.

## 비목표

- Intel Mac, Linux, Windows 지원
- 공개 저장소에서 테스트·정적 분석을 재현하는 개발자 안내
- 기존 원격 커밋의 이력 재작성
- 자동 배포 파이프라인이나 별도 배포 도구
- 로컬 개발 브랜치의 원격 백업

## 브랜치와 작업 폴더

`master`는 개인 실행과 원격 공개에 사용한다. `local-development`는 코드,
테스트, 설계 문서, 에이전트 개발 설정을 함께 추적하는 로컬 전용 브랜치다.

기본 운영은 한 작업 폴더에서 두 브랜치를 전환하는 것이다. 실행과 개발을
동시에 열어 두어야 할 때만 두 번째 worktree를 만든다.

작은 변경은 `local-development`에 직접 커밋한다. 병렬 작업이나 미완성
작업의 격리가 필요할 때만 `feature/*` 또는 `fix/*` 브랜치를 만들고, 완료
후 `local-development`에 병합·삭제한다.

## 공개 파일 허용 목록

`master`에는 다음 파일만 추적한다.

```text
.gitignore
.python-version
README.md
pyproject.toml
uv.lock
AGENTS.md
.agents/skills/video-transcription/SKILL.md
examples/vision-decisions.json
src/transcription_module/**
```

- `pyproject.toml`과 `uv.lock`에는 빌드 및 런타임 의존성만 둔다.
- `AGENTS.md`에는 전사 요청을 공개 스킬로 연결하는 규칙만 둔다.
- 공개 스킬은 테스트 fixture 대신 `examples/vision-decisions.json`을
  참조한다.
- 새 런타임 파일이 필요하면 허용 목록과 README를 함께 갱신한다.

위 목록은 `.gitignore` 문법이 아니라 `master`로 승격하고 검증할 경로의
기준이다. `.gitignore`는 복잡한 중첩 예외 없이 다음 개발·생성 항목을
직접 제외한다.

```text
.mcp.json
.senpi/
.omo/
docs/
tests/
.venv/
각종 캐시와 로그
.env 및 자격증명
runs/와 미디어·모델·전사 산출물
```

이미 추적 중인 `.mcp.json`과 `.senpi/`는 현재 트리에서 추적 해제한다.
로컬 파일은 보존하고 과거 커밋은 유지한다.

## 로컬 개발 자산

`local-development`에서는 실행 코드와 함께 다음을 계속 추적한다.

- 테스트와 deterministic fixture
- PRD, 조사 자료, 이 설계 명세
- Ouroboros·Senpi 등 개발 에이전트 설정
- pytest, Ruff, basedpyright용 설정과 개발 의존성

개발 의존성과 도구 설정은 공개 `pyproject.toml`·`uv.lock`과 분리해
`requirements-dev.txt`, `pytest.ini`, `ruff.toml`, `pyrightconfig.json`에
둔다. 이 파일은 `local-development`에서만 추적하므로 런타임 메타데이터를
`master`로 옮길 때 개발 패키지가 섞이지 않는다.

비밀값, 실행 결과, 캐시와 내려받은 미디어는 개발 브랜치에서도 추적하지
않는다. 로컬 브랜치는 백업이 아니므로 디스크 손실 시 이력도 잃는다는 점을
운영자가 감수한다.

## 공개 승격 흐름

```text
feature/* → local-development → 전체 검증
                               ↓
                    허용 파일만 master로 승격
                               ↓
                         공개 표면 검증
                               ↓
                       git push origin master
```

`local-development`를 `master`에 통째로 병합하지 않는다. 표준 Git 명령으로
허용된 경로만 가져오고 `git status`와 `git ls-files`로 결과를 확인한다.
브랜치마다 정책이 다른 `.gitignore`는 복사하지 않는다. `AGENTS.md`는 공개
스킬 연결 규칙만 남겼는지 확인한다. 별도 동기화 프로그램이나 배포
프레임워크는 만들지 않는다.

`master`만 명시적으로 push한다. 초기에는 별도 hook을 만들지 않는다.
실제 오조작이 발생할 때만 로컬 pre-push 차단을 추가한다.

## 한글 README

README는 다음 순서로 작성한다.

1. 프로젝트 목적과 결과물
2. macOS Apple Silicon 지원 범위
3. Python 3.13, `uv`, FFmpeg 등 사전 요구사항
4. 설치와 최초 모델 다운로드
5. 자연어 에이전트 실행 빠른 시작
6. context gate를 포함한 전체 처리 흐름
7. CLI 명령별 입력과 역할
8. 실행 ID별 산출물과 로컬 보존 위치
9. 비전 결정 예시와 PTS/VFR 처리 개념
10. FFmpeg, 접근 불가 영상, 모델 다운로드, 확인 대기, 검증 실패 해결법

README에는 테스트 실행법, 정적 분석, Ouroboros·Senpi 설정을 싣지 않는다.

## 오류와 안전 경계

- 허용 목록 밖 파일이 `master`에서 추적되면 공개를 중단한다.
- 런타임 의존성이 바뀌면 `pyproject.toml`과 `uv.lock`을 함께 승격한다.
- README 명령과 CLI 동작이 다르면 공개 전에 둘 중 잘못된 쪽을 수정한다.
- `.env`, 토큰, 내려받은 영상, 모델 파일이 발견되면 즉시 추적에서 제외한다.
- 기존 공개 이력에 비밀이 발견되면 이 설계와 별도로 자격증명을 폐기하고
  이력 재작성 여부를 결정한다.

## 검증

`local-development`에서 다음을 통과해야 공개 승격을 시작한다.

- Ruff
- basedpyright
- 전체 pytest
- 관련 실제 CLI 시나리오

`master`에서는 공개 사용자가 수행할 수 있는 표면만 검증한다.

- 추적 파일이 허용 목록과 정확히 일치
- 깨끗한 환경에서 `uv sync` 성공
- README의 설치·빠른 시작 명령이 실제로 동작

## 완료 조건

- `master`와 `local-development`의 역할과 추적 범위가 분리된다.
- `master`에는 허용된 실행 파일과 README만 남는다.
- 개발 자산은 `local-development`에서 Git 이력으로 보존된다.
- 한글 README의 명령을 새 환경에서 실행할 수 있다.
- 전체 개발 검증과 공개 표면 검증이 각각 통과한다.
- 원격에는 `master`만 push되며 이력 재작성은 하지 않는다.

## 최소화 결정

- 상시 worktree, 기능별 브랜치, 동기화 도구를 요구하지 않는다.
- `.gitignore` 하나에 보안 책임을 맡기지 않고 경로 제한 승격과 추적 파일
  확인을 사용한다.
- pre-push hook과 자동 배포는 실제 오조작이나 반복 비용이 확인되기 전에는
  추가하지 않는다.
- `master` 검증은 개발 브랜치의 전체 테스트를 반복하지 않고 설치와 README
  실행 표면만 확인한다.
