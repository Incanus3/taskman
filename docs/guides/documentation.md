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

## Handoffs

Handoffs are short-lived transfer documents for active workstreams. They preserve the minimum
current state needed to resume safely: the implemented baseline, remaining sequence, blockers,
dependencies, pending decisions, approval gates, and verification evidence that establishes the
baseline or explains a blocker.

Maintain one handoff per workstream under `docs/handoffs/` and keep its index entry current. Update
it when the workstream advances, changes direction, reaches a material checkpoint, or needs to move
to another session. Keep it concise and actionable. Link to canonical specifications, plans, product
documents, guides, and tracker items rather than copying their content or turning the handoff into a
second source of truth.

Do not replace agreed unfinished work with only the immediate next action. Preserve the execution
sequence and its dependencies, decisions, and authorization or verification gates until each item
is completed or explicitly superseded. Repository history alone is not sufficient preservation.

Retire a handoff only after the operator explicitly confirms that the workstream is complete,
explicitly acknowledges every ruling made autonomously during implementation, and lasting decisions
and evidence have been harvested into their canonical owners. Completion acknowledgement does not
also acknowledge rulings unless the rulings were presented and the acknowledgement expressly covers
them. Once these gates are satisfied, retire the handoff before merging the workstream; do not leave
retirement as post-merge cleanup that requires another branch. Normally discard obsolete
continuation and historical narration. If a historical handoff or ledger still has a concrete use,
keep only the relevant material under `docs/archive/handoffs/` and index it as historical. Do not
archive a ledger merely to preserve everything.

### Workstream rulings

During an active workstream, retain every agent ruling in a dedicated section of its handoff.
A ruling is a decision made to resolve conflicting guidance, an ambiguity, a plan defect, a review
disagreement, or a scope or ownership trade-off. Record what was decided, why, its current status,
and the cost or risk if wrong; link to the canonical decision or implementation contract when useful.
Keep entries concise and in decision order. Routine progress narration is not a ruling.

Persist rulings before deleting temporary ledgers or other execution artifacts. A chat response,
repository history, or a current specification alone does not replace this active-workstream record.
Handoff refreshes and shortening must preserve all rulings while the workstream remains active,
including those from completed tasks. Mark superseded or reversed entries and identify their
replacements rather than deleting them; retaining the original rationale makes correction visible.

Before retirement, present every ruling made autonomously during implementation to the operator and
record its explicit acknowledgement, revision, or rejection. Keep a ruling pending until that
response is unambiguous. Workstream completion and ruling acknowledgement are separate gates, even
when the operator addresses both in one response.

At explicit operator-confirmed workstream completion, harvest lasting decisions and rationale into
canonical owners. After the operator acknowledges the ruling ledger, retire the handoff before
merge. The active record requirement does not require permanently archiving a complete ruling
ledger.
