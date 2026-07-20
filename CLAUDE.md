# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

A static, no-build "Problem-Driven Learning" study site for graduate math textbooks (currently Ziegler's
*Lectures on Polytopes*), served from GitHub Pages. It has two independent halves:

- **`index.html`** — the entire viewer app (vanilla HTML/CSS/JS in one file, no bundler, no npm deps). It
  fetches `data/manifest.json` and per-chapter JSON files and renders them as a swipeable card deck.
- **`data/*.json`** — the actual content (definitions, theorems, worked problems, review quizzes). This is
  what the "app" displays; `index.html` never hardcodes math content.
- **`pipeline/`** — offline tooling that turns a textbook PDF into the `data/*.json` files above. Not part
  of the served site.

There is no build step, no package manager, no linter, and no test suite in this repo.

## Running / previewing the site

`index.html` fetches local JSON via `fetch()`, which browsers block under `file://`. Always serve it:

```
python3 -m http.server 8080   # from the repo root, then open http://localhost:8080/index.html
```

To add a chapter's content: drop a JSON file into `data/` matching the schema below, then add an entry to
`data/manifest.json` (`{id, title, file, book}` — the `book` field is required if you want the topic
dropdown to group correctly; see `index.html`'s `populateSelect()`).

## `index.html` architecture

Single IIFE, no framework. The parts worth knowing before touching it:

- **Boot**: `boot()` → fetches `manifest.json` → `populateSelect()` groups the dropdown by each topic's
  `book` field → `loadTopic()` fetches that chapter's JSON into `state.data`.
- **Card-deck engine**: `buildDeck()` flattens one chapter's `learning_flow` into a linear list of cards
  (`cover` → `frame` (big picture) → one `concept` card per `step2_rigorous_logic` block → one card per
  `step3_checkpoint` item, dispatched by that item's `type` — `checkpoint`/`ox`/`conceptual` render as
  `check`/`ox`/`cq` cards respectively). `renderCard()` renders `state.deck[state.cardIndex]`.
- **Legacy final-review deck**: older chapters may still carry a chapter-level `step4_final_review`
  (`ox_quizzes` + `conceptual_questions`) from before review questions were folded into `step3_checkpoint`.
  `hasLegacyReview()` gates a separate "최종 복습" rail item and deck for those chapters only — new chapters
  never have this key, so it doesn't appear for them.
- **Seek bar**: the bottom progress bar (`#deckBar`/`#deckFill`/`#deckThumb`) supports click-to-jump and
  drag-to-scrub via pointer events (`goToIndex()`), not just prev/next.
- **Visualization engine** (`viz_core`, inlined near the top of the `<script>`): renders SVG figures from
  pure geometry data (generator vectors, points, normals) rather than from hand-drawn coordinates — see
  `renderViz()`/`buildVizSVG()`. The supported `visualization.type` values are a fixed whitelist (see
  schema below); this whitelist is duplicated in `pipeline/lib/merger.py`'s `VIZ_WHITELIST` and must stay
  in sync if a new type is ever added to the renderer.
- **Progress tracking**: per-topic completion checkmarks persist in `localStorage` (`pdl_completed_v1`),
  not in the JSON data.

## Content JSON schema

Every `data/<chapter>.json` file has this shape (see `pipeline/prompts/system_prompt.md` for the full,
authoritative schema definition used to generate this content with an LLM):

```
chapter_info: { title, source, overall_goal }
learning_flow: [
  { section_title,
    step1_big_picture: { context, hidden_intuition, visualization? },
    step2_rigorous_logic: [ { type: Definition|Theorem|Lemma|Proposition|Corollary|Remark|Example,
                               name?, formal_statement, idea_behind_proof?, proof_steps?, visualization? } ],
    step3_checkpoint: [
      { type: "checkpoint" (default), problem, hint, solution, visualization? } |
      { type: "ox", question, answer: "O"|"X", explanation } |
      { type: "conceptual", question, hint, answer }
    ] }
]
```

There is no chapter-level `step4_final_review` anymore — each section's own `ox`/`conceptual` review items
live inside that section's `step3_checkpoint`, generated together with it (see `pipeline/prompts/system_prompt.md`
for why: accumulating review questions chapter-wide degraded quality as chapters grew). Merging a chapter is
just concatenating its sections' `learning_flow` entries; there's no separate review-merge step. A handful of
already-completed chapters predate this change and still carry a top-level `step4_final_review` — that's
legacy-only (see `index.html`'s `hasLegacyReview()`), not a currently-supported way to author new content.

All math is KaTeX (`$...$` / `$$...$$`); LaTeX backslashes must be double-escaped in the JSON strings.

## Exam-prep flashcards (`data/exam/`) — temporary, wipe freely

A second, **disposable** kind of content lives alongside the textbook chapters: exam-prep flashcard sets
built from a user's past-paper PDFs. It reuses the exact same content JSON schema above (so `index.html`'s
existing deck engine renders it unchanged — past-paper problems go in each section's `step3_checkpoint` as
`checkpoint` items), but it is **fully isolated** from the textbook content so it can be created and deleted
on a whim without ever touching `data/*.json`:

- **Separate directory + manifest**: exam sets are `data/exam/<file>.json`, listed in their own
  `data/exam/manifest.json` (same `{topics:[{id,title,file,book}]}` shape). The textbook `data/manifest.json`
  and `data/*.json` are never read or written by the exam flow.
- **How they appear in the UI**: `index.html`'s `boot()` now merges *two* manifests — `loadManifests()`
  fetches `data/manifest.json` (required) and `data/exam/manifest.json` (optional; a missing/empty exam
  manifest is silently skipped), tags each topic with the directory it came from (`_dir`), and `loadTopic()`
  fetches from that topic's own `_dir`. Exam topics carry `book: "📝 시험 대비"`, so `populateSelect()`'s
  existing group-by-`book` logic puts them in their own dropdown group. This is the *only* index.html change.
- **Never author or delete these by hand.** Use the dedicated CLI, which validates via `lib/merger.py` (same
  rules as the textbook pipeline) and keeps `data/exam/manifest.json` in sync:
  ```
  python pipeline/exam.py add --json <file> [--title "..."] [--id <base>] [--book "group"]
  python pipeline/exam.py list                       # current exam sets
  python pipeline/exam.py remove --id <id>           # drop one set (json + manifest entry)
  python pipeline/exam.py clear --yes                # wipe ALL exam sets (textbook data untouched)
  ```
  Topic ids are force-prefixed `exam__` (so browser localStorage progress never collides with a textbook
  topic), while the on-disk **filename is ASCII-only** (`<ascii>_<hash8>.json`) to avoid any GitHub-Pages
  non-ASCII-filename risk even when the title is Korean.
