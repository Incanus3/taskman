from __future__ import annotations

from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from taskman_ops.host_helper.operations import discover as discover_module
from taskman_ops.host_helper.backup_protection import BackupProtection
from taskman_ops.host_helper.records import BackupRecord, ReleaseRecord, SelectionRecord
from taskman_ops.host_helper.restore_target import RestoreTarget, restore_target_sha256
from taskman_ops.host_helper.state import HostState, StateAmbiguityError
from taskman_ops.host_protocol import HostRequest
from taskman_ops.releases.identifiers import build_release_id
from taskman_ops.releases.manifests import BUILDER_BASE_DIGEST, BUILDER_BASE_TAG, ArtifactManifest
from taskman_ops.services.systemd import build_systemd_plan
from taskman_ops.provisioning import ProvisioningInputs, validate_preconvergence_authority
from tests.support.caddy import controller_plan, authority_result, prepared_result
from tests.support.environments import environment_config


CORRELATION = "op-0123456789abcdef0123456789abcdef"
RESOURCE_DIGESTS = {
    "taskman_service": "1" * 64,
    "backup_environment": "2" * 64,
    "backup_service": "3" * 64,
    "backup_timer": "4" * 64,
}
REVISION = "a" * 40
ARTIFACT_SHA256 = "b" * 64
RELEASE_ID = build_release_id("0.2.0", REVISION, artifact_sha256=ARTIFACT_SHA256, source_dirty=False)
SELECTED_AT = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)



def _pg_authority(state):
    return {"branch": "existing", "cluster": {"version": "16", "name": "main", "port": 5432,
        "data_directory": "/var/lib/postgresql/16/main", "config_file": "/etc/postgresql/16/main/postgresql.conf",
        "hba_file": "/etc/postgresql/16/main/pg_hba.conf"}, "role": "compatible" if state == "ready" else "absent",
        "database": "compatible" if state == "ready" else "absent", "access": "unproven"}

def _request(*, mode: str = "strict", backup_id: str | None = None) -> HostRequest:
    parameters: dict[str, object] = {
        "credentials_path": "/etc/taskman/pgpass",
        "database": {"host": "127.0.0.1", "port": 5432, "role": "taskman", "name": "taskman"},
        "mode": mode,
    }
    if mode == "provision":
        parameters.update(postgres_package_track=None, force_postgresql_config=False)
    if backup_id is not None:
        parameters["backup_id"] = backup_id
    return HostRequest(3, "discover", CORRELATION, {}, {"install_root": "/opt/taskman", "backup_root": "/var/backups/taskman"}, parameters)


def _release(*, migrations: tuple[dict[str, str], ...] = ()) -> ReleaseRecord:
    manifest = ArtifactManifest.from_mapping(
        {
            "schema_version": 3, "application": "taskman", "application_version": "0.2.0",
            "source_revision": REVISION, "release_id": RELEASE_ID, "built_at": "2026-09-05T12:00:00Z",
            "target_os": "ubuntu26.04", "architecture": "amd64", "otp_version": "29.0.6",
            "elixir_version": "1.20.4", "node_version": "22.22.1", "hex_version": "2.5.1",
            "rebar3_version": "3.24.0", "builder_base_tag": BUILDER_BASE_TAG,
            "builder_base_digest": BUILDER_BASE_DIGEST, "migrations": list(migrations), "top_level": "taskman",
            "artifact_sha256": ARTIFACT_SHA256, "source_dirty": False,
        }
    )
    return ReleaseRecord(RELEASE_ID, REVISION, ARTIFACT_SHA256, migrations, 2, manifest)


def _state(*, migrations: tuple[int, ...] = (20260905120000,)) -> HostState:
    selection = SelectionRecord(RELEASE_ID, None, None, SELECTED_AT, 2, None, ())
    return HostState(
        selected_release_id=RELEASE_ID, releases=(_release(),), backups=(), selections=(selection,),
        applied_migrations=migrations, service_state="running", database_state="ready",
        temporary_paths=(), warnings=(),
    )


def _restore_state(*, migrations: tuple[int, ...] = (20260905120000,)) -> HostState:
    migration = {
        "filename": "20260905120000_create_tasks.exs",
        "sha256": "c" * 64,
    }
    backup = BackupRecord(
        "backup-" + "c" * 32,
        SELECTED_AT,
        "d" * 64,
        RELEASE_ID,
        (20260905120000,),
        1024,
    )
    return HostState(
        **{
            **_state(migrations=migrations).__dict__,
            "releases": (_release(migrations=(migration,)),),
            "backups": (backup,),
        }
    )


def _install_observer(monkeypatch: pytest.MonkeyPatch, observed: HostState) -> None:
    monkeypatch.setattr(discover_module, "require_scram_client", lambda: None)
    monkeypatch.setattr(discover_module, "prove_scram_access", lambda *_: None)
    monkeypatch.setattr(discover_module, "_observe_postgresql_authority", lambda *_args: _pg_authority(observed.database_state))
    monkeypatch.setattr(discover_module, "validate_credentials", lambda *_args: None)
    monkeypatch.setattr(discover_module, "observe_database_state", lambda *_args: {"state": observed.database_state, "applied_migrations": observed.applied_migrations})
    monkeypatch.setattr(discover_module, "observe_database_state_or_empty", lambda *_args: {"state": observed.database_state, "applied_migrations": observed.applied_migrations, "initial_empty": not observed.applied_migrations})
    monkeypatch.setattr(discover_module, "observe_database_state_or_empty_as_admin", lambda *_args: {"state": observed.database_state, "applied_migrations": observed.applied_migrations, "initial_empty": not observed.applied_migrations})
    monkeypatch.setattr(discover_module, "observe_host_state", lambda *_args, **_kwargs: observed)
    if observed.backups:
        def validated_backup(_paths, backup_id):
            return next(
                item for item in observed.backups if item.backup_id == backup_id
            )

        monkeypatch.setattr(
            discover_module,
            "_validate_restore_backup",
            validated_backup,
        )
    monkeypatch.setattr(discover_module, "lifecycle_lock", lambda *_args, **_kwargs: nullcontext())
    monkeypatch.setattr(
        discover_module,
        "_scheduler_facts",
        lambda *_args: {
            "scheduled_backup_sha256": "d" * 64,
            "backup_timer_enabled": True,
            "backup_timer_state": "active",
        },
        raising=False,
    )


def _listing_request(operation: str, cursor: object = None) -> HostRequest:
    return HostRequest(
        3,
        operation,
        CORRELATION,
        {},
        {"install_root": "/opt/taskman", "backup_root": "/var/backups/taskman"},
        {"cursor": cursor},
    )


