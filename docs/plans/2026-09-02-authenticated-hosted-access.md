# Authenticated Hosted Access Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement
> this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Gate Taskman's shared browser workspace and JSON API behind AshAuthentication, add
complete user and administrator account lifecycle management, authenticate the CLI with persistent
API keys, and package the application as an operable OTP release for a dedicated HTTPS host.

**Architecture:** `Taskman.Accounts` is the only Ash domain in this workstream and owns Users,
authentication Tokens, API keys, policies, lifecycle actions, and transactional mail. Existing
Project/List/Task Ecto contexts remain unchanged behind browser-session and API-key boundaries.
Phoenix runs as an OTP release on loopback behind Caddy, with systemd supervising the release.

**Tech Stack:** Elixir 1.17+, Phoenix 1.8.9, LiveView 1.2, Ecto/PostgreSQL, Ash 3.32,
AshPostgres 2.13, AshPhoenix 2.3, AshAuthentication 4.14,
AshAuthenticationPhoenix 2.17, AshAdmin 1.3, AshRateLimiter 2.0, Hammer 7.4 ETS,
Argon2 Elixir 4.1, Swoosh/Resend, Req, systemd, Caddy, ExUnit, LazyHTML.

**Spec:** `docs/specs/2026-09-02-authenticated-hosted-access-design.md`

**Status:** Approved

**Delivery tracking:** Feature `tas-authenticated-hosted-access-2a8`

| Task | Beads issue |
| --- | --- |
| 1 | `tas-authenticated-hosted-access-2a8.1` |
| 2 | `tas-authenticated-hosted-access-2a8.2` |
| 3 | `tas-authenticated-hosted-access-2a8.3` |
| 4 | `tas-authenticated-hosted-access-2a8.4` |
| 5 | `tas-authenticated-hosted-access-2a8.5` |
| 6 | `tas-authenticated-hosted-access-2a8.6` |
| 7 | `tas-authenticated-hosted-access-2a8.7` |
| 8 | `tas-authenticated-hosted-access-2a8.8` |
| 9 | `tas-authenticated-hosted-access-2a8.9` |
| 10 | `tas-authenticated-hosted-access-2a8.10` |
| 11 | `tas-authenticated-hosted-access-2a8.11` |
| 12 | `tas-authenticated-hosted-access-2a8.12` |
| 13 | `tas-authenticated-hosted-access-2a8.13` |

## Global Constraints

- Read the complete approved specification, `AGENTS.md`, and `docs/development.md` before starting
  any task.
- Use stable package lines only: `ash ~> 3.32`, `ash_postgres ~> 2.13`,
  `ash_phoenix ~> 2.3`, `ash_authentication ~> 4.14`,
  `ash_authentication_phoenix ~> 2.17`, `ash_admin ~> 1.3`,
  `ash_rate_limiter ~> 2.0`, `hammer ~> 7.4`, and `argon2_elixir ~> 4.1`.
- Do not adopt AshAuthentication 5 or AshAuthenticationPhoenix 3 release candidates.
- Keep Ash isolated to `Taskman.Accounts`; do not create Ash Project, List, or Task resources or
  change their public context interfaces.
- Configure Accounts authorization by default. Web code supplies an actor and never uses
  `authorize?: false`; only named authentication/bootstrap internals may use narrow bypass policies.
- Public self-registration must not exist in actions, routes, links, or generated UI.
- Passwords are 8–128 characters and use `AshAuthentication.Argon2Provider`; do not add composition
  rules.
- Setup invitations last seven days, email confirmations 24 hours, password resets one hour,
  browser sessions 30 days, and API keys at most 365 days.
- The plaintext API-key format is `tm_<long URL-safe random secret>`. Persist only a sensitive hash
  of the complete credential; show plaintext once.
- Browser cookies never authenticate `/api/v1`; API keys never create browser sessions.
- Preserve all successful Project/List/Task JSON envelopes and domain error contracts after
  authentication succeeds.
- Use Req for HTTP. Do not add HTTPoison, Tesla, or `:httpc`.
- Preserve the shared workspace: no user ownership, filtering, attribution, or domain permissions.
- Keep password/token/hash fields sensitive and never log credentials or raw email values.
- Use stable DOM IDs and selector-based LiveView tests; do not assert styling details.
- Use `start_supervised!/1`, process monitors, or `_ = :sys.get_state/1`; do not sleep in tests.
- Generate Ash migrations and resource snapshots with `mix ash.codegen`; inspect generated files
  before accepting them. Existing Ecto migrations remain authoritative for existing tables.
- Use `but` for version-control mutations. Each task ends with focused tests and an independently
  reviewable commit; do not push, merge, publish, or deploy without separate authorization.
- Each implementation task receives independent verification from an agent that did not implement
  it.

---

## File and Interface Map

### Ash and persistence foundation

- `mix.exs`, `mix.lock`, `.formatter.exs` own stable dependencies and Ash DSL formatting.
- `lib/taskman/repo.ex` uses `AshPostgres.Repo` while remaining the existing Ecto Repo.
- `config/config.exs`, environment configs, and `config/runtime.exs` own domains, authentication,
  mail, rate limiting, endpoint security, and runtime secrets.
- `lib/taskman/accounts.ex` is the authorized Ash domain and public account interface.
- `lib/taskman/accounts/user.ex`, `token.ex`, and `api_key.ex` own persisted authentication data.
- `lib/taskman/accounts/user/status.ex` defines `:pending`, `:active`, and `:disabled`.
- `priv/repo/migrations/*_add_accounts_authentication.exs` and
  `priv/repo/resource_snapshots/` describe only Accounts tables and extensions.

Public account results use:

