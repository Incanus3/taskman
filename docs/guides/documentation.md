# Documentation guide

Use the [documentation index](../README.md) to find the canonical owner relevant to a change.
Read and follow this guide before creating, editing, moving or retiring repository documentation.

## Documentation roles

- **Product** documents define current MVP behavior and domain rules. They are authoritative for
  what Taskman is and must do.
- **Planning** documents describe the current delivery direction. They are intentionally less
  permanent than product decisions and should be refined as implementation teaches us more.
- **Guides** define reusable documentation, development and operating practices. They own
  instructions and procedures rather than environment-specific inventory or test evidence.
- **Development** documents describe durable principles for building Taskman. They should contain
  project-wide working and architecture guidance, not session-specific instructions.
- **Inventories** record identified resources, dated environment facts and ongoing obligations.
  Store them under `docs/inventories/`; distinguish recorded observations from refreshed live
  state and retain renewal, retention and recovery requirements.
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

Handoffs are short-lived and should remain concise rather than accumulate a complete development
history. Retire them after the operator explicitly acknowledges workstream completion and their
relevant final information has been harvested into durable owners. Normally discard obsolete
continuation and historical narration. If a historical handoff or ledger still has a concrete use,
keep only the relevant material under `docs/archive/handoffs/` and index it as historical. Do not
archive a ledger merely to preserve everything, and do not retire an active handoff before explicit
completion confirmation.
