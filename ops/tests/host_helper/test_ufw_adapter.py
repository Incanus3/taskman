"""Read-only normalization and isolation of the ordinary UFW adapter."""

from __future__ import annotations

import builtins
from contextlib import contextmanager
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from taskman_ops.host_helper import ufw_adapter
from taskman_ops.host_helper.ufw_adapter import UfwInspectionError, inspect_ufw


@contextmanager
def patch_read_backend(callback):
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(ufw_adapter, "_read_backend", lambda **kwargs: callback())
        yield


def rule(**overrides):
    fields = dict(v6=False, forward=False, action="allow", direction="in", protocol="tcp",
                  src="any", dst="any", sport="any", dport="22", interface_in="",
                  interface_out="", logtype="", sapp="", dapp="", comment="646220657863657074696f6e")
    fields.update(overrides)
    return SimpleNamespace(**fields, get_comment=lambda: bytes.fromhex(fields["comment"]).decode())


def backend(v4=(), v6=(), **defaults):
    settings = dict(default_input_policy="drop", default_output_policy="accept",
                    default_forward_policy="drop", ipv6="yes", enabled="no")
    settings.update(defaults)
    return SimpleNamespace(defaults=settings, get_rules_ipv4=lambda: list(v4),
                           get_rules_ipv6=lambda: list(v6))


@pytest.fixture
def native(monkeypatch):
    calls = []
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, b"Status: active\n", b"")
    monkeypatch.setattr(ufw_adapter, "run_command", run)
    return calls


def inspect():
    return inspect_ufw(deadline=time.monotonic() + 45)


def test_normalizes_ordered_families_saved_expansion_and_comments(native):
    saved = backend(
        [rule(src="192.0.2.17/24", dst="198.51.100.7", dport="80,443,6000:6010",
              sport="1024:2048", logtype="log", dapp="Web Test"),
         rule(action="deny", direction="out", protocol="udp", dport="any", interface_out="eth0")],
        [rule(v6=True, src="2001:db8::1/64", dport="5432", sport="123", sapp="Client")])
    with patch_read_backend(lambda: saved):
        snapshot = inspect()
    assert snapshot.active is True  # persisted enabled=no is not runtime evidence
    assert snapshot.defaults == (("default_forward_policy", "DROP"), ("default_input_policy", "DROP"),
                                 ("default_output_policy", "ACCEPT"), ("ipv6", "yes"))
    assert [r.family for r in snapshot.rules] == [4, 4, 6]
    first, second, third = snapshot.rules
    assert (first.src, first.dst) == ("192.0.2.0/24", "198.51.100.7/32")
    assert first.dports == ((80, 80), (443, 443), (6000, 6010))
    assert first.sports == ((1024, 2048),)
    assert (first.comment, first.dapp, first.logtype) == ("db exception", "Web Test", "log")
    assert (second.direction, second.protocol, second.dports, second.interface_out) == ("out", "udp", None, "eth0")
    assert (third.src, third.dst, third.sapp) == ("2001:db8::/64", "::/0", "Client")
    assert native[0][0] == ("/usr/sbin/ufw", "status")
    assert native[0][1]["env"]["LC_ALL"] == "C"
    assert 0 < native[0][1]["timeout_seconds"] <= 45
    assert native[0][1]["output_limit"] == 1024 * 1024


@pytest.mark.parametrize("field,value", [
    ("v6", 1), ("forward", True), ("action", "accept"), ("protocol", "icmp"),
    ("direction", "sideways"), ("src", "host.example"), ("dst", "::/0"),
    ("sport", "0"), ("dport", "65536"), ("dport", "40:20"), ("dport", "80,,443"),
    ("interface_in", "!eth0"), ("logtype", "unknown"), ("comment", "not-hex"),
    ("dapp", 12), ("forward", "false")])
