"""Closed selected PostgreSQL resource authority shared across the helper boundary."""

from collections.abc import Mapping
from pathlib import PurePosixPath
import re

from .identifiers import ProtocolError


_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_-]*\Z")


def validate_postgresql_context(package_track, force_postgresql_config):
    if (package_track is not None and
            (type(package_track) is not str or re.fullmatch(r"[0-9]+", package_track) is None)):
        raise ProtocolError("invalid PostgreSQL package track")
    if type(force_postgresql_config) is not bool:
        raise ProtocolError("invalid PostgreSQL configuration authority")


def native_path(value):
    if (type(value) is not str or not value.startswith("/") or
            re.fullmatch(r"/[A-Za-z0-9_./-]+", value) is None or
            ".." in value.split("/") or str(PurePosixPath(value)) != value):
        raise ProtocolError("invalid PostgreSQL native path")
    return value


def validate_postgresql_authority(value, *, package_track, port, force_postgresql_config):
    """Validate combinations and selected identity against the original request."""
    validate_postgresql_context(package_track, force_postgresql_config)
    if type(port) is not int or not 0 < port < 65536:
        raise ProtocolError("invalid PostgreSQL configured port")
    if (not isinstance(value, Mapping) or
            set(value) != {"branch", "cluster", "role", "database", "access"}):
        raise ProtocolError("invalid PostgreSQL authority fields")
    if any(type(value[key]) is not str for key in ("branch", "role", "database", "access")):
        raise ProtocolError("invalid PostgreSQL authority states")
    if (value["role"] not in {"absent", "compatible"} or
            value["database"] not in {"absent", "compatible"} or
            value["access"] not in {"unproven", "ready"} or
            value["role"] == "absent" and value["database"] != "absent" or
            value["access"] == "ready" and value["database"] != "compatible"):
        raise ProtocolError("inconsistent PostgreSQL resources")
    if value["branch"] == "bootstrap":
        if (value["cluster"] is not None or value["role"] != "absent" or
                value["database"] != "absent" or value["access"] != "unproven"):
            raise ProtocolError("inconsistent PostgreSQL bootstrap")
        return value
    expected_branch = "configure-existing" if force_postgresql_config else "existing"
    if value["branch"] != expected_branch or force_postgresql_config and value["access"] != "unproven":
        raise ProtocolError("inconsistent PostgreSQL configuration branch")
    cluster = value["cluster"]
    if (not isinstance(cluster, Mapping) or
            set(cluster) != {"version", "name", "data_directory", "config_file", "hba_file", "port"}):
        raise ProtocolError("invalid PostgreSQL cluster fields")
    version, name = cluster["version"], cluster["name"]
    if (type(version) is not str or re.fullmatch(r"[0-9]+", version) is None or
            type(name) is not str or _NAME.fullmatch(name) is None or
            package_track is not None and version != package_track or
            type(cluster["port"]) is not int or cluster["port"] != port or not 0 < port < 65536):
        raise ProtocolError("PostgreSQL cluster does not match configured target")
    native_path(cluster["data_directory"])
    native_path(cluster["config_file"])
    native_path(cluster["hba_file"])
    if (cluster["config_file"] != f"/etc/postgresql/{version}/{name}/postgresql.conf" or
            force_postgresql_config and cluster["hba_file"] != f"/etc/postgresql/{version}/{name}/pg_hba.conf"):
        raise ProtocolError("PostgreSQL native configuration identity is invalid")
    return value


def validate_postgresql_failure_context(state, *, outcome, package_track, port, force_postgresql_config):
    """Accept consistent failure categories and request-bound selection evidence."""
    if (set(state) != {"failed_boundary", "exit_code", "failed_check", "postgresql_observation"} or
            state.get("failed_boundary") != "postgresql" or
            type(outcome) is not str or type(state.get("exit_code")) is not int or
            (outcome, state["exit_code"]) not in {("manual", 5), ("refused", 10)} or
            type(state.get("failed_check")) is not str or
            state.get("failed_check") not in {"scram-client-prerequisite", "scram-application-access",
            "schema-inspection", "resource-schema-consistency", "selected-target-or-resource-safety"}):
        raise ProtocolError("invalid PostgreSQL failure context")
    observation = state.get("postgresql_observation")
    if observation is not None:
        validate_postgresql_authority(observation, package_track=package_track, port=port,
            force_postgresql_config=force_postgresql_config)
    else:
        validate_postgresql_context(package_track, force_postgresql_config)
    return state


