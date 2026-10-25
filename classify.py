"""Smart-sort classifier: candidate folders, the prompt, and a local llama-server.

Privacy: the only network traffic is HTTP to 127.0.0.1, to a llama-server
process this module starts with ``--host 127.0.0.1`` and always stops.
"""
from __future__ import annotations

import hashlib
import http.client
import json
import os
import shutil
import signal
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
PIDFILE = "llama-server.pid"
SERVER_LOG = "llama-server.log"
BINARY_FALLBACKS = ("/opt/homebrew/bin/llama-server", "/usr/local/bin/llama-server")


class ServerUnavailable(Exception):
    """The model or llama-server cannot be used for the rest of this run."""


class ItemRejected(Exception):
    """llama-server refused this one request (HTTP 4xx): unsure for this item only."""


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


def _pid_cmdline(pid: int) -> str | None:
    """Command line of a live process, or None if there is no such process."""
    try:
        out = subprocess.run(["ps", "-p", str(int(pid)), "-o", "command="],
                             capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    line = out.stdout.strip()
    return line if out.returncode == 0 and line else None


def _is_our_llama(pid: int, model_path, pid_cmdline) -> bool:
    """Our llama-server: the binary name *and* this model on its command line."""
    cmd = pid_cmdline(pid)
    return bool(cmd) and "llama-server" in cmd and str(model_path) in cmd


def reap_stale(state_dir, model_path, pid_cmdline=None, kill=None) -> None:
    """Stop a llama-server left behind by a SIGKILLed run (pidfile + model path match).

    Without a pidfile nothing runs at all. A pid that is gone, reused by
    another program or serving another model is never signalled.
    """
    pid_cmdline = pid_cmdline or _pid_cmdline
    kill = kill or os.kill
    pidfile = Path(state_dir) / PIDFILE
    try:
        pid = int(pidfile.read_text().strip())
    except FileNotFoundError:
        return
    except (OSError, ValueError):
        pid = None

    def signal_(sig):
        try:
            kill(pid, sig)
        except OSError:  # already gone, or not ours to signal
            pass

    if pid is not None and pid > 1 and pid != os.getpid() \
            and _is_our_llama(pid, model_path, pid_cmdline):
        signal_(signal.SIGTERM)
        deadline = time.monotonic() + STOP_TIMEOUT
        while _is_our_llama(pid, model_path, pid_cmdline) and time.monotonic() < deadline:
            time.sleep(POLL_INTERVAL)
        if _is_our_llama(pid, model_path, pid_cmdline):
            signal_(signal.SIGKILL)
    try:
        pidfile.unlink(missing_ok=True)
    except OSError:
        pass


class LlamaServer:
    """llama-server for one run. Started on enter, always stopped on exit.

    With ``state_dir``, the server's pid is kept in ``<state_dir>/llama-server.pid``
    while it runs. A pidfile left behind by a run that was SIGKILLed names an
    orphan: if that pid is still a ``llama-server`` it is terminated before a new
    server starts. ``pid_cmdline`` and ``kill`` are injectable so tests never
    touch real processes.
    """

    def __init__(self, model_path, popen=subprocess.Popen, urlopen=_direct_urlopen, port=None,
                 state_dir=None, pid_cmdline=_pid_cmdline, kill=os.kill):
        self.model_path = Path(model_path) if model_path is not None else None
        self._popen = popen
        self._urlopen = urlopen
        self._port = port
        self._pidfile = Path(state_dir) / PIDFILE if state_dir is not None else None
        self._pid_cmdline = pid_cmdline
        self._kill = kill
        self.port: int | None = None
        self.proc = None

    @property
    def base_url(self) -> str:
        return f"http://{HOST}:{self.port}"

    def __enter__(self) -> "LlamaServer":
        if self.model_path is None or not self.model_path.is_file():
            raise ServerUnavailable(f"model not found: {self.model_path}")
        self._reap_stale()
        self.port = self._port or _free_port()
        cmd = [_binary(), "-m", str(self.model_path), "--host", HOST, "--port", str(self.port),
               "-c", str(CONTEXT), "--reasoning", "off", "--offline", "--no-webui"]
        out, err, log = subprocess.DEVNULL, subprocess.DEVNULL, None
        if self._pidfile is not None:
            # the server's own output, for diagnosing a model that will not load
            try:
                self._pidfile.parent.mkdir(parents=True, exist_ok=True)
                log = open(self._pidfile.parent / SERVER_LOG, "wb")  # truncated per start
                out, err = log, subprocess.STDOUT
            except OSError:
                log = None
        try:
            self.proc = self._popen(cmd, stdin=subprocess.DEVNULL, stdout=out, stderr=err)
        except OSError as exc:
            raise ServerUnavailable(f"cannot start llama-server: {exc}") from exc
        finally:
            if log is not None:
                log.close()  # the child keeps its own copy of the descriptor
        try:
            self._write_pidfile()
            self._wait_healthy()
        except BaseException:
            self._stop()
            raise
        return self

    def __exit__(self, *exc) -> None:
        self._stop()

    # -- pidfile: the model is never left running, even after a SIGKILL

    def _write_pidfile(self) -> None:
        pid = getattr(self.proc, "pid", None)
        if self._pidfile is None or pid is None:
            return
        self._pidfile.parent.mkdir(parents=True, exist_ok=True)
        self._pidfile.write_text(f"{pid}\n")

    def _remove_pidfile(self) -> None:
        if self._pidfile is not None:
            try:
                self._pidfile.unlink(missing_ok=True)
            except OSError:
                pass

    def _reap_stale(self) -> None:
        if self._pidfile is not None:
            reap_stale(self._pidfile.parent, self.model_path, self._pid_cmdline, self._kill)

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
            except http.client.HTTPException as exc:  # garbled reply: not a llama-server we can use
                raise ServerUnavailable(f"llama-server health check failed: {exc!r}") from exc
            if time.monotonic() >= deadline:
                raise ServerUnavailable(f"llama-server not healthy after {HEALTH_TIMEOUT:g}s")
            time.sleep(POLL_INTERVAL)

    def _stop(self) -> None:
        proc, self.proc = self.proc, None
        if proc is None:
            return
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=STOP_TIMEOUT)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=STOP_TIMEOUT)
                except subprocess.TimeoutExpired:
                    return  # still alive: keep the pidfile so the next run reaps it
        self._remove_pidfile()

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
            if 400 <= exc.code < 500:
                # this request was refused (too long, bad input): this item only
                raise ItemRejected(f"llama-server HTTP {exc.code}") from exc
            # a server-side failure, not the model's judgement: never a cacheable "unsure"
            raise ServerUnavailable(f"llama-server HTTP {exc.code}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise ServerUnavailable(f"llama-server request failed: {exc}") from exc
        except http.client.HTTPException as exc:  # IncompleteRead, BadStatusLine, ...
            raise ServerUnavailable(f"llama-server response broken: {exc!r}") from exc
        try:
            body = json.loads(raw)
        except ValueError:
            return _unsure("malformed model response")
        return parse_verdict(body, cands, threshold)
