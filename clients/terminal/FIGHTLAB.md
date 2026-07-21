# fightlab — the combat debug tool

`fightlab` plays, watches, and replays tactical combat with **every calculation
observable**. It has no game around it: no city map, no economy, no save/load UI —
just a fight, driven from a scenario file or replayed from a recording. It is the
tool for seeing *exactly* what the combat engine did and why.

It is a thin client: it renders and reads keys. Every number it prints already
exists on the engine's combat screen, an `ActivationEvent`, or a `CombatResult` — it
never recomputes a hit or a damage roll itself.

---

## Running it

Invoke it as a module from the repo root (`/home/data/lab/mine/mafia/engine`):

```bash
python -m clients.terminal.fightlab <command> [options]
```

Two commands:

| Command | What it does |
|---|---|
| `play  --scenario PATH [--seed N] [--debug]` | Drive a scenario file as a **human-vs-AI** fight |
| `watch --recording PATH [--debug] [--delay S]` | Step through a **recorded** fight, forwards and back |

`python -m clients.terminal.fightlab --help` (or `… play --help` / `… watch --help`)
prints the same.

---

## `play` — fight a scenario yourself

You are **side 1** (human); **side 2** is the AI. The tool renders the grid, a fighter
panel, and a prompt each time it is your turn.

```bash
python -m clients.terminal.fightlab play \
  --scenario data/game_configs/mafia_1920s/content/scenarios/kdh_ambush.yaml \
  --seed 42
```

- `--seed N` fixes the RNG so the fight is reproducible (and its outcome assertable).
  Omit it and each run differs; the scenario file's own `seed:` is the default.
