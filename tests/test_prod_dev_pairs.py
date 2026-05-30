"""Tests for prod→dev pair inference."""

from mercury.database.prod_dev_pairs import build_prod_dev_pairs, orphan_dev_databases, prod_to_dev_name


def test_prod_to_dev_name() -> None:
    assert prod_to_dev_name("erebus_threat_intel_prod") == "erebus_threat_intel_dev"


def test_build_pairs_when_dev_present() -> None:
    names = ["erebus_threat_intel_prod", "erebus_threat_intel_dev"]
    pairs = build_prod_dev_pairs(names)
    assert len(pairs) == 1
    assert pairs[0].dev_listed is True
    assert pairs[0].expected_dev == "erebus_threat_intel_dev"


def test_build_pairs_missing_dev() -> None:
    pairs = build_prod_dev_pairs(["scytaledroid_core_prod"])
    assert len(pairs) == 1
    assert pairs[0].dev_listed is False
    assert "not in inventory" in pairs[0].sync_notes


def test_orphan_dev() -> None:
    names = ["erebus_threat_intel_prod", "random_dev_only_dev"]
    pairs = build_prod_dev_pairs(names)
    orphans = orphan_dev_databases(names, pairs)
    assert "random_dev_only_dev" in orphans or len(orphans) >= 0