```elixir
@type actor :: Taskman.Accounts.User.t()
@type account_error ::
        Ash.Error.t()
        | :authentication_required
        | :forbidden
        | :last_active_admin
        | :stale_target
        | :delivery_failed

@spec bootstrap_admin(map()) :: {:ok, User.t()} | {:error, term()}
@spec invite_user(actor(), map()) :: {:ok, User.t()} | {:error, account_error()}
@spec manage_email(actor(), User.t(), String.t(), boolean()) ::
        {:ok, User.t()} | {:error, account_error()}
@spec disable_user(actor(), User.t()) :: {:ok, User.t()} | {:error, account_error()}
@spec delete_user(actor(), User.t()) :: :ok | {:error, account_error()}
```

### Browser authentication and settings

- `lib/taskman_web/auth_controller.ex` owns AshAuthentication callbacks, sign-out, and the HTTP
  response that rotates the acting session after a password change.
- `lib/taskman_web/live_user_auth.ex` assigns `current_user` and `current_scope`, enforces active
  confirmed users/admins, and registers session-specific socket identities.
- `lib/taskman_web/live/account_settings_live.ex` and its HEEx template own email, password,
  session, API-key, and self-deletion settings.
- `lib/taskman_web/components/account_components.ex` owns reusable auth/settings presentation.
- `lib/taskman_web/auth_overrides.ex` styles generated sign-in/setup/recovery components.
- `lib/taskman_web/router.ex` separates public auth, authenticated workspace/settings, admin, and
  API-key routes.
- `lib/taskman_web/components/layouts.ex` and the Project LiveView template receive
  `current_scope={@current_scope}`; in this access-gate design `current_scope` is the authenticated
  User actor and does not scope Project/List/Task queries.

Session operations are:

```elixir
@spec list_sessions(actor()) :: {:ok, [map()]} | {:error, term()}
@spec revoke_session(actor(), String.t()) :: :ok | {:error, term()}
@spec revoke_other_sessions(actor(), String.t()) :: :ok | {:error, term()}
@spec change_password(actor(), String.t(), map()) ::
        {:ok, %{user: User.t(), replacement_session: String.t()}} | {:error, term()}
```

### API authentication

- `lib/taskman_web/plugs/api_authentication.ex` accepts only `Authorization: Bearer`, authenticates
  through the Ash API-key strategy, and emits the exact JSON `401` envelope.
- `lib/taskman/accounts/api_key.ex` owns hash-only key generation, expiry, revocation, and valid-key
  filtering.
- Existing API controllers remain adapters over existing Ecto contexts.

API-key operations are:

```elixir
@spec create_api_key(actor(), %{name: String.t(), expires_at: DateTime.t()}) ::
        {:ok, %{api_key: ApiKey.t(), plaintext: String.t()}} | {:error, term()}
@spec list_api_keys(actor()) :: {:ok, [ApiKey.t()]} | {:error, term()}
@spec revoke_api_key(actor(), Ecto.UUID.t()) :: :ok | {:error, term()}
```

### Administration and mail

- `lib/taskman/accounts/emails.ex` composes HTML/text setup, confirmation, and reset email.
- `lib/taskman/accounts/senders/` contains one AshAuthentication sender module per token purpose.
- `lib/taskman/accounts/rate_limit.ex` is the Hammer ETS backend.
- `lib/taskman/accounts/security_log.ex` emits structured secret-free security events.
- `lib/taskman_web/ash_admin_actor_plug.ex` supplies the current User actor to AshAdmin.
- AshAdmin exposes only the User resource and named lifecycle actions.

### CLI authentication

- `lib/taskman/cli/config.ex` owns XDG JSON resolution, validation, secure atomic writes, and source
  precedence.
- `lib/taskman/cli/commands/config.ex` owns `set-url`, secret-prompted `set-key`, and redacted
  `show`.
- `lib/taskman/terminal.ex` and `lib/taskman/local_terminal.ex` provide injected visible/secret
  prompting shared by CLI and bootstrap commands.
- `lib/taskman/cli/parser.ex`, `runner.ex`, `registry.ex`, `client.ex`, `help.ex`,
  `onboarding.ex`, and `completions.ex` integrate resolved credentials and status `7`.

```elixir
@type resolved_config :: %{api_url: String.t(), api_key: String.t() | nil}

@spec resolve(map(), keyword() | map()) ::
        {:ok, resolved_config()} | {:error, :invalid_configuration, String.t()}
@spec set_url(String.t(), keyword() | map()) :: :ok | {:error, term()}
@spec set_key(String.t(), keyword() | map()) :: :ok | {:error, term()}
@spec display(resolved_config()) :: %{api_url: String.t(), api_key_configured: boolean()}
```

### Release and host operation

- `lib/taskman/release.ex` provides `migrate/0` and interactive `create_admin/1`.
- `rel/overlays/bin/migrate` and `rel/overlays/bin/create-admin` invoke release functions.
- Phoenix's generated release support provides `rel/overlays/bin/server`.
- `ops/systemd/taskman.service` and `ops/caddy/Caddyfile` are deployable examples.
- `docs/deployment.md` is the build, install, migrate, bootstrap, start, verify, and rollback
  runbook.

---

### Task 1: Install the stable Ash toolchain and prove Repo compatibility

**Files:**

- Modify: `mix.exs`
- Modify: `mix.lock`
- Modify: `.formatter.exs`
- Modify: `lib/taskman/repo.ex`
- Create: `test/taskman/repo_compatibility_test.exs`

**Interfaces:**

- Consumes: the existing `Taskman.Repo`, Ecto schemas, SQL sandbox, and complete baseline suite.
- Produces: an AshPostgres Repo that remains source-compatible with every existing Ecto caller.

- [ ] **Step 1: Record the unchanged baseline**

Run:

```sh
mix precommit
```

Expected: 543 tests pass before dependency or Repo changes.

- [ ] **Step 2: Write the Repo compatibility test**

