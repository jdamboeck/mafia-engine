"""The fold check: every classic string, content name and art line still has its source's
words (R12, KTD-1 to KTD-3, KTD-8).

``tests/fixtures/c64_text/`` freezes the text as the C64 printed it, before the string
normalization; ``tests/text_fold.py`` folds a string to compare it with its fixture text.
The proof chain: the research's disk-file texts equal the fixture byte for byte (here,
and the ``test_*_strings_verbatim`` shell tests), and the tree's text folds to the
fixture's. Also here: the fold's own cases (AE1, AE3, AE5, AE6), the named lists'
unused-entry rule, NFC, and the side-by-side tool (``tools/text_side_by_side.py``).
"""

from __future__ import annotations

import shutil
import unicodedata
from pathlib import Path

import pytest
import yaml

from engine.strings import Resolver
from tests.helpers import LOCATION_DIALOGUE, read_research
from tests.text_fold import (
    ASCII_ART,
    FIXTURE,
    LISTS,
    ROOT,
    Respelling,
    expand,
    fixture_text,
    flat,
    fold,
    fold_failures,
    joined_groups,
    respellings,
    tree_text,
    unused_entries,
)
from tools.text_side_by_side import side_by_side, show

_CONFIG = ROOT / "data" / "game_configs" / "mafia_1920s"
FIXTURE_DIR = ROOT / FIXTURE

# The pub's recruit option and the sgl menu's fake-police option, as the disk files
# (src/pub, src/sgl) hold them: location-dialogue.yaml, pub and sgl.
_PUB_RECRUIT = "'ICH SUCHE EIN PAAR JUNGS FUER MEINE    GANG. MAL SEHEN, WER DA IST...'"
_SGL_FAKE_POLICE = "'POLIZEI! IN IHRER KASSE IST FALSCH-    GELD VERSTECKT, DAS IST KONFISZIERT!'"


# --------------------------------------------------------------------------- #
# The tree folds to the fixture                                               #
# --------------------------------------------------------------------------- #


def test_every_fixture_key_folds_to_the_tree_s_text():
    """Every key of the fixture is still in the tree, and its text folds to the
    fixture's, with the reform list and the hyphen joins applied at their keys."""
    current, fixture = flat(tree_text()), flat(fixture_text())
    assert fold_failures(current, fixture, respellings()) == []


def test_the_fixture_covers_every_text_source():
    """The fixture holds every classic string file, entity file and fight with a
    fighter name, and the client's art text: the tree's text has no file it lacks."""
    assert set(tree_text()) == set(fixture_text())
    assert len(flat(fixture_text())) == sum(len(keys) for keys in fixture_text().values())


def test_no_reform_or_hyphen_entry_is_unused():
    assert unused_entries(flat(tree_text()), respellings()) == []


def test_every_joined_key_group_names_fixture_keys():
    """Each pattern of each joined group matches a fixture key, so the list cannot
    name a key that is gone."""
    keys = flat(fixture_text())
    for group in joined_groups():
        for pattern in group["keys"]:
            assert expand([pattern], keys), (group["where"], pattern)


def _text_files() -> list[Path]:
    return [
        *sorted((_CONFIG / "themes").rglob("*.yaml")),
        *sorted((_CONFIG / "entities").glob("*.yaml")),
        *sorted((_CONFIG / "content").rglob("*.yaml")),
        ROOT / ASCII_ART,
    ]


def test_every_string_in_the_theme_and_content_files_is_nfc():
    """An umlaut is one code point, so a column layout counts it as one cell (KTD-8)."""
    files = _text_files()
    assert len(files) > 40
    assert [
        path.relative_to(ROOT).as_posix()
        for path in files
        if not unicodedata.is_normalized("NFC", path.read_text(encoding="utf-8"))
    ] == []


def test_a_decomposed_umlaut_is_not_nfc():
    """The NFC check above can fail: ``u`` + combining diaeresis is not NFC."""
    assert not unicodedata.is_normalized("NFC", "für")
    assert unicodedata.is_normalized("NFC", "für")


# --------------------------------------------------------------------------- #
# The fold                                                                    #
# --------------------------------------------------------------------------- #


