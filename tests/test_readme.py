from pathlib import Path

README = Path(__file__).resolve().parent.parent / "README.md"


def test_readme_covers_key_topics():
    text = README.read_text().lower()
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
    text = README.read_text()
    for topic in ["rules.local.toml", "--backlog", "Documents/Keys", "eject",
                  "Changing the model", "terminal-notifier"]:
        assert topic in text, topic
    assert "content analysis" not in text.lower()


def test_no_personal_paths_in_published_files():
    root = README.parent
    files = [README, root / "rules.default.toml", root / "rules.local.default.toml"]
    for f in files:
        text = f.read_text()
        assert "/Users/" not in text, f.name
        assert "Orb" not in text and "gemma" not in text.lower(), f.name


def _section(text, heading):
    start = text.index(f"## {heading}")
    end = text.find("\n## ", start + 1)
    return text[start:end if end != -1 else None]


def test_readme_final_review_accuracy():
    text = README.read_text()
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
