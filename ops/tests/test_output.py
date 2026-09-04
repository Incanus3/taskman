from __future__ import annotations

from dataclasses import dataclass
import json

import pytest

from taskman_ops.errors import ExitStatus, OpsError
from taskman_ops.output import (
    WorkflowResult,
    clear_secrets,
    register_secret,
    redact,
    render_error,
    render_human,
    render_json,
)


@dataclass(frozen=True)
class NestedFacts:
    token: str
    labels: tuple[str, ...]


@pytest.fixture(autouse=True)
def no_secrets_between_tests() -> None:
    clear_secrets()
    yield
    clear_secrets()


def test_redaction_recurses_through_strings_mappings_sequences_and_dataclasses() -> None:
    canary = "taskman-output-canary-7e7c5c"
    register_secret(canary)
    value = {
        canary: [
            f"prefix-{canary}-suffix",
            (NestedFacts(canary, (f"{canary}-nested",)),),
        ]
    }

    redacted = redact(value)
    rendered = repr(redacted)
    assert canary not in rendered
    assert redacted["[REDACTED]"][0] == "prefix-[REDACTED]-suffix"
    assert redacted["[REDACTED]"][1][0].token == "[REDACTED]"


def test_redaction_recurses_through_exception_arguments_and_attributes() -> None:
    canary = "taskman-exception-canary-5b6a2d"
    register_secret(canary)
    error = RuntimeError(f"failed with {canary}")
    error.context = {"secret": canary}

    safe_error = redact(error)
    assert canary not in str(safe_error)
    assert canary not in repr(safe_error)
    assert canary not in repr(safe_error.context)


def test_json_result_has_versioned_secret_free_schema() -> None:
    canary = "taskman-json-canary-8d97fd"
    register_secret(canary)
    result = WorkflowResult(
        command="verify",
        environment="production",
        changed=False,
        stage="complete",
        facts={"detail": canary},
        warnings=(f"warning-{canary}",),
        next_action=None,
    )

    payload = json.loads(render_json(result))
    assert payload == {
        "schema_version": 1,
        "command": "verify",
        "environment": "production",
        "status": "ok",
        "changed": False,
        "stage": "complete",
        "facts": {"detail": "[REDACTED]"},
        "warnings": ["warning-[REDACTED]"],
        "next_action": None,
    }
    assert canary not in render_json(result)


def test_human_result_and_error_are_redacted() -> None:
    canary = "taskman-human-canary-9f3cb1"
    register_secret(canary)
    result = WorkflowResult(
        command="verify",
        environment="production",
        changed=False,
        stage="complete",
        facts={"canary": canary},
        warnings=(f"warning: {canary}",),
        next_action=f"inspect {canary}",
    )
    error = OpsError(
        status=ExitStatus.SAFETY,
        stage=f"stage-{canary}",
        message=f"refused {canary}",
        changed=True,
        next_action=f"run recovery with {canary}",
    )

    human = render_human(result)
    rendered_error = render_error(error, command="verify", environment="production")
    assert canary not in human
    assert canary not in rendered_error
    assert "[REDACTED]" in human
    assert "[REDACTED]" in rendered_error
