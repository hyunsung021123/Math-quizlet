# -*- coding: utf-8 -*-
"""
프로그램 1 — 전공서 분할기 (Claude Code 런타임용)

입력: 전공서 전체 PDF + toc_data.json + 오프셋(정수)
동작:
  1) pdf_splitter 로 소단원 단위 분할 (.txt 헤더 + .pdf)  → work/<책 슬러그>/sections_out/
  2) make_tracker 로 진행 체크리스트 생성                  → work/<책 슬러그>/progress.md
  3) work/<책 슬러그>/chapter_raw/ (완성 JSON 보관함) 준비
  4) 상태 메타(state.json) 및 toc_data.json 사본 기록
  5) 전달용 zip 생성 (txt+pdf 전부)                        → work/<책 슬러그>/_delivery/<book>_sections.zip

여러 책을 동시에 다룰 수 있도록, 상태 폴더는 책마다 분리된다: work/<책 슬러그>/...
책 슬러그는 toc_data.json의 "book" 필드(또는 --book-name)에서 만든다(lib/merger.slug_id).
따라서 --work-dir 을 직접 지정하지 않는 한, 서로 다른 책끼리는 절대 상태가 섞이지 않는다.

커밋 정책(대화에서 확정): 텍스트 상태(.txt, progress.md, chapter_raw, state.json, toc_data.json)만 git 에 올린다.
  분할 PDF와 zip 은 .gitignore 로 제외하고, zip 은 사용자에게 직접 전송한다.

사용 예:
  python pipeline/program1_split.py --pdf book.pdf --toc toc_data.json --offset 15
  python pipeline/program1_split.py --pdf book.pdf --toc toc_data.json --offset 15 \
        --offset-override '{"App.A": 13}'
"""

import os, sys, json, shutil, argparse

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, "lib"))

import pdf_splitter as S      # noqa: E402
import make_tracker as T      # noqa: E402
from merger import slug_id    # noqa: E402


def run(pdf, toc, offset, offset_overrides=None, work_dir=None, book_name=None):
    if not os.path.exists(pdf):
        raise FileNotFoundError(f"PDF 를 찾을 수 없습니다: {pdf}")
    if not os.path.exists(toc):
        raise FileNotFoundError(f"toc_data.json 을 찾을 수 없습니다: {toc}")

    with open(toc, encoding="utf-8") as f:
        toc_data = json.load(f)
    book = book_name or toc_data.get("book") or os.path.splitext(os.path.basename(pdf))[0].strip()
    author = toc_data.get("author", "")

    # 책마다 별도 폴더에 상태를 둔다 — 여러 전공서를 동시에 다뤄도 서로 섞이지 않는다.
    work_dir = work_dir or os.path.join(HERE, "work", slug_id(book))
    sections_out = os.path.join(work_dir, "sections_out")
    chapter_raw = os.path.join(work_dir, "chapter_raw")
    delivery = os.path.join(work_dir, "_delivery")
    progress = os.path.join(work_dir, "progress.md")

    # 이 책 폴더에 기존 상태가 있으면 알린다(같은 책을 다시 분할해 진행 체크가 초기화될 수 있음).
    warn_existing = os.path.exists(sections_out) or os.path.exists(progress)

    os.makedirs(work_dir, exist_ok=True)
    os.makedirs(chapter_raw, exist_ok=True)
    if os.path.exists(delivery):
        shutil.rmtree(delivery)
    os.makedirs(delivery)

    # --- 1) 분할 ---
    S.MANUAL_OFFSET = int(offset)
    S.OFFSET_OVERRIDES = offset_overrides or {}
    S.EMIT_TXT = True
    S.EMIT_PDF = True
    print(f"[프로그램 1] 분할 시작 — 오프셋 {offset}"
          + (f", override {S.OFFSET_OVERRIDES}" if S.OFFSET_OVERRIDES else ""))
    S.run(pdf_path=pdf, toc_path=toc, out_dir=sections_out)

    # --- 2) 트래커 ---
    _, rows = T.build_tracker(sections_out, progress)
    print(f"\n[프로그램 1] progress.md 생성 — {len(rows)}개 항목")

    # --- 3) 상태 메타 + toc_data.json 사본(재현·재실행용) ---
    with open(os.path.join(work_dir, "toc_data.json"), "w", encoding="utf-8") as f:
        json.dump(toc_data, f, ensure_ascii=False, indent=2)
    state = {
        "book": book,
        "author": author,
        "offset": int(offset),
        "offset_overrides": S.OFFSET_OVERRIDES,
        "n_sections": len(rows),
        "source_pdf": os.path.basename(pdf),
    }
    with open(os.path.join(work_dir, "state.json"), "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

    # --- 4) 전달용 zip (txt+pdf 전부) ---
    zip_stem = slug_id(book) + "_sections"
    zip_path = shutil.make_archive(os.path.join(delivery, zip_stem), "zip", sections_out)

    n_pdf = len([f for f in os.listdir(sections_out) if f.endswith(".pdf")])
    n_txt = len([f for f in os.listdir(sections_out) if f.endswith(".txt")])
    print("\n" + "=" * 56)
    print(f"완료 — 책: {book}" + (f" / 저자: {author}" if author else ""))
    print(f"  · 상태 폴더: {work_dir}")
    print(f"  · 소단원 조각: {len(rows)}개  (.txt {n_txt} / .pdf {n_pdf})")
    print(f"  · 진행 체크리스트: {progress}")
    print(f"  · 전달용 zip: {zip_path}")
    if warn_existing:
        print(f"  ⚠ 이 책의 기존 상태를 덮어썼습니다({work_dir}) — 이미 진행 체크가 있었다면 확인 필요.")
    print("=" * 56)
    return {"zip": zip_path, "progress": progress, "state": state, "n_rows": len(rows),
            "warn_existing": warn_existing, "work_dir": work_dir, "book": book}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="프로그램 1 — 전공서 PDF 분할기")
    ap.add_argument("--pdf", required=True, help="전공서 전체 PDF 경로")
    ap.add_argument("--toc", required=True, help="toc_data.json 경로")
    ap.add_argument("--offset", required=True, type=int,
                    help="인쇄 쪽수 → 실제 PDF 쪽수 오프셋 (PDF쪽 - 인쇄쪽)")
    ap.add_argument("--offset-override", default=None,
                    help='소단원별 오프셋 예외(JSON 문자열). 예: \'{"App.A": 13}\'')
    ap.add_argument("--work-dir", default=None,
                    help="작업/상태 폴더 강제 지정(기본은 pipeline/work/<책 슬러그>, 책마다 자동 분리됨)")
    ap.add_argument("--book-name", default=None, help="책 표시명 강제 지정(기본 toc_data.json의 book)")
    args = ap.parse_args()
    ov = json.loads(args.offset_override) if args.offset_override else None
    run(args.pdf, args.toc, args.offset, ov, args.work_dir, args.book_name)
