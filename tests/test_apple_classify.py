"""Apple backend tests; no Apple hardware or real document reads needed."""
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import classify
import route
import setup


def completed(code=0, stdout="", stderr=""):
    return SimpleNamespace(returncode=code, stdout=stdout, stderr=stderr)


def test_apple_bridge_uses_stdin_and_validates_verdict():
    calls = []

    def runner(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if "--check" in cmd:
            return completed()
        body = json.loads(kwargs["input"])
        assert set(body) == {"messages"}
        assert "Earlier corrections" in body["messages"][1]["content"]
        assert len(body["messages"][1]["content"]) < 5000
        return completed(stdout=json.dumps({"folder": "Finance", "confidence": 0.9,
                                           "reason": "An invoice"}))

    cands = [classify.Candidate("Finance", Path("/synthetic/Finance"), ())]
    with classify.AppleIntelligence(runner=runner, binary="/fake/helper") as model:
        verdict = model.classify("x" * 12000, cands,
                                 [{"file": "bill.pdf", "right": "Finance", "wrong": "Travel"}], 0.6)
    assert verdict.folder == "Finance"
    assert len(calls) == 2
    assert calls[0][0] == ["/fake/helper", "--check"]
    assert calls[1][1]["timeout"] == classify.REQUEST_TIMEOUT


@pytest.mark.parametrize("body", ["not json", '{"folder":"../../escape","confidence":1}',
                                       '{"folder":"Finance","confidence":0.2}',
                                       '{"folder":"Finance","confidence":true}'])
def test_apple_invalid_or_uncertain_result_never_picks_a_folder(body):
    model = classify.AppleIntelligence(runner=lambda *a, **kw: completed(stdout=body))
    cands = [classify.Candidate("Finance", Path("/synthetic/Finance"), ())]
    assert model.classify("sample", cands, [], 0.6).folder is None


@pytest.mark.parametrize("code,error", [(2, classify.ServerUnavailable),
                                      (3, classify.ItemRejected),
                                      (-9, classify.ServerUnavailable)])
def test_apple_failure_types(code, error):
    model = classify.AppleIntelligence(runner=lambda *a, **kw: completed(code, stderr="failure"))
    with pytest.raises(error, match="failure"):
        model.classify("sample", [], [], 0.6)


@pytest.mark.parametrize("error", [FileNotFoundError("helper missing"),
                                 subprocess.TimeoutExpired("helper", 180)])
def test_apple_missing_helper_and_timeout(error):
    def runner(*args, **kwargs):
        raise error

    with pytest.raises(classify.ServerUnavailable):
        classify.AppleIntelligence(runner=runner).__enter__()


def test_apple_backend_loads_and_needs_no_gguf(tmp_path):
    rules = tmp_path / "rules.toml"
    rules.write_text('[smart_sort]\nbackend = "apple"\n')
    settings = route.load_config(rules, None).smart
    router = route.SmartRouter(settings, tmp_path, read_only=True)
    assert settings.model_path is None
    assert router.factory is classify.AppleIntelligence
    assert not router.permanent


def test_unknown_backend_fails_before_routing(tmp_path):
    rules = tmp_path / "rules.toml"
    rules.write_text('[smart_sort]\nbackend = "remote"\n')
    with pytest.raises(ValueError, match="backend"):
        route.load_config(rules, None)


def test_select_backend_preserves_custom_rules(tmp_path):
    local = tmp_path / "rules.local.toml"
    local.write_text('[smart_sort]\nbackend = "llama.cpp"\nthreshold = 0.8\n'
                     '\n[[rule]]\nsource = "~/Downloads"\naction = "trash"\n')
    setup.set_smart_setting(local, "backend", "apple")
    assert 'backend = "apple"' in local.read_text()
    assert 'threshold = 0.8' in local.read_text()
    assert 'action = "trash"' in local.read_text()
    model = tmp_path / "model.gguf"
    model.touch()
    assert setup.set_model_path(local, model) is None
    assert 'backend = "llama.cpp"' in local.read_text()


def test_failed_apple_setup_does_not_select_backend(tmp_path, monkeypatch):
    monkeypatch.setattr(setup, "REPO", tmp_path)
    monkeypatch.setattr(classify, "APPLE_BINARY", tmp_path / ".build/apple-classify")
    calls = []

    def runner(cmd, **kwargs):
        calls.append(cmd)
        if "--check" in cmd:
            raise subprocess.CalledProcessError(2, cmd)
        Path(cmd[-1]).touch()

    monkeypatch.setattr(setup.subprocess, "run", runner)
    with pytest.raises(subprocess.CalledProcessError):
        setup.setup_apple_intelligence()
    assert len(calls) == 2
    assert not (tmp_path / "rules.local.toml").exists()
    assert not (tmp_path / ".build/apple-classify.building").exists()


@pytest.mark.skipif(os.environ.get("MAC_ORGANIZER_APPLE_LIVE") != "1",
                    reason="set MAC_ORGANIZER_APPLE_LIVE=1 for the on-device model")
def test_live_apple_synthetic_invoice(tmp_path):
    cands = [classify.Candidate("Finance", tmp_path / "Finance", ("tax_return.pdf",)),
             classify.Candidate("Travel", tmp_path / "Travel", ("itinerary.pdf",))]
    with classify.AppleIntelligence() as model:
        verdict = model.classify("Name: invoice.pdf\nInvoice for accounting services. "
                                 "Total due $250. Payment due November 30.", cands, [], 0.6)
    assert verdict.folder in ("Finance", None)
    assert verdict.reason != "malformed model response"
