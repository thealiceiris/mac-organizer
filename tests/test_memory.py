import json
import os
import re
from pathlib import Path

import classify
import memory

DAY = 86400.0
NOW = 1_800_000_000.0


def cands(docs, *names):
    out = []
    for n in names:
        p = docs / n
        p.mkdir(parents=True, exist_ok=True)
        out.append(classify.Candidate(n, p, ()))
    return out


def place(state, docs, folder, fname, now=NOW, summary="s", content="hello"):
    d = docs / folder
    d.mkdir(parents=True, exist_ok=True)
    f = d / fname
    f.write_text(content)
    memory.record_placement(state, f, folder, summary, now)
    return f


def locator(docs):
    def locate(name):
        return [p for p in docs.rglob(name)]
    return locate


def test_correction_recorded_when_moved_to_other_candidate(tmp_path):
    state, docs = tmp_path / "st", tmp_path / "Documents"
    cs = cands(docs, "Finance", "Development")
    f = place(state, docs, "Finance", "notes.txt", summary="a note")
    f.rename(docs / "Development" / "notes.txt")
    out = memory.detect_corrections(state, cs, NOW + 10, locate=locator(docs))
    assert len(out) == 1
    c = out[0]
    assert (c["file"], c["wrong"], c["right"], c["summary"]) == ("notes.txt", "Finance", "Development", "a note")
    assert memory.corrections_count(state) == 1
    # resolved: second run adds nothing
    assert memory.detect_corrections(state, cs, NOW + 20, locate=locator(docs)) == []
    assert memory.corrections_count(state) == 1


def test_move_within_same_candidate_subfolder_is_not_a_correction(tmp_path):
    state, docs = tmp_path / "st", tmp_path / "Documents"
    cs = cands(docs, "Finance", "Academics", "Academics/NLP")
    f = place(state, docs, "Academics/NLP", "hw.pdf")
    (docs / "Academics/NLP/week3").mkdir()
    f.rename(docs / "Academics/NLP/week3/hw.pdf")
    assert memory.detect_corrections(state, cs, NOW + 10, locate=locator(docs)) == []
    assert memory.corrections_count(state) == 0


def test_item_moved_outside_documents_is_ignored(tmp_path):
    state, docs = tmp_path / "st", tmp_path / "Documents"
    cs = cands(docs, "Finance", "Development")
    f = place(state, docs, "Finance", "gone.txt")
    f.unlink()
    assert memory.detect_corrections(state, cs, NOW + 10, locate=lambda n: []) == []
    assert memory.corrections_count(state) == 0
    # resolved without correction: placement compacted away
    pf = state / "placements.jsonl"
    assert not pf.exists() or pf.read_text().strip() == ""


def test_placements_older_than_30_days_ignored(tmp_path):
    state, docs = tmp_path / "st", tmp_path / "Documents"
    cs = cands(docs, "Finance", "Development")
    f = place(state, docs, "Finance", "old.txt", now=NOW - 31 * DAY)
    f.rename(docs / "Development" / "old.txt")
    assert memory.detect_corrections(state, cs, NOW, locate=locator(docs)) == []
    assert memory.corrections_count(state) == 0


def test_corrupt_lines_skipped_and_wrong_file_not_matched(tmp_path):
    state, docs = tmp_path / "st", tmp_path / "Documents"
    cs = cands(docs, "Finance", "Development")
    f = place(state, docs, "Finance", "a.txt")
    with open(state / "placements.jsonl", "a") as fh:
        fh.write("{broken\n")
    with open(state / "corrections.jsonl", "a") as fh:
        fh.write("not json\n")
    f.unlink()
    # same name, different inode/size -> not our file
    (docs / "Development" / "a.txt").write_text("different content entirely")
    assert memory.detect_corrections(state, cs, NOW + 1, locate=locator(docs)) == []
    assert memory.corrections_count(state) == 0


def test_select_examples_prefers_token_overlap_and_caps_k(tmp_path):
    state = tmp_path / "st"
    state.mkdir()
    rows = [
        {"file": "tax-return-2024.pdf", "summary": "", "wrong": "A", "right": "Finance", "at": 1},
        {"file": "lecture-nlp-week3.pdf", "summary": "", "wrong": "A", "right": "Academics/NLP", "at": 2},
        {"file": "zzz.pdf", "summary": "", "wrong": "A", "right": "Other", "at": 3},
    ]
    with open(state / "corrections.jsonl", "w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
        fh.write("garbage\n")
    got = memory.select_examples(state, "Academics/Natural Language NLP", k=2)
    assert len(got) == 2
    assert got[0]["file"] == "lecture-nlp-week3.pdf"
    assert set(got[0]) >= {"file", "summary", "wrong", "right"}
    assert memory.select_examples(tmp_path / "nowhere", "x") == []


def test_verdict_cache_hits_only_on_identical_key(tmp_path):
    state = tmp_path / "st"
    vc = memory.VerdictCache(state)
    args = (Path("/x/a.pdf"), 10, 5.0, "h", 2)
    assert vc.get(*args) is False
    vc.put_unsure(*args)
    assert vc.get(*args) is True
    assert memory.VerdictCache(state).get(*args) is True  # persisted
    for alt in [(Path("/x/a.pdf"), 11, 5.0, "h", 2), (Path("/x/a.pdf"), 10, 6.0, "h", 2),
                (Path("/x/a.pdf"), 10, 5.0, "h2", 2), (Path("/x/a.pdf"), 10, 5.0, "h", 3),
                (Path("/x/b.pdf"), 10, 5.0, "h", 2)]:
        assert vc.get(*alt) is False