def test_malformed_exposed_rule_is_refused(native, field, value):
    with patch_read_backend(lambda: backend([rule(**{field: value})])):
        with pytest.raises(UfwInspectionError, match="unsupported_state"):
            inspect()


@pytest.mark.parametrize("emit", [lambda: print("warning"),
    lambda: sys.stderr.buffer.write(b"WARN: malformed tuple\n"),
    lambda: sys.stdout.buffer.write(b"x" * (1024 * 1024 + 1))])
def test_capture_accepts_ufw_bytes_and_restores_streams(native, emit):
    before = (sys.stdout, sys.stderr, getattr(builtins, "_", None))
    with pytest.raises(UfwInspectionError):
        with patch_read_backend(emit):
            inspect()
    assert (sys.stdout, sys.stderr, getattr(builtins, "_", None)) == before


def test_restores_translation_after_normalization_raises(native, monkeypatch):
    sentinel = object()
    monkeypatch.setattr(builtins, "_", sentinel, raising=False)
    before = (sys.stdout, sys.stderr)
    with patch_read_backend(lambda: backend([rule(src="broken")])):
        with pytest.raises(UfwInspectionError):
            inspect()
    assert builtins._ is sentinel
    assert (sys.stdout, sys.stderr) == before


@pytest.mark.parametrize("header", [b"active\n", b"Status: active-ish\n", b"", b"Status: inactive\nStatus: active\n"])
def test_requires_unambiguous_exact_native_status(monkeypatch, header):
    monkeypatch.setattr(ufw_adapter, "run_command", lambda argv, **kwargs:
                        subprocess.CompletedProcess(argv, 0, header, b""))
    with patch_read_backend(lambda: backend()):
        with pytest.raises(UfwInspectionError):
            inspect()


def test_inactive_status_supported(native, monkeypatch):
    monkeypatch.setattr(ufw_adapter, "run_command", lambda argv, **kwargs:
                        subprocess.CompletedProcess(argv, 0, b"Status: inactive\n", b""))
    with patch_read_backend(lambda: backend()):
        assert inspect().active is False


def test_limits_each_rule_family(native):
    with patch_read_backend(lambda: backend([rule()] * 4097)):
        with pytest.raises(UfwInspectionError):
            inspect()


def test_expired_deadline_never_loads_backend(native):
    def unexpected():
        pytest.fail("expired inspection constructed a backend")
    with patch_read_backend(unexpected):
        with pytest.raises(UfwInspectionError, match="inspection_failed"):
            inspect_ufw(deadline=time.monotonic() - 1)


def test_deadline_checked_after_read(native):
    def late():
        return backend()
    with patch_read_backend(late), pytest.MonkeyPatch.context() as patch:
        ticks = iter([1.0, 1.0, 50.0])
        patch.setattr(ufw_adapter.time, "monotonic", lambda: next(ticks, 50.0))
        with pytest.raises(UfwInspectionError, match="inspection_failed"):
            inspect_ufw(deadline=45.0)


def test_named_ports_resolve_for_protocol_and_reject_ambiguous_omission(native, monkeypatch):
    monkeypatch.setattr(ufw_adapter.socket, "getservbyname", lambda name, proto: 22 if proto == "tcp" else 2222)
    with patch_read_backend(lambda: backend([rule(dport="ssh")])):
        assert inspect().rules[0].dports == ((22, 22),)
    with patch_read_backend(lambda: backend([rule(protocol="any", dport="ssh")])):
        with pytest.raises(UfwInspectionError):
            inspect()


def test_profile_saved_expansion_must_be_numeric(native):
    with patch_read_backend(lambda: backend([rule(dapp="Web Test", dport="http")])):
        with pytest.raises(UfwInspectionError):
            inspect()


