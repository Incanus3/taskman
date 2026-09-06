"""Real zipapp characterization for local deployed-host verification."""

from __future__ import annotations

from datetime import UTC, datetime
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from taskman_ops.helper_package import build_helper_package
from taskman_ops.host_helper.verification import _MAX_OUTPUT, _successful
from taskman_ops.host_protocol import HostRequest, decode_result, encode_request


RELEASE_ID = "0.2.0-aaaaaaaaaaaa-ubuntu26.04-amd64-otp27.3.4.6"


def _record(value: Path, payload: object) -> None:
    value.parent.mkdir(parents=True, exist_ok=True)
    value.parent.parent.chmod(0o750)
    value.parent.chmod(0o750)
    value.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    value.chmod(0o600)


def _managed_root(tmp_path: Path) -> Path:
    install = tmp_path / "install"
    release = install / "releases" / RELEASE_ID
    release.mkdir(parents=True)
    release.parent.chmod(0o750)
    release.chmod(0o750)
    (install / "current").symlink_to(release)
    at = datetime(2026, 9, 5, 12, 0, tzinfo=UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    _record(
        install / "deployments" / "releases" / f"release-{RELEASE_ID}.json",
        {"schema_version": 1, "release_id": RELEASE_ID, "artifact_sha256": "a" * 64, "installed_at": at, "activated_at": at, "previous_release_id": None, "backup_id": None, "migration_policy": "no-change"},
    )
    _record(
        install / "deployments" / "activations" / "activation-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.json",
        {"schema_version": 1, "activation_id": "activation-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "previous_release_id": None, "candidate_release_id": RELEASE_ID, "activated_at": at, "backup_id": None, "migration_policy": "no-change"},
    )
    _record(
        install / "deployments" / "manifests" / f"release-{RELEASE_ID}.json",
        {"schema_version": 2, "application": "taskman", "application_version": "0.2.0", "source_revision": "a" * 40, "release_id": RELEASE_ID, "built_at": at, "target_os": "ubuntu26.04", "architecture": "amd64", "otp_version": "27.3.4.6", "elixir_version": "1.18.3", "node_version": "22.22.1", "hex_version": "2.5.1", "rebar3_version": "3.24.0", "builder_base_tag": "ubuntu:resolute-20260811.1", "builder_base_digest": "sha256:2260313b31c8c011cd2eebe728008efac1b3982be73eb71348ea2648d2c0e09b", "migrations": [], "top_level": "taskman"},
    )
    return install


def _commands(
    root: Path,
    release: Path,
    *,
    hsts: bool = True,
    dns_address: str = "203.0.113.10",
    dns_ipv6: str | None = None,
    failures: frozenset[str] = frozenset(),
) -> Path:
    commands = root / "commands"
    commands.mkdir()
    scripts = {
        "systemctl": "#!/bin/sh\ncase \"$*\" in *show*) printf 'active\\n4242\\n' ;; *is-active*) exit 0 ;; *) exit 2 ;; esac\n",
        "cat": "#!/bin/sh\ncase \"$1\" in /etc/os-release) printf 'ID=" + ("debian" if "os" in failures else "ubuntu") + "\\nVERSION_ID=26.04\\n' ;; /proc/1/comm) printf '" + ("init" if "systemd" in failures else "systemd") + "\\n' ;; *) exit 2 ;; esac\n",
        "uname": "#!/bin/sh\nprintf '" + ("aarch64" if "architecture" in failures else "x86_64") + "\\n'\n",
        "getent": f"#!/bin/sh\nif [ \"$1\" = ahostsv6 ]; then printf '{dns_ipv6 or dns_address} STREAM taskman.acme.tld\\n'; else printf '{dns_address} STREAM taskman.acme.tld\\n{dns_address} DGRAM taskman.acme.tld\\n'; fi\n",
        "sudo": "#!/bin/sh\nexit 0\n",
        "runuser": "#!/bin/sh\ncase \"$*\" in *'sudo -n true'*) " + ("exit 1" if "sudo" in failures else "exit 0") + " ;; *psql*) " + ("exit 1" if "postgres" in failures else "printf '1\\n'") + " ;; *) exit 2 ;; esac\n",
        "free": "#!/bin/sh\nprintf 'Mem: " + ("536870912" if "memory" in failures else "2147483648") + " 0 0 0 0 0\\n'\n",
        "df": "#!/bin/sh\nprintf 'Avail\\n" + ("1073741824" if "capacity" in failures else "21474836480") + "\\n'\n",
        "readlink": f"#!/bin/sh\nprintf '%s\\n' '{release}/erts-27/bin/beam.smp'\n",
        "ss": "#!/bin/sh\nprintf 'LISTEN 0 4096 0.0.0.0:22 0.0.0.0:*\\nLISTEN 0 4096 127.0.0.1:4000 0.0.0.0:*\\nLISTEN 0 4096 [::1]:6789 [::]:*\\nLISTEN 0 4096 127.0.0.1:5432 0.0.0.0:*\\n'\n",
        "journalctl": "#!/bin/sh\nprintf 'started cleanly\\n'\n",
        "curl": "#!/bin/sh\nprintf 'HTTP/1.1 200 OK\\r\\nCache-Control: no-store\\r\\n" + ("Strict-Transport-Security: max-age=1\\r\\n" if hsts else "") + "\\r\\nready'\n",
    }
    for name, script in scripts.items():
        path = commands / name
        path.write_text(script, encoding="utf-8")
        path.chmod(0o700)
    return commands


def _verify(
    root: Path,
    install: Path,
    commands: Path,
    *,
    public_ipv6: str | None = None,
    expected_release_id: str | None = RELEASE_ID,
    environment: dict[str, str | None] | None = None,
):
    package = build_helper_package(root / "taskman-host.pyz")
    request = HostRequest(
        protocol_version=1,
        operation="verify",
        operation_id="op-0123456789abcdef0123456789abcdef",
        expected_state={"expected_release_id": expected_release_id},
        paths={"install_root": str(install), "backup_root": str(root / "backups")},
        parameters={"application_port": 4000, "distribution_port": 6789, "database_port": 5432, "public_hostname": "taskman.acme.tld", "public_ipv4": "203.0.113.10", "public_ipv6": public_ipv6, "ssh_port": 22, "ssh_user": "deployer", "readiness_timeout": 1, "connection_timeout": 1},
    )
    helper_environment = {
        **os.environ,
        "PATH": f"{commands}:{os.environ['PATH']}",
        "SUDO_USER": "deployer",
        # The fixed runner preserves this real OpenSSH session fact across
        # sudo; sudo itself does not invent a connection variable.
        "SSH_CONNECTION": "198.51.100.42 54321 203.0.113.10 22",
    }
    for key, value in (environment or {}).items():
        if value is None:
            helper_environment.pop(key, None)
        else:
            helper_environment[key] = value
    completed = subprocess.run(
        [sys.executable, "-I", str(package.path)],
        input=encode_request(request),
        capture_output=True,
        check=False,
        env=helper_environment,
    )
    assert completed.returncode == 0
    assert completed.stderr == b""
    return decode_result(completed.stdout)


def test_built_zipapp_verifies_services_topology_and_readiness_once(tmp_path: Path) -> None:
    """A successful verification carries the established fixed check sequence."""

    install = _managed_root(tmp_path)
    result = _verify(tmp_path, install, _commands(tmp_path, install / "releases" / RELEASE_ID))

    assert result.outcome == "succeeded"
    assert result.stage == "verified"
    assert [item["name"] for item in result.verification["checks"]] == [
        "taskman-service", "release-identity", "caddy-service", "listener-topology",
        "startup-journal", "local-readiness", "public-readiness", "public-hsts",
    ]


def test_built_zipapp_verifies_the_actual_current_release_without_a_deploy_assertion(tmp_path: Path) -> None:
    """Public verification records no expected release while still proving a managed current one."""

    install = _managed_root(tmp_path)
    result = _verify(
        tmp_path,
        install,
        _commands(tmp_path, install / "releases" / RELEASE_ID),
        expected_release_id=None,
    )

    assert result.outcome == "succeeded"
    assert result.verification["expected_release_id"] is None
    assert result.verification["release_id"] == RELEASE_ID


def test_built_zipapp_reports_hsts_failure_without_rechecking_host_state(tmp_path: Path) -> None:
    """Public ready and HSTS remain distinct bounded verification results."""

    install = _managed_root(tmp_path)
    result = _verify(tmp_path, install, _commands(tmp_path, install / "releases" / RELEASE_ID, hsts=False))

    assert result.outcome == "failed"
    assert result.stage == "verification"
    assert result.verification["checks"][-2]["status"] == "passed"
    assert result.verification["checks"][-1]["status"] == "failed"


def test_built_zipapp_refuses_success_when_public_dns_targets_another_host(tmp_path: Path) -> None:
    """A healthy endpoint elsewhere cannot satisfy this host's verification."""

    install = _managed_root(tmp_path)
    result = _verify(
        tmp_path,
        install,
        _commands(tmp_path, install / "releases" / RELEASE_ID, dns_address="198.51.100.42"),
    )

    assert result.outcome == "failed"
    assert result.stage == "host-preflight"
    assert result.runtime_state == {"host_authority": "unsupported"}


def test_built_zipapp_refuses_a_symlinked_authority_root_before_capacity_observation(tmp_path: Path) -> None:
    """Capacity discovery must not follow an existing caller-selected root link."""

    install = _managed_root(tmp_path)
    target = tmp_path / "elsewhere"
    target.mkdir()
    (tmp_path / "backups").symlink_to(target, target_is_directory=True)

    result = _verify(tmp_path, install, _commands(tmp_path, install / "releases" / RELEASE_ID))

    assert result.outcome == "failed"
    assert result.stage == "host-preflight"
    assert result.runtime_state == {"host_authority": "unsupported"}


def test_built_zipapp_requires_every_configured_public_address(tmp_path: Path) -> None:
    """IPv6, when configured, participates in the same host-identity proof."""

    install = _managed_root(tmp_path)
    result = _verify(
        tmp_path,
        install,
        _commands(tmp_path, install / "releases" / RELEASE_ID, dns_ipv6="2001:db8::10"),
        public_ipv6="2001:db8::10",
    )

    assert result.outcome == "succeeded"
    assert result.stage == "verified"


@pytest.mark.parametrize(
    ("failure", "environment", "authority"),
    (
        ("os", {}, "unsupported"),
        ("architecture", {}, "unsupported"),
        ("systemd", {}, "unsupported"),
        ("memory", {}, "unsupported"),
        ("capacity", {}, "unsupported"),
        ("dns", {}, "unsupported"),
        ("sudo", {}, "preflight"),
        ("postgres", {}, "preflight"),
        ("ssh", {"SSH_CONNECTION": "198.51.100.42 54321 203.0.113.10 2200"}, "preflight"),
        ("invoker", {"SUDO_USER": "intruder"}, "preflight"),
        ("connection", {"SSH_CONNECTION": None}, "preflight"),
    ),
)
def test_built_zipapp_refuses_each_host_prerequisite_with_stable_authority_status(
    tmp_path: Path,
    failure: str,
    environment: dict[str, str | None],
    authority: str,
) -> None:
    """Root execution must prove the original SSH administrator, not just root itself."""

    install = _managed_root(tmp_path)
    commands = _commands(
        tmp_path,
        install / "releases" / RELEASE_ID,
        dns_address="198.51.100.42" if failure == "dns" else "203.0.113.10",
        failures=frozenset() if failure in {"dns", "ssh", "invoker", "connection"} else frozenset({failure}),
    )

    result = _verify(tmp_path, install, commands, environment=environment)

    assert result.outcome == "failed"
    assert result.stage == "host-preflight"
    assert result.runtime_state == {"host_authority": authority}


def test_verification_command_reader_refuses_output_beyond_its_stream_bound() -> None:
    """A hostile command cannot make verification buffer arbitrary output."""

    succeeded, output = _successful(
        (sys.executable, "-c", f"import sys; sys.stdout.write('x' * {_MAX_OUTPUT + 1})"),
        2,
    )

    assert not succeeded
    assert output == ""
