# Cosmos Session Launch and Deep-Link Capabilities

**Status:** Historical research; superseded as the MVP integration direction
**Researched:** 2026-07-13
**Scope:** Current public Augment documentation for the Agent-Aware Project Manager MVP. This records
what is documented, not an assertion about private or future APIs.

## Conclusion

Cosmos documents interactive session creation and trigger-driven session creation, but the sources
reviewed do **not** document an external API or SDK that lets this product create, configure, and then
receive a direct reference to an arbitrary Cosmos Session. This explains why Cosmos was not selected
as the MVP integration direction. A preconfigured Cosmos webhook automation is a possible separate,
less-interactive mode, not a replacement for manual launch.

## Capability matrix

| Product need | Documented Cosmos support | MVP implication |
| --- | --- | --- |
| Create a Session | A user can start one in the Cosmos UI; an automation opens one when a matching event arrives. | No documented product-to-Cosmos create-session API. |
| Choose a model | A model may be chosen while configuring an Expert or starting a Session. | User-facing selection is documented in Cosmos UI, not as an external launch parameter. |
| Provide an editable initial instruction | The UI prompt is editable before sending. Automation inserts raw event payload as the first message. | Manual handoff can prepare text for the user to review and paste; programmable editable prefill is undocumented. |
| Select the execution directory | Cloud Environments configure repositories and filesystem state; selected repositories are cloned under `/workspace/{org}/{repo}`. | No documented per-Session working-directory parameter. The Project directory cannot be passed as a Cosmos launch argument. |
| Obtain/open a Session link | Users can copy a Session link from the Share dialog and reopen Sessions from the Sessions page. | Record a user-supplied copied link. Exact deep-link URL format and a programmatic link-return API are undocumented. |
| Resume later | Conversation is saved indefinitely; reopening a paused Session restarts its Environment after the user sends a message. | Persist the copied link; it is a return path, not a completion signal. |

## Documented flows

### Interactive Cosmos launch

1. The user opens Cosmos, selects an Expert, chooses a model if needed, and writes or edits the task
   in the prompt box.
2. Cosmos creates the Session in the selected Expert and Environment.
3. The user can later copy a link from the Share dialog and attach it to the Task in this product.

This is the only documented flow that satisfies editable human review of the instruction before it is
sent. It also keeps Session execution, transcript, and controls in Cosmos.

### Trigger-driven launch

A Cosmos automation binds an event source to an Expert. A matching event opens a Session and puts the
raw event payload into its first message. Custom webhooks provide an HTTPS event endpoint for an
external system. This can create a Session indirectly, but the reviewed docs do not describe a
callback that returns its Session URL or ID, a per-event model override, an editable pre-send prompt,
or a per-event working-directory setting.

## Historical conclusion at the time of research

At the time this research was conducted, the dependable Cosmos fallback was a manual handoff: prepare
an editable instruction and read-only Project directory path, let the user launch in the Cosmos UI,
and attach a copied Session link. That is no longer the MVP direction. The current MVP provider and
workflow are defined in [the Agent Session workflow](../product/agent-sessions.md).

Webhook automation remains background research only. It must not be treated as an active MVP launch
path without a new product decision.

## Non-Cosmos alternative

The Auggie Python and TypeScript SDKs document local agent-process launch with a model and workspace
root. That is useful evidence for a future **Auggie** provider adapter, but it does not create or
control a Cosmos Session and must not be represented as Cosmos capability.

## Sources

- [Using Sessions](https://docs.augmentcode.com/cosmos/sessions-overview.md) — interactive creation,
  prompt UI, persistence, restarting environments, sharing, and copied Session links.
- [Understanding Automation](https://docs.augmentcode.com/cosmos/automations.md) — automations open
  Sessions and insert raw event payloads as first messages.
- [Webhooks](https://docs.augmentcode.com/cosmos/config-webhooks.md) — custom webhook triggers.
- [Cloud Environments](https://docs.augmentcode.com/cosmos/environments/cloud.md) — environment-level
  repository and filesystem configuration.
- [Available Models](https://docs.augmentcode.com/models/available-models.md) — model selection in
  Cosmos when configuring an Expert or starting a Session.
- [Python SDK](https://docs.augmentcode.com/cli/sdk-python.md) and
  [TypeScript SDK](https://docs.augmentcode.com/cli/sdk-typescript.md) — local Auggie SDK distinction.
