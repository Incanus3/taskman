from __future__ import annotations

from dataclasses import replace
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from pyinfra.api import Config, Inventory, State, deploy
from tests.support.environments import environment_config
from tests.support.remotes import HelperRunnerRemote
from taskman_ops.errors import ExitStatus, OpsError
from taskman_ops.host_protocol import HostResult, decode_request, encode_result
from taskman_ops.host_helper.ufw_adapter import UfwInspectionError, UfwRule, UfwSnapshot
from taskman_ops.host_helper.operations import firewall as native
from taskman_ops.remote import CommandResult, PyinfraRemote, ChangeSet
import taskman_ops.host.firewall as firewall


DEFAULTS = (("default_forward_policy", "DROP"), ("default_input_policy", "DROP"),
            ("default_output_policy", "ACCEPT"), ("ipv6", "yes"))
IDENTITY = {"source_address": "192.0.2.1", "destination_address": "203.0.113.10"}
ADDITIONS = tuple(("ufw", action, "from", source, "to", "any", "port", str(port), "proto", "tcp")
    for action, values in (("allow", (2202, 80, 443)), ("deny", (4000, 5432, 6789, 4369)))
    for port in values for source in ("0.0.0.0/0", "::/0")) + (("ufw", "--force", "enable"),)


def rule(port: int, family: int = 4, action: str = "allow") -> UfwRule:
    any_address = "0.0.0.0/0" if family == 4 else "::/0"
    return UfwRule(family, "in", action, "tcp", any_address, any_address,
                   None, ((port, port),), "", "", "", "", "", "")


class FirewallRemote(HelperRunnerRemote):
    def __init__(self, monkeypatch):
        super().__init__(checksum="0" * 64, helper_result=CommandResult(0))
        self.snapshot = UfwSnapshot(False, DEFAULTS, ())
        self.present = True
        self.failure = None
        self.requests = []
        self.mutations = []
        monkeypatch.setenv("SSH_CONNECTION", "192.0.2.1 12345 203.0.113.10 2202")
        monkeypatch.setattr(native, "inspect_ufw", self.inspect)
        monkeypatch.setattr(native, "run_command", self.mutate)

    def inspect(self, **kwargs):
        if not self.present:
            raise UfwInspectionError("ufw_missing")
        if self.failure == "api":
            raise UfwInspectionError("inspection_failed")
        return self.snapshot

    def mutate(self, argv, **kwargs):
        self.mutations.append(tuple(argv))
        if argv[1:] == ("--force", "enable"):
            self.snapshot = replace(self.snapshot, active=True)
        else:
            port = int(argv[7])
            self.snapshot = replace(self.snapshot, rules=(*self.snapshot.rules,
                rule(port, 4 if argv[3] == "0.0.0.0/0" else 6, argv[1])))
        if self.failure == "native":
            return SimpleNamespace(returncode=1, stdout=b"", stderr=b"")
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    def put(self, source: Path, destination, **kwargs):
        self.checksum = hashlib.sha256(source.read_bytes()).hexdigest()
        return super().put(source, destination, **kwargs)

    def run(self, argv, **kwargs):
        if tuple(argv)[:3] == ("sudo", "--preserve-env=SSH_CONNECTION", "--"):
            self.calls.append((tuple(argv), kwargs))
            request = decode_request(kwargs["stdin"])
            self.requests.append(request)
            result = native.firewall(request)
            if request.parameters["mode"] == "converge" and self.failure == "lost":
                raise RuntimeError("lost result")
            if self.failure == "identity":
                result = replace(result, correlation_id="op-" + "f" * 32)
            return CommandResult(0, encode_result(result).decode())
        return super().run(argv, **kwargs)


def inspect(remote):
    return firewall.inspect_firewall(remote, environment_config())


def apply(remote, confirmed, monkeypatch):
    inventory = Inventory((["@local"], {}))
    state = State(inventory, Config(PARALLEL=1), check_for_changes=False)
    host = inventory.get_host("@local")
    state.activate_host(host)
    monkeypatch.setattr(firewall, "PyinfraRemote", lambda actual, config, **kwargs: remote)
    firewall._run_firewall_convergence(state, host, environment_config(), confirmed)
    return state


def test_inspection_uses_closed_helper_request_without_mutation(monkeypatch):
    remote = FirewallRemote(monkeypatch)
    result = inspect(remote)
    assert result.state["proposed"] == ADDITIONS
    assert result.state["administrator_connection"] == IDENTITY
    assert remote.mutations == []
    assert remote.requests[0].paths == {}
    assert remote.requests[0].expected_state == {"snapshot_sha256": None}
    command = next(argv for argv, _ in remote.calls if argv[0] == "sudo")
    assert command[3:6] == ("/usr/bin/python3", "-I", "-S")