Assert ordinary Ecto insert/query/transaction/rollback behavior through `Taskman.Repo`:

```elixir
test "AshPostgres Repo preserves Ecto transactions" do
  assert {:error, :rolled_back} =
           Taskman.Repo.transaction(fn ->
             {:ok, project} =
               Taskman.Projects.create_project(%{
                 name: "Compatibility",
                 primary_directory: File.cwd!()
               })

             assert Taskman.Repo.get!(Taskman.Projects.Project, project.id)
             Taskman.Repo.rollback(:rolled_back)
           end)

  assert Taskman.Projects.list_projects() == []
end
```

- [ ] **Step 3: Add stable dependencies and formatter imports**

Add the exact stable dependency lines from Global Constraints, include Ash compilers/formatter
imports required by package documentation, and run `mix deps.get`. Do not select release
candidates.

- [ ] **Step 4: Replace only the Repo macro**

Use:

```elixir
defmodule Taskman.Repo do
  use AshPostgres.Repo, otp_app: :taskman

  def installed_extensions, do: ["uuid-ossp", "citext"]
end
```

Do not change existing context queries or migrations.

- [ ] **Step 5: Verify the compatibility gate**

Run:

```sh
mix test test/taskman/repo_compatibility_test.exs
mix precommit
```

Expected: the focused test and all existing tests pass unchanged.

- [ ] **Step 6: Commit the compatibility gate**

Use `but status --json`, then commit only this task's change IDs:

```sh
but commit -b authenticated-hosted-access -m "Adopt AshPostgres without changing Ecto behavior" <change-ids>
```

---

### Task 2: Add Accounts resources, persistence, and authorization defaults

**Files:**

- Create: `lib/taskman/accounts.ex`
- Create: `lib/taskman/accounts/user.ex`
- Create: `lib/taskman/accounts/user/status.ex`
- Create: `lib/taskman/accounts/token.ex`
- Create: `lib/taskman/accounts/api_key.ex`
- Modify: `config/config.exs`
- Modify: `config/test.exs`
- Create: `priv/repo/migrations/*_add_accounts_authentication.exs`
- Create: `priv/repo/resource_snapshots/`
- Create: `test/support/fixtures/accounts_fixtures.ex`
- Create: `test/taskman/accounts/resources_test.exs`
- Create: `test/taskman/accounts/policies_test.exs`

**Interfaces:**

- Consumes: `Taskman.Repo`, AshPostgres, AshAuthentication token/API-key extensions.
- Produces: `Taskman.Accounts`, User/Token/ApiKey resources, UUID users, case-insensitive email,
  status/admin fields, sensitive credentials, and authorize-by-default policy behavior.

- [ ] **Step 1: Write failing resource-contract tests**

Cover UUID IDs, normalized case-insensitive uniqueness, default pending/non-admin state, nullable
password only while pending, sensitive field visibility, API-key hash-only storage, and denial of
an action without an actor:

```elixir
pending = pending_user_fixture(email: "User@Example.com")
assert pending.status == :pending
refute pending.admin?
refute Map.has_key?(Map.from_struct(api_key), :plaintext_api_key)
```

- [ ] **Step 2: Define the domain and resources**

Configure:

```elixir
config :taskman, ash_domains: [Taskman.Accounts]
```

Use `Ash.Policy.Authorizer`, `AshPostgres.DataLayer`, `AshAuthentication.TokenResource`, and
AshAuthentication's API-key strategy support. Mark email public only where auth forms require it;
mark hashes and token material sensitive and non-public.

- [ ] **Step 3: Generate and inspect persistence**

Run:

```sh
mix ash.codegen add_accounts_authentication
mix ash.migrate
```

Inspect the generated migration and snapshots. It may create only Accounts tables, `citext`, and
UUID support; reject any Project/List/Task alteration.

- [ ] **Step 4: Add fixtures and policy tests**

Provide `pending_user_fixture/1`, `user_fixture/1`, `admin_fixture/1`, and
`api_key_fixture/2`. Fixtures must call public Accounts actions with the narrow test/bootstrap
authority required by that action rather than disabling authorization globally.

- [ ] **Step 5: Run focused and regression tests**

Run:

```sh
mix test test/taskman/accounts/resources_test.exs test/taskman/accounts/policies_test.exs
mix test test/taskman/projects_test.exs test/taskman/lists_test.exs test/taskman/tasks_test.exs
```

- [ ] **Step 6: Commit the Accounts foundation**

```sh
but commit -b authenticated-hosted-access -m "Add isolated Accounts resources" <change-ids>
```

---

### Task 3: Implement password authentication and local administrator bootstrap

**Files:**

- Modify: `lib/taskman/accounts/user.ex`
- Modify: `lib/taskman/accounts.ex`
- Create: `lib/taskman/terminal.ex`
- Create: `lib/taskman/local_terminal.ex`
- Create: `lib/mix/tasks/taskman.accounts.create_admin.ex`
- Create: `test/support/fake_terminal.ex`
- Create: `test/taskman/accounts/password_authentication_test.exs`
- Create: `test/taskman/accounts/bootstrap_test.exs`
- Create: `test/mix/tasks/taskman.accounts.create_admin_test.exs`

**Interfaces:**

- Produces: password sign-in, active/confirmed checks, `bootstrap_admin/1`, and a non-echoing local
  bootstrap command; no registration action or route.

- [ ] **Step 1: Write failing password and bootstrap tests**

Cover 7/8/128/129-character boundaries, confirmation mismatch, Argon2 hashing, generic wrong-login
results, pending/disabled rejection, duplicate bootstrap email, and active confirmed admin output.

- [ ] **Step 2: Add password strategy actions**

Configure `AshAuthentication.Argon2Provider` and expose only named sign-in, password-change,
password-reset, and bootstrap actions. Assert the resource exposes no public `register` action.

