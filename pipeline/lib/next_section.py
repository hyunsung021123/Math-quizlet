# -*- coding: utf-8 -*-
"""
next_section.py — progress.md에서 "다음에 처리할 조각"을 찾아,
  · 첨부할 .pdf 경로
  · 채팅에 붙여넣을 헤더 텍스트
  · 결과를 저장할 정확한 파일명(.json)
을 한 번에 알려준다. 파일명을 매번 손으로 만들 필요가 없어진다.

사용법:
  # 다음 할 일 보기
  python next_section.py sections_out progress.md

  # 방금 seq 002를 끝냈으면 체크 표시
  python next_section.py sections_out progress.md --done 002
"""

import os, re, sys, json

ROW_RE = re.compile(r'^\|\s*(\d+)\s*\|\s*\[( |x|X)\]\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|\s*`(.+?)`\s*\|\s*$')
SKIP_ROLES = {"front"}


def load_rows(progress_path):
    rows = []
    chapter = None
    with open(progress_path, encoding="utf-8") as f:
        for line in f:
            hm = re.match(r'^##\s+(.+)$', line.rstrip("\n"))
            if hm:
                chapter = hm.group(1).strip()
                continue
            m = ROW_RE.match(line.rstrip("\n"))
            if m:
                rows.append({
                    "seq": m.group(1), "done": m.group(2).lower() == "x",
                    "section": m.group(3), "role": m.group(4), "file": m.group(5),
                    "chapter": chapter, "raw_line": line.rstrip("\n"),
                })
    if not rows:
        raise ValueError(f"'{progress_path}'에서 표 형식 행을 찾지 못했습니다. make_tracker.py로 만든 파일이 맞는지 확인하세요.")
    return rows


def next_pending(rows, skip_roles=SKIP_ROLES):
    for r in rows:
        if r["done"]:
            continue
        if any(role in r["role"] for role in skip_roles):
            continue  # 스킵 후보는 건너뛰고 다음 것을 찾는다
        return r
    return None


def header_of(txt_path):
    with open(txt_path, encoding="utf-8") as f:
        content = f.read()
    idx_body = content.find("[본문]")
    idx_ex = content.find("[연습문제]")
    idxs = [i for i in (idx_body, idx_ex) if i != -1]
    cut = min(idxs) if idxs else len(content)
    return content[:cut].rstrip("\n")


def summarize_prior_json(json_path, max_results=3):
    """완성된 JSON에서 정의/정리 이름을 뽑아 한 줄 요약을 만든다.
    파일이 없거나 파싱 실패하면 None을 반환한다."""
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
            if block.get("type") in ("Theorem", "Definition", "Proposition", "Lemma", "Corollary"):
                label = block.get("name") or block.get("type")
                names.append(label)
    if not names:
        return data.get("chapter_info", {}).get("overall_goal")
    return ", ".join(names[:max_results]) + (" 등" if len(names) > max_results else "")


def enrich_prev_summary(header_text, raw_dir, rows, current_idx, lookback=3):
    """헤더의 '직전 소단원: 「번호 제목」' 줄 뒤에, 같은 챕터 안에서 바로 앞의 최대 `lookback`개
    소단원의 완성된 결과가 raw_dir에 있으면 '이전 소단원 핵심 결과' 블록으로 덧붙인다.
    챕터 경계를 넘어가지 않으며, 결과가 하나도 없으면 헤더를 그대로 둔다."""
    cur_chapter = rows[current_idx]["chapter"]
    candidates = []
    for r in reversed(rows[:current_idx]):
        if r["chapter"] != cur_chapter:
            break  # 챕터 경계 — 더 거슬러 올라가지 않는다
        candidates.append(r)
        if len(candidates) >= lookback:
            break
    candidates.reverse()  # 오래된 것 → 최근 것 순서로 (책 읽는 순서와 동일)

    lines = []
    for r in candidates:
        stem = re.sub(r'\.txt$', '', r["file"], flags=re.I)
        summary = summarize_prior_json(os.path.join(raw_dir, stem + ".json"))
        if summary:
            lines.append(f"- {r['section']}: {summary}")

    if not lines:
        return header_text
    block = "이전 소단원 핵심 결과:\n" + "\n".join(lines)

    if re.search(r'직전 소단원:\s*「[^」]*」', header_text):
        return re.sub(
            r'(직전 소단원:\s*「[^」]*」)',
            lambda m: m.group(1) + "\n" + block,
            header_text,
        )
    # "직전 소단원:" 줄 자체가 없는 경우(예: 책/챕터의 첫 항목이라 pdf_splitter가 그 줄을 안 만든 경우 —
    # 이 경우 그 항목의 모든 분할 파트에도 똑같이 해당 줄이 없다). 이때는 "현재 소단원:" 줄 뒤에 바로 붙여서
    # 블록이 조용히 사라지지 않게 한다.
    if re.search(r'현재 소단원:.*$', header_text, re.M):
        return re.sub(
            r'(현재 소단원:.*)$',
            lambda m: m.group(1) + "\n" + block,
            header_text, count=1, flags=re.M,
        )
    return header_text + "\n" + block


def suggested_json_name(txt_filename):
    return re.sub(r'\.txt$', '.json', txt_filename, flags=re.I)


