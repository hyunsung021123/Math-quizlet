# -*- coding: utf-8 -*-
"""
프로그램 2 — 작업 추적기 / 수집·검증·병합기 (Claude Code 런타임용)

프로그램 1이 만든 상태(work/sections_out/*.txt, work/progress.md, work/chapter_raw/)를 읽어:
  · next        : 다음에 만들 소단원의 [번호 · 파일 full name · 붙여넣을 헤더 · 저장 파일명]을 안내
  · submit      : 완성된 소단원/복습 JSON을 받아 검증 → chapter_raw 저장 → progress 갱신 → 다음 안내
                  (이미 있으면 덮어씀 = 재업로드도 이 명령으로 처리)
  · undo        : 특정 seq 를 미완료로 되돌리고 결과 JSON 삭제(다시 만들고 싶을 때)
  · status      : 챕터별 진행 현황 요약
  · merge       : 특정 챕터를 강제로 병합해 data/ 에 반영(보통은 submit 이 자동 처리)

챕터 '완료' 기준(대화에서 확정): 그 챕터의 모든 소단원 + 최종복습 JSON 까지 있어야 완료.
완료 시: 병합본을 data/<slug>.json 으로 쓰고 data/manifest.json 을 갱신한다.
        (git commit/push 와 사용자 전송은 오케스트레이터(Claude)가 담당 — 이 스크립트는 파일만 만든다.)

검증은 section_merger.html 규칙을 그대로 이식한 lib/merger.py 를 쓴다.
'err' 이 있으면 저장/완료 처리하지 않고 경고만 보고한다('경고나 위험 공지' 요구사항).

여러 책 지원: 상태는 work/<책 슬러그>/ 로 책마다 분리되어 있다(program1_split.py 참고).
--book 으로 어느 책인지 지정하고, 생략하면 work/ 안에 책 폴더가 하나뿐일 때만 자동으로 그 책을 쓴다
(여러 책이 있으면 목록을 보여주고 --book 을 요구한다). --work-dir 을 직접 주면 그 값이 최우선이다.

순서 보장: 같은 챕터 안에서는 소단원을 반드시 순서대로(seq 오름차순) 완성해야 한다 — next_section.py의
헤더 보강(enrich_prev_summary)이 '직전 최대 3개 소단원'의 chapter_raw 결과를 참조하기 때문이다. submit 은
같은 챕터에서 더 앞선 seq가 아직 미완료인데 뒤의 seq를 제출하면 경고를 낸다(막지는 않되 반드시 알린다).
"""

import os, re, sys, json, argparse

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, "lib"))

import next_section as N        # noqa: E402
import make_review_input as R   # noqa: E402
import merger as M              # noqa: E402


# ----------------------------- 경로 -----------------------------
def resolve_work_dir(work_dir=None, book=None):
    """--work-dir > --book > 자동감지(책 폴더가 하나뿐일 때만) 순으로 상태 폴더를 정한다."""
    if work_dir:
        return work_dir
    base = os.path.join(HERE, "work")
    if book:
        return os.path.join(base, M.slug_id(book))
    candidates = sorted(d for d in os.listdir(base)) if os.path.isdir(base) else []
    candidates = [d for d in candidates if os.path.isdir(os.path.join(base, d))]
    if len(candidates) == 1:
        return os.path.join(base, candidates[0])
    if not candidates:
        print(f"✗ '{base}' 에 책 상태 폴더가 없습니다. 프로그램 1을 먼저 실행하세요.")
        sys.exit(1)
    print(f"△ 여러 책이 있습니다 — --book 으로 지정하세요: {', '.join(candidates)}")
    sys.exit(1)


def paths(work_dir=None, data_dir=None, book=None):
    work_dir = resolve_work_dir(work_dir, book)
    return {
        "work": work_dir,
        "sections": os.path.join(work_dir, "sections_out"),
        "raw": os.path.join(work_dir, "chapter_raw"),
        "progress": os.path.join(work_dir, "progress.md"),
        "state": os.path.join(work_dir, "state.json"),
        "data": data_dir or os.path.join(REPO_ROOT, "data"),
    }