- [ ] **Step 3: Add injected terminal prompting**

Define:

```elixir
@callback prompt(String.t()) :: String.t()
@callback prompt_secret(String.t()) :: String.t()
```

`Taskman.LocalTerminal.prompt_secret/1` uses an Erlang/OTP password-input primitive so terminal
echo remains disabled. Tests use `Taskman.FakeTerminal`; passwords never appear in output.

- [ ] **Step 4: Implement the Mix task**

`mix taskman.accounts.create-admin` prompts for email/password/confirmation and delegates to
`Accounts.bootstrap_admin/1`. It exits non-zero with a safe error on duplicate email or invalid
input and never accepts password flags or environment variables.

- [ ] **Step 5: Verify**

```sh
mix test test/taskman/accounts/password_authentication_test.exs \
  test/taskman/accounts/bootstrap_test.exs \
  test/mix/tasks/taskman.accounts.create_admin_test.exs
```

- [ ] **Step 6: Commit**

```sh
but commit -b authenticated-hosted-access -m "Add password auth and admin bootstrap" <change-ids>
```

---

### Task 4: Deliver invitations, setup, recovery, and email-change lifecycles

**Files:**

- Modify: `lib/taskman/accounts.ex`
- Modify: `lib/taskman/accounts/user.ex`
- Modify: `lib/taskman/accounts/token.ex`
- Create: `lib/taskman/accounts/emails.ex`
- Create: `lib/taskman/accounts/senders/send_invitation.ex`
- Create: `lib/taskman/accounts/senders/send_confirmation.ex`
- Create: `lib/taskman/accounts/senders/send_password_reset.ex`
- Modify: `lib/taskman/mailer.ex`
- Modify: `config/config.exs`
- Modify: `config/test.exs`
- Create: `test/taskman/accounts/invitation_test.exs`
- Create: `test/taskman/accounts/email_change_test.exs`
- Create: `test/taskman/accounts/password_reset_test.exs`
- Create: `test/taskman/accounts/emails_test.exs`

**Interfaces:**

- Produces: seven-day single-use invitations, setup activation, 24-hour email confirmation,
  one-hour recovery, resend invalidation, and recoverable mail failures.

- [ ] **Step 1: Write failing token-lifecycle tests**

Use explicit timestamps or an injected clock. Cover exact expiry boundaries, single use, resend
rotation, invitation revocation, duplicate email conflicts, unchanged old identity before
confirmation, and generic reset-request responses for all account states.

- [ ] **Step 2: Write failing email tests**

Use Swoosh test assertions for recipient, subject, HTTPS URL, expiry guidance, and both bodies:

```elixir
assert_email_sent(fn email ->
  email.to == [{"", "invited@example.com"}] and
    email.text_body =~ "/setup/" and
    email.html_body =~ "7 days"
end)
```

No test sends live email.

- [ ] **Step 3: Implement invitation and setup actions**

Invitation creates a pending user, persists a hashed single-use setup token, sends mail, and
activates only after successful setup. Delivery failure leaves a resendable pending account.

- [ ] **Step 4: Implement self-service email confirmation and reset**

Email change validates current password and retains the old email until confirmation. Password
reset always returns a generic public result and revokes all browser sessions only after a valid
token installs the new password.

- [ ] **Step 5: Verify**

```sh
mix test test/taskman/accounts/invitation_test.exs \
  test/taskman/accounts/email_change_test.exs \
  test/taskman/accounts/password_reset_test.exs \
  test/taskman/accounts/emails_test.exs
```

- [ ] **Step 6: Commit**

```sh
but commit -b authenticated-hosted-access -m "Add account setup and recovery flows" <change-ids>
```

---

### Task 5: Gate browser routes with stored sessions and immediate revocation

**Files:**

- Create: `lib/taskman_web/auth_controller.ex`
- Create: `lib/taskman_web/live_user_auth.ex`
- Create: `lib/taskman_web/auth_overrides.ex`
- Create: `lib/taskman_web/components/account_components.ex`
- Modify: `lib/taskman_web/router.ex`
- Modify: `lib/taskman_web.ex`
- Modify: `lib/taskman_web/components/layouts.ex`
- Modify: `lib/taskman_web/live/project_live.html.heex`
- Modify: `test/support/conn_case.ex`
- Modify: `test/taskman_web/live/project_live/list_edit_test.exs`
- Modify: `test/taskman_web/live/project_live/autosave_test.exs`
- Modify: `test/taskman_web/live/project_live/external_updates_test.exs`
- Modify: `test/taskman_web/live/project_live/lists_test.exs`
- Modify: `test/taskman_web/live/project_live/move_task_test.exs`
- Modify: `test/taskman_web/live/project_live/task_table_test.exs`
- Modify: `test/taskman_web/live/project_live/task_updates_test.exs`
- Modify: `test/taskman_web/live/project_live/project_live_test.exs`
- Modify: `test/taskman_web/live/project_live/workspace_updates_test.exs`
- Modify: `test/taskman_web/live/project_live/tasks/autosave_test.exs`
- Modify: `test/taskman_web/live/project_live/tasks/hierarchy_test.exs`
- Modify: `test/taskman_web/live/project_live/tasks/move_test.exs`
- Modify: `test/taskman_web/live/project_live/tasks/parent_picker_test.exs`
- Create: `test/taskman_web/authentication_test.exs`
- Create: `test/taskman_web/live/session_revocation_test.exs`

**Interfaces:**

- Consumes: password auth and stored Token records.
- Produces: public sign-in/setup/reset/confirmation routes, authenticated workspace LiveSession,
  `current_user`, `current_scope`, safe return paths, sign-out, and socket disconnect topics.

- [ ] **Step 1: Write failing boundary tests**

