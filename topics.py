"""Topic names for suggested folders: normalisation, near-duplicates, validity."""
from __future__ import annotations

import re
import sys
from pathlib import Path

import classify
import memory

MAX_NAME = 60
MISC_TOPICS = "misc-topics.jsonl"


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


def record(state: Path, item: Path, topic: str, now: float) -> None:
    """Remember the model's topic for an item that now sits in Misc."""
    key = normalise(topic)
    if not key:
        return
    try:
        st = Path(item).stat()
        memory._append(Path(state) / MISC_TOPICS, {
            "path": str(item), "size": st.st_size, "mtime": st.st_mtime,
            "topic": topic, "key": key, "at": now})
    except OSError as exc:  # a topic hint must never break routing
        print(f"ERROR  recording topic for {item}: {exc!r}", file=sys.stderr)


def clusters(state: Path, misc_dir: Path) -> dict[str, tuple[str, list[Path]]]:
    """Topic key -> (most common topic text, items still directly in ``misc_dir``).

    Rows for items that are gone are pruned from the file.
    """
    file = Path(state) / MISC_TOPICS
    with memory._locked(file):
        rows = memory._read(file)
        misc = Path(misc_dir)
        kept, seen = [], set()
        for r in rows:
            p, key, topic = r.get("path"), r.get("key"), r.get("topic")
            if not (isinstance(p, str) and isinstance(key, str) and key and isinstance(topic, str)):
                continue
            path = Path(p)
            if path.parent != misc or not path.exists():
                continue
            kept.append(r)
        if file.exists() and len(kept) != len(rows):
            memory._rewrite(file, kept)
    by_key: dict[str, dict[str, list]] = {}
    for r in kept:
        by_key.setdefault(r["key"], {}).setdefault(r["topic"], []).append(Path(r["path"]))
    out = {}
    for key, texts in by_key.items():
        name = max(texts, key=lambda t: (len(texts[t]), -list(texts).index(t)))
        items: list[Path] = []
        for paths in texts.values():
            for p in paths:
                if p not in items:
                    items.append(p)
        out[key] = (name, items)
    return out
