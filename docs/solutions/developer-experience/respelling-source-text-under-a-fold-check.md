---
title: "Respelling source text under a fold check: freeze it, fold it, list every exception, review the rest"
date: 2026-10-17
category: developer-experience
module: tests
problem_type: developer_experience
component: testing_framework
severity: medium
applies_when:
  - "Changing the spelling, case or spacing of text whose words must stay the source's"
  - "Replacing a byte-for-byte test with a looser comparison that must still catch a changed word"
  - "Adding a respelling the fold cannot undo by itself (a reform spelling, a joined hyphen, a fixed typo)"
  - "Editing a template string that the client joins with other text on one screen line"
symptoms:
  - "A string test pinned to the research text fails on every respelled string"
  - "A respelling passes the fold check but reads wrong on screen (a wrong umlaut, a wrong capital)"
  - "A list entry no longer matches any key after a later edit"
tags:
  - testing
  - text
  - theme
  - fixtures
  - review-gate
related_components:
  - theme
  - client
---

# Respelling source text under a fold check

## Context

The classic theme carried the C64's text as the C64 printed it: one letter case,
`ae`/`oe`/`ue`/`ss` for ä/ö/ü/ß, and runs of spaces that padded a line to the
40-column wrap. Tests pinned it byte for byte to `location-dialogue.yaml`. The string
normalization pass rewrote it into today's German spelling while keeping the author's
words, so "byte for byte" had to give way to a check that still fails when a *word*
changes.

## Guidance

**1. Freeze the source text before touching it.** `tests/fixtures/c64_text/` holds
every display string as it was, keyed exactly as in the tree (theme keys, entity
names and descriptions, fighter names, the client's art literals). The research-pinned
tests moved to the fixture (`test_*_strings_verbatim` read `fixture_value`, and
`test_every_fixture_location_key_is_its_disk_file_text_byte_for_byte` ties it to
`location-dialogue.yaml`), so research → fixture stays exact and fixture → tree is
the fold's job. Without the frozen copy, the first respelled unit destroys the thing
the later units are checked against.

**2. Fold both sides; assert the folds are equal.** `tests/text_fold.py`'s `fold`
lowercases, writes ä/ö/ü/ß back as ae/oe/ue/ss and collapses whitespace runs.
`tests/test_text_fold.py` asserts `fold(tree) == fold(fixture)` for every key and that
no fixture key disappeared. The fold is **many-to-one**: `neue` and `nüe` fold alike,
so it proves the words, never the umlaut or the capital. That gap is closed by review,
not by a cleverer fold.

**3. Every exception is a named list entry, and an unused entry fails.** What the fold
cannot undo goes on a list in the fixture, each entry `source`/`new`/`keys`, mapped
back as a whole word at those keys only: `reform.yaml` (`TIP` → `Tipp`,
`wieviele` → `wie viele`), `hyphen_joins.yaml` (`FALSCH-    GELD` → `Falschgeld`),
`author_typos.yaml` (`poliyei` → `Polizei`, each with a `reason`) and
`letter_spaced.yaml` (`H A E N D L E R` → `H Ä N D L E R`). An entry no key uses
fails the check, as an unused citation allowance does, so the lists cannot rot into
blanket permissions.

**4. Review is generated, side by side, before each commit.** `tools/text_side_by_side.py`
diffs the tree against the fixture and writes every changed key as old → new, grouped
by file, placeholders shown as `⟨name⟩` markers, any key whose fold differs flagged.
Keys the client joins on one line (`joined_keys.yaml`) are rendered together. The
user approved each unit from that page; the user's decisions during review (row
breaks inside running text become spaces, clear typos are fixed on a list, every
rank and gangster name starts uppercase) went into the plan, not into silent edits.

**5. Tests take the new text by edit, never by loosening.** A test that quotes a
theme string gets the new literal. Making an assertion case-insensitive to absorb
the change would hide the next real regression.

## Pitfalls hit

- **Hyphens at row breaks.** The C64 split words at its wrap (`SCHUL-  DEN`,
  `lei-\nder`). Joining them is a real change the fold cannot see through: each join
  is a `hyphen_joins.yaml` entry with the source form, padding and newline included.
  A compound's own hyphen (`army-hand-\n\ngranaten` → `Army-Handgranaten`) keeps the
  first and loses only the break's.
- **The number's sign space at a joined row.** A row that began with a printed
  number got its separating space from the C64 number style's sign position
  (`_format.yaml`; `engine/c64_numbers.py`: a space for zero and positives, checked
  against VICE), so joining the rows adds no space of its own: `habe\n{stock}faesser`
  → `habe{stock}Fässer`. Writing `habe {stock}` prints two spaces.
- **Placeholders sit flush against words.** `das{price}$ Miete`, `Für{count}Pässe`:
  the word boundary is the brace, and a list entry's whole-word match treats `{…}` as
  one. Judge such strings in the side-by-side's rendered form, not the raw template.
- **A fragment is cased by its place on screen.** A rank name or a side's name lands
  mid-sentence or after a label; casing it as a sentence start is wrong somewhere.
  `joined_keys.yaml` names those groups so they are reviewed together.
- **Lowercased test needles.** Tests quoted the old text as lowercase needles
  (`"du hast zu wenig kies!"`), so every unit grepped the tests (the test theme's
  included) for each fragment it changed. A missed `in` needle turns the suite red; a
  missed `not in` needle is worse: it passes vacuously once its text is respelled.
- **"Verbatim" outlives the change.** File headers, docstrings and docs kept calling
  the text verbatim after it was not. `tests/test_text_wording.py` now fails on a
  string, entity, encounter or scenario file comment that says so, sparing backticked
  source quotes (held to `mf-prg.bas` by `tests/test_citations.py`) and the fixture.