def _inventory_sha256(operation: str, records: list[dict[str, object]]) -> str:
    payload = json.dumps(
        {"operation": operation, "records": records},
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def test_strict_v3_discovery_projects_only_the_exact_bounded_common_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Returning full inventories again would bypass the dedicated page protocol."""
    observed = _state()
    _install_observer(monkeypatch, observed)

    result = discover_module.discover(_request())

    selection = observed.latest_successful_selection.to_mapping()
    selection["recovery_backup_ids"] = ()
    assert result.outcome == "succeeded"
    assert result.state == {
        "selected_release_id": RELEASE_ID,
        "last_successful_selection_id": observed.latest_successful_selection_filename,
        "last_successful_selection": selection,
        "previous_successful_selection": None,
        "applied_migrations": (20260905120000,),
        "service_state": "running", "database_state": "ready",
    }


def test_strict_v3_discovery_round_trips_the_full_512_version_observation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Truncating a valid observed schema would make later migration admission unsound."""
    _install_observer(monkeypatch, _state(migrations=tuple(range(512))))

    result = discover_module.discover(_request())

    assert result.outcome == "succeeded"
    assert len(result.state["applied_migrations"]) == 512


@pytest.mark.parametrize(
    ("mode", "backup_id"),
    (("restore", None), ("strict", "backup-" + "c" * 32), ("unknown", None)),
)
def test_v3_discovery_refuses_malformed_mode_specific_parameters(monkeypatch: pytest.MonkeyPatch, mode: str, backup_id: str | None) -> None:
    """Accepting a partial restore shape could inspect a different recovery input."""
    _install_observer(monkeypatch, _state())

    result = discover_module.discover(_request(mode=mode, backup_id=backup_id))

    assert result.outcome == "refused"
    assert result.state == {}


def test_restore_discovery_returns_exact_database_shapes_and_flat_binding(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nesting or omitting binding identity would prevent exact apply-time drift checks."""
    _install_observer(monkeypatch, _restore_state())
    database_state = {
        "canonical": {"oid": 101, "owner": "taskman", "migration_table_present": True, "applied_migrations": (20260905120000,)},
        "temporary": None,
        "retired": None,
    }
    monkeypatch.setattr(
        discover_module, "observe_restore_databases", lambda *_args: database_state
    )

    result = discover_module.discover(
        _request(mode="restore", backup_id="backup-" + "c" * 32)
    )

    assert result.outcome == "succeeded"
    assert result.state["restore_target"] is None
    assert result.state["restore_database_state"] == database_state
    assert result.state["applied_migrations"] == (20260905120000,)
    assert result.state["scheduled_backup_sha256"] == "d" * 64
    assert "downgrade_baseline_sha256" not in result.state


def test_restore_discovery_adds_digest_to_the_flat_validated_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A nested record or persisted digest would break the exact restore discovery contract."""

    target = RestoreTarget(
        1,
        "backup-" + "c" * 32,
        "d" * 64,
        RELEASE_ID,
        None,
        RELEASE_ID,
        101,
        None,
        True,
        "backup-" + "e" * 32,
        None,
        ({"backup_id": "backup-" + "e" * 32, "attempt_number": 0},),
    )
    restore_state = _restore_state()
    safety = BackupRecord(
        target.safety_backup_id,
        SELECTED_AT,
        "e" * 64,
        RELEASE_ID,
        (20260905120000,),
        1024,
    )
    observed = HostState(
        **{
            **restore_state.__dict__,
            "backups": (*restore_state.backups, safety),
            "restore_target": target,
        }
    )
    _install_observer(monkeypatch, observed)
    monkeypatch.setattr(
        discover_module,
        "observe_restore_databases",
        lambda *_args: {
            "canonical": {"oid": 101, "owner": "taskman", "migration_table_present": True, "applied_migrations": (20260905120000,)},
            "temporary": None,
            "retired": None,
        },
    )

    result = discover_module.discover(
        _request(mode="restore", backup_id="backup-" + "c" * 32)
    )

    assert result.outcome == "succeeded"
    projected = result.state["restore_target"]
    assert set(projected) == {
        "schema_version", "backup_id", "dump_sha256", "source_release_id",
        "base_selection_id", "observed_previous_release_id", "original_database_oid",
        "restored_database_oid", "temporary_creation_pending", "safety_backup_id",
        "replacement", "safety_backup_attempts", "sha256",
    }
    assert projected["backup_id"] == "backup-" + "c" * 32
    assert projected["original_database_oid"] == 101
    assert projected["sha256"] == restore_target_sha256(target)
    assert "record" not in projected


def test_restore_discovery_refuses_failed_database_observation_instead_of_returning_nulls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Null database entries are proof of absence, never an observation fallback."""

    from taskman_ops.host_helper.restore_database import RestoreDatabaseError

    _install_observer(monkeypatch, _restore_state())
    monkeypatch.setattr(
        discover_module,
        "observe_restore_databases",
        lambda *_args: (_ for _ in ()).throw(RestoreDatabaseError("failed")),
    )

    result = discover_module.discover(
        _request(mode="restore", backup_id="backup-" + "c" * 32)
    )

    assert result.outcome == "refused"
    assert result.state == {}


def test_restore_discovery_uses_null_top_level_migrations_for_a_missing_canonical_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Temporary migration evidence must never masquerade as canonical live schema."""

    _install_observer(monkeypatch, _restore_state(migrations=()))
    monkeypatch.setattr(
        discover_module,
        "observe_restore_databases",
        lambda *_args: {
            "canonical": {"oid": 101, "owner": "taskman", "migration_table_present": False, "applied_migrations": None},
            "temporary": {"oid": 202, "owner": "taskman", "migration_table_present": True, "applied_migrations": (20260905120000,)},
            "retired": None,
        },
    )

    result = discover_module.discover(
        _request(mode="restore", backup_id="backup-" + "c" * 32)
    )

    assert result.outcome == "succeeded"
    assert result.state["applied_migrations"] is None


def test_deploy_discovery_projects_protection_scheduler_and_downgrade_digests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Omitting compact authority would force deploy back to whole inventories."""
    protection = BackupProtection(
        1,
        "backup-" + "c" * 32,
        None,
        RELEASE_ID,
        0,
        SELECTED_AT,
    )
    observed = HostState(
        **{
            **_state().__dict__,
            "backup_protections": (protection,),
        }
    )
    _install_observer(monkeypatch, observed)

    result = discover_module.discover(_request(mode="deploy"))

    protection_rows = [protection.to_mapping()]
    protection_digest = hashlib.sha256(
        json.dumps(protection_rows, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")
    ).hexdigest()
    baseline_digest = hashlib.sha256(
        json.dumps([RELEASE_ID], ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")
    ).hexdigest()
    assert result.outcome == "succeeded"
    assert result.state["backup_protections"] == tuple(protection_rows)
    assert result.state["independently_held_backup_ids"] == ()
    assert result.state["backup_protection_sha256"] == protection_digest
    assert result.state["downgrade_baseline_sha256"] == baseline_digest
    assert result.state["scheduled_backup_sha256"] == "d" * 64
    assert result.state["backup_timer_enabled"] is True
    assert result.state["backup_timer_state"] == "active"
    assert "releases" not in result.state
    assert "backups" not in result.state


def test_provision_discovery_uses_direct_empty_database_observation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing migration table is admissible only through the empty-schema proof."""

    observed = _state(migrations=())
    _install_observer(monkeypatch, observed)
    calls: list[str] = []
    monkeypatch.setattr(
        discover_module,
        "observe_database_state",
        lambda *_args: pytest.fail("provision must not require an existing migration table"),
    )
    monkeypatch.setattr(
        discover_module,
        "observe_database_state_or_empty",
        lambda *_args: calls.append("empty-proof") or {"state": "ready", "applied_migrations": ()},
    )

    (tmp_path / "pgpass").write_bytes(b"protected-by-fixture")
    result = discover_module.discover(_local_discovery_request(tmp_path))

    assert result.outcome == "succeeded"
    assert calls == ["empty-proof"]


def _local_discovery_request(tmp_path: Path, *, mode: str = "provision") -> HostRequest:
    original = _request(mode=mode, backup_id="backup-" + "c" * 32 if mode == "restore" else None)
    return HostRequest(
        3, "discover", CORRELATION, {},
        {"install_root": str(tmp_path / "install"), "backup_root": str(tmp_path / "backups")},
        {**original.parameters, "credentials_path": str(tmp_path / "pgpass")},
    )


def _missing_scheduler(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(discover_module, "require_scram_client", lambda: None)
    monkeypatch.setattr(discover_module, "prove_scram_access", lambda *_: None)
    monkeypatch.setattr(discover_module, "_scheduler_facts", lambda *_: {
        "scheduled_backup_sha256": None, "backup_timer_enabled": False, "backup_timer_state": "inactive",
    })


def test_public_provision_discovery_admits_absent_identities_without_pgpass(tmp_path, monkeypatch):
    _install_postgresql_authority_commands(tmp_path, monkeypatch, "#!/bin/sh\ncat >/dev/null\n")
    _missing_scheduler(monkeypatch)

    result = discover_module.discover(_local_discovery_request(tmp_path))

    assert result.outcome == "succeeded"
    assert result.state["database_state"] == "absent"
    assert result.state["applied_migrations"] == ()
    assert not (tmp_path / "pgpass").exists()


def test_public_provision_discovery_admits_retained_role_without_database(tmp_path, monkeypatch):
    _install_postgresql_authority_commands(
        tmp_path, monkeypatch,
        '#!/bin/sh\nsql=$(cat)\ncase "$sql" in *pg_roles*) printf "1\\n" ;; esac\n',
    )
    _missing_scheduler(monkeypatch)
    monkeypatch.setattr(discover_module, "validate_credentials", lambda *_: None)
    monkeypatch.setattr(discover_module, "observe_database_state_or_empty", lambda *_: {
        "state": "ready", "applied_migrations": (), "initial_empty": True,
    })

    result = discover_module.discover(_local_discovery_request(tmp_path))

    assert result.outcome == "succeeded"
    assert result.state["postgresql"]["role"] == "compatible"
    assert result.state["database_state"] == "absent"
    assert not (tmp_path / "pgpass").exists()


def test_public_provision_discovery_uses_admin_template_proof_when_ready_pgpass_is_missing(tmp_path, monkeypatch):
    _missing_scheduler(monkeypatch)
    monkeypatch.setattr(discover_module, "_observe_postgresql_authority", lambda *_: _pg_authority("ready"))
    monkeypatch.setattr(discover_module, "observe_database_state_or_empty_as_admin", lambda *_: {
        "state": "ready", "applied_migrations": (), "initial_empty": True,
    })

    result = discover_module.discover(_local_discovery_request(tmp_path))

    assert result.outcome == "succeeded"
    assert result.state["database_state"] == "ready"
    assert result.state["applied_migrations"] == ()
    assert not (tmp_path / "pgpass").exists()


@pytest.mark.parametrize("mode", ["strict", "deploy", "restore"])
def test_other_public_discovery_modes_still_require_pgpass(tmp_path, mode):
    result = discover_module.discover(_local_discovery_request(tmp_path, mode=mode))
    assert result.outcome == "refused"


@pytest.mark.parametrize("failure,expected", [(None, "succeeded"), ("credentials", "refused"), ("authentication", "manual")])
def test_public_ready_provision_discovery_retains_credential_and_application_authentication_guards(tmp_path, monkeypatch, failure, expected):
    _missing_scheduler(monkeypatch)
    (tmp_path / "pgpass").write_bytes(b"retained-credential")
    calls = []
    monkeypatch.setattr(discover_module, "_observe_postgresql_authority", lambda *_: _pg_authority("ready"))
    monkeypatch.setattr(discover_module, "observe_database_state_or_empty_as_admin", lambda *_: pytest.fail("present credentials require application authentication"))

    def credentials(path):
        calls.append("credentials")
        if failure == "credentials":
            raise ValueError("incompatible protected file")

    def application(*_args):
        calls.append("authentication")
        if failure == "authentication":
            raise discover_module.CommandError("application authentication failed")
        return {"state": "ready", "applied_migrations": (20260905120000,), "initial_empty": False}

    monkeypatch.setattr(discover_module, "validate_credentials", credentials)
    monkeypatch.setattr(discover_module, "observe_database_state_or_empty", application)
    result = discover_module.discover(_local_discovery_request(tmp_path))
    assert result.outcome == expected
    assert calls == (["credentials"] if failure == "credentials" else ["credentials", "authentication"])
    if expected == "succeeded":
        assert result.state["applied_migrations"] == (20260905120000,)


def test_public_provision_discovery_refuses_conflicting_admin_observation(tmp_path, monkeypatch):
    _missing_scheduler(monkeypatch)
    monkeypatch.setattr(discover_module, "_observe_postgresql_authority", lambda *_: _pg_authority("ready"))
    monkeypatch.setattr(discover_module, "observe_database_state_or_empty_as_admin", lambda *_: {
        "state": "absent", "applied_migrations": (), "initial_empty": False,
    })
    result = discover_module.discover(_local_discovery_request(tmp_path))
    assert result.outcome == "refused"


def test_preconvergence_authority_uses_the_same_locked_record_observer_before_pyinfra(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The early observer must validate records/current, not reparse their JSON."""

    observed = _state(migrations=())
    _install_observer(monkeypatch, observed)
    request = HostRequest(
        3,
        "provision_authority",
        CORRELATION,
        {},
        {"install_root": "/opt/taskman", "backup_root": "/var/backups/taskman"},
        {
            "database": {"host": "127.0.0.1", "port": 5432, "role": "taskman", "name": "taskman"},
            "postgres_package_track": None, "force_postgresql_config": False,
            "resource_digests": RESOURCE_DIGESTS,
        },
    )
    calls: list[str] = []
    monkeypatch.setattr(
        discover_module,
        "_observe_postgresql_authority",
        lambda *_args: calls.append("postgres") or _pg_authority("ready"),
        raising=False,
    )

    result = discover_module.provision_authority(request)

    assert result.outcome == "succeeded"
    assert result.state["authority"] == "validated"
    assert result.state["selected_release_id"] == RELEASE_ID
    assert result.state["last_successful_selection_id"] is not None
    assert result.state["applied_migrations"] == ()
    assert result.state["backup_protection_sha256"] == hashlib.sha256(b"[]").hexdigest()
    assert result.state["installed_release_count"] == 1
    assert len(result.state["installed_release_sha256"]) == 64
    assert calls == ["postgres"]


def test_preconvergence_authority_binds_live_partial_migrations_before_pyinfra(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replacing a partial prefix with an empty tuple would misplan its backup."""

    observed = _state(migrations=())
    _install_observer(monkeypatch, observed)
    monkeypatch.setattr(discover_module, "_observe_postgresql_authority", lambda *_args: _pg_authority("ready"))
    monkeypatch.setattr(
        discover_module,
        "observe_database_state_or_empty",
        lambda *_args: {"state": "ready", "applied_migrations": (20260905120000,), "initial_empty": False},
    )
    monkeypatch.setattr(
        discover_module,
        "observe_database_state_or_empty_as_admin",
        lambda *_args: {"state": "ready", "applied_migrations": (20260905120000,), "initial_empty": False},
    )
    result = discover_module.provision_authority(
        HostRequest(
            3, "provision_authority", CORRELATION, {},
            {"install_root": "/opt/taskman", "backup_root": "/var/backups/taskman"},
            {"database": {"host": "127.0.0.1", "port": 5432, "role": "taskman", "name": "taskman"}, "postgres_package_track": None, "force_postgresql_config": False, "resource_digests": RESOURCE_DIGESTS},
        )
    )

    assert result.outcome == "succeeded"
    assert result.state["applied_migrations"] == (20260905120000,)


def test_preconvergence_authority_refuses_existing_resource_digest_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed = _state(migrations=())
    _install_observer(monkeypatch, observed)
    postgres_calls: list[str] = []
    monkeypatch.setattr(
        discover_module,
        "_observe_postgresql_authority",
        lambda *_args: postgres_calls.append("postgres") or _pg_authority("ready"),
    )
    service = tmp_path / "taskman.service"
    expected = b"expected taskman service"
    service.write_bytes(expected)
    service.chmod(0o644)
    original = discover_module._validate_managed_resources
    monkeypatch.setattr(
        discover_module.pwd,
        "getpwnam",
        lambda _name: SimpleNamespace(pw_uid=os.geteuid()),
    )
    monkeypatch.setattr(
        discover_module.grp,
        "getgrnam",
        lambda _name: SimpleNamespace(gr_gid=os.getegid()),
    )

    def validate(paths, digests, **kwargs):
        monkeypatch.setattr(discover_module, "Path", lambda value: service if value == "/etc/systemd/system/taskman.service" else Path(value))
        return original(paths, digests, **kwargs)

    monkeypatch.setattr(discover_module, "_validate_managed_resources", validate)
    request = HostRequest(
        3, "provision_authority", CORRELATION, {},
        {"install_root": "/opt/taskman", "backup_root": "/var/backups/taskman"},
        {
            "database": {
                "host": "127.0.0.1", "port": 5432,
                "role": "taskman", "name": "taskman",
            },
            "postgres_package_track": None, "force_postgresql_config": False,
            "resource_digests": {
                **RESOURCE_DIGESTS,
                "taskman_service": hashlib.sha256(expected).hexdigest(),
            },
        },
    )
    matching = discover_module.provision_authority(request)
    service.write_bytes(b"foreign")
    result = discover_module.provision_authority(request)

    assert matching.outcome == "succeeded"
    assert result.outcome == "refused"
    assert postgres_calls == ["postgres"]


def test_preconvergence_authority_preserves_database_absence_without_empty_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fresh database creation must not be mislabeled as a proved empty schema."""

    observed = _state(migrations=())
    _install_observer(monkeypatch, observed)
    monkeypatch.setattr(discover_module, "_observe_postgresql_authority", lambda *_args: _pg_authority("absent"))
    monkeypatch.setattr(
        discover_module,
        "observe_database_state_or_empty",
        lambda *_args: pytest.fail("an absent database has no schema to inspect"),
    )
    result = discover_module.provision_authority(
        HostRequest(
            3, "provision_authority", CORRELATION, {},
            {"install_root": "/opt/taskman", "backup_root": "/var/backups/taskman"},
            {"database": {"host": "127.0.0.1", "port": 5432, "role": "taskman", "name": "taskman"}, "postgres_package_track": None, "force_postgresql_config": False, "resource_digests": RESOURCE_DIGESTS},
        )
    )

    assert result.outcome == "succeeded"
    assert result.state["database_state"] == "absent"
    assert result.state["initial_database_empty"] is False


def test_scheduler_facts_represent_a_missing_timer_as_planned_first_install_delta(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fresh provisioning must distinguish missing scheduler resources from ambiguity."""

    paths = discover_module.ManagedPaths.from_mapping(
        {"install_root": (tmp_path / "install").as_posix(), "backup_root": (tmp_path / "backups").as_posix()}
    )
    properties = {
        "LoadState": "not-found",
        "UnitFileState": "",
        "ActiveState": "inactive",
    }
    monkeypatch.setattr(discover_module, "_systemd_property", properties.__getitem__)

    assert discover_module._scheduler_facts(paths) == {
        "scheduled_backup_sha256": None,
        "backup_timer_enabled": False,
        "backup_timer_state": "inactive",
    }


def test_real_provision_authority_projection_passes_the_production_controller_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The packaged helper's exact projection must be consumable before pyinfra."""

    observed = _state(migrations=())
    _install_observer(monkeypatch, observed)
    monkeypatch.setattr(discover_module, "_observe_postgresql_authority", lambda *_args: _pg_authority("ready"))
    helper_result = discover_module.provision_authority(
        HostRequest(
            3, "provision_authority", CORRELATION, {},
            {"install_root": "/opt/taskman", "backup_root": "/var/backups/taskman"},
            {"database": {"host": "127.0.0.1", "port": 5432, "role": "taskman", "name": "taskman"}, "postgres_package_track": None, "force_postgresql_config": False, "resource_digests": RESOURCE_DIGESTS},
        )
    )
    import taskman_ops.workflows.helper as helper_module

    monkeypatch.setattr(helper_module, "run_request", lambda *_args: helper_result)
    config = environment_config()
    inputs = ProvisioningInputs(
        config=config,
        systemd_plan=build_systemd_plan(config),
        caddy_plan=controller_plan(),
        runtime_environment=b"runtime", pgpass=b"pgpass", role_password_input=b"password",
    )

    remote = SimpleNamespace(run=lambda *_a, **_k: SimpleNamespace(succeeded=True))
    assert validate_preconvergence_authority(remote, inputs) == helper_result.state


def _install_postgresql_authority_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runuser: str
) -> None:
    # Each native command receives the real argv; only host-specific identity
    # files are synthetic. SQL still arrives on stdin with psql variables.
    original = tmp_path / "query-runuser"
    original.write_text(runuser)
    original.chmod(0o755)
    scripts = {
        "pg_lsclusters": "#!/bin/sh\nprintf '%s\\n' '16 main 5432 online postgres /var/lib/postgresql/16/main /var/log/postgresql/16.log'\n",
        "pg_conftool": "#!/bin/sh\ncase \"$*\" in *hba_file*) printf '/etc/postgresql/16/main/pg_hba.conf\\n';; *) printf '/var/lib/postgresql/16/main\\n';; esac\n",
        "cat": "#!/bin/sh\ncase \"$*\" in */postmaster.pid*) printf '42\\n/var/lib/postgresql/16/main\\n1\\n5432\\n/var/run/postgresql\\n';; *) exec /bin/cat \"$@\";; esac\n",
        "readlink": "#!/bin/sh\ncase \"$*\" in */proc/*) printf '%s\\n' /usr/lib/postgresql/16/bin/postgres;; *) for path do :; done; printf '%s\\n' \"$path\";; esac\n",
        "stat": "#!/bin/sh\ncase \"$*\" in */proc/*) printf 'postgres:postgres\\n';; */var/lib/*) printf 'directory:postgres:postgres:700\\n';; *) printf 'regular file:postgres:postgres:640\\n';; esac\n",
        "ss": '#!/bin/sh\nprintf \'%s\\n\' \'LISTEN 0 100 127.0.0.1:5432 *:* users:(("postgres",pid=42,fd=5))\'\n',
        "runuser": '#!/bin/sh\nsql=$(/bin/cat)\ncase "$sql" in *current_setting*) printf "16|/var/lib/postgresql/16/main|/etc/postgresql/16/main/postgresql.conf|/etc/postgresql/16/main/pg_hba.conf|5432\\n";; *) printf "%s\\n" "$sql" | ' + str(original) + ' "$@";; esac\n',
    }
    for name, source in scripts.items():
        path = tmp_path / name
        path.write_text(source)
        path.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")


def test_preconvergence_postgresql_observer_uses_a_nonempty_any_track_selector_at_the_command_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unspecified package track must still run the broad single-cluster probe."""

    _install_postgresql_authority_commands(
        tmp_path,
        monkeypatch,
        "#!/bin/sh\n"
        "[ \"$1\" = -u ] && [ \"$2\" = postgres ] && [ \"$3\" = -- ] || exit 97\n"
        "case \"$*\" in *'--file=-'*) ;; *) exit 98 ;; esac\n"
        "sql=$(cat)\n"
        "case \"$sql\" in *pg_roles*) printf '1\\n' ;; *pg_database*) printf '1\\n' ;; *) exit 99 ;; esac\n",
    )

    assert discover_module._observe_postgresql_authority(
        {"host": "127.0.0.1", "port": 5432, "role": "taskman", "name": "taskman"}, None
    )["database"] == "compatible"


def test_preconvergence_postgresql_observer_provides_identity_sql_through_psql_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """psql variables in the role and database proofs expand from a script input stream."""

    _install_postgresql_authority_commands(
        tmp_path,
        monkeypatch,
        "#!/bin/sh\n"
        "case \"$*\" in *'--file=-'*);; *) exit 97;; esac\n"
        "case \"$*\" in *'--command'*) exit 98;; esac\n"
        "sql=$(cat)\n"
        "case \"$sql:$*\" in\n"
        "  *\"pg_roles WHERE rolname = :'role'\"*'--set=role=taskman'*) printf '1\\n' ;;\n"
        "  *\"pg_database WHERE datname = :'database'\"*'--set=role=taskman'*'--set=database=taskman'*) printf '1\\n' ;;\n"
        "  *) exit 99 ;;\n"
        "esac\n",
    )

    assert discover_module._observe_postgresql_authority(
        {"host": "127.0.0.1", "port": 5432, "role": "taskman", "name": "taskman"}, "16"
    )["database"] == "compatible"


def test_preconvergence_postgresql_observer_refuses_a_failed_identity_sql_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed PostgreSQL identity query must refuse instead of treating it as absent."""

    _install_postgresql_authority_commands(
        tmp_path,
        monkeypatch,
        "#!/bin/sh\n"
        "case \"$*\" in *'--file=-'*) exit 1;; *) exit 97;; esac\n",
    )

    with pytest.raises(discover_module.PostgresqlObservationError):
        discover_module._observe_postgresql_authority(
            {"host": "127.0.0.1", "port": 5432, "role": "taskman", "name": "taskman"}, "16"
        )


@pytest.mark.parametrize("role,database", [("incompatible", "absent"), ("absent", "incompatible"), ("incompatible", "incompatible")])
def test_postgresql_observer_does_not_confuse_filtered_incompatible_identities_with_absence(tmp_path, monkeypatch, role, database):
    # A filtered SELECT 1 returns no row for incompatible existing identities;
    # an existence-preserving CASE returns the incompatible row as zero.
    _install_postgresql_authority_commands(
        tmp_path, monkeypatch,
        "#!/bin/sh\nsql=$(cat)\n"
        f"case \"$sql\" in *pg_roles*) identity={role} ;; *pg_database*) identity={database} ;; *) exit 99 ;; esac\n"
        "[ \"$identity\" = incompatible ] || exit 0\n"
        "case \"$sql\" in *'CASE WHEN'*'THEN 1 ELSE 0 END'*) printf '0\\n' ;; esac\n",
    )
    with pytest.raises(discover_module.PostgresqlObservationError):
        discover_module._observe_postgresql_authority(
            {"host": "127.0.0.1", "port": 5432, "role": "taskman", "name": "taskman"}, "16"
        )


def test_native_postgresql_identity_sql_distinguishes_absence_privileges_memberships_and_ownership(tmp_path, monkeypatch):
    """Run the production observer script with native SQL and synthetic Ubuntu process facts."""
    from shlex import quote
    from uuid import uuid4

    container = os.environ.get("TASKMAN_TEST_POSTGRES_CONTAINER")
    if container is None:
        pytest.skip("set TASKMAN_TEST_POSTGRES_CONTAINER to run native identity discovery SQL")
    suffix = uuid4().hex
    role, parent, name = f"fresh_role_{suffix}", f"fresh_parent_{suffix}", f"fresh_db_{suffix}"
    database = {"host": "127.0.0.1", "port": 5432, "role": role, "name": name}
    _install_postgresql_authority_commands(
        tmp_path, monkeypatch,
        '#!/bin/sh\n[ "$1" = -u ] && [ "$2" = postgres ] && [ "$3" = -- ] || exit 99\n'
        f'shift 3\nexec docker exec -i --user postgres {quote(container)} "$@"\n',
    )

    def query(sql):
        result = subprocess.run(
            ("docker", "exec", "--user", "postgres", container, "psql", "--no-psqlrc",
             "--tuples-only", "--no-align", "--username", "postgres", "--dbname", "postgres",
             "--set=ON_ERROR_STOP=1", "--command", sql), capture_output=True, check=False, timeout=60,
        )
        assert result.returncode == 0, "native test administration failed"
        return result.stdout

    before_roles = query("SELECT rolname FROM pg_roles ORDER BY rolname")
    before_databases = query("SELECT datname FROM pg_database ORDER BY datname")
    assert role.encode() not in before_roles.splitlines()
    assert parent.encode() not in before_roles.splitlines()
    assert name.encode() not in before_databases.splitlines()
    try:
        assert discover_module._observe_postgresql_authority(database, "16")["database"] == "absent"
        query(f"CREATE ROLE {role} LOGIN SUPERUSER")
        with pytest.raises(discover_module.PostgresqlObservationError):
            discover_module._observe_postgresql_authority(database, "16")
        query(f"ALTER ROLE {role} NOSUPERUSER")
        assert discover_module._observe_postgresql_authority(database, "16")["role"] == "compatible"
        query(f"CREATE DATABASE {name} OWNER {role}")
        assert discover_module._observe_postgresql_authority(database, "16")["database"] == "compatible"
        for incompatible, compatible in (
            ("NOLOGIN", "LOGIN"), ("SUPERUSER", "NOSUPERUSER"), ("CREATEDB", "NOCREATEDB"),
            ("CREATEROLE", "NOCREATEROLE"), ("REPLICATION", "NOREPLICATION"),
            ("BYPASSRLS", "NOBYPASSRLS"), ("NOINHERIT", "INHERIT"),
        ):
            query(f"ALTER ROLE {role} {incompatible}")
            with pytest.raises(discover_module.PostgresqlObservationError):
                discover_module._observe_postgresql_authority(database, "16")
            query(f"ALTER ROLE {role} {compatible}")
        query(f"CREATE ROLE {parent}; GRANT {parent} TO {role}")
        with pytest.raises(discover_module.PostgresqlObservationError):
            discover_module._observe_postgresql_authority(database, "16")
        query(f"REVOKE {parent} FROM {role}; ALTER DATABASE {name} OWNER TO postgres")
        with pytest.raises(discover_module.PostgresqlObservationError):
            discover_module._observe_postgresql_authority(database, "16")
        query(f"DROP ROLE {role}")
        with pytest.raises(discover_module.PostgresqlObservationError):
            discover_module._observe_postgresql_authority(database, "16")
    finally:
        query(f"DROP DATABASE IF EXISTS {name}")
        query(f"DROP ROLE IF EXISTS {role}; DROP ROLE IF EXISTS {parent}")
        assert query("SELECT rolname FROM pg_roles ORDER BY rolname") == before_roles
        assert query("SELECT datname FROM pg_database ORDER BY datname") == before_databases


def test_preconvergence_postgresql_observer_refuses_a_failed_cluster_listing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cluster-listing failure is not evidence that PostgreSQL is absent."""

    _install_postgresql_authority_commands(tmp_path, monkeypatch, "#!/bin/sh\nexit 99\n")
    (tmp_path / "pg_lsclusters").write_text("#!/bin/sh\nexit 1\n")

    with pytest.raises(discover_module.PostgresqlObservationError):
        discover_module._observe_postgresql_authority(
            {"host": "127.0.0.1", "port": 5432, "role": "taskman", "name": "taskman"}, "16"
        )


def _postgres_authority_exit(tmp_path, monkeypatch, *, clusters,
    executable="/usr/lib/postgresql/16/bin/postgres", owner="postgres:postgres", role="1", database="1"):
    _install_postgresql_authority_commands(tmp_path, monkeypatch,
        "#!/bin/sh\nsql=$(cat)\ncase \"$sql\" in *pg_roles*) printf '%s\\n' '" + role + "';; *pg_database*) printf '%s\\n' '" + database + "';; esac\n")
    # Preserve real seven-column Ubuntu rows in compatibility fixtures.
    rows = clusters.replace("\\n", "\n").splitlines()
    rows = [line + " /var/lib/postgresql/16/" + line.split()[1] + " /var/log/postgresql/16.log" if len(line.split()) == 5 else line for line in rows]
    (tmp_path / "pg_lsclusters").write_text("#!/bin/sh\nprintf '%s\\n' '" + "\n".join(rows) + "'\n")
    (tmp_path / "readlink").write_text("#!/bin/sh\ncase \"$*\" in */proc/*) printf '%s\\n' '" + executable + "';; *) for path do :; done; printf '%s\\n' \"$path\";; esac\n")
    (tmp_path / "stat").write_text("#!/bin/sh\ncase \"$*\" in */proc/*) printf '%s\\n' '" + owner + "';; */var/lib/*) printf 'directory:postgres:postgres:700\\n';; *) printf 'regular file:postgres:postgres:640\\n';; esac\n")
    try:
        discover_module._observe_postgresql_authority(_request().parameters["database"], None)
    except discover_module.PostgresqlObservationError:
        return subprocess.CompletedProcess((), 1, b"", b"")
    return subprocess.CompletedProcess((), 0, b"", b"")

@pytest.mark.parametrize(
    ("clusters", "executable", "owner", "role", "database", "expected"),
    (
        ("16 main 5432 online postgres", "/foreign/postgres", "postgres:postgres", "1", "1", 1),
        ("16 main 5432 online postgres", "/usr/lib/postgresql/16/bin/postgres", "root:root", "1", "1", 1),
    ),
)
def test_preconvergence_postgresql_observer_refuses_every_present_incompatible_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    clusters: str,
    executable: str,
    owner: str,
    role: str,
    database: str,
    expected: int,
) -> None:
    """Missing resources may proceed; each present contradictory fact refuses."""

    completed = _postgres_authority_exit(
        tmp_path, monkeypatch, clusters=clusters, executable=executable, owner=owner, role=role, database=database
    )
    assert completed.returncode == 0 if expected == 0 else completed.returncode != 0, (
        completed.returncode, completed.stdout.decode(), completed.stderr.decode()
    )


def test_deploy_discovery_bounds_history_to_the_protection_reference_intersection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mature history must not make deploy discovery exceed its wire budget."""

    held_protection = "backup-" + "a" * 32
    unheld_protection = "backup-" + "b" * 32
    protections = (
        BackupProtection(1, held_protection, None, RELEASE_ID, 0, SELECTED_AT),
        BackupProtection(1, unheld_protection, None, RELEASE_ID, 1, SELECTED_AT),
    )
    selections = tuple(
        SelectionRecord(
            RELEASE_ID,
            None,
            f"backup-{index:032x}",
            SELECTED_AT + timedelta(minutes=index),
            2,
            None,
            (),
        )
        for index in range(65)
    )
    selections = selections[:-1] + (
        SelectionRecord(
            RELEASE_ID,
            None,
            held_protection,
            selections[-1].selected_at,
            2,
            None,
            (),
        ),
    )
    observed = HostState(
        **{
            **_state().__dict__,
            "selections": selections,
            "selection_filenames": (),
            "successful_backup_ids": frozenset(),
            "backup_protections": (protections[1],),
            "retiring_backup_protections": (protections[0],),
        }
    )
    _install_observer(monkeypatch, observed)

    result = discover_module.discover(_request(mode="deploy"))

    assert result.outcome == "succeeded"
    assert result.state["independently_held_backup_ids"] == (held_protection,)
    assert result.state["backup_protections"] == tuple(item.to_mapping() for item in protections)


def test_restore_discovery_projects_only_independently_held_safety_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Full history must affect exact pruning without becoming an unbounded response."""

    attempt_ids = tuple(f"backup-{index:032x}" for index in range(6))
    requested = _restore_state().backups[0]
    target = RestoreTarget(
        1,
        requested.backup_id,
        requested.dump_sha256,
        requested.source_release_id,
        None,
        RELEASE_ID,
        101,
        202,
        False,
        attempt_ids[0],
        None,
        tuple(
            {"backup_id": backup_id, "attempt_number": number}
            for number, backup_id in enumerate(attempt_ids)
        ),
    )
    protection = BackupProtection(1, attempt_ids[2], None, RELEASE_ID, 0, SELECTED_AT)
    restore_state = _restore_state()
    safety_backups = tuple(
        BackupRecord(
            backup_id,
            SELECTED_AT + timedelta(seconds=number + 1),
            f"{number + 1:064x}",
            RELEASE_ID,
            (20260905120000,),
            1024,
        )
        for number, backup_id in enumerate(attempt_ids)
    )
    observed = HostState(
        **{
            **restore_state.__dict__,
            "backups": (*restore_state.backups, *safety_backups),
            "restore_target": target,
            # This represents complete disk-backed history, including an ID
            # that need not appear in the latest/predecessor projection.
            "successful_backup_ids": frozenset({attempt_ids[1]}),
            "backup_protections": (protection,),
        }
    )
    _install_observer(monkeypatch, observed)
    monkeypatch.setattr(
        discover_module,
        "observe_restore_databases",
        lambda *_args: {
            "canonical": {
                "oid": 202,
                "owner": "taskman",
                "migration_table_present": True,
                "applied_migrations": (20260905120000,),
            },
            "temporary": None,
            "retired": {
                "oid": 101,
                "owner": "taskman",
                "migration_table_present": True,
                "applied_migrations": (20260905120000,),
            },
        },
    )

    result = discover_module.discover(
        _request(mode="restore", backup_id=requested.backup_id)
    )

    assert result.outcome == "succeeded"
    assert result.state["independently_held_backup_ids"] == (
        attempt_ids[1],
        attempt_ids[2],
    )


def test_unfinished_provision_downgrade_digest_includes_release_proving_live_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The no-history baseline must still bind the release used to interpret live schema."""
    migration = {
        "filename": "20260905120000_create_tasks.exs",
        "sha256": "c" * 64,
    }
    observed = HostState(
        selected_release_id=None,
        releases=(_release(migrations=(migration,)),),
        backups=(),
        selections=(),
        applied_migrations=(20260905120000,),
        service_state="stopped",
        database_state="ready",
        temporary_paths=(),
        warnings=(),
    )
    _install_observer(monkeypatch, observed)

    result = discover_module.discover(_request(mode="provision"))

    expected = hashlib.sha256(
        json.dumps([RELEASE_ID], separators=(",", ":")).encode("ascii")
    ).hexdigest()
    assert result.outcome == "succeeded"
    assert result.state["downgrade_baseline_sha256"] == expected


def test_deploy_scheduler_facts_are_observed_under_the_lifecycle_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Observing scheduler identity after unlock could combine different plans."""
    active = False

    class Lock:
        def __enter__(self) -> None:
            nonlocal active
            active = True

        def __exit__(self, *_args: object) -> None:
            nonlocal active
            active = False

    observed = _state()
    monkeypatch.setattr(discover_module, "validate_credentials", lambda *_args: None)
    monkeypatch.setattr(
        discover_module,
        "observe_database_state",
        lambda *_args: {
            "state": observed.database_state,
            "applied_migrations": observed.applied_migrations,
        },
    )
    monkeypatch.setattr(discover_module, "observe_host_state", lambda *_args, **_kwargs: observed)
    monkeypatch.setattr(discover_module, "lifecycle_lock", lambda *_args, **_kwargs: Lock())

    def scheduler(*_args: object) -> dict[str, object]:
        assert active is True
        return {
            "scheduled_backup_sha256": "d" * 64,
            "backup_timer_enabled": True,
            "backup_timer_state": "active",
        }

    monkeypatch.setattr(discover_module, "_scheduler_facts", scheduler)

    result = discover_module.discover(_request(mode="deploy"))

    assert result.outcome == "succeeded"


def test_release_inventory_uses_exact_cursor_and_full_snapshot_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Paging from only the current slice would not bind later pages to one inventory."""
    records = tuple(
        ReleaseRecord(
            release_id=build_release_id(
                f"0.2.{index}",
                f"{index:040x}",
                artifact_sha256=f"{index + 1:064x}",
                source_dirty=False,
            ),
            source_revision=f"{index:040x}",
            artifact_sha256=f"{index + 1:064x}",
            migrations=(),
            schema_version=2,
            artifact_manifest=ArtifactManifest.from_mapping(
                {
                    **_release().artifact_manifest.to_mapping(),
                    "application_version": f"0.2.{index}",
                    "source_revision": f"{index:040x}",
                    "release_id": build_release_id(
                        f"0.2.{index}",
                        f"{index:040x}",
                        artifact_sha256=f"{index + 1:064x}",
                        source_dirty=False,
                    ),
                    "artifact_sha256": f"{index + 1:064x}",
                }
            ),
        )
        for index in range(65)
    )
    records = tuple(sorted(records, key=lambda item: item.release_id))
    observed = HostState(**{**_state().__dict__, "releases": records})
    _install_observer(monkeypatch, observed)

    first = discover_module.list_releases(_listing_request("list_releases"))
    entries = [{"id": item.release_id, "record": item.to_mapping()} for item in records]

    assert first.outcome == "succeeded"
    assert len(first.state["records"]) == 64
    assert first.state["inventory_sha256"] == _inventory_sha256("list_releases", entries)
    assert first.state["next_cursor"] == {
        "inventory_sha256": first.state["inventory_sha256"],
        "after_id": records[63].release_id,
    }
    second = discover_module.list_releases(
        _listing_request("list_releases", dict(first.state["next_cursor"]))
    )
    assert len(second.state["records"]) == 1
    assert second.state["records"][0]["id"] == entries[-1]["id"]
    assert second.state["next_cursor"] is None


def test_listing_checks_snapshot_drift_before_continuation_membership(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A removed continuation member is drift, not an invalid cursor."""
    observed = _state()
    _install_observer(monkeypatch, observed)
    initial_entries = [{"id": RELEASE_ID, "record": _release().to_mapping()}]
    cursor = {
        "inventory_sha256": _inventory_sha256("list_releases", initial_entries),
        "after_id": RELEASE_ID,
    }
    changed = HostState(**{**observed.__dict__, "releases": ()})
    monkeypatch.setattr(discover_module, "observe_host_state", lambda *_args, **_kwargs: changed)

    result = discover_module.list_releases(_listing_request("list_releases", cursor))

    assert result.outcome == "refused"
    assert result.message == "inventory-changed"
    assert result.state == {}


def test_listing_rejects_unknown_continuation_member_in_matching_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Accepting an unknown after_id could silently omit records."""
    observed = _state()
    _install_observer(monkeypatch, observed)
    entries = [{"id": RELEASE_ID, "record": _release().to_mapping()}]
    cursor = {
        "inventory_sha256": _inventory_sha256("list_releases", entries),
        "after_id": build_release_id(
            "0.2.1", "c" * 40, artifact_sha256="d" * 64, source_dirty=False
        ),
    }

    result = discover_module.list_releases(_listing_request("list_releases", cursor))

    assert result.outcome == "refused"
    assert result.message == "invalid inventory cursor"
    assert result.state == {"invalid_request": True}


def test_listing_validates_full_authority_before_rejecting_malformed_cursor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Request validation must not mask ambiguity in authoritative history."""
    observed = _state()
    _install_observer(monkeypatch, observed)
    inspected = False

    def observe(*_args: object, **_kwargs: object) -> HostState:
        nonlocal inspected
        inspected = True
        return observed

    monkeypatch.setattr(discover_module, "observe_host_state", observe)

    result = discover_module.list_releases(
        _listing_request("list_releases", {"inventory_sha256": "bad", "after_id": "bad"})
    )

    assert inspected is True
    assert result.outcome == "refused"
    assert result.state == {"invalid_request": True}


def test_release_page_stops_on_encoded_bytes_before_sixty_four_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Count-only paging could make the helper emit no JSON result at all."""
    migrations = tuple(
        {
            "filename": f"{index:014d}_" + ("a" * 230) + ".exs",
            "sha256": "e" * 64,
        }
        for index in range(256)
    )
    records = []
    for index in range(20):
        release_id = build_release_id(
            f"1.0.{index}", f"{index:040x}", artifact_sha256=f"{index + 1:064x}", source_dirty=False
        )
        manifest = ArtifactManifest.from_mapping(
            {
                **_release().artifact_manifest.to_mapping(),
                "application_version": f"1.0.{index}",
                "source_revision": f"{index:040x}",
                "release_id": release_id,
                "artifact_sha256": f"{index + 1:064x}",
                "migrations": list(migrations),
            }
        )
        records.append(ReleaseRecord(release_id, f"{index:040x}", f"{index + 1:064x}", migrations, 2, manifest))
    records.sort(key=lambda item: item.release_id)
    observed = HostState(**{**_state().__dict__, "releases": tuple(records)})
    _install_observer(monkeypatch, observed)

    result = discover_module.list_releases(_listing_request("list_releases"))

    assert result.outcome == "succeeded"
    assert 0 < len(result.state["records"]) < 20
    assert result.state["next_cursor"] is not None


def test_read_only_discovery_maps_lock_contention_to_retryable_result(monkeypatch: pytest.MonkeyPatch) -> None:
    def locked(*_args: object, **_kwargs: object):
        class Lock:
            def __enter__(self):
                raise discover_module.LifecycleLockContention("held")
            def __exit__(self, *_exc: object) -> None:
                return None
        return Lock()
    monkeypatch.setattr(discover_module, "lifecycle_lock", locked)

    result = discover_module.discover(_request())

    assert result.outcome == "retryable"
    assert result.state == {"locked": True}


def test_authoritative_state_ambiguity_is_a_bounded_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_observer(monkeypatch, _state())
    monkeypatch.setattr(discover_module, "observe_host_state", lambda *_args, **_kwargs: (_ for _ in ()).throw(StateAmbiguityError("ambiguous")))

    result = discover_module.discover(_request())

    assert result.outcome == "refused"
    assert result.state == {}


def test_major_version_filter_preserves_other_clusters():
    from taskman_ops.host_helper.postgresql import parse_cluster_rows
    rows = parse_cluster_rows(
        "16 main 5433 down postgres /var/lib/postgresql/16/main /var/log/postgresql/postgresql-16-main.log\n"
        "18 main 5432 online postgres /var/lib/postgresql/18/main /var/log/postgresql/postgresql-18-main.log\n",
        "18",
    )
    assert len(rows) == 1
    assert (rows[0]["version"], rows[0]["name"], rows[0]["port"]) == ("18", "main", 5432)


@pytest.mark.parametrize("rows", [
    "16 main 5433 down postgres\n18 main 5432 online postgres /var/lib/postgresql/18/main /var/log/postgresql/18.log\n",
    "18 main invalid online postgres /var/lib/postgresql/18/main /var/log/postgresql/18.log\n",
    "18 main 5432 online foreign /var/lib/postgresql/18/main /var/log/postgresql/18.log\n",
])
def test_full_inventory_is_validated_before_track_filter(rows):
    from taskman_ops.host_helper.postgresql import parse_cluster_rows
    with pytest.raises(ValueError):
        parse_cluster_rows(rows, "18")


@pytest.fixture
def selected_postgresql(monkeypatch):
    from taskman_ops.host_helper import postgresql as module
    from subprocess import CompletedProcess
    values = {
        "inventory": "16 main 5433 down postgres /var/lib/postgresql/16/main /var/log/postgresql/16.log\n18 main 5432 online postgres /var/lib/postgresql/18/main /var/log/postgresql/18.log\n",
        "role": "1\n", "database": "1\n", "pid": "42\n/var/lib/postgresql/18/main\n1\n5432\n/var/run/postgresql\nlocalhost\n",
        "listeners": 'LISTEN 0 100 127.0.0.1:5432 0.0.0.0:* users:(("postgres",pid=42,fd=5))\nLISTEN 0 100 [::1]:5432 [::]:* users:(("postgres",pid=42,fd=6))\n',
        "retained": False, "admin_unavailable": False, "hba": "/etc/postgresql/18/main/pg_hba.conf",
    }
    monkeypatch.setattr(module.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(module, "_retained_authority", lambda: values["retained"])
    def command(argv, **kwargs):
        if argv[0] == "pg_lsclusters": output = values["inventory"]
        elif argv[0] == "pg_conftool":
            output = {"data_directory": "/var/lib/postgresql/18/main\n", "hba_file": values["hba"] + "\n"}[argv[-1]]
        elif argv[0] == "cat": output = values["pid"]
        elif argv[0] == "readlink": output = argv[-1] + "\n" if argv[-1].startswith(("/var/lib/", "/etc/")) else "/usr/lib/postgresql/18/bin/postgres\n"
        elif argv[0] == "stat":
            output = ("directory:postgres:postgres:700\n" if argv[-1].startswith("/var/lib/") else "regular file:postgres:postgres:640\n") if "--format=%F:%U:%G:%a" in argv else "postgres:postgres\n"
        elif argv[0] == "ss": output = values["listeners"]
        elif argv[0] == "runuser":
            assert "--host" in argv and argv[argv.index("--host") + 1] == "/var/run/postgresql"
            assert argv[argv.index("--port") + 1] == "5432"
            assert "--file=-" in argv and "--command" not in argv
            if values["admin_unavailable"]: raise module.CommandError("unavailable")
            sql = kwargs["stdin"].decode()
            if "current_setting" in sql:
                output = "18|/var/lib/postgresql/18/main|/etc/postgresql/18/main/postgresql.conf|" + values["hba"] + "|5432\n"
            elif "pg_roles" in sql: output = values["role"]
            elif "pg_database" in sql: output = values["database"]
            else: pytest.fail("unexpected SQL")
        else: pytest.fail(f"unexpected command {argv[0]}")
        return CompletedProcess(argv, 0, str(output).encode(), b"")
    monkeypatch.setattr(module, "run_command", command)
    return module, values


def test_selected_cluster_keeps_down_other_track_and_binds_native_identity(selected_postgresql):
    module, _ = selected_postgresql
    result = module.observe_postgresql_authority(_request().parameters["database"], "18", False)
    assert result["cluster"]["version"] == "18"
    assert result["cluster"]["port"] == 5432
    assert result["access"] == "unproven"


@pytest.mark.parametrize("role,database,want", [("", "", ("absent", "absent")),
    ("1\n", "", ("compatible", "absent")), ("1\n", "1\n", ("compatible", "compatible"))])
def test_compatible_partial_resources_are_not_invented_as_ready(selected_postgresql, role, database, want):
    module, values = selected_postgresql
    values.update(role=role, database=database)
    result = module.observe_postgresql_authority(_request().parameters["database"], "18", False)
    assert (result["role"], result["database"]) == want
    assert result["access"] == "unproven"


@pytest.mark.parametrize("change", [
    {"inventory": "16 main 5433 down postgres /var/lib/postgresql/16/main /var/log/postgresql/16.log\n"},
    {"inventory": "18 main 5432 down postgres /var/lib/postgresql/18/main /var/log/postgresql/18.log\n"},
    {"inventory": "18 main 5433 online postgres /var/lib/postgresql/18/main /var/log/postgresql/18.log\n"},
    {"inventory": "18 main 5432 online postgres /var/lib/postgresql/18/main /var/log/postgresql/18.log\n18 other 5433 online postgres /var/lib/postgresql/18/other /var/log/postgresql/other.log\n"},
    {"role": "0\n"}, {"database": "0\n"}, {"role": "", "database": "1\n"},
    {"listeners": 'LISTEN 0 100 0.0.0.0:5432 *:* users:(("postgres",pid=42,fd=5))\n'},
    {"listeners": 'LISTEN 0 100 127.0.0.1:5432 *:* users:(("other",pid=99,fd=5))\n'},
    {"pid": "42\n/var/lib/postgresql/16/main\n1\n5432\n/var/run/postgresql\nlocalhost\n"},
])
def test_selected_cluster_refuses_unsafe_or_contradictory_evidence(selected_postgresql, change):
    module, values = selected_postgresql
    values.update(change)
    with pytest.raises(module.PostgresqlObservationError) as error:
        module.observe_postgresql_authority(_request().parameters["database"], "18", False)
    assert error.value.exit_code == 10


def test_unavailable_administrator_is_access_failure(selected_postgresql):
    module, values = selected_postgresql
    values["admin_unavailable"] = True
    with pytest.raises(module.PostgresqlObservationError) as error:
        module.observe_postgresql_authority(_request().parameters["database"], "18", False)
    assert error.value.exit_code == 5


@pytest.mark.parametrize("retained", [False, True])
def test_missing_inspection_tools_require_independent_absence_proof(selected_postgresql, monkeypatch, retained):
    module, values = selected_postgresql
    values["retained"] = retained
    monkeypatch.setattr(module.shutil, "which", lambda _: None)
    if retained:
        with pytest.raises(module.PostgresqlObservationError):
            module.observe_postgresql_authority(_request().parameters["database"], "18", False)
    else:
        values["listeners"] = ""
        assert module.observe_postgresql_authority(_request().parameters["database"], "18", False)["branch"] == "bootstrap"


def test_native_cluster_paths_must_not_be_linked_or_foreign(selected_postgresql, monkeypatch):
    module, _ = selected_postgresql
    original = module.run_command
    def foreign(argv, **kwargs):
        if argv[0] == "stat" and argv[-1].endswith("postgresql.conf"):
            return subprocess.CompletedProcess(argv, 0, b"symbolic link:root:root:777\n", b"")
        return original(argv, **kwargs)
    monkeypatch.setattr(module, "run_command", foreign)
    with pytest.raises(module.PostgresqlObservationError):
        module.observe_postgresql_authority(_request().parameters["database"], "18", False)


def test_default_observer_preserves_selected_safe_alternate_hba(selected_postgresql):
    module, values = selected_postgresql
    values["hba"] = "/etc/postgresql/shared-authentication.conf"
    assert module.observe_postgresql_authority(_request().parameters["database"], "18", False)["cluster"]["hba_file"] == values["hba"]
    with pytest.raises(module.PostgresqlObservationError) as error:
        module.observe_postgresql_authority(_request().parameters["database"], "18", True)
    assert error.value.exit_code == 10


@pytest.mark.parametrize("artifact,want", [("data", True), ("config", True), ("service", True), ("none", False)])
def test_absence_checks_retained_native_data_configuration_and_service(tmp_path, monkeypatch, artifact, want):
    from taskman_ops.host_helper import postgresql as module
    monkeypatch.setattr(module, "Path", lambda value: tmp_path / value.lstrip("/"))
    def absent_account(_):
        raise KeyError("postgres")
    monkeypatch.setattr(module.pwd, "getpwnam", absent_account)
    if artifact in {"data", "config"}:
        retained = tmp_path / ("var/lib/postgresql" if artifact == "data" else "etc/postgresql")
        retained.mkdir(parents=True)
        (retained / "18").mkdir()
    monkeypatch.setattr(module, "run_command", lambda argv, **kwargs: subprocess.CompletedProcess(
        argv, 0, b"postgresql.service enabled\n" if artifact == "service" else b"", b""))
    assert module._retained_authority() is want


@pytest.mark.parametrize("value", [None, "false", 0])
def test_provision_discovery_refuses_non_boolean_configuration_authority(tmp_path, value):
    request = _local_discovery_request(tmp_path)
    malformed = HostRequest(3, request.operation, request.correlation_id, {}, request.paths,
                            {**request.parameters, "force_postgresql_config": value})
    assert discover_module.discover(malformed).outcome == "refused"


@pytest.mark.parametrize("deferred,failure", [(False, "native-query"), (True, "provenance")],
    ids=["missing-passfile-native-query", "deferred-configuration-provenance"])
def test_public_admin_schema_failure_preserves_access_and_provenance_categories(tmp_path, monkeypatch, deferred, failure):
    from taskman_ops.host_helper.database import DatabaseObservationError
    from taskman_ops.errors import ExitStatus
    from taskman_ops.workflows.helper import result_error

    _missing_scheduler(monkeypatch)
    selected = _pg_authority("ready")
    if deferred:
        selected["branch"] = "configure-existing"
        (tmp_path / "pgpass").write_bytes(b"retained-credential")
        monkeypatch.setattr(discover_module, "validate_credentials", lambda *_: None)
    monkeypatch.setattr(discover_module, "_observe_postgresql_authority", lambda *_: selected)
    def schema_query(*_args):
        if failure == "native-query":
            raise discover_module.CommandError("native query unavailable")
        raise DatabaseObservationError("unrecognized initial data provenance")
    monkeypatch.setattr(discover_module, "observe_database_state_or_empty_as_admin", schema_query)
    original = _local_discovery_request(tmp_path)
    request = HostRequest(3, original.operation, original.correlation_id, {}, original.paths,
                         {**original.parameters, "force_postgresql_config": deferred})
    result = discover_module.discover(request)
    error = result_error(result)
    if failure == "native-query":
        assert result.outcome == "manual"
        assert result.state == {"failed_boundary": "postgresql", "exit_code": 5,
            "failed_check": "schema-inspection", "postgresql_observation": selected}
        assert error.status is ExitStatus.REMOTE_PREFLIGHT
        assert error.stage == "postgresql"
    else:
        assert result.outcome == "refused"
        assert error.status is ExitStatus.SAFETY
    assert error.changed is False


def test_parsed_native_command_uses_c_locale_and_preserves_path(tmp_path, monkeypatch):
    from taskman_ops.host_helper import postgresql as module
    if b"cs_CZ.utf8" not in subprocess.check_output(("locale", "-a")):
        pytest.skip("native Czech locale is unavailable")
    # Use GNU stat's actual translated output at the production bounded
    # subprocess boundary, including executable lookup through the retained PATH.
    (tmp_path / "stat").symlink_to("/usr/bin/stat")
    native_file = tmp_path / "postgresql.conf"
    native_file.write_text("synthetic local configuration\n")
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setenv("LC_ALL", "cs_CZ.utf8")
    monkeypatch.setenv("LANG", "cs_CZ.utf8")
    assert module._command(("stat", "--format=%F", str(native_file))) == "regular file"


def test_native_subprocess_locale_is_pinned_even_without_installed_translations(tmp_path, monkeypatch):
    from taskman_ops.host_helper import postgresql as module
    command = tmp_path / "metadata-locale"
    command.write_text("#!/bin/sh\nprintf '%s\\n%s\\n' \"$LC_ALL\" \"$PATH\"\n")
    command.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setenv("LC_ALL", "operator-locale")
    assert module._command(("metadata-locale",)) == "C\n" + str(tmp_path)


def test_configuration_admission_defers_scram_only_after_safe_selected_authority(tmp_path, monkeypatch):
    _missing_scheduler(monkeypatch)
    (tmp_path / "pgpass").write_bytes(b"protected")
    selected = _pg_authority("ready")
    selected["branch"] = "configure-existing"
    calls = []
    monkeypatch.setattr(discover_module, "_observe_postgresql_authority", lambda *_: selected)
    monkeypatch.setattr(discover_module, "validate_credentials", lambda *_: calls.append("credentials"))
    def denied(*_):
        calls.append("scram")
        raise discover_module.CommandError("PostgreSQL SCRAM access proof failed")
    monkeypatch.setattr(discover_module, "prove_scram_access", denied)
    monkeypatch.setattr(discover_module, "observe_database_state_or_empty", lambda *_: pytest.fail("failed authentication cannot inspect application schema"))
    monkeypatch.setattr(discover_module, "observe_database_state_or_empty_as_admin", lambda *_: calls.append("schema") or {"state": "ready", "applied_migrations": (), "initial_empty": True})
    original = _local_discovery_request(tmp_path)
    request = HostRequest(3, original.operation, original.correlation_id, {}, original.paths, {**original.parameters, "force_postgresql_config": True})
    result = discover_module.discover(request)
    assert calls == ["credentials", "schema"]
    assert result.outcome == "succeeded"
    assert result.state["postgresql"]["access"] == "unproven"
    assert result.state["database_state"] == "ready"


def test_missing_application_resources_still_require_capable_existing_client(tmp_path, monkeypatch):
    _missing_scheduler(monkeypatch)
    monkeypatch.setattr(discover_module, "_observe_postgresql_authority", lambda *_: _pg_authority("absent"))
    def unsupported():
        raise discover_module.CommandError("PostgreSQL client must support require_auth")
    monkeypatch.setattr(discover_module, "require_scram_client", unsupported)
    result = discover_module.discover(_local_discovery_request(tmp_path))
    assert result.outcome == "manual"
    assert result.state == {"failed_boundary": "postgresql", "exit_code": 5,
        "failed_check": "scram-client-prerequisite", "postgresql_observation": _pg_authority("absent")}
    assert "require_auth" in result.message
