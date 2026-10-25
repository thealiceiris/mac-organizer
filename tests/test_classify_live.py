"""Opt-in end-to-end test against the real llama-server and default model.

Run by hand: MAC_ORGANIZER_LIVE=1 .venv/bin/python -m pytest tests/test_classify_live.py -q -s
It lists folder and file NAMES under ~/Documents (no file is opened) and
classifies a synthetic sample written here.
"""
import os
import time
from pathlib import Path

import pytest

import classify

MODEL = Path("~/Library/Application Support/Orb/models/gguf/google_gemma-4-E4B-it-Q4_K_M.gguf").expanduser()
TARGET = "Academics/Cyber Security Essentials"

pytestmark = pytest.mark.skipif(os.environ.get("MAC_ORGANIZER_LIVE") != "1",
                                reason="set MAC_ORGANIZER_LIVE=1 to run the real model")

SAMPLE = """Name: ICS570_lab3_report.pdf
ICS 570 Cyber Security Essentials - Lab 3: OpenPGP
In this lab we generate an RSA 4096 key pair with GnuPG (gpg --full-generate-key),
export the public key in ASCII armor, sign and encrypt a message for a classmate,
verify a detached signature, and discuss the web of trust and key revocation
certificates. Questions: 1. Why is the private key protected with a passphrase?
2. What does the fingerprint identify? 3. Explain hybrid encryption in OpenPGP.
"""


def test_live_openpgp_lab_goes_to_cyber_security_or_unsure():
    cands = classify.candidates(Path("~/Documents").expanduser())
    names = [c.name for c in cands]
    assert TARGET in names
    t0 = time.monotonic()
    with classify.LlamaServer(MODEL) as srv:
        t1 = time.monotonic()
        verdict = srv.classify(SAMPLE, cands, [], 0.6)
        t2 = time.monotonic()
    print(f"\nload {t1 - t0:.1f}s, classify {t2 - t1:.1f}s, verdict {verdict}")
    assert verdict.folder in (TARGET, None)
    # An unsure caused by unparseable output means the server setup is broken.
    assert verdict.reason != "malformed model response"
