# Authenticated hosted access review

- Status: active
- Updated: 2026-09-04
- Resume: `$resume authenticated-hosted-access-review`

## Objective

Resolve the final authenticated-hosted-access review findings one at a time while preserving the
accepted behavior in
[the hosted-access specification](../specs/2026-09-02-authenticated-hosted-access-design.md).

## Current checkpoint

The AshAdmin `create_pending_user` action now consumes invitation delivery results when called
directly. A failed delivery leaves the user pending, logs a sanitized warning, and keeps the form
open with guidance to open the user details and resend the invitation. Calls made through
`Accounts.invite_user/2` retain their existing structured delivery-error result.

The regression test exercises the real AshAdmin form with a failing mailer and verifies both the
guidance and recoverable pending account.

## Next action

Address the remaining review finding in `TaskmanWeb.AuthController`: rejected setup and email-change
confirmation callbacks must lead to retry or administrator-resend guidance instead of the generic
password sign-in failure path. Apply the same bounded-design approval and test-first workflow.

## Verification

- `mix test test/taskman/accounts/user/invitation_test.exs test/taskman_web/ash_admin_actions_test.exs`
  — 20 passed.
- `mix precommit` — 781 passed.
