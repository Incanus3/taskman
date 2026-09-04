# Authenticated Hosted Access and Release Deployment

**Status:** Approved
**Date:** 2026-09-02

## Context

Taskman is a Phoenix 1.8 LiveView application backed by PostgreSQL. Its browser UI and versioned
JSON API currently provide shared access to Projects, nested Lists, and Tasks. The installable
`taskman` escript calls that API through Req. The application was intentionally local-only:
production binds the endpoint to loopback, the browser has no login, and every `/api/v1` operation
is unauthenticated.

The application is now intended to run on a web-accessible server. Arbitrary browsers and API
clients must be able to connect through HTTPS, but only explicitly provisioned users may access the
shared Taskman workspace. This changes the product boundary from locally started and unauthenticated
to hostable and authenticated.

Taskman remains a shared workspace. Authentication identifies an allowed user and gates the whole
application; it does not give Projects, Lists, or Tasks an owner and does not filter domain data by
user. Multi-user ownership, collaboration, per-resource permissions, and attribution remain
excluded.

The design baseline is upstream commit
`7a0a664caa6c2ef050649b98202df6db8c1ee415` (`Add immediate cooperative workspace updates`). The
workspace was clean when this design was finalized. The approved implementation plan is
`docs/plans/2026-09-02-authenticated-hosted-access.md`; Beads feature
`tas-authenticated-hosted-access-2a8` owns its delivery graph.

## Outcome

Taskman can run as an OTP release behind an HTTPS reverse proxy and a systemd service. Allowed users
can sign in from an arbitrary browser, manage their own credentials and API keys, and use the
existing shared workspace. Administrators can invite and manage allowed users through a constrained
AshAdmin surface. API clients authenticate with expiring, revocable API keys, and the `taskman` CLI
stores its server URL and key in a protected XDG configuration file.

Success means:

- no browser LiveView or `/api/v1` operation is usable without valid authentication;
- public self-registration does not exist;
- a server-local command can bootstrap the first administrator and recover administrative access;
- administrators can invite, correct or confirm another user's email, disable, delete, promote, and
  demote users without bypassing Ash policies;
- invited users verify their email and choose their own password through a one-time setup link;
- users can change their password, confirm a new email, recover a forgotten password, revoke
  sessions, manage their own API keys, and permanently delete their own account;
- disabled users lose browser and API access immediately, including connected LiveView sockets;
- existing Project, List, Task, API, and notification behavior remains unchanged behind the access
  gate;
- the application builds and runs as an OTP release with migration, bootstrap, systemd, Caddy, and
  rollback documentation; and
- focused verification and `mix precommit` pass.

## Scope

### Included

- Ash, AshPostgres, AshPhoenix, AshAuthentication, AshAuthenticationPhoenix, AshAdmin, and
  AshRateLimiter integration.
- An isolated `Taskman.Accounts` Ash domain.
- `User`, authentication `Token`, and `ApiKey` Ash resources backed by PostgreSQL.
- Password authentication using the AshAuthentication Argon2 provider.
- Administratively provisioned accounts with seven-day setup invitations.
- Confirmed email changes and one-hour password recovery.
- Stored, revocable browser sessions with a 30-day lifetime.
- Administrator-only account management through a constrained AshAdmin route.
- Administrator correction and explicit confirmation of another user's email.
- User self-service account, session, API-key, and account-deletion management.
- Immediate permanent account deletion with transactional credential cleanup and last-active-admin
  protection.
- Mandatory API authentication through expiring API keys.
- Persistent XDG CLI configuration and environment-variable overrides.
- Targeted authentication rate limiting for the initial single-server deployment.
- Resend transactional email through Swoosh and Req.
- OTP release generation and release-local migration and bootstrap commands.
- A systemd unit and Caddy reverse-proxy example for a dedicated Linux host.
- Product, development, onboarding, CLI help, bundled skill, and deployment documentation updates.

### Excluded

- Project, List, or Task ownership by a user.
- Per-user filtering, roles for domain operations, collaboration permissions, or activity
  attribution.
- Migrating Project, List, or Task resources or contexts to Ash in this workstream.
- Building new domain capabilities as Ash resources while existing Taskman domain resources remain
  Ecto contexts.
- Public self-registration.
- Magic-link, OAuth, OIDC, passkey, or multi-factor sign-in. The design keeps these AshAuthentication
  strategies addable later.
- A persistent authentication audit-log UI. Security-relevant events are logged without secrets.
- Containers, container orchestration, automated deployment, managed hosting, or publishing a live
  instance.
- Multiple Taskman server profiles in the CLI configuration.
- A generalized permission or role framework. The `admin?` flag governs Accounts administration
  only.
- Moving an existing PostgreSQL database to another server or managed service, provisioning
  PostgreSQL infrastructure, configuring backups, or provisioning the application host. Schema
  migrations required by this workstream remain included.

## Accepted architecture

### An isolated Ash Accounts domain

The only Ash domain added by this workstream is:

```text
Taskman.Accounts
├── Taskman.Accounts.User
├── Taskman.Accounts.Token
└── Taskman.Accounts.ApiKey
```

`User` uses a UUID primary key. Existing domain tables retain their bigint keys; there is no
relationship between Accounts records and Project, List, or Task records in this increment.