@pytest.fixture
def synthetic_package(tmp_path, monkeypatch):
    package = tmp_path / "ufw"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "util.py").write_text("def cmd(command):\n    raise AssertionError('unbounded command hook')\n")
    (package / "backend_iptables.py").write_text('''import ufw.util
class UFWBackendIptables:
    def __init__(self, dryrun):
        assert dryrun is True
        ufw.util.cmd(['/usr/sbin/iptables', '-V'])
        self.defaults = {'default_input_policy': 'drop', 'default_output_policy': 'accept',
                         'default_forward_policy': 'drop', 'ipv6': 'yes'}
    def get_rules_ipv4(self): return []
    def get_rules_ipv6(self): return []
''')
    monkeypatch.setattr(ufw_adapter, "_UFW_PACKAGE", package)
    # Synthetic source is deliberately unprivileged; trust checks are tested independently.
    monkeypatch.setattr(ufw_adapter, "_trusted_path", lambda path: None)
    return package


def test_loading_only_probes_bounded_version_and_restores_namespace(native, synthetic_package):
    before = {key: value for key, value in sys.modules.items() if key == "ufw" or key.startswith("ufw.")}
    original_path = list(sys.path)
    original_bytecode_flag = sys.dont_write_bytecode
    assert inspect().rules == ()
    assert not (synthetic_package / "__pycache__").exists()
    assert sys.dont_write_bytecode is original_bytecode_flag
    assert [argv for argv, _ in native] == [("/usr/sbin/iptables", "-V"), ("/usr/sbin/ufw", "status")]
    assert {key: value for key, value in sys.modules.items() if key == "ufw" or key.startswith("ufw.")} == before
    assert sys.path == original_path


@pytest.mark.parametrize("failure", ["import", "version", "unexpected_probe"])
def test_failed_initialization_restores_globals(native, synthetic_package, monkeypatch, failure):
    if failure == "import":
        (synthetic_package / "backend_iptables.py").write_text("raise RuntimeError('bad import')\n")
    elif failure == "unexpected_probe":
        path = synthetic_package / "backend_iptables.py"
        path.write_text(path.read_text().replace("'/usr/sbin/iptables', '-V'", "'/usr/sbin/iptables', '-N', 'unsafe'"))
    else:
        def fail(*args, **kwargs):
            raise ufw_adapter.CommandError("native failure")
        monkeypatch.setattr(ufw_adapter, "run_command", fail)
    before = (sys.stdout, sys.stderr, getattr(builtins, "_", None))
    with pytest.raises(UfwInspectionError):
        inspect()
    assert (sys.stdout, sys.stderr, getattr(builtins, "_", None)) == before
    assert not any(key == "ufw" or key.startswith("ufw.") for key in sys.modules)
    if failure == "unexpected_probe":
        assert native == []


def test_rejects_preloaded_ufw_namespace_before_using_it(native, monkeypatch):
    alien = SimpleNamespace(__file__="/tmp/alien/ufw/util.py")
    monkeypatch.setitem(sys.modules, "ufw.util", alien)
    with pytest.raises(UfwInspectionError, match="unsupported_state"):
        inspect()
    assert sys.modules["ufw.util"] is alien
    assert native == []


def test_missing_system_package_is_distinct_from_untrusted_package(native, tmp_path, monkeypatch):
    monkeypatch.setattr(ufw_adapter, "_UFW_PACKAGE", tmp_path / "absent")
    with pytest.raises(UfwInspectionError, match="ufw_missing"):
        inspect()
    package = tmp_path / "ufw"
    package.mkdir()
    (package / "__init__.py").write_text("")
    monkeypatch.setattr(ufw_adapter, "_UFW_PACKAGE", package)
    with pytest.raises(UfwInspectionError, match="unsupported_state"):
        inspect()


@pytest.mark.parametrize("key,value", [("ipv6", "perhaps"), ("default_input_policy", "other"),
                                       ("default_output_policy", None)])
def test_invalid_defaults_refused(native, key, value):
    with patch_read_backend(lambda: backend(**{key: value})):
        with pytest.raises(UfwInspectionError, match="unsupported_state"):
            inspect()


