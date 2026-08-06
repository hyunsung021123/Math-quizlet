#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Self-rescheduling driver over all books, built on top of orchestrate_claude.py.

Problem this solves: the underlying Claude subscription has a rolling session
quota (~5h windows); once it's hit, `orchestrate_claude.py` can't do anything
until the quota resets, and someone has to notice and rerun it by hand. This
script removes that "someone has to notice" step:

  1. Runs orchestrate_claude.py for each book in BOOKS, in order, until either
     a book is fully generated (its `next` has nothing left) or one of them
     hits the subscription's session limit or a genuine error.
  2. On a quota hit: parses the exact reset time `claude` itself reported
     (via orchestrate_claude.py's QUOTA_RESET_AT= marker), and registers a
     ONE-SHOT Windows Scheduled Task to rerun *this exact script* a few
     minutes after that time -- not a blind fixed interval, since the actual
     reset time depends on when that quota window started, not a fixed
     wall-clock cadence. That task, when it fires, repeats this same process
     -- so once started, the chain keeps itself going indefinitely with zero
     manual intervention, always waking up close to the real reset time.
  3. On a genuine error (validation/submit failure -- NOT a quota message):
     stops the chain entirely and writes work/_NEEDS_ATTENTION.txt. It does
     NOT reschedule itself in this case, since retrying a real error
     automatically would just burn quota repeating the same failure forever.
  4. If every book is fully generated with no quota hit: logs completion and
     does not reschedule (nothing left to generate).

Usage:
    python auto_cycle.py                 # process every book under pipeline/work/
    python auto_cycle.py --book <name>   # process only one book (still self-reschedules)
    python auto_cycle.py --books a,b     # restrict the rotation to these books
    python auto_cycle.py --books a,b --by-chapter   # ...one chapter at a time, alternating

`--books` is how you suspend a book without deleting its state: books left out
of the list are simply never visited (their pipeline/work/<slug>/ stays put and
they rejoin the moment you name them again). `--book` remains the single-book
shorthand.

By default each book is run to exhaustion before the next one starts. With
`--by-chapter`, each turn advances exactly ONE chapter of one book and then
hands over to the next book in the rotation, so several books progress side by
side instead of finishing one at a time. A book drops out of the rotation once
its tracker reports nothing pending; the run ends when the rotation empties.

Both flags survive a quota reschedule -- write_wrapper_bat() bakes the current
run's arguments into run_auto_cycle.bat. (It used to write a bare
`auto_cycle.py` line, which would have silently resurrected the suspended books
on the first scheduled wake-up.)

To bootstrap the chain for the first time (or restart it after fixing a
_NEEDS_ATTENTION.txt issue), just run this script once by hand; from then on
it re-invokes itself via Windows Task Scheduler with no further action needed.
To stop the chain permanently: `schtasks /Delete /TN MathQuizletAutoCycle /F`.

Pausing to reclaim the subscription for interactive use (e.g. you want to use
Claude Code yourself right now, and don't want a scheduled run competing for
the same session/usage quota): create `work/_PAUSE` (any content, even an
empty file). orchestrate_claude.py checks it once per piece and backs off
between pieces (never mid-generation, so nothing is ever killed uncommitted);
if a scheduled run fires while it exists, this script itself checks it before
touching anything and just logs + exits, not rescheduling. Neither path
deletes the Windows Scheduled Task entry, if one is currently pending -- it
will fire, see the pause file, and no-op. Delete work/_PAUSE and rerun this
script by hand whenever you want the chain going again -- exactly like
resuming after a _NEEDS_ATTENTION.txt fix.
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys

PIPE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(PIPE)
LOG_PATH = os.path.join(PIPE, "work", "_auto_cycle.log")
ALERT_PATH = os.path.join(PIPE, "work", "_NEEDS_ATTENTION.txt")
TASK_NAME = "MathQuizletAutoCycle"
BAT_PATH = os.path.join(PIPE, "run_auto_cycle.bat")
RESCHEDULE_BUFFER_MIN = 4  # fire a few minutes after the reported reset, not exactly at it