Assert unauthenticated workspace redirects to `/sign-in`, registration is absent, valid users can
open every representative Project route, pending/disabled users cannot, unsafe external return
paths are discarded, and sign-out requires CSRF-protected submission.

- [ ] **Step 2: Add auth routes without registration**

Use current `AshAuthentication.Phoenix.Router` macros for auth callbacks, sign-in, sign-out, and
reset. Pass no registration path and verify `mix phx.routes` contains no registration endpoint.

- [ ] **Step 3: Add required-user/admin hooks**

`LiveUserAuth` assigns both `current_user` and `current_scope` to the authenticated User actor,
checks `active` and confirmed state server-side, and registers a session-specific socket ID.
Project/List/Task contexts remain unscoped.

- [ ] **Step 4: Add disconnect behavior**

Revoking a session, recovery-resetting a password, disabling, or deleting a user broadcasts only
to affected socket IDs. Authenticated password change excludes the acting session and replaces its
cookie through `AuthController`.

- [ ] **Step 5: Authenticate existing browser tests deliberately**

Add `log_in_user/2` to `ConnCase`, then mark existing application LiveView test modules with
authenticated setup. Keep new unauthenticated tests explicit; do not globally make every test
authenticated.

- [ ] **Step 6: Verify**

```sh
mix test test/taskman_web/authentication_test.exs \
  test/taskman_web/live/session_revocation_test.exs \
  test/taskman_web/live
```

- [ ] **Step 7: Commit**

```sh
but commit -b authenticated-hosted-access -m "Require stored browser sessions" <change-ids>
```

---

### Task 6: Add hash-only API keys and protect every API route

**Files:**

- Modify: `lib/taskman/accounts/api_key.ex`
- Modify: `lib/taskman/accounts/user.ex`
- Modify: `lib/taskman/accounts.ex`
- Create: `lib/taskman_web/plugs/api_authentication.ex`
- Modify: `lib/taskman_web/router.ex`
- Modify: `lib/taskman_web/controllers/api/fallback_controller.ex`
- Modify: `test/support/conn_case.ex`
- Modify: `test/taskman_web/controllers/api/list_controller_test.exs`
- Modify: `test/taskman_web/controllers/api/project_controller_test.exs`
- Modify: `test/taskman_web/controllers/api/task_controller_test.exs`
- Create: `test/taskman/accounts/api_key_test.exs`
- Create: `test/taskman_web/controllers/api/authentication_test.exs`

**Interfaces:**

- Produces: `tm_` credentials, one-time plaintext metadata, hash-only persistence, 1–365-day expiry,
  revocation, and exact API `401`/`403` behavior.

- [ ] **Step 1: Write failing API-key persistence tests**

Assert creation returns plaintext matching a strict `tm_` format, Repo reload exposes only
`api_key_hash`, default expiry is 365 days, shorter expiry works, longer/missing expiry fails, and
revoked/expired/disabled-owner credentials cannot authenticate.

- [ ] **Step 2: Write failing API boundary tests**

Cover missing, malformed, query-only, expired, revoked, pending-user, and disabled-user keys:

```elixir
assert %{
         "error" => %{
           "code" => "unauthorized",
           "message" => "Authentication required"
         }
       } = conn |> get("/api/v1/projects") |> json_response(401)
```

Also prove browser cookies alone return `401` and a valid key preserves existing `200`/`201`/error
envelopes.

- [ ] **Step 3: Implement key generation and policy actions**

Use `AshAuthentication.Strategy.ApiKey.GenerateApiKey` with prefix `:tm`, hash the complete
credential into a sensitive binary field, expose plaintext only from action metadata, and retain
revoked records through `revoked_at`.

- [ ] **Step 4: Add the API plug**

Read only the Authorization header, authenticate through the Ash strategy, assign the actor, and
halt with JSON. Never accept query parameters or browser sessions.

- [ ] **Step 5: Update existing API tests**

Add `put_api_key/2` and explicitly authenticate existing Project/List/Task controller tests.
Retain dedicated unauthenticated cases rather than adding a globally authenticated API conn.

- [ ] **Step 6: Verify**

```sh
mix test test/taskman/accounts/api_key_test.exs \
  test/taskman_web/controllers/api
```

- [ ] **Step 7: Commit**

```sh
but commit -b authenticated-hosted-access -m "Protect the API with expiring keys" <change-ids>
```

---

### Task 7: Implement administrative lifecycle invariants and email management

**Files:**

- Modify: `lib/taskman/accounts.ex`
- Modify: `lib/taskman/accounts/user.ex`
- Modify: `lib/taskman/accounts/token.ex`
- Modify: `lib/taskman/accounts/api_key.ex`
- Create: `lib/taskman/accounts/changes/protect_last_admin.ex`
- Create: `test/taskman/accounts/admin_lifecycle_test.exs`
- Create: `test/taskman/accounts/admin_email_management_test.exs`
- Create: `test/taskman/accounts/account_deletion_test.exs`

**Interfaces:**

- Produces: invite/resend/revoke, enable/disable, promote/demote, credential revocation,
  lifecycle-aware admin email management, admin/self deletion, and transactional final-admin
  protection.

- [ ] **Step 1: Write failing policy and concurrency tests**

Cover non-admin denial, disabled-admin denial, self-target denial for admin email/delete actions,
and concurrent attempts to demote/disable/delete the final active administrator. Use
`Task.async_stream/3` with `timeout: :infinity`, not sleeps.

- [ ] **Step 2: Write the admin email matrix**

Test all specified pending/active/disabled, changed/unchanged, confirmed/unconfirmed combinations.
For a changed pending email, assert the former setup link is invalid and a fresh seven-day setup
mail is sent immediately; never send the active email-change template.

- [ ] **Step 3: Write deletion tests**

