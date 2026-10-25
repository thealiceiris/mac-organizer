import http.server
import io
import json
import subprocess
import threading
import urllib.error
from pathlib import Path

import pytest

import classify


# ---------------------------------------------------------------- fixtures


def _docs(tmp_path: Path) -> Path:
    root = tmp_path / "Documents"
    for d in ["Career", "Inbox", "Keys", ".hidden", "Development/project-a",
              "Academics/Natural Language Processing/week1",
              "Academics/Cyber Security Essentials", "Academics/.secret"]:
        (root / d).mkdir(parents=True)
    for f in ["Career/cv.pdf", "Career/.DS_Store", "Inbox/x.pdf", "loose.pdf",
              "Academics/transcript.pdf",
              "Academics/Natural Language Processing/ngrams.pdf"]:
        (root / f).write_text("x")
    for i in range(8):
        (root / "Academics/Cyber Security Essentials" / f"lab{i}.pdf").write_text("x")
    return root


def _cands(tmp_path):
    return classify.candidates(_docs(tmp_path))


class FakeProc:
    def __init__(self, cmd, exit_code=None, ignore_terminate=False):
        self.cmd = cmd
        self.returncode = exit_code
        self.ignore_terminate = ignore_terminate
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        if not self.ignore_terminate:
            self.returncode = -15

    def kill(self):
        self.killed = True
        self.returncode = -9

    def wait(self, timeout=None):
        if self.returncode is None:
            raise subprocess.TimeoutExpired(self.cmd, timeout)
        return self.returncode


class FakePopen:
    def __init__(self, **proc_kw):
        self.proc_kw = proc_kw
        self.procs = []

    def __call__(self, cmd, **kw):
        proc = FakeProc(cmd, **self.proc_kw)
        self.procs.append(proc)
        return proc


class FakeResponse(io.BytesIO):
    def __init__(self, body: bytes, status=200):
        super().__init__(body)
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def _chat_body(content) -> dict:
    if not isinstance(content, str):
        content = json.dumps(content)
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}