def test_ae1_the_normalized_recruit_option_folds_to_the_padded_source():
    new = "'Ich suche ein paar Jungs für meine Gang. Mal sehen, wer da ist...'"
    assert fold(new) == fold(_PUB_RECRUIT)


def test_ae6_a_joined_wrap_hyphen_folds_back_only_with_its_entry():
    new = "'Polizei! In Ihrer Kasse ist Falschgeld versteckt, das ist konfisziert!'"
    key = "locations.sgl.menu.fake_police"
    join = Respelling("FALSCH-    GELD", "Falschgeld", (key,), "hyphen_joins")
    assert fold(new, [join]) == fold(_SGL_FAKE_POLICE)
    assert fold(new) != fold(_SGL_FAKE_POLICE)
    assert fold_failures({key: new}, {key: _SGL_FAKE_POLICE}, [join]) == []
    assert fold_failures({key: new}, {key: _SGL_FAKE_POLICE}, []) != []


def test_ae3_tipp_folds_to_tip_only_with_the_reform_entry():
    key = "locations.pub.menu.tip"
    source = "'HAST DU VIELLEICHT 'N GUTEN TIP FUER   'NEN UEBERFALL ODER SOWAS?'"
    new = "'Hast du vielleicht 'n guten Tipp für 'nen Überfall oder sowas?'"
    reform = Respelling("tip", "Tipp", (key,))
    assert fold(new, [reform]) == fold(source)
    assert fold(new) != fold(source)
    # The entry maps a whole word only: "tipps" is not "tipp".
    assert fold("tipps", [reform]) == "tipps"


def test_ae5_a_changed_word_fails_naming_the_key():
    key = "locations.sub.entry_prompt"
    source = "DU BIST IN EINEM GROSSEN U-BAHNHOF. 'NE MENGE LEUTE TREIBEN SICH HIER 'RUM..."
    new = "Du bist in einem großen U-Bahnhof. Eine Menge Leute treiben sich hier 'rum..."
    (failure,) = fold_failures({key: new}, {key: source}, [])
    assert failure.startswith(f"{key}: ")
    kept = "Du bist in einem großen U-Bahnhof. 'Ne Menge Leute treiben sich hier 'rum..."
    assert fold_failures({key: kept}, {key: source}, []) == []


def test_a_fixed_author_typo_folds_back_only_with_its_entry():
    """``Poliyei`` is ``Polizei`` in the new text: the fold check passes only because the
    slip is on ``author_typos.yaml``, read like the reform list, and each entry there
    says why it is a slip."""
    key = "locations.sgl.police_waiting"
    source = "vor dem laden erwartet dich die\npoliyei!"
    new = "Vor dem Laden erwartet dich die Polizei!"
    typo = Respelling("poliyei", "Polizei", (key,), "author_typos")
    assert fold_failures({key: new}, {key: source}, [typo]) == []
    assert fold_failures({key: new}, {key: source}, []) != []
    entries = yaml.safe_load((FIXTURE_DIR / "author_typos.yaml").read_text(encoding="utf-8"))
    assert entries["author_typos"]
    assert all(entry["reason"].strip() for entry in entries["author_typos"])
    assert {e.kind for e in respellings()} == set(LISTS)
    assert (typo.source, typo.new, typo.keys) in {
        (e.source, e.new, e.keys) for e in respellings() if e.kind == "author_typos"
    }


def test_a_letter_spaced_umlaut_folds_back_only_with_its_entry():
    """``H Ä N D L E R`` folds to ``h ae n d l e r``, the C64 heading to ``h a e n d l e
    r``: the art heading passes only because it is on ``letter_spaced.yaml``."""
    key = "ascii_art.location.aut.1"
    source = "  |   A U T O M O B I L - H A E N D L E R    |"
    new = "  |   A U T O M O B I L - H Ä N D L E R      |"
    spaced = Respelling("H A E N D L E R", "H Ä N D L E R", (key,), "letter_spaced")
    assert fold_failures({key: new}, {key: source}, [spaced]) == []
    assert fold_failures({key: new}, {key: source}, []) != []
    assert (spaced.source, spaced.new, spaced.keys) in {
        (e.source, e.new, e.keys) for e in respellings() if e.kind == "letter_spaced"
    }