def test_convergence_uses_current_channel_then_fresh_ssh_and_is_repeatable(monkeypatch):
    remote = FirewallRemote(monkeypatch)
    fresh = []
    monkeypatch.setattr(firewall, "verify_fresh_ssh_connection", lambda config: fresh.append(config))
    confirmed = inspect(remote)
    apply(remote, confirmed, monkeypatch)
    assert [argv[1:] for argv in remote.mutations] == [argv[1:] for argv in ADDITIONS]
    assert fresh == [environment_config()]
    assert remote.requests[-1].expected_state == {"snapshot_sha256": confirmed.state["snapshot_sha256"]}
    apply(remote, inspect(remote), monkeypatch)
    assert len(remote.mutations) == 15
    assert len(fresh) == 1


@pytest.mark.parametrize("failure,changed", (("native", True), ("lost", True), ("api", False)))
def test_failures_preserve_firewall_mutation_evidence(monkeypatch, failure, changed):
    remote = FirewallRemote(monkeypatch)
    confirmed = inspect(remote)
    remote.failure = failure
    with pytest.raises(OpsError) as raised:
        apply(remote, confirmed, monkeypatch)
    assert raised.value.status is ExitStatus.REMOTE_PREFLIGHT
    assert raised.value.stage == "firewall"
    assert raised.value.changed is changed


def test_invalid_inspection_identity_is_not_authority(monkeypatch):
    remote = FirewallRemote(monkeypatch)
    remote.failure = "identity"
    with pytest.raises(OpsError) as raised:
        inspect(remote)
    assert raised.value.status is ExitStatus.REMOTE_PREFLIGHT
    assert raised.value.changed is False


def test_policy_drift_refuses_before_mutation(monkeypatch):
    remote = FirewallRemote(monkeypatch)
    confirmed = inspect(remote)
    remote.snapshot = replace(remote.snapshot, rules=(rule(10050),))
    with pytest.raises(OpsError) as raised:
        apply(remote, confirmed, monkeypatch)
    assert raised.value.status is ExitStatus.SAFETY
    assert raised.value.changed is False
    assert remote.mutations == []


@pytest.mark.parametrize("forward", ("DROP", "REJECT"))
def test_absent_package_accepts_compatible_empty_policy_with_nonpermitting_forward_defaults(monkeypatch, forward):
    remote = FirewallRemote(monkeypatch)
    remote.present = False
    confirmed = inspect(remote)
    assert confirmed.state["reason"] == "ufw_missing"
    remote.present = True
    remote.snapshot = replace(remote.snapshot, defaults=(("default_forward_policy", forward), *DEFAULTS[1:]))
    monkeypatch.setattr(firewall, "verify_fresh_ssh_connection", lambda config: None)
    apply(remote, confirmed, monkeypatch)
    assert len(remote.mutations) == 15


@pytest.mark.parametrize("change", ("identity", "conflict", "api", "missing"))
def test_absent_package_refuses_conflict_unreadable_package_or_identity_change(monkeypatch, change):
    remote = FirewallRemote(monkeypatch)
    remote.present = False
    confirmed = inspect(remote)
    remote.present = True
    if change == "conflict":
        remote.snapshot = replace(remote.snapshot, rules=(rule(5432),))
    elif change == "identity":
        monkeypatch.setenv("SSH_CONNECTION", "192.0.2.2 12345 203.0.113.10 2202")
    elif change == "missing":
        remote.present = False
    else:
        remote.failure = "api"
    with pytest.raises(OpsError):
        apply(remote, confirmed, monkeypatch)
    assert remote.mutations == []


def test_installed_unreadable_api_is_failure_before_preview(monkeypatch):
    remote = FirewallRemote(monkeypatch)
    remote.failure = "api"
    with pytest.raises(OpsError) as raised:
        inspect(remote)
    assert raised.value.status is ExitStatus.REMOTE_PREFLIGHT
    assert remote.mutations == []


def test_missing_authority_refuses_before_package_declaration(monkeypatch):
    from pyinfra.operations import apt
    monkeypatch.setattr(apt, "packages", lambda **kwargs: pytest.fail("no package authorization"))
    with pytest.raises(OpsError):
        firewall.declare_firewall(environment_config(), confirmed=None)


def test_fresh_ssh_failure_retains_known_mutation(monkeypatch):
    remote = FirewallRemote(monkeypatch)
    confirmed = inspect(remote)
    monkeypatch.setattr(firewall, "verify_fresh_ssh_connection", lambda config: (_ for _ in ()).throw(
        OpsError(ExitStatus.REMOTE_PREFLIGHT, "firewall", "fresh SSH failed", False)))
    with pytest.raises(OpsError) as raised:
        apply(remote, confirmed, monkeypatch)
    assert raised.value.changed is True


def test_fresh_strict_connection_executes_true_and_closes():
    class Fresh:
        closed = False
        def run(self, argv):
            assert argv == ("true",)
            return CommandResult(0)
        def close(self):
            self.closed = True
    fresh = Fresh()
    firewall.verify_fresh_ssh_connection(environment_config(), connector=lambda config: fresh)
    assert fresh.closed is True


