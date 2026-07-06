#!/bin/bash
# 세션 시작 훅 — Claude Code on the web 전용.
# 1) pipeline/ 실행에 필요한 pymupdf 설치 확인
# 2) 현재 git 브랜치와 파이프라인 진행 현황을 요약해 세션 컨텍스트에 자동으로 띄운다
#    (컨테이너가 세션마다 초기화되므로, 매번 상황을 다시 설명하지 않아도 되게 하기 위함)
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "$CLAUDE_PROJECT_DIR"

# --- 1) 의존성 설치 (idempotent) ---
pip install -q --disable-pip-version-check -r pipeline/requirements.txt

# --- 2) 상황 요약 출력 ---
echo "## 저장소 상태 요약 (session-start 훅)"
echo ""
echo "- 현재 브랜치: $(git branch --show-current 2>/dev/null || echo '(알 수 없음)')"
echo "- CLAUDE.md / pipeline/README.md 에 저장소 구조와 파이프라인 사용법이 있습니다."
echo ""

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
