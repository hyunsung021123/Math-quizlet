#!/bin/bash
# 세션 시작 훅.
# 1) 여러 기기에서 같은 저장소를 다루므로, 매 세션 시작 시 원격(GitHub) 대비 로컬 브랜치의
#    동기화 상태(뒤처짐/앞섬/커밋 안 된 변경)를 확인해 세션 컨텍스트에 띄운다.
#    (로컬 CLI, Claude Code on the web 모두에서 동작한다.)
# 2) (Claude Code on the web 전용) pipeline/ 실행에 필요한 pymupdf 설치 확인
# 3) 현재 git 브랜치와 파이프라인 진행 현황을 요약해 세션 컨텍스트에 자동으로 띄운다
#    (컨테이너가 세션마다 초기화되므로, 매번 상황을 다시 설명하지 않아도 되게 하기 위함)
set -uo pipefail

cd "$CLAUDE_PROJECT_DIR"

echo "## 저장소 상태 요약 (session-start 훅)"
echo ""

# --- 0) GitHub 동기화 상태 확인 (여러 기기 작업 대비) ---
CURRENT_BRANCH="$(git branch --show-current 2>/dev/null || echo '')"
echo "- 현재 브랜치: ${CURRENT_BRANCH:-(알 수 없음)}"

if [ -n "$CURRENT_BRANCH" ] && git remote get-url origin >/dev/null 2>&1; then
  if git fetch origin "$CURRENT_BRANCH" --quiet 2>/dev/null; then
    AHEAD="$(git rev-list --count "origin/$CURRENT_BRANCH..$CURRENT_BRANCH" 2>/dev/null || echo '?')"
    BEHIND="$(git rev-list --count "$CURRENT_BRANCH..origin/$CURRENT_BRANCH" 2>/dev/null || echo '?')"
    if [ "$BEHIND" != "0" ] && [ "$BEHIND" != "?" ]; then
      echo "- ⚠️  원격(origin/$CURRENT_BRANCH)이 로컬보다 커밋 ${BEHIND}개 앞서 있습니다 — 다른 기기에서 작업이 올라왔을 수 있습니다. 작업 전에 'git pull'로 받아오는 것을 권장합니다."
    fi
    if [ "$AHEAD" != "0" ] && [ "$AHEAD" != "?" ]; then
      echo "- 로컬이 원격보다 커밋 ${AHEAD}개 앞서 있습니다(아직 푸시되지 않음)."
    fi
    if [ "$BEHIND" = "0" ] && [ "$AHEAD" = "0" ]; then
      echo "- 원격(origin/$CURRENT_BRANCH)과 동기화되어 있습니다."
    fi
  else
    echo "- (원격 fetch 실패 — 네트워크를 확인하세요. 동기화 상태를 알 수 없습니다.)"
  fi

  if ! git diff --quiet 2>/dev/null || ! git diff --cached --quiet 2>/dev/null; then
    echo "- 커밋되지 않은 로컬 변경사항이 있습니다 ('git status'로 확인하세요)."
  fi
else
  echo "- (원격 저장소 정보를 확인할 수 없습니다.)"
fi
echo ""
echo "- CLAUDE.md / pipeline/README.md 에 저장소 구조와 파이프라인 사용법이 있습니다."
echo ""

# --- 1) 의존성 설치 (idempotent, Claude Code on the web 전용) ---
if [ "${CLAUDE_CODE_REMOTE:-}" = "true" ]; then
  pip install -q --disable-pip-version-check -r pipeline/requirements.txt
fi

# --- 2) 파이프라인 진행 현황 ---
BOOKS_DIR="pipeline/work"
if [ -d "$BOOKS_DIR" ]; then
  for book_dir in "$BOOKS_DIR"/*/; do
    [ -d "$book_dir" ] || continue
    book_slug="$(basename "$book_dir")"
    echo "### 파이프라인 진행 현황 — $book_slug"
    python3 pipeline/program2_track.py --book "$book_slug" status 2>&1 || true
    echo ""
  done
else
  echo "(pipeline/work/ 에 진행 중인 책이 아직 없습니다 — 프로그램 1을 실행하면 생성됩니다.)"
fi
