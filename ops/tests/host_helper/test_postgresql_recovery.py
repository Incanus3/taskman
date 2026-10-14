"""Recovery contracts and protected local capture; no native PostgreSQL writes."""
import hashlib
import json
import os
from pathlib import Path
import stat
from dataclasses import replace

import pytest

from taskman_ops.host_protocol import HostRequest, HostResult, ProtocolError

CLUSTER = {"version": "18", "name": "main", "port": 5432,
           "data_directory": "/var/lib/postgresql/18/main",
           "config_file": "/etc/postgresql/18/main/postgresql.conf",
           "hba_file": "/etc/postgresql/18/main/pg_hba.conf"}
DIRECTORY = "/etc/taskman/postgresql-config-recovery/01234567-89ab-4def-8123-456789abcdef"


def recovery_request():
    return HostRequest(3, "postgresql_config_recovery", "op-" + "a" * 32, {},
        {"install_root": "/opt/taskman", "backup_root": "/var/backups/taskman"}, {"cluster": CLUSTER})


def recovery_result(request, *, mutation="changed", directory=DIRECTORY, exit_code=None):
    state = {"mutation_state": mutation, "recovery_directory": directory, "cluster": CLUSTER}
    if exit_code is not None:
        state.update(exit_code=exit_code, failed_boundary="postgresql")
    return HostResult.for_request(request, "succeeded" if exit_code is None else "refused" if exit_code == 10 else "retryable", "PostgreSQL recovery capture", state)


def test_recovery_receipt_is_closed_and_bound_to_selected_cluster():
    from taskman_ops.host_protocol.postgresql import validate_postgresql_recovery_request, validate_postgresql_recovery_result
    request = recovery_request()
    assert validate_postgresql_recovery_request(request) == request
    assert validate_postgresql_recovery_result(request, recovery_result(request)).state["recovery_directory"] == DIRECTORY
    for candidate in (
        replace(request, expected_state={"skip": True}),
        replace(request, parameters={"cluster": CLUSTER, "root": "/tmp"}),
        replace(request, paths={}),
        replace(request, parameters={"cluster": {**CLUSTER, "hba_file": "/tmp/hba"}}),
    ):
        with pytest.raises(ProtocolError):
            validate_postgresql_recovery_request(candidate)
    for state in (
        {**recovery_result(request).to_mapping()["state"], "secret": "canary"},
        {"mutation_state": "unchanged", "recovery_directory": DIRECTORY, "cluster": CLUSTER},
        {"mutation_state": "changed", "recovery_directory": DIRECTORY + "/child", "cluster": CLUSTER},
        {"mutation_state": "changed", "recovery_directory": DIRECTORY, "cluster": {**CLUSTER, "name": "other"}},
    ):
        with pytest.raises(ProtocolError):
            validate_postgresql_recovery_result(request, HostResult.for_request(request, "succeeded", "capture", state))


@pytest.mark.parametrize("exit_code", [5, 8, 10])
def test_recovery_failure_preserves_category_and_mutation(exit_code):
    from taskman_ops.host_protocol.postgresql import validate_postgresql_recovery_result
    request = recovery_request()
    result = recovery_result(request, exit_code=exit_code)
    assert validate_postgresql_recovery_result(request, result).state["exit_code"] == exit_code
    with pytest.raises(ProtocolError):
        validate_postgresql_recovery_result(request, replace(result, outcome="succeeded"))


@pytest.mark.skipif(os.geteuid() != 0, reason="requires disposable root-owned local paths")
def test_capture_retains_original_native_bytes_and_private_metadata(tmp_path):
    from taskman_ops.host_helper.postgresql_recovery import capture_postgresql_config
    config, hba = tmp_path / "postgresql.conf", tmp_path / "pg_hba.conf"
    config.write_bytes(b"listen_addresses = 'localhost'\n# secret-native-canary\n")
    hba.write_bytes(b"local all postgres peer\n")
    config.chmod(0o640)
    hba.chmod(0o600)
    cluster = {**CLUSTER, "config_file": str(config), "hba_file": str(hba), "data_directory": str(tmp_path / "data")}
    root = tmp_path / "recovery"
    saved = capture_postgresql_config(cluster, root=root)
    assert stat.S_IMODE(root.stat().st_mode) == stat.S_IMODE(saved.stat().st_mode) == 0o700
    metadata = json.loads((saved / "metadata.json").read_bytes())
    assert metadata["cluster"] == cluster
    for name, original in (("postgresql.conf", config), ("pg_hba.conf", hba)):
        assert (saved / name).read_bytes() == original.read_bytes()
        assert stat.S_IMODE((saved / name).stat().st_mode) == 0o600
        info = original.stat()
        assert metadata["files"][name] == {"native_path": str(original), "uid": info.st_uid,
            "gid": info.st_gid, "mode": stat.S_IMODE(info.st_mode), "sha256": hashlib.sha256(original.read_bytes()).hexdigest()}
    assert stat.S_IMODE((saved / "metadata.json").stat().st_mode) == 0o600
    assert b"secret-native-canary" not in (saved / "metadata.json").read_bytes()
    second = capture_postgresql_config(cluster, root=root)
    assert second != saved
    assert (saved / "postgresql.conf").read_bytes() == config.read_bytes()


