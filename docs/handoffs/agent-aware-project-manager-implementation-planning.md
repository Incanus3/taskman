# Handoff: Agent-Aware Project Manager Implementation Planning

Date: 2026-07-16

## Purpose

Turn the resolved MVP product specification into an implementation architecture and a dependency-
ordered set of small, testable delivery tickets. This is a new planning phase, separate from the
closed product-discovery Wayfinder.

The current `Incanus3/taskman` repository is now the greenfield implementation repository. Retain the
existing product and research documentation under `docs/` as the authoritative planning record while
implementation planning proceeds here.

## Authoritative product input

- [MVP Product Specification](../MVP_PRODUCT_SPECIFICATION.md) is the
  product baseline.
- [Domain glossary](../CONTEXT.md), [Agent Session workflow](../AGENT_SESSION_WORKFLOW.md), and [Task
  relationship rules](../TASK_RELATIONSHIPS.md) are detailed, already-resolved decisions.
- The closed **Chart the Agent-Aware Project Manager MVP** Wayfinder is historical product-discovery
  context. Do not reopen or revise its decisions without a new product decision.

## New map destination

An implementation-ready plan that identifies the target repository and stack, local deployment and
persistence design, domain persistence model, browser/server/API boundary, Auggie ACP process and
recovery design, test strategy, and a dependency-ordered backlog of tracer-bullet implementation
tickets with acceptance criteria.

## Planning posture

The repository, technology direction, and MVP product scope are sufficiently settled to begin
roadmap-level planning. The project is intentionally planning-only at this stage; Jakub will
generate the Phoenix application skeleton locally later. No implementation work is implied by this
document.

The next planning artifact is the [Lightweight MVP Roadmap](../MVP_ROADMAP.md). It is a sequence of
high-level vertical slices, not a detailed backlog. Refine a slice shortly before it is implemented,
rather than resolving the entire implementation architecture upfront.

The first product slice is **Projects and basic Tasks**: create a Project, create and manage Tasks in
it, and view them in the default list-first screen.

## Just-in-time architecture rule

Avoid speculative architecture, but do not skip deliberate design where a real feature needs an
abstraction. Immediately before each slice, think through the minimum abstractions, boundaries, and
persistence decisions that the slice genuinely requires. Add only justified seams, while keeping them
small and replaceable where the product specification identifies a future integration boundary.

## Initial frontier

When implementation begins, start with the application foundation and then the Projects and basic
Tasks vertical slice. Detailed architecture questions should be answered in the context of the next
slice that needs them, not as a separate broad architecture phase.

## Resolved high-level implementation direction

The following technology-shape decisions were resolved during the initial architecture brainstorm on
2026-07-15:

- The implementation is greenfield in this repository, `Incanus3/taskman`; the existing `docs/`
  product and research assets remain in place.
- Use **Elixir/OTP** with **Phoenix LiveView**.
- Use Phoenix's default **Tailwind-backed component setup** rather than opting out of it.
- Use the conventional Phoenix application structure with contexts and separate core and web
  libraries (`lib/taskman/` and `lib/taskman_web/`).
- Use **PostgreSQL** from the start. The developer will run PostgreSQL manually in Docker.
- Run the Phoenix application directly with `mix phx.server` during initial development. Do not add
  Docker Compose or application container orchestration at this stage, preserving Phoenix's normal
  code reloading for changed files.
- Prefer a **LiveView-first** browser experience, adding only small, isolated JavaScript hooks when
  browser behavior genuinely requires them; do not build a JavaScript SPA.

These are intentionally high-level decisions, not a completed architecture. Detailed decisions about
the Auggie ACP integration, URL and LiveView state mechanics, filesystem boundaries, persistence
schema, testing seams, and the dependency-ordered implementation backlog remain open for later,
feature-local planning discussions.

## Planning questions to resolve

1. Target implementation repository, language, framework, package management, and local run model.
2. Persistence technology, schema/migration approach, and local data-location rules.
3. Server/browser process boundary, API shape, and how the browser interacts with local services.
4. Auggie ACP process ownership, capability negotiation, session-ID persistence, recovery, and
   failure handling.
5. UI composition, routing, Task-modal state, accessibility, and visual test strategy.
6. Unit, integration, end-to-end, and ACP-adapter test seams; deterministic substitutes for Auggie.
7. Delivery slices, their dependencies, acceptance criteria, and verification commands.

## Operating rules

- Use the final specification as a constraint, not as an invitation to expand MVP scope.
- Work top-down through user-visible vertical slices.
- Before each slice, make the minimum architecture and abstraction decisions needed for that slice.
- Do not design generalized systems for hypothetical future requirements.
- Create detailed implementation tickets only when the corresponding slice is next or close to next.
- Every delivery ticket must declare its user-visible or technical outcome, acceptance criteria,
  tests, and blockers.
- Keep plan/map state in Beads and flush it after updates when implementation planning begins.
- Never implement more than the currently claimed ticket without explicit approval.
