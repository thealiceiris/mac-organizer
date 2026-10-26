"""Topic names for suggested folders: normalisation, near-duplicates, validity."""
from __future__ import annotations

import re
from pathlib import Path

import classify

MAX_NAME = 60


def normalise(text: str) -> str:
    """Lower case, drop digits and punctuation, collapse spaces, strip one trailing 's' per word."""
    text = re.sub(r"[\d_]+|[^\w\s]", " ", text.lower())
    return " ".join(_singular(w) for w in text.split())


def _singular(word: str) -> str:
    """Strip one plural ending: "taxes" -> "tax", "recipes" -> "recipe", "class" stays."""
    if word.endswith(("xes", "ches", "shes", "sses")):
        return word[:-2]
    if word.endswith("s") and not word.endswith("ss") and len(word) > 1:
        return word[:-1]
    return word


def near_duplicate(name: str, documents_root: Path, expand: tuple[str, ...]) -> str | None:
    """Display name of an existing folder whose normalised form equals that of ``name``."""
    key = normalise(name)
    if not key:
        return None
    top_level = classify._visible_dirs(Path(documents_root))
    for p in sorted(top_level, key=lambda p: p.name):
        if normalise(p.name) == key:
            return p.name
        if p.name in expand:
            for sub in sorted(classify._visible_dirs(p), key=lambda p: p.name):
                if normalise(sub.name) == key:
                    return f"{p.name}/{sub.name}"
    return None


def valid_folder_name(name: str, documents_root: Path) -> str | None:
    """An error message, or None if ``name`` can be created as a top-level folder."""
    if not name or not name.strip():
        return "The folder name is empty."
    if "/" in name or "\0" in name:
        return "The folder name cannot contain \"/\"."
    if name.startswith("."):
        return "The folder name cannot start with \".\"."
    if len(name) > MAX_NAME:
        return f"The folder name is longer than {MAX_NAME} characters."
    root = Path(documents_root)
    try:
        taken = (root / name).exists() or any(
            p.name.casefold() == name.casefold() for p in root.iterdir())
    except OSError:
        taken = False
    if taken:
        return f"A folder named \"{name}\" already exists."
    return None
