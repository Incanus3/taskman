# Handoff: Agent-Aware Project Manager Implementation Planning

Date: 2026-07-14

## Purpose

Turn the resolved MVP product specification into an implementation architecture and a dependency-
ordered set of small, testable delivery tickets. This is a new planning phase, separate from the
closed product-discovery Wayfinder.

The current Notes repository remains a planning repository. Do not implement the product here unless
the user explicitly changes that decision. First identify the target implementation repository.

## Authoritative product input

- [MVP Product Specification](../agent-aware-project-manager/MVP_PRODUCT_SPECIFICATION.md) is the
  product baseline.
- [Domain glossary](../agent-aware-project-manager/CONTEXT.md), [Agent Session workflow]
  (../agent-aware-project-manager/AGENT_SESSION_WORKFLOW.md), and [Task relationship rules]
  (../agent-aware-project-manager/TASK_RELATIONSHIPS.md) are detailed, already-resolved decisions.
- The closed **Chart the Agent-Aware Project Manager MVP** Wayfinder is historical product-discovery
  context. Do not reopen or revise its decisions without a new product decision.

## New map destination

An implementation-ready plan that identifies the target repository and stack, local deployment and
persistence design, domain persistence model, browser/server/API boundary, Auggie ACP process and
recovery design, test strategy, and a dependency-ordered backlog of tracer-bullet implementation
tickets with acceptance criteria.

## Initial frontier

Start with **Define the implementation host and local application architecture**. Resolve one
decision at a time through an architecture interview before creating delivery tickets. The first
decision is which repository owns the implementation and whether it is greenfield.

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
- Ask the user one architecture decision at a time and recommend an answer; research external facts
  rather than asking the user to supply them.
- Create implementation tickets only after the architectural dependencies they need are resolved.
- Every delivery ticket must declare its user-visible or technical outcome, acceptance criteria,
  tests, and blockers.
- Keep plan/map state in Beads and flush it after updates. Never implement more than the currently
  claimed ticket without explicit approval.