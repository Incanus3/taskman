from __future__ import annotations

from pathlib import Path
import subprocess


def test_conditional_pyinfra_adapter_reports_a_real_change_then_a_real_noop(tmp_path: Path) -> None:
    """A dynamic script marker must affect pyinfra's own operation result."""

    target = tmp_path / "owned-state"
    deploy = tmp_path / "conditional.py"
    deploy.write_text(
        "from taskman_ops.pyinfra import conditional_convergence\n"
        f"target = {target.as_posix()!r}\n"
        "conditional_convergence(\n"
        "    probe=f\"if test -e {target}; then printf 'changed=0\\\\n'; else printf 'changed=1\\\\n'; fi\",\n"
        "    script=f\"touch {target}\",\n"
        "    sudo=False,\n"
        ")\n",
        encoding="utf-8",
    )

    first = _run_pyinfra(deploy)
    second = _run_pyinfra(deploy)

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert target.exists()
    assert "Success" in first.stderr
    assert "No changes" in second.stderr


def _run_pyinfra(deploy: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("uv", "run", "pyinfra", "@local", deploy.as_posix(), "-y"),
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