def test_backend_warning_from_getter_is_refused(native):
    saved = backend()
    def getter():
        sys.stderr.buffer.write(b"WARN: skipped rule\n")
        return []
    saved.get_rules_ipv6 = getter
    with patch_read_backend(lambda: saved):
        with pytest.raises(UfwInspectionError):
            inspect()


@pytest.fixture
def official_reader(tmp_path, monkeypatch):
    """Opt-in operator-provided Ubuntu UFW source; no downloads or source authentication."""
    import os
    from pathlib import Path

    configured = os.environ.get("TASKMAN_TEST_UFW_PACKAGE")
    if not configured:
        pytest.skip("set TASKMAN_TEST_UFW_PACKAGE to a trusted extracted Ubuntu UFW root")
    extracted = Path(configured)
    package = extracted / "usr/lib/python3/dist-packages/ufw"
    for name in ("__init__.py", "backend_iptables.py", "util.py"):
        assert (package / name).is_file()
    monkeypatch.setattr(ufw_adapter, "_UFW_PACKAGE", package)
    monkeypatch.setattr(ufw_adapter, "_trusted_path", lambda path: None)
    fixture = tmp_path / "fixture"
    etc = fixture / "etc/ufw"
    etc.mkdir(parents=True)
    (fixture / "etc/default").mkdir()
    (fixture / "etc/default/ufw").write_text('IPV6=yes\nDEFAULT_INPUT_POLICY="DROP"\nDEFAULT_OUTPUT_POLICY="ACCEPT"\nDEFAULT_FORWARD_POLICY="DROP"\nDEFAULT_APPLICATION_POLICY="SKIP"\n')
    (etc / "ufw.conf").write_text("ENABLED=yes\nLOGLEVEL=low\n")
    (etc / "applications.d").mkdir()
    (etc / "applications.d/web").write_text("[Web Test]\ntitle=Changed\ndescription=Changed\nports=9999/tcp\n")
    (etc / "user.rules").write_text('''### tuple ### allow tcp 5432 0.0.0.0/0 any 192.0.2.0/24 in comment=646220657863657074696f6e
### tuple ### deny tcp 22 0.0.0.0/0 any 198.51.100.1 in
### tuple ### allow tcp 80,443 0.0.0.0/0 any 0.0.0.0/0 Web%20Test - in
### tuple ### allow udp 6000:6010 0.0.0.0/0 any 0.0.0.0/0 out_eth0
''')
    (etc / "user6.rules").write_text("### tuple ### allow tcp 5432 ::/0 any 2001:db8::/64 in\n")
    original_load = ufw_adapter._load_package
    def fixture_load(**kwargs):
        original_load(**kwargs)
        import ufw.backend_iptables
        # Skip fixture-only privilege checks without emitting UFW's "Checks disabled" warning.
        # Production keeps the actual checks; privileged acceptance remains separate.
        constructor = ufw.backend_iptables.UFWBackendIptables
        monkeypatch.setattr(constructor, "_do_checks", lambda self: None)
        def construct(dryrun):
            result = constructor(dryrun, rootdir=str(fixture), datadir=str(fixture))
            assert result.caps is None
            return result
        monkeypatch.setattr(ufw.backend_iptables, "UFWBackendIptables", construct)
    monkeypatch.setattr(ufw_adapter, "_load_package", fixture_load)
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        assert argv in {("/usr/sbin/iptables", "-V"), ("/usr/sbin/ufw", "status")}
        output = b"iptables v1.8.11 (nf_tables)\n" if argv[0].endswith("iptables") else b"Status: active\n"
        return subprocess.CompletedProcess(argv, 0, output, b"")
    monkeypatch.setattr(ufw_adapter, "run_command", run)
    return etc, calls