`Taskman.Repo` changes from `use Ecto.Repo` to `use AshPostgres.Repo`. AshPostgres Repo is a thin
wrapper around Ecto Repo, so existing Ecto schemas, queries, transactions, migrations, SQL sandbox
behavior, and public contexts continue to use `Taskman.Repo`. This substitution is nevertheless the
first implementation compatibility gate: the unchanged repository suite must pass before further
auth work proceeds.

Ash resource snapshots and generated migrations live in their conventional project-owned
locations. Existing hand-written Ecto migrations remain authoritative for existing tables. The
Accounts migration creates only the Accounts tables and required PostgreSQL extensions.

`Taskman.Accounts` is configured to authorize by default. Accounts actions receive an explicit
actor wherever an authenticated user initiates the action. Narrow authentication interactions and
the initial server-local bootstrap action use explicit policy bypasses required for those flows;
ordinary web code cannot disable authorization globally.

### No permanent hybrid domain direction

Ash is introduced now because AshAuthentication and AshAdmin fit the hosted-access problem and
because the operator intends to migrate Taskman's complete domain to Ash later. That later migration
is a separate workstream.

Until that migration is designed:

- existing domain capabilities continue through the existing Ecto contexts;
- this workstream does not add Ash representations of Projects, Lists, or Tasks;
- later domain features do not begin as isolated Ash resources merely because Ash is now installed;
  and
- a future migration moves coherent resource clusters and all their callers rather than maintaining
  competing Ecto and Ash mutation paths indefinitely.

This constraint avoids duplicated validation, notification, optimistic-locking, and API error
semantics during ordinary feature delivery.

## Account model and policies

### User state

`User` contains at least:

- `id`, a UUID primary key;
- case-insensitive unique `email`;
- sensitive `hashed_password`, nullable until invitation setup completes;
- `status`, one of `pending`, `active`, or `disabled`;
- `admin?`, defaulting to `false`;
- confirmation/setup metadata required by AshAuthentication;
- inserted and updated timestamps; and
- the authentication relationships required for stored tokens and API keys.

Initially, an account can sign in only when its status is `active`, its email is confirmed, and it
has a password credential. Pending and disabled users cannot authenticate or create API keys.
Future authentication strategies may define their own credential requirements while preserving the
active-account and confirmed-identity checks.

The first server-local bootstrap creates an active, confirmed administrator because there is no
prior administrator available to receive or approve an invitation. Later users default to
non-administrators and begin in `pending`.

### Administrative invariants

Only an active administrator may:

- list or inspect accounts other than itself through the admin surface;
- invite a user;
- resend or revoke a pending invitation;
- change another user's email and explicitly choose whether the resulting address is confirmed;
- enable or disable a user;
- permanently delete a pending, active, or disabled user;
- promote or demote a user; or
- revoke another user's sessions or API keys.

The last active administrator cannot be demoted, disabled, or deleted. Concurrent attempts must
preserve this invariant transactionally rather than relying on a UI count.

An administrator may change another account's access state but may not read password hashes,
plaintext tokens, or plaintext API keys. Ash field sensitivity and field policies protect those
values in addition to the admin UI configuration.

### Self-service actions

An active authenticated user may:

- inspect its own safe account fields;
- request and confirm an email change;
- change its password after validating the current password;
- request a password reset through the public recovery flow;
- list and revoke its own stored browser sessions;
- revoke all other browser sessions;
- list, create, and revoke its own API keys;
- permanently delete its own active account after validating the current password; and
- sign out.

An authenticated password change revokes every other stored browser session but rotates and
preserves the session that performed the change, so the user remains signed in. Other-session
LiveViews are disconnected; tabs sharing the acting browser session remain usable after any
required reconnection. A recovery-link password reset has no trusted acting session and revokes
every browser session. Neither operation revokes API keys, avoiding unexpected automation outages.
Disabling an account invalidates both sessions and API keys.

### Permanent account deletion

Account deletion is an explicit Ash destroy operation with separate policy-authorized entry points
for self-service and administration. It is not exposed as an unrestricted generic resource destroy
action.

Self-deletion is available only to an active authenticated user. The settings form requires the
current password and a separate explicit destructive confirmation. An administrator may delete a
pending, active, or disabled account through the admin surface without re-entering the
administrator's password after accepting a standard destructive confirmation dialog. The
administrator-delete action rejects self-targeting; administrators delete their own accounts
through the password-confirmed self-service flow.

The destroy transaction permanently removes the User and all dependent authentication Tokens,
stored browser sessions, setup/reset/email-change tokens, and API keys. Foreign-key cascades
provide database integrity, while the Ash action remains the authorization and lifecycle boundary.
Project, List, and Task records remain untouched because this workstream creates no ownership or
attribution relationship from them to User.

Deleting the final active administrator fails transactionally, including under concurrent delete,
disable, and demotion attempts. A successful deletion broadcasts the same account-revocation event
used by disablement so connected LiveView sockets terminate promptly. All later browser and API
authentication fails because the user and its credentials no longer exist.

Deletion is immediate and has no application-level undo, grace period, anonymized tombstone, or
account-restoration flow. It removes the account from the live Taskman database; it does not claim
to erase already emitted email-provider delivery metadata, security logs containing opaque record
IDs, or independently retained database backups.

## Provisioning, invitations, and recovery

### Bootstrap and break-glass administration

Development/source operation provides:

```sh
mix taskman.accounts.create-admin
```

