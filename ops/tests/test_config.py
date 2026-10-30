from __future__ import annotations

from io import StringIO
import json
from pathlib import Path, PurePosixPath
import subprocess

import pytest
import yaml
from pydantic import ValidationError

from taskman_ops.cli import main
from taskman_ops.config import EnvironmentConfig, load_environment
from taskman_ops.errors import ExitStatus, OpsError
from taskman_ops.output import WorkflowResult, render_error, render_human, render_json, render_table

from tests.support.environments import two_root_environment, valid_environment
from tests.support.secrets import no_registered_secrets_between_tests as no_registered_secrets_between_tests


def encrypted_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, payload: object,
                          *, name: str = "production") -> subprocess.CompletedProcess:
    (tmp_path / f"{name}.config.sops.yaml").write_text("ENC[synthetic]", encoding="utf-8")
    monkeypatch.setattr("taskman_ops.config.ENVIRONMENTS_DIR", tmp_path)
    completed = subprocess.CompletedProcess([], 0, bytearray(yaml.safe_dump(payload).encode()), bytearray())
    monkeypatch.setattr("taskman_ops.sops.subprocess.run", lambda *_args, **_kwargs: completed)
    return completed


def test_ssh_port_must_not_overlap_fixed_epmd_port() -> None:
    with pytest.raises(ValidationError, match="public and private service ports"):
        EnvironmentConfig.model_validate(valid_environment(ssh_port=4369))


def test_ordinary_ssh_port_remains_supported() -> None:
    assert EnvironmentConfig.model_validate(valid_environment(ssh_port=22)).ssh_port == 22


def test_complete_environment_is_frozen_and_normalizes_architecture_alias() -> None:
    config = EnvironmentConfig.model_validate(valid_environment())

    assert config.architecture == "amd64"
    assert config.ssh_port == 2202
    assert config.public_ipv4 == "203.0.113.10"
    with pytest.raises(ValidationError):
        config.application_port = 4001  # type: ignore[misc]


def test_two_root_defaults_derive_installation_subordinates_and_current_link() -> None:
    payload = valid_environment()
    payload.pop("install_root")
    payload.pop("backup_root")
    config = EnvironmentConfig.model_validate(payload)

    assert config.install_root == PurePosixPath("/opt/taskman")
    assert config.backup_root == PurePosixPath("/var/backups/taskman")
    assert config.release_root == PurePosixPath("/opt/taskman/releases")
    assert config.deployment_root == PurePosixPath("/opt/taskman/deployments")
    assert config.current_link == PurePosixPath("/opt/taskman/current")


@pytest.mark.parametrize(
    "field,value",
    [
        ("install_root", "/"),
        ("install_root", "/tmp/taskman"),
        ("backup_root", "/tmp/backups"),
        ("install_root", "relative/taskman"),
        ("backup_root", "/opt/taskman/../outside"),
        ("backup_root", "/opt/taskman"),
        ("backup_root", "/opt/taskman/backups"),
        ("backup_root", "/etc/taskman/backups"),
        ("install_root", "/run/taskman-ops/helpers"),
    ],
)
def test_two_root_paths_must_be_absolute_normalized_disjoint_and_reserved_free(field: str, value: str) -> None:
    with pytest.raises(ValidationError):
        EnvironmentConfig.model_validate(two_root_environment(**{field: value}))


def test_two_root_custom_installation_derives_all_subordinate_paths() -> None:
    config = EnvironmentConfig.model_validate(
        two_root_environment(install_root="/srv/taskman", backup_root="/srv/taskman-backups")
    )

    assert config.install_root == PurePosixPath("/srv/taskman")
    assert config.release_root == PurePosixPath("/srv/taskman/releases")
    assert config.deployment_root == PurePosixPath("/srv/taskman/deployments")
    assert config.current_link == PurePosixPath("/srv/taskman/current")


@pytest.mark.parametrize("name", ["../production", "/tmp", "production/name", "Production", ""])
def test_path_like_or_invalid_environment_names_are_refused(name: str) -> None:
    with pytest.raises(OpsError) as raised:
        load_environment(name)

    assert raised.value.status is ExitStatus.INVALID


