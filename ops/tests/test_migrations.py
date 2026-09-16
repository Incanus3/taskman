"""Canonical migration history shared across authority boundaries."""

import pytest

from taskman_ops.migrations import MigrationOrderError, validate_migration_versions


@pytest.mark.parametrize("value, expected", (([], ()), ((), ()), ([0, 2, 10], (0, 2, 10)), ((1, 3), (1, 3))))
def test_preserves_valid_history(value, expected) -> None:
    assert validate_migration_versions(value) == expected


@pytest.mark.parametrize("value", (None, {}, "12", {1, 2}, [True], [False], [-1], [1.0], ["1"]))
def test_rejects_malformed_history(value) -> None:
    with pytest.raises(ValueError) as failure:
        validate_migration_versions(value)
    assert not isinstance(failure.value, MigrationOrderError)


@pytest.mark.parametrize("value", ([1, 1], [2, 1], [0, 2, 1]))
def test_rejects_ordering_instead_of_normalizing(value) -> None:
    with pytest.raises(MigrationOrderError):
        validate_migration_versions(value)


def test_record_limit_does_not_constrain_the_shared_history_rule() -> None:
    assert validate_migration_versions(list(range(513))) == tuple(range(513))


@pytest.mark.parametrize("value, record_message, state_message", (
    ([True], "invalid migration versions", "applied migrations are invalid"),
    ([2, 1], "migration versions must be sorted and unique", "applied migrations are not sorted and unique"),
    ([1, 1], "migration versions must be sorted and unique", "applied migrations are not sorted and unique"),
))
def test_boundaries_preserve_their_error_categories(value, record_message, state_message) -> None:
    from taskman_ops.host_helper.database import migration_versions
    from taskman_ops.host_helper.records import RecordError, _migration_versions
    from taskman_ops.host_helper.state import StateAmbiguityError, _database_state

    with pytest.raises(ValueError, match="^migration versions are invalid$"):
        migration_versions(value)
    with pytest.raises(RecordError) as failure:
        _migration_versions(value)
    assert str(failure.value) == record_message
    with pytest.raises(StateAmbiguityError) as failure:
        _database_state({"state": "ready", "applied_migrations": value})
    assert str(failure.value) == state_message


def test_record_limit_and_unknown_database_defaults_remain_local() -> None:
    from taskman_ops.host_helper.records import RecordError, _migration_versions
    from taskman_ops.host_helper.state import _database_state

    assert _migration_versions(list(range(512))) == tuple(range(512))
    with pytest.raises(RecordError, match="^invalid migration versions$"):
        _migration_versions(list(range(513)))
    assert _database_state(None) == ((), "unknown", False)
    assert _database_state({}) == ((), "unknown", False)
    assert _database_state(
        {"state": "ready", "applied_migrations": (), "initial_empty": True}
    ) == ((), "ready", True)


@pytest.mark.parametrize("filenames, expected", (
    ([], ()),
    ((), ()),
    (["00000000000000_initial.exs", "20260907120001_create_tasks.exs"], (0, 20260907120001)),
    (("20260907120001_create_tasks.exs", "20260907120002_add_index.exs"), (20260907120001, 20260907120002)),
))
def test_extracts_versions_from_filename_sequences(filenames, expected) -> None:
    from taskman_ops import migrations

    assert migrations.versions_from_filenames(filenames) == expected


@pytest.mark.parametrize("filenames", (
    None, {}, "20260907120001_create_tasks.exs", {"20260907120001_create_tasks.exs"},
    [None], [True], [20260907120001],
    ["2026090712001_short.exs"], ["202609071200011_long.exs"],
    ["20260907120001_.exs"], ["20260907120001_Upper.exs"],
    ["20260907120001_name.exs\n"], ["path/20260907120001_name.exs"],
))
def test_rejects_malformed_filename_sequences(filenames) -> None:
    from taskman_ops import migrations

    with pytest.raises(ValueError, match="^invalid migration filenames$"):
        migrations.versions_from_filenames(filenames)


@pytest.mark.parametrize("filenames", (
    ("20260907120001_a.exs", "20260907120001_b.exs"),
    ("20260907120002_b.exs", "20260907120001_a.exs"),
))
def test_rejects_filename_version_order_without_normalizing(filenames) -> None:
    from taskman_ops import migrations

    with pytest.raises(MigrationOrderError, match="^migration versions must be sorted and unique$"):
        migrations.versions_from_filenames(filenames)


@pytest.mark.parametrize("records, message", (
    ([], "release migration records are invalid"),
    ((None,), "release migration records are invalid"),
    (({},), "release migration records are invalid"),
    (({"filename": "20260907120001_.exs"},), "release migration records are invalid"),
    (({"filename": "20260907120002_b.exs"}, {"filename": "20260907120001_a.exs"}), "migration versions are invalid"),
    (({"filename": "20260907120001_a.exs"}, {"filename": "20260907120001_b.exs"}), "migration versions are invalid"),
))
def test_release_mapping_extraction_preserves_shape_and_error_categories(records, message) -> None:
    from taskman_ops.host_helper.records import migration_record_versions

    with pytest.raises(ValueError) as failure:
        migration_record_versions(records)
    assert str(failure.value) == message


def test_release_mapping_extraction_keeps_empty_and_record_limits_local() -> None:
    from taskman_ops.host_helper.records import migration_record_versions

    assert migration_record_versions(()) == ()
    records = tuple({"filename": f"{version:014d}_migration.exs"} for version in range(513))
    assert migration_record_versions(records) == tuple(range(513))


@pytest.mark.parametrize("filenames", (
    ("20260907120001_a.exs", "20260907120001_b.exs"),
    ("20260907120002_b.exs", "20260907120001_a.exs"),
))
def test_deployment_boundaries_keep_candidate_order_refusals(filenames) -> None:
    from types import SimpleNamespace
    from taskman_ops.errors import OpsError
    from taskman_ops.host_helper.operations.deploy import _migration_versions_from_manifest
    from taskman_ops.releases.manifests import MigrationFingerprint
    from taskman_ops.workflows.deploy import _pending_migration_versions

    candidate = tuple(MigrationFingerprint(filename, "a" * 64) for filename in filenames)
    with pytest.raises(ValueError, match="^invalid candidate migrations$"):
        _migration_versions_from_manifest(SimpleNamespace(migrations=candidate))
    with pytest.raises(OpsError, match="^candidate migration versions are invalid$"):
        _pending_migration_versions((), candidate)
