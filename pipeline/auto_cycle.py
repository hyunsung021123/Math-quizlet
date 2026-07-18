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

To bootstrap the chain for the first time (or restart it after fixing a
_NEEDS_ATTENTION.txt issue), just run this script once by hand; from then on
it re-invokes itself via Windows Task Scheduler with no further action needed.
To stop the chain permanently: `schtasks /Delete /TN MathQuizletAutoCycle /F`.
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


def write_wrapper_bat():
    py = sys.executable
    content = (
        "@echo off\r\n"
        f'cd /d "{PIPE}"\r\n'
        f'"{py}" "{os.path.join(PIPE, "auto_cycle.py")}" >> "{LOG_PATH}" 2>&1\r\n'
    )
    with open(BAT_PATH, "w", encoding="utf-8") as f:
        f.write(content)
    return BAT_PATH


def schedule_next_run(target_dt):
    target_dt = target_dt + datetime.timedelta(minutes=RESCHEDULE_BUFFER_MIN)
    bat = write_wrapper_bat()
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


def process_book(book):
    """Run orchestrate_claude.py for one book. Returns (exit_code, stdout)."""
    cmd = [sys.executable, os.path.join(PIPE, "orchestrate_claude.py"), "--book", book]
    r = subprocess.run(cmd, cwd=PIPE, capture_output=True, text=True, encoding="utf-8")
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--book", default=None,
                     help="process only this book (still self-reschedules); default: all books")
    args = ap.parse_args()

    books = [oc.resolve_book(args.book)] if args.book else discover_books()
    log(f"===== auto_cycle start, books={books} =====")

    for book in books:
        log(f"-- processing book: {book} --")
        rc, out = process_book(book)
        tail = out[-1500:]
        log(f"orchestrate_claude.py[{book}] exit={rc}\n{tail}")

        if rc == oc.EXIT_QUOTA:
            m = re.search(r"QUOTA_RESET_AT=(\S+)", out)
            if m:
                reset_at = datetime.datetime.fromisoformat(m.group(1))
                schedule_next_run(reset_at)
            else:
                log("QUOTA hit but no QUOTA_RESET_AT marker found -- falling back to "
                     "retry in 5 hours from now.")
                schedule_next_run(datetime.datetime.now() + datetime.timedelta(hours=5))
            return

        if rc == oc.EXIT_ERROR:
            msg = (f"auto_cycle stopped: book '{book}' hit a non-quota error and needs "
                   f"a human to look at it before the chain continues.\n\n"
                   f"See log: {LOG_PATH}\n\nTail of the failing run:\n{tail}")
            with open(ALERT_PATH, "w", encoding="utf-8") as f:
                f.write(f"[{datetime.datetime.now().isoformat()}]\n{msg}\n")
            log(msg)
            log("NOT rescheduling (would just repeat the same failure). "
                f"After fixing it, rerun `python {os.path.join(PIPE, 'auto_cycle.py')}` "
                "by hand to resume the chain.")
            notify_user_popup("Math-quizlet auto-generation needs attention",
                               f"Book '{book}' failed (non-quota). See {ALERT_PATH}")
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
