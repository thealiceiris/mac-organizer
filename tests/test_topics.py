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
