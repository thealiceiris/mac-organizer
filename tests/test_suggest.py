import json

import pytest

import suggest
import topics
from route import SuggestSettings

S = SuggestSettings()
DAY = 86400.0
NOW = 1_000_000.0


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))  # accept refuses folders outside $HOME
    state, misc, docs = tmp_path / "state", tmp_path / "Misc", tmp_path / "Documents"
    for d in (state, misc, docs):
        d.mkdir()
    logs = []
    return state, misc, docs, (lambda v, s, d: logs.append((v, s, d))), logs


def add(env, topic, n, prefix=None):
    state, misc = env[0], env[1]
    out = []
    for i in range(n):
        p = misc / f"{prefix or topic}-{i}.txt"
        p.write_text("x")
        topics.record(state, p, topic, NOW)
        out.append(p)
    return out


def test_due_needs_min_items_and_picks_largest(env):
    state, misc, docs, *_ = env
    add(env, "Recipes", 2)
    assert suggest.due(state, misc, docs, (), S, NOW) is None
    add(env, "Travel", 3)
    add(env, "Taxes", 4)
    s = suggest.due(state, misc, docs, (), S, NOW)
    assert s.name == "Taxes" and s.key == "tax" and len(s.items) == 4 and s.sent_at is None


def test_due_respects_never_snooze_near_duplicate_and_daily_limit(env):
    state, misc, docs, *_ = env
    add(env, "Taxes", 5)
    add(env, "Travel", 5)
    add(env, "Recipes", 4)
    add(env, "Photos", 3)
    (docs / "Tax").mkdir()
    s = suggest.due(state, misc, docs, (), S, NOW)
    assert s.key == "travel"  # tax is a near-duplicate of Documents/Tax
    suggest.decline(state, "travel", NOW, S, never=True)
    s = suggest.due(state, misc, docs, (), S, NOW)
    assert s.key == "recipe"
    suggest.decline(state, "recipe", NOW, S, never=False)
    s = suggest.due(state, misc, docs, (), S, NOW + 1)
    assert s.key == "photo"
    # snooze ends after snooze_days
    assert suggest.due(state, misc, docs, (), S, NOW + 8 * DAY).key == "recipe"
    suggest.mark_sent(state, s, NOW)
    # daily limit counts from send, answered or not
    assert suggest.due(state, misc, docs, (), S, NOW + DAY - 1) is None
    add(env, "Music", 3)
    suggest.decline(state, "photo", NOW + 10, S, never=False)
    assert suggest.due(state, misc, docs, (), S, NOW + DAY - 1) is None
    assert suggest.due(state, misc, docs, (), S, NOW + DAY + 1) is not None


def test_pending_expires_after_7_days_into_snooze(env):
    state, misc, docs, *_ = env
    add(env, "Travel", 3)
    s = suggest.due(state, misc, docs, (), S, NOW)
    suggest.mark_sent(state, s, NOW)
    assert [p.key for p in suggest.pending(state, NOW + 6 * DAY, S)] == ["travel"]
    assert suggest.pending(state, NOW + 7 * DAY + 1, S) == []
    assert suggest.due(state, misc, docs, (), S, NOW + 7 * DAY + 2) is None  # snoozed
    assert suggest.due(state, misc, docs, (), S, NOW + 15 * DAY).key == "travel"


def sent(env, topic, n):
    state, misc, docs = env[:3]
    items = add(env, topic, n)
    s = suggest.due(state, misc, docs, (), S, NOW)
    suggest.mark_sent(state, s, NOW)
    return s, items


def test_accept_creates_folder_and_moves_remaining_items_only(env):
    state, misc, docs, log, logs = env
    s, items = sent(env, "Travel", 4)
    items[0].unlink()
    r = suggest.accept(state, s.key, "Trips", misc, docs, (), log)
    assert r.error is None and r.similar is None
    assert r.created == docs / "Trips" and len(r.moved) == 3
    assert all(p.parent == docs / "Trips" for p in r.moved)
    verbs = [v for v, *_ in logs]
    assert verbs.count("NEWFOLDER") == 1 and verbs.count("SUGGEST") == 3
    assert suggest.pending(state, NOW, S) == []


def test_accept_with_no_remaining_items_creates_nothing(env):
    state, misc, docs, log, logs = env
    s, items = sent(env, "Travel", 3)
    for p in items:
        p.unlink()
    r = suggest.accept(state, s.key, "Travel", misc, docs, (), log)
    assert r.error == "those files are no longer in Misc" and r.created is None
    assert list(docs.iterdir()) == [] and logs == []
    assert suggest.pending(state, NOW, S) == []


@pytest.mark.parametrize("bad", ["", "   ", "a/b", ".x", "a:b", "a\nb", "a\tb", "x" * 61, "Existing"])
def test_accept_invalid_or_existing_name_creates_nothing(env, bad):
    state, misc, docs, log, logs = env
    (docs / "Existing").mkdir()
    s, items = sent(env, "Travel", 3)
    r = suggest.accept(state, s.key, bad, misc, docs, (), log)
    assert r.error and r.created is None and r.moved == []
    assert sorted(p.name for p in docs.iterdir()) == ["Existing"]
    assert all(p.exists() for p in items) and logs == []
    assert [p.key for p in suggest.pending(state, NOW, S)] == [s.key]