def test_the_fold_check_covers_the_title_prompt_the_tagline_and_the_art_captions():
    """The title screen's prompt line (the client's indent and the theme's
    ``client.title.prompt``), the tagline and every art line with a letter in it are
    fixture keys, and each folds to its fixture text."""
    current = tree_text()["client/ascii_art.yaml"]
    fixture = fixture_text()["client/ascii_art.yaml"]
    assert set(current) == set(fixture)
    resolver = Resolver.from_config(_CONFIG, theme="classic")
    assert current["ascii_art.title_prompt"] == "   " + resolver.resolve("client.title.prompt")
    assert {"ascii_art.title_prompt", "ascii_art.title.12", "ascii_art.location.sph.13"} <= set(
        fixture
    )
    assert fold_failures(current, fixture, respellings()) == []


def test_a_key_gone_from_the_tree_fails():
    (failure,) = fold_failures({}, {"locations.aut.menu.buy": "WAGEN KAUFEN"}, [])
    assert failure == "locations.aut.menu.buy: in the fixture but gone from the tree"


def test_an_entry_no_string_uses_fails():
    key = "locations.aut.menu.buy"
    current = {key: "Wagen kaufen"}
    reform = Respelling("tip", "Tipp", (key,))
    join = Respelling("falsch-  geld", "Falschgeld", (key,), "hyphen_joins")
    elsewhere = Respelling("tip", "Tipp", ("locations.no.such_key",))
    assert unused_entries(current, [reform, join, elsewhere]) == [
        f"reform 'tip' -> 'Tipp': unused at {key}",
        f"hyphen_joins 'falsch-  geld' -> 'Falschgeld': unused at {key}",
        "reform 'tip' -> 'Tipp': unused at locations.no.such_key",
    ]
    assert unused_entries({key: "der Tipp"}, [reform]) == []


def test_the_fold_only_folds_case_umlauts_and_whitespace():
    assert fold("Größe  ÄRGER\nÜbel") == "groesse aerger uebel"


# --------------------------------------------------------------------------- #
# The research's disk-file texts are the fixture's                            #
# --------------------------------------------------------------------------- #

#: Where the tree's location text was not the disk file's when the fixture froze it:
#: ``key -> (the research's run, the fixture's run)``. src/waf holds one space between
#: HANDBOELLER and GEFAELLIG (its line is 40 characters without padding); the theme had
#: two. The fold collapses the run, so the normalized text owes nothing to it.
_NOT_THE_DISK_FILE = {
    "locations.waf.entry_prompt": ("HANDBOELLER GEFAELLIG", "HANDBOELLER  GEFAELLIG"),
}


def _disk_file_texts() -> dict[str, str]:
    """``{fixture key: text}`` of each location's title, entry prompt and menu labels
    in ``location-dialogue.yaml``, a menu label keyed by its option's id in the
    location's shell (``content/locations/<key>.yaml``, in menu order)."""
    data = yaml.safe_load(read_research(LOCATION_DIALOGUE))
    out: dict[str, str] = {}
    for location in data["location_dialogue"]["locations"]:
        key = location["key"]
        shell = yaml.safe_load((_CONFIG / "content" / "locations" / f"{key}.yaml").read_text())
        ids = [option["id"] for option in shell["options"]]
        assert len(ids) == len(location["options"]), key
        out[f"locations.{key}.title"] = location["title"]
        out[f"locations.{key}.entry_prompt"] = location["entry_prompt"]
        for option_id, option in zip(ids, location["options"]):
            out[f"locations.{key}.menu.{option_id}"] = option["option_text"]
    return out


def _disk_file_mismatches(fixture: dict[str, str], research: dict[str, str]) -> list[str]:
    mismatches = []
    for key, text in research.items():
        if key in _NOT_THE_DISK_FILE:
            run, fixture_run = _NOT_THE_DISK_FILE[key]
            assert run in text, key
            text = text.replace(run, fixture_run)
        if fixture.get(key) != text:
            mismatches.append(key)
    return mismatches


def test_every_fixture_location_key_is_its_disk_file_text_byte_for_byte():
    """The twelve locations' ``title``, ``entry_prompt`` and ``menu.*`` in the fixture
    are ``location-dialogue.yaml``'s, the wrap padding included (KTD-1)."""
    research = _disk_file_texts()
    assert len(research) == 12 * 2 + 44  # location-dialogue.yaml's 44 menu options
    assert _disk_file_mismatches(flat(fixture_text()), research) == []