@pytest.mark.skipif(os.geteuid() != 0, reason="requires disposable root-owned local paths")
@pytest.mark.parametrize("unsafe", ["linked-parent", "public-parent", "linked-source", "collision"])
def test_capture_refuses_unsafe_paths_and_never_overwrites(tmp_path, monkeypatch, unsafe):
    from taskman_ops.host_helper import postgresql_recovery as module
    config, hba = tmp_path / "postgresql.conf", tmp_path / "pg_hba.conf"
    config.write_bytes(b"original-conf\n")
    hba.write_bytes(b"original-hba\n")
    root = tmp_path / "recovery"
    cluster = {**CLUSTER, "config_file": str(config), "hba_file": str(hba)}
    if unsafe == "linked-parent":
        target = tmp_path / "target"
        target.mkdir(mode=0o700)
        root.symlink_to(target)
    elif unsafe == "public-parent":
        root.mkdir(mode=0o755)
    elif unsafe == "linked-source":
        config.unlink()
        config.symlink_to(hba)
    else:
        root.mkdir(mode=0o700)
        existing = root / "01234567-89ab-4def-8123-456789abcdef"
        existing.mkdir(mode=0o700)
        (existing / "postgresql.conf").write_bytes(b"retained-original")
        monkeypatch.setattr(module.uuid, "uuid4", lambda: "01234567-89ab-4def-8123-456789abcdef")
    with pytest.raises(module.PostgreSQLRecoveryError) as caught:
        module.capture_postgresql_config(cluster, root=root)
    assert caught.value.exit_code == 10
    if unsafe == "collision":
        assert (existing / "postgresql.conf").read_bytes() == b"retained-original"


def test_capture_without_root_refuses_before_creating_recovery(tmp_path):
    from taskman_ops.host_helper.postgresql_recovery import capture_postgresql_config, PostgreSQLRecoveryError
    if os.geteuid() == 0:
        pytest.skip("non-root authority test")
    with pytest.raises(PostgreSQLRecoveryError) as caught:
        capture_postgresql_config(CLUSTER, root=tmp_path / "recovery")
    assert caught.value.exit_code == 10
    assert caught.value.mutation_state == "unchanged"
    assert not (tmp_path / "recovery").exists()


@pytest.mark.parametrize("code,mutation,directory", [(5, "unchanged", None), (5, "changed", DIRECTORY), (10, "changed", DIRECTORY)])
def test_operation_capture_failure_retains_its_actual_mutation(monkeypatch, code, mutation, directory):
    from taskman_ops.host_helper import postgresql_recovery as module
    from taskman_ops.host_helper.__main__ import _dispatch
    def capture(*_args, **_kwargs):
        raise module.PostgreSQLRecoveryError(code, mutation_state=mutation, recovery_directory=directory,
            cluster=CLUSTER if directory else None)
    monkeypatch.setattr(module, "capture_postgresql_config", capture)
    result = _dispatch(recovery_request())
    assert result.state["exit_code"] == code
    assert result.state["mutation_state"] == mutation
    assert result.state["recovery_directory"] == directory


def test_invalid_success_cannot_claim_no_capture_mutation():
    from taskman_ops.host_protocol.postgresql import reduce_postgresql_recovery_evidence
    request = recovery_request()
    result = recovery_result(request, mutation="unchanged", directory=None)
    assert reduce_postgresql_recovery_evidence(request, result)["mutation_state"] == "unknown"


def test_recovery_file_writer_is_private_and_never_overwrites(tmp_path):
    from taskman_ops.host_helper.postgresql_recovery import _write_private
    path = tmp_path / "postgresql.conf"
    _write_private(path, b"original-native-bytes\n")
    assert path.read_bytes() == b"original-native-bytes\n"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    with pytest.raises(FileExistsError):
        _write_private(path, b"overwrite-attempt")
    assert path.read_bytes() == b"original-native-bytes\n"