- **Authoring**: when the user hands over exam PDFs + their per-exam requirements, generate the JSON directly
  in-session following `pipeline/prompts/exam_prompt.md` (the sister of `system_prompt.md`, tuned for
  problem/solution flashcards and honoring the user's custom requirements), then `exam.py add` it.
- **Lifecycle**: these are committed to `dev` like everything else (so they serve on GitHub Pages for
  phone/tablet study); "wiping" is just `exam.py clear --yes` followed by a commit. Expect the user to ask to
  clear a set after an exam.

## Content production pipeline (`pipeline/`)

Turns a textbook PDF into the JSON above via two CLI programs (see `pipeline/README.md` for full detail —
this is a summary). No API calls are made anywhere in this pipeline; JSON generation happens by pasting
headers/PDFs into an external LLM chat by hand.

```
python pipeline/program1_split.py --pdf <book.pdf> --toc <toc_data.json> --offset <int>
python pipeline/program2_track.py [--book <name>] next                        # next section to make + its header
python pipeline/program2_track.py [--book <name>] submit --json <done.json>   # validate, save, advance tracker
python pipeline/program2_track.py [--book <name>] status                      # per-chapter progress
```

Key architectural points (span multiple files, easy to miss):

- **State is per-book**: `pipeline/work/<book-slug>/` (slug derived from `toc_data.json`'s `book` field via
  `lib/merger.slug_id`), so multiple textbooks can be in progress without collision. `program2_track.py`
  auto-picks the only book folder if there's just one, otherwise requires `--book`.
- **Header enrichment depends on submission order**: `lib/next_section.py`'s `enrich_prev_summary()` builds
  each section's "이전 소단원 핵심 결과" header block by reading the *already-saved* `chapter_raw/*.json` of
  up to 3 preceding sections in the same chapter. Submitting sections out of order silently produces
  incomplete headers for later ones. `program2_track.py submit` warns (but doesn't block) when an earlier
  same-chapter section is still incomplete.
- **`data/<slug>.json` is republished on every `submit`/`undo`, not just on completion**: `publish_chapter()` in
  `program2_track.py` merges whatever sections currently exist in `chapter_raw/` for that chapter — even just
  the first one — and overwrites the same `data/<slug>.json` + `data/manifest.json` each time (this is the only
  code path that writes to `data/`). `sections_complete()` only decides whether the result counts as
  `status: "merged"` (chapter fully done) vs `"partial"` (still missing sections) for the CLI's `[[PUBLISHED ...]]`
  marker — there's no separate final-review submission to wait for and no separate "completion" file.
- **Validation** (`lib/merger.py`) is a Python port of the original browser tool `pipeline/section_merger.html`
  (kept in the repo for manual/offline use) — both must stay consistent, e.g. the `VIZ_WHITELIST` and the
  requirement that `manifest` entries carry a `book` field.
- **`.gitignore` under `pipeline/`** intentionally excludes split PDFs and delivery zips
  (`work/*/sections_out/*.pdf`, `work/*/_delivery/`) — only text state (`.txt` headers, `progress.md`,
  `chapter_raw/*.json`, `state.json`) is committed, since the sandbox this pipeline runs in is ephemeral
  and PDFs are large/regenerable.

## Automated section generation (`pipeline/orchestrate_claude.py`)

The pipeline's `program1`/`program2` scripts and the manual "paste PDF + system_prompt.md into an
external AI" step (see above) can be replaced end-to-end by this repo's own `claude` CLI, running under
the user's existing Claude Code subscription — **no separate API key, no per-token billing**. This is the
default way to author new sections when the user asks to "automate" or "continue" content generation; you
do not need to ask them to paste anything into an external AI first.

```
python pipeline/orchestrate_claude.py --book <anything identifying a book> [--chapter N] [--max N]
```

**Books in progress right now** (each is a folder under `pipeline/work/`; run
`python pipeline/program2_track.py --book <slug> status` for that book's current per-chapter progress —
don't rely on any progress numbers written here, they go stale):

| slug | title (`toc_data.json`'s `book` field) | author |
|---|---|---|
| `algebraic_topology` | Algebraic Topology | Allen Hatcher |
| `complex_analysis` | Complex Analysis | Elias M. Stein & Rami Shakarchi |
| `lectures_on_polytopes` | Lectures on Polytopes | Günter M. Ziegler |

`--book` does not need to be that exact slug: `orchestrate_claude.py`'s `resolve_book()` also accepts the
exact title above, a case-insensitive substring of the slug/title (e.g. `ziegler`, `stein`, `polytopes`), or
a Korean alias from its `BOOK_ALIASES` table (e.g. `복소해석`, `폴리토프`, `해처`) — so however the user names
a book in conversation, try passing that string through as-is before normalizing it yourself. An ambiguous
or unmatched `--book` value exits with the full list of available books/slugs rather than guessing, so a
failed run here is informative, not a dead end. When a genuinely new book is started (new
`pipeline/work/<slug>/`), add a `BOOK_ALIASES` entry only if its natural Korean/English name doesn't already
substring-match its slug or title.

For each remaining section it: reads `program2_track.py next`, builds the prompt (header + PDF path +
`system_prompt.md`'s rules), calls `claude -p --resume <session> --model opus` to generate the JSON,
validates it (`lib/merger.py`), saves it to `chapter_raw/`, runs `program2_track.py submit`, then commits
and pushes to `dev` (per the Git workflow below — no confirmation needed once the user has approved this
generation approach for the session). Run this in the background (it can take many minutes per piece);
don't poll it — you'll be notified when the process exits or when a piece fails.

Key mechanics to know before touching or rerunning this:
- **Dedicated per-(book, chapter) session**: `--resume`s a Claude session ID persisted at
  `pipeline/work/<book>/.claude_gen_session_id.ch<N>` (one file per chapter number, auto-created the first
  time that chapter is generated; gitignored — it's a local `~/.claude` conversation handle, not portable
  content). The orchestrator switches to (or creates) the current chapter's dedicated session automatically
  whenever `next`'s chapter number changes, so a chapter's pieces share one accumulating context (consistent
  style, awareness of already-used definitions within that chapter) without that context growing unboundedly
  across an entire book (e.g. chapter 4 of Hatcher has 52 sections), and without polluting or being polluted
  by whatever chat session invoked the orchestrator or by other chapters/books.
  If these session-id files are ever missing on a fresh machine/clone, the orchestrator just creates fresh
  ones — content generation still works, it only loses that chapter's accumulated generation-session context.
- **Stops, doesn't corrupt, on failure**: a validation error, a `submit` error, or the subscription's own
  session-limit message halts the loop before writing anything broken, and exits with a distinct code
  (`EXIT_QUOTA`=2 for the session-limit case, `EXIT_ERROR`=1 for a real error, `EXIT_OK`=0 otherwise) plus a
  `QUOTA_RESET_AT=<ISO8601>` stdout line on the quota case — see `auto_cycle.py` below, which relies on this
  distinction to decide whether to auto-retry or stop and flag for a human.
- **`--chapter N`**: stop once the next piece would leave chapter `N` — use this to pause at a natural
  checkpoint (e.g. finish chapter 1, then re-evaluate) rather than running unattended through an entire book.
- Encoding: this repo's Python pipeline scripts print Korean text with em-dashes etc. to stdout; on Windows
  the orchestrator forces UTF-8 on its own stdout/stderr to avoid `cp949` `UnicodeEncodeError` crashes —
  keep that `reconfigure()` call if you edit the script. Native Windows console tools invoked as subprocesses
  (`schtasks`, not this repo's own Python scripts) print in the system's ANSI codepage instead (`cp949` on a
  ko-KR machine) — capture their output with `encoding="mbcs", errors="replace"`, not `"utf-8"`, or the
  subprocess capture itself throws `UnicodeDecodeError` (see `auto_cycle.py`'s `schedule_next_run()`).
- Quality note: before scaling this up on a new book/chapter, generate one piece and sanity-check it
  against the schema/style of an already-completed neighboring piece (e.g. diff the `step2_rigorous_logic`
  block count, check for content overlap with the immediately preceding piece) rather than assuming the
  first output is representative.

### Unattended operation across quota resets (`pipeline/auto_cycle.py`)

The Claude subscription's own usage quota resets on a rolling ~5h window, not a fixed schedule — the
`claude` CLI reports the *exact* reset time in its own limit message when hit. Don't schedule blind
fixed-interval reruns (e.g. "every 5 hours") to work around this; `auto_cycle.py` instead parses that exact
time and self-reschedules a one-shot Windows Scheduled Task for a few minutes after it, so the chain always
wakes close to the real reset regardless of when the quota window actually started:

```
python pipeline/auto_cycle.py [--book <name>]   # default: cycle through every book under pipeline/work/
```

- Runs `orchestrate_claude.py` per book, in order, until a book is fully generated (moves to the next one),
  a quota hit occurs (registers the Scheduled Task `MathQuizletAutoCycle` for `reset_time + 4min`, running
  `pipeline/run_auto_cycle.bat`, then exits — the quota is subscription-wide, so it doesn't bother trying
  the remaining books this round), or a genuine error occurs (writes `pipeline/work/_NEEDS_ATTENTION.txt`,
  best-effort desktop popup, and **does not reschedule** — auto-retrying a real error would just burn the
  next quota window repeating the same failure).
- Bootstrapping or resuming the chain (first time, or after fixing a `_NEEDS_ATTENTION.txt` issue) is just
  running the command above once by hand; from then on it keeps itself going with no further action needed.
- Stopping the chain permanently: `schtasks /Delete /TN MathQuizletAutoCycle /F` (from PowerShell — Git
  Bash's MSYS layer mangles `/`-prefixed args into path conversions; run schtasks itself from PowerShell, or
  from Python via `subprocess.run([...])` with an argv list, which bypasses that shell entirely).
- Progress/decisions land in `pipeline/work/_auto_cycle.log`; check there (or `_NEEDS_ATTENTION.txt`) for
  what happened between chat sessions rather than assuming silence means it's still running fine.

## Git workflow

Active development happens on the `dev` branch (branched from `main`); commit and push directly to `dev`
without waiting for confirmation, and don't open a pull request unless explicitly asked.
