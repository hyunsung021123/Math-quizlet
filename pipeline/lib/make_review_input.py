# -*- coding: utf-8 -*-
"""
make_review_input.py — 한 챕터의 조각 .txt 파일들을 스캔해,
system_prompt.md의 "[모드: 최종복습]" 입력 블록을 자동으로 만들어준다.
chapter_raw/ 안에 그 조각의 결과 JSON이 이미 있으면 정의·정리 이름을 자동으로 뽑아 채우고,
아직 없으면(그 소단원을 아직 생성 전이면) 수동으로 채우라는 빈칸을 남긴다.

사용법:
  python make_review_input.py sections_out "7"                        # 챕터 번호로 필터링
  python make_review_input.py sections_out "7" out.txt                # 결과를 파일로 저장(생략하면 화면 출력)
  python make_review_input.py sections_out "7" out.txt chapter_raw    # 결과 JSON 폴더 직접 지정(기본값 chapter_raw)

챕터 번호는 [문서 맥락]의 "챕터: 7. ..." 줄에서 매칭한다.
"""

import os, re, sys, json

RESULT_TYPES = ("Theorem", "Definition", "Proposition", "Lemma", "Corollary")


def parse_header(txt_path):
    with open(txt_path, encoding="utf-8") as f:
        content = f.read()
    head = content.split("[본문]")[0].split("[연습문제]")[0]
    info = {"chapter_no": "", "chapter_title": "", "section": ""}
    m = re.search(r"챕터:\s*(\S+?)\.?\s*(.*)", head)
    if m:
        info["chapter_no"] = m.group(1).strip()
        info["chapter_title"] = m.group(2).strip()
    m = re.search(r"현재 소단원:\s*(.+)", head)
    if m: info["section"] = m.group(1).strip()
    return info


def summarize_result_json(json_path, max_results=6):
    """완성된 소단원 JSON에서 정의·정리 이름을 뽑아 한 줄 요약을 만든다.
    파일이 없거나 파싱 실패하면 None을 반환한다(호출부가 수동 입력 빈칸으로 폴백)."""
    if not os.path.exists(json_path):
        return None
    try:
        with open(json_path, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None

    names = []
    for sec in data.get("learning_flow", []):
        for block in sec.get("step2_rigorous_logic", []):
            if block.get("type") in RESULT_TYPES:
                names.append(block.get("name") or block.get("type"))
    if not names:
        return data.get("chapter_info", {}).get("overall_goal")
    return ", ".join(names[:max_results]) + (" 등" if len(names) > max_results else "")


def build_review_input(in_dir, chapter_no, exclude_roles=("front",), raw_dir="chapter_raw"):
    files = sorted(f for f in os.listdir(in_dir) if f.endswith(".txt"))
    items = []
    chapter_title = ""
    for fn in files:
        info = parse_header(os.path.join(in_dir, fn))
        if info["chapter_no"] != str(chapter_no):
            continue
        if any(r in fn.lower() for r in exclude_roles):
            continue
        chapter_title = chapter_title or info["chapter_title"]
        stem = re.sub(r"\.txt$", "", fn, flags=re.I)
        summary = summarize_result_json(os.path.join(raw_dir, stem + ".json"))
        items.append((info["section"], summary))

    if not items:
        raise ValueError(f"챕터 '{chapter_no}'에 해당하는 조각을 찾지 못했습니다. "
                         f"'{in_dir}' 폴더와 챕터 번호를 확인하세요.")

    lines = ["[모드: 최종복습]", f"챕터: {chapter_no}. {chapter_title}".strip(),
            "포함 소단원과 핵심 결과:"]
    n_auto = n_missing = 0
    for section, summary in items:
        if summary:
            lines.append(f"- {section}: {summary}")
            n_auto += 1
        else:
            lines.append(f"- {section}: (여기에 그 소단원에서 나온 정의/정리 이름을 채워넣으세요 — {raw_dir}에 결과 JSON이 아직 없습니다)")
            n_missing += 1

    if n_missing:
        lines.append("")
        lines.append(f"# ↑ {n_missing}개 항목은 아직 결과 JSON을 못 찾아 자동으로 못 채웠습니다({n_auto}개는 자동 채움).")
        lines.append("#   위 빈칸을 그 소단원 JSON에서 나온 정의·정리 이름으로 직접 채운 뒤 붙여넣으세요.")
    return "\n".join(lines)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("사용법: python make_review_input.py <조각폴더> <챕터번호> [출력파일] [결과JSON폴더]")
        sys.exit(1)
    in_dir, chapter_no = sys.argv[1], sys.argv[2]
    raw_dir = sys.argv[4] if len(sys.argv) > 4 else "chapter_raw"
    text = build_review_input(in_dir, chapter_no, raw_dir=raw_dir)
    if len(sys.argv) > 3:
        with open(sys.argv[3], "w", encoding="utf-8") as f:
            f.write(text)
        print(f"✅ {sys.argv[3]} 에 저장했습니다.")
    else:
        print(text)
