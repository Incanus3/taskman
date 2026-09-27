# Orca instance registration

Status: active. Updated: 2026-09-27. Resume: `$resume orca-instance-registration`.

## Objective and current checkpoint

Design user-owned Orca instance registration in Taskman. The accepted first increment covers only
add, list, connection test, and remove. A registration and its pairing credential are available
only to its owning Taskman user. Worktree and agent-session browsing or control are outside this
increment. Taskman's backend is expected to reach the Orca servers over a private Tailscale route.

The [proposed specification](../specs/2026-09-27-orca-instance-registration-design.md) is written
in the independent Orca worktree based on `origin/main`, for operator review. No implementation
plan or product code has been written. The [MVP specification](../product/mvp-spec.md) still defers
agent integration; the [authenticated access design](../specs/2026-09-02-authenticated-hosted-access-design.md)
describes existing account and API boundaries.

## Current position and agreed remaining sequence

1. **Current:** Operator reviews the written proposal, especially the strict tailnet-IP endpoint
   policy, encrypted credential lifecycle, safe test outcomes, and removal versus Orca grant
   revocation. Revise the spec to resolve feedback and obtain explicit written-spec approval.
2. After written-spec approval, write an implementation plan and obtain its review and approval;
   implementation also requires an execution choice. The approved design and plan establish a
   clean-session boundary by default, with implementation in this session only if the operator
   explicitly chooses it.
3. Implement only the four registration operations with UI/API/CLI parity, then verify the pinned
   Orca CLI remote-routing/state behavior, owner isolation, credential handling, and full repository
   gate. Worktree/session integration requires separate design.

## Workstream rulings

- **Proposed, pending operator review:** Restrict this increment to numeric Tailscale IPv4
  endpoints from the pairing offer, and invoke a transient Orca CLI `status --json` process via
  `ORCA_PAIRING_CODE`. This avoids DNS rebinding and saved Orca profile state, but excludes MagicDNS
  and depends on a pinned CLI that honors the environment route without persistent secrets. If the
  CLI contract fails verification, revise the design before shipping the probe.
