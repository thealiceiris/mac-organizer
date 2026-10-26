"""The review dialog and plain notifications, through osascript.

Only ever shown because the user clicked a suggestion (or ran the CLI):
nothing here pops up on its own.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

TITLE = "mac-organizer"
CREATE, NOT_NOW, NEVER = "Create & move", "Not now", "Never suggest this"
BUTTONS = {CREATE: "create", NOT_NOW: "not_now", NEVER: "never"}
MAX_LISTED = 10
DIALOG_TIMEOUT = 3600  # an unanswered dialog counts as cancel
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def quote(text: str) -> str:
    """An AppleScript string literal: backslashes and quotes escaped, control characters as spaces."""
    text = _CONTROL.sub(" ", str(text)).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"'


def _lines(*lines: str) -> str:
    return " & return & ".join(quote(line) for line in lines)


def _run(script: str, runner, timeout) -> str | None:
    """osascript's stdout, or None when it failed or was dismissed (Esc)."""
    try:
        result = runner(["osascript", "-e", script], capture_output=True, text=True,
                        check=False, timeout=timeout)
    except Exception:  # noqa: BLE001 - a dialog that cannot be shown is a cancel
        return None
    if getattr(result, "returncode", 1):
        return None
    out = getattr(result, "stdout", "") or ""
    return out.decode() if isinstance(out, bytes) else out


def _parse(out: str) -> tuple[str | None, str]:
    out = out.rstrip("\r\n")
    prefix, sep = "button returned:", ", text returned:"
    if not out.startswith(prefix):
        return None, ""
    rest = out[len(prefix):]
    button, found, text = rest.partition(sep)
    return button, text if found else ""


def review(s, runner=subprocess.run, error: str | None = None,
           name: str | None = None) -> tuple[str, str]:
    """Ask about one suggestion: ``(button, name)``, button one of create/not_now/never/cancel.

    Re-shown after a name that could not be used: ``error`` is the first line
    and ``name`` (what the user typed) is prefilled instead of the suggestion.
    """
    names = [Path(p).name for p in s.items]
    listed = names[:MAX_LISTED]
    if len(names) > MAX_LISTED:
        listed.append(f"and {len(names) - MAX_LISTED} more")
    lead = [error, ""] if error else []
    body = _lines(*lead, f'Create a "{s.name}" folder in Documents?', "", *listed)
    buttons = ", ".join(quote(b) for b in (NOT_NOW, NEVER, CREATE))
    answer = s.name if name is None else name
    script = (f"display dialog {body} with title {quote(TITLE)} default answer {quote(answer)} "
              f"buttons {{{buttons}}} default button {quote(CREATE)}")
    out = _run(script, runner, DIALOG_TIMEOUT)
    if out is None:
        return "cancel", ""
    button, name = _parse(out)
    if button not in BUTTONS:
        return "cancel", ""
    return BUTTONS[button], name


def confirm_similar(existing: str, runner=subprocess.run) -> bool:
    """``A similar folder "<Existing>" exists. Move the files there instead?``"""
    use = f'Use "{existing}"'
    question = f'A similar folder "{existing}" exists. Move the files there instead?'
    script = (f"display dialog {quote(question)} "
              f"with title {quote(TITLE)} buttons {{{quote('Cancel')}, {quote(use)}}} "
              f"default button {quote(use)} cancel button {quote('Cancel')}")
    out = _run(script, runner, DIALOG_TIMEOUT)
    if out is None:
        return False
    return _parse(out)[0] == _CONTROL.sub(" ", use)


def notify(message: str, runner=subprocess.run) -> None:
    """A plain notification (no click action). Never raises."""
    script = f"display notification {quote(message)} with title {quote(TITLE)}"
    try:
        runner(["osascript", "-e", script], capture_output=True, text=True, check=False,
               timeout=10)
    except Exception:  # noqa: BLE001 - a notification never fails anything
        pass
