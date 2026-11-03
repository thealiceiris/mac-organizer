from pathlib import Path

README = Path(__file__).resolve().parent.parent / "README.md"
ADVANCED = README.parent / "ADVANCED.md"


def test_readme_covers_key_topics():
    text = ADVANCED.read_text().lower()
    for topic in [
        "./install.sh",
        "./uninstall.sh",
        "rules.toml",
        "~/.local/state/mac-organizer",
        "screenshot",
        "python",
    ]:
        assert topic.lower() in text, topic


def test_readme_documents_v2():
    text = ADVANCED.read_text()
    for topic in ["rules.local.toml", "--backlog", "Documents/Keys", "eject",
                  "Changing the model", "terminal-notifier"]:
        assert topic in text, topic
    assert "content analysis" not in text.lower()


def test_no_personal_paths_in_published_files():
    root = README.parent
    files = [README, ADVANCED, root / "rules.default.toml", root / "rules.local.default.toml"]
    files += sorted((root / "docs" / "adr").glob("*.md"))
    for f in files:
        text = f.read_text()
        assert "/Users/" not in text, f.name
        assert "Orb" not in text and "gemma" not in text.lower(), f.name


def _section(text, heading):
    start = text.index(f"## {heading}")
    end = text.find("\n## ", start + 1)
    return text[start:end if end != -1 else None]


def test_readme_final_review_accuracy():
    text = ADVANCED.read_text()
    assert "installer or code" not in text  # archives are inspected for installers only
    assert "placements are shown" not in text.lower()  # only corrections reach the model
    rules = _section(text, "Changing the rules")
    for word in ["`trash`", "`smart`", "`kind`", "`name_regex`", "`has_installer`"]:
        assert word in rules, word
    upgrade = _section(text, "Upgrading from v1")
    steps = ["./uninstall.sh", "setup.py", "model_path", "--backlog", "--apply", "./install.sh"]
    positions = [upgrade.index(s) for s in steps]
    assert positions == sorted(positions), "upgrade steps out of order"
    notes = _section(text, "Notifications")
    assert "24 hours" in notes and "once the model has started" in notes


def test_live_model_test_takes_the_model_from_the_environment():
    live = (README.parent / "tests" / "test_classify_live.py").read_text()
    assert "MAC_ORGANIZER_MODEL" in live
    assert "Orb" not in live and "gemma" not in live.lower()


def test_readme_documents_suggestions_and_expand():
    text = ADVANCED.read_text()
    section = _section(text, "Setting up your folders")
    for needle in [
        "starter", "python3 setup.py", "python3 route.py --backlog ~/Downloads ~/Documents",
        "NEWFOLDER", "#", "folder name", "~/Documents/Misc", "Open Folder Suggestions to review", "--review-pending",
        "--suggestions", "--accept-suggestion", "--decline-suggestion",
        "terminal-notifier", "expand", "rules.local.toml", "[y/N]",
    ]:
        assert needle in section, needle
    assert "never creates folders" not in text


def test_readme_setup_py_never_starts_the_sorter():
    """C1: plain setup.py writes no agents; the README must not say otherwise."""
    text = ADVANCED.read_text()
    assert "writes the agents but does not load them" not in text
    assert "would load at your next login" not in text
    upgrade = _section(text, "Upgrading from v1")
    assert "writes no `launchd` agents" in upgrade
    messy = _section(README.read_text(), "Already have a messy Mac?")
    assert "never start the background sorter" in messy


def test_plain_readme_is_for_everyone():
    text = README.read_text()
    for section in ["## What you need", "## Install", "## Every day", "## Folder suggestions",
                    "## Already have a messy Mac?", "## Turn it off", "## Privacy"]:
        assert section in text, section
    assert "ADVANCED.md" in text
    assert "Folder Suggestions" in text and "Open Folder Suggestions to review" in text
    assert "python3 route.py --backlog ~/Downloads ~/Documents" in text
    # no jargon in the plain guide
    for jargon in ["launchd", "--review-suggestion", "name_regex", "has_installer", "SmartRouter"]:
        assert jargon not in text, jargon
    assert len(text.splitlines()) < 260  # a step-by-step guide, not a reference


ONE_LINER = ('/bin/bash -c "$(curl -fsSL '
             'https://raw.githubusercontent.com/thealiceiris/mac-organizer/main/get.sh)"')


def test_docs_show_the_one_line_install():
    for doc in (README, ADVANCED):
        text = doc.read_text()
        assert ONE_LINER in text, doc.name
        # piping into bash would skip install.sh's question
        blocks = text.split("```")[1::2]
        assert not [b for b in blocks if "| bash" in b], doc.name
    assert ONE_LINER in (README.parent / "get.sh").read_text()