The task securely prompts for email, password, and password confirmation. The password is never a
command argument, environment variable, or log value. It calls the same public Accounts bootstrap
operation used by the release command.

The release overlay provides:

```sh
bin/create-admin
```

It invokes a release task that starts only the applications required for Accounts and Repo access,
prompts through the local terminal, and creates a new active confirmed administrator. Duplicate
email returns a clear non-zero failure. The command remains available as break-glass recovery even
after other administrators exist; server shell access is the authority boundary.

The command never starts the Phoenix endpoint and has no HTTP equivalent.

### Invitation

An administrator enters an email address and the intended administrator flag in AshAdmin. Taskman
creates a pending user and sends a single-use setup link valid for seven days. The link verifies the
email and lets the user choose and confirm a password with a minimum length of eight characters.
Successful setup atomically activates the account and consumes the token.

Resending an invitation issues a new token and invalidates previous setup tokens. Revoking an
invitation invalidates its tokens and leaves the pending account unable to authenticate; the
administrator may issue a new invitation later or keep it revoked.

If delivery fails after the pending account is created, the account remains pending and the
administrator receives an actionable error with a resend path. Delivery is not allowed to create
an active account accidentally.

### Email change

Changing email requires the current password. The new address remains pending and the old address
continues to authenticate the user until a single-use confirmation link is followed. The link is
valid for 24 hours. Successful confirmation atomically installs the case-insensitively unique new
email. Invalid, expired, used, or conflicting links do not change the account.

A delivery failure leaves the current email unchanged and preserves a recoverable pending request.
Resending generates a fresh token and invalidates the previous one.

### Administrative email management

An active administrator has a dedicated Ash action for another pending, active, or disabled user.
It accepts an email address and an explicit `confirmed?` choice. The action rejects self-targeting;
administrators use the ordinary self-service flow for their own address. It normalizes and checks
case-insensitive uniqueness before changing state and does not expose generic User updates.

For an active or disabled account with an established email identity:

- a changed email with `confirmed?` true atomically installs and confirms the new address;
- a changed email with `confirmed?` false starts the ordinary 24-hour email-change confirmation
  flow, leaving the current confirmed address in place until success;
- an unchanged email with `confirmed?` true marks the existing address confirmed; and
- an unchanged email with `confirmed?` false is rejected because it has no effect.

For a pending invited account, a changed primary email is installed immediately. All prior setup
links and pending email tokens are invalidated, and Taskman immediately issues and sends a fresh
seven-day setup invitation to the resulting address. With `confirmed?` false, completing that
setup verifies the address and sets the password. With `confirmed?` true, the address is confirmed
immediately, but the fresh setup link is still sent so the user can choose a password. A pending
account never receives the active-account email-change confirmation link.

For a pending account whose email is unchanged, `confirmed?` true confirms the existing address
without replacing an otherwise-valid setup invitation; `confirmed?` false is rejected because it
has no effect.

If fresh invitation delivery fails, the pending account retains its new email, the former setup
links remain invalid, and the administrator receives an actionable resend path. No separate
account-change notification is sent for any administrator email action; only the confirmation or
setup message required by the selected lifecycle is delivered.

Every successful administrative email action invalidates older pending email-change tokens.
Existing sessions and API keys remain valid when the resulting account is active and confirmed;
disabled accounts remain unable to authenticate. Security logs contain the administrator and
target record IDs but not either email value.

### Password reset

The public sign-in surface offers password recovery. A reset request always produces the same
browser response regardless of whether the address exists, is pending, is active, is disabled, or
delivery fails. An eligible active account receives a single-use reset link valid for one hour.
Successful reset installs the new Argon2 hash and revokes every browser session.

Operational delivery failures are logged without revealing them to an unauthenticated requester.

### Future strategies

Password is the only initial browser sign-in strategy. The User resource, token storage, confirmed
email identity, route organization, and settings UI must allow later addition of magic link and
OAuth/OIDC strategies without changing Project, List, or Task data or authentication boundaries.
Future strategies require their own design and are not represented by inactive UI controls now.

## Browser and LiveView boundary

### Router organization

The browser pipeline loads authentication subjects from the stored session. Public authentication
routes are limited to:

- sign-in;
- password reset request and completion;
- invitation setup;
- email confirmation callbacks; and
- the authentication controller callbacks required by AshAuthenticationPhoenix.

There is no public registration route.

Existing ProjectLive routes move into an Ash authentication LiveView session with a required-user
`on_mount` hook. The hook assigns `current_user`, rejects pending or disabled users, records the
stored session identity needed for revocation, and redirects unauthenticated users to sign-in while
preserving a safe return path.

Account settings use the same authenticated session. AshAdmin uses a distinct admin-required
LiveView session and a second server-side administrator check. Navigation between authentication
classes may perform a full page load where Phoenix LiveView session boundaries require it.

### Immediate session invalidation

Revoking a session, recovery-resetting a password, or disabling a user revokes each affected stored
token and broadcasts a disconnect to its LiveView socket identity. An authenticated password
change revokes and disconnects every other session while rotating and preserving the acting
session. A disconnected socket cannot continue to call existing domain contexts; reconnect
performs the normal authenticated mount and redirects to sign-in when its session was revoked.

The UI hiding an action is never the authorization boundary. Ash policies enforce Accounts actions,
and the authenticated LiveView/API pipelines enforce entry into the existing shared domain.

