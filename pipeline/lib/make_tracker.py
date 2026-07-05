# -*- coding: utf-8 -*-
"""
make_tracker.py — sections_out/ 폴더를 스캔해 진행 체크리스트(progress.md)를 생성한다.

용도:
  · pdf_splitter.py가 만든 수십 개 조각을 한눈에 보고, role="front" 등 스킵할 것을 표시(⏭)한 뒤
  · 실제로 시스템 프롬프트에 넣어 생성할 때마다 체크(- [x])해서 진행 상황을 놓치지 않게 한다.

사용법:
  python make_tracker.py                      # sections_out/ → progress.md
  python make_tracker.py 다른_폴더 결과.md      # 경로 직접 지정
"""

import os, re, sys

SKIP_ROLE_HINT = {"front"}   # 기본적으로 "스킵 후보"로 표시할 role


def parse_header(txt_path):
    """조각 .txt 파일의 [문서 맥락] 헤더에서 챕터/소단원/역할 정보를 읽는다."""
    with open(txt_path, encoding="utf-8") as f:
        content = f.read()
    head = content.split("[본문]")[0].split("[연습문제]")[0]
    info = {"chapter": "", "section": ""}
    m = re.search(r"챕터:\s*(.+)", head)
    if m: info["chapter"] = m.group(1).strip()
    m = re.search(r"현재 소단원:\s*(.+)", head)
    if m: info["section"] = m.group(1).strip()
    info["tag"] = "[연습문제]" if "[연습문제]" in content[:len(head) + 20] else "[본문]"
    return info


def infer_role(stem, section_label):
    """파일명/소단원 라벨에서 role 힌트를 추정(트리아지 표시용, 실제 role은 toc_data.json에 있었지만
    이 시점엔 .txt만 있으므로 라벨 패턴으로 추정한다)."""
    s = (stem + " " + section_label).lower()
    if "front" in s or "preface" in s:
        return "front"
    if ".intro" in s or "개관" in section_label:
        return "intro"
    if "__ex" in s or "연습문제" in section_label or ".ex" in s:
        return "exercises"
    if "appendix" in s or "부록" in section_label:
        return "appendix"
    return "section"


def build_tracker(in_dir, out_path):
    files = sorted(f for f in os.listdir(in_dir) if f.endswith(".txt"))
    if not files:
        raise FileNotFoundError(f"'{in_dir}'에 .txt 파일이 없습니다. pdf_splitter.py를 먼저 실행하세요.")

    rows = []
    width = len(str(len(files) - 1)) if len(files) > 1 else 3
    width = max(width, 3)
    for row_idx, fn in enumerate(files):
        # 순번은 파일명의 그룹 접두어(분할된 조각들이 공유함)를 그대로 쓰지 않고,
        # 행마다 무조건 고유하도록 새로 매긴다(000, 001, 002 … 절대 겹치지 않음).
        # 원본 그룹 번호(파일명 접두어)는 참고용으로 따로 남겨둔다.
        seq = str(row_idx).zfill(width)
        group_m = re.match(r"^(\d+)__", fn)
        info = parse_header(os.path.join(in_dir, fn))
        role = infer_role(fn, info["section"])
        rows.append({"seq": seq, "group": group_m.group(1) if group_m else "?",
                    "file": fn, "chapter": info["chapter"],
                    "section": info["section"], "role": role, "tag": info["tag"]})

    by_chapter = {}
    for r in rows:
        by_chapter.setdefault(r["chapter"] or "(챕터 정보 없음)", []).append(r)

    lines = ["# 생성 진행 체크리스트", "",
            f"총 {len(rows)}개 조각. `role`이 `front`인 항목은 기본적으로 **스킵 후보**로 표시했습니다 — ",
            "필요 없으면 그 줄을 지우거나 체크만 하고 넘어가세요.",
            "(순번은 행마다 고유합니다. 분할된 조각도 서로 다른 순번을 가지니 그대로 순서대로 처리하면 됩니다.)", ""]

    for chapter, items in by_chapter.items():
        lines.append(f"## {chapter}")
        lines.append("")
        lines.append("| 순번 | 상태 | 소단원 | 역할 | 파일 |")
        lines.append("|---|---|---|---|---|")
        for r in items:
            skip_mark = " ⏭️스킵후보" if r["role"] in SKIP_ROLE_HINT else ""
            group_hint = f" _(원본 #{r['group']})_" if r["file"].count("__p") or "__p" in r["file"] else ""
            lines.append(f"| {r['seq']} | [ ] | {r['section']}{group_hint} | {r['role']}{skip_mark} | `{r['file']}` |")
        lines.append("")

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return out_path, rows


if __name__ == "__main__":
    in_dir = sys.argv[1] if len(sys.argv) > 1 else "sections_out"
    out_path = sys.argv[2] if len(sys.argv) > 2 else "progress.md"
    path, rows = build_tracker(in_dir, out_path)
    n_skip = sum(1 for r in rows if r["role"] in SKIP_ROLE_HINT)
    print(f"✅ {path} 생성 완료 — 총 {len(rows)}개 항목, 스킵 후보 {n_skip}개")
