"""On-device text sampling for the smart sort. Nothing here touches the network."""
from __future__ import annotations

import json
import re
import subprocess
import tarfile
import zipfile
from pathlib import Path

TIMEOUT = 60
MAX_MEMBERS = 200
OFFICE = {"docx", "pptx", "xlsx"}
CODE = {
    "py", "js", "ts", "tsx", "jsx", "c", "h", "cpp", "hpp", "cc", "java", "kt", "swift",
    "go", "rs", "rb", "php", "sh", "zsh", "bash", "sql", "html", "css", "scss", "json",
    "yaml", "yml", "toml", "xml", "md", "txt", "csv", "r", "m", "cs", "lua", "pl", "tex",
}
DOCS = {"pdf", "doc", "rtf", "ipynb"} | OFFICE | {"txt", "md"}
TAR_EXT = {"tar", "gz", "tgz"}
INSTALLER_SUFFIXES = (".pkg", ".mpkg", ".dmg", ".exe", ".msi")


def _truncate(text: str, max_words: int) -> str:
    matches = list(re.finditer(r"\S+", text))
    if len(matches) <= max_words:
        return text.strip()
    return text[: matches[max_words - 1].end()].strip() if max_words > 0 else ""


def _strip_xml(xml: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", xml)).strip()


def _run_text(runner, cmd: list[str]) -> str:
    return runner(cmd, capture_output=True, text=True, check=True, timeout=TIMEOUT).stdout or ""


def _office(path: Path) -> str:
    parts = []
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        wanted = [n for n in names if n == "word/document.xml" or n == "xl/sharedStrings.xml"
                  or re.fullmatch(r"ppt/slides/slide\d+\.xml", n)]
        wanted.sort(key=lambda n: [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", n)])
        for n in wanted:
            parts.append(_strip_xml(z.read(n).decode("utf-8", errors="replace")))
    return "\n".join(parts)


def _notebook(path: Path) -> str:
    nb = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    out = []
    for cell in nb.get("cells", []):
        if cell.get("cell_type") in ("markdown", "code"):
            src = cell.get("source", "")
            out.append("".join(src) if isinstance(src, list) else str(src))
    return "\n".join(out)


def _code(path: Path) -> str:
    lines = []
    with open(path, "rb") as f:
        for _, raw in zip(range(150), f):
            lines.append(raw.decode("utf-8", errors="replace").rstrip("\r\n"))
    return "\n".join(lines)


def _members(path: Path, ext: str) -> list[str]:
    if ext == "zip":
        with zipfile.ZipFile(path) as z:
            return z.namelist()[:MAX_MEMBERS]
    names = []
    with tarfile.open(path) as t:
        for m in t:
            names.append(m.name)
            if len(names) >= MAX_MEMBERS:
                break
    return names


def _dir(path: Path, runner) -> str:
    rels = []
    docs = []
    for p in sorted(path.rglob("*")):
        rel = p.relative_to(path)
        if any(part.startswith(".") for part in rel.parts):
            continue
        if len(rels) < MAX_MEMBERS:
            rels.append(str(rel) + ("/" if p.is_dir() else ""))
        if p.is_file() and len(docs) < 3 and p.suffix.lower().lstrip(".") in DOCS:
            docs.append(p)
        if len(rels) >= MAX_MEMBERS and len(docs) >= 3:
            break
    out = "\n".join(rels)
    for d in docs:
        out += "\n\n" + sample(d, runner=runner, max_words=300)
    return out


def _body(path: Path, runner) -> str:
    ext = path.suffix.lower().lstrip(".")
    if path.is_dir():
        return _dir(path, runner)
    if ext == "pdf":
        return _run_text(runner, ["pdftotext", "-l", "5", "-q", str(path), "-"])
    if ext in OFFICE:
        return _office(path)
    if ext in ("doc", "rtf"):
        return _run_text(runner, ["textutil", "-convert", "txt", "-stdout", str(path)])
    if ext == "ipynb":
        return _notebook(path)
    if ext in CODE:
        return _code(path)
    if ext in ("zip",) or ext in TAR_EXT:
        return "\n".join(_members(path, ext))
    return ""  # pages and anything unknown: name only


def sample(path: Path, runner=subprocess.run, max_words: int = 1500) -> str:
    path = Path(path)
    head = f"Name: {path.name}\n"
    try:
        body = _truncate(_body(path, runner), max_words)
    except Exception:  # noqa: BLE001 - sampling must never break routing
        return head
    return head + body if body else head


def archive_has_installer(path: Path) -> bool:
    path = Path(path)
    ext = path.suffix.lower().lstrip(".")
    try:
        names = _members(path, "zip" if ext == "zip" else "tar")
    except Exception:  # noqa: BLE001
        return False
    for n in names:
        low = n.lower().rstrip("/")
        if low.endswith(".app") or ".app/" in low or low.endswith(INSTALLER_SUFFIXES):
            return True
    return False
