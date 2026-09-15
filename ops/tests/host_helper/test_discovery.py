from __future__ import annotations

from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
import hashlib
import json
import os
import subprocess

import pytest

from taskman_ops.host_helper.operations import discover as discover_module
from taskman_ops.host_helper.backup_protection import BackupProtection
from taskman_ops.host_helper.records import BackupRecord, ReleaseRecord, SelectionRecord
from taskman_ops.host_helper.state import HostState, StateAmbiguityError
from taskman_ops.host_protocol import HostRequest
from taskman_ops.releases.identifiers import build_release_id
from taskman_ops.releases.manifests import BUILDER_BASE_DIGEST, BUILDER_BASE_TAG, ArtifactManifest
from taskman_ops.provisioning import ProvisioningInputs, validate_preconvergence_authority
from taskman_ops.services.caddy import CaddyPlan, CaddyRepository
from tests.support.environments import environment_config


CORRELATION = "op-0123456789abcdef0123456789abcdef"
REVISION = "a" * 40
ARTIFACT_SHA256 = "b" * 64
RELEASE_ID = build_release_id("0.2.0", REVISION, artifact_sha256=ARTIFACT_SHA256, source_dirty=False)
SELECTED_AT = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)


def _request(*, mode: str = "strict", backup_id: str | None = None) -> HostRequest:
    parameters: dict[str, object] = {
        "credentials_path": "/etc/taskman/pgpass",
        "database": {"host": "127.0.0.1", "port": 5432, "role": "taskman", "name": "taskman"},
        "mode": mode,
    }
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


def _install_observer(monkeypatch: pytest.MonkeyPatch, observed: HostState) -> None:
    monkeypatch.setattr(discover_module, "validate_credentials", lambda *_args: None)
    monkeypatch.setattr(discover_module, "observe_database_state", lambda *_args: {"state": observed.database_state, "applied_migrations": observed.applied_migrations})
    monkeypatch.setattr(discover_module, "observe_database_state_or_empty", lambda *_args: {"state": observed.database_state, "applied_migrations": observed.applied_migrations})
    monkeypatch.setattr(discover_module, "observe_host_state", lambda *_args, **_kwargs: observed)
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


def test_restore_discovery_refuses_until_database_binding_facts_are_observable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replacing unavailable restore database authority with null would claim absence."""
    _install_observer(monkeypatch, _state())

    result = discover_module.discover(
        _request(mode="restore", backup_id="backup-" + "c" * 32)
    )

    assert result.outcome == "refused"
    assert result.state == {}


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
    monkeypatch: pytest.MonkeyPatch,
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

    result = discover_module.discover(_request(mode="provision"))

    assert result.outcome == "succeeded"
    assert calls == ["empty-proof"]


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
            "postgres_package_track": None,
        },
    )
    calls: list[str] = []
    monkeypatch.setattr(
        discover_module,
        "_observe_postgresql_authority",
        lambda *_args: calls.append("postgres") or None,
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


def test_real_provision_authority_projection_passes_the_production_controller_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The packaged helper's exact projection must be consumable before pyinfra."""

    observed = _state(migrations=())
    _install_observer(monkeypatch, observed)
    monkeypatch.setattr(discover_module, "_observe_postgresql_authority", lambda *_args: None)
    helper_result = discover_module.provision_authority(
        HostRequest(
            3, "provision_authority", CORRELATION, {},
            {"install_root": "/opt/taskman", "backup_root": "/var/backups/taskman"},
            {"database": {"host": "127.0.0.1", "port": 5432, "role": "taskman", "name": "taskman"}, "postgres_package_track": None},
        )
    )
    import taskman_ops.workflows.helper as helper_module

    monkeypatch.setattr(helper_module, "run_request", lambda *_args: helper_result)
    config = environment_config()
    inputs = ProvisioningInputs(
        config=config,
        caddy_plan=CaddyPlan(CaddyRepository("https://example.test/key", "/key", "deb example"), (), (), ""),
        runtime_environment=b"runtime", pgpass=b"pgpass", role_password_input=b"password",
    )

    assert validate_preconvergence_authority(object(), inputs) == helper_result.state


