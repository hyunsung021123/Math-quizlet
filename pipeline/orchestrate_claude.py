#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Dedicated-session orchestrator for the PDL content pipeline.

Generates each remaining section's JSON by calling the `claude` CLI itself
(`claude -p --resume <session-id> --model opus`), using the SAME Claude Code
subscription this repo's sessions already run under -- no separate API key,
no per-token billing. Pieces are generated inside a session dedicated to their
(book, chapter) pair (see .claude_gen_session_id.ch<N> below) so the
generation model accumulates context across a chapter's pieces (style,
already-used definitions, etc.) without that context growing unboundedly
across an entire book, and without polluting whatever chat session invoked
this script. Crossing a chapter boundary transparently switches to (or
creates) that chapter's own dedicated session.

Usage:
    python orchestrate_claude.py --book <anything identifying a book> [--chapter N] [--max N]

- --book accepts the exact folder slug (e.g. `algebraic_topology`), the exact
  English title from that book's toc_data.json (e.g. `Complex Analysis`), a
  case-insensitive substring of either, or a known Korean alias (see
  BOOK_ALIASES below) -- resolved against every folder under `pipeline/work/`.
  Ambiguous or unmatched input lists the available books and their slugs so
  the caller (human or Claude) can retry with the exact slug.
- --chapter N: stop once the next piece belongs to a chapter other than N
  (omit to run until the book is exhausted or an error occurs).
- --max N: stop after generating at most N pieces this run (safety valve).

Each piece: program2_track.py next -> build prompt -> claude -p (resume
dedicated session) -> extract/validate JSON -> save to chapter_raw/ ->
program2_track.py submit -> git add/commit/push (dev branch), matching the
"commit and push directly to dev" policy in CLAUDE.md.

Stops immediately (without corrupting state) on: validation error, submit
error, or unparseable `claude` output (e.g. hit the subscription's session
quota -- message reads "You've hit your session limit ... resets <time>").
Rerun the same command afterwards; already-submitted pieces are skipped
automatically by `program2_track.py next` continuing from where it left off.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import uuid

PIPE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(PIPE)
sys.path.insert(0, PIPE)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def log(msg):
    print(msg, flush=True)


# Slug -> extra Korean/English aliases a user might say instead of the exact
# slug or the exact toc_data.json "book" title. Add an entry here whenever a
# new book is started if its natural-language name doesn't already contain
# an obvious substring of the slug/title (resolve_book() below already does
# case-insensitive substring matching on both, so most short English/author
# references need no entry at all).
BOOK_ALIASES = {
    "algebraic_topology": ["대수적 위상수학", "대수위상", "해처", "hatcher"],
    "complex_analysis": ["복소해석", "복소해석학", "스타인", "stein", "shakarchi"],
    "lectures_on_polytopes": ["폴리토프", "폴리토프 이론", "지글러", "ziegler"],
}


def resolve_book(query):
    """Resolve a user-provided book reference to its pipeline/work/ slug.

    Tries, in order: exact slug match, exact toc_data.json title match,
    case-insensitive substring match against slug/title/aliases. Raises
    SystemExit with the list of available books on no-match or ambiguity.
    """
    work_dir = os.path.join(PIPE, "work")
    candidates = []  # (slug, title)
    for slug in sorted(os.listdir(work_dir)):
        toc_path = os.path.join(work_dir, slug, "toc_data.json")
        if not os.path.isdir(os.path.join(work_dir, slug)) or not os.path.exists(toc_path):
            continue
        title = json.load(open(toc_path, encoding="utf-8")).get("book", "")
        candidates.append((slug, title))

    q = query.strip().lower()
    for slug, title in candidates:
        if q == slug.lower() or q == title.lower():
            return slug

    matches = []
    for slug, title in candidates:
        haystacks = [slug.lower(), title.lower()] + [a.lower() for a in BOOK_ALIASES.get(slug, [])]
        if any(q in h or h in q for h in haystacks):
            matches.append(slug)

    if len(matches) == 1:
        return matches[0]

    listing = "\n".join(f"  - {slug}  ({title})" for slug, title in candidates)
    if len(matches) > 1:
        sys.exit(f"'{query}' matches more than one book ({matches}). "
                  f"Rerun with the exact slug:\n{listing}")
    sys.exit(f"'{query}' did not match any book in pipeline/work/. Available books:\n{listing}")


def run(cmd, **kw):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           env=env, **kw)


def track(book, *args):
    return run([sys.executable, "program2_track.py", "--book", book, *args], cwd=PIPE)


def git(*args):
    return run(["git", *args], cwd=REPO)


def get_or_create_session_id(book, chapter):
    path = os.path.join(PIPE, "work", book, f".claude_gen_session_id.ch{chapter}")
    if os.path.exists(path):
        return open(path, encoding="utf-8").read().strip(), path
    sid = str(uuid.uuid4())
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w", encoding="utf-8").write(sid)
    return sid, path


def parse_next(txt):
    """Parse program2_track.py's `next`/`submit` trailing "다음 조각" block."""
    if "다음 조각" not in txt:
        return None
    seq = re.search(r"다음 조각:\s*seq\s*(\d+)", txt)
    chap = re.search(r"챕터:\s*(\d+)\.", txt)
    pdf = re.search(r"①[^\n]*\n\s*(.+\.pdf)", txt)
    save = re.search(r"③[^\n]*\n\s*(.+\.json)", txt)
    hdr = re.search(r"(\[문서 맥락\].*?)(?=\n③)", txt, re.S)
    if not (seq and chap and pdf and save and hdr):
        return None
    return dict(seq=seq.group(1), chapter=int(chap.group(1)),
                header=hdr.group(1).strip(),
                pdf=pdf.group(1).strip(), save=save.group(1).strip())


