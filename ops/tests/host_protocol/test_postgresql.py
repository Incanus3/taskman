from copy import deepcopy

import pytest

from taskman_ops.host_protocol import ProtocolError


CLUSTER = {
    "version": "18", "name": "main", "port": 5432,
    "data_directory": "/var/lib/postgresql/18/main",
    "config_file": "/etc/postgresql/18/main/postgresql.conf",
    "hba_file": "/etc/postgresql/18/main/pg_hba.conf",
}


def authority(**changes):
    return {"branch": "existing", "cluster": dict(CLUSTER), "role": "compatible",
            "database": "compatible", "access": "unproven", **changes}


def validate(value, **changes):
    from taskman_ops.host_protocol.postgresql import validate_postgresql_authority
    return validate_postgresql_authority(value, package_track="18", port=5432,
                                         force_postgresql_config=False, **changes)


@pytest.mark.parametrize("value", [authority(), authority(role="absent", database="absent"),
    authority(database="absent"), authority(branch="bootstrap", cluster=None,
    role="absent", database="absent")])
def test_valid_selected_resource_combinations(value):
    assert validate(value) == value


@pytest.mark.parametrize("changes", [
    {"extra": True}, {"access": "unknown"}, {"role": "absent"},
    {"database": "absent", "access": "ready"}, {"cluster": None},
    {"branch": "configure-existing"}, {"branch": "bootstrap"},
])
def test_contradictory_or_open_observation_refuses(changes):
    with pytest.raises(ProtocolError):
        validate(authority(**changes))


@pytest.mark.parametrize("changes", [{"version": "16"}, {"port": 5433}, {"port": True},
    {"data_directory": "/var/lib/postgresql/18/../main"},
    {"config_file": "/tmp/operator.conf"}, {"hba_file": "/etc/postgresql/18/../pg_hba.conf"},
    {"name": "main/other"}, {"extra": True}])
def test_cluster_identity_is_closed_and_bound_to_request(changes):
    value = deepcopy(authority())
    value["cluster"].update(changes)
    with pytest.raises(ProtocolError):
        validate(value)


@pytest.mark.parametrize("flag", [None, "false", 0, 1])
def test_configuration_authority_requires_boolean(flag):
    from taskman_ops.host_protocol.postgresql import validate_postgresql_context
    with pytest.raises(ProtocolError):
        validate_postgresql_context("18", flag)


def test_preserved_existing_configuration_accepts_observed_safe_alternate_hba():
    value = authority()
    value["cluster"]["hba_file"] = "/etc/postgresql/shared-authentication.conf"
    assert validate(value) == value


def test_configuration_override_requires_the_native_hba_it_can_converge():
    from taskman_ops.host_protocol.postgresql import validate_postgresql_authority
    value = authority(branch="configure-existing")
    value["cluster"]["hba_file"] = "/etc/postgresql/shared-authentication.conf"
    with pytest.raises(ProtocolError):
        validate_postgresql_authority(value, package_track="18", port=5432, force_postgresql_config=True)


def test_configuration_override_refuses_the_preserved_configuration_branch():
    from taskman_ops.host_protocol.postgresql import validate_postgresql_authority
    with pytest.raises(ProtocolError):
        validate_postgresql_authority(authority(), package_track="18", port=5432,
                                      force_postgresql_config=True)


@pytest.mark.parametrize("field", ["branch", "role", "database", "access"])
def test_malformed_wire_state_types_raise_protocol_error(field):
    value = authority()
    value[field] = []
    with pytest.raises(ProtocolError):
        validate(value)


@pytest.mark.parametrize("invalid", ["classification", "target"])
@pytest.mark.parametrize("mutation", ["unchanged", "changed"])
def test_recovery_reduction_keeps_only_correlated_capture_evidence(invalid, mutation):
    from dataclasses import replace
    from taskman_ops.host_protocol.postgresql import reduce_postgresql_recovery_evidence
    from tests.host_helper.test_postgresql_recovery import recovery_request, recovery_result, DIRECTORY

    request = recovery_request()
    result = recovery_result(request, mutation=mutation,
        directory=None if mutation == "unchanged" else DIRECTORY, exit_code=5)
    if invalid == "classification":
        result = replace(result, outcome="manual")
    else:
        other = {**CLUSTER, "name": "other", "data_directory": "/var/lib/postgresql/18/other",
            "config_file": "/etc/postgresql/18/other/postgresql.conf",
            "hba_file": "/etc/postgresql/18/other/pg_hba.conf"}
        result = replace(result, state={**result.to_mapping()["state"], "cluster": other})

    assert reduce_postgresql_recovery_evidence(request, result) == {
        "mutation_state": "unknown" if mutation == "unchanged" else "changed",
        "recovery_directory": DIRECTORY if mutation == "changed" and invalid == "classification" else None,
        "cluster": CLUSTER if invalid == "classification" else None,
    }


def failure_context(**changes):
    return {"failed_boundary": "postgresql", "exit_code": 5,
            "failed_check": "scram-client-prerequisite",
            "postgresql_observation": authority(), **changes}


def validate_failure(state, outcome):
    from taskman_ops.host_protocol.postgresql import validate_postgresql_failure_context
    return validate_postgresql_failure_context(state, outcome=outcome, package_track="18",
                                               port=5432, force_postgresql_config=False)


@pytest.mark.parametrize("outcome,code", [("manual", 5), ("refused", 10)])
@pytest.mark.parametrize("check", ["scram-client-prerequisite", "scram-application-access",
    "schema-inspection", "resource-schema-consistency", "selected-target-or-resource-safety"])
def test_postgresql_failure_accepts_consistent_categories_with_independent_checks(outcome, code, check):
    state = failure_context(exit_code=code, failed_check=check)
    assert validate_failure(state, outcome) == state


@pytest.mark.parametrize("outcome,code", [("manual", 5), ("refused", 10)])
def test_postgresql_failure_accepts_null_observation(outcome, code):
    state = failure_context(exit_code=code, postgresql_observation=None)
    assert validate_failure(state, outcome) == state


@pytest.mark.parametrize("outcome,code", [
    ("refused", 5), ("manual", 10), ("retryable", 5), ("retryable", 10),
    ("succeeded", 5), ("succeeded", 10), ("manual", 0), ("refused", 8),
    ("manual", True), ("manual", 5.0), ("refused", 10.0), ("manual", "5"),
    ("manual", None), ("manual", []), ("manual", {}),
    (None, 5), (True, 5), (5, 5), ([], 5), ({}, 5), ("unknown", 5),
])
def test_postgresql_failure_rejects_invalid_classification(outcome, code):
    with pytest.raises(ProtocolError, match="^invalid PostgreSQL failure context$"):
        validate_failure(failure_context(exit_code=code), outcome)


@pytest.mark.parametrize("check", ["unknown", None, True, 5, [], {}])
def test_postgresql_failure_rejects_invalid_check(check):
    with pytest.raises(ProtocolError, match="^invalid PostgreSQL failure context$"):
        validate_failure(failure_context(failed_check=check), "manual")