def _norm(s):
    return " ".join(str(s or "").split()).lower()


def _strip_part(label):
    # "7.3 Zonotopes (분할 part 2)" → "7.3 Zonotopes"
    return re.sub(r'\s*\(분할\s*part\s*\d+\)\s*$', '', str(label or "")).strip()


def _chapter_no(chapter_label):
    # "7. Minkowski Sums and Zonotopes" → "7";  "0. Introduction" → "0"
    m = re.match(r'\s*([0-9A-Za-z]+)', str(chapter_label or ""))
    return m.group(1) if m else ""


def load_state(P):
    if os.path.exists(P["state"]):
        with open(P["state"], encoding="utf-8") as f:
            return json.load(f)
    return {}


# ----------------------------- 검증 출력 -----------------------------
def print_msgs(msgs, indent="   "):
    for m in msgs:
        icon = {"err": "✗", "warn": "△", "ok": "✓"}.get(m["level"], "·")
        print(f"{indent}{icon} {m['text']}")


# ----------------------------- 매칭 -----------------------------
def match_section_row(parsed, rows, seq=None, filename=None):
    """완성된 소단원 JSON을 progress 행에 매칭한다.
    우선순위: 명시 seq > 파일명 stem > section_title 내용.
    반환: (row, [후보들]) — row 가 None 이면 후보 목록으로 사용자가 판단."""
    # 1) 명시 seq
    if seq is not None:
        padded = str(seq).zfill(len(rows[0]["seq"])) if rows else str(seq)
        cand = [r for r in rows if r["seq"] in (padded, str(seq))]
        # 분할 part 여러 행이면 아직 안 끝난 첫 행
        pend = [r for r in cand if not r["done"]]
        return ((pend[0] if pend else (cand[0] if cand else None)), cand)

    # 2) 업로드 파일명 stem (변형 안 됐을 때만 신뢰)
    if filename:
        stem = re.sub(r'\.json$', '', os.path.basename(filename), flags=re.I)
        cand = [r for r in rows if re.sub(r'\.txt$', '', r["file"], flags=re.I) == stem]
        if len(cand) == 1:
            return (cand[0], cand)

    # 3) section_title 내용 매칭
    flow = parsed.get("learning_flow") or []
    titles = [_norm(_strip_part(s.get("section_title"))) for s in flow if s.get("section_title")]
    cand = []
    for r in rows:
        row_sec = _norm(_strip_part(r["section"]))
        if any(row_sec == t or (t and (row_sec in t or t in row_sec)) for t in titles):
            cand.append(r)
    if len(cand) == 1:
        return (cand[0], cand)
    # 여러 후보 중 아직 안 끝난 것 우선(재업로드가 아니라 신규일 때)
    pend = [r for r in cand if not r["done"]]
    if len(pend) == 1:
        return (pend[0], cand)
    return (None, cand)


# ----------------------------- 챕터 완료 감지 -----------------------------
def chapter_rows(rows, chno):
    return [r for r in rows if _chapter_no(r["chapter"]) == str(chno)
            and not any(sk in r["role"] for sk in N.SKIP_ROLES)]


def review_path(P, chno):
    return os.path.join(P["raw"], f"__review_ch{chno}.json")


def sections_complete(P, rows, chno):
    crows = chapter_rows(rows, chno)
    if not crows:
        return False, [], []
    missing = []
    for r in crows:
        stem = re.sub(r'\.txt$', '', r["file"], flags=re.I)
        jp = os.path.join(P["raw"], stem + ".json")
        if not r["done"] or not os.path.exists(jp):
            missing.append(r)
    return (len(missing) == 0), crows, missing