def test_accept_strips_whitespace(env):
    state, misc, docs, log, _ = env
    s, _ = sent(env, "Travel", 3)
    r = suggest.accept(state, s.key, "  Trips \n", misc, docs, (), log)
    assert r.created == docs / "Trips"


def test_accept_near_duplicate_returns_similar_and_creates_nothing(env):
    state, misc, docs, log, logs = env
    (docs / "Academics" / "Trips").mkdir(parents=True)
    s, items = sent(env, "Travel", 3)
    r = suggest.accept(state, s.key, "trip", misc, docs, ("Academics",), log)
    assert r.similar == "Academics/Trips" and r.created is None and r.error is None
    assert sorted(p.name for p in docs.iterdir()) == ["Academics"]
    assert all(p.exists() for p in items) and logs == []


def test_accept_mkdir_race_creates_nothing_else(env, monkeypatch):
    state, misc, docs, log, logs = env
    s, items = sent(env, "Travel", 3)
    monkeypatch.setattr(topics, "valid_folder_name", lambda n, r: None)
    (docs / "Trips").mkdir()
    monkeypatch.setattr(topics, "near_duplicate", lambda *a: None)
    r = suggest.accept(state, s.key, "Trips", misc, docs, (), log)
    assert r.error and r.created is None and all(p.exists() for p in items)


def test_accept_into_existing_moves_items(env):
    state, misc, docs, log, logs = env
    (docs / "Academics" / "Trips").mkdir(parents=True)
    s, items = sent(env, "Travel", 3)
    r = suggest.accept_into_existing(state, s.key, "Academics/Trips", misc, docs, log)
    assert r.error is None and r.created is None and len(r.moved) == 3
    assert all(p.parent == docs / "Academics" / "Trips" for p in r.moved)
    assert [v for v, *_ in logs] == ["SUGGEST"] * 3
    assert suggest.pending(state, NOW, S) == []


def test_accept_into_existing_rejects_escape_and_missing(env):
    state, misc, docs, log, logs = env
    s, items = sent(env, "Travel", 3)
    for bad in ("../x", "Nope", "/tmp"):
        assert suggest.accept_into_existing(state, s.key, bad, misc, docs, log).error
    assert all(p.exists() for p in items)


def test_accept_twice_is_noop(env):
    state, misc, docs, log, logs = env
    s, _ = sent(env, "Travel", 3)
    assert suggest.accept(state, s.key, "Trips", misc, docs, (), log).error is None
    r = suggest.accept(state, s.key, "Trips 2", misc, docs, (), log)
    assert r.error == "no such suggestion" and r.created is None
    assert [p.name for p in docs.iterdir()] == ["Trips"]


def test_corrupt_state_reads_as_empty(env):
    state, misc, docs, *_ = env
    (state / "suggestions.json").write_text("{not json")
    (state / "never.json").write_text("[[[")
    add(env, "Travel", 3)
    assert suggest.pending(state, NOW, S) == []
    assert suggest.due(state, misc, docs, (), S, NOW).key == "travel"
    suggest.decline(state, "travel", NOW, S, never=True)
    assert json.loads((state / "never.json").read_text())


# ---------------------------------------------------------------- final review fixes


def test_ignored_suggestion_expires_inside_due_without_any_cli_call(env):
    """I2: due() itself expires an ignored suggestion; snooze counts from expiry, not now."""
    state, misc, docs, *_ = env
    add(env, "Travel", 3)
    suggest.mark_sent(state, suggest.due(state, misc, docs, (), S, NOW), NOW)
    assert suggest.due(state, misc, docs, (), S, NOW + 8 * DAY) is None  # snoozed
    data = json.loads((state / "suggestions.json").read_text())
    assert data["pending"] == {}
    assert data["snoozed"]["travel"] == NOW + 14 * DAY  # sent_at + expire + snooze
    assert suggest.due(state, misc, docs, (), S, NOW + 13 * DAY) is None
    s = suggest.due(state, misc, docs, (), S, NOW + 15 * DAY)
    assert s is not None and s.key == "travel"


def test_ignored_suggestion_first_seen_late_is_not_snoozed_from_now(env):
    state, misc, docs, *_ = env
    add(env, "Travel", 3)
    suggest.mark_sent(state, suggest.due(state, misc, docs, (), S, NOW), NOW)
    # nothing ran for 30 days: the topic may be offered straight away
    assert suggest.due(state, misc, docs, (), S, NOW + 30 * DAY).key == "travel"


