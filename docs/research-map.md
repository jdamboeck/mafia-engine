# Research map — where the game knowledge lives

The authoritative knowledge of the original 1986 C64 game "Mafia" (Igelsoft) is a
sibling checkout at `../research/`. **Never invent game behavior** — port from the
cited BASIC line blocks.

**For any game-behavior question — "how does X work", "is it true that Mafia does Y",
"which BASIC line drives Z" — invoke the `mafia-oracle` skill first.** It answers from
this research and is the preferred path over reading the raw files below. This map
exists for when you need to open a specific file directly.

Key research files (all under `../research/`):

| File | Contains |
|---|---|
| `src/decompiled_basic/mf-prg.bas` | The authoritative logic (870 lines, 108 vars). Each Python handler is a port of a labeled line block (10000–26080). |
| `research-data/pass-2/location-handlers.yaml` | Per-menu-option behavior + guards — source for YAML shells and handler ids. |
| `research-data/pass-2/game-logic.yaml` | RNG outcome tables, combat AI (`ri()` direction memory), score/rank formulas. |
| `research-data/pass-2/systems-analysis.yaml`, `data-structures.yaml` | Combat/economy/wanted rules, the ~108 variables, grid dimensions. |
| `research-data/pass-1/location-extraction.yaml` | Menu trees + option→handler mapping. |
| `research-data/pass-2/location-dialogue.yaml` | **Dialogue source of truth** — each location's complete verbatim script (entry prompt + every option + the game's printed responses, each cited to `mf-prg.bas:<line>`). Port the exact strings from here. |
| `research-data/pass-1/game-text.yaml` | The full verbatim text corpus (all print/input/data/assign strings + SEQ menus). The authority for any on-screen string (narration, prompts, weapon/rank/vehicle/opponent names). |
| `docs/systems/*.md` | Human-readable system summaries. |

**Fidelity bar is behavioral, not bit-exact:** match the original's formulas,
probabilities, rewards, and outcomes exactly — but internal representation and RNG
draw-order may differ. A seeded run matches statistically/mechanically, not byte-for-byte.