def test_official_reader_preserves_saved_ports_comments_and_order(official_reader):
    etc, calls = official_reader
    snapshot = inspect()
    assert [r.family for r in snapshot.rules] == [4, 4, 4, 4, 6]
    assert snapshot.rules[0].comment == "db exception"
    assert snapshot.rules[1].src == "198.51.100.1/32"
    assert (snapshot.rules[2].dapp, snapshot.rules[2].dports) == ("Web Test", ((80, 80), (443, 443)))
    assert snapshot.rules[3].dports == ((6000, 6010),)
    assert snapshot.rules[3].interface_out == "eth0"
    assert calls == [("/usr/sbin/iptables", "-V"), ("/usr/sbin/ufw", "status")]


def test_official_reader_real_byte_warning_is_refused_and_restored(official_reader):
    etc, calls = official_reader
    with (etc / "user.rules").open("a") as output:
        output.write("### tuple ### allow tcp malformed\n")
    before = (sys.stdout, sys.stderr, getattr(builtins, "_", None))
    with pytest.raises(UfwInspectionError, match="inspection_failed"):
        inspect()
    assert (sys.stdout, sys.stderr, getattr(builtins, "_", None)) == before
    assert calls == [("/usr/sbin/iptables", "-V")]


def test_preloaded_trusted_modules_keep_original_native_hook(native, synthetic_package):
    before = {key: value for key, value in sys.modules.items() if key == "ufw" or key.startswith("ufw.")}
    try:
        ufw_adapter._load_package(deadline=time.monotonic() + 45)
        import ufw.util
        original_hook = ufw.util.cmd
        original_attributes = dict(vars(ufw))
        assert inspect().rules == ()
        assert ufw.util.cmd is original_hook
        assert vars(ufw) == original_attributes
    finally:
        for name in tuple(sys.modules):
            if (name == "ufw" or name.startswith("ufw.")) and name not in before:
                del sys.modules[name]
        sys.modules.update(before)


def test_preloaded_package_cannot_extend_ufw_search_locations(native, synthetic_package, monkeypatch):
    before = {key: value for key, value in sys.modules.items() if key == "ufw" or key.startswith("ufw.")}
    try:
        ufw_adapter._load_package(deadline=time.monotonic() + 45)
        import ufw
        monkeypatch.setattr(ufw, "__path__", [str(synthetic_package), "/tmp/alien"])
        with pytest.raises(UfwInspectionError, match="unsupported_state"):
            inspect()
        assert native == []
    finally:
        for name in tuple(sys.modules):
            if (name == "ufw" or name.startswith("ufw.")) and name not in before:
                del sys.modules[name]
        sys.modules.update(before)


def test_normalized_evidence_overflow_is_not_truncated(native):
    large = rule(comment=("a" * 400000).encode().hex())
    with patch_read_backend(lambda: backend([large, large, large])):
        with pytest.raises(UfwInspectionError, match="unsupported_state"):
            inspect()


def test_normalization_checks_deadline_after_last_rule(native, monkeypatch):
    saved = rule()
    def late_comment():
        monkeypatch.setattr(ufw_adapter.time, "monotonic", lambda: 50.0)
        return ""
    saved.get_comment = late_comment
    with patch_read_backend(lambda: backend([saved])):
        monkeypatch.setattr(ufw_adapter.time, "monotonic", lambda: 1.0)
        with pytest.raises(UfwInspectionError, match="inspection_failed"):
            inspect_ufw(deadline=45.0)


@pytest.mark.parametrize("uid,mode", [(1000, 0o644), (0, 0o664), (0, 0o646)])
def test_package_trust_checks_owner_and_each_write_class(monkeypatch, uid, mode):
    from pathlib import Path
    import stat

    monkeypatch.setattr(Path, "lstat", lambda path: SimpleNamespace(st_uid=uid, st_mode=stat.S_IFREG | mode))
    with pytest.raises(UfwInspectionError, match="unsupported_state"):
        ufw_adapter._trusted_path(Path("/usr/lib/python3/dist-packages/ufw/util.py"))


