"""Task 5: the review dialog (fake osascript runner; nothing is ever shown)."""

import subprocess

import dialog
from suggest import Suggestion


class Runner:
    def __init__(self, stdout="", returncode=0):
        self.stdout, self.returncode, self.calls = stdout, returncode, []

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        return subprocess.CompletedProcess(cmd, self.returncode, self.stdout, "")

    @property
    def script(self):
        cmd = self.calls[-1]
        assert cmd[0] == "osascript" and cmd[1] == "-e"
        return cmd[2]


def _s(name="Taxes", n=4):
    return Suggestion("tax", name, [f"/h/Documents/Misc/tax-{i}.pdf" for i in range(n)], 1.0)


def test_dialog_parses_buttons_and_name():
    r = Runner("button returned:Create & move, text returned:Taxes 2024\n")
    assert dialog.review(_s(), runner=r) == ("create", "Taxes 2024")
    script = r.script
    assert 'Create a \\"Taxes\\" folder in Documents?' in script
    assert 'default answer "Taxes"' in script
    assert 'buttons {"Not now", "Never suggest this", "Create & move"}' in script
    assert 'default button "Create & move"' in script
    assert "tax-0.pdf" in script and "tax-3.pdf" in script
    r = Runner("button returned:Not now, text returned:Taxes\n")
    assert dialog.review(_s(), runner=r)[0] == "not_now"
    r = Runner("button returned:Never suggest this, text returned:Taxes\n")
    assert dialog.review(_s(), runner=r)[0] == "never"
    # a name containing the separator text is kept whole
    r = Runner("button returned:Create & move, text returned:A, B, text returned:C\n")
    assert dialog.review(_s(), runner=r) == ("create", "A, B, text returned:C")


def test_dialog_lists_at_most_ten_items():
    r = Runner("button returned:Not now, text returned:Taxes\n")
    dialog.review(_s(n=13), runner=r)
    assert "tax-9.pdf" in r.script and "tax-10.pdf" not in r.script
    assert "and 3 more" in r.script


def test_dialog_escapes_quotes_and_newlines():
    s = Suggestion("x", 'Bad "name" \\ here\nnext', ['/m/evil".pdf\nline2', "/m/back\\slash.txt"], 1.0)
    r = Runner("button returned:Not now, text returned:x\n")
    dialog.review(s, runner=r)
    script = r.script
    assert "\n" not in script and "\r" not in script
    assert 'default answer "Bad \\"name\\" \\\\ here next"' in script
    assert 'evil\\".pdf line2' in script
    assert "back\\\\slash.txt" in script
    r = Runner('button returned:Use "Ta\\"x", text returned:\n')
    dialog.confirm_similar('Ta"x', runner=r)
    assert 'A similar folder \\"Ta\\"x\\" exists. Move the files there instead?' in r.script
    assert "\n" not in r.script


def test_dialog_esc_is_cancel():
    assert dialog.review(_s(), runner=Runner("", returncode=1)) == ("cancel", "")
    assert dialog.confirm_similar("Tax", runner=Runner("", returncode=1)) is False

    def boom(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 1)

    assert dialog.review(_s(), runner=boom) == ("cancel", "")
    assert dialog.review(_s(), runner=Runner("garbage\n")) == ("cancel", "")


def test_confirm_similar_buttons():
    r = Runner('button returned:Use "Tax"\n')
    assert dialog.confirm_similar("Tax", runner=r) is True
    assert 'buttons {"Cancel", "Use \\"Tax\\""}' in r.script
    assert dialog.confirm_similar("Tax", runner=Runner("button returned:Cancel\n")) is False


def test_notify_is_plain_display_notification():
    r = Runner()
    dialog.notify('Created "Taxes" and moved 4 files', runner=r)
    assert r.script == ('display notification "Created \\"Taxes\\" and moved 4 files" '
                        'with title "mac-organizer"')

    def boom(cmd, **kw):
        raise OSError("no osascript")

    dialog.notify("x", runner=boom)  # never raises
