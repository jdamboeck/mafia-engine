#!/usr/bin/env python3
"""
Mafia oracle — query the reverse-engineering research from the engine repo.

The research project (`../research/`, sibling of this engine repo) is the single
authority on the original 1986 "Mafia" (Igelsoft) C64 game. This script is the
lookup layer: it answers game/code questions with an EXACT place in the original
BASIC source plus the documented interpretation, and verifies claims against the
knowledge graph's source-backed evidence.

Subcommands:
  line   <n>              Explain BASIC line n: the source line + its documented meaning.
  lines  <a> <b>          Explain lines a..b (a labeled block, e.g. combat 30100-30160).
  search <text>           Full-text search across line docs, KG nodes, and systems.
  node   <id-or-name>     Show a knowledge-graph node with its source-backed evidence.
  verify <text>           Find the KG evidence that confirms/refutes a claim (source refs).
  quote  <n>              Print the raw BASIC source line n verbatim (no interpretation).
  conclude <lines> <claim>  GATE a conclusion behind its verbatim source. <lines> is
                          a number, a-b range, or comma list (e.g. 12103,12105). Prints
                          the raw code, then forces a citation-first answer. USE THIS
                          before stating any rule, cap, or number.
  status                  Coverage + final-state metrics (how much is known, how verified).

Every answer cites `mf-prg.bas:<line>` or a byte/`$addr` — never an unsourced claim.
Rule: never conclude from a `search` snippet (that's interpretation). Read the raw
line with `quote`/`line`, or gate the claim through `conclude`, FIRST.

Usage:
  python scripts/oracle.py line 30255
  python scripts/oracle.py search "jail"
  python scripts/oracle.py conclude 12103,12105 "a player can own at most 5 gangsters"
  python scripts/oracle.py verify "damage is at least 1 on a hit"
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("Error: PyYAML required. Use the research venv: "
          "../research/.venv/bin/python3 scripts/oracle.py ...", file=sys.stderr)
    sys.exit(2)


# ---- locate the research project robustly ---------------------------------
def find_research_root() -> Path:
    """Find ../research/ from anywhere in the engine repo, or via env override."""
    import os
    env = os.environ.get("MAFIA_RESEARCH")
    if env and (Path(env) / "research-data").is_dir():
        return Path(env)
    here = Path(__file__).resolve()
    for base in [here.parent, *here.parents]:
        # sibling `research/`, or a parent that IS the research project
        for cand in (base / "research", base.parent / "research", base):
            if (cand / "research-data" / "pass-3" / "knowledge-graph.yaml").is_file():
                return cand
    print("Error: could not locate the research project. Set MAFIA_RESEARCH=/path/to/research",
          file=sys.stderr)
    sys.exit(2)


RESEARCH = find_research_root()
BAS = RESEARCH / "src" / "decompiled_basic" / "mf-prg.bas"


def _load(rel: str):
    with open(RESEARCH / rel, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _find_key(obj, key):
    """Recursively find the first value for `key` anywhere in a nested structure."""
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            r = _find_key(v, key)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _find_key(v, key)
            if r is not None:
                return r
    return None


# ---- source access ---------------------------------------------------------
def bas_lines() -> dict[int, str]:
    """Map BASIC line number -> raw source text (petcat detokenized)."""
    out: dict[int, str] = {}
    for raw in BAS.read_text(encoding="utf-8", errors="replace").splitlines():
        s = raw.strip()
        if not s:
            continue
        num = ""
        for ch in s:
            if ch.isdigit():
                num += ch
            else:
                break
        if num:
            out[int(num)] = s
    return out


def line_docs() -> dict[int, dict]:
    ld = _find_key(_load("research-data/pass-1/basic-extraction.yaml"), "line_documentation")
    return {int(e["line"]): e for e in (ld or []) if "line" in e}


# ---- commands --------------------------------------------------------------
def cmd_line(n: int) -> int:
    src = bas_lines().get(n)
    doc = line_docs().get(n)
    print(f"# BASIC line {n}  ({BAS.relative_to(RESEARCH)})\n")
    if src:
        print(f"SOURCE  : {src}")
    else:
        print(f"SOURCE  : (no line {n} in the program — line numbers are sparse)")
    if doc:
        print(f"MEANING : {doc.get('description','(undocumented)')}")
        print(f"CATEGORY: {doc.get('category','?')}  |  confidence: {doc.get('confidence','?')}"
              f"  |  provenance: {doc.get('provenance','?')}")
    else:
        print("MEANING : (no documented interpretation for this line)")
    return 0 if (src or doc) else 1


def cmd_lines(a: int, b: int) -> int:
    src, docs = bas_lines(), line_docs()
    nums = sorted(x for x in set(src) | set(docs) if a <= x <= b)
    if not nums:
        print(f"(no lines in {a}..{b})")
        return 1
    print(f"# BASIC lines {a}..{b}  ({len(nums)} lines)\n")
    for n in nums:
        s = src.get(n, "")
        d = docs.get(n, {}).get("description", "")
        print(f"{n:>6}  {s}")
        if d:
            print(f"        → {d}")
    return 0


def cmd_quote(n: int) -> int:
    src = bas_lines().get(n)
    if not src:
        print(f"(no line {n})", file=sys.stderr)
        return 1
    print(src)
    return 0


def _iter_nodes():
    return _load("research-data/pass-3/knowledge-graph.yaml").get("nodes", []) or []


def cmd_search(text: str) -> int:
    t = text.lower()
    hits = 0
    # 1) BASIC line docs
    docs = line_docs()
    src = bas_lines()
    ld_hits = [(n, d) for n, d in docs.items()
               if t in str(d.get("description", "")).lower() or t in src.get(n, "").lower()]
    if ld_hits:
        print(f"## BASIC lines ({len(ld_hits)})  — line numbers only; NOT the source")
        for n, d in sorted(ld_hits)[:25]:
            # Deliberately short: a snippet of the INTERPRETATION, not the source.
            # Do not conclude from this — run `quote <n>` / `line <n>` to read the code.
            print(f"  {n:>6}  {str(d.get('description',''))[:72]}…")
        if len(ld_hits) > 25:
            print(f"  … {len(ld_hits)-25} more (narrow the search)")
        hits += len(ld_hits)
    # 2) KG nodes
    node_hits = [n for n in _iter_nodes()
                 if t in json.dumps(n, default=str).lower()]
    if node_hits:
        print(f"\n## Knowledge-graph nodes ({len(node_hits)})")
        for n in node_hits[:20]:
            print(f"  {n.get('id'):32} [{n.get('type')}]  {n.get('name','')[:50]}")
        hits += len(node_hits)
    if not hits:
        print(f"(no matches for '{text}')")
        return 1
    print("\n" + "!" * 70)
    print("STOP — these are search snippets (interpretation), NOT the source code.")
    print("Do NOT draw a conclusion, cap, number, or rule from a snippet above.")
    print("Read the actual line first:  oracle.py quote <n>   (verbatim BASIC)")
    print("                             oracle.py line  <n>   (source + meaning)")
    print("                             oracle.py conclude <n> \"<your claim>\"")
    print("!" * 70)
    return 0


def _node_by(key: str):
    key_l = key.lower()
    for n in _iter_nodes():
        if n.get("id") == key or str(n.get("name", "")).lower() == key_l:
            return n
    # fuzzy: id contains
    for n in _iter_nodes():
        if key_l in str(n.get("id", "")).lower() or key_l in str(n.get("name", "")).lower():
            return n
    return None


def _print_node(n: dict) -> None:
    print(f"# {n.get('id')}  [{n.get('type')}]  —  {n.get('name','')}")
    print(f"\n{n.get('description','')}\n")
    print(f"source           : {n.get('source','?')}")
    print(f"verified         : {n.get('verified')}  ({n.get('verification_method','?')})")
    if n.get("verification_note"):
        print(f"verification note: {n['verification_note']}")
    ev = n.get("evidence")
    ev = ev if isinstance(ev, list) else ([ev] if ev else [])
    if ev:
        print(f"\nEVIDENCE ({len(ev)}) — the source-backed basis:")
        for e in ev:
            ref = e.get("ref", "?")
            detail = e.get("quote") or e.get("note") or ""
            print(f"  [{e.get('kind','?'):11}] {ref}")
            if detail:
                print(f"                {str(detail)[:120]}")


def cmd_node(key: str) -> int:
    n = _node_by(key)
    if not n:
        print(f"(no KG node matching '{key}'). Try: oracle.py search {key}", file=sys.stderr)
        return 1
    _print_node(n)
    return 0


def cmd_verify(text: str) -> int:
    """Surface the source-backed evidence relevant to a claim so it can be checked."""
    t = text.lower()
    words = [w for w in t.replace("(", " ").replace(")", " ").split() if len(w) > 3]
    scored = []
    for n in _iter_nodes():
        blob = json.dumps(n, default=str).lower()
        score = sum(1 for w in words if w in blob)
        if score:
            scored.append((score, n))
    scored.sort(key=lambda x: -x[0])
    if not scored:
        print(f"No knowledge-graph evidence matches '{text}'.")
        print("This claim is NOT backed by the research — treat it as unverified.")
        return 1
    print(f"# Evidence for: \"{text}\"\n")
    print("The research is source-backed; check the claim against these cited facts:\n")
    for _, n in scored[:3]:
        _print_node(n)
        print()
    print("VERDICT RULE: a claim is CONFIRMED only if it matches the cited BASIC line / "
          "byte evidence above. If it contradicts them, the claim is WRONG. If nothing "
          "above addresses it, it is UNVERIFIED (not in the research).")
    return 0


def _parse_line_spec(spec: str) -> list[int]:
    """Parse '30255' | '30100-30160' | '12103,12105' into a sorted line list."""
    nums: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            nums.update(range(int(a), int(b) + 1))
        elif part:
            nums.add(int(part))
    return sorted(nums)


def cmd_conclude(spec: str, claim: str) -> int:
    """Gate a conclusion behind its verbatim source.

    You may not state a rule/cap/number about the game without the exact BASIC
    line(s) in front of you. This prints the raw source for the cited line(s),
    then a template that forces the answer to quote code, not a summary. It exists
    because the failure mode is concluding from a search snippet (interpretation)
    instead of the source — e.g. reading '5 apartment slots' and guessing a cap of 5
    when the code (12105) actually caps at 10 and 12103 is a boolean any-check."""
    src, docs = bas_lines(), line_docs()
    lines = _parse_line_spec(spec)
    present = [n for n in lines if n in src]
    if not present:
        print(f"REFUSED: none of the cited line(s) {spec} exist in the program. "
              "You cannot conclude from lines that aren't there — re-check with "
              "`oracle.py search <keyword>` and cite real lines.", file=sys.stderr)
        return 1
    print(f'# Claim under test:\n  "{claim}"\n')
    print("## Verbatim source (this — not a summary — is what you must cite):\n")
    for n in present:
        print(f"  {n:>6}  {src[n]}")
        d = docs.get(n, {})
        if d.get("description"):
            prov = d.get("provenance", "?")
            print(f"         ↳ interpretation ({prov}, conf {d.get('confidence','?')}): "
                  f"{d['description']}")
    missing = [n for n in lines if n not in src]
    if missing:
        print(f"\n  (requested but absent — sparse numbering: {missing})")
    print("\n" + "=" * 70)
    print("Now write the conclusion in THIS form — every clause tied to a line above:")
    print('  - "According to mf-prg.bas:<line> (`<verbatim code fragment>`), <what it does>."')
    print("  - The final rule/number MUST appear in the quoted code (a literal, a")
    print("    comparison, a loop bound). If it does not, you have NOT proven it —")
    print("    quote more lines or say UNVERIFIED. Do not infer a number from prose.")
    print("  - If two lines look like they conflict (e.g. a `for i=1 to 5` next to a")
    print("    `=10` check), state what EACH does separately; don't average or guess.")
    print("=" * 70)
    return 0


def cmd_status() -> int:
    cov = _find_key(_load("research-data/source-coverage.yaml"), "source_coverage")
    if not cov:
        print("Error: source-coverage.yaml missing/unreadable in the research project.",
              file=sys.stderr)
        return 2
    b = cov["basic"]
    print("# Mafia research — final state (earned + gated, not self-certified)\n")
    print(f"BASIC understood : {b['understood_pct']}%  ({b['understood_lines']}/{b['total_lines']} lines)")
    print(f"Binary interpreted: {cov['binary']['interpreted_pct']}%")
    rl = cov["levels"]["reachability_level"]
    print(f"Dead code        : {rl['unreachable']}  (reachable {rl['reachable']}, {rl['data_blocks']} DATA blocks)")
    nodes = _iter_nodes()
    verified = sum(1 for n in nodes if n.get("verified"))
    print(f"Knowledge graph  : {len(nodes)} nodes, {verified} verified (source-backed)")
    print("\nAuthority: every answer traces to mf-prg.bas:<line> or a byte/$addr. "
          "See ../research/docs/recreation-readiness.html and docs/timeline.md.")
    return 0


USAGE = __doc__


def main() -> int:
    a = sys.argv[1:]
    if not a or a[0] in ("-h", "--help", "help"):
        print(USAGE)
        return 0
    cmd, rest = a[0], a[1:]
    try:
        if cmd == "line":
            return cmd_line(int(rest[0]))
        if cmd == "lines":
            return cmd_lines(int(rest[0]), int(rest[1]))
        if cmd == "quote":
            return cmd_quote(int(rest[0]))
        if cmd == "search":
            return cmd_search(" ".join(rest))
        if cmd == "node":
            return cmd_node(" ".join(rest))
        if cmd == "verify":
            return cmd_verify(" ".join(rest))
        if cmd == "conclude":
            return cmd_conclude(rest[0], " ".join(rest[1:]))
        if cmd == "status":
            return cmd_status()
    except (IndexError, ValueError):
        print(f"bad arguments for '{cmd}'.\n\n{USAGE}", file=sys.stderr)
        return 2
    print(f"unknown command '{cmd}'.\n\n{USAGE}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