def try_finalize_chapter(P, chno):
    """그 챕터의 소단원+복습이 모두 준비됐으면 병합해 data/ 에 반영한다.
    반환 dict: {"status": "merged"|"need_review"|"incomplete", ...}"""
    rows = N.load_rows(P["progress"])
    done, crows, missing = sections_complete(P, rows, chno)
    if not done:
        return {"status": "incomplete", "missing": [f"{r['seq']} {r['section']}" for r in missing]}

    rp = review_path(P, chno)
    if not os.path.exists(rp):
        # 소단원은 다 됐지만 최종복습이 아직 → 복습 입력 생성해서 안내
        review_input = None
        try:
            text = R.build_review_input(P["sections"], str(chno), raw_dir=P["raw"])
            ri_path = os.path.join(P["work"], f"_review_input_ch{chno}.txt")
            with open(ri_path, "w", encoding="utf-8") as f:
                f.write(text)
            review_input = ri_path
        except Exception as e:
            review_input = f"(복습 입력 생성 실패: {e})"
        return {"status": "need_review", "review_input": review_input}

    # 소단원 + 복습 전부 준비됨 → 병합
    items = []
    for r in crows:
        stem = re.sub(r'\.txt$', '', r["file"], flags=re.I)
        jp = os.path.join(P["raw"], stem + ".json")
        with open(jp, encoding="utf-8") as f:
            items.append({"kind": "section", "parsed": json.load(f)})
    with open(rp, encoding="utf-8") as f:
        items.append({"kind": "review", "parsed": json.load(f)})

    chapter = M.merge(items)
    title = chapter.get("chapter_info", {}).get("title") or f"Chapter {chno}"
    cid = M.slug_id(title)
    os.makedirs(P["data"], exist_ok=True)
    out_file = os.path.join(P["data"], cid + ".json")
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(chapter, f, ensure_ascii=False, indent=2)

    manifest_changed = update_manifest(P, cid, title, cid + ".json")
    return {"status": "merged", "file": out_file, "id": cid, "title": title,
            "manifest_changed": manifest_changed,
            "n_sections": len(chapter.get("learning_flow", [])),
            "n_ox": len(chapter.get("step4_final_review", {}).get("ox_quizzes", [])),
            "n_cq": len(chapter.get("step4_final_review", {}).get("conceptual_questions", []))}


def update_manifest(P, cid, title, filename):
    state = load_state(P)
    book = state.get("book", "")
    author = state.get("author", "")
    # 기존 manifest 항목들의 book 표기와 최대한 맞춘다.
    book_disp = book
    if author:
        last = author.split()[-1] if author.split() else author
        book_disp = f"{last} — {book}" if book else author

    mpath = os.path.join(P["data"], "manifest.json")
    if os.path.exists(mpath):
        with open(mpath, encoding="utf-8") as f:
            manifest = json.load(f)
    else:
        manifest = {"topics": []}
    topics = manifest.setdefault("topics", [])

    entry = {"id": cid, "title": title, "file": filename}
    if book_disp:
        entry["book"] = book_disp

    for i, t in enumerate(topics):
        if t.get("id") == cid:
            if t == entry:
                return False
            topics[i] = entry
            break
    else:
        topics.append(entry)

    with open(mpath, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    return True


# ----------------------------- 명령: next -----------------------------
def cmd_next(P):
    N.show_next(P["sections"], P["progress"], raw_dir=P["raw"])


# ----------------------------- 명령: submit -----------------------------
def cmd_submit(P, json_path, seq=None, chapter=None, force=False):
    with open(json_path, encoding="utf-8") as f:
        raw = f.read()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"✗ JSON 파싱 실패 — 저장하지 않았습니다: {e}")
        return
    v = M.validate_item(parsed)
    lvl = M.worst_level(v["msgs"])
    print(f"[검증] 종류={v['kind']}, 최고심각도={lvl}")
    print_msgs(v["msgs"])
    if lvl == "err":
        print("\n✗ 오류가 있어 저장/완료 처리하지 않았습니다. 원본 JSON을 고쳐 다시 업로드하세요.")
        return

    rows = N.load_rows(P["progress"])

    # ---- 복습 JSON ----
    if v["kind"] == "review":
        chno = str(chapter) if chapter is not None else _guess_review_chapter(json_path, rows)
        if not chno:
            print("\n△ 이 복습 JSON이 어느 챕터인지 알 수 없습니다. --chapter <번호> 로 다시 지정하세요.")
            return
        os.makedirs(P["raw"], exist_ok=True)
        with open(review_path(P, chno), "w", encoding="utf-8") as f:
            json.dump(parsed, f, ensure_ascii=False, indent=2)
        print(f"\n✓ 챕터 {chno} 최종복습 JSON 저장 완료.")
        _after_change(P, chno)
        return

    # ---- 소단원 JSON ----
    row, cand = match_section_row(parsed, rows, seq=seq, filename=json_path)
    if row is None:
        print("\n△ progress 행 매칭이 모호합니다. 아래 후보 중 하나를 --seq 로 지정해 다시 업로드하세요:")
        for r in (cand or rows):
            print(f"   - seq {r['seq']} | {r['section']} | {'완료' if r['done'] else '미완료'}")
        return

    if row["done"] and not force:
        print(f"\nℹ seq {row['seq']} 는 이미 완료 상태입니다 — 재업로드로 덮어씁니다.")

    earlier = _earlier_incomplete(rows, row)
    if earlier:
        print("\n⚠ 순서 경고: 같은 챕터에서 이보다 앞선 소단원이 아직 미완료입니다.")
        print("   이 소단원의 헤더가 참조해야 했던 '이전 소단원 핵심 결과'가 비어 있거나 불완전했을 수 있습니다:")
        for r in earlier:
            print(f"   - seq {r['seq']} | {r['section']} (미완료)")
        print("   먼저 위 항목들을 완료한 뒤, 이 소단원을 헤더를 다시 받아 재생성하는 것을 권장합니다.")

    stem = re.sub(r'\.txt$', '', row["file"], flags=re.I)
    os.makedirs(P["raw"], exist_ok=True)
    with open(os.path.join(P["raw"], stem + ".json"), "w", encoding="utf-8") as f:
        json.dump(parsed, f, ensure_ascii=False, indent=2)
    if not row["done"]:
        N.mark_done(P["progress"], row["seq"])
    print(f"✓ seq {row['seq']} — {row['section']} 저장 완료.")

    _after_change(P, _chapter_no(row["chapter"]))