def test_due_and_pending_can_read_without_writing(env):
    state, misc, docs, *_ = env
    add(env, "Travel", 3)
    suggest.mark_sent(state, suggest.due(state, misc, docs, (), S, NOW), NOW)
    before = (state / "suggestions.json").read_text()
    assert suggest.pending(state, NOW + 8 * DAY, S, write=False) == []
    assert suggest.due(state, misc, docs, (), S, NOW + 8 * DAY, write=False) is None
    assert (state / "suggestions.json").read_text() == before


def test_due_skips_clusters_whose_name_is_not_a_valid_folder(env):
    state, misc, docs, *_ = env
    add(env, "a:b", 5, prefix="colon")
    add(env, ".hidden", 5, prefix="dot")
    add(env, "Travel", 3)
    assert suggest.due(state, misc, docs, (), S, NOW).key == "travel"


def test_accept_moves_only_the_files_the_user_saw(env):
    """I7: a file that joined the cluster after the notification stays in Misc."""
    state, misc, docs, log, logs = env
    s, items = sent(env, "Travel", 3)
    late = add(env, "Travel", 1, prefix="late")[0]
    r = suggest.accept(state, s.key, "Trips", misc, docs, (), log)
    assert r.error is None and len(r.moved) == 3 and r.gone == 0
    assert late.exists() and not (docs / "Trips" / late.name).exists()


def test_accept_reports_listed_files_no_longer_in_misc(env):
    state, misc, docs, log, logs = env
    s, items = sent(env, "Travel", 4)
    items[0].unlink()
    items[1].rename(docs / items[1].name)
    r = suggest.accept(state, s.key, "Trips", misc, docs, (), log)
    assert len(r.moved) == 2 and r.gone == 2


def test_accept_into_existing_moves_only_the_files_the_user_saw(env):
    state, misc, docs, log, logs = env
    (docs / "Trips").mkdir()
    s, items = sent(env, "Travel", 3)
    late = add(env, "Travel", 1, prefix="late")[0]
    items[0].unlink()
    r = suggest.accept_into_existing(state, s.key, "Trips", misc, docs, log)
    assert len(r.moved) == 2 and r.gone == 1 and late.exists()


def test_accept_into_existing_refuses_a_symlinked_parent_outside_home(env, tmp_path, monkeypatch):
    """I6: Documents/Academics -> a folder outside $HOME must never receive files."""
    state, misc, docs, log, logs = env
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    outside = tmp_path / "outside"
    (outside / "Trips").mkdir(parents=True)
    hd, hm, hs = home / "Documents", home / "Documents" / "Misc", home / "state"
    hm.mkdir(parents=True)
    hs.mkdir()
    (hd / "Academics").symlink_to(outside)
    paths = []
    for i in range(3):
        p = hm / f"t-{i}.txt"
        p.write_text("x")
        topics.record(hs, p, "Travel", NOW)
        paths.append(p)
    s = suggest.due(hs, hm, hd, ("Academics",), S, NOW)
    suggest.mark_sent(hs, s, NOW)
    r = suggest.accept_into_existing(hs, s.key, "Academics/Trips", hm, hd, log)
    assert r.error and r.moved == []
    assert all(p.exists() for p in paths) and list((outside / "Trips").iterdir()) == []
    assert [x.key for x in suggest.pending(hs, NOW, S)] == [s.key]


def test_accept_into_existing_refuses_a_symlinked_top_level_inside_home(env, tmp_path):
    state, misc, docs, log, logs = env
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (docs / "Trips").symlink_to(elsewhere)
    s, items = sent(env, "Travel", 3)
    r = suggest.accept_into_existing(state, s.key, "Trips", misc, docs, log)
    assert r.error and r.moved == [] and all(p.exists() for p in items)


def test_accept_refuses_a_documents_root_outside_home(env, tmp_path, monkeypatch):
    state, misc, docs, log, logs = env
    s, items = sent(env, "Travel", 3)
    monkeypatch.setenv("HOME", str(tmp_path / "elsewhere"))
    (tmp_path / "elsewhere").mkdir()
    r = suggest.accept(state, s.key, "Trips", misc, docs, (), log)
    assert r.error and r.created is None and not (docs / "Trips").exists()
    assert all(p.exists() for p in items)


def test_move_all_never_recreates_a_vanished_folder(env, tmp_path):
    state, misc, docs, log, logs = env
    f = misc / "a.txt"
    f.write_text("x")
    moved, err = suggest._move_all([f], tmp_path / "gone", log)
    assert moved == [] and err and f.exists() and not (tmp_path / "gone").exists()


def test_accept_keeps_suggestion_pending_when_nothing_moved(env, monkeypatch):
    state, misc, docs, log, logs = env
    s, items = sent(env, "Travel", 3)

    def fail(src, dst, mkdir=True):
        raise OSError("disk says no")

    monkeypatch.setattr(suggest.fsops, "move_item", fail)
    r = suggest.accept(state, s.key, "Trips", misc, docs, (), log)
    assert r.created == docs / "Trips" and r.moved == [] and r.error
    assert (docs / "Trips").is_dir()  # nothing is removed
    assert [x.key for x in suggest.pending(state, NOW, S)] == [s.key]