@pytest.mark.parametrize("field", ["ssh_host", "public_hostname"])
@pytest.mark.parametrize("hostname", ["localhost", "localhost.localdomain", "127.0.0.1", "::1", "foo.local"])
def test_reserved_or_local_hostnames_are_refused(field: str, hostname: str) -> None:
    with pytest.raises(ValidationError):
        EnvironmentConfig.model_validate(valid_environment(**{field: hostname}))


@pytest.mark.parametrize("field,value", [("target_os", "ubuntu24.04"), ("target_os", "debian12"), ("architecture", "arm64")])
def test_unsupported_target_is_refused(field: str, value: str) -> None:
    with pytest.raises(ValidationError):
        EnvironmentConfig.model_validate(valid_environment(**{field: value}))


@pytest.mark.parametrize("target_os", ["ubuntu-26.04", "Ubuntu 26.04", "ubuntu26.04lts", "ubuntu2604"])
def test_target_os_requires_the_exact_supported_value(target_os: str) -> None:
    with pytest.raises(ValidationError):
        EnvironmentConfig.model_validate(valid_environment(target_os=target_os))


@pytest.mark.parametrize("field", ["ssh_port", "application_port", "distribution_port"])
@pytest.mark.parametrize("port", [0, -1, 65536, "4000", True])
def test_invalid_ports_are_refused(field: str, port: object) -> None:
    with pytest.raises(ValidationError):
        EnvironmentConfig.model_validate(valid_environment(**{field: port}))


@pytest.mark.parametrize(
    "overrides",
    [
        {"application_port": 2202},
        {"distribution_port": 2202},
        {"distribution_port": 4000},
        {"application_port": 5432},
    ],
)
def test_service_ports_must_not_overlap(overrides: dict[str, int]) -> None:
    with pytest.raises(ValidationError):
        EnvironmentConfig.model_validate(valid_environment(**overrides))


@pytest.mark.parametrize("field", ["database_name", "database_role"])
@pytest.mark.parametrize("identifier", ["1taskman", "task-man", "task.man", "", "a" * 64])
def test_database_identifiers_use_the_postgresql_allowlist(field: str, identifier: str) -> None:
    with pytest.raises(ValidationError):
        EnvironmentConfig.model_validate(valid_environment(**{field: identifier}))


@pytest.mark.parametrize(
    "overrides",
    [
        {"backup_root": "/opt/taskman/current"},
        {"backup_root": "/opt/taskman/releases/backups"},
        {"install_root": "/var/lib/taskman"},
        {"backup_root": "/var/lock/taskman/backups"},
    ],
)
def test_install_root_topology_rejects_overlap_and_reserved_path_collisions(
    overrides: dict[str, str],
) -> None:
    """Conflicting roots would apply incompatible owners/modes or delete authority."""

    with pytest.raises(ValidationError):
        EnvironmentConfig.model_validate(valid_environment(**overrides))


def test_non_default_disjoint_roots_remain_supported() -> None:
    """Topology hardening must retain the documented configurable-root contract."""

    config = EnvironmentConfig.model_validate(
        valid_environment(
            install_root="/srv/taskman",
            backup_root="/srv/taskman-backups",
        )
    )

    assert config.install_root.as_posix() == "/srv/taskman"
    assert config.release_root.as_posix() == "/srv/taskman/releases"
    assert config.deployment_root.as_posix() == "/srv/taskman/deployments"
    assert config.backup_root.as_posix() == "/srv/taskman-backups"


@pytest.mark.parametrize(
    "install_root",
    [
        "/srv/taskman\nExecStart=/bin/attacker",
        "/srv/taskman%N",
        "/srv/task man",
        "/srv/task\tman",
        "/srv/taskman'quoted",
        '/srv/taskman"quoted',
        "/srv/taskman\x01control",
        "/srv/taskman\x7fdel",
    ],
)
def test_systemd_rendered_roots_reject_unsafe_exec_path_characters(install_root: str) -> None:
    """An executable path must satisfy systemd's native safety contract."""

    with pytest.raises(ValidationError):
        EnvironmentConfig.model_validate(
            valid_environment(
                install_root=install_root,
            )
        )


