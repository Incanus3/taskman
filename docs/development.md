# Taskman development guide

This document contains durable project-wide guidance for building Taskman. It is intentionally
separate from product requirements, delivery planning, and session handoffs.

## Delivery approach

- Work top-down through user-visible vertical slices.
- Keep the first implementation narrow and learn from the running application.
- Refine the next slice when it is close to implementation; do not create a detailed backlog for the
  entire MVP in advance.
- Use the product documents as constraints, not as an invitation to expand MVP scope.
- Prefer subagent-driven development for implementation work when it is available and not explicitly
  forbidden.

## Just-in-time architecture

Avoid speculative architecture, but do not skip deliberate design where a real feature needs an
abstraction.

Immediately before a feature or slice, think through the minimum abstractions, boundaries, and
persistence decisions that it genuinely requires. Add abstractions only when a current feature or a
clearly identified boundary justifies them. Keep justified seams small and replaceable where the
product specification identifies a future integration boundary.

Do not design generalized systems for hypothetical future requirements. Conversely, do not avoid a
necessary abstraction merely because a broader version of it is not needed yet: design the smallest
useful seam before adding the feature that needs it.

## Application boundaries

Modules in `TaskmanWeb` interact with persistence through public context APIs. They must not call
`Taskman.Repo` directly or construct Ecto queries. Context and core-library modules own persistence
coordination and keep schemas, changesets, queries, and Repo calls out of the web layer.

Use three levels of ownership inside an application context:

- The main context module exposes application use cases to web, CLI, and other contexts. It owns
  application-boundary validation, authorization entry points, multi-resource orchestration,
  auditing, externally meaningful side effects, and stable application-level results and errors.
  The context namespace owns these use cases, while the main context module may remain a stable
  facade that delegates their implementation to focused workflow and capability modules. It should
  delegate raw queries and mutations.
- Resource and dedicated capability modules expose reusable domain behavior intrinsic to a
  resource or invariant. Prefer Ash actions for resource behavior. A capability module may own
  persistence coordination when the exact query or lock set is inseparable from maintaining its
  invariant, as with administrator authority.
- A resource-specific `Persistence` module owns storage-shaped primitives and deliberate Ash
  bypasses: direct Repo calls, Ecto queries and changesets, row locks, and bulk updates or deletes.
  These functions do not own authorization, auditing, broadcasts, application workflows, or
  user-facing error translation, and they should not start a transaction that must encompass a
  larger workflow.

Keep a semantic resource-level wrapper when it expresses meaningful domain behavior beyond its
storage implementation. Call a persistence function directly from an internal workflow when a
wrapper would merely repeat a storage-shaped operation.

Framework lifecycle adapters such as Ash changes, checks, preparations, and manual actions may own
the action-specific policy, workflow, or saga they exist to implement, including input extraction,
sequencing, hooks, context metadata, and final result or error translation. Do not extract
single-consumer policy merely to make an adapter artificially thin.

These adapters must delegate direct persistence mechanics—queries, row locking, updates, and
deletes—to focused context or core-library modules that own the affected records. Keep the
workflow in the adapter when it is action-specific. Delegate storage-shaped operations directly
to resource-specific persistence modules, and use domain wrappers only when they add meaningful
behavior.

## Current technology direction

- The implementation is greenfield in `Incanus3/taskman`.
- Use Elixir/OTP with Phoenix LiveView.
- Use Phoenix's default Tailwind-backed component setup.
- Use the conventional Phoenix structure with contexts and separate core and web libraries:
  `lib/taskman/` and `lib/taskman_web/`.
- Use PostgreSQL from the start. PostgreSQL is run manually in Docker during local development.
- Run Phoenix directly with `mix phx.server`; do not add Docker Compose or application container
  orchestration initially.
- Local source development remains a supported workflow. Dedicated-host operation uses the approved
  OTP release behind systemd and loopback Caddy topology; follow the deployment runbook rather than
  adding a second server lifecycle or container layer.
- Prefer a LiveView-first browser experience, adding small isolated JavaScript hooks only when
  browser behavior genuinely requires them. Do not build a JavaScript SPA.

These are high-level technology decisions, not a complete architecture. Decide persistence details,
filesystem boundaries, URL and LiveView state mechanics, ACP process behavior, and testing seams in
context immediately before the relevant feature.

## Scope discipline

- Keep Project, List, and Task data in one shared workspace without per-user ownership or
  collaboration permissions.
- Authentication, hosted access, and OTP release packaging follow the accepted hosted-access
  specification. Do not expand that boundary into synchronization, collaboration, managed hosting,
  or speculative multi-user authorization.
- Ash is initially isolated to the Accounts domain. Do not build new domain capabilities as Ash
  resources alongside the existing Ecto contexts. A complete domain migration requires its own
  approved design and coherent migration boundaries.
- Keep external integrations behind focused boundaries when they first appear; do not design the
  final provider architecture before the first concrete provider feature needs it.
- Treat product documents as the source of truth for current behavior. Research and prototypes
  provide evidence and guidance, but do not silently change the product contract.

## Verification expectations

Every implementation slice should have a clear user-visible or technical outcome and a small,
meaningful verification gate: focused tests, formatting, a build, a smoke test, or another direct
inspection appropriate to the change.

Automated tests should cover observable behavior, interactions, and meaningful structure. Do not
add assertions that only verify styling details such as spacing, colors, or alignment. CSS-class
assertions are appropriate only when they establish functional user-visible state, such as whether
an element is shown or hidden.

Meaningful persisted or query operations exposed through the UI must ship with corresponding API,
CLI, help, Bash/Fish completion, bundled skill, and focused verification parity unless the
accepted feature specification records an explicit exception.

For hosted operation, preserve the public boundary: Caddy owns public HTTPS and Phoenix binds to
loopback. Forwarded client details are trusted only from that immediate loopback proxy. Keep
runtime secrets outside version control, use versioned immutable releases selected by a `current`
symlink, and treat migration compatibility and backup/restore evidence as release requirements.