def show_next(sections_dir, progress_path, raw_dir="chapter_raw"):
    rows = load_rows(progress_path)
    row = next_pending(rows)
    if row is None:
        print("🎉 남은 항목이 없습니다 — 모두 처리했거나 스킵 대상뿐입니다.")
        return None

    txt_path = os.path.join(sections_dir, row["file"])
    pdf_path = re.sub(r'\.txt$', '.pdf', txt_path, flags=re.I)
    if not os.path.exists(txt_path):
        print(f"⚠ '{txt_path}'을 찾을 수 없습니다. sections_dir 경로를 확인하세요.")
        return row

    header = header_of(txt_path)

    # 같은 챕터 안에서 최대 3개까지 앞선 소단원의 완료된 결과를 헤더에 보강한다.
    idx = rows.index(row)
    header = enrich_prev_summary(header, raw_dir, rows, idx, lookback=3)

    out_name = suggested_json_name(row["file"])

    print(f"=== 다음 조각: seq {row['seq']} — {row['section']} ({row['role']}) ===\n")
    print("① 첨부할 PDF:")
    print(f"   {pdf_path}" + ("" if os.path.exists(pdf_path) else "  ⚠ 파일 없음(EMIT_PDF=False였을 수 있음)"))
    print("\n② 채팅에 붙여넣을 헤더 (그대로 복사):\n")
    print(header)
    print("\n③ 결과 저장 파일명:")
    print(f"   {raw_dir}/{out_name}")
    print(f"\n완료 후: python next_section.py {sections_dir} {progress_path} --done {row['seq']}")
    return row


def mark_done(progress_path, seq):
    rows = load_rows(progress_path)
    padded = str(seq).zfill(len(rows[0]["seq"])) if rows else str(seq)
    matches = [r for r in rows if r["seq"] in (padded, str(seq))]
    if not matches:
        print(f"⚠ seq '{seq}'를 찾지 못했습니다.")
        return False

    # 같은 순번을 가진 여러 행(분할 part 1/2/3 등) 중, 아직 안 끝난 첫 번째 것을 목표로 삼는다.
    # (항상 첫 번째 행만 고르면 다시 호출해도 그 행만 계속 [x]로 남고 나머지 part가 영원히 안 끝난다.)
    target = next((r for r in matches if not r["done"]), None)
    if target is None:
        print(f"ℹ seq {seq}의 모든 항목이 이미 완료 상태입니다({len(matches)}개 분할 포함).")
        return False

    with open(progress_path, encoding="utf-8") as f:
        lines = f.readlines()
    new_lines = []
    changed = False
    for line in lines:
        if line.rstrip("\n") == target["raw_line"]:
            new_lines.append(line.replace("[ ]", "[x]", 1))
            changed = True
        else:
            new_lines.append(line)
    if changed:
        with open(progress_path, "w", encoding="utf-8") as f:
            f.writelines(new_lines)
        remaining = sum(1 for r in matches if r["done"] is False) - 1
        note = f" ({remaining}개 분할 더 남음 — 같은 seq로 다시 --done 호출)" if remaining > 0 else ""
        print(f"✅ seq {seq} 체크 완료.{note}")
    return changed


def mark_undone(progress_path, seq, raw_dir=None, delete_json=True):
    """mark_done의 반대. 잘못 만든 결과를 다시 만들고 싶을 때, 체크를 [x] -> [ ]로 되돌린다.
    raw_dir을 주면(기본 동작) 그 항목에 대응하는 결과 JSON이 있을 경우 함께 삭제해서,
    바로 재생성할 수 있게 정리해 준다. 삭제를 원치 않으면 delete_json=False로 호출한다."""
    rows = load_rows(progress_path)
    padded = str(seq).zfill(len(rows[0]["seq"])) if rows else str(seq)
    matches = [r for r in rows if r["seq"] in (padded, str(seq))]
    if not matches:
        print(f"⚠ seq '{seq}'를 찾지 못했습니다.")
        return False

    # 여러 행이 같은 순번을 공유하는 예전 방식 progress.md와의 호환을 위해,
    # 완료된 것 중 가장 나중 행(뒤쪽 part)부터 하나씩 되돌린다.
    target = next((r for r in reversed(matches) if r["done"]), None)
    if target is None:
        print(f"ℹ seq {seq}는 아직 완료 표시가 없어서 취소할 게 없습니다.")
        return False

    with open(progress_path, encoding="utf-8") as f:
        lines = f.readlines()
    new_lines = []
    changed = False
    for line in lines:
        if line.rstrip("\n") == target["raw_line"]:
            new_lines.append(re.sub(r'\[[xX]\]', '[ ]', line, count=1))
            changed = True
        else:
            new_lines.append(line)
    if not changed:
        print(f"⚠ seq {seq}에 해당하는 줄을 파일에서 찾지 못했습니다(progress.md가 그 사이 바뀌었을 수 있음).")
        return False

    with open(progress_path, "w", encoding="utf-8") as f:
        f.writelines(new_lines)
    print(f"↩ seq {seq} ({target['section']}) 체크 취소됨.")

    if delete_json:
        raw_dir = raw_dir or "chapter_raw"
        stem = re.sub(r'\.txt$', '', target["file"], flags=re.I)
        json_path = os.path.join(raw_dir, stem + ".json")
        if os.path.exists(json_path):
            os.remove(json_path)
            print(f"  🗑 기존 결과 파일 삭제함: {json_path}")
        else:
            print(f"  (참고: {json_path} 파일은 원래 없었습니다 — 지울 게 없음)")
    return True


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("사용법: python next_section.py <조각폴더> <progress.md> [--done <seq> | --undone <seq>]")
        sys.exit(1)
    sections_dir, progress_path = sys.argv[1], sys.argv[2]
    if len(sys.argv) > 4 and sys.argv[3] == "--done":
        mark_done(progress_path, sys.argv[4])
        print()
        show_next(sections_dir, progress_path)
    elif len(sys.argv) > 4 and sys.argv[3] == "--undone":
        mark_undone(progress_path, sys.argv[4])
        print()
        show_next(sections_dir, progress_path)
    else:
        show_next(sections_dir, progress_path)
