"""Lifecycle and signal ownership for bounded native PostgreSQL configuration."""
from contextlib import contextmanager
import signal
from threading import Event
import time

from taskman_ops.host_protocol import HostResult, ProtocolError
from taskman_ops.host_protocol.envelope import validate_result_for_request
from taskman_ops.host_protocol.postgresql import (
    validate_postgresql_configuration_request, validate_postgresql_configuration_result,
)
from taskman_ops.host_helper.lock import LifecycleLockContention, lifecycle_lock
from taskman_ops.host_helper.paths import ManagedPaths
from taskman_ops.host_helper.postgresql_configuration import (
    PostgreSQLConfigurationError, inspect_configuration, configure_cluster,
)


def _failure(request, reason, mutation="unchanged", *, manual=False):
    outcome, code = {
        "invalid_request": ("refused", 2), "unsafe_state": ("refused", 10),
        "lock_unavailable": ("retryable", 12), "internal_failure": ("retryable", 5),
        "command_failed": ("manual" if manual else "retryable", 5),
        "recovery_required": ("manual", 10),
    }[reason]
    if request.operation == "inspect_postgresql":
        mutation = "unchanged"
    state = dict(exit_code=code, changed=mutation != "unchanged", mutation_state=mutation,
                 failed_boundary="postgresql", reason=reason)
    if reason == "lock_unavailable":
        state["locked"] = True
    return HostResult.for_request(request, outcome, "PostgreSQL configuration failed", state)


def postgresql_configuration_failure_result(request, result=None):
    """Project only matching internal positive proof; never salvage wire replies."""
    mutation = "unknown"
    try:
        validate_result_for_request(request, result)
    except (TypeError, ValueError, AttributeError):
        pass
    else:
        try:
            validate_postgresql_configuration_result(request, result)
        except (TypeError, ValueError, AttributeError):
            if (result.outcome != "succeeded" and
                    type(result.state.get("mutation_state")) is str and
                    result.state["mutation_state"] == "changed"):
                mutation = "changed"
        else:
            mutation = ("changed" if result.state["changed"] else "unchanged") if result.outcome == "succeeded" else result.state["mutation_state"]
    return _failure(request, "internal_failure", mutation)


@contextmanager
def _cancellation_handlers(cancellation):
    previous = {}
    def cancel(_number, _frame):
        cancellation.set()
    try:
        for number in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
            previous[number] = signal.signal(number, cancel)
        yield
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


def _run(request):
    try:
        validate_postgresql_configuration_request(request)
    except ProtocolError:
        return _failure(request, "invalid_request")
    deadline = time.monotonic() + 600.0
    cancellation = Event()
    try:
        with _cancellation_handlers(cancellation):
            with lifecycle_lock(ManagedPaths.from_mapping(request.paths), timeout_seconds=5.0):
                inspect = request.operation == "inspect_postgresql"
                changed = (inspect_configuration if inspect else configure_cluster)(
                    request.parameters, deadline=deadline, cancellation=cancellation)
                state = {"changed": False, "configuration_required": changed} if inspect else {"changed": changed}
                result = HostResult.for_request(request, "succeeded", "PostgreSQL configuration complete", state)
                return validate_postgresql_configuration_result(request, result)
    except LifecycleLockContention:
        return _failure(request, "lock_unavailable")
    except PostgreSQLConfigurationError as error:
        return _failure(request, error.reason, error.mutation_state, manual=error.manual)
    except (Exception, SystemExit):
        return postgresql_configuration_failure_result(request)


def inspect_postgresql(request):
    return _run(request)


def configure_postgresql(request):
    return _run(request)
