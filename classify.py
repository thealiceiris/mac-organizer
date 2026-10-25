"""Smart-sort classifier: candidate folders, the prompt, and a local llama-server.

Privacy: the only network traffic is HTTP to 127.0.0.1, to a llama-server
process this module starts with ``--host 127.0.0.1`` and always stops.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

EXCLUDED_TOP = {"Inbox", "Keys"}
COURSES_DIR = "Academics"
MAX_EXAMPLES = 5
MAX_SAMPLE_CHARS = 12_000
MAX_REASON = 200
HOST = "127.0.0.1"
CONTEXT = 8192
HEALTH_TIMEOUT = 120.0
POLL_INTERVAL = 0.5
STOP_TIMEOUT = 10.0
REQUEST_TIMEOUT = 180.0
BINARY_FALLBACKS = ("/opt/homebrew/bin/llama-server", "/usr/local/bin/llama-server")


class ServerUnavailable(Exception):
    """The model or llama-server cannot be used for the rest of this run."""


@dataclass(frozen=True)
class Candidate:
    name: str
    path: Path
    examples: tuple[str, ...]


@dataclass(frozen=True)
class Verdict:
    folder: str | None
    confidence: float
    reason: str


# ---------------------------------------------------------------- candidates


def _visible_dirs(parent: Path) -> list[Path]:
    try:
        entries = list(parent.iterdir())
    except OSError:
        return []
    out = []
    for p in entries:
        if p.name.startswith("."):
            continue
        try:
            if p.is_dir():
                out.append(p)
        except OSError:
            continue
    return out


def _examples(folder: Path) -> tuple[str, ...]:
    """Up to MAX_EXAMPLES visible entry names (files first). Listing only, no reads."""
    try:
        entries = [p for p in folder.iterdir() if not p.name.startswith(".")]
    except OSError:
        return ()

    def key(p: Path):
        try:
            is_dir = p.is_dir()
        except OSError:
            is_dir = True
        return (is_dir, p.name.lower())

    return tuple(p.name for p in sorted(entries, key=key)[:MAX_EXAMPLES])


def candidates(documents_root: Path) -> list[Candidate]:
    root = Path(documents_root)
    found: dict[str, Path] = {}
    for top in _visible_dirs(root):
        if top.name in EXCLUDED_TOP:
            continue
        found[top.name] = top
        if top.name == COURSES_DIR:
            for course in _visible_dirs(top):
                found[f"{COURSES_DIR}/{course.name}"] = course
    return [Candidate(name, found[name], _examples(found[name])) for name in sorted(found)]


def candidates_hash(cands) -> str:
    """Hash of the candidate *names*: new or renamed folders invalidate cached verdicts,
    ordinary filing into an existing folder (which changes examples) does not."""
    blob = json.dumps(sorted(c.name for c in cands), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- prompt

SYSTEM_PROMPT = (
    "You file documents into folders on the user's Mac. "
    "Pick exactly one folder from the list below, or \"unsure\". "
    "\"unsure\" is better than a wrong guess: answer \"unsure\" unless the content clearly "
    "belongs in one folder. Each candidate folder is described by its name and up to five "
    "example filenames already in it. "
    "Reply with JSON: folder (a name from the list, or \"unsure\"), confidence (0 to 1), "
    "and reason (one short sentence)."
)


def _schema(cands) -> dict:
    return {
        "type": "object",
        "properties": {
            "folder": {"type": "string", "enum": [c.name for c in cands] + ["unsure"]},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "reason": {"type": "string", "maxLength": MAX_REASON},
        },
        "required": ["folder", "confidence", "reason"],
        "additionalProperties": False,
    }


def build_request(sample: str, cands, examples: list[dict]) -> dict:
    lines = ["Candidate folders (name: example filenames):"]
    for c in cands:
        hint = ", ".join(c.examples) if c.examples else "(empty)"
        lines.append(f"- {c.name}: {hint}")
    corrections = [e for e in examples or [] if e.get("file") and e.get("right") and e.get("wrong")]
    if corrections:
        lines += ["", "Earlier corrections by the user:"]
        lines += [f"- {e['file']} belongs in {e['right']}, not {e['wrong']}" for e in corrections]
    text = sample if len(sample) <= MAX_SAMPLE_CHARS else sample[:MAX_SAMPLE_CHARS] + "\n[truncated]"
    lines += ["", "Item to file:", text]
    return {
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": "\n".join(lines)},
        ],
        "temperature": 0,
        "max_tokens": 256,
        "response_format": {"type": "json_schema", "json_schema": {"schema": _schema(cands)}},
    }


def _unsure(reason: str, confidence: float = 0.0) -> Verdict:
    return Verdict(None, confidence, reason[:MAX_REASON])


def parse_verdict(body: dict, cands, threshold: float) -> Verdict:
    """Any deviation from the schema counts as unsure; never raises."""
    try:
        content = body["choices"][0]["message"]["content"]
        data = json.loads(content)
    except (KeyError, IndexError, TypeError, ValueError):
        return _unsure("malformed model response")
    if not isinstance(data, dict):
        return _unsure("malformed model response")
    folder, conf, reason = data.get("folder"), data.get("confidence"), data.get("reason", "")
    if isinstance(conf, bool) or not isinstance(conf, (int, float)) or not 0 <= conf <= 1:
        return _unsure("malformed confidence")
    conf = float(conf)
    reason = reason[:MAX_REASON] if isinstance(reason, str) else ""
    names = {c.name for c in cands}
    if not isinstance(folder, str) or folder not in names:
        return _unsure(reason or "unsure", conf)
    if conf < threshold:
        return Verdict(None, conf, reason)
    return Verdict(folder, conf, reason)


# ---------------------------------------------------------------- server

_NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _direct_urlopen(req, timeout=None):
    """urlopen that ignores http_proxy & co, so requests can only reach 127.0.0.1."""
    return _NO_PROXY_OPENER.open(req, timeout=timeout)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((HOST, 0))
        return s.getsockname()[1]


def _binary() -> str:
    found = shutil.which("llama-server")
    if found:
        return found
    for cand in BINARY_FALLBACKS:
        if Path(cand).exists():
            return cand
    return "llama-server"


class LlamaServer:
    def __init__(self, model_path, popen=subprocess.Popen, urlopen=_direct_urlopen, port=None):
        self.model_path = Path(model_path) if model_path is not None else None
        self._popen = popen
        self._urlopen = urlopen
        self._port = port
        self.port: int | None = None
        self.proc = None

    @property
    def base_url(self) -> str:
        return f"http://{HOST}:{self.port}"

    def __enter__(self) -> "LlamaServer":
        if self.model_path is None or not self.model_path.is_file():
            raise ServerUnavailable(f"model not found: {self.model_path}")
        self.port = self._port or _free_port()
        cmd = [_binary(), "-m", str(self.model_path), "--host", HOST, "--port", str(self.port),
               "-c", str(CONTEXT), "--reasoning", "off", "--offline", "--no-webui"]
        try:
            self.proc = self._popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)
        except OSError as exc:
            raise ServerUnavailable(f"cannot start llama-server: {exc}") from exc
        try:
            self._wait_healthy()
        except BaseException:
            self._stop()
            raise
        return self

    def __exit__(self, *exc) -> None:
        self._stop()

    def _wait_healthy(self) -> None:
        deadline = time.monotonic() + HEALTH_TIMEOUT
        req_url = self.base_url + "/health"
        while True:
            code = self.proc.poll()
            if code is not None:
                raise ServerUnavailable(f"llama-server exited with code {code}")
            try:
                with self._urlopen(urllib.request.Request(req_url), timeout=5) as resp:
                    if resp.status == 200:
                        return
            except (urllib.error.URLError, OSError):
                pass  # still loading (503) or not listening yet
            if time.monotonic() >= deadline:
                raise ServerUnavailable(f"llama-server not healthy after {HEALTH_TIMEOUT:g}s")
            time.sleep(POLL_INTERVAL)

    def _stop(self) -> None:
        proc, self.proc = self.proc, None
        if proc is None or proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=STOP_TIMEOUT)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(timeout=STOP_TIMEOUT)
            except subprocess.TimeoutExpired:
                pass

    def classify(self, sample: str, cands, examples, threshold: float) -> Verdict:
        if self.proc is None or self.proc.poll() is not None:
            raise ServerUnavailable("llama-server is not running")
        data = json.dumps(build_request(sample, cands, examples)).encode("utf-8")
        req = urllib.request.Request(self.base_url + "/v1/chat/completions", data=data,
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            with self._urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            return _unsure(f"llama-server HTTP {exc.code}")
        except (urllib.error.URLError, OSError) as exc:
            raise ServerUnavailable(f"llama-server request failed: {exc}") from exc
        try:
            body = json.loads(raw)
        except ValueError:
            return _unsure("malformed model response")
        return parse_verdict(body, cands, threshold)