@pytest.mark.skipif(os.geteuid() != 0, reason="requires disposable root-owned local paths")
def test_failed_partial_capture_retains_private_originals(tmp_path, monkeypatch):
    from taskman_ops.host_helper import postgresql_recovery as module
    config, hba = tmp_path / "postgresql.conf", tmp_path / "pg_hba.conf"
    config.write_bytes(b"native-original\n")
    hba.write_bytes(b"native-hba\n")
    original = module._write_private
    def write(path, content):
        if path.name == "metadata.json":
            raise OSError("native-secret-canary")
        original(path, content)
    monkeypatch.setattr(module, "_write_private", write)
    with pytest.raises(module.PostgreSQLRecoveryError) as caught:
        module.capture_postgresql_config({**CLUSTER, "config_file": str(config), "hba_file": str(hba)}, root=tmp_path / "recovery")
    error = caught.value
    assert error.exit_code == 5 and error.mutation_state == "changed"
    saved = Path(error.recovery_directory)
    assert (saved / "postgresql.conf").read_bytes() == b"native-original\n"
    assert (saved / "pg_hba.conf").read_bytes() == b"native-hba\n"
    assert "native-secret-canary" not in str(error)


@pytest.mark.parametrize("bad_code", [True, {"secret": "canary"}, [8]])
def test_failure_reduction_rejects_untyped_classification_without_crashing(bad_code):
    from taskman_ops.host_helper.postgresql_recovery import postgresql_recovery_failure_result
    request = recovery_request()
    result = recovery_result(request, mutation="unchanged", directory=None, exit_code=5)
    malformed = replace(result, state={**result.to_mapping()["state"], "exit_code": bad_code})
    final = postgresql_recovery_failure_result(request, malformed)
    assert final.state["exit_code"] == 8
    assert final.state["mutation_state"] == "unknown"


@pytest.mark.parametrize("outcome,code", [("manual", 5), ("retryable", 10), ("refused", 5)])
def test_failure_reduction_rejects_wrong_category_outcome_pair(outcome, code):
    from taskman_ops.host_helper.postgresql_recovery import postgresql_recovery_failure_result
    request = recovery_request()
    result = replace(recovery_result(request, mutation="unchanged", directory=None, exit_code=code), outcome=outcome)
    final = postgresql_recovery_failure_result(request, result)
    assert final.state["exit_code"] == 8
    assert final.state["mutation_state"] == "unknown"


@pytest.mark.parametrize("cluster", [None, CLUSTER])
def test_failure_reduction_retains_proven_unchanged_with_optional_bound_cluster(cluster):
    from taskman_ops.host_helper.postgresql_recovery import postgresql_recovery_failure_result
    request = recovery_request()
    result = recovery_result(request, mutation="unchanged", directory=None, exit_code=5)
    result = replace(result, state={**result.to_mapping()["state"], "cluster": cluster})
    final = postgresql_recovery_failure_result(request, result)
    assert final.state["exit_code"] == 8
    assert final.state["mutation_state"] == "unchanged"
    assert final.state["cluster"] == cluster


@pytest.mark.parametrize("drift", ["replacement", "permissions", "content", "link"])
@pytest.mark.parametrize("moment", ["before-open", "after-read"])
def test_original_refuses_accidental_source_drift(tmp_path, monkeypatch, drift, moment):
    """Exercise real local descriptors with the local user as synthetic postgres."""
    from contextlib import contextmanager
    from types import SimpleNamespace
    from taskman_ops.host_helper import postgresql_recovery as recovery
    monkeypatch.setattr(recovery.pwd, "getpwnam", lambda _: SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid()))
    source, replacement = tmp_path / "postgresql.conf", tmp_path / "other.conf"
    source.write_bytes(b"original native settings\n")
    replacement.write_bytes(b"replacement native settings\n")
    source.chmod(0o600)
    replacement.chmod(0o600)
    tmp_path.chmod(0o700)
    def mutate():
        if drift == "replacement": replacement.replace(source)
        elif drift == "permissions": source.chmod(0o640)
        elif drift == "content": source.write_bytes(b"changed native settings!\n")
        else:
            source.unlink()
            source.symlink_to(replacement)
    if moment == "before-open":
        original_lstat = Path.lstat
        changed = False
        def inspect(path, *args, **kwargs):
            nonlocal changed
            info = original_lstat(path, *args, **kwargs)
            if path == source and not changed:
                changed = True
                mutate()
            return info
        monkeypatch.setattr(Path, "lstat", inspect)
    else:
        original_fdopen = os.fdopen
        @contextmanager
        def descriptor_stream(fd, *args, **kwargs):
            with original_fdopen(fd, *args, **kwargs) as stream:
                def read(limit):
                    content = stream.read(limit)
                    mutate()
                    return content
                yield SimpleNamespace(read=read, fileno=stream.fileno)
        monkeypatch.setattr(recovery.os, "fdopen", descriptor_stream)
    with pytest.raises(recovery.PostgreSQLRecoveryError) as raised:
        recovery._original(source)
    assert raised.value.exit_code == 10
    assert raised.value.mutation_state == "unchanged"
    assert raised.value.recovery_directory is None
    assert "native settings" not in str(raised.value)


