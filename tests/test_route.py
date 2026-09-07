from pathlib import Path

import route


def test_load_rules_parses_fields_and_order(tmp_path):
    toml = tmp_path / "r.toml"
    toml.write_text(
        """
[[rule]]
source = "~/Downloads"
ext = ["PNG", ".jpg"]
action = "move"
dest = "~/Media/Images"

[[rule]]
source = "~/Desktop"
glob = "Screen*"
action = "move_and_timestamp"
dest = "~/Media/Screenshots/%Y-%m"
min_age_seconds = 60
"""
    )
    rules = route.load_rules(toml)

    assert len(rules) == 2
    assert rules[0].source == Path("~/Downloads").expanduser()
    assert rules[0].ext == frozenset({"png", "jpg"})
    assert rules[0].action == "move"
    assert rules[0].dest == "~/Media/Images"
    assert rules[0].glob is None
    assert rules[0].min_age_seconds == 0
    assert rules[1].glob == "Screen*"
    assert rules[1].min_age_seconds == 60


def test_sources_are_unique_and_sorted(tmp_path):
    toml = tmp_path / "r.toml"
    toml.write_text(
        """
[[rule]]
source = "~/Inbox"
action = "skip"

[[rule]]
source = "~/Downloads"
ext = ["pdf"]
action = "move"
dest = "~/Documents/Inbox"

[[rule]]
source = "~/Downloads"
ext = ["png"]
action = "move"
dest = "~/Media/Images"
"""
    )
    assert route.sources(route.load_rules(toml)) == [
        Path("~/Downloads").expanduser(),
        Path("~/Inbox").expanduser(),
    ]


def test_default_rules_file_loads(tmp_path):
    src = Path(__file__).resolve().parent.parent / "rules.default.toml"
    patched = tmp_path / "rules.toml"
    patched.write_text(src.read_text().replace("@@SCREENSHOT_DIR@@", "~/Pictures/Screenshots"))

    rules = route.load_rules(patched)

    assert Path("~/Downloads").expanduser() in route.sources(rules)
    assert Path("~/Desktop").expanduser() in route.sources(rules)
    assert Path("~/Inbox").expanduser() in route.sources(rules)
    assert all(r.action in {"move", "move_and_timestamp", "skip"} for r in rules)
