# Documentation guide

Use the [documentation index](../README.md) to find the canonical owner relevant to a change.
Read and follow this guide before creating, editing, moving or retiring repository documentation.

## Documentation roles

The repository is public. Documentation describes portable project behavior and synthetic examples,
not actual deployment hosts or operator workstations. Keep real host settings in encrypted deployment
configuration and operational inventories, recovery locations and raw evidence in private operator
storage outside version control. This applies to historical snapshots and generated documents too.
Never copy personal paths or real infrastructure values into repository documentation.

- **Product** documents define current MVP behavior and domain rules. They are authoritative for
  what Taskman is and must do.
- **Planning** documents describe the current delivery direction. They are intentionally less
  permanent than product decisions and should be refined as implementation teaches us more.
- **Guides** define reusable documentation, development and operating practices. They own
  instructions and procedures rather than environment-specific inventory or test evidence.
- **Development** documents describe durable principles for building Taskman. They should contain
  project-wide working and architecture guidance, not session-specific instructions.
- **Inventories** may describe generic resource categories and obligations. Actual environment
  inventories and dated machine observations belong in private operator storage outside version
  control; link to generic operating procedures instead of exposing their contents or locations.
- **Research** documents preserve external evidence and rationale. They inform decisions but do not
  override the current product documents.
- **Specifications** describe the design context, requirements, final decisions, alternatives and
  accepted trade-offs needed to understand or implement a capability.
- **Handoffs** preserve current workstream continuation: implemented state, remaining work,
  blockers, dependencies, pending decisions, approval gates and relevant verification evidence.
- **Prototypes** are visual/product exploration artifacts. They are not production code, but the
  current navigation prototype is broader MVP guidance and should be respected until the real UI
  replaces it.
- **Archive** contains historical material kept for provenance only. It is not actionable.

## Placement and retirement rules

Research and specifications may retain the context and state of affairs relevant to their findings
or design, including dated measurements, identified evidence, limitations and final conclusions.
They must not become historical development ledgers. Do not append sequences of operator approvals,
superseding approvals, discovered-then-fixed gaps, implementation checkpoints or routine verification
reports. Keep the resulting finding, decision or current state in its durable owner; put continuation
information needed by another session in the workstream handoff.

Lists of findings, decisions or other relevant items may remain in durable documents with their
current status (for example, pending, approved, implemented or rejected) and useful rationale.
Gaps, simplification candidates and optimization candidates are examples, not an exhaustive list.
Update the entry to its current conclusion rather than accumulating its development history. A dated
before/after experiment that supports a finding is evidence; a sequence of delivery updates is not.

### Completed implementation plans

Implementation plans are temporary delivery artifacts. Once delivery is complete or explicitly
superseded, harvest any still-relevant decisions, rationale, operating instructions and verification
limits into their canonical specifications, guides or research. Update references and remove the
obsolete plan. Do not archive completed plans by default; retain historical material only when it
has a concrete continuing use, and keep only the material needed for that use.

## Handoffs

The global handoff policy and handoff skill own handoff content, continuation preservation,
ruling acknowledgement and retirement gates. Keep those general requirements in their global
owners rather than duplicating them in repository instructions.

This repository keeps active handoffs in `docs/handoffs/` and indexes them in
[`docs/handoffs/INDEX.md`](../handoffs/INDEX.md). Update that index and affected references when
handoffs change or retire. When the global policy permits retaining material for a concrete
historical use, place it under `docs/archive/handoffs/` and index it as historical.

### Workstream rulings

The global handoff policy governs the active ruling record. Harvest lasting product decisions,
architecture rationale and reusable guidance into their canonical documentation roles above;
a handoff ruling ledger is temporary continuation, not a permanent project decision owner.