def test_local_packaging_failure_has_no_firewall_mutation_evidence(monkeypatch):
    remote = FirewallRemote(monkeypatch)
    confirmed = inspect(remote)
    def unavailable():
        raise RuntimeError("local packaging failed")
    monkeypatch.setattr(firewall, "temporary_helper_package", unavailable)
    with pytest.raises(OpsError) as raised:
        firewall._invoke(remote, firewall._request(environment_config(), mode="converge",
            digest=confirmed.state["snapshot_sha256"]))
    assert raised.value.changed is False
    assert remote.mutations == []


def test_addition_failure_after_success_preserves_known_and_possible_change(monkeypatch):
    remote = FirewallRemote(monkeypatch)
    confirmed = inspect(remote)
    original = remote.mutate
    def fail_second(argv, **kwargs):
        if remote.mutations:
            remote.failure = "native"
        return original(argv, **kwargs)
    monkeypatch.setattr(native, "run_command", fail_second)
    with pytest.raises(OpsError) as raised:
        apply(remote, confirmed, monkeypatch)
    assert raised.value.changed is True
    assert len(remote.mutations) == 2
    assert remote.snapshot.active is False


def test_source_exception_is_previewed_and_preserved(monkeypatch):
    remote = FirewallRemote(monkeypatch)
    exception = replace(rule(5432), src="192.0.2.0/24", comment="operator database access")
    remote.snapshot = replace(remote.snapshot, rules=(exception,))
    confirmed = inspect(remote)
    preview = firewall.firewall_preview(environment_config(), confirmed)
    assert preview["exceptions"][0]["comment"] == "operator database access"
    monkeypatch.setattr(firewall, "verify_fresh_ssh_connection", lambda config: None)
    apply(remote, confirmed, monkeypatch)
    assert remote.snapshot.rules[0] == exception
    assert all(argv[1] != "default" for argv in remote.mutations)


@pytest.mark.parametrize("mode", ("inspect", "converge"))
@pytest.mark.parametrize("local_cleanup", (False, True))
def test_invalid_reply_preserves_only_trusted_local_cleanup(monkeypatch, mode, local_cleanup):
    remote = FirewallRemote(monkeypatch)
    original = firewall.invoke_helper
    def mismatched(*args):
        result = original(*args)
        return replace(result, correlation_id="op-" + "f" * 32,
            warnings=("unvalidated remote warning",), local_cleanup_incomplete=local_cleanup)
    monkeypatch.setattr(firewall, "invoke_helper", mismatched)
    with pytest.raises(OpsError) as raised:
        firewall._invoke(remote, firewall._request(environment_config(), mode=mode,
            digest="a" * 64 if mode == "converge" else None))
    assert raised.value.warnings == (("transient helper cleanup was incomplete",) if local_cleanup else ())
    assert raised.value.changed is (mode == "converge")


@pytest.mark.parametrize("case", ("unrelated", "active-complete"))
def test_absent_package_binds_actual_compatible_saved_policy_and_preserves_rules(monkeypatch, case):
    remote = FirewallRemote(monkeypatch)
    remote.present = False
    confirmed = inspect(remote)
    remote.present = True
    saved = (replace(rule(10050), comment="operator monitor"),)
    if case == "active-complete":
        saved += tuple(rule(port, family, action) for family in (4, 6)
            for action, ports in (("allow", (2202, 80, 443)), ("deny", (4000, 5432, 6789, 4369))) for port in ports)
    remote.snapshot = replace(remote.snapshot, rules=saved, active=case == "active-complete")
    original = remote.snapshot
    actual = inspect(remote)
    maximum = firewall.firewall_preview(environment_config(), confirmed)["proposed"]
    if case == "unrelated":
        assert actual.state["proposed"] == maximum
        assert actual.state["snapshot_sha256"] != firewall._empty_delta(environment_config(), IDENTITY).snapshot_sha256
    else:
        assert actual.state["proposed"] == ()
    fresh = []
    monkeypatch.setattr(firewall, "verify_fresh_ssh_connection", lambda config: fresh.append(config))
    apply(remote, confirmed, monkeypatch)
    assert remote.requests[-1].expected_state == {"snapshot_sha256": actual.state["snapshot_sha256"]}
    assert native._preserved(original, remote.snapshot)
    assert remote.snapshot.active
    assert bool(fresh) == (case != "active-complete")
    assert len(remote.mutations) == len(actual.state["proposed"])


def test_missing_package_preview_explains_compatible_saved_policy_activation_and_addition_bound(monkeypatch):
    remote = FirewallRemote(monkeypatch)
    remote.present = False
    preview = firewall.firewall_preview(environment_config(), inspect(remote))
    assert preview["post_install_gate"] == "preserve compatible saved policy; actual coverage may reduce these maximum fallback additions"
    assert preview["activation"] == "activate preserved compatible saved rules if UFW is inactive"
    assert len(preview["proposed"]) == 15