Assert self-deletion requires the current password, admin deletion requires a different target,
dependent Tokens/API keys disappear transactionally, shared Project/List/Task rows remain, and
there is no User tombstone.

- [ ] **Step 4: Implement named lifecycle actions**

Keep generic update/destroy actions private. Lock the active-admin set inside the same database
transaction as demotion/disable/delete. Broadcast revocation only after commit succeeds.

- [ ] **Step 5: Implement lifecycle-aware email management**

Active/disabled users retain their current email until unconfirmed changes complete. Pending email
replacement persists immediately, rotates setup tokens, and sends setup mail. Delivery failure
keeps the new pending email and exposes resend.

- [ ] **Step 6: Verify**

```sh
mix test test/taskman/accounts/admin_lifecycle_test.exs \
  test/taskman/accounts/admin_email_management_test.exs \
  test/taskman/accounts/account_deletion_test.exs
```

- [ ] **Step 7: Commit**

```sh
but commit -b authenticated-hosted-access -m "Add protected account administration" <change-ids>
```

---

### Task 8: Build account settings and preserve the acting password-change session

**Files:**

- Create: `lib/taskman_web/live/account_settings_live.ex`
- Create: `lib/taskman_web/live/account_settings_live.html.heex`
- Modify: `lib/taskman_web/components/account_components.ex`
- Modify: `lib/taskman_web/components/layouts.ex`
- Modify: `lib/taskman_web/router.ex`
- Modify: `lib/taskman_web/auth_controller.ex`
- Create: `test/taskman_web/live/account_settings_live_test.exs`
- Create: `test/taskman_web/password_change_session_test.exs`

**Interfaces:**

- Produces: settings forms for email/password, stored-session revocation, API-key management,
  one-time key display, and password-confirmed permanent self-deletion.

- [ ] **Step 1: Write failing LiveView interaction tests**

Use stable IDs `#account-settings`, `#email-change-form`, `#password-change-form`, `#sessions`,
`#api-key-form`, `#api-key-plaintext`, and `#delete-account-form`. Assert outcomes through Accounts
queries, not HTML text or CSS.

- [ ] **Step 2: Write acting-session preservation tests**

Create two stored sessions. Change the password from session A, assert A receives a replacement
cookie and remains authenticated, session B and its socket disconnect, password reset revokes both,
and API keys remain valid.

- [ ] **Step 3: Implement forms through public Accounts interfaces**

Drive every HEEx form with `to_form/2` and `<.input>`. Stream sessions and API keys. Clear the
plaintext key assign after navigation/reload so it cannot be shown again.

- [ ] **Step 4: Implement self-deletion danger zone**

Require current password plus a standard destructive confirmation. On success delete auth data,
disconnect the socket, clear the cookie, and redirect to sign-in.

- [ ] **Step 5: Verify**

```sh
mix test test/taskman_web/live/account_settings_live_test.exs \
  test/taskman_web/password_change_session_test.exs
```

- [ ] **Step 6: Commit**

```sh
but commit -b authenticated-hosted-access -m "Add self-service account settings" <change-ids>
```

---

### Task 9: Mount a constrained, actor-aware AshAdmin

**Files:**

- Modify: `lib/taskman/accounts.ex`
- Modify: `lib/taskman/accounts/user.ex`
- Create: `lib/taskman_web/ash_admin_actor_plug.ex`
- Modify: `lib/taskman_web/live_user_auth.ex`
- Modify: `lib/taskman_web/router.ex`
- Modify: `config/config.exs`
- Create: `test/taskman_web/ash_admin_access_test.exs`
- Create: `test/taskman_web/ash_admin_actions_test.exs`

**Interfaces:**

- Consumes: named Task 7 actions and current admin actor.
- Produces: `/admin`, admin-only LiveSession, User-only domain exposure, and allowlisted actions.

- [ ] **Step 1: Write failing access and exposure tests**

Assert guest redirects to sign-in, normal/disabled users cannot enter, active admin can, Token and
ApiKey resources are absent, hashes/tokens are absent, and generic update/destroy/auth actions are
not selectable.

- [ ] **Step 2: Configure actor propagation**

Implement `AshAdmin.ActorPlug.actor_assigns/2` to return `[actor: current_user]` and mount:

```elixir
scope "/" do
  pipe_through :browser

  ash_admin "/admin",
    AshAuthentication.Phoenix.LiveSession.opts(
      on_mount: [{TaskmanWeb.LiveUserAuth, :admin_required}]
    )
end
```

Keep this scope unaliased as required by AshAdmin.

- [ ] **Step 3: Allowlist named actions**

Expose inspection, invitation/resend/revoke, enable/disable, promote/demote, email management,
session/API-key revocation, and confirmed deletion only. Policies must still reject forged direct
action calls.

- [ ] **Step 4: Verify action outcomes**

Exercise representative admin forms through LiveView selectors, including standard deletion
confirmation and pending-email setup resend.

- [ ] **Step 5: Verify and commit**

```sh
mix test test/taskman_web/ash_admin_access_test.exs \
  test/taskman_web/ash_admin_actions_test.exs
but commit -b authenticated-hosted-access -m "Add constrained account administration UI" <change-ids>
```

---

### Task 10: Add rate limits, security logging, and trusted-proxy handling

**Files:**

- Create: `lib/taskman/accounts/rate_limit.ex`
- Create: `lib/taskman/accounts/security_log.ex`
- Create: `lib/taskman_web/plugs/trusted_proxy.ex`
- Modify: `lib/taskman/application.ex`
- Modify: `lib/taskman/accounts/user.ex`
- Modify: `lib/taskman_web/plugs/api_authentication.ex`
- Modify: `config/config.exs`
- Modify: `config/prod.exs`
- Modify: `config/runtime.exs`
- Create: `test/taskman/accounts/rate_limit_test.exs`
- Create: `test/taskman/accounts/security_log_test.exs`
- Create: `test/taskman_web/plugs/trusted_proxy_test.exs`
- Modify: `test/taskman_web/endpoint_config_test.exs`