def test_a_changed_fixture_character_fails_naming_its_key():
    research = _disk_file_texts()
    fixture = flat(fixture_text())
    fixture["locations.aut.menu.buy"] = "WAGEN KAUFEn"
    assert _disk_file_mismatches(fixture, research) == ["locations.aut.menu.buy"]


# --------------------------------------------------------------------------- #
# The side-by-side tool                                                       #
# --------------------------------------------------------------------------- #

_STRINGS = Path("data/game_configs/mafia_1920s/themes/classic/strings")


@pytest.fixture
def scratch(tmp_path: Path) -> Path:
    """A copy of the tree's text sources and the fixture, to change freely.

    The copy's fixture is frozen again from the copy's own text, with empty reform,
    hyphen and typo lists, so the copy starts unchanged however far the normalization has got."""
    for part in (
        _STRINGS,
        Path("data/game_configs/mafia_1920s/entities"),
        Path("data/game_configs/mafia_1920s/content"),
        FIXTURE,
    ):
        shutil.copytree(ROOT / part, tmp_path / part)
    (tmp_path / ASCII_ART).parent.mkdir(parents=True)
    shutil.copy(ROOT / ASCII_ART, tmp_path / ASCII_ART)
    for name, keys in tree_text(tmp_path).items():
        dumped = yaml.safe_dump(keys, allow_unicode=True, sort_keys=False, width=1_000_000)
        (tmp_path / FIXTURE / name).write_text(dumped, encoding="utf-8")
    for kind in LISTS:
        (tmp_path / FIXTURE / f"{kind}.yaml").write_text(f"{kind}: []\n", encoding="utf-8")
    return tmp_path


def _edit(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert text.count(old) == 1, old
    path.write_text(text.replace(old, new), encoding="utf-8")


def test_the_tool_lists_no_change_on_an_unchanged_tree(scratch: Path):
    assert "No changes" in side_by_side(scratch)
    assert " 0 whose fold differs." in side_by_side(ROOT)


def test_the_tool_lists_exactly_the_changed_key(scratch: Path):
    _edit(scratch / _STRINGS / "aut.yaml", '"Wagen kaufen"', '"Wagen Kaufen"')
    report = side_by_side(scratch)
    assert "1 changed keys, 0 whose fold differs." in report
    assert f"## {_STRINGS.as_posix()}/aut.yaml" in report
    assert "- `locations.aut.menu.buy`" in report
    assert "  - old: `Wagen kaufen`" in report
    assert "  - new: `Wagen Kaufen`" in report
    assert report.count("- `locations.") == 1
    assert "fold differs" not in report.split("\n", 3)[3]


def test_the_tool_flags_a_changed_word_and_the_fold_check_fails(scratch: Path):
    _edit(scratch / _STRINGS / "aut.yaml", '"Wagen kaufen"', '"Wagen leasen"')
    report = side_by_side(scratch)
    assert "1 changed keys, 1 whose fold differs." in report
    assert "  - **fold differs**: `wagen leasen` ≠ `wagen kaufen`" in report
    (failure,) = fold_failures(
        flat(tree_text(scratch)), flat(fixture_text(scratch)), respellings(scratch)
    )
    assert failure.startswith("locations.aut.menu.buy: ")


def test_the_tool_renders_a_joined_group_together(scratch: Path):
    """A changed rank name brings every template it is a value of into one section."""
    _edit(scratch / "data/game_configs/mafia_1920s/entities/ranks.yaml", '"Ganove"', '"ganove"')
    report = side_by_side(scratch)
    joined = report.split("## Joined on screen", 1)[1]
    assert "- `entities.ranks.4`" in joined
    assert "- `locations.ban.rank_too_low` — unchanged: " in joined
    assert "⟨rank⟩" in joined


def test_the_tool_shows_placeholders_and_row_breaks_as_markers():
    assert show("als {rank} kannst du\nnoch") == "`als ⟨rank⟩ kannst du⏎noch`"
    assert show("{n:mid$} {name}") == "`⟨n:mid$⟩ ⟨name⟩`"
    assert show("") == "*(empty)*"