def test_preconvergence_postgresql_observer_binds_cluster_listener_and_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A role/database name alone cannot admit a foreign live cluster."""

    commands: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        discover_module,
        "run_command",
        lambda argv, **_kwargs: commands.append(argv) or object(),
        raising=False,
    )

    discover_module._observe_postgresql_authority(
        {"host": "127.0.0.1", "port": 5432, "role": "taskman", "name": "taskman"},
        None,
    )

    command = commands[0]
    assert command[:3] == ("sh", "-ceu", command[2])
    for required in ("pg_lsclusters", "postmaster.pid", "/proc/$pid/exe", "pg_roles", "pg_database"):
        assert required in command[2]
    assert command[-4:] == ("", "5432", "taskman", "taskman")


def _postgres_authority_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    clusters: str,
    executable: str = "/usr/lib/postgresql/16/bin/postgres",
    owner: str = "postgres:postgres",
    role: str = "1",
    database: str = "1",
) -> subprocess.CompletedProcess[bytes]:
    captured: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        discover_module,
        "run_command",
        lambda argv, **_kwargs: captured.append(argv) or object(),
    )
    discover_module._observe_postgresql_authority(
        {"host": "127.0.0.1", "port": 5432, "role": "taskman", "name": "taskman"}, None
    )
    scripts = {
        "pg_lsclusters": f"#!/bin/sh\nprintf '%s\\n' '{clusters}'\n",
        "pg_conftool": "#!/bin/sh\nprintf '/var/lib/postgresql/16/main\\n'\n",
        "sed": "#!/bin/sh\nif [ \"$1\" = -n ]; then printf '42\\n'; elif [ \"$1\" = '/^$/d' ]; then /bin/sed \"$1\"; else cat; fi\n",
        "readlink": f"#!/bin/sh\nprintf '%s\\n' '{executable}'\n",
        "stat": f"#!/bin/sh\nprintf '%s\\n' '{owner}'\n",
        "runuser": "#!/bin/sh\ncase \"$*\" in *pg_roles*) printf '%s\\n' \"${ROLE_RESULT-1}\" ;; *pg_database*) printf '%s\\n' \"${DATABASE_RESULT-1}\" ;; esac\n",
    }
    for name, source in scripts.items():
        path = tmp_path / name
        path.write_text(source)
        path.chmod(0o755)
    environment = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "ROLE_RESULT": role,
        "DATABASE_RESULT": database,
    }
    completed = subprocess.run(captured[0], env=environment, capture_output=True, check=False)
    return completed


@pytest.mark.parametrize(
    ("clusters", "executable", "owner", "role", "database", "expected"),
    (
        ("invalid", "/usr/lib/postgresql/16/bin/postgres", "postgres:postgres", "1", "1", 1),
        ("16 main 5432 online postgres\\n16 other 5432 online postgres", "/usr/lib/postgresql/16/bin/postgres", "postgres:postgres", "1", "1", 1),
        ("16 main 5433 online postgres", "/usr/lib/postgresql/16/bin/postgres", "postgres:postgres", "1", "1", 1),
        ("16 main 5432 down postgres", "/usr/lib/postgresql/16/bin/postgres", "postgres:postgres", "1", "1", 1),
        ("16 main 5432 online postgres", "/foreign/postgres", "postgres:postgres", "1", "1", 1),
        ("16 main 5432 online postgres", "/usr/lib/postgresql/16/bin/postgres", "root:root", "1", "1", 1),
        ("16 main 5432 online postgres", "/usr/lib/postgresql/16/bin/postgres", "postgres:postgres", "", "", 0),
        ("16 main 5432 online postgres", "/usr/lib/postgresql/16/bin/postgres", "postgres:postgres", "1", "", 1),
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