def _earlier_incomplete(rows, row):
    """row와 같은 챕터에서, row보다 seq가 앞서면서(작으면서) 아직 미완료인 행들.
    seq는 모두 같은 자리수로 0-패딩되어 있어 문자열 비교만으로 순서가 맞다."""
    out = []
    for r in rows:
        if r["chapter"] != row["chapter"] or r is row:
            continue
        if r["seq"] >= row["seq"]:
            continue
        if any(sk in r["role"] for sk in N.SKIP_ROLES):
            continue
        if not r["done"]:
            out.append(r)
    return out


def _guess_review_chapter(json_path, rows):
    m = re.search(r'(\d+)', os.path.basename(json_path))
    if m:
        return m.group(1)
    return ""


def _after_change(P, chno):
    """저장 후: 챕터 완료 여부 점검 + 다음 작업 안내."""
    if chno:
        res = try_finalize_chapter(P, chno)
        print("\n" + "-" * 56)
        if res["status"] == "merged":
            print(f"🎉 챕터 {chno} 완료 · 병합본 생성!")
            print(f"   · 제목: {res['title']}")
            print(f"   · 파일: {res['file']}")
            print(f"   · 소단원 {res['n_sections']}개, OX {res['n_ox']}개, 개념질문 {res['n_cq']}개")
            print(f"   · manifest {'갱신됨' if res['manifest_changed'] else '변화 없음'}")
            print("   → 오케스트레이터가 data/ 변경을 dev 에 커밋·푸시하고 병합본을 전송합니다.")
            print(f"   [[MERGED chapter={chno} file={res['file']} id={res['id']}]]")
        elif res["status"] == "need_review":
            print(f"📘 챕터 {chno} 소단원 전부 완료! 이제 최종복습만 남았습니다.")
            print(f"   · 최종복습 입력 파일: {res['review_input']}")
            print("   → 이 파일을 외부 AI(system_prompt.md '[모드: 최종복습]')에 넣어 복습 JSON을 만든 뒤")
            print("     `submit --json <복습.json> --chapter " + str(chno) + "` 로 업로드하세요.")
            print(f"   [[NEED_REVIEW chapter={chno} input={res['review_input']}]]")
        else:
            miss = ", ".join(res.get("missing", [])[:6])
            print(f"챕터 {chno} 진행 중 — 남은 소단원: {miss}")
        print("-" * 56)
    print()
    N.show_next(P["sections"], P["progress"], raw_dir=P["raw"])