- `--debug` prints each shot's full hit/damage arithmetic as the fight goes (see
  [Debug output](#debug-output-what-every-number-means)).

**Controls during your turn** (type the key, then Enter):

| Key | Action |
|---|---|
| `w` `a` `s` `d` | Move one cell (up / left / down / right) |
| `f` then `w`/`a`/`s`/`d` | Aim and **fire** in that direction |
| `p` | Pass (spend the activation, do nothing) |
| `surrender` (the whole word) | Give up — the other side wins |
| Ctrl-D / EOF | Same as surrender |

`play` builds **no `GameState` and writes no file** — the fight itself is the whole
artifact. (To keep a fight for later, record one; see
[Making a recording](#making-a-recording-file).)

---

## `watch` — step through a recorded fight

`watch` loads a recording and lets you walk it one activation at a time, forwards or
back, or autoplay to the end.

```bash
python -m clients.terminal.fightlab watch --recording recordings/kdh_ambush.json --debug
```

**Controls** (single keypress, no Enter needed on a real terminal):

| Key | Action |
|---|---|
| Space / Enter / (most keys) | Advance one activation |
| `b` | Step **back** one activation (loads the snapshot at `index-1` — no re-simulation) |
| `a` | **Autoplay** to the end, then stop (it does **not** loop or wrap) |
| any key while autoplaying | Pause |
| `q` / EOF | Quit |

Each frame is labelled with its activation index and the action taken, e.g.
`-- activation 18 / 33 | activation:shoot --`.

**Pacing autoplay.** By default autoplay redraws as fast as the terminal can — each
frame clears the screen, so you only see the last one. Add `--delay S` to pause `S`
seconds on each frame so you can actually watch it play:

```bash
python -m clients.terminal.fightlab watch --recording recordings/kdh_ambush.json --delay 0.5
```

`--delay` paces only autoplay; manual stepping (space / `b`) is always immediate, and
`--delay 0` (the default) keeps the instant race-to-the-end behaviour. Press any key
during a paced autoplay to pause it.

### Replay is a fidelity detector

`watch` replays the recorded decisions against the **live** combat formulas. If the
formulas have changed since the recording was made, a replayed shot produces a
different result than the one recorded — `watch` prints the recorded-vs-recomputed
values side by side and **halts autoplay** at the diverging activation. A clean replay
(formulas unchanged) shows no divergence. This is how a recording doubles as a
regression test for the combat math.

---

## Debug output: what every number means

With `--debug`, every shot prints a block like this (this is `play`; `watch --debug`
prefixes it with `(replayed)`):

```
=== activation 18 | side 1, fighter 1 (hero) -> shoot east ===
  weapon: revolver (ts=5, tg=10, range=15)
  hit check:
    draw = rng.range(ts=5)          -> 4
    accuracy attr (kraft)           -> 34
    both factors non-zero           -> HIT
  damage roll:
    draw = rng.range(tg=10)         -> 0
    damage attr (brutalitaet)         -> 28
    int(draw + attr/10) + 1         -> int(0 + 2.8) + 1 = 3
  target: side 2, fighter 1 (schuldner)  energie 35 -> 32
  result: hit, damage=3, downed=False
```

Reading it:

- **weapon** — the firing weapon and its stats: `ts` (hit-check bound), `tg` (damage
  bound), `range` (how many cells the shot travels).
- **hit check** — the accuracy roll: a `rng.range(ts)` draw and the attacker's accuracy
  attribute (`kraft`). Both non-zero → HIT.
- **damage roll** — the damage: a `rng.range(tg)` draw, the attacker's damage attribute
  (`brutalitaet`), and the exact `int(draw + attr/10) + 1` arithmetic.
- **target** — who was struck (real name, not a placeholder) and its energie before → after.
- **result** — the recorded outcome: hit/miss, damage dealt, whether the target went down.

A shot that reaches no fighter (fired into empty space, or into a wall) prints
`(shot reached no target — no hit check)` instead of a hit-check block — because no
hit roll happened.

Every value in this block comes straight off the recorded event; nothing here is
recomputed by the tool.

---

## Scenario files

A scenario file describes a **complete two-sided fight** with no `GameState` and no
roster. It lives under `data/game_configs/mafia_1920s/content/scenarios/`. The shipped
example (`kdh_ambush.yaml`):

```yaml
encounter: kdh_ambush   # the ENEMY side — reuses the in-game kdh_ambush encounter
seed: 42                # default RNG seed; --seed overrides it
player:                 # the PLAYER side — invented fighters (no roster needed)
  - name: hero
    weapon: 5           # revolver (id into entities/weapons.yaml)
    energie: 20
    kraft: 34
    brutalitaet: 28
```

- **`encounter`** names one of this config's own encounter declarations
  (`content/encounters/*.yaml`). The enemy side is built through the *same*
  `Scenario.from_encounter` path the real game uses, so a scenario's enemy side and the
  in-game fight cannot diverge.
- **`player`** is a list of invented fighters (there is no roster to draw from). Each
  needs a `name`, a `weapon` id, and combat stats (`energie`, `kraft`, `brutalitaet`;
  `intelligenz` is optional).
- **`seed`** is the default RNG seed; `--seed` on the command line overrides it.

An unknown weapon id (either side) fails **at load**, naming the id — never as a crash
mid-fight.

To make a new scenario, copy `kdh_ambush.yaml`, point `encounter` at a different
declaration, and edit the `player` list.

---

## Making a recording file

By design, `play` writes nothing to disk — that is the "no save UI" boundary. To
produce a recording for `watch`, record a fight programmatically with the engine's
`record_fight` + `save`:

```python
from clients.terminal.fightlab import load_scenario
from engine.interactions import AiDriver
from engine.recording import record_fight, save

scenario = load_scenario(
    "data/game_configs/mafia_1920s/content/scenarios/kdh_ambush.yaml"
)
# Drive both sides with the AI so the fight runs headlessly to the end.
result, recording = record_fight(scenario, {1: AiDriver(), 2: AiDriver()})
save(recording, "recordings/kdh_ambush.json")
print(result.winner, result.losses)
```

A ready-made example lives at **`recordings/kdh_ambush.json`** (seed 42, 34
activations). Watch it:

```bash
python -m clients.terminal.fightlab watch --recording recordings/kdh_ambush.json --debug
```

Recordings are **run artifacts, not versioned game content**, so `recordings/` is
gitignored — regenerate the file any time with the snippet above.

---

## Piping input (scripting / tests)

Both commands read keys from stdin, so you can drive them non-interactively:

```bash
# play: pass once, then surrender
printf 'p\nsurrender\n' | python -m clients.terminal.fightlab play \
  --scenario data/game_configs/mafia_1920s/content/scenarios/kdh_ambush.yaml --seed 42

# watch: advance five times, step back once, quit
printf ' \n \n \n \n \nb\nq\n' | python -m clients.terminal.fightlab watch \
  --recording recordings/kdh_ambush.json
```

On a piped stream a genuinely blank line reads as EOF and quits `watch` — send an
explicit key (space, `a`, `b`, `q`) per step.

---

## What fightlab deliberately does not do

- No city map, economy, jobs, or turn loop — it is **combat only**.
- No `--player`, no config-dir flag, no save/load menu.
- It never computes a hit or damage itself. If a value you want isn't in the debug
  dump, that means the recording schema doesn't carry it yet — the fix belongs in the
  engine's recording layer, not here.
