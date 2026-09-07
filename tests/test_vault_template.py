import json
from pathlib import Path

VAULT = Path(__file__).resolve().parent.parent / "vault-template"


def test_para_folders_exist():
    for name in ["00-Inbox", "10-Projects", "20-Areas", "30-Resources", "40-Archive",
                 "_templates", "_attachments"]:
        assert (VAULT / name).is_dir(), name


def test_obsidian_config_is_valid_json_and_wired():
    app = json.loads((VAULT / ".obsidian/app.json").read_text())
    assert app["attachmentFolderPath"] == "_attachments"
    assert app["newFileFolderPath"] == "00-Inbox"

    templates = json.loads((VAULT / ".obsidian/templates.json").read_text())
    assert templates["folder"] == "_templates"

    daily = json.loads((VAULT / ".obsidian/daily-notes.json").read_text())
    assert daily["folder"].startswith("20-Areas")

    core = json.loads((VAULT / ".obsidian/core-plugins.json").read_text())
    # accept object form {name: bool} or list form [name, ...]
    enabled = set(core) if isinstance(core, list) else {k for k, v in core.items() if v}
    assert {"templates", "daily-notes"} <= enabled


def test_templates_have_expected_headings():
    daily = (VAULT / "_templates/daily.md").read_text()
    assert "## Focus" in daily and "## Log" in daily

    project = (VAULT / "_templates/project.md").read_text()
    assert "## Next actions" in project

    meeting = (VAULT / "_templates/meeting.md").read_text()
    assert "## Action items" in meeting and "## Decisions" in meeting