sys.path.insert(0, PIPE)
import orchestrate_claude as oc  # reuses resolve_book/EXIT_* -- single source of truth

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def log(msg):
    line = f"[{datetime.datetime.now().isoformat(timespec='seconds')}] {msg}"
    print(line, flush=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def discover_books():
    work_dir = os.path.join(PIPE, "work")
    books = []
    for slug in sorted(os.listdir(work_dir)):
        if os.path.exists(os.path.join(work_dir, slug, "toc_data.json")):
            books.append(slug)
    return books


def write_wrapper_bat(extra_args=()):
    """Bake THIS run's arguments into the wrapper so the rescheduled wake-up
    repeats the same run. Without this, a `--books`/`--by-chapter` restriction
    would be lost at the first quota hit and the chain would quietly resume
    generating the books the user had suspended."""
    py = sys.executable
    args = " ".join(f'"{a}"' for a in extra_args)
    content = (
        "@echo off\r\n"
        f'cd /d "{PIPE}"\r\n'
        f'"{py}" "{os.path.join(PIPE, "auto_cycle.py")}"'
        + (f" {args}" if args else "")
        + f' >> "{LOG_PATH}" 2>&1\r\n'
    )
    with open(BAT_PATH, "w", encoding="utf-8") as f:
        f.write(content)
    return BAT_PATH


def schedule_next_run(target_dt, extra_args=()):
    target_dt = target_dt + datetime.timedelta(minutes=RESCHEDULE_BUFFER_MIN)
    bat = write_wrapper_bat(extra_args)
    # schtasks' expected /SD date format follows the machine's locale (this
    # one is ko-KR, which wants yyyy/MM/dd rather than the US-locale MM/DD/YYYY).
    date_str = target_dt.strftime("%Y/%m/%d")
    time_str = target_dt.strftime("%H:%M")
    r = subprocess.run(
        ["schtasks", "/Create", "/TN", TASK_NAME, "/TR", bat,
         "/SC", "ONCE", "/SD", date_str, "/ST", time_str, "/F"],
        capture_output=True, text=True, encoding="mbcs", errors="replace",
    )
    if r.returncode != 0:
        log(f"WARNING: failed to schedule next run via schtasks (rc={r.returncode}): "
            f"{r.stdout}\n{r.stderr}")
        log(f"Manual fallback: rerun `python {os.path.join(PIPE, 'auto_cycle.py')}` "
            f"any time after {target_dt.isoformat()}.")
    else:
        log(f"Scheduled next run for {target_dt.isoformat()} "
            f"(task '{TASK_NAME}', {RESCHEDULE_BUFFER_MIN} min after reported reset).")


def notify_user_popup(title, message):
    """Best-effort desktop popup so a real error doesn't sit unnoticed in a log
    file. Never allowed to raise -- this is a nicety, not the alert mechanism
    (the _NEEDS_ATTENTION.txt file and log entry are the real record)."""
    try:
        ps_cmd = (
            "Add-Type -AssemblyName System.Windows.Forms; "
            f"[System.Windows.Forms.MessageBox]::Show('{message}', '{title}') | Out-Null"
        )
        subprocess.Popen(
            ["powershell", "-WindowStyle", "Hidden", "-Command", ps_cmd],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


def process_book(book, chapter=None):
    """Run orchestrate_claude.py for one book. Returns (exit_code, stdout).
    With `chapter`, orchestrate stops as soon as the next piece would leave
    that chapter -- that's what makes --by-chapter hand over to the next book
    at a chapter boundary instead of running this book to exhaustion."""
    cmd = [sys.executable, os.path.join(PIPE, "orchestrate_claude.py"), "--book", book]
    if chapter is not None:
        cmd += ["--chapter", str(chapter)]
    r = subprocess.run(cmd, cwd=PIPE, capture_output=True, text=True, encoding="utf-8")
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def next_chapter_of(book):
    """Chapter number of the book's next pending piece, or None if the tracker
    reports nothing left (or its output can't be parsed -- treated the same,
    since either way there is no chapter for us to advance)."""
    r = oc.track(book, "next")
    info = oc.parse_next(r.stdout)
    return info["chapter"] if info else None


def handle_terminal(rc, out, tail, book, self_args):
    """Shared handling of orchestrate's non-OK exits. Returns True if the whole
    run should stop now (pause / quota-and-rescheduled / needs-a-human), False
    if rc was EXIT_OK and the caller should carry on. Both the sequential and
    the --by-chapter loop go through here so the two can't drift apart."""
    if rc == oc.EXIT_PAUSED:
        log(f"book '{book}' paused mid-run (work/_PAUSE present). Not rescheduling. "
            "Delete the pause file and rerun `auto_cycle.py` by hand to resume the chain.")
        return True

    if rc == oc.EXIT_QUOTA:
        m = re.search(r"QUOTA_RESET_AT=(\S+)", out)
        if m:
            reset_at = datetime.datetime.fromisoformat(m.group(1))
            schedule_next_run(reset_at, self_args)
        else:
            log("QUOTA hit but no QUOTA_RESET_AT marker found -- falling back to "
                 "retry in 5 hours from now.")
            schedule_next_run(datetime.datetime.now() + datetime.timedelta(hours=5), self_args)
        return True

    if rc == oc.EXIT_ERROR:
        msg = (f"auto_cycle stopped: book '{book}' hit a non-quota error and needs "
               f"a human to look at it before the chain continues.\n\n"
               f"See log: {LOG_PATH}\n\nTail of the failing run:\n{tail}")
        with open(ALERT_PATH, "w", encoding="utf-8") as f:
            f.write(f"[{datetime.datetime.now().isoformat()}]\n{msg}\n")
        log(msg)
        log("NOT rescheduling (would just repeat the same failure). "
            f"After fixing it, rerun `python {os.path.join(PIPE, 'auto_cycle.py')}"
            + (" " + " ".join(self_args) if self_args else "")
            + "` by hand to resume the chain.")
        notify_user_popup("Math-quizlet auto-generation needs attention",
                           f"Book '{book}' failed (non-quota). See {ALERT_PATH}")
        return True

    return False


def main_by_chapter(books, self_args):
    """Round-robin: one chapter of one book per turn, then hand over.

    A book leaves the rotation as soon as its tracker has nothing pending. The
    `progressed` guard matters because orchestrate returns EXIT_OK both for
    "finished that chapter" and for "did nothing at all"; without it, a book
    that reports a chapter but can't actually advance (e.g. a missing
    sections_out piece that `next` still lists) would spin the rotation
    forever, burning nothing but never terminating.
    """
    rotation = list(books)
    log(f"===== auto_cycle start (by-chapter rotation), books={rotation} =====")
    while rotation:
        progressed = False
        for book in list(rotation):
            chapter = next_chapter_of(book)
            if chapter is None:
                log(f"book '{book}' has nothing pending -- leaving the rotation.")
                rotation.remove(book)
                continue
            log(f"-- {book}: advancing chapter {chapter} --")
            rc, out = process_book(book, chapter=chapter)
            tail = out[-1500:]
            log(f"orchestrate_claude.py[{book} ch{chapter}] exit={rc}\n{tail}")
            if handle_terminal(rc, out, tail, book, self_args):
                return
            done_here = next_chapter_of(book)
            if done_here != chapter:
                progressed = True
                log(f"book '{book}': chapter {chapter} done, next up is "
                    f"{'chapter ' + str(done_here) if done_here else 'nothing (book complete)'}. "
                    "handing over to the next book.")
            else:
                log(f"⚠ book '{book}': still on chapter {chapter} after a full run -- "
                    "no progress made this turn.")
        if not progressed and rotation:
            log(f"⚠ a full rotation over {rotation} advanced nothing. Stopping rather "
                "than looping forever -- check the tracker for these books.")
            return
    log("===== rotation empty -- every requested book is fully generated. "
        "not rescheduling. =====")
    try:
        subprocess.run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"],
                        capture_output=True, text=True, encoding="mbcs", errors="replace")
    except Exception:
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--book", default=None,
                     help="process only this book (still self-reschedules); default: all books")
    ap.add_argument("--books", default=None,
                     help="comma-separated books to rotate over; books left out are "
                          "suspended (state kept, just never visited)")
    ap.add_argument("--by-chapter", action="store_true",
                     help="advance one chapter per book per turn, alternating, instead "
                          "of running each book to exhaustion before the next")
    args = ap.parse_args()

    # Rebuilt verbatim into run_auto_cycle.bat so a quota reschedule repeats
    # THIS run, restrictions included.
    self_args = []
    if args.book:
        self_args += ["--book", args.book]
    if args.books:
        self_args += ["--books", args.books]
    if args.by_chapter:
        self_args += ["--by-chapter"]

    if os.path.exists(oc.PAUSE_FILE):
        log(f"pause file found ({oc.PAUSE_FILE}) at chain start -- doing nothing "
            "this run and not rescheduling. Delete it and rerun `auto_cycle.py` "
            "by hand to resume the chain.")
        return

    if args.book and args.books:
        sys.exit("--book and --books are mutually exclusive; use one of them.")
    if args.books:
        books = [oc.resolve_book(b) for b in args.books.split(",") if b.strip()]
    elif args.book:
        books = [oc.resolve_book(args.book)]
    else:
        books = discover_books()
    if args.by_chapter:
        return main_by_chapter(books, self_args)
    log(f"===== auto_cycle start, books={books} =====")

    for book in books:
        log(f"-- processing book: {book} --")
        rc, out = process_book(book)
        tail = out[-1500:]
        log(f"orchestrate_claude.py[{book}] exit={rc}\n{tail}")

        if handle_terminal(rc, out, tail, book, self_args):
            return

        # EXIT_OK: this book is fully generated (or had nothing to do) -- move on.
        log(f"book '{book}' fully processed with no quota hit this run; continuing.")

    log("===== all books processed with no quota hit -- nothing left to generate. "
        "not rescheduling. =====")
    try:
        subprocess.run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"],
                        capture_output=True, text=True, encoding="mbcs", errors="replace")
    except Exception:
        pass


if __name__ == "__main__":
    main()
