"""Controller paging, confirmation, and batching for exact filesystem cleanup."""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from ..config import EnvironmentConfig
from ..errors import ExitStatus, OpsError
from ..host_protocol import HostRequest, ProtocolError, encode_request
from ..host_protocol.identifiers import validate_absolute_path
from ..output import WorkflowResult
from ..releases.identifiers import validate_release_id
from ..remote import Remote
from .helper import (
    merge_warnings,
    mutation_result_facts,
    result_error,
    run_request,
)
from .helper import (
    request as helper_request,
)
from .operational_preflight import validate_cleanup_preflight

_TARGET_KEYS = frozenset({"kind", "identifier", "path"})
_FACT_KEYS = frozenset(
    {
        "selected_release_id",
        "last_successful_selection_id",
        "backup_protection_sha256",
        "restore_target_sha256",
    }
)
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_BACKUP_ID_RE = re.compile(r"backup-[0-9a-f]{32}\Z")
_SELECTION_ID_RE = re.compile(r"selection-[0-9a-f]{64}\.json\Z")
_TIMEOUT_SECONDS = 660.0


@dataclass(frozen=True)
class CleanupPlan:
    targets: tuple[Mapping[str, object], ...]
    expected_state: Mapping[str, object]
    inventory_sha256: str
    typed_confirmation: str


def cleanup(
    remote: Remote,
    config: EnvironmentConfig,
    *,
    confirm: Callable[[Mapping[str, object]], bool] | None = None,
    dry_run: bool = False,
) -> WorkflowResult:
    if not isinstance(config, EnvironmentConfig):
        raise TypeError("cleanup requires a validated environment configuration")
    if not isinstance(dry_run, bool):
        raise TypeError("cleanup dry-run flag must be boolean")
    warnings: tuple[str, ...] = ()
    plan: CleanupPlan | None = None
    mutation_state = "unchanged"
    completed: tuple[Mapping[str, object], ...] = ()
    try:
        validate_cleanup_preflight(remote, config)
        plan, warnings = _collect_plan(
            remote, config, deadline=time.monotonic() + _TIMEOUT_SECONDS
        )
        facts = {
            "targets": plan.targets,
            "inventory_sha256": plan.inventory_sha256,
            "confirmation_facts": dict(plan.expected_state),
            "typed_confirmation": plan.typed_confirmation,
            "starting_state": None,
        }
        if dry_run:
            return WorkflowResult(
                "cleanup",
                config.name or "",
                False,
                "planned",
                facts,
                warnings,
                "review the exact cleanup targets and rerun without --dry-run",
            )
        if not plan.targets:
            return WorkflowResult(
                "cleanup",
                config.name or "",
                False,
                "nothing-to-clean",
                {
                    **facts,
                    "starting_state": None,
                    "mutation_state": "unchanged",
                    "completed_targets": (),
                    "observations": dict(plan.expected_state),
                    "unavailable_fields": (),
                    "inspection_error": None,
                    "failed_boundary": None,
                    "report": None,
                },
                warnings,
                "rerun cleanup later after additional artifacts become eligible",
            )
        if not (confirm or _confirm)(facts):
            return WorkflowResult(
                "cleanup",
                config.name or "",
                False,
                "confirmation-cancelled",
                facts,
                warnings,
                "confirm the exact target list on a later run",
            )
        starting_state = dict(plan.expected_state)
        final_facts: dict[str, object] | None = None
        for batch in _batches(config, starting_state, plan.targets):
            result = run_request(
                remote,
                _request(config, starting_state, batch, action="execute", cursor=None),
                prior_mutation_state=mutation_state,
                completed_targets=completed,
            )
            warnings = merge_warnings(warnings, result.warnings)
            if result.outcome != "succeeded":
                raise result_error(
                    result,
                    starting_state=starting_state,
                    prior_mutation_state=mutation_state,
                    completed_targets=completed,
                )
            final_facts = mutation_result_facts(
                result,
                starting_state=starting_state,
                prior_mutation_state=mutation_state,
                completed_targets=completed,
            )
            mutation_state = str(final_facts["mutation_state"])
            completed = tuple(final_facts["completed_targets"])
        assert final_facts is not None
        return WorkflowResult(
            "cleanup",
            config.name or "",
            mutation_state != "unchanged",
            "cleaned" if mutation_state != "unchanged" else "nothing-to-clean",
            {
                "targets": plan.targets,
                "inventory_sha256": plan.inventory_sha256,
                **final_facts,
            },
            warnings,
            "rerun cleanup later after additional artifacts become eligible",
        )
    except OpsError as error:
        error_facts = (
            {"targets": plan.targets, "inventory_sha256": plan.inventory_sha256}
            if plan is not None
            else {"targets": ()}
        )
        error_facts.update(error.state)
        return WorkflowResult(
            "cleanup",
            config.name or "",
            error.changed,
            "lock-contended"
            if error.status is ExitStatus.LOCKED
            else "preflight-failed"
            if error.status is ExitStatus.REMOTE_PREFLIGHT
            else "safety-refused"
            if error.status is ExitStatus.SAFETY
            else "cleanup-failed",
            error_facts,
            merge_warnings(warnings, error.warnings),
            error.next_action,
            error.status,
        )


