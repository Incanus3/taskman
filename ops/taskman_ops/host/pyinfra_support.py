"""Common settings and result parsing for guarded pyinfra operations."""


def operation_sudo(host: object) -> bool:
    arguments = getattr(host, "current_op_global_arguments", None)
    if not isinstance(arguments, dict):
        return True
    return bool(arguments.get("_sudo", True))


def probe_changed(output: object, operation: str) -> bool:
    lines = getattr(output, "stdout_lines", ())
    markers = [line.removeprefix("changed=") for line in lines if line.startswith("changed=")]
    if markers == ["0"]:
        return False
    if markers == ["1"]:
        return True
    raise RuntimeError(f"{operation} returned an invalid change result")
