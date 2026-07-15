# Emdash and Direct Auggie Orchestration Capabilities

**Researched:** 2026-07-14  
**Scope:** Whether Agent-Aware Project Manager can replace Cosmos with Emdash, or directly
orchestrate Auggie while retaining a durable launch-and-return workflow.

## Conclusion

**Do not make Emdash the MVP integration boundary.** It is an Electron, local-first agent
development environment, but its released/mainline product is driven through its desktop UI and
internal Electron IPC. It does not document an external task-creation API, return URL, or task
deep-link contract for another local web application.

An inbound Emdash MCP server could offer task creation and PTY observation, but it is currently an
open, unmerged pull request. It is not a stable MVP dependency.

**Direct Auggie ACP is the viable fallback and recommended first concrete adapter.** The product
can act as an ACP client, start local Auggie with the Project primary directory as the session
working directory, persist the returned opaque session ID, and resume it later when the negotiated
agent capabilities allow. This is local process orchestration, not a Cosmos Session or an external
GUI handoff.

## Emdash assessment

| Requirement | Current Emdash evidence | MVP assessment |
| --- | --- | --- |
| Run locally | Emdash is an Electron desktop application with local SQLite state. | Yes, but it is a separate desktop application. |
| Select provider and prompt | Its UI creates a task, selects a detected CLI provider, and enters a prompt. Auggie is listed as a provider. | Yes through Emdash's UI, not a documented external contract. |
| Use Project directory | Current source has a `repo-root` workspace option that uses the repository directory without a worktree. | A compatible UI choice, but not externally programmable. |
| Durable task and status | Emdash persists Tasks and conversations in its SQLite database and presents terminal/diff UI. | Internally available; no supported external ID/status interface found in mainline. |
| Launch/return target | No documented URL scheme or external deep-link API was found. | Not suitable. |

The promising alternative is [open PR #2055][emdash-pr], which proposes a loopback-only, opt-in
MCP server with task and PTY tools. The GitHub API reports it as open and unmerged; the referenced
server code is absent from the inspected `generalaction/emdash` main branch. Treat it as future
evidence only, not a product capability.

## Direct Auggie options

| Interface | Confirmed capability | Limitation for this product |
| --- | --- | --- |
| CLI interactive mode | Initial instruction, explicit workspace root, model selection, saved-session list, and resume by session ID are documented. | The product would have to launch and supervise a separate terminal process; no GUI deep-link is documented. |
| CLI print mode | Runs one instruction and returns output before exit. | Not a durable interactive-session workflow. |
| TypeScript/Python SDK | Launches a local Auggie ACP process in a selected workspace and supports bidirectional prompts and streaming updates. | The documented SDK surface does not expose durable session-ID/recovery methods. |
| ACP client | `session/new` returns an ID and binds the session to an absolute working directory; standard ACP supports prompt, load, resume, and list behind advertised capabilities. | Product must implement the client, persist the ID, and check capability negotiation at runtime. |

The Auggie CLI documents `--workspace-root`, `--model`, `--instruction`, `session list --json`,
and `--resume <sessionId>`. Auggie documents that `--acp` makes it an ACP agent; ACP's standard
defines a session ID and requires the client to use advertised support before attempting optional
load, resume, or list operations.

## Recommended adapter boundary

1. Treat **Auggie ACP** as the first provider adapter, launched locally with the read-only Project
   primary-directory path as the ACP session working directory.
2. The product prepares an editable Task instruction and lets the user choose the exposed model
   before it starts the agent. It persists provider, model, created time, opaque ACP session ID,
   directory, label, and the negotiated recovery capabilities.
3. The launch action creates the Agent Session record only after ACP successfully returns its ID.
   A failed launch creates no Session record.
4. Returning to a Session means the product invokes the stored ACP ID via supported recovery, or
   clearly says that the local session is unavailable. It must never claim a browser or desktop
   deep link exists unless a provider documents one.
5. This expands MVP scope from an external-session launcher to **local agent-process lifecycle
   management**. Whether MVP also displays a live transcript or only offers explicit send/resume
   controls remains a product decision; neither must imply Task completion.

## Sources

- [Emdash introduction][emdash-introduction] — desktop Electron architecture, local SQLite, task UI,
  provider model, and agent PTYs.
- [Emdash providers][emdash-providers] — Auggie support and prompt/provider behavior.
- [Emdash main source][emdash-source] — inspected for public external control and workspace modes.
- [Emdash MCP-server PR][emdash-pr] and its [GitHub API record][emdash-pr-api] — proposed MCP
  surface; current open/unmerged status.
- [Auggie CLI reference][auggie-cli] — workspace, model, instruction, saved-session, and resume
  commands.
- [Auggie ACP mode][auggie-acp] — Auggie as an ACP agent over standard input/output.
- [Auggie TypeScript SDK][auggie-sdk] — local ACP process, workspace configuration, and updates.
- [ACP session setup][acp-setup] and [ACP session list][acp-list] — IDs, working-directory rules,
  capability-gated session recovery, and discovery.

[emdash-introduction]: https://generalaction-emdash-14.mintlify.app/introduction
[emdash-providers]: https://generalaction-emdash-14.mintlify.app/features/providers
[emdash-source]: https://github.com/generalaction/emdash
[emdash-pr]: https://github.com/generalaction/emdash/pull/2055
[emdash-pr-api]: https://api.github.com/repos/generalaction/emdash/pulls/2055
[auggie-cli]: https://docs.augmentcode.com/cli/reference.md
[auggie-acp]: https://docs.augmentcode.com/cli/acp/agent.md
[auggie-sdk]: https://docs.augmentcode.com/cli/sdk-typescript.md
[acp-setup]: https://agentclientprotocol.com/protocol/v1/session-setup
[acp-list]: https://agentclientprotocol.com/protocol/v1/session-list