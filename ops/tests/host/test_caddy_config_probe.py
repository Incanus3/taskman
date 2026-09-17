from __future__ import annotations

import os
from pathlib import Path
import subprocess

import pytest

from tests.support.shell import write_shell_script
from taskman_ops.host.facts import _CADDY_CONFIG_SCRIPT


_CADDY_UNIT_ALIASES = (
    "/lib/systemd/system/caddy.service",
    "/usr/lib/systemd/system/caddy.service",
)
_CADDY_UNIT_MD5 = "e1d5d0f481a49c05f9d2bb6fdb2b4f25"


def _caddy_probe(
    tmp_path: Path,
    *,
    package_owner: str = "caddy",
    package_record_path: str = "/lib/systemd/system/caddy.service",
    package_resolved_path: str | None = None,
    actual_md5: str = _CADDY_UNIT_MD5,
) -> dict[str, str]:
    """Run the shipped probe against command-level package and systemd evidence."""

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    unit = tmp_path / "caddy.service"
    unit.write_text("[Service]\nExecStart=/usr/bin/caddy run\n", encoding="utf-8")
    config = tmp_path / "Caddyfile"
    config.write_text("example.test { respond \"ok\" }\n", encoding="utf-8")
    write_shell_script(
        bin_dir / "systemctl",
        """case "$3" in
  --property=LoadState) printf '%s\\n' loaded ;;
  --property=ActiveState) printf '%s\\n' active ;;
  --property=MainPID) printf '%s\\n' 0 ;;
  --property=FragmentPath) printf '%s\\n' "$TASKMAN_CADDY_FRAGMENT" ;;
esac""",
    )
    write_shell_script(bin_dir / "stat", "printf '%s\\n' root:root:644")
    write_shell_script(
        bin_dir / "dpkg-query",
        """case "$1" in
  --showformat=*) printf '%s\\n' installed ;;
  --search)
    if [ "$2" = "$TASKMAN_PACKAGE_RECORD_PATH" ]; then
      printf '%s: %s\\n' "$TASKMAN_PACKAGE_OWNER" "$TASKMAN_PACKAGE_RECORD_PATH"
    else
      exit 1
    fi
    ;;
  --control-show) printf '%s  %s\\n' "$TASKMAN_EXPECTED_MD5" "${TASKMAN_PACKAGE_RECORD_PATH#/}" ;;
esac""",
    )
    write_shell_script(
        bin_dir / "readlink",
        """case "$2" in
  /lib/systemd/system/caddy.service|/usr/lib/systemd/system/caddy.service)
    printf '%s\\n' "$TASKMAN_PACKAGE_RESOLVED_PATH"
    ;;
  *) exec /usr/bin/readlink "$@" ;;
esac""",
    )
    write_shell_script(
        bin_dir / "md5sum",
        "printf '%s  %s\\n' \"$TASKMAN_ACTUAL_MD5\" \"$1\"",
    )

    completed = subprocess.run(
        ("sh", "-ceu", _CADDY_CONFIG_SCRIPT, "taskman-caddy-config", config.as_posix()),
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "TASKMAN_CADDY_FRAGMENT": unit.as_posix(),
            "TASKMAN_PACKAGE_OWNER": package_owner,
            "TASKMAN_PACKAGE_RECORD_PATH": package_record_path,
            "TASKMAN_PACKAGE_RESOLVED_PATH": package_resolved_path or unit.as_posix(),
            "TASKMAN_EXPECTED_MD5": _CADDY_UNIT_MD5,
            "TASKMAN_ACTUAL_MD5": actual_md5,
        },
    )

    assert completed.returncode == 0, completed.stderr
    return dict(line.split("=", 1) for line in completed.stdout.splitlines())


@pytest.mark.parametrize("package_record_path", _CADDY_UNIT_ALIASES, ids=("lib", "usr-lib"))
def test_caddy_probe_accepts_a_package_recorded_systemd_alias_for_the_fragment(
    tmp_path: Path, package_record_path: str
) -> None:
    """Changing alias discovery back to FragmentPath-only must fail this regression."""

    evidence = _caddy_probe(tmp_path, package_record_path=package_record_path)

    assert evidence["unit_package"] == "caddy"
    assert evidence["unit_verified"] == "clean"


@pytest.mark.parametrize(
    ("package_owner", "package_record_path", "package_resolved_path", "actual_md5", "expected_package", "expected_verified"),
    (
        ("foreign", _CADDY_UNIT_ALIASES[0], None, _CADDY_UNIT_MD5, "foreign", "unknown"),
        ("caddy", _CADDY_UNIT_ALIASES[0], None, "0" * 32, "caddy", "modified"),
        ("caddy", _CADDY_UNIT_ALIASES[0], "/tmp/foreign-caddy.service", _CADDY_UNIT_MD5, "foreign", "unknown"),
        ("caddy", "/opt/caddy.service", "/opt/caddy.service", _CADDY_UNIT_MD5, "foreign", "unknown"),
    ),
    ids=("foreign-owner", "modified-unit", "resolved-file-mismatch", "unrelated-package-path"),
)
def test_caddy_probe_keeps_package_ownership_checksum_and_supported_path_guards(
    tmp_path: Path,
    package_owner: str,
    package_record_path: str,
    package_resolved_path: str | None,
    actual_md5: str,
    expected_package: str,
    expected_verified: str,
) -> None:
    """Alias support must not adopt foreign, modified, or unrelated unit evidence."""

    evidence = _caddy_probe(
        tmp_path,
        package_owner=package_owner,
        package_record_path=package_record_path,
        package_resolved_path=package_resolved_path,
        actual_md5=actual_md5,
    )

    assert evidence["unit_package"] == expected_package
    assert evidence["unit_verified"] == expected_verified