### Layout and navigation

The sign-in, setup, confirmation, and recovery pages use Taskman's existing root layout and
daisyUI/Tailwind foundation. AshAuthenticationPhoenix's daisyUI overrides may provide the initial
components, but the delivered pages must follow the project's typography, spacing, accessibility,
and responsive behavior.

Authenticated navigation adds an account menu with:

- Account settings;
- Administration for administrators only; and
- Sign out.

Existing Project/List/Task behavior remains visually unchanged apart from this navigation and the
authenticated user assign.

### Account settings

The settings surface has stable DOM IDs and focused sections for:

- current and pending email;
- password change;
- active sessions and session revocation;
- API-key listing, creation, one-time plaintext display, and revocation; and
- a visually distinct danger zone for permanent self-deletion.

API-key creation includes a copy affordance and explicitly says that Taskman cannot show the key
again.

### AshAdmin

AshAdmin is mounted at `/admin` and receives the current administrator as its actor. Only the
Accounts domain is supplied. The User admin configuration allowlists the actions needed for account
inspection, invitation, invitation resend/revocation, activation/disablement, administrator
changes, explicit email management, credential revocation, and confirmed account deletion.

Raw Token resources, password hashes, confirmation internals, generic authentication actions, and
generic destroy actions are not exposed. The constrained administrator-delete action is available
only through the User surface. Ash policies independently enforce the same restrictions.

## API authentication

Every route below `/api/v1` requires an AshAuthentication API key:

```http
Authorization: Bearer tm_<secret>
```

The API-key plug reads only the Authorization header. Query-parameter keys are forbidden. Browser
session cookies do not authenticate API requests and API-key authentication does not create a
browser session.

The one-time plaintext credential has the form `tm_<long URL-safe random secret>`. Recognition and
validation use the complete token shape rather than the short prefix alone. The plaintext value is
returned only in the successful creation result and is never persisted or recoverable.

The persisted `ApiKey` record has:

- a UUID identifier;
- an owning user;
- a user-provided display name;
- a sensitive hash of the complete generated credential, with no plaintext credential column;
- creation and expiration times;
- `revoked_at`, which makes it unusable immediately while retaining safe administrative history.

Expiration is mandatory. The UI defaults to 365 days and permits shorter durations; it never allows
a value beyond one year. Initial API-key use does not update `last_used_at`; avoiding a database
write on every API request is preferred to imprecise usage metadata in this increment.

Missing, malformed, expired, revoked, pending-user, or disabled-user credentials return:

```json
{
  "error": {
    "code": "unauthorized",
    "message": "Authentication required"
  }
}
```

with HTTP `401`. A valid identity that lacks permission for an Accounts operation returns HTTP `403`
with code `forbidden`; the current Project/List/Task API has no per-user domain permissions. No
authentication failure redirects an API request or renders HTML.

Existing successful resource envelopes, validation errors, concurrency conflicts, and operation
semantics remain unchanged after authentication succeeds.

## CLI authentication and configuration

### Configuration path and format

The CLI reads:

```text
${XDG_CONFIG_HOME:-$HOME/.config}/taskman/config.json
```

with this initial schema:

```json
{
  "api_url": "https://taskman.example.com",
  "api_key": "tm_..."
}
```

JSON uses the existing Jason dependency. YAML is rejected because two scalar settings do not
justify another parser or YAML's larger syntax surface. Multiple named server profiles are deferred.

On Unix, the CLI creates the directory without group/world write access and the file with mode
`0600`. Writes stage a sibling file, set its permissions, sync/close it, and atomically rename it
over the target. An existing group/world-readable file is rejected with remediation guidance rather
than used silently. Malformed JSON, unknown top-level shape, wrong field types, and unreadable files
fail visibly.

Configuration and filesystem code accepts an injected environment and configuration root so tests
never read or mutate the developer's actual home directory.

### Commands

The registry adds:

```text
taskman config set-url URL
taskman config set-key
taskman config show
```

`set-key` prompts without echo and never accepts the key as a command argument. `show` displays the
resolved API URL and whether a key is configured but always redacts the key.

There is no `--api-key` option. The API key may be provided through `TASKMAN_API_KEY` for CI,
containers, secret injection, or temporary overrides.

Resolution precedence is:

```text
--api-url
TASKMAN_API_URL / TASKMAN_API_KEY
config.json
http://localhost:4000 default URL
```

The CLI checks for a resolved key before an ordinary API command and returns a local
`authentication_required` error when none exists. It sends the key through Req's Authorization
header and never includes it in a URL, diagnostic, inspected request, or output.

### CLI errors and parity

The CLI compatibility table adds exit status `7` for missing/rejected authentication and forbidden
operations. Invalid local configuration syntax or permissions remains invalid invocation status
`2`. Server `401`/`403` error envelopes are preserved on stderr in JSON mode and rendered without
the key in readable mode.

Help, onboarding, Bash/Fish completions, end-to-end tests, and the bundled `taskman-cli` skill are
updated with the config commands, XDG path, environment variables, bearer requirement, key creation
workflow, and remote HTTPS example. Config commands do not require a running backend except for any
future explicit verification feature.