def test_package_trust_checks_ancestor_not_only_module(monkeypatch):
    from pathlib import Path
    import stat

    def metadata(path):
        mode = 0o777 if str(path) == "/usr/lib" else 0o755
        return SimpleNamespace(st_uid=0, st_mode=stat.S_IFDIR | mode)
    monkeypatch.setattr(Path, "lstat", metadata)
    with pytest.raises(UfwInspectionError, match="unsupported_state"):
        ufw_adapter._trusted_path(Path("/usr/lib/python3/dist-packages/ufw/util.py"))


def test_translation_is_removed_when_originally_absent(native, monkeypatch):
    monkeypatch.delattr(builtins, "_", raising=False)
    with patch_read_backend(lambda: backend()):
        inspect()
    assert not hasattr(builtins, "_")


@pytest.mark.parametrize("source", ["fe80::1%eth0", "fe80::%eth0/64"])
def test_ipv6_zone_predicate_is_not_a_literal_network(native, source):
    with patch_read_backend(lambda: backend(v6=[rule(v6=True, src=source)])):
        with pytest.raises(UfwInspectionError, match="unsupported_state"):
            inspect()


def test_existing_bytecode_cache_is_in_package_trust_checks(native, synthetic_package, monkeypatch):
    cache = synthetic_package / "__pycache__"
    cache.mkdir()
    unsafe = cache / "util.cpython-312.pyc"
    unsafe.write_bytes(b"untrusted")
    checked = []
    def trust(path):
        checked.append(path)
        if path == unsafe:
            raise UfwInspectionError("unsupported_state")
    monkeypatch.setattr(ufw_adapter, "_trusted_path", trust)
    with pytest.raises(UfwInspectionError, match="unsupported_state"):
        inspect()
    assert unsafe in checked
    assert native == []


@pytest.mark.parametrize("encoded,decoded", [("6", ""), ("ff", r"\xff")])
def test_saved_comment_cannot_be_silently_truncated_or_replaced(native, encoded, decoded):
    saved = rule(comment=encoded)
    # Official UFW hex_decode truncates odd hex and uses backslashreplace for invalid UTF-8.
    saved.get_comment = lambda: decoded
    with patch_read_backend(lambda: backend([saved])):
        with pytest.raises(UfwInspectionError, match="unsupported_state"):
            inspect()


@pytest.mark.parametrize("encoded", ["6", "ff"])
def test_official_reader_malformed_saved_comment_refused(official_reader, encoded):
    etc, calls = official_reader
    with (etc / "user.rules").open("a") as output:
        output.write(f"### tuple ### allow tcp 12345 0.0.0.0/0 any 0.0.0.0/0 in comment={encoded}\n")
    with pytest.raises(UfwInspectionError, match="unsupported_state"):
        inspect()
    assert calls == [("/usr/sbin/iptables", "-V")]


@pytest.mark.parametrize("kind", ["dangling_package_symlink", "package_file", "missing_entry", "dangling_entry_symlink"])
def test_existing_damaged_package_path_is_never_installation_prerequisite(native, tmp_path, monkeypatch, kind):
    package = tmp_path / "ufw"
    if kind == "dangling_package_symlink":
        package.symlink_to(tmp_path / "missing-target", target_is_directory=True)
    elif kind == "package_file":
        package.write_text("damaged package")
    else:
        package.mkdir()
        if kind == "dangling_entry_symlink":
            (package / "__init__.py").symlink_to(tmp_path / "missing-entry")
    # Only substitute the fixed path for disposable state; keep production trust validation.
    monkeypatch.setattr(ufw_adapter, "_UFW_PACKAGE", package)
    before = (sys.stdout, sys.stderr, getattr(builtins, "_", None), sys.dont_write_bytecode)
    with pytest.raises(UfwInspectionError, match="unsupported_state"):
        inspect()
    assert native == []
    assert (sys.stdout, sys.stderr, getattr(builtins, "_", None), sys.dont_write_bytecode) == before