def test_original_binds_stable_descriptor_bytes_and_metadata_without_atime(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from taskman_ops.host_helper import postgresql_recovery as recovery
    monkeypatch.setattr(recovery.pwd, "getpwnam", lambda _: SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid()))
    source = tmp_path / "postgresql.conf"
    source.write_bytes(b"stable native settings\n")
    source.chmod(0o640)
    tmp_path.chmod(0o700)
    os.utime(source, ns=(1, source.stat().st_mtime_ns))
    initial = source.stat()
    content, metadata = recovery._original(source)
    assert content == b"stable native settings\n"
    assert metadata == {"native_path": str(source), "uid": initial.st_uid, "gid": initial.st_gid,
        "mode": 0o640, "sha256": hashlib.sha256(content).hexdigest()}


def test_recovery_ancestor_accepts_the_actual_baseline_directory_declaration(monkeypatch):
    """Only metadata is synthetic; use baseline's actual pyinfra declaration."""
    from types import SimpleNamespace
    from pyinfra.operations import apt, files, server
    from taskman_ops.host import baseline
    from taskman_ops.host_helper import postgresql_recovery as recovery
    from taskman_ops.host_protocol.postgresql import POSTGRESQL_RECOVERY_ROOT
    from tests.support.environments import environment_config
    declarations = {}
    monkeypatch.setattr(apt, "packages", lambda **_: None)
    monkeypatch.setattr(baseline, "refresh_required_packages", lambda *_a, **_k: None)
    monkeypatch.setattr(server, "user", lambda *_a, **_k: None)
    monkeypatch.setattr(server, "service", lambda *_a, **_k: None)
    monkeypatch.setattr(files, "put", lambda *_a, **_k: None)
    monkeypatch.setattr(files, "directory", lambda path, **kwargs: declarations.update({path: kwargs}))
    monkeypatch.setattr(baseline, "declare_firewall", lambda *_a, **_k: None)
    baseline.declare_baseline(environment_config())
    ancestor = Path(POSTGRESQL_RECOVERY_ROOT).parent
    declaration = declarations[str(ancestor)]
    assert (declaration["user"], declaration["group"], declaration["mode"]) == ("root", "taskman", "750")
    metadata = SimpleNamespace(st_mode=stat.S_IFDIR | int(declaration["mode"], 8),
        st_uid=0, st_gid=1000)
    monkeypatch.setattr(Path, "resolve", lambda path: path)
    monkeypatch.setattr(Path, "lstat", lambda path: metadata)
    assert recovery._safe_parent(ancestor) is None


@pytest.mark.parametrize("uid,gid,mode,kind,linked,accepted", [
    (0, 0, 0o700, stat.S_IFDIR, False, True),
    (0, 1000, 0o750, stat.S_IFDIR, False, True),
    (0, 1001, 0o755, stat.S_IFDIR, False, True),
    (1000, 0, 0o700, stat.S_IFDIR, False, False),
    (0, 1000, 0o770, stat.S_IFDIR, False, False),
    (0, 0, 0o702, stat.S_IFDIR, False, False),
    (0, 1000, 0o2750, stat.S_IFDIR, False, False),
    (0, 1000, 0o750, stat.S_IFREG, False, False),
    (0, 1000, 0o750, stat.S_IFLNK, False, False),
    (0, 1000, 0o750, stat.S_IFDIR, True, False),
])
def test_recovery_ancestor_requires_root_ownership_and_no_write_or_link_authority(monkeypatch, uid, gid, mode, kind, linked, accepted):
    from types import SimpleNamespace
    from taskman_ops.host_helper import postgresql_recovery as recovery
    from taskman_ops.host_protocol.postgresql import POSTGRESQL_RECOVERY_ROOT
    ancestor = Path(POSTGRESQL_RECOVERY_ROOT).parent
    metadata = SimpleNamespace(st_mode=kind | mode, st_uid=uid, st_gid=gid)
    monkeypatch.setattr(Path, "resolve", lambda path: Path("/unsafe/linked") if linked else path)
    monkeypatch.setattr(Path, "lstat", lambda path: metadata)
    if accepted:
        assert recovery._safe_parent(ancestor) is None
    else:
        with pytest.raises(recovery.PostgreSQLRecoveryError) as raised:
            recovery._safe_parent(ancestor)
        assert raised.value.exit_code == 10
        assert raised.value.mutation_state == "unchanged"
        assert raised.value.recovery_directory is None