@pytest.mark.parametrize("install_root", ["/srv/taskman$(printf${IFS}CANARY)", "/srv/taskman;:"])
def test_systemd_rendered_roots_keep_supported_shell_metacharacters(
    install_root: str,
) -> None:
    """Characters that systemd accepts must remain available as managed roots."""

    config = EnvironmentConfig.model_validate(
        valid_environment(install_root=install_root, backup_root="/srv/taskman-backups")
    )

    assert config.install_root.as_posix() == install_root


@pytest.mark.parametrize("overrides", [
    {"install_root": "/srv/taskman'quoted"},
    {"backup_retention": 65},
    {"release_retention": 65},
])
def test_cli_rejects_invalid_configuration_without_connecting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    overrides: dict[str, object],
) -> None:
    """Invalid configuration fails at the public CLI boundary before SSH can run."""

    payload = valid_environment(**overrides)
    encrypted_environment(tmp_path, monkeypatch, payload)
    monkeypatch.setattr(
        "taskman_ops.remote.connect",
        lambda *_: pytest.fail("invalid configuration must not connect to SSH"),
    )

    stderr = StringIO()
    assert main(["create-admin", "production"], stdout=StringIO(), stderr=stderr) == ExitStatus.INVALID
    assert "error (2)" in stderr.getvalue()


@pytest.mark.parametrize("field", ["backup_retention", "release_retention", "readiness_timeout", "connection_timeout", "pool_size"])
def test_retention_timeouts_and_pool_size_must_be_positive(field: str) -> None:
    with pytest.raises(ValidationError):
        EnvironmentConfig.model_validate(valid_environment(**{field: 0}))


@pytest.mark.parametrize("field", ["backup_retention", "release_retention"])
@pytest.mark.parametrize("value", [1, 64])
def test_retention_accepts_the_supported_range_boundaries(field: str, value: int) -> None:
    config = EnvironmentConfig.model_validate(valid_environment(**{field: value}))

    assert getattr(config, field) == value


def test_readiness_timeout_caps_each_independent_window() -> None:
    """Each polling phase rejects a configured window above thirty seconds."""

    with pytest.raises(ValidationError, match="readiness timeout"):
        EnvironmentConfig.model_validate(valid_environment(readiness_timeout=31))


@pytest.mark.parametrize("fingerprint", ["", "SHA256:short", "MD5:aa:bb", "SHA256:abc def", "not-a-fingerprint"])
def test_pinned_host_key_fingerprint_has_the_expected_shape(fingerprint: str) -> None:
    with pytest.raises(ValidationError):
        EnvironmentConfig.model_validate(valid_environment(host_key_fingerprint=fingerprint))


def test_unknown_environment_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        EnvironmentConfig.model_validate(valid_environment(unexpected="value"))


def test_load_environment_decrypts_stdout_injects_name_and_wipes_captures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = valid_environment()
    payload.pop("name")
    completed = encrypted_environment(tmp_path, monkeypatch, payload, name="staging")
    stdout, stderr = completed.stdout, completed.stderr
    calls = []

    def runner(argv: list[str]) -> subprocess.CompletedProcess:
        calls.append(argv)
        return completed

    config = load_environment("staging", runner)

    assert config.name == "staging"
    assert config.architecture == "amd64"
    assert calls == [["sops", "decrypt", "--output-type", "yaml", str(tmp_path / "staging.config.sops.yaml")]]
    assert stdout == stderr == bytearray()
    assert completed.stdout == completed.stderr == b""
    assert sorted(path.name for path in tmp_path.iterdir()) == ["staging.config.sops.yaml"]


@pytest.mark.parametrize("name", ["production", "example", "sandbox"])
def test_default_requires_encrypted_settings_even_with_plaintext_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str,
) -> None:
    (tmp_path / f"{name}.yaml").write_text(yaml.safe_dump(valid_environment(name=name)), encoding="utf-8")
    monkeypatch.setattr("taskman_ops.config.ENVIRONMENTS_DIR", tmp_path)

    with pytest.raises(OpsError) as raised:
        load_environment(name, lambda _: pytest.fail("missing encrypted settings must not invoke SOPS"))

    assert raised.value.status is ExitStatus.SECRET
    assert raised.value.stage == "configuration"