This authentication change invokes the existing CLI/skill parity contract. The CLI version advances
from `0.1.0` to `0.2.0`. The canonical skill source, compile-time bundle, safely installed copy,
ownership marker, registry command coverage, and executable examples must all describe the same
version and behavior. Re-running `taskman agent skill install` updates an installer-owned older or
content-different skill atomically; it never leaves an authenticated CLI paired with stale
credential guidance.

Account settings and administrator lifecycle actions are intentionally browser/AshAdmin workflows
in this access-gate increment. They are not added as account-management JSON API or CLI commands.
The existing Project/List/Task API and CLI retain operational parity once authenticated. The bundled
skill explains how an agent obtains a user-created API key, configures it without exposing it, and
handles missing, rejected, or forbidden authentication.

## Security behavior

- Passwords use `AshAuthentication.Argon2Provider`.
- Password length is 8–128 characters. The implementation does not add arbitrary composition rules.
- Password, password confirmation, token, secret, and hash fields are sensitive and redacted.
- Authentication, setup, confirmation, and recovery failures do not enumerate registered email
  addresses.
- Browser cookies are secure in production, HTTP-only, and SameSite Lax.
- Stored browser sessions expire after 30 days.
- API keys expire no later than 365 days.
- Production forces HTTPS and HSTS and honors forwarded scheme information only from the intended
  reverse-proxy topology.
- Phoenix CSRF protection remains active for browser and LiveView forms.
- Secrets are read in `runtime.exs`; none are compiled into the release or committed.
- Signing secrets for Phoenix sessions and AshAuthentication tokens are distinct.
- Authentication and administration logs never contain passwords, setup/reset/confirmation tokens,
  session tokens, API keys, or password hashes.

### Rate limiting

AshRateLimiter with Hammer's ETS backend protects the initial single-node authentication actions.
The HTTP boundary supplies normalized remote-address context, and action keys also use a normalized
identity where applicable.

Initial limits are conservative and may be refined from observed operation:

- sign-in: 10 attempts per 15 minutes per normalized email and 60 per 15 minutes per remote address;
- password-reset request: 5 per hour per normalized email and 20 per hour per remote address;
- invitation resend and email-change resend: 5 per hour per target address and acting
  administrator/user; and
- invalid API-key authentication: 60 per minute per remote address.

Exceeding a limit returns HTTP `429` with retry guidance. Generic public responses continue to avoid
account enumeration. ETS state is node-local and resets on restart, which is accepted for the
initial single-server deployment. A clustered deployment must select and design a shared backend
before adding nodes.

Correct client-address handling behind Caddy is part of the deployment smoke test. The production
endpoint rewrites forwarded scheme/address information only when the immediate peer is the
configured loopback proxy; it does not trust arbitrary public forwarded headers. Binding Phoenix to
a non-loopback address requires an explicit later trusted-proxy configuration.

### Security event logging

Structured logs record successful and failed sign-ins, setup completion, password change/reset,
confirmed email change, administrative email management, session revocation, account enable/disable,
administrator changes, and API-key creation/revocation. Successful and rejected account deletion
attempts are also logged before the target record disappears. Logs identify actors and target
record IDs where authenticated, but omit email values, secrets, and other unnecessary personal
data. Persistent audit storage and an audit UI are deferred.

## Transactional email

`Taskman.Mailer` remains the email boundary. Templates provide both HTML and plain-text bodies for:

- user setup invitation;
- email-change confirmation; and
- password reset.

Development continues to use `Swoosh.Adapters.Local`. Tests use the Swoosh test or sandbox adapter.
Production uses `Swoosh.Adapters.Resend` with `Swoosh.ApiClient.Req`.

Production runtime requires:

- `RESEND_API_KEY`;
- `MAIL_FROM`, containing a sender at a verified domain; and
- the correct public `PHX_HOST`/URL so generated links use HTTPS.

Production startup fails clearly when required auth or mail configuration is absent. Deployment
documentation explains Resend account creation, sending-domain DNS verification, API-key creation,
and an end-to-end delivery smoke test. Email composition does not use provider-specific templates,
so a later adapter change does not alter Accounts actions.

## OTP release and server deployment

### Release artifact

The repository adds Phoenix's conventional release support:

```sh
MIX_ENV=prod mix assets.deploy
MIX_ENV=prod mix release
```

The release contains:

- `bin/server`, which starts the Phoenix endpoint;
- `bin/migrate`, which runs all Ecto/AshPostgres migrations without Mix;
- `bin/create-admin`, which invokes the interactive Accounts bootstrap; and
- the application release task module used by those scripts.

The artifact is built on the same operating-system family and architecture as the target host. It
contains the Erlang runtime and does not require source code, Mix, or a development toolchain on the
runtime host.

### Runtime secrets

At minimum production requires:

- `DATABASE_URL`;
- `SECRET_KEY_BASE`;
- a distinct AshAuthentication token-signing secret;
- `PHX_HOST`;
- `RESEND_API_KEY`;
- `MAIL_FROM`; and
- optional pool, port, DNS-cluster, and trusted-proxy settings already supported or introduced by
  deployment configuration.

The systemd environment file is owned by `root:root`, mode `0600`, read by systemd, and never
committed. Runtime configuration gives each missing value a specific startup error.

### systemd

The example unit runs as a dedicated unprivileged `taskman` user and group. It:

- loads the protected environment file;
- uses a versioned release directory selected through a controlled `current` symlink;
- keeps Erlang distribution available only on a fixed loopback port without EPMD for break-glass
  local `remote` or `rpc` inspection;