POSTGRESQL_RECOVERY_ROOT = "/etc/taskman/postgresql-config-recovery"


def validate_postgresql_cluster(cluster):
    """Validate a closed native configuration target without observing the host."""
    if not isinstance(cluster, Mapping):
        raise ProtocolError("invalid PostgreSQL selected cluster")
    validate_postgresql_authority(
        {"branch": "configure-existing", "cluster": cluster, "role": "absent",
         "database": "absent", "access": "unproven"},
        package_track=None, port=cluster.get("port"), force_postgresql_config=True)
    return cluster


def validate_postgresql_recovery_directory(value):
    if (type(value) is not str or re.fullmatch(
            re.escape(POSTGRESQL_RECOVERY_ROOT) + r"/[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", value) is None):
        raise ProtocolError("invalid PostgreSQL recovery directory")
    return value


def validate_postgresql_recovery_request(request):
    from .envelope import HostRequest
    if (not isinstance(request, HostRequest) or request.operation != "postgresql_config_recovery" or
            request.expected_state or set(request.parameters) != {"cluster"} or
            set(request.paths) != {"install_root", "backup_root"}):
        raise ProtocolError("invalid PostgreSQL recovery request")
    roots = [PurePosixPath(request.paths[key]) for key in ("install_root", "backup_root")]
    if roots[0].is_relative_to(roots[1]) or roots[1].is_relative_to(roots[0]):
        raise ProtocolError("overlapping PostgreSQL recovery request roots")
    validate_postgresql_cluster(request.parameters["cluster"])
    return request


def validate_postgresql_recovery_result(request, result):
    from .envelope import validate_result_for_request
    validate_postgresql_recovery_request(request)
    validate_result_for_request(request, result)
    success = result.outcome == "succeeded"
    keys = {"mutation_state", "recovery_directory", "cluster"}
    if not success:
        keys |= {"exit_code", "failed_boundary"}
    state = result.state
    if set(state) != keys or type(state["mutation_state"]) is not str or state["mutation_state"] not in {"unchanged", "changed", "unknown"}:
        raise ProtocolError("invalid PostgreSQL recovery state")
    if success and (state["mutation_state"] != "changed" or state["recovery_directory"] is None or state["cluster"] is None):
        raise ProtocolError("unproven PostgreSQL recovery capture")
    if not success:
        code = state["exit_code"]
        if (type(code) is not int or code not in {5, 8, 10} or state["failed_boundary"] != "postgresql" or
                result.outcome != ("refused" if code == 10 else "retryable")):
            raise ProtocolError("invalid PostgreSQL recovery failure")
    if state["cluster"] is not None:
        validate_postgresql_cluster(state["cluster"])
        if state["cluster"] != request.parameters["cluster"]:
            raise ProtocolError("PostgreSQL recovery target drift")
    if state["recovery_directory"] is not None:
        validate_postgresql_recovery_directory(state["recovery_directory"])
        if state["cluster"] is None or state["mutation_state"] == "unchanged":
            raise ProtocolError("inconsistent PostgreSQL recovery identity")
    return result


def reduce_postgresql_recovery_evidence(request, result=None):
    """Retain only independently valid, request-correlated capture evidence."""
    from .envelope import validate_result_for_request
    state = {"mutation_state": "unknown", "recovery_directory": None, "cluster": None}
    try:
        validate_postgresql_recovery_request(request)
        validate_result_for_request(request, result)
    except (TypeError, ValueError, AttributeError):
        return state
    raw = result.state
    mutation = raw.get("mutation_state")
    if type(mutation) is str and mutation in {"changed", "unknown"}:
        state["mutation_state"] = mutation
    elif mutation == "unchanged":
        try:
            validate_postgresql_recovery_result(request, result)
        except (TypeError, ValueError):
            pass
        else:
            state["mutation_state"] = "unchanged"
    try:
        cluster = validate_postgresql_cluster(raw.get("cluster"))
        if cluster != request.parameters["cluster"]:
            raise ProtocolError("PostgreSQL recovery target drift")
        state["cluster"] = dict(cluster)
        directory = raw.get("recovery_directory")
        if directory is not None:
            state["recovery_directory"] = validate_postgresql_recovery_directory(directory)
            # A retained directory is mutation even when classification is corrupt.
            if state["mutation_state"] == "unchanged":
                state["mutation_state"] = "unknown"
    except (TypeError, ValueError):
        if raw.get("recovery_directory") is not None and state["mutation_state"] == "unchanged":
            state["mutation_state"] = "unknown"
    return state