@pytest.mark.parametrize("name", ["production", "example"])
def test_encrypted_settings_are_authoritative_when_plaintext_coexists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str,
) -> None:
    encrypted_environment(tmp_path, monkeypatch, valid_environment(name=name, ssh_port=2222), name=name)
    (tmp_path / f"{name}.yaml").write_text(yaml.safe_dump(valid_environment(name=name, ssh_port=23)), encoding="utf-8")

    assert load_environment(name).ssh_port == 2222


@pytest.mark.parametrize("name", ["example", "sandbox"])
def test_named_plaintext_configuration_requires_explicit_opt_in_without_sops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str,
) -> None:
    monkeypatch.setattr("taskman_ops.config.ENVIRONMENTS_DIR", tmp_path)
    payload = valid_environment(name=name)
    (tmp_path / f"{name}.yaml").write_text(yaml.safe_dump(payload), encoding="utf-8")
    # The explicit mode selects plaintext even if encrypted settings coexist.
    (tmp_path / f"{name}.config.sops.yaml").write_text("ENC[synthetic]", encoding="utf-8")

    config = load_environment(name, lambda _: pytest.fail("plaintext mode must not invoke SOPS"), encrypted=False)

    assert config.name == name
    assert config.ssh_port == payload["ssh_port"]


@pytest.mark.parametrize("content", [None, "not: [private-parser-canary", "- private-parser-canary"])
def test_unavailable_or_malformed_plaintext_configuration_has_stable_status_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, content: str | None,
) -> None:
    monkeypatch.setattr("taskman_ops.config.ENVIRONMENTS_DIR", tmp_path)
    # A plaintext failure must not trigger a fallback to encrypted settings.
    (tmp_path / "sandbox.config.sops.yaml").write_text("ENC[synthetic]", encoding="utf-8")
    if content is not None:
        (tmp_path / "sandbox.yaml").write_text(content, encoding="utf-8")

    with pytest.raises(OpsError) as raised:
        load_environment("sandbox", lambda _: pytest.fail("plaintext mode must not invoke SOPS"), encrypted=False)

    assert raised.value.status is ExitStatus.INVALID
    assert raised.value.__cause__ is raised.value.__context__ is None
    assert "canary" not in repr(raised.value)
    assert "canary" not in render_error(raised.value, json_output=True)


@pytest.mark.parametrize("output,returncode", [
    (b"private-output-canary", 1), (b"", 0), (b"\xffprivate-output-canary", 0),
    (b"not: [private-output-canary", 0), (b"- private-output-canary", 0),
])
def test_encrypted_settings_failures_are_safe_and_wipe_sops_captures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, output: bytes, returncode: int,
) -> None:
    encrypted_environment(tmp_path, monkeypatch, {})
    stdout, stderr = bytearray(output), bytearray(b"private-stderr-canary")
    completed = subprocess.CompletedProcess([], returncode, stdout, stderr)

    with pytest.raises(OpsError) as raised:
        load_environment("production", lambda _: completed)

    error = raised.value
    assert error.status is ExitStatus.SECRET
    assert error.__cause__ is error.__context__ is None
    assert "canary" not in repr(error)
    assert "canary" not in render_error(error, json_output=True)
    assert stdout == stderr == bytearray()
    assert completed.stdout == completed.stderr == b""


def test_missing_sops_or_identity_errors_do_not_expose_local_details(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    encrypted_environment(tmp_path, monkeypatch, {})

    def runner(_: list[str]) -> subprocess.CompletedProcess:
        raise OSError("private-identity-location-canary")

    with pytest.raises(OpsError) as raised:
        load_environment("production", runner)

    assert raised.value.status is ExitStatus.SECRET
    assert raised.value.__context__ is None
    assert "canary" not in repr(raised.value)


@pytest.mark.parametrize("overrides", [
    {"name": "different"}, {"ssh_host": "private-invalid\nhost-canary"},
    {"ssh_port": "2202"}, {"unexpected": "private-unexpected-canary"},
])
@pytest.mark.parametrize("encrypted", [True, False])
def test_invalid_settings_keep_status_two_and_no_raw_error_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, overrides: dict[str, object], encrypted: bool,
) -> None:
    payload = valid_environment(**overrides)
    completed = encrypted_environment(tmp_path, monkeypatch, payload)
    if not encrypted:
        (tmp_path / "production.yaml").write_text(yaml.safe_dump(payload), encoding="utf-8")

    with pytest.raises(OpsError) as raised:
        load_environment("production", encrypted=encrypted)

    assert raised.value.status is ExitStatus.INVALID
    assert raised.value.__cause__ is raised.value.__context__ is None
    assert "canary" not in repr(raised.value)
    if encrypted:
        assert completed.stdout == completed.stderr == b""


