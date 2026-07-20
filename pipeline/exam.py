#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
시험 대비 flashcard 세트 관리 CLI (임시 콘텐츠 전용).

전공서 학습 콘텐츠(data/*.json, data/manifest.json)와 **완전히 분리된** data/exam/ 영역만
건드린다. 그래서 이 스크립트로 무엇을 하든 전공서 학습 데이터에는 절대 영향이 없다.

시험 flashcard 세트 하나 = data/exam/<id>.json 파일 하나 + data/exam/manifest.json 의 항목 하나.
JSON 내용 스키마는 전공서 챕터와 **똑같다** (chapter_info + learning_flow[].step3_checkpoint …) —
그래서 index.html 의 기존 카드 덱 엔진이 그대로 렌더링한다. 기출문제는 보통 각 문제를
step3_checkpoint 의 checkpoint 항목(problem/hint/solution)으로 담는다(자세한 작성 기준은
prompts/exam_prompt.md 참고).

사용법:
  python pipeline/exam.py add --json <파일> [--title "제목"] [--id <id>] [--book "그룹명"]
        # 검증 → data/exam/ 로 복사 → data/exam/manifest.json 에 등록 (같은 id 면 덮어쓰기)
  python pipeline/exam.py list                         # 현재 등록된 시험 세트 목록
  python pipeline/exam.py remove --id <id>             # 세트 하나 삭제(json + manifest 항목)
  python pipeline/exam.py clear [--yes]                # 모든 시험 세트 비우기(전공서엔 영향 없음)

id 는 항상 'exam__' 로 시작하도록 강제한다 — 브라우저 localStorage 의 진행상태/마지막위치가
전공서 주제 id 와 절대 충돌하지 않게 하기 위함(세트를 지웠다 새로 만들어도 옛 진행상태가
안 딸려온다).
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

# pipeline/ 를 import 경로에 두고 lib.merger 재사용 (program2_track / orchestrate 와 동일 방식)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import merger  # noqa: E402

# Windows 콘솔의 cp949 UnicodeEncodeError 방지 (한글/em-dash 출력)
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

REPO_ROOT = Path(__file__).resolve().parent.parent
EXAM_DIR = REPO_ROOT / "data" / "exam"
MANIFEST = EXAM_DIR / "manifest.json"
DEFAULT_BOOK = "📝 시험 대비"


def _load_manifest():
    if not MANIFEST.exists():
        return {"topics": []}
    try:
        m = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"⚠ manifest.json 을 읽지 못했습니다 ({e}) — 빈 목록으로 취급합니다.")
        return {"topics": []}
    if not isinstance(m.get("topics"), list):
        m = {"topics": []}
    return m


def _save_manifest(m):
    EXAM_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(m, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _norm_id(raw):
    """항상 exam__ 접두사가 붙은 안전한 slug 로 만든다(한글 허용 — id 는 브라우저 문자열일 뿐)."""
    slug = merger.slug_id(raw)
    if not slug.startswith("exam__"):
        # slug_id 는 '__' 를 '_' 로 눌러버리므로 접두사는 slug 후에 붙인다
        slug = "exam__" + slug
    return slug


def _ascii_file(topic_id):
    """실제 저장·HTTP 로 노출되는 '파일명'은 ASCII 로만 만든다(GitHub Pages 한글 파일명 리스크 회피).
    id 는 한글일 수 있으므로, ASCII 부분만 남기고 비면 id 해시로 대체해 항상 유일하게 한다."""
    ascii_part = re.sub(r'[^a-z0-9_]+', '', topic_id.lower())
    ascii_part = re.sub(r'_+', '_', ascii_part).strip('_')
    if ascii_part in ("", "exam"):
        ascii_part = "exam"
    h = hashlib.md5(topic_id.encode("utf-8")).hexdigest()[:8]
    return f"{ascii_part}_{h}.json"


def cmd_add(args):
    src = Path(args.json)
    if not src.exists():
        print(f"✗ 입력 파일이 없습니다: {src}")
        return 1
    try:
        parsed = json.loads(src.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"✗ JSON 파싱 실패: {e}")
        return 1

    # 제목 보정: --title 이 있으면 chapter_info.title 로 심는다(없으면 파일 내용 그대로)
    if args.title:
        parsed.setdefault("chapter_info", {})
        parsed["chapter_info"]["title"] = args.title

    # 검증 (lib/merger.py — 전공서와 동일 규칙). err 가 있으면 저장 거부, warn 은 통과.
    res = merger.validate_item(parsed)
    level = merger.worst_level(res["msgs"])
    for msg in res["msgs"]:
        mark = {"err": "✗", "warn": "⚠", "ok": "✓"}.get(msg["level"], "·")
        print(f"  {mark} {msg['text']}")
    if level == "err":
        print("✗ 오류가 있어 저장하지 않았습니다. 위 ✗ 항목을 고친 뒤 다시 시도하세요.")
        return 1

    title = args.title or (parsed.get("chapter_info") or {}).get("title") or src.stem
    base = args.id or title
    topic_id = _norm_id(base)
    book = args.book or DEFAULT_BOOK
    fname = _ascii_file(topic_id)

    # 같은 id 로 재등록 시 옛 파일명이 다르면 그 파일을 먼저 지운다(고아 파일 방지)
    for t in _load_manifest()["topics"]:
        if t.get("id") == topic_id and t.get("file") and t["file"] != fname:
            old = EXAM_DIR / t["file"]
            if old.exists():
                old.unlink()

    EXAM_DIR.mkdir(parents=True, exist_ok=True)
    (EXAM_DIR / fname).write_text(
        json.dumps(parsed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    m = _load_manifest()
    entry = {"id": topic_id, "title": title, "file": fname, "book": book}
    topics = [t for t in m["topics"] if t.get("id") != topic_id]  # 같은 id 있으면 교체
    replaced = len(topics) != len(m["topics"])
    topics.append(entry)
    m["topics"] = topics
    _save_manifest(m)

    action = "교체" if replaced else "추가"
    print(f"✓ 시험 세트 {action} 완료")
    print(f"    id    : {topic_id}")
    print(f"    title : {title}")
    print(f"    group : {book}")
    print(f"    file  : data/exam/{fname}")
    print("→ 커밋·푸시하면 사이트 주제 드롭다운의 '" + book + "' 그룹에 나타납니다.")
    return 0


def cmd_list(args):
    m = _load_manifest()
    if not m["topics"]:
        print("(등록된 시험 세트가 없습니다 — data/exam/ 가 비어 있습니다.)")
        return 0
    print(f"등록된 시험 세트 {len(m['topics'])}개:")
    for t in m["topics"]:
        exists = (EXAM_DIR / (t.get("file") or "")).exists()
        flag = "" if exists else "  ⚠(파일 없음)"
        print(f"  · [{t.get('book','')}] {t.get('title','?')}  (id={t.get('id')}){flag}")
    return 0


def cmd_remove(args):
    m = _load_manifest()
    target = [t for t in m["topics"] if t.get("id") == args.id]
    if not target:
        print(f"✗ 그런 id 의 세트가 없습니다: {args.id}")
        print("  현재 목록을 보려면: python pipeline/exam.py list")
        return 1
    for t in target:
        f = EXAM_DIR / (t.get("file") or "")
        if f.exists():
            f.unlink()
            print(f"  삭제: data/exam/{f.name}")
    m["topics"] = [t for t in m["topics"] if t.get("id") != args.id]
    _save_manifest(m)
    print(f"✓ 세트 삭제 완료: {args.id}")
    return 0


def cmd_clear(args):
    m = _load_manifest()
    n = len(m["topics"])
    if not args.yes:
        print(f"이 작업은 시험 세트 {n}개(data/exam/*.json)를 모두 지웁니다. "
              "전공서 학습 데이터(data/*.json)에는 영향이 없습니다.")
        print("정말 지우려면 --yes 를 붙여 다시 실행하세요:")
        print("  python pipeline/exam.py clear --yes")
        return 0
    removed = 0
    for f in EXAM_DIR.glob("*.json"):
        if f.name == "manifest.json":
            continue
        f.unlink()
        removed += 1
    _save_manifest({"topics": []})
    print(f"✓ 시험 세트 전체 비움 (json {removed}개 삭제, manifest 초기화). 전공서 데이터는 그대로입니다.")
    return 0


def main():
    ap = argparse.ArgumentParser(description="시험 대비 flashcard 세트 관리 (임시, data/exam/ 전용)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add", help="시험 세트 추가/교체")
    a.add_argument("--json", required=True, help="등록할 flashcard JSON 파일 경로")
    a.add_argument("--title", help="주제 제목(생략 시 chapter_info.title 또는 파일명)")
    a.add_argument("--id", help="세트 id 기준 문자열(자동으로 exam__ 접두사가 붙음)")
    a.add_argument("--book", help=f"드롭다운 그룹명(기본: {DEFAULT_BOOK})")
    a.set_defaults(func=cmd_add)

    li = sub.add_parser("list", help="등록된 시험 세트 목록")
    li.set_defaults(func=cmd_list)

    r = sub.add_parser("remove", help="세트 하나 삭제")
    r.add_argument("--id", required=True, help="삭제할 세트 id")
    r.set_defaults(func=cmd_remove)

    c = sub.add_parser("clear", help="모든 시험 세트 비우기")
    c.add_argument("--yes", action="store_true", help="확인 없이 즉시 비움")
    c.set_defaults(func=cmd_clear)

    args = ap.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
