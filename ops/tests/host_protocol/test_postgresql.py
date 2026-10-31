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


def configuration_request(operation="configure_postgresql", **changes):
    from taskman_ops.host_protocol import HostRequest, PROTOCOL_VERSION
    value = dict(protocol_version=PROTOCOL_VERSION, operation=operation,
        correlation_id="op-" + "1" * 32, expected_state={},
        paths={"install_root": "/srv/taskman", "backup_root": "/srv/taskman-backups"},
        parameters={"package_track": "18", "database": {"host": "127.0.0.1", "port": 5432,
            "role": "taskman", "name": "taskman"}, "cluster": dict(CLUSTER), "hba_sha256": "a" * 64})
    value.update(changes)
    return HostRequest(**value)


def test_configuration_request_accepts_frozen_and_bootstrap_targets():
    from taskman_ops.host_protocol.postgresql import validate_postgresql_configuration_request
    request = configuration_request()
    assert validate_postgresql_configuration_request(request) is request
    request = configuration_request(parameters={**request.to_mapping()["parameters"], "cluster": None})
    assert validate_postgresql_configuration_request(request) is request


@pytest.mark.parametrize("changes", [
    {"credentials_path": "/etc/taskman/pgpass"}, {"hba_stage": "/tmp/hba"},
    {"command": "true"}, {"owner": "root"}, {"password": "synthetic"},
    {"package_track": True}, {"package_track": "18;true"}, {"hba_sha256": "A" * 64},
    {"database": {"host": "localhost", "port": 5432, "name": "taskman", "role": "taskman"}},
    {"database": {"host": "127.0.0.1", "port": True, "name": "taskman", "role": "taskman"}},
    {"database": {"host": "127.0.0.1", "port": 5432, "name": "taskman", "role": "bad;role"}},
    {"cluster": {**CLUSTER, "port": 5433}}, {"cluster": {**CLUSTER, "version": "17"}},
    {"cluster": {**CLUSTER, "extra": True}},
])
def test_configuration_request_rejects_open_or_unbound_parameters(changes):
    from taskman_ops.host_protocol.postgresql import validate_postgresql_configuration_request
    value = configuration_request().to_mapping()
    value["parameters"].update(changes)
    with pytest.raises(ProtocolError):
        validate_postgresql_configuration_request(configuration_request(**value))


@pytest.mark.parametrize("changes", [
    {"expected_state": {"ready": True}}, {"paths": {"install_root": "/srv/taskman"}},
    {"paths": {"install_root": "/srv/taskman", "backup_root": "/srv/taskman/backups"}},
    {"paths": {"install_root": "/srv/taskman", "backup_root": "/srv/taskman-backups", "stage": "/tmp/hba"}},
])
def test_configuration_request_rejects_unexpected_state_or_roots(changes):
    from taskman_ops.host_protocol.postgresql import validate_postgresql_configuration_request
    with pytest.raises(ProtocolError):
        validate_postgresql_configuration_request(configuration_request(**changes))


@pytest.mark.parametrize("operation,state", [
    ("inspect_postgresql", {"changed": False, "configuration_required": True}),
    ("inspect_postgresql", {"changed": False, "configuration_required": False}),
    ("configure_postgresql", {"changed": True}), ("configure_postgresql", {"changed": False}),
])
def test_configuration_success_is_exact(operation, state):
    from taskman_ops.host_protocol import HostResult
    from taskman_ops.host_protocol.postgresql import validate_postgresql_configuration_result
    request = configuration_request(operation)
    result = HostResult.for_request(request, "succeeded", "PostgreSQL configuration complete", state)
    assert validate_postgresql_configuration_result(request, result) is result
    for bad in ({**state, "extra": True}, {**state, "changed": 1}):
        with pytest.raises(ProtocolError):
            validate_postgresql_configuration_result(request, HostResult.for_request(request, "succeeded", "done", bad))


@pytest.mark.parametrize("reason,outcome,code", [
    ("invalid_request", "refused", 2), ("unsafe_state", "refused", 10),
    ("lock_unavailable", "retryable", 12), ("internal_failure", "retryable", 5),
    ("command_failed", "retryable", 5), ("command_failed", "manual", 5),
    ("recovery_required", "manual", 10),
])
@pytest.mark.parametrize("mutation", ["unchanged", "changed", "unknown"])
def test_configuration_failure_classification_is_exact(reason, outcome, code, mutation):
    from taskman_ops.host_protocol import HostResult
    from taskman_ops.host_protocol.postgresql import validate_postgresql_configuration_result
    request = configuration_request()
    state = dict(exit_code=code, changed=mutation != "unchanged", mutation_state=mutation,
        reason=reason, failed_boundary="postgresql")
    if reason == "lock_unavailable":
        state["locked"] = True
    valid = reason not in {"invalid_request", "lock_unavailable"} or mutation == "unchanged"
    result = HostResult.for_request(request, outcome, "failed", state)
    if not valid:
        with pytest.raises(ProtocolError):
            validate_postgresql_configuration_result(request, result)
        return
    assert validate_postgresql_configuration_result(request, result) is result
    for field, value in (("exit_code", True), ("exit_code", 8), ("changed", not state["changed"]),
                         ("locked", False), ("extra", True), ("reason", "other")):
        with pytest.raises(ProtocolError):
            validate_postgresql_configuration_result(request,
                HostResult.for_request(request, outcome, "failed", {**state, field: value}))
    if mutation != "unchanged":
        inspection = configuration_request("inspect_postgresql")
        with pytest.raises(ProtocolError):
            validate_postgresql_configuration_result(inspection, HostResult.for_request(inspection, outcome, "failed", state))
