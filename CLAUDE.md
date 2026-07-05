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
  (`cover` → `frame` (big picture) → one `concept` card per `step2_rigorous_logic` block → one `check` card
  per `step3_checkpoint` item), and a separate `review` deck built from `step4_final_review`
  (`ox_quizzes` + `conceptual_questions`). `renderCard()` renders `state.deck[state.cardIndex]`.
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
    step3_checkpoint: [ { problem, hint, solution, visualization? } ] }
]
step4_final_review: { ox_quizzes: [{question,answer,explanation}], conceptual_questions: [{question,hint,answer}] }
```

All math is KaTeX (`$...$` / `$$...$$`); LaTeX backslashes must be double-escaped in the JSON strings.

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
- **A chapter is "complete" only once every section *and* the final-review JSON exist** in `chapter_raw/`
  (`sections_complete()` / `try_finalize_chapter()` in `program2_track.py`). Completion auto-merges into
  `data/<slug>.json` and updates `data/manifest.json` — this is the only code path that writes to `data/`.
- **Validation** (`lib/merger.py`) is a Python port of the original browser tool `pipeline/section_merger.html`
  (kept in the repo for manual/offline use) — both must stay consistent, e.g. the `VIZ_WHITELIST` and the
  requirement that `manifest` entries carry a `book` field.
- **`.gitignore` under `pipeline/`** intentionally excludes split PDFs and delivery zips
  (`work/*/sections_out/*.pdf`, `work/*/_delivery/`) — only text state (`.txt` headers, `progress.md`,
  `chapter_raw/*.json`, `state.json`) is committed, since the sandbox this pipeline runs in is ephemeral
  and PDFs are large/regenerable.

## Git workflow

Active development happens on the `dev` branch (branched from `main`); commit and push directly to `dev`
without waiting for confirmation, and don't open a pull request unless explicitly asked.
