# 로그인 브라우저를 사용하는 YouTube 다운로드 설계

## 문제

`protected-run`은 현재 로그인하지 않은 `yt-dlp`로 YouTube 영상을 받는다. 2026년 8월 19일 실행에서는 영상 정보 조회까지 성공했지만 실제 영상 전송이 `HTTP 403: Forbidden`으로 끝났다. 불완전한 영상으로 전사를 계속하지 않도록 run은 실패 상태로 멈췄다.

익명 PO Token provider는 토큰 생성까지 성공했지만 기본 client와 `mweb` client 모두 영상의 2.9% 지점에서 403이 발생했다. `web_safari` HLS 경로는 이 영상에 다운로드 가능한 영상 형식을 제공하지 않았다. 이 경로들은 자동 fallback으로 추가하지 않는다.

Chrome에 로그인된 YouTube Premium 세션을 `yt-dlp --cookies-from-browser chrome`으로 직접 읽은 시험에서는 1080p AV1 영상과 Opus 오디오를 끝까지 받은 뒤 MP4로 합쳤다. 결과는 exit code 0, 재생 시간 2064.374초, 크기 543,729,678바이트, 1920×1080 영상 스트림 1개와 오디오 스트림 1개였다.

## 목표

`protected-run`이 사용자가 로그인한 Chrome 세션으로 영상과 한국어 자막을 받도록 한다. 쿠키 값은 파일, run artifact, 로그, 명령 인자에 직접 기록하지 않는다.

로그인 세션을 읽을 수 없으면 media 준비를 시작하지 않고 사용자가 이해할 수 있는 typed failure로 멈춘다. 기존 실패 run은 고치거나 이어 쓰지 않으며 변경 후 새 run에서 처음부터 실행한다.

## 동작

`protected-run`은 브라우저 종류를 명시적으로 받는다. 현재 검증된 값은 `chrome` 하나만 지원한다. 호출 예시는 다음과 같다.

```bash
uv run transcription-cli protected-run "$RUN_ID" \
  --context "$ANSWER" \
  --youtube-browser chrome \
  --runs-dir "$RUNS_DIR"
```

media 준비는 영상과 한국어 자막을 받을 때 같은 브라우저 세션을 사용한다. `yt-dlp`에는 `--cookies-from-browser chrome`을 전달한다. 쿠키를 별도 파일로 내보내는 기능은 만들지 않는다.

성공한 run에는 다음 정보만 기록한다.

- 인증 방식: `browser`
- 브라우저: `chrome`
- media 준비 결과와 기존 digest

쿠키 수, 쿠키 값, 계정 이름, 이메일, 세션 ID는 기록하지 않는다.

## 실패 처리

Chrome 프로필을 찾지 못하거나 쿠키 저장소를 읽지 못하면 다운로드를 시도하지 않고 인증 준비 실패로 끝낸다. `yt-dlp`가 쿠키를 읽었지만 YouTube 로그인이 없거나 만료되어 다운로드가 실패하면 인증된 media 다운로드 실패로 끝내고, Chrome에서 YouTube 로그인을 확인한 뒤 새 run으로 다시 시작하라고 안내한다.

로그인 상태에서도 YouTube가 403을 반환하면 기존 media 다운로드 실패로 끝낸다. 익명 다운로드, PO Token provider, HLS, 저화질 format을 자동으로 다시 시도하지 않는다.

부분 다운로드와 임시 병합 파일은 기존 `protected-run` 정리 규칙에 따라 제거한다. 실패한 run은 성공 상태로 바꾸지 않는다.

## 검증

구현 전에 실패하는 테스트로 다음 계약을 고정한다.

- `chrome`을 선택하면 영상과 자막 명령에 `--cookies-from-browser chrome`이 포함된다.
- 지원하지 않는 브라우저 값은 media 작업 전에 거부된다.
- Chrome 프로필이나 쿠키 저장소를 읽지 못하면 인증 준비 typed failure가 반환된다.
- 쿠키를 읽은 뒤 발생한 403은 인증된 media 다운로드 실패와 로그인 확인 안내로 반환된다.
- run artifact와 오류 기록에 쿠키 값이나 계정 정보가 들어가지 않는다.
- 영상 다운로드 실패 후 익명·PO Token·HLS fallback이 실행되지 않는다.

집중 테스트와 타입·린트 검사를 통과한 뒤 새 run으로 실제 `protected-run`을 실행한다. 실제 검증은 전체 다운로드, 영상·음성 추출, 저장된 프레임, ASR, source fingerprint 보존까지 확인한다.

## 제외 범위

- 다른 브라우저 지원
- 쿠키 파일 내보내기 또는 장기 보관
- 익명 PO Token provider
- HLS 및 저화질 자동 fallback
- 이전 run의 media 복사 또는 공유 cache
- 실패한 기존 run 복구
- 대시보드와 ASS 변경