- runs `bin/migrate` as `ExecStartPre`;
- runs `bin/server` in the foreground as `ExecStart`;
- sends logs to journald;
- restarts unexpected failures with a bounded delay;
- stops through SIGTERM with sufficient shutdown time; and
- applies compatible systemd hardening without denying required release, temporary, certificate,
  or database-network access.

A migration failure prevents the new release from starting. Database migrations must remain
backward compatible with the immediately previous release when rollback is expected; otherwise the
deployment runbook must state that application rollback requires a database restore or explicit
migration rollback.

The release launcher is an operating-system trust boundary, not an application authorization
boundary. `eval`, `rpc`, and `remote` permit arbitrary code execution under the release identity or
inside the running VM. Root, the dedicated service account, and any principal able to read the
release cookie and execute the launcher are therefore fully trusted. Human operators are not added
to the service group or granted generic sudo access to the launcher. Release archives and cookies
are handled as deployment credentials. Distribution remains available for production diagnosis
because `iex -S mix` would start a separate instance and is not supported on the release host; its
node name, listener, and fixed EPMD-less port are constrained to IPv4 loopback.

### Caddy

The initial reverse-proxy example uses Caddy:

```text
taskman.example.com {
  reverse_proxy 127.0.0.1:4000
}
```

Caddy owns public ports 80/443 and certificate issuance. Phoenix remains on loopback, so the
application port is not directly Internet-accessible. The deployment guide covers DNS, firewall
rules, HTTPS/HSTS verification, forwarded scheme/client address behavior, LiveView WebSocket
connectivity, and request-size/time-out considerations.

PostgreSQL remains local or private and is never exposed publicly.

### Deployment boundary

This workstream supplies build/runtime artifacts and a manual deployment runbook. It does not log
into a server, change DNS, create a Resend account, publish a release, or deploy an instance. Those
external actions require separate operator authorization.

## Error handling

- Public browser auth failures show generic, accessible form errors.
- Expired, invalid, used, or revoked links offer an appropriate safe retry path without exposing
  account existence.
- Authenticated policy failures render a safe forbidden state or redirect; they do not disclose
  policy internals in production.
- A wrong self-deletion password, stale target, or last-active-administrator conflict leaves the
  account and all credentials intact and shows a specific safe error.
- API authentication errors always use JSON and never redirect.
- Email delivery failure preserves a recoverable pending invitation/email change and logs the
  operational cause without exposing secrets.
- A configuration, migration, or missing-secret failure prevents unsafe production startup.
- Ash exceptions are translated at the web boundary; ordinary Project/List/Task API envelopes do
  not leak Ash internals.

## Expected implementation boundaries

The exact generated filenames may vary with current package generators, but responsibilities remain:

- `mix.exs`, `.formatter.exs`, and `mix.lock`: Ash ecosystem, Argon2, AshAdmin, rate limiter, and
  release dependencies/configuration.
- `lib/taskman/repo.ex`: adopt `AshPostgres.Repo` while remaining the existing Ecto Repo.
- `config/*.exs`: Ash domains, repo extensions/snapshots, auth, rate limiter, mail, endpoint, and
  runtime secrets.
- `lib/taskman/accounts.ex`: Accounts domain and explicit code interface.
- `lib/taskman/accounts/`: User, Token, ApiKey, senders, policy checks, and narrowly focused account
  lifecycle actions, including explicit administrator email management and self-service and
  administrator deletion.
- `lib/taskman/application.ex`: AshAuthentication and rate-limiter supervision.
- `lib/taskman_web/router.ex`: public auth, protected LiveView, admin, and API-key boundaries.
- `lib/taskman_web/auth_controller.ex` and auth helpers: session creation/removal and safe callbacks,
  including an HTTP response boundary that can replace the acting session cookie after an
  authenticated password change.
- `lib/taskman_web/live_user_auth.ex`: LiveView actor assignment, access requirements, and socket
  revocation identity.
- focused account/auth LiveViews and HEEx: account settings and setup/recovery customization.
- layouts/navigation: authenticated account menu only.
- `lib/taskman/cli/`: config file abstraction, commands, registry/help/onboarding, request headers,
  error mapping, and bundled skill content.
- `lib/taskman/release.ex` and `rel/overlays/bin/`: migrations, bootstrap, and release server
  commands.
- `priv/repo/migrations/` and Ash snapshots: Accounts persistence and required extensions.
- `ops/systemd/taskman.service` and `ops/caddy/Caddyfile`: production service and reverse-proxy
  examples.
- `test/`: focused Accounts, policy, web, API, CLI, mail, migration, release-task, and compatibility
  coverage.
- `README.md` and `docs/`: local/hosted operation, auth, CLI configuration, product scope, deployment,
  and verification guidance.

Web modules continue to use public Accounts or existing domain context interfaces. They do not call
Repo directly or construct Ecto/Ash queries outside their owning boundary.

## Testing and verification

### Repo compatibility

After adding AshPostgres and changing `Taskman.Repo`, run the unchanged complete test suite before
adding Accounts behavior. Existing Ecto schema operations, transactions, SQL sandbox tests,
optimistic locking, PubSub change notifications, LiveViews, controllers, migrations, and CLI
end-to-end tests must pass.

### Accounts and policies

Focused tests cover:

