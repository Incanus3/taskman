"""Host-local code-only rollback policy and recovery evidence."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
import os
from pathlib import Path
import subprocess

from taskman_ops.host_protocol import PROTOCOL_VERSION

from ..lifecycle import (
    ActivationRecord,
    LifecycleError,
    LifecycleLockContention,
    LifecycleStore,
    LifecycleWriteFailure,
    rollback_eligibility,
)
from ..legacy_result import OperationRequest as HostRequest, OperationResult as HostResult
from ..paths import ManagedPaths, PathAuthorityError
from ..verification import verify
from .legacy_backup import (
    BackupOperationFailure,
    create_validated_backup,
    prepare_backup_root,
    safe_secret,
)


_DATABASE_KEYS = frozenset({"host", "name", "port", "role"})
_FULL_PARAMETER_KEYS = frozenset(
    {"target_release_id", "credentials_path", "database", "verification"}
)


class _Failure(Exception):
    def __init__(
        self,
        stage: str,
        *,
        selected: str | None,
        service: str,
        backup_id: str | None = None,
        activation_id: str | None = None,
        changed: tuple[str, ...] = (),
        residue: tuple[str, ...] = (),
        recovery: tuple[str, ...] = (),
        verification: Mapping[str, object] | None = None,
    ) -> None:
        self.stage = stage
        self.selected = selected
        self.service = service
        self.backup_id = backup_id
        self.activation_id = activation_id
        self.changed = changed
        self.residue = residue
        self.recovery = recovery
        self.verification = {} if verification is None else verification


class _Inputs:
    def __init__(
        self,
        credentials: Path,
        database: dict[str, object],
        verification: dict[str, object],
    ) -> None:
        self.credentials = credentials
        self.database = database
        self.verification = verification


def rollback(request: HostRequest) -> HostResult:
    """Revalidate every crossed activation edge, then select and verify."""

    target = request.parameters.get("target_release_id")
    if type(target) is not str:
        return _refused(request)
    try:
        inputs = _inputs(request)
        paths = ManagedPaths.from_mapping(request.paths)
        store = LifecycleStore(paths)
        paths.validate_existing(owner_uid=store.owner_uid)
        store.require_release_directory(store.release_path(target))
        with store.exclusive_lifecycle_lock(operation="rollback"):
            records = store.read()
            store.require_release_directory(store.release_path(target))
            rerun = _completed_rerun(
                request,
                store,
                records,
                target,
                inputs,
            )
            if rerun is not None:
                return rerun
            current = records.current_release_id
            if request.expected_state["current_release_id"] != current or current is None:
                return _refused(request, target=target)
            eligible, _reason = rollback_eligibility(records, current, target)
            if not eligible:
                return _refused(request, target=target)
            return _execute(request, store, records, current, target, inputs)
    except LifecycleLockContention as error:
        return _locked(request, error)
    except _Failure as error:
        return _failed(request, target, error)
    except (LifecycleError, PathAuthorityError, OSError, ValueError):
        return _refused(request, target=target)


def _inputs(request: HostRequest) -> _Inputs:
    if (
        set(request.expected_state) != {"current_release_id"}
        or set(request.parameters) != _FULL_PARAMETER_KEYS
    ):
        raise ValueError("rollback request is incomplete")
    credentials = request.parameters["credentials_path"]
    database = request.parameters["database"]
    verification = request.parameters["verification"]
    if (
        type(credentials) is not str
        or not isinstance(database, Mapping)
        or set(database) != _DATABASE_KEYS
        or not isinstance(verification, Mapping)
    ):
        raise ValueError("rollback inputs are invalid")
    values = dict(database)
    if (
        not all(
            type(values[key]) is str and values[key]
            for key in ("host", "name", "role")
        )
        or type(values["port"]) is not int
        or not 0 < values["port"] < 65536
    ):
        raise ValueError("rollback database inputs are invalid")
    return _Inputs(Path(credentials), values, dict(verification))


def _execute(
    request: HostRequest,
    store: LifecycleStore,
    records: object,
    current: str,
    target: str,
    inputs: _Inputs,
) -> HostResult:
    changed: list[str] = []
    backup_id: str | None = None
    activation_id = f"activation-{request.operation_id.removeprefix('op-')}"
    selected: str | None = current
    service = "active"
    verification: Mapping[str, object] = {}
    selection_temporary = (
        store.paths.local(store.paths.install_root)
        / f".current-{request.operation_id}"
    )
    try:
        safe_secret(inputs.credentials, store.owner_uid)
        prepare_backup_root(store)
        backup, backup_changed = create_validated_backup(
            store,
            records,
            operation_id=request.operation_id,
            database=inputs.database,
            credentials=inputs.credentials,
            reason="pre-rollback",
            current_release_id=current,
            candidate_release_id=target,
        )
        backup_id = backup.backup_id
        if backup_changed:
            changed.append("backup")
        try:
            _service("stop")
            service = "stopped"
            changed.append("stop")
        except (OSError, subprocess.SubprocessError) as error:
            raise _Failure(
                "stop",
                selected=selected,
                service=_service_state(),
                backup_id=backup_id,
                changed=tuple(changed),
                recovery=_recovery(store),
            ) from error
        try:
            if selection_temporary.exists() or selection_temporary.is_symlink():
                raise OSError
            selection_temporary.symlink_to(store.release_path(target))
            os.replace(selection_temporary, store.current_link)
            selected = target
            changed.append("selection")
            _fsync_directory(store.paths.local(store.paths.install_root))
        except OSError as error:
            residue = _cleanup_selection(selection_temporary)
            raise _Failure(
                "selection",
                selected=_selected(store),
                service=service,
                backup_id=backup_id,
                changed=tuple(changed),
                residue=residue,
                recovery=_recovery(store),
            ) from error
        try:
            _service("start")
            service = "active"
            changed.append("start")
        except (OSError, subprocess.SubprocessError) as error:
            raise _Failure(
                "start",
                selected=selected,
                service=_service_state(),
                backup_id=backup_id,
                changed=tuple(changed),
                recovery=_recovery(store),
            ) from error
        try:
            verification = _verify_locked(request, target, inputs.verification)
        except _Failure as error:
            raise _Failure(
                error.stage,
                selected=error.selected,
                service=error.service,
                backup_id=backup_id,
                changed=tuple(changed),
                recovery=error.recovery,
                verification=error.verification,
            ) from error
        changed.append("verification")
        try:
            store.write_activation(
                ActivationRecord(
                    1,
                    activation_id,
                    current,
                    target,
                    datetime.now(UTC).replace(microsecond=0),
                    backup_id,
                    "no-change",
                )
            )
            changed.append("records")
        except LifecycleWriteFailure as error:
            residue = tuple(path.as_posix() for path in error.residue_paths)
            if error.effect.published:
                changed.append("records")
            raise _Failure(
                "records",
                selected=selected,
                service=service,
                backup_id=backup_id,
                activation_id=activation_id if error.effect.published else None,
                changed=tuple(changed),
                residue=residue,
                recovery=_recovery(store),
                verification=verification,
            ) from error
        except LifecycleError as error:
            raise _Failure(
                "records",
                selected=selected,
                service=service,
                backup_id=backup_id,
                changed=tuple(changed),
                recovery=_recovery(store),
                verification=verification,
            ) from error
    except BackupOperationFailure as error:
        nested_changed = tuple(
            dict.fromkeys((*changed, *error.changed_stages))
        )
        raise _Failure(
            error.stage,
            selected=selected,
            service=service,
            backup_id=(
                backup_id
                or (
                    f"backup-{request.operation_id.removeprefix('op-')}"
                    if error.changed_stages
                    else None
                )
            ),
            changed=nested_changed,
            residue=tuple(path.as_posix() for path in error.residue_paths),
            recovery=_recovery(store),
        ) from error

    return HostResult(
        PROTOCOL_VERSION,
        request.operation,
        request.operation_id,
        "succeeded",
        "records",
        tuple(changed),
        {
            "previous_release_id": current,
            "target_release_id": target,
            "selected_release_id": target,
            "backup_id": backup_id,
            "activation_id": activation_id,
            "service_state": "active",
            "database_state": "unchanged",
            "activation_recorded": True,
        },
        {},
        verification,
        (),
        (),
        (),
    )


def _verify_locked(
    request: HostRequest,
    target: str,
    settings: Mapping[str, object],
) -> Mapping[str, object]:
    result = verify(
        HostRequest(
            1,
            "verify",
            request.operation_id,
            {"expected_release_id": target},
            request.paths,
            settings,
        ),
        lifecycle_locked=True,
    )
    if result.outcome != "succeeded" or result.stage != "verified":
        raise _Failure(
            "verification",
            selected=target,
            service=_service_state(),
            verification=result.verification,
            recovery=(
                "inspect selected release and service verification before retrying",
            ),
        )
    return result.verification


def _completed_rerun(
    request: HostRequest,
    store: LifecycleStore,
    records: object,
    target: str,
    inputs: _Inputs,
) -> HostResult | None:
    activation_id = f"activation-{request.operation_id.removeprefix('op-')}"
    activation = next(
        (item for item in records.activations if item.activation_id == activation_id),
        None,
    )
    if activation is None:
        return None
    if (
        records.current_release_id != target
        or activation.previous_release_id
        != request.expected_state.get("current_release_id")
        or activation.candidate_release_id != target
        or activation.backup_id
        != f"backup-{request.operation_id.removeprefix('op-')}"
    ):
        return _refused(request, target=target)
    if _selected(store) != target:
        return _refused(request, target=target)
    verification = _verify_locked(request, target, inputs.verification)
    return HostResult(
        PROTOCOL_VERSION,
        request.operation,
        request.operation_id,
        "no_change",
        "already-current",
        (),
        {
            "previous_release_id": activation.previous_release_id,
            "target_release_id": target,
            "selected_release_id": target,
            "backup_id": activation.backup_id,
            "activation_id": activation.activation_id,
            "service_state": "active",
            "database_state": "unchanged",
            "activation_recorded": True,
        },
        {},
        verification,
        (),
        (),
        (),
    )


def _service(action: str) -> None:
    subprocess.run(
        ("systemctl", action, "taskman.service"),
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=60,
    )


def _service_state() -> str:
    try:
        completed = subprocess.run(
            (
                "systemctl",
                "show",
                "taskman.service",
                "--property=ActiveState",
                "--value",
            ),
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=15,
            text=True,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    value = completed.stdout.strip()
    if completed.returncode == 0 and value == "active":
        return "active"
    if value in {"inactive", "failed"}:
        return "stopped"
    return "unknown"


def _selected(store: LifecycleStore) -> str | None:
    try:
        target = store.current_link.resolve(strict=True)
        return (
            target.name
            if target.parent == store.release_root.resolve(strict=True)
            else None
        )
    except OSError:
        return None


def _cleanup_selection(path: Path) -> tuple[str, ...]:
    if not path.exists() and not path.is_symlink():
        return ()
    try:
        if not path.is_symlink():
            return (path.as_posix(),)
        path.unlink()
        _fsync_directory(path.parent)
        return ()
    except OSError:
        return (path.as_posix(),)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _recovery(store: LifecycleStore) -> tuple[str, ...]:
    return (
        "systemctl status taskman.service",
        f"readlink -f {store.paths.install_root}/current",
        "journalctl --no-pager --unit taskman.service --lines=100",
    )


def _refused(
    request: HostRequest,
    *,
    target: str | None = None,
) -> HostResult:
    lifecycle = (
        {}
        if target is None
        else {"rollback_eligible": False, "target_release_id": target}
    )
    return HostResult(
        PROTOCOL_VERSION,
        request.operation,
        request.operation_id,
        "refused",
        "rollback-preflight",
        (),
        lifecycle,
        {},
        {},
        (),
        ("inspect the activation history before retrying",),
        (),
    )


def _failed(request: HostRequest, target: str, error: _Failure) -> HostResult:
    return HostResult(
        PROTOCOL_VERSION,
        request.operation,
        request.operation_id,
        "failed",
        error.stage,
        error.changed,
        {
            "previous_release_id": request.expected_state.get("current_release_id"),
            "target_release_id": target,
            "selected_release_id": error.selected,
            "backup_id": error.backup_id,
            "activation_id": error.activation_id,
            "service_state": error.service,
            "database_state": "unchanged",
            "activation_recorded": error.activation_id is not None,
        },
        {},
        error.verification,
        error.residue,
        error.recovery or ("inspect rollback state before retrying",),
        ("rollback operation did not complete",),
    )


def _locked(
    request: HostRequest,
    error: LifecycleLockContention,
) -> HostResult:
    runtime_state = (
        {} if error.holder is None else {"lock_holder": error.holder.to_mapping()}
    )
    return HostResult(
        PROTOCOL_VERSION,
        request.operation,
        request.operation_id,
        "refused",
        "lifecycle-lock",
        (),
        {},
        runtime_state,
        {},
        (),
        ("wait for the recorded lifecycle operation to finish and retry",),
        (),
    )


__all__ = ["rollback"]