@pytest.mark.parametrize("encrypted", [True, False])
def test_settings_and_normalized_alias_values_remain_visible_in_operator_reports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, encrypted: bool,
) -> None:
    payload = valid_environment(
        ssh_host="SSH.PRIVATE.TLD.", ssh_user="private_operator",
        host_key_fingerprint="SHA256:" + "B" * 43,
        public_hostname="APP.PRIVATE.TLD.", public_ipv4="203.0.113.48",
        public_ipv6="2001:db8::48", database_name="private_database", database_role="private_role",
        mail_from="SENDER@PRIVATE.TLD", dns_cluster_query="cluster.private.tld",
        install_root="/srv/private-install", backup_root="/srv/private-backups",
    )
    # Input aliases must remain reportable like canonical fields.
    payload["administrator_user"] = payload.pop("ssh_user")
    encrypted_environment(tmp_path, monkeypatch, payload)
    if not encrypted:
        (tmp_path / "production.yaml").write_text(yaml.safe_dump(payload), encoding="utf-8")
    config = load_environment("production", encrypted=encrypted)
    settings_values = [str(getattr(config, field)) for field in (
        "ssh_host", "ssh_user", "host_key_fingerprint", "public_hostname", "public_ipv4",
        "public_ipv6", "database_name", "database_role", "mail_from", "dns_cluster_query",
        "install_root", "backup_root",
    )]
    settings_values += [payload["ssh_host"], payload["public_hostname"], payload["mail_from"]]
    result = WorkflowResult(command="verify", environment="production", changed=False,
                            stage="verified", facts={"values": settings_values})
    rendered = [render_human(result), render_json(result), render_table({"values": settings_values}),
                render_error(OpsError(ExitStatus.INVALID, "configuration", " ".join(settings_values)))]

    for report in rendered:
        assert all(value in report for value in settings_values)
    assert "production" in render_human(result)


@pytest.mark.parametrize("encrypted", [True, False])
@pytest.mark.parametrize("identifier", ["taskman", "a"])
def test_settings_preserve_report_structure_commands_and_release_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, encrypted: bool, identifier: str,
) -> None:
    payload = valid_environment(database_role=identifier, database_name=identifier)
    encrypted_environment(tmp_path, monkeypatch, payload)
    if not encrypted:
        (tmp_path / "production.yaml").write_text(yaml.safe_dump(payload), encoding="utf-8")
    config = load_environment("production", encrypted=encrypted)
    facts = {
        "database_role": config.database_role,
        "taskman_service": "taskman.service",
        "release_id": "0.2.0-abcdef123456-ubuntu26.04-amd64-otp29.0.6",
        "sha256": "a" * 64,
    }
    next_action = "./ops/taskman provision production"
    result = WorkflowResult(command="verify", environment="production", changed=False,
                            stage="verified", facts=facts, next_action=next_action)
    report = json.loads(render_json(result))
    assert report["facts"] == facts
    assert report["next_action"] == next_action
    for rendered in (render_human(result), render_table(facts)):
        assert all(key in rendered and value in rendered for key, value in facts.items())
    assert next_action in render_human(result)
    error = OpsError(ExitStatus.INVALID, "configuration", "retry provision", next_action=next_action)
    assert next_action in render_error(error)
    assert json.loads(render_error(error, json_output=True))["next_action"] == next_action


@pytest.mark.parametrize("root,expected", [("/opt/taskman", "/opt/taskman/uploads"), ("/srv/custom", "/srv/custom/uploads")])
def test_upload_root_is_derived_from_installation(root, expected):
    config = EnvironmentConfig.model_validate(two_root_environment(install_root=root))
    assert config.upload_root == PurePosixPath(expected)
    assert config.model_dump()["install_root"] == PurePosixPath(root)
    assert "upload_root" not in config.model_dump()