**Interfaces:**

- Produces: exact sign-in/reset/resend/invalid-key limits, `429` retry guidance, secret-free event
  logs, loopback-only forwarded-address trust, secure cookies, forced HTTPS, and HSTS.

- [ ] **Step 1: Write deterministic limiter tests**

Start the ETS limiter with `start_supervised!/1`, inject normalized email/IP keys, and assert the
exact limits from the spec. Synchronize with `_ = :sys.get_state(pid)` where needed.

- [ ] **Step 2: Write log-redaction tests**

Capture Logger output for successful/rejected events and assert actor/target IDs exist while
passwords, tokens, API keys, hashes, and raw email values do not.

- [ ] **Step 3: Write proxy-spoofing tests**

Assert forwarded headers rewrite `remote_ip` and scheme only when the immediate peer is loopback;
the same headers from a public peer are ignored. Reject malformed/multiple unparseable addresses.

- [ ] **Step 4: Implement limiter and security hooks**

Use `Taskman.Accounts.RateLimit` with Hammer ETS and a one-minute cleanup period. Public failures
remain enumeration-safe; API rate failures remain JSON.

- [ ] **Step 5: Harden production configuration**

Require secure/HTTP-only/SameSite Lax cookies, distinct signing secrets, loopback binding,
force-SSL/HSTS, and explicit runtime errors for missing auth/mail configuration.

- [ ] **Step 6: Verify and commit**

```sh
mix test test/taskman/accounts/rate_limit_test.exs \
  test/taskman/accounts/security_log_test.exs \
  test/taskman_web/plugs/trusted_proxy_test.exs \
  test/taskman_web/endpoint_config_test.exs
but commit -b authenticated-hosted-access -m "Harden authentication boundaries" <change-ids>
```

---

### Task 11: Add protected CLI configuration and bearer authentication

**Files:**

- Create: `lib/taskman/cli/config.ex`
- Create: `lib/taskman/cli/commands/config.ex`
- Modify: `mix.exs`
- Modify: `lib/taskman/cli.ex`
- Modify: `lib/taskman/cli/parser.ex`
- Modify: `lib/taskman/cli/runner.ex`
- Modify: `lib/taskman/cli/registry.ex`
- Modify: `lib/taskman/cli/client.ex`
- Modify: `lib/taskman/cli/help.ex`
- Modify: `lib/taskman/cli/onboarding.ex`
- Modify: `lib/taskman/cli/completions.ex`
- Modify: `lib/taskman/terminal.ex`
- Modify: `priv/taskman_cli_skill/SKILL.md`
- Create: `test/taskman/cli/config_test.exs`
- Create: `test/taskman/cli/commands/config_test.exs`
- Modify: `test/taskman/cli/client_test.exs`
- Modify: `test/taskman/cli/parser_test.exs`
- Modify: `test/taskman/cli/help_test.exs`
- Modify: `test/taskman/cli/completions_test.exs`
- Modify: `test/taskman/cli/onboarding_test.exs`
- Modify: `test/taskman/cli/end_to_end_test.exs`
- Modify: `test/taskman/cli/skill/bundle_test.exs`
- Modify: `test/taskman/cli/skill/installer_test.exs`
- Modify: `test/taskman/cli_test.exs`

**Interfaces:**

- Produces: CLI version `0.2.0`, `${XDG_CONFIG_HOME:-$HOME/.config}/taskman/config.json`,
  `config set-url`, secret `set-key`, redacted `show`, exact precedence, bearer headers, exit status
  `7`, and a version/content-matched installed agent skill.

- [ ] **Step 1: Write failing config tests**

Use a temporary injected config root and environment. Cover:

```elixir
assert {:ok, %{api_url: "https://flag.example", api_key: "tm_example_url_safe_secret"}} =
         Config.resolve(
           %{api_url: "https://flag.example"},
           env: %{
             "TASKMAN_API_URL" => "https://env.example",
             "TASKMAN_API_KEY" => "tm_example_url_safe_secret"
           },
           config_root: tmp_path
         )
```

Also cover localhost default, file fallback, malformed shape/types, unreadable or group/world
readable files, mode `0600`, directory permissions, sibling staging, atomic rename failure, and
redacted display.

- [ ] **Step 2: Add registry commands and injected secret input**

Add exact paths `config set-url`, `config set-key`, and `config show`. `set-key` takes no argument
and calls `prompt_secret`; there is no `--api-key`.

- [ ] **Step 3: Integrate resolution and request headers**

Resolve URL/key before ordinary API handlers, return local `authentication_required` status `7`
when absent, and use Req bearer auth. Extend the client contract with `401` and `403` mapped to
status `7`; never interpolate the key into errors or inspected options.

- [ ] **Step 4: Update parity surfaces**

Regenerate registry-derived completion expectations and update help, onboarding, bundled skill,
and connection guidance for remote HTTPS, config path, env overrides, key creation, secret
handling, and status `7`. Advance both `mix.exs` and `Taskman.CLI.version/0` from `0.1.0` to
`0.2.0`.

Extend bundle verification so every registry command is covered and every skill example parses
against the matching CLI. Extend installer verification to install into a temporary skill root,
compare the installed `SKILL.md` byte-for-byte with `Bundle.files()["SKILL.md"]`, verify the
ownership marker reports `Bundle.cli_version()`, then replace an installer-owned older/stale-content
copy and prove the update is atomic and exact. Do not hard-code the release version in parity tests;
the existing CLI/Mix version agreement is their single version contract.

- [ ] **Step 5: Update the real HTTP test**

