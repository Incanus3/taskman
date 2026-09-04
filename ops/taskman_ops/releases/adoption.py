"""One-time, host-evidenced adoption of an existing current release.

The controller never walks a workstation path or accepts caller-supplied
health, topology, version, migration, digest, or selected-release evidence.
Those facts are collected and checked inside the exclusive host transaction
implemented by :class:`RemoteLifecycleStore`.
"""

from __future__ import annotations

from .records import AdoptionRecord, ManualAdoptionCandidate, RemoteLifecycleStore


def inspect_manual_current_release(
    store: RemoteLifecycleStore,
    *,
    lock_timeout_seconds: float = 5,
) -> ManualAdoptionCandidate:
    """Derive the exact manual baseline that may later be confirmed."""

    if not isinstance(store, RemoteLifecycleStore):
        raise TypeError("manual adoption inspection requires a remote lifecycle store")
    return store.inspect_manual_current_release(lock_timeout_seconds=lock_timeout_seconds)


def adopt_current_release(
    store: RemoteLifecycleStore,
    *,
    confirmed: bool,
    expected: ManualAdoptionCandidate | None = None,
    lock_timeout_seconds: float = 5,
) -> AdoptionRecord:
    """Record the host's selected manual release after explicit confirmation."""

    if not isinstance(store, RemoteLifecycleStore):
        raise TypeError("manual adoption requires a remote lifecycle store")
    return store.adopt_current_release(
        confirmed=confirmed,
        expected=expected,
        lock_timeout_seconds=lock_timeout_seconds,
    )


__all__ = ["adopt_current_release", "inspect_manual_current_release"]