def _collect_plan(
    remote: Remote, config: EnvironmentConfig, *, deadline: float
) -> tuple[CleanupPlan, tuple[str, ...]]:
    targets: list[Mapping[str, object]] = []
    cursor: Mapping[str, object] | None = None
    digest: str | None = None
    facts: dict[str, object] | None = None
    warnings: tuple[str, ...] = ()
    previous: tuple[str, str, str] | None = None
    while True:
        if time.monotonic() >= deadline:
            raise _safety("cleanup inspection exceeded the command deadline")
        result = run_request(
            remote,
            _request(config, {}, (), action="inspect", cursor=cursor),
            deadline=deadline,
        )
        warnings = merge_warnings(warnings, result.warnings)
        if result.outcome != "succeeded":
            raise result_error(result)
        page, page_facts, page_digest, next_cursor = _inspection(
            result.state, cursor, digest, previous
        )
        if facts is None:
            facts, digest = page_facts, page_digest
        elif facts != page_facts:
            raise _safety("cleanup confirmation facts changed between pages")
        targets.extend(page)
        if page:
            previous = _identity(page[-1])
        if next_cursor is None:
            assert facts is not None and digest is not None
            if digest != _inventory_sha256(facts, targets):
                raise _safety("cleanup inventory digest does not match its targets")
            identifiers = ",".join(str(target["identifier"]) for target in targets)
            return CleanupPlan(
                tuple(targets),
                facts,
                digest,
                f"cleanup {config.name or ''} {identifiers or 'none'}",
            ), warnings
        cursor = next_cursor


def _inspection(
    value: object,
    expected_cursor: Mapping[str, object] | None,
    expected_digest: str | None,
    previous: tuple[str, str, str] | None,
) -> tuple[
    list[Mapping[str, object]], dict[str, object], str, Mapping[str, object] | None
]:
    expected = {*_FACT_KEYS, "targets", "inventory_sha256", "next_cursor"}
    if not isinstance(value, Mapping) or set(value) != expected:
        raise _safety("cleanup helper returned invalid inspection fields")
    facts = {key: value[key] for key in _FACT_KEYS}
    _validate_facts(facts)
    digest = value["inventory_sha256"]
    if (
        type(digest) is not str
        or _SHA256_RE.fullmatch(digest) is None
        or expected_digest is not None
        and digest != expected_digest
    ):
        raise _safety("cleanup helper returned invalid inventory digest")
    raw = value["targets"]
    if not isinstance(raw, (tuple, list)) or len(raw) > 64:
        raise _safety("cleanup helper returned an invalid target page")
    page = [_target(item) for item in raw]
    identities = [_identity(item) for item in page]
    if (
        identities != sorted(set(identities))
        or previous is not None
        and identities
        and identities[0] <= previous
    ):
        raise _safety("cleanup targets are not strictly ordered")
    next_cursor = value["next_cursor"]
    if next_cursor is not None and (
        not page
        or not isinstance(next_cursor, Mapping)
        or set(next_cursor) != {"inventory_sha256", "after_id"}
        or next_cursor["inventory_sha256"] != digest
        or next_cursor["after_id"] != _canonical_identity(identities[-1])
    ):
        raise _safety("cleanup continuation does not match its page")
    if expected_cursor is not None and expected_cursor["inventory_sha256"] != digest:
        raise _safety("cleanup cursor does not match its page")
    return page, facts, digest, next_cursor