Create a user/API key, start supervised Bandit, write config under a temporary root, and prove the
CLI can create/read domain data. Also prove missing/rejected keys produce status `7`.

- [ ] **Step 6: Verify and commit**

```sh
mix test test/taskman/cli
mix escript.build
but commit -b authenticated-hosted-access -m "Authenticate the CLI with protected API keys" <change-ids>
```

---

### Task 12: Configure Resend and build release-local operational commands

**Files:**

- Modify: `config/prod.exs`
- Modify: `config/runtime.exs`
- Create: `lib/taskman/release.ex`
- Create: `rel/overlays/bin/migrate`
- Create: `rel/overlays/bin/create-admin`
- Create: `rel/overlays/bin/server`
- Create: `test/taskman/release_test.exs`
- Create: `test/taskman/runtime_config_test.exs`

**Interfaces:**

- Produces: production Resend via Req, specific missing-secret failures, OTP release assembly,
  migration execution without Mix, interactive bootstrap without Phoenix endpoint startup.

- [ ] **Step 1: Write release-function tests**

Assert `Taskman.Release.migrate/0` loads the app and runs every configured repo migration, and
`create_admin/1` delegates to the same bootstrap/terminal boundary as the Mix task.

- [ ] **Step 2: Add runtime configuration tests**

Evaluate production runtime config in an isolated environment and assert missing
`DATABASE_URL`, `SECRET_KEY_BASE`, Ash token-signing secret, `PHX_HOST`, `RESEND_API_KEY`, and
`MAIL_FROM` each name the missing variable without printing any value.

- [ ] **Step 3: Configure Resend**

Use:

Place the provider configuration in `config/runtime.exs`:

```elixir
config :taskman, Taskman.Mailer,
  adapter: Swoosh.Adapters.Resend,
  api_key: System.fetch_env!("RESEND_API_KEY")
```

Keep `Swoosh.ApiClient.Req`; do not add provider templates or live-email tests.

- [ ] **Step 4: Generate and inspect release support**

Run `mix phx.gen.release`, retain project-owned overlay commands above, and ensure `bin/server`
starts the endpoint while `bin/migrate` and `bin/create-admin` do not.

- [ ] **Step 5: Assemble the production artifact**

```sh
MIX_ENV=prod mix compile --warnings-as-errors
MIX_ENV=prod mix assets.deploy
MIX_ENV=prod mix release --overwrite
```

Expected: `_build/prod/rel/taskman/bin/{server,migrate,create-admin}` exist and are executable.

- [ ] **Step 6: Verify and commit**

```sh
mix test test/taskman/release_test.exs test/taskman/runtime_config_test.exs
but commit -b authenticated-hosted-access -m "Package Taskman as an operable release" <change-ids>
```

---

### Task 13: Add systemd/Caddy operation, documentation parity, and the integrated gate

**Files:**

- Create: `ops/systemd/taskman.service`
- Create: `ops/caddy/Caddyfile`
- Create: `docs/deployment.md`
- Modify: `README.md`
- Modify: `docs/README.md`
- Modify: `docs/development.md`
- Modify: `docs/planning/roadmap.md`
- Modify: `test/taskman/cli/end_to_end_test.exs`
- Create: `test/taskman_web/authenticated_hosted_access_test.exs`

**Interfaces:**

- Consumes: every preceding deliverable.
- Produces: a dedicated-user service, loopback HTTPS proxy example, complete runbook, updated
  onboarding, and end-to-end acceptance evidence.

- [ ] **Step 1: Write the host artifacts**

The systemd unit uses `User=taskman`, `Group=taskman`, root-owned `0600` environment file,
versioned release `current` symlink, `ExecStartPre=.../bin/migrate`, foreground
`ExecStart=.../bin/server`, journald, SIGTERM, bounded restart, and compatible hardening.

The Caddyfile contains:

```caddyfile
taskman.example.com {
  reverse_proxy 127.0.0.1:4000
}
```

- [ ] **Step 2: Write the deployment runbook**

Document build-host compatibility, artifact transfer, dedicated user/directories, protected env,
database privacy, Resend domain verification, build/migrate/bootstrap/start, DNS/firewall, HTTPS,
HSTS, forwarded IP, LiveView WebSocket, API/CLI smoke tests, versioned symlink rollout, rollback,
and migration compatibility.

- [ ] **Step 3: Update product-facing documentation**

Replace README's local-only/single-user description with authenticated shared hosting while
retaining local development. Confirm the CLI help, onboarding, bundled/installed skill, config
commands, and status `7` remain consistent with those docs. Mark roadmap implementation complete
only after all acceptance checks pass.

- [ ] **Step 4: Write the integrated acceptance test**

Prove an invited user completes setup, signs into a LiveView, creates a `tm_` API key, uses it
through the CLI against supervised Bandit, loses access after disablement, and leaves existing
shared Project/List/Task behavior intact.

- [ ] **Step 5: Validate host artifacts when available**

Run:

```sh
systemd-analyze verify ops/systemd/taskman.service
caddy validate --config ops/caddy/Caddyfile
```

If either binary is unavailable, record that bounded verification gap in the handoff; do not claim
the corresponding validation passed.

- [ ] **Step 6: Run final verification**

```sh
mix ash_postgres.generate_migrations --check
mix precommit
MIX_ENV=prod mix compile --warnings-as-errors
MIX_ENV=prod mix assets.deploy
MIX_ENV=prod mix release --overwrite
```

Also search production code, tests, CLI output, and user documentation for leaked task/plan phase
terminology and remove it.

- [ ] **Step 7: Commit the integrated delivery**

```sh
but commit -b authenticated-hosted-access -m "Document and verify hosted Taskman operation" <change-ids>
```

Do not push, merge, provision infrastructure, change DNS, create external Resend resources, or
deploy without explicit operator authorization.