POSTGRESQL_CONFIGURATION_OPERATIONS = frozenset({"inspect_postgresql", "configure_postgresql"})


def validate_postgresql_configuration_request(request):
    """Validate narrow native configuration authority without controller imports."""
    from .envelope import HostRequest
    if (not isinstance(request, HostRequest) or request.operation not in POSTGRESQL_CONFIGURATION_OPERATIONS or
            request.expected_state or set(request.paths) != {"install_root", "backup_root"} or
            set(request.parameters) != {"package_track", "database", "cluster", "hba_sha256"}):
        raise ProtocolError("invalid PostgreSQL configuration request")
    roots = [PurePosixPath(request.paths[key]) for key in ("install_root", "backup_root")]
    if roots[0].is_relative_to(roots[1]) or roots[1].is_relative_to(roots[0]):
        raise ProtocolError("overlapping PostgreSQL configuration roots")
    parameters = request.parameters
    track = parameters["package_track"]
    validate_postgresql_context(track, False)
    database = parameters["database"]
    if (not isinstance(database, Mapping) or set(database) != {"host", "port", "role", "name"} or
            type(database["host"]) is not str or database["host"] not in {"127.0.0.1", "::1"} or
            type(database["port"]) is not int or not 0 < database["port"] < 65536 or
            any(type(database[key]) is not str or re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", database[key]) is None
                for key in ("role", "name"))):
        raise ProtocolError("invalid PostgreSQL configuration database")
    cluster = parameters["cluster"]
    if cluster is not None:
        validate_postgresql_cluster(cluster)
        if cluster["port"] != database["port"] or track is not None and cluster["version"] != track:
            raise ProtocolError("PostgreSQL configuration target mismatch")
    if type(parameters["hba_sha256"]) is not str or re.fullmatch(r"[0-9a-f]{64}", parameters["hba_sha256"]) is None:
        raise ProtocolError("invalid PostgreSQL staged digest")
    return request


def validate_postgresql_configuration_result(request, result):
    """Validate the complete correlated reply, including exact failure semantics."""
    from .envelope import validate_result_for_request
    validate_result_for_request(request, result)
    if request.operation not in POSTGRESQL_CONFIGURATION_OPERATIONS:
        raise ProtocolError("invalid PostgreSQL configuration operation")
    state = result.state
    inspection = request.operation == "inspect_postgresql"
    if result.outcome == "succeeded":
        keys = {"changed", "configuration_required"} if inspection else {"changed"}
        if (set(state) != keys or type(state["changed"]) is not bool or
                inspection and (state["changed"] or type(state["configuration_required"]) is not bool)):
            raise ProtocolError("invalid PostgreSQL configuration success")
        return result
    reason = state.get("reason")
    classification = {
        "invalid_request": {("refused", 2)}, "unsafe_state": {("refused", 10)},
        "lock_unavailable": {("retryable", 12)}, "internal_failure": {("retryable", 5)},
        "command_failed": {("retryable", 5), ("manual", 5)}, "recovery_required": {("manual", 10)},
    }
    if type(reason) is not str or reason not in classification:
        raise ProtocolError("invalid PostgreSQL configuration reason")
    keys = {"exit_code", "changed", "failed_boundary", "reason", "mutation_state"}
    if reason == "lock_unavailable":
        keys.add("locked")
    if (set(state) != keys or type(state["exit_code"]) is not int or
            (result.outcome, state["exit_code"]) not in classification[reason] or
            state["failed_boundary"] != "postgresql" or type(state["mutation_state"]) is not str or
            state["mutation_state"] not in {"unchanged", "changed", "unknown"} or
            type(state["changed"]) is not bool or state["changed"] != (state["mutation_state"] != "unchanged") or
            reason == "lock_unavailable" and state["locked"] is not True or
            (inspection or reason in {"invalid_request", "lock_unavailable"}) and state["mutation_state"] != "unchanged"):
        raise ProtocolError("invalid PostgreSQL configuration failure")
    return result
