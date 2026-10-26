"""Opt-in end-to-end test against the real llama-server and your model.

Run by hand:
  MAC_ORGANIZER_LIVE=1 MAC_ORGANIZER_MODEL=~/models/your-model.gguf \
    .venv/bin/python -m pytest tests/test_classify_live.py -q -s
It builds a throwaway Documents tree in a temp folder (your own files are never
read) and classifies a synthetic sample written here.
"""
import os
import time
from pathlib import Path

import pytest

import classify

MODEL_ENV = os.environ.get("MAC_ORGANIZER_MODEL", "")
MODEL = Path(MODEL_ENV).expanduser() if MODEL_ENV else None
TARGET = "Academics/Computer Security"

pytestmark = [
    pytest.mark.skipif(os.environ.get("MAC_ORGANIZER_LIVE") != "1",
                       reason="set MAC_ORGANIZER_LIVE=1 to run the real model"),
    pytest.mark.skipif(MODEL is None, reason="set MAC_ORGANIZER_MODEL to the GGUF model to test"),
]

SAMPLE = """Name: lab3_report.pdf
Computer Security - Lab 3: OpenPGP
In this lab we generate an RSA 4096 key pair with GnuPG (gpg --full-generate-key),
export the public key in ASCII armor, sign and encrypt a message for a classmate,
verify a detached signature, and discuss the web of trust and key revocation
certificates. Questions: 1. Why is the private key protected with a passphrase?
2. What does the fingerprint identify? 3. Explain hybrid encryption in OpenPGP.
"""

FOLDERS = {
    "Academics/Computer Security": ["firewalls_notes.pdf", "lab2_hashing.pdf"],
    "Academics/Statistics": ["regression_hw.pdf"],
    "Career": ["cv.pdf", "cover_letter.docx"],
    "Recipes": ["jollof.pdf"],
}


def test_live_openpgp_lab_goes_to_computer_security_or_unsure(tmp_path):
    for folder, files in FOLDERS.items():
        (tmp_path / folder).mkdir(parents=True)
        for f in files:
            (tmp_path / folder / f).write_text("x")
    cands = classify.candidates(tmp_path)
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
