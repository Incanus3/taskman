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
    assert _database_state(None) == ((), "unknown")
    assert _database_state({}) == ((), "unknown")
