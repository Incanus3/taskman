# Agent Session Launch and Return Workflow

**Resolved:** 2026-07-14
**MVP provider:** Auggie ACP, run locally through a provider-adapter boundary.

## Scope and boundary

The product manages a Task's references to local Auggie sessions; it is not an embedded agent
runtime. It starts or resumes a local Auggie ACP process, sends user-confirmed instructions, and
records durable provider references. It does not offer browser/deep links, an agent transcript,
live supervision, result streaming, or background status polling.

The internal adapter boundary can support later providers, but the only MVP provider is **Auggie
(ACP)**. The launch form deliberately shows it in a one-option provider selector to make that
future expansion visible without implying another provider is supported.

## Provider-adapter contract

The Auggie ACP adapter must:

- Start or discover a session in the absolute Project primary-directory path; no Task or Session can
  override that directory.
- Report whether it can expose model choices, discover or validate existing sessions, obtain a
  provider session name, and recover a stored session. The product invokes optional operations only
  when supported by current capability negotiation.
- Return and persist the opaque provider session ID after successful session creation. The ID is the
  durable reference; it is not a URL.
- Persist, where available, the provider name, resolved model or provider-default marker, created
  time, primary directory, exact submitted initial instruction, optional local label, and
  last-known recovery status.
- Treat recovery status as **available**, **unavailable**, or **not yet checked**. Update it only on
  a launch, validated attachment, or explicit resume attempt; do not poll or present it as live
  execution state.

## Launch workflow

1. In a Task's Sessions rail, the user chooses **Launch Agent Session** and sees the one available
   provider, **Auggie (ACP)**, plus the Task Project's primary directory as read-only information.
2. If the adapter reports model choices, the form offers an optional model selector. Otherwise it
   uses the provider default and records that fact.
3. The product generates an editable instruction from the Task title, description, checklist,
   state, priority, and a concise primary-directory reminder. The user must review or edit it before
   launch. The exact submitted text is retained with the Session.
4. The form offers an optional local label, initially generated from provider, resolved model, and
   launch time. If supplied, it remains the Session's primary label; it is never overwritten by a
   provider name.
5. On confirmation, the adapter creates the ACP session and sends the reviewed initial instruction.
   The product creates the Agent Session record only after session creation returns its opaque ID.
   A creation failure produces an actionable error and no Session record.
6. If no local label was supplied, the product uses Auggie's returned session name when available;
   otherwise it displays **Auggie session · short ID**. The full opaque ID remains copyable as the
   durable reference.

## Attach an existing Auggie Session

1. The user chooses **Attach Auggie Session** and pastes an existing Auggie session ID. A label is
   not required.
2. The adapter validates that the ID is discoverable or recoverable for the same Project primary
   directory. A validation failure creates no Session record.
3. On success, the product links the ID to the current Task and uses the provider name when
   available, otherwise **Auggie session · short ID**. An attached Session obeys the same recovery
   and lifecycle rules as one launched by the product.

## Session list and return workflow

- A Task lists every linked Session with its primary label, provider name when distinct, resolved
  model or default marker, created time, and last-known recovery status.
- Selecting **Resume** is always explicit. The product shows stored launch context and an editable
  next-message field, then attempts capability-supported ACP recovery and sends the confirmed
  message.
- If recovery is unsupported or fails, the product says the Session is unavailable and updates its
  last-known recovery status. It neither manufactures a link nor reconstructs a transcript.
- Agent Session actions never transition a Task's lifecycle. A person explicitly changes state and
  must review work before marking a Task Done.

## Explicit MVP exclusions and later opportunity

- Other providers, external Cosmos or Emdash task integrations, and arbitrary external links.
- Per-Session directory selection, managed workspaces, embedded conversations, live agent status,
  execution monitoring, result streaming, and automatic Task-state transitions.
- Live status is a desirable future capability, but it requires a separately specified observation
  contract; it is not implied by recovery status.