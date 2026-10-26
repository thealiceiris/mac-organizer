import pytest

import topics


def test_normalise():
    assert topics.normalise("Taxes 2024") == "tax"
    assert topics.normalise("  Car-Insurance! ") == "car insurance"
    assert topics.normalise("Recipes") == "recipe"


def test_near_duplicate_top_level_and_expand(tmp_path):
    root = tmp_path / "Documents"
    (root / "Tax").mkdir(parents=True)
    (root / "Academics" / "Calculus").mkdir(parents=True)
    (root / ".hidden").mkdir()
    assert topics.near_duplicate("Taxes", root, ("Academics",)) == "Tax"
    assert topics.near_duplicate("calculus", root, ("Academics",)) == "Academics/Calculus"
    assert topics.near_duplicate("calculus", root, ()) is None
    assert topics.near_duplicate("Recipes", root, ("Academics",)) is None


def test_valid_folder_name_rejections(tmp_path):
    root = tmp_path / "Documents"
    (root / "Tax").mkdir(parents=True)
    assert topics.valid_folder_name("Recipes", root) is None
    for bad in ("", "   ", "a/b", ".hidden", "x" * 61, "Tax"):
        assert topics.valid_folder_name(bad, root), bad
    assert topics.valid_folder_name("x" * 60, root) is None


# ---------------------------------------------------------------- record / clusters


def test_clusters_only_counts_items_still_in_misc(tmp_path):
    import topics
    st, misc = tmp_path / "state", tmp_path / "Misc"
    misc.mkdir()
    files = []
    for n in "abc":
        f = misc / f"{n}.pdf"
        f.write_text(n)
        topics.record(st, f, "Taxes", 1.0)
        files.append(f)
    files[2].rename(tmp_path / "c.pdf")

    got = topics.clusters(st, misc)

    assert list(got) == ["tax"]
    name, items = got["tax"]
    assert name == "Taxes" and sorted(items) == sorted(files[:2])
    assert len([l for l in (st / "misc-topics.jsonl").read_text().splitlines() if l]) == 2


def test_clusters_display_name_is_most_common_text(tmp_path):
    import topics
    st, misc = tmp_path / "state", tmp_path / "Misc"
    misc.mkdir()
    for i, t in enumerate(["Tax", "Taxes", "Taxes 2024", "Taxes"]):
        f = misc / f"{i}.pdf"
        f.write_text("x")
        topics.record(st, f, t, 1.0)

    assert topics.clusters(st, misc)["tax"][0] == "Taxes"


def test_clusters_ignores_nested_and_missing_state(tmp_path):
    import topics
    misc = tmp_path / "Misc"
    (misc / "sub").mkdir(parents=True)
    f = misc / "sub" / "x.pdf"
    f.write_text("x")
    topics.record(tmp_path / "st", f, "Taxes", 1.0)
    assert topics.clusters(tmp_path / "st", misc) == {}
    assert topics.clusters(tmp_path / "none", misc) == {}
