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