- bootstrap creation and duplicate-email failure;
- invitation creation, seven-day expiry, single use, resend invalidation, revocation, and delivery
  failure;
- password length, confirmation, hashing, sign-in, generic failure, change, and reset;
- pending, active, and disabled account behavior;
- email-change confirmation, expiry, conflict, resend, and unchanged old identity before success;
- administrator email uniqueness, self-target rejection, changed/unchanged confirmation choices,
  immediate confirmed replacement, and active/disabled pending-confirmation behavior;
- pending-user email replacement, old setup-token invalidation, immediate fresh setup delivery,
  setup-time versus immediate confirmation, delivery failure, and resend recovery;
- administrator authorization and transactional last-admin protection;
- self-service versus administrator action boundaries;
- self-deletion password validation and confirmation, administrator destructive confirmation,
  dependent credential cleanup, shared-workspace preservation, and the absence of a tombstone;
- concurrent final-admin delete/disable/demotion protection;
- session creation, expiry, individual/all-other revocation, authenticated password-change
  rotation with acting-session preservation, recovery-reset revocation of all sessions, and
  disabled-user revocation;
- API-key creation, `tm_` prefix and full token shape, one-time plaintext, absence of plaintext
  persistence, hashing of the complete credential, maximum/default expiry, shorter expiry,
  revocation, and disabled-user behavior; and
- sensitive field redaction.

### Browser and LiveView

LiveView tests use stable DOM IDs to verify:

- unauthenticated redirects and safe return paths;
- sign-in/out and generic failures;
- invitation, confirmation, and password-reset states;
- account settings outcomes;
- administrator email management for pending, active, and disabled accounts;
- API-key one-time display and subsequent redaction;
- session management;
- authenticated password change preserving the acting session while disconnecting other sessions;
- recovery password reset disconnecting every existing session;
- self-deletion confirmation, failure, successful sign-out, and active-socket disconnection;
- admin navigation visibility and direct-route enforcement;
- administrator deletion confirmation and AshAdmin's action allowlist and policy enforcement; and
- active socket disconnection after session/account revocation.

Tests assert outcomes and meaningful structure, not styling classes or raw application HTML.

### API

Controller tests prove:

- missing, malformed, query-parameter, expired, revoked, pending-user, and disabled-user keys return
  the stable `401` envelope;
- browser cookies alone cannot authenticate;
- a valid key authenticates existing representative reads and mutations;
- authenticated Project/List/Task success and error contracts remain unchanged; and
- policy-denied Accounts operations, if exposed through an authenticated HTTP boundary later, map
  to `403`.

### CLI

Parser, registry, help, config, client, onboarding, completions, bundled-skill, skill-installer, and
end-to-end tests cover:

- XDG/default config resolution;
- precedence across explicit URL, environment, file, and default;
- secure creation, atomic replacement, permission refusal, malformed JSON, and injected test roots;
- non-echoing key input through an IO abstraction;
- redaction in display and diagnostics;
- Authorization header insertion without URL/log leakage;
- missing and rejected authentication exit status `7`;
- config command help and completions; and
- CLI/Mix version agreement, registry coverage and parseable examples in the bundled skill, exact
  source/bundle/installed-content parity, matching bundle/ownership-marker versions, and atomic
  upgrade of an installer-owned stale skill; and
- a real HTTP request against a controlled authenticated endpoint.

### Email

Swoosh test/sandbox assertions cover recipient, subject, safe HTTPS link, text and HTML bodies,
expiry guidance, non-disclosure, resend invalidation, and recoverable delivery failure. No test
sends live email.

### Release and deployment

Verification includes:

1. production compilation with warnings as errors;
2. production asset generation;
3. OTP release assembly;
4. migration execution against a controlled database;
5. interactive bootstrap behavior through an injected IO test and release-task function;
6. release startup with required runtime configuration;
7. `systemd-analyze verify` and `caddy validate` when those tools are available;
8. a release smoke test for browser sign-in, LiveView connection, email construction, and an
   authenticated API request; and
9. a documentation dry run of build, migrate, bootstrap, start, rollback, and secret requirements.

Run focused checks throughout and finish with `mix precommit`.

## Documentation changes

- This specification is indexed from `docs/README.md`.
- `docs/product/mvp-spec.md` changes from local-only/unauthenticated to an authenticated,
  hostable shared workspace while retaining the exclusion of ownership and collaboration.
- `docs/development.md` permits this accepted auth/release boundary while preserving just-in-time
  architecture and the prohibition on speculative multi-user work.
- `docs/planning/roadmap.md` records authenticated hosted access as the next priority insertion
  before continuing Task relationship increments.
- `README.md`, CLI help/onboarding, the bundled skill, and a deployment guide are updated during
  implementation when the described behavior exists.

## Rejected alternatives and trade-offs

### Private network only

Tailscale or WireGuard would avoid application auth but would require client software and would not
serve arbitrary browsers/API clients. Rejected for the access requirement.

### Reverse-proxy Basic Auth

Basic Auth could protect every route quickly but gives poor browser login/logout, shared
credentials, weak account lifecycle, and awkward CLI secret handling. Rejected in favor of
application-native users and API keys.

### External identity-aware proxy

An external proxy can provide SSO/MFA without application account code, but API service credentials,
provider dependency, and the desire to adopt Ash make it a worse initial fit. Future OAuth/OIDC may
still delegate identity to an external provider through AshAuthentication.