def _request(
    config: EnvironmentConfig,
    expected_state: Mapping[str, object],
    targets: tuple[Mapping[str, object], ...],
    *,
    action: str,
    cursor: Mapping[str, object] | None,
) -> HostRequest:
    return helper_request(
        "cleanup",
        config,
        expected_state=expected_state,
        parameters={
            "action": action,
            "targets": targets,
            "release_retention": config.release_retention,
            "backup_retention": config.backup_retention,
            "cursor": cursor,
        },
    )


def _batches(
    config: EnvironmentConfig,
    expected_state: Mapping[str, object],
    targets: tuple[Mapping[str, object], ...],
) -> tuple[tuple[Mapping[str, object], ...], ...]:
    batches: list[tuple[Mapping[str, object], ...]] = []
    current: list[Mapping[str, object]] = []
    for target in targets:
        candidate = (*current, target)
        try:
            encode_request(
                _request(
                    config, expected_state, candidate, action="execute", cursor=None
                )
            )
        except ProtocolError:
            if not current:
                raise _safety("one cleanup target exceeds the request budget") from None
            batches.append(tuple(current))
            current = [target]
        else:
            current = list(candidate)
        if len(current) == 64:
            batches.append(tuple(current))
            current = []
    if current:
        batches.append(tuple(current))
    return tuple(batches)


def _validate_facts(facts: Mapping[str, object]) -> None:
    selected = facts["selected_release_id"]
    selection = facts["last_successful_selection_id"]
    try:
        if selected is not None:
            validate_release_id(selected)
    except (TypeError, ValueError):
        raise _safety("cleanup confirmation facts are invalid") from None
    if selection is not None and (
        type(selection) is not str or _SELECTION_ID_RE.fullmatch(selection) is None
    ):
        raise _safety("cleanup confirmation facts are invalid")
    for field in ("backup_protection_sha256", "restore_target_sha256"):
        if facts[field] is not None and (
            type(facts[field]) is not str
            or _SHA256_RE.fullmatch(str(facts[field])) is None
        ):
            raise _safety("cleanup confirmation facts are invalid")


def _target(value: object) -> Mapping[str, object]:
    if (
        not isinstance(value, Mapping)
        or set(value) != _TARGET_KEYS
        or value.get("kind") not in {"release", "backup", "temporary"}
        or type(value.get("identifier")) is not str
        or type(value.get("path")) is not str
    ):
        raise _safety("cleanup helper returned invalid target")
    identifier, path = str(value["identifier"]), str(value["path"])
    try:
        validate_absolute_path(path)
        if value["kind"] == "release":
            validate_release_id(identifier)
        elif value["kind"] == "backup" and _BACKUP_ID_RE.fullmatch(identifier) is None:
            raise ValueError
    except (ProtocolError, TypeError, ValueError):
        raise _safety("cleanup helper returned invalid target") from None
    if value["kind"] == "temporary" and path.rsplit("/", 1)[-1] != identifier:
        raise _safety("cleanup helper returned invalid target")
    return dict(value)


def _identity(target: Mapping[str, object]) -> tuple[str, str, str]:
    return tuple(str(target[key]) for key in ("kind", "identifier", "path"))


def _canonical_identity(identity: tuple[str, str, str]) -> str:
    return json.dumps(identity, ensure_ascii=True, separators=(",", ":"))


def _inventory_sha256(
    facts: Mapping[str, object], targets: list[Mapping[str, object]]
) -> str:
    payload = {"confirmation_facts": facts, "targets": targets}
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("ascii")
    ).hexdigest()


def _confirm(plan: Mapping[str, object]) -> bool:
    expected = str(plan["typed_confirmation"])
    return (
        input(f"Delete exact Taskman artifacts? Type '{expected}': ").strip()
        == expected
    )


def _safety(message: str) -> OpsError:
    return OpsError(
        ExitStatus.SAFETY,
        "cleanup",
        message,
        False,
        "inspect exact cleanup targets before retrying",
    )


__all__ = ["CleanupPlan", "cleanup"]
