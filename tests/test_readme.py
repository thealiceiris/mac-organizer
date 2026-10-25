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
