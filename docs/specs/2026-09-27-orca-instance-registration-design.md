# User-owned Orca instance registration

**Status:** Proposed for operator review, 2026-09-27. This document authorizes neither an
implementation plan nor product code.

## Purpose and current context

An authenticated Taskman user needs to remember an Orca server they can reach from Taskman's
backend, see their registrations, verify one on demand, and remove it. Each registration and its
runtime pairing credential belongs only to that user. This is a personal integration beside the
shared Project, List, and Task workspace; it does not introduce user ownership of those records.

Taskman currently runs on Phoenix LiveView with PostgreSQL, an Ash Accounts domain, authenticated
browser sessions, API keys, a versioned `/api/v1` JSON API, and a Req-backed `taskman` CLI. Account
Settings is already an authenticated LiveView. The current [MVP product specification](../product/mvp-spec.md)
defers Agent Session integration. The [domain model](../product/domain.md) defines shared work
records, and the [roadmap](../planning/roadmap.md) has not made remote session control the next
slice. The [authenticated hosted-access design](2026-09-02-authenticated-hosted-access-design.md)
defines user/session/API-key and account-deletion boundaries. This design adds a narrow user-owned
credential boundary without changing shared work or account authentication.

The deployed Taskman backend, including its installed Orca CLI, must be able to reach each allowed
Orca endpoint through a private Tailscale route. The browser and the `taskman` CLI speak only to
Taskman. Orca's [remote-server guide](https://www.onorca.dev/docs/remote-servers) says a pairing
link grants runtime access, paired clients have separate revocable server grants, and Tailscale
grants should remain narrow. The [Orca CLI reference](https://www.onorca.dev/docs/cli/reference)
documents `orca status --json`, remote routing, and saved environments. The installed CLI in this
design worktree also advertises `ORCA_PAIRING_CODE` for remote routing; exact installed-version
behavior must be verified before shipping. Orca's published [pairing encoder](https://github.com/stablyai/orca/blob/main/src/shared/pairing.ts)
uses `orca://pair?code=` with a base64url JSON offer, and its [offer schema](https://github.com/stablyai/orca/blob/main/src/shared/mobile-relay-pairing-offer.ts)
contains `endpoint`, `deviceToken`, `publicKeyB64`, and optional scope/relay fields. The beta
protocol can change; Taskman must fail closed on an unrecognized offer or CLI result.

## Scope and user contract

The first increment has four operations: add, list, test connection, and remove. A user can save a
valid registration while its Orca server is offline. Add does not contact Orca or promise that the
grant works. List performs no network request, never reveals pairing material, and does not imply
live connection state. Test is an explicit read-only attempt; it does not browse or control Orca.
Remove deletes Taskman's registration and stored credential. It does **not** revoke the separate
grant on the Orca server; the confirmation and success text must explain how to revoke that grant
in Orca's Shared Server Access. Removal cannot truthfully claim remote revocation.

The Account Settings surface has a named registration form, a masked pairing-link field, a list of
the acting user's names and safe endpoints, a Test connection action per row, and a Remove action
with explicit confirmation. Show a neutral **Not tested** state on load; show the current page's
test result and time after a test. Reloading returns to Not tested. A failed test must not erase the
registration. Browser forms do not place a pairing link in a URL or retained flash message. There
is no edit, automatic polling, server discovery, browsing, session control, sharing, or Orca-grant
revocation in this increment.

The `taskman` CLI has matching `orca add`, `orca list`, `orca test`, and `orca remove` commands.
`taskman orca add --name NAME` reads the pairing link from a no-echo terminal prompt. For
automation, `--pairing-link-stdin` reads one bounded line from stdin; there is no secret-valued
CLI flag or environment variable. `taskman orca test ID` and `taskman orca remove ID` use the
Taskman registration ID. Remove requires `--yes` in noninteractive use, otherwise an interactive
confirmation explaining the remote grant. Global `--json`, help, completions, and the bundled CLI
skill cover the commands. Neither output mode prints the pairing link, including errors.

## Persistence and authorization

Use one `OrcaRegistration` record in the Accounts domain with an opaque UUID primary key,
non-null `user_id` referencing Users with `ON DELETE CASCADE`, trimmed display `name`, normalized
allowed `endpoint`, encrypted pairing offer, encryption key ID, nonce/tag, and UTC creation time.
No live status or probe transcript is persisted. Enforce a case-insensitive unique name per user
and allow the same name or endpoint for different users. Name is 1–80 Unicode grapheme clusters
after trimming, with control characters rejected. One user may register several servers; no
arbitrary per-user count cap is introduced for this increment. An owner-only Ash policy governs
create, read, test lookup, and destroy. The public Accounts use cases always take the authenticated
actor and derive `user_id` from it; callers cannot supply an owner. Lookup for show/test/remove
filters by both ID and actor, so another user's UUID returns the same `404` as a missing UUID.
Administrators have no cross-user registration access through either surface or AshAdmin.

The account-deletion workflow explicitly includes registrations in its dependent cleanup and the
foreign key cascade is a final integrity guard. Deleting or disabling an account removes or
blocks access through the existing session and API-key paths. Disabling does not delete the
registrations. An account's own delete and administrator delete must leave no live registration or
credential rows. Old database backups may retain ciphertext until their retention period ends.

Encrypt the entire normalized pairing offer with a random nonce and an authenticated cipher
(AES-256-GCM), binding `user_id`, registration ID, and format version as associated data. Never
store the link, device token, or public key as plaintext columns; `endpoint` alone is nonsecret
display and policy metadata. The runtime keyring is provided outside the database through protected
release configuration: `TASKMAN_ORCA_ACTIVE_KEY_ID`, a `TASKMAN_ORCA_KEYRING_JSON` object mapping
key IDs to base64-encoded 32-byte keys, and retained older keys during rotation. The release
environment file holding these values is readable only by the service owner and deployment
administrator; it is excluded from source control and log capture. Production startup fails if the
active key is absent or malformed. Decryption of a record whose
key ID is missing or whose tag fails returns an operational failure, never a plaintext fallback.
Key rotation adds a new active key, reencrypts existing rows in a bounded recoverable migration,
and removes old keys only after verification and backup-retention review. Back up ciphertext and
the protected keyring separately; recovery requires both. Database dumps, application logs,
telemetry, error reports, and audit events must not include the offer or decrypted value. Runtime
BEAM memory and the short-lived child environment still contain it during a test; deployment
access to the service identity is therefore trusted.

## Pairing input and outbound network boundary

Accept only one canonical `orca://pair?code=<base64url>` URL, with no fragment, user info, extra
query parameters, or whitespace around/inside it. Bound the submitted URL to 8 KiB and decoded
JSON to 6 KiB before parsing. Decode exactly one JSON object, reject duplicate keys, unknown
version/scope/relay forms, missing or extra security-sensitive fields, and malformed tokens. Require
the current runtime offer (version 2, `scope` absent or `runtime`, and no relay). Do not accept bare
base64, mobile-scoped offers, HTTPS relays, or hand-entered endpoints. The endpoint is extracted
from the offer; a second caller-supplied endpoint cannot override it. Re-encode a canonical URL
from validated fields before encryption so stored input has one meaning.

For this increment, accept only `ws://` or `wss://` with a numeric IPv4 address in Tailscale's
`100.64.0.0/10` range and an explicit port from 1 through 65535. Reject hostnames, IPv6, DNS,
URL credentials, fragments, query strings, non-root paths, encoded/alternate IP spellings,
localhost, public/private non-tailnet addresses, and wildcard addresses. Taskman stores the
normalized scheme/address/port. Validate at add and again immediately before each test using the
decrypted offer, checking that its endpoint still matches the stored normalized endpoint. A
separate egress firewall permits the Taskman service identity to reach only approved tailnet
destinations/ports; Tailscale grants permit only the necessary Taskman-to-Orca path. Input checks
are not the sole SSRF defense. This IP-only restriction excludes MagicDNS and non-Tailscale private
routes for now; relaxing it needs a new reviewed network policy. `ws://` is acceptable only over
the approved private Tailscale path, where Tailscale supplies transport encryption.

## Connection test and failure boundary

After owner lookup and endpoint revalidation, run the trusted, absolute Orca CLI executable named
by `TASKMAN_ORCA_CLI_PATH` (resolved and version-checked at startup), with the fixed argument
vector `status --json`. Pass the decrypted link only as `ORCA_PAIRING_CODE` in the child
environment. Do not pass `--pairing-code`, call a shell, create a
saved Orca environment, or use a long-lived shared Orca profile. Give the child a minimal
environment, private temporary HOME/XDG directories, closed stdin, and no inherited Orca routing
variables or proxy settings. Do not run `orca open`; a failed remote test must not start or mutate
a local runtime. Limit one in-flight test per user and five tests per user per minute. Impose a
five-second total deadline, terminate and reap the child on expiry, and cap each stdout/stderr
stream at 32 KiB; overflow is failure. Never include raw CLI stdout/stderr in responses or logs.

A zero exit with a parseable `status --json` success shape is **connected**. Explicit remote
authentication rejection is **grant_rejected**; timeout is **timed_out**; connection refusal or
route failure is **unreachable**; a proven protocol/version incompatibility is **incompatible**.
Unrecognized nonzero exits, malformed/oversized output, CLI launch failure, or ambiguous response
are **probe_failed**. Do not classify an ambiguous CLI message as authentication rejection by
substring matching. The adapter must use documented structured fields or verified exact exit/error
contracts for the installed CLI version; otherwise return `probe_failed`. A per-user rate-limit
rejection is `429`, and missing CLI/key or unsafe runtime configuration is `503`, without starting a
probe. A removed record cancels any later lookup; an already-running test may finish but cannot
resurrect the record. A test result is a point-in-time observation, not a durable health guarantee.

Before implementation, verify on the pinned deployment CLI that `ORCA_PAIRING_CODE` routes
`status --json` directly to the remote server without writing pairing credentials or returning
unbounded status details. Inspect all state it uses, including config/data directories, metadata,
locks, caches, and XDG variables. If the installed CLI cannot meet this contract, stop before
shipping the test operation and revise this design; the CLI's saved `environment add` path is not
an acceptable silent fallback.

## HTTP API contract

All routes live under the existing API-key authenticated `/api/v1` pipeline. Browser sessions alone
cannot call them. JSON request and response envelopes follow existing Taskman conventions. The
pairing link appears only in the add request body and is never echoed. Timestamps use UTC ISO 8601.

| Request | Success | Behavior |
| --- | --- | --- |
| `POST /api/v1/orca-registrations` with `{"registration":{"name":"Desk","pairing_link":"<secret>"}}` | `201 {"data":<registration>}` | Validate and save without network access. |
| `GET /api/v1/orca-registrations` | `200 {"data":[<registration>,...]}` | Only the caller's rows, ordered by case-folded name then ID. |
| `POST /api/v1/orca-registrations/:id/test` with no body | `200 {"data":{"id":"<uuid>","status":"connected|grant_rejected|timed_out|unreachable|incompatible|probe_failed","checked_at":"<UTC>"}}` | One on-demand probe. |
| `DELETE /api/v1/orca-registrations/:id` | `204` with empty body | Delete caller's row and encrypted credential. |

`<registration>` has exactly `id`, `name`, `endpoint`, and `created_at`. It has no credential,
masked credential prefix, server grant ID, or cached test result. No GET-by-ID or update operation
is exposed. IDs are UUIDs; malformed IDs return `400 invalid_request`, while well-formed missing
or cross-user IDs return `404 not_found`. An absent/invalid API key returns the existing `401
unauthorized` envelope, and a disabled user is rejected by that same authentication boundary.
Malformed payloads return `400 invalid_request`; invalid name or pairing input returns `422
validation_failed` with safe `fields.name` or `fields.pairing_link` entries. A duplicate name for
the same owner returns `409 name_taken`. Rate limiting returns `429 rate_limited`; unavailable probe
infrastructure returns `503 probe_unavailable`. These errors use
`{"error":{"code":"...","message":"..."}}`; validation errors add `fields`. Error messages
never reflect user-supplied pairing text, decrypted values, remote output, or internal addresses
beyond the separately safe stored endpoint. Test outcomes in a `200` response are operational
results, not permission or transport errors. The Taskman CLI keeps its established exit statuses:
`0` for successful API calls including a completed negative test, `2` for locally invalid input,
`3` for `400`/`404`/`409`/`422`, `4` for Taskman transport failure, `5` for `429`/`503` or an
invalid Taskman response, and `7` for `401`/`403`. JSON mode preserves these API envelopes.

## Expected implementation boundaries

- `lib/taskman/accounts/orca_registration.ex` and focused Accounts workflow/persistence modules:
  Ash resource, owner policy, validation, encryption, owner-scoped use cases, and account deletion
  integration. `lib/taskman/accounts.ex` exposes the four use cases; web modules never query Repo.
- `lib/taskman/orca/`: pairing-offer parser and endpoint policy, encryption/keyring adapter, and a
  narrow connection-probe runner. The runner owns executable configuration, environment isolation,
  process deadline/output bounds, and result mapping. No Orca worktree/session API is introduced.
- `priv/repo/migrations/` and Ash snapshots: registration table, owner/name constraints, cascade,
  encrypted columns. Generate migrations with the repository's Mix migration task.
- `config/runtime.exs` and deployment guidance: required encryption keyring, absolute Orca CLI
  path/version, private network grant and egress assumptions, backup/rotation procedure.
- `lib/taskman_web/live/account_settings_live.ex` and its components: add/list/test/remove within
  the authenticated live session; stable IDs and safe transient test feedback.
- `lib/taskman_web/controllers/api/`, router, representation, fallback: four API routes and the
  exact envelopes above, with owner-scoped context calls.
- `lib/taskman/cli/`: registry, parser, IO/client/output, help, completions, onboarding and bundled
  skill: matching commands and secret-safe input/output.
- Focused `test/` files cover each boundary without a real server grant. Update product and roadmap
  documents only after the operator approves the written specification and delivery direction.

## Verification strategy

Parser tests cover accepted runtime offers and each rejected URL, field, size, endpoint, and
non-tailnet case. Encryption tests prove ciphertext-only persistence, user/record binding, tamper
rejection, missing key failure, rotation, and redaction. Accounts and web/API tests use two users
and an administrator to prove owner-only add/list/test/remove, cross-user `404`, disabled-account
denial, duplicate-name handling, account-deletion cleanup, offline add, and no secret disclosure.
LiveView tests use stable element IDs and rendered interactions. CLI tests verify no-echo or stdin
input, help/completion/skill parity, JSON and exit behavior, and absence of secret values in argv,
output, exceptions, and diagnostic capture.

The probe runner uses an injected fake executable to test exact argv/environment, isolated state
directories, success, rejection, unreachable, timeout/reaping, concurrent/rate bounds, malformed
JSON, output overflow, and no saved profile. A controlled Orca runtime integration check validates
the pinned CLI's remote `ORCA_PAIRING_CODE` and JSON/error contract without putting credentials in
test fixtures or logs. Verify service egress and Tailscale grants in deployment acceptance; a fake
runner cannot prove network confinement. Run focused tests and then the repository `mix precommit`
gate once implementation is complete. Do not turn this design checkpoint into product-code tests.

## Alternatives and trade-offs proposed for review

| Approach | Assessment |
| --- | --- |
| User-owned encrypted Taskman records and transient backend CLI probe (recommended) | Keeps ownership, API/CLI parity, backup, and account deletion under Taskman's existing boundary. Adds key management and carefully bounded child-process/network risk. |
| Per-user Orca profile directories | Orca would own credential persistence, but Taskman would need to track every profile file, cache, lock, metadata file, and lifecycle rule; owner isolation, account deletion, release backups, and switching become filesystem dependent. Rejected for this increment. |
| Browser/client-side connector | Avoids backend Orca credentials but requires local Orca or Tailscale access from every browser/CLI client and diverges from Taskman's hosted, API-driven contract. Rejected for this increment. |

The IP-only Tailscale rule deliberately narrows deployment options. Encryption protects database
copies but does not protect against a compromised Taskman runtime or service identity. A pairing
grant may remain active after Taskman removal or account deletion until the Orca server operator
revokes it. An offline add can store a stale or already revoked link. Orca beta protocol/version
changes can make existing registrations temporarily untestable; Taskman must preserve their
ciphertext and report a safe test failure rather than silently rewriting them.

## Next-session checklist after operator review

1. Resolve review comments on this proposed specification, particularly the IP-only endpoint
   restriction, the four-operation API/CLI contract, and the server-side revocation wording.
2. Obtain explicit approval of the written specification; then create and review a separate
   implementation plan. Do not infer plan approval from the earlier approval of the design direction.
3. In the implementation session, read this complete specification first. Verify the pinned Orca
   CLI/env/state contract before implementing the probe, and retain owner/secret and egress tests as
   release gates.