### Phoenix `phx.gen.auth`

Phoenix's generator is mature and supports LiveView sessions and an official API-token extension.
AshAuthentication was selected because it can be isolated now, has first-class API-key and future
strategy support, integrates with AshAdmin/policies, and provides experience toward the intended
later full-domain Ash migration.

### Hand-built shared-password auth

Rejected because it creates security-sensitive session, hashing, recovery, and token lifecycle code
without the benefits of either the Phoenix generator or AshAuthentication.

### Immediate or opportunistic domain migration

Rejected for this workstream. Authentication must not expand into rewriting Project/List/Task
behavior, and new features must not create a permanent hybrid architecture. Full migration is a
later, separately designed workstream.

### Public registration

Rejected. Accounts are created by a server-local bootstrap or administrator invitation.

### Soft deletion or a restoration grace period

Rejected for the initial account lifecycle. It would retain credentials or personal data, require
restoration semantics, and complicate authentication checks. Account deletion is immediate and
permanent in the live database.

### Database-cascade-only account deletion

Rejected as the application boundary. Foreign-key cascades enforce dependent-record cleanup, but
explicit Ash destroy actions are still required for authorization, last-active-administrator
protection, destructive confirmation, socket disconnection, and security logging.

### Anonymized account tombstones

Rejected because current Project/List/Task data has no user ownership or attribution to preserve.
Opaque security-log record IDs remain sufficient for the bounded operational logging requirement.

### Administrator-assigned temporary passwords

Rejected in favor of one-time setup links, which avoid transmitting reusable passwords and verify
the invited address.

### YAML CLI configuration

Rejected because JSON uses existing Jason support and the CLI manages the file. Profiles and richer
human-authored configuration are not needed.

### Production `mix phx.server`

Technically viable but rejected as the recommended deployment. It retains source, Mix, build tools,
and dependencies on the host and gives weaker artifact/rollback boundaries than an OTP release.

### Container-first deployment

Rejected for the initial single dedicated server. A release under systemd provides the needed
runtime boundary without image building, a registry, volumes, and container networking. Containers
remain possible later.

### SMTP-first mail

Rejected because Resend through Swoosh and Req requires less host/mail transport configuration.
Swoosh keeps the provider replaceable.

## External evidence

The following current primary documentation informed the design:

- [Ash can be added to an existing project](https://ash.hexdocs.pm/get-started.html)
- [AshAuthentication Accounts resources and strategies](https://ash-authentication.hexdocs.pm/get-started.html)
- [AshAuthenticationPhoenix router and session
  integration](https://ash-authentication-phoenix.hexdocs.pm/get-started.html)
- [AshAuthentication LiveView sessions and
  `on_mount`](https://ash-authentication-phoenix.hexdocs.pm/liveview.html)
- [AshAuthentication API keys](https://ash-authentication.hexdocs.pm/api-keys.html)
- [AshPostgres Repo remains an Ecto Repo](https://ash-postgres.hexdocs.pm/AshPostgres.Repo.html)
- [AshAdmin action allowlisting](https://ash-admin.hexdocs.pm/dsl-ashadmin-resource.html)
- [Ash policies and actors](https://ash.hexdocs.pm/policies.html)
- [AshRateLimiter single-resource action limits](https://ash-rate-limiter.hexdocs.pm/)
- [Swoosh adapters and Req client](https://swoosh.hexdocs.pm/Swoosh.html)
- [Resend sending-domain
  setup](https://resend.com/docs/knowledge-base/how-do-I-create-an-email-address-or-sender-in-resend)
- [Phoenix release generation](https://phoenix.hexdocs.pm/Mix.Tasks.Phx.Gen.Release.html)
- [Phoenix release deployment](https://phoenix.hexdocs.pm/releases.html)
- [Mix release operation under systemd and one-off commands](https://mix.hexdocs.pm/Mix.Tasks.Release.html)

## Known caveats

- Generator output must be inspected rather than accepted wholesale. Public registration and
  unrelated generated actions/routes must be removed or constrained.
- Switching the Repo macro is expected to preserve Ecto behavior, but Taskman's complete suite is
  the authority for compatibility.
- AshAdmin is safe only with both a narrow action/resource allowlist and Ash policy enforcement.
- Connected LiveViews require explicit socket disconnection in addition to token revocation.
- Permanent account deletion has no application-level recovery. Backups and third-party mail
  delivery metadata are governed by their separate retention policies.
- Rate limiting depends on correct client-address handling behind the reverse proxy. ETS is not a
  clustered backend.
- The release rollback story is bounded by database migration compatibility and must be documented
  per release.
- Resend account creation, DNS verification, host provisioning, DNS changes, and actual deployment
  are external operator actions and are not authorized by this specification.
- Adding magic link or OAuth later may require an identity resource and provider-specific account
  linking decisions; this design only preserves the seam.

## Next-session checklist

1. Resume implementation in a fresh session with `$resume authenticated-hosted-access`, defaulting
   to subagent-driven development unless the operator explicitly chooses another approach.
2. Read this specification, the approved implementation plan, and Beads feature
   `tas-authenticated-hosted-access-2a8`.
3. Begin with `tas-authenticated-hosted-access-2a8.1`, the isolated AshPostgres Repo compatibility
   gate, before building authentication behavior.