# ----------------------------- 명령: undo -----------------------------
def cmd_undo(P, seq):
    N.mark_undone(P["progress"], seq, raw_dir=P["raw"], delete_json=True)
    print()
    N.show_next(P["sections"], P["progress"], raw_dir=P["raw"])


# ----------------------------- 명령: status -----------------------------
def cmd_status(P):
    rows = N.load_rows(P["progress"])
    by_ch = {}
    for r in rows:
        by_ch.setdefault(r["chapter"], []).append(r)
    state = load_state(P)
    print(f"책: {state.get('book','(미상)')}" + (f" / 저자: {state.get('author')}" if state.get("author") else ""))
    print("=" * 56)
    for ch, items in by_ch.items():
        chno = _chapter_no(ch)
        content = [r for r in items if not any(sk in r["role"] for sk in N.SKIP_ROLES)]
        done = sum(1 for r in content if r["done"])
        rv = "✓복습" if os.path.exists(review_path(P, chno)) else "·복습대기"
        flag = ""
        if done == len(content) and content:
            flag = "  ← 소단원 완료 " + rv
        print(f"[{done}/{len(content)}] {ch}{flag}")
    print("=" * 56)
    N.show_next(P["sections"], P["progress"], raw_dir=P["raw"])


# ----------------------------- 명령: merge (수동) -----------------------------
def cmd_merge(P, chapter):
    res = try_finalize_chapter(P, str(chapter))
    print(json.dumps(res, ensure_ascii=False, indent=2))


# ----------------------------- main -----------------------------
def main():
    ap = argparse.ArgumentParser(description="프로그램 2 — 작업 추적/수집/병합기")
    ap.add_argument("--work-dir", default=None,
                    help="상태 폴더 강제 지정(기본은 --book 이나 자동감지로 pipeline/work/<책 슬러그> 를 씀)")
    ap.add_argument("--book", default=None,
                    help="책 이름(또는 슬러그). work/ 안에 책 폴더가 여러 개면 반드시 지정해야 함")
    ap.add_argument("--data-dir", default=None, help="병합본 출력 폴더(기본 data/)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("next", help="다음 작업 안내")

    sp = sub.add_parser("submit", help="완성 JSON 업로드(검증·저장·갱신). 재업로드도 이걸로.")
    sp.add_argument("--json", required=True, help="완성된 소단원/복습 JSON 경로")
    sp.add_argument("--seq", default=None, help="매칭 모호 시 progress seq 명시")
    sp.add_argument("--chapter", default=None, help="복습 JSON 의 챕터 번호 명시")
    sp.add_argument("--force", action="store_true", help="완료 항목도 강제 덮어쓰기")

    su = sub.add_parser("undo", help="특정 seq 미완료로 되돌리고 결과 JSON 삭제")
    su.add_argument("--seq", required=True)

    sub.add_parser("status", help="챕터별 진행 현황")

    smg = sub.add_parser("merge", help="특정 챕터 강제 병합 → data/ 반영")
    smg.add_argument("--chapter", required=True)

    args = ap.parse_args()
    P = paths(args.work_dir, args.data_dir, args.book)
    if not os.path.exists(P["progress"]):
        print(f"✗ 상태를 찾을 수 없습니다: {P['progress']}\n  프로그램 1을 먼저 실행했는지, --work-dir/--book 이 맞는지 확인하세요.")
        sys.exit(1)

    if args.cmd == "next":
        cmd_next(P)
    elif args.cmd == "submit":
        cmd_submit(P, args.json, seq=args.seq, chapter=args.chapter, force=args.force)
    elif args.cmd == "undo":
        cmd_undo(P, args.seq)
    elif args.cmd == "status":
        cmd_status(P)
    elif args.cmd == "merge":
        cmd_merge(P, args.chapter)


if __name__ == "__main__":
    main()
