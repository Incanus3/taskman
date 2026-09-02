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

## Current technology direction

- The implementation is greenfield in `Incanus3/taskman`.
- Use Elixir/OTP with Phoenix LiveView.
- Use Phoenix's default Tailwind-backed component setup.
- Use the conventional Phoenix structure with contexts and separate core and web libraries:
  `lib/taskman/` and `lib/taskman_web/`.
- Use PostgreSQL from the start. PostgreSQL is run manually in Docker during local development.
- Run Phoenix directly with `mix phx.server`; do not add Docker Compose or application container
  orchestration initially.
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