def build_prompt(info):
    pdf_path = info["pdf"].replace("\\", "/")
    return f"""{info['header']}

[본문]
아래 경로의 PDF가 이 소단원("현재 소단원")의 본문이다. Read 도구로 읽어라:
{pdf_path}

규약은 이 대화 처음에 준 시스템 프롬프트(pipeline/prompts/system_prompt.md의 시작~끝)를 그대로 따른다. 이 소단원 하나에 대한 유효한 JSON 객체 **하나만** 출력하라 — 코드펜스·설명·인사 없이 첫 글자 {{ 마지막 글자 }}. 본문 끝부분이 다음 소단원과 겹치면 무시하고 현재 소단원 범위 안에서 마무리하라. 이미 만든 이전 소단원과 중복되는 정의/정리는 다시 만들지 말고 "이전 소단원 핵심 결과"를 참조해 이어가라. chapter_info는 같은 챕터의 기존 data/*.json에 이미 쓰인 것과 동일하게 유지하라."""


def gen(prompt, session_id):
    sysprompt_path = os.path.join(PIPE, "prompts", "system_prompt.md")
    raw = open(sysprompt_path, encoding="utf-8").read()
    m = re.search(r"---8<--- 시스템 프롬프트 시작 ---8<---(.*?)---8<--- 시스템 프롬프트 끝 ---8<---", raw, re.S)
    sysprompt = m.group(1).strip()
    sysfile = os.path.join(PIPE, "work", "_tmp_sysprompt.txt")
    open(sysfile, "w", encoding="utf-8").write(sysprompt)
    cmd = ["claude", "-p", prompt, "--resume", session_id, "--model", "opus",
           "--output-format", "text", "--allowedTools", "Read",
           "--append-system-prompt-file", sysfile]
    r = run(cmd, cwd=REPO, stdin=subprocess.DEVNULL)
    return r.stdout or ""


def extract_validate(raw):
    from lib import merger
    s, e = raw.find("{"), raw.rfind("}")
    if s < 0 or e < 0:
        return None, f"no JSON braces in output (raw: {raw[:200]!r})"
    try:
        data = json.loads(raw[s:e + 1])
    except Exception as ex:
        return None, f"JSON parse error: {ex}"
    iss = merger.validate_item(data)
    order = {"ok": 0, "warn": 1, "err": 2}
    worst = max((m["level"] for m in iss["msgs"]), key=lambda l: order[l]) if iss["msgs"] else "ok"
    msg = "; ".join(f"[{m['level']}] {m['text']}" for m in iss["msgs"])
    if worst == "err":
        return None, "validation error: " + msg
    return data, msg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--book", required=True,
                     help="slug, exact title, substring, or known alias -- see resolve_book()")
    ap.add_argument("--chapter", type=int, default=None,
                     help="stop once the next piece leaves this chapter number")
    ap.add_argument("--max", type=int, default=None,
                     help="max number of pieces to generate this run")
    args = ap.parse_args()
    args.book = resolve_book(args.book)

    log(f"===== orchestrator start (book={args.book}) =====")
    done = 0
    current_chapter = None
    sid = None
    while args.max is None or done < args.max:
        nx = track(args.book, "next")
        info = parse_next(nx.stdout)
        if not info:
            log("no further pieces parsed from `next` output. stopping.")
            log(nx.stdout[-500:])
            break
        if args.chapter is not None and info["chapter"] != args.chapter:
            log(f"reached chapter {info['chapter']} (seq {info['seq']}) "
                f"-- chapter {args.chapter} boundary. stopping.")
            break
        if info["chapter"] != current_chapter:
            current_chapter = info["chapter"]
            sid, sid_path = get_or_create_session_id(args.book, current_chapter)
            log(f"-- switched to chapter {current_chapter} dedicated session "
                f"{sid} ({sid_path}) --")
        seq = info["seq"]
        log(f"\n--- seq {seq}: generating ---")
        raw = gen(build_prompt(info), sid)
        raw_dump = os.path.join(PIPE, "work", args.book, f"_gen_raw_{seq}.txt")
        open(raw_dump, "w", encoding="utf-8").write(raw)
        data, msg = extract_validate(raw)
        if data is None:
            log(f"seq {seq} FAILED: {msg}")
            log("Repo state unchanged; rerun this script later to retry "
                "(e.g. after a subscription session-limit reset).")
            break
        json.dump(data, open(info["save"], "w", encoding="utf-8"),
                   ensure_ascii=False, indent=2)
        log(f"seq {seq} validated ({msg}), saved.")
        sub = track(args.book, "submit", "--json", info["save"])
        if "✗" in sub.stdout or "저장하지 않" in sub.stdout:
            log(f"seq {seq} submit reported errors:\n{sub.stdout[-800:]}")
            break
        sect_m = re.search(r"현재 소단원:\s*(.+)", info["header"])
        sect = sect_m.group(1).strip() if sect_m else seq
        git("add", "-A")
        commit_msg = (f"{args.book}: {sect} 추가\n\n"
                      "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>")
        git("commit", "-m", commit_msg)
        git("push", "origin", "dev")
        log(f"seq {seq} submitted + committed + pushed. ({sect})")
        done += 1
    log(f"===== orchestrator done: {done} piece(s) this run =====")


if __name__ == "__main__":
    main()