class FakeUrlopen:
    """Answers /health with `health` (status code or exception) and chat with `chat`."""

    def __init__(self, health=200, chat=None):
        self.health = health
        self.chat = chat
        self.urls = []
        self.requests = []

    def __call__(self, req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else req
        self.urls.append(url)
        self.requests.append(req)
        if url.endswith("/health"):
            if isinstance(self.health, BaseException):
                raise self.health
            if self.health != 200:
                raise urllib.error.HTTPError(url, self.health, "loading", {}, None)
            return FakeResponse(b'{"status":"ok"}')
        if isinstance(self.chat, BaseException):
            raise self.chat
        return FakeResponse(json.dumps(self.chat).encode())


@pytest.fixture
def model(tmp_path):
    p = tmp_path / "model.gguf"
    p.write_bytes(b"GGUF")
    return p


@pytest.fixture
def fast(monkeypatch):
    monkeypatch.setattr(classify, "HEALTH_TIMEOUT", 0.05)
    monkeypatch.setattr(classify, "POLL_INTERVAL", 0.001)
    monkeypatch.setattr(classify, "STOP_TIMEOUT", 0.01)


# ---------------------------------------------------------------- candidates


def test_candidates_top_level_plus_academics_courses_excludes_inbox_keys_hidden(tmp_path):
    root = _docs(tmp_path)
    cands = classify.candidates(root)
    names = [c.name for c in cands]
    assert names == [
        "Academics",
        "Academics/Cyber Security Essentials",
        "Academics/Natural Language Processing",
        "Career",
        "Development",
    ]
    by = {c.name: c for c in cands}
    assert by["Career"].path == root / "Career"
    assert by["Career"].examples == ("cv.pdf",)
    assert by["Academics/Cyber Security Essentials"].path == root / "Academics/Cyber Security Essentials"
    assert len(by["Academics/Cyber Security Essentials"].examples) == 5
    assert by["Academics/Natural Language Processing"].examples[0] == "ngrams.pdf"
    assert all(not e.startswith(".") for c in cands for e in c.examples)


def test_candidates_missing_root_is_empty(tmp_path):
    assert classify.candidates(tmp_path / "nope") == []


def test_candidates_hash_depends_on_names_not_examples(tmp_path):
    root = _docs(tmp_path)
    h1 = classify.candidates_hash(classify.candidates(root))
    (root / "Career/new.pdf").write_text("x")
    assert classify.candidates_hash(classify.candidates(root)) == h1
    (root / "Music").mkdir()
    assert classify.candidates_hash(classify.candidates(root)) != h1


# ---------------------------------------------------------------- request


def test_request_schema_enum_is_candidates_plus_unsure(tmp_path):
    cands = _cands(tmp_path)
    req = classify.build_request("Name: x.pdf\nhello", cands, [])
    assert req["temperature"] == 0
    rf = req["response_format"]
    assert rf["type"] == "json_schema"
    schema = rf["json_schema"]["schema"]
    assert schema["properties"]["folder"]["enum"] == [c.name for c in cands] + ["unsure"]
    conf = schema["properties"]["confidence"]
    assert conf["type"] == "number" and conf["minimum"] == 0 and conf["maximum"] == 1
    reason = schema["properties"]["reason"]
    assert reason["type"] == "string" and reason["maxLength"] == 200
    assert set(schema["required"]) == {"folder", "confidence", "reason"}


def test_request_prompt_lists_candidates_examples_and_corrections(tmp_path):
    cands = _cands(tmp_path)
    examples = [{"file": "hw3.pdf", "summary": "bigram counts",
                 "wrong": "Career", "right": "Academics/Natural Language Processing"}]
    req = classify.build_request("Name: x.pdf\nSAMPLE-TEXT", cands, examples)
    text = "\n".join(m["content"] for m in req["messages"])
    assert "unsure" in text and "wrong guess" in text
    assert "Academics/Cyber Security Essentials" in text and "lab0.pdf" in text
    assert "hw3.pdf belongs in Academics/Natural Language Processing, not Career" in text
    assert "SAMPLE-TEXT" in text


def test_request_truncates_huge_sample(tmp_path):
    req = classify.build_request("w " * 100_000, _cands(tmp_path), [])
    assert sum(len(m["content"]) for m in req["messages"]) < classify.MAX_SAMPLE_CHARS + 5000


def test_request_never_targets_non_localhost(tmp_path, model, fast):
    cands = _cands(tmp_path)
    fake = FakeUrlopen(chat=_chat_body({"folder": "Career", "confidence": 0.9, "reason": "cv"}))
    popen = FakePopen()
    with classify.LlamaServer(model, popen=popen, urlopen=fake) as srv:
        srv.classify("Name: cv.pdf", cands, [], 0.6)
    assert fake.urls and all(u.startswith("http://127.0.0.1:") for u in fake.urls)
    assert any(u.endswith("/v1/chat/completions") for u in fake.urls)
    cmd = popen.procs[0].cmd
    assert cmd[cmd.index("--host") + 1] == "127.0.0.1"
    assert cmd[cmd.index("-m") + 1] == str(model)
    assert cmd[cmd.index("-c") + 1] == "8192"
    assert int(cmd[cmd.index("--port") + 1]) > 0
    # Thinking models would spend the token budget on reasoning and return no JSON.
    assert cmd[cmd.index("--reasoning") + 1] == "off"
    assert "--offline" in cmd


def test_default_urlopen_ignores_proxy_environment(monkeypatch):
    """A proxy variable must never route the sample off the Mac."""
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        for var in ("http_proxy", "HTTP_PROXY", "all_proxy", "ALL_PROXY"):
            monkeypatch.setenv(var, "http://192.0.2.1:9")  # TEST-NET, unroutable
        for var in ("no_proxy", "NO_PROXY"):
            monkeypatch.delenv(var, raising=False)
        url = f"http://127.0.0.1:{srv.server_address[1]}/health"
        with classify._direct_urlopen(url, timeout=5) as resp:
            assert resp.status == 200
    finally:
        srv.shutdown()
        srv.server_close()


# ---------------------------------------------------------------- parse


def test_parse_verdict_below_threshold_is_unsure(tmp_path):
    cands = _cands(tmp_path)
    low = classify.parse_verdict(_chat_body({"folder": "Career", "confidence": 0.59, "reason": "r"}), cands, 0.6)
    assert low.folder is None
    ok = classify.parse_verdict(_chat_body({"folder": "Career", "confidence": 0.6, "reason": "r"}), cands, 0.6)
    assert ok == classify.Verdict("Career", 0.6, "r")


def test_parse_verdict_unknown_folder_is_unsure(tmp_path):
    cands = _cands(tmp_path)
    v = classify.parse_verdict(_chat_body({"folder": "Taxes", "confidence": 0.99, "reason": "r"}), cands, 0.6)
    assert v.folder is None
    v = classify.parse_verdict(_chat_body({"folder": "unsure", "confidence": 0.99, "reason": "r"}), cands, 0.6)
    assert v.folder is None


@pytest.mark.parametrize("body", [
    _chat_body("{not json"),
    _chat_body("[1, 2]"),
    _chat_body({"folder": "Career"}),
    _chat_body({"folder": "Career", "confidence": "high", "reason": "r"}),
    _chat_body({"folder": "Career", "confidence": True, "reason": "r"}),
    _chat_body({"folder": "Career", "confidence": 1.5, "reason": "r"}),
    _chat_body({"folder": ["Career"], "confidence": 0.9, "reason": "r"}),
    {"choices": []},
    {},
    "garbage",
])
def test_parse_verdict_malformed_json_is_unsure(tmp_path, body):
    v = classify.parse_verdict(body, _cands(tmp_path), 0.6)
    assert v.folder is None
    assert isinstance(v.reason, str)


def test_parse_verdict_truncates_long_reason(tmp_path):
    v = classify.parse_verdict(_chat_body({"folder": "Career", "confidence": 0.9, "reason": "x" * 500}),
                               _cands(tmp_path), 0.6)
    assert v.folder == "Career" and len(v.reason) == 200


# ---------------------------------------------------------------- server


def test_classify_http_error_is_unsure(tmp_path, model, fast):
    err = urllib.error.HTTPError("http://127.0.0.1:1/v1/chat/completions", 500, "boom", {}, io.BytesIO(b""))
    fake = FakeUrlopen(chat=err)
    with classify.LlamaServer(model, popen=FakePopen(), urlopen=fake) as srv:
        v = srv.classify("Name: x.pdf", _cands(tmp_path), [], 0.6)
    assert v.folder is None


def test_classify_returns_verdict(tmp_path, model, fast):
    fake = FakeUrlopen(chat=_chat_body({"folder": "Academics/Cyber Security Essentials",
                                        "confidence": 0.8, "reason": "OpenPGP lab"}))
    with classify.LlamaServer(model, popen=FakePopen(), urlopen=fake) as srv:
        v = srv.classify("Name: lab.pdf", _cands(tmp_path), [], 0.6)
    assert v == classify.Verdict("Academics/Cyber Security Essentials", 0.8, "OpenPGP lab")
    sent = json.loads(fake.requests[-1].data)
    assert sent["response_format"]["type"] == "json_schema"


def test_server_missing_model_raises_unavailable(tmp_path, fast):
    popen = FakePopen()
    with pytest.raises(classify.ServerUnavailable):
        with classify.LlamaServer(tmp_path / "gone.gguf", popen=popen, urlopen=FakeUrlopen()):
            pass
    with pytest.raises(classify.ServerUnavailable):
        with classify.LlamaServer(None, popen=popen, urlopen=FakeUrlopen()):
            pass
    assert popen.procs == []


def test_server_binary_missing_raises_unavailable(model, fast):
    def popen(cmd, **kw):
        raise FileNotFoundError(cmd[0])
    with pytest.raises(classify.ServerUnavailable):
        with classify.LlamaServer(model, popen=popen, urlopen=FakeUrlopen()):
            pass


def test_server_health_timeout_raises_unavailable_and_kills_process(model, fast):
    popen = FakePopen(ignore_terminate=True)
    with pytest.raises(classify.ServerUnavailable):
        with classify.LlamaServer(model, popen=popen, urlopen=FakeUrlopen(health=503)):
            pass
    proc = popen.procs[0]
    assert proc.terminated and proc.killed


def test_server_health_connection_refused_then_timeout(model, fast):
    popen = FakePopen()
    with pytest.raises(classify.ServerUnavailable):
        with classify.LlamaServer(model, popen=popen,
                                  urlopen=FakeUrlopen(health=urllib.error.URLError("refused"))):
            pass
    assert popen.procs[0].terminated


def test_server_process_exits_during_startup_raises_unavailable(model, fast):
    popen = FakePopen(exit_code=1)
    with pytest.raises(classify.ServerUnavailable):
        with classify.LlamaServer(model, popen=popen, urlopen=FakeUrlopen(health=503)):
            pass


def test_server_exit_terminates_process(model, fast):
    popen = FakePopen()
    with classify.LlamaServer(model, popen=popen, urlopen=FakeUrlopen()):
        proc = popen.procs[0]
        assert not proc.terminated
    assert proc.terminated and not proc.killed


def test_server_exit_kills_process_that_ignores_terminate(model, fast):
    popen = FakePopen(ignore_terminate=True)
    with classify.LlamaServer(model, popen=popen, urlopen=FakeUrlopen()):
        pass
    assert popen.procs[0].terminated and popen.procs[0].killed


def test_server_stops_process_when_body_raises(model, fast):
    popen = FakePopen()
    with pytest.raises(RuntimeError):
        with classify.LlamaServer(model, popen=popen, urlopen=FakeUrlopen()):
            raise RuntimeError("boom")
    assert popen.procs[0].terminated


def test_server_uses_given_port(model, fast):
    fake = FakeUrlopen()
    with classify.LlamaServer(model, popen=FakePopen(), urlopen=fake, port=45678):
        pass
    assert fake.urls[0] == "http://127.0.0.1:45678/health"


def test_classify_after_process_died_raises_unavailable(tmp_path, model, fast):
    popen = FakePopen()
    fake = FakeUrlopen(chat=_chat_body({"folder": "Career", "confidence": 0.9, "reason": "r"}))
    with classify.LlamaServer(model, popen=popen, urlopen=fake) as srv:
        popen.procs[0].returncode = -11
        with pytest.raises(classify.ServerUnavailable):
            srv.classify("Name: x.pdf", _cands(tmp_path), [], 0.6)


def test_classify_connection_failure_raises_unavailable(tmp_path, model, fast):
    fake = FakeUrlopen(chat=ConnectionResetError("reset"))
    with classify.LlamaServer(model, popen=FakePopen(), urlopen=fake) as srv:
        with pytest.raises(classify.ServerUnavailable):
            srv.classify("Name: x.pdf", _cands(tmp_path), [], 0.6)


def test_classify_non_json_response_is_unsure(tmp_path, model, fast):
    class Fake(FakeUrlopen):
        def __call__(self, req, timeout=None):
            url = req.full_url
            if url.endswith("/health"):
                return FakeResponse(b"ok")
            return FakeResponse(b"<html>nope</html>")
    with classify.LlamaServer(model, popen=FakePopen(), urlopen=Fake()) as srv:
        assert srv.classify("Name: x.pdf", _cands(tmp_path), [], 0.6).folder is None
