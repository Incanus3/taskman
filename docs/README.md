# Taskman documentation

## Product

- [MVP specification](product/mvp-spec.md)
- [Domain model and glossary](product/domain.md)
- [Task relationships](product/relationships.md)
- [Agent Sessions (superseded historical workflow)](product/agent-sessions.md)

## Planning

- [MVP roadmap](planning/roadmap.md)

## Specifications

- [Upload staging outside deployment records (implemented and operator accepted; native evidence remains limited to the earlier relocation)](specs/2026-10-30-upload-staging-design.md)
- [Modal-local hierarchy status filter (implemented and operator accepted)](specs/2026-10-28-hierarchy-status-filter-design.md)
- [Shared form controls: select and staged date/time pickers (implemented and accepted)](specs/2026-10-28-form-controls-design.md)
- [Task detail layout, hierarchy and responsive sizing (implemented and accepted)](specs/2026-10-28-task-detail-layout-design.md)
- [Task tree guides in the main listing (implemented and operator accepted)](specs/2026-10-27-task-tree-guides-design.md)
- [Expandable Task listing (implemented and operator accepted)](specs/2026-10-27-expandable-task-listing-design.md)
- [Task counts in List navigation and headers (implemented and operator accepted)](specs/2026-10-26-list-task-counts-design.md)
- [Named CLI hosts and repository Project bindings (implemented and operator accepted)](specs/2026-09-30-repository-backends-design.md)
- [PostgreSQL provisioning with operator-owned cluster configuration (approved; implementation and bounded native acceptance complete; results accepted)](specs/2026-10-07-shared-postgresql-design.md)
- [Caddy provisioning with operator-owned configuration (implemented; bounded native results accepted)](specs/2026-10-01-shared-caddy-design.md)
- [Foundation documentation](specs/2026-07-18-foundation-documentation-design.md)
- [Projects and basic Tasks first slice (Project directory requirements superseded)](specs/2026-07-18-projects-basic-tasks-first-slice-design.md)
- [Task editing and human-controlled lifecycle](specs/2026-07-31-task-editing-lifecycle-design.md)
- [Full Task creation form](specs/2026-08-03-full-task-creation-form-design.md)
- [Alpine Elixir CI](specs/2026-08-10-alpine-elixir-ci-design.md)
- [Task detail and navigation (Activity/Sessions, relationship presentation and hierarchy layout superseded)](specs/2026-08-24-task-detail-navigation-design.md)
- [Task comments (implemented)](specs/2026-09-26-task-comments-design.md)
- [Lists and nested organization (navigation/filter presentation superseded)](specs/2026-08-26-lists-nested-organization-design.md)
- [API, CLI, and agent skill (Project representation superseded; operation surface extended)](specs/2026-08-29-api-cli-agent-skill-design.md)
- [Parent-child Task hierarchy (parent search matching and hierarchy presentation superseded)](specs/2026-08-30-parent-child-task-hierarchy-design.md)
- [Task blocking relationships and Done warning (implemented)](specs/2026-09-27-task-blocking-relationships-design.md)
- [Global Task jump search (implemented)](specs/2026-09-29-global-task-search-design.md)
- [Immediate cooperative workspace updates](specs/2026-09-01-live-task-updates-design.md)
- [Authenticated hosted access and release deployment](specs/2026-09-02-authenticated-hosted-access-design.md)
- [ProjectLive workflow decomposition (implemented)](specs/2026-09-03-project-live-decomposition-design.md)
- [Missing List recovery (implemented and operator accepted)](specs/2026-09-18-missing-list-recovery-design.md)
- [Active Project selector (implemented and operator accepted)](specs/2026-09-22-active-project-selector-design.md)
- [Projects without primary directories (implemented and operator accepted)](specs/2026-09-10-projects-without-primary-directories-design.md)
- [Dedicated-host deployment architecture](specs/2026-09-09-dedicated-host-deployment-design.md)
- [Additive ordinary-UFW convergence (implemented; bounded native scope accepted)](specs/2026-09-30-additive-ufw-design.md)
- [Operations contracts (artifacts, protocol, recovery and failure evidence)](specs/2026-09-18-operations-contracts.md)
- [Operations admission and provisioning recovery (written design awaiting approval; separate post-merge workstream)](specs/2026-09-18-provisioning-lock-coverage-proposal.md)
- [Operations CLI progress and outcomes (proposed)](specs/2026-09-09-operations-cli-ux-design.md)
- [PostgreSQL host-side Python configuration (parked proposal)](specs/2026-09-09-postgresql-host-python-design.md)

## Handoffs

- [Current workstreams and continuation gates](handoffs/INDEX.md)

## Guides

- [Native operations acceptance contracts and limits](guides/native-acceptance.md)
- [Documentation roles, placement and retirement](guides/documentation.md)
- [Development guide](guides/development.md)
- [Operations development guidelines](guides/development.md#operations-development)
- [Dedicated-host deployment automation runbook](guides/deployment.md)
- [PostgreSQL ownership and native access](guides/deployment.md#postgresql-ownership-and-access)
- [PostgreSQL operator setup and retry](guides/deployment.md#postgresql-operator-setup-and-retry)
- [Explicit PostgreSQL configuration override and recovery](guides/deployment.md#explicit-postgresql-configuration-override-and-recovery)
- [Repository-bundled operations reference skill](../priv/taskman_ops_skill/SKILL.md)
- [Pending Caddy activation and exact-target retry](guides/deployment.md#activate-a-pending-taskman-site)
- [Provider-console SSH lockout recovery](guides/deployment.md#ssh-lockout-recovery-through-the-provider-console)

## Inventories

- [Operations inventory privacy and operating obligations](inventories/operations-environments.md)

## Research

- [Invitation inbox-placement investigation and evidence limits](research/2026-10-31-invitation-inbox-placement.md)
- [Verification timeout assessment and approved bounded-phase correction (implemented and locally verified)](research/2026-10-07-verification-timeout-review.md)
- [UFW Python API and host-helper integration evaluation](research/2026-10-01-ufw-python-api-evaluation.md)
- [Operations test overlap and branch coverage ownership](research/2026-09-17-operations-test-overlap.md)
- [Operations test parallelism measurements](research/2026-09-16-operations-test-parallelism.md)
- [Cosmos capabilities](research/cosmos-capabilities.md)
- [Emdash and Auggie capabilities](research/emdash-auggie-capabilities.md)

## Prototypes

- [Navigation and MVP visual guidance](prototypes/navigation.html)
- [Task detail hierarchy visual source and snapshot authority](specs/2026-10-28-task-detail-layout-design.md#visual-references-and-their-authority)

## Archive

- [Repository task migration: source mapping, custody and verification](archive/repository-task-migration.md)
