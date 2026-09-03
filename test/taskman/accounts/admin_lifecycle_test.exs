defmodule Taskman.Accounts.AdminLifecycleTest do
  use Taskman.DataCase, async: false

  import Taskman.AccountsFixtures, only: [pending_user_fixture: 1, user_fixture: 1]

  alias AshAuthentication.Jwt
  alias Taskman.Accounts
  alias Taskman.Accounts.ApiKey
  alias Taskman.Repo

  test "only active administrators can administer accounts" do
    administrator = admin_fixture("administrator@example.com")
    disabled_administrator = admin_fixture("disabled-administrator@example.com")
    ordinary_user = user_fixture(email: "ordinary-user@example.com")
    pending = pending_user_fixture(email: "pending-user@example.com")

    assert {:ok, _disabled} = Accounts.disable_user(administrator, disabled_administrator)

    for actor <- [ordinary_user, disabled_administrator] do
      assert {:error, _reason} =
               Accounts.invite_user(actor, %{email: "blocked-#{actor.id}@example.com"})

      assert {:error, _reason} = Accounts.manage_email(actor, pending, "new@example.com", true)
      assert {:error, _reason} = Accounts.disable_user(actor, pending)
      assert {:error, _reason} = Accounts.delete_user(actor, pending)
    end
  end

  test "an administrator can manage another account lifecycle and credentials" do
    administrator = admin_fixture("lifecycle-administrator@example.com")
    user = user_fixture(email: "lifecycle-user@example.com")

    assert {:ok, promoted} = Accounts.promote_user(administrator, user)
    assert promoted.admin?

    assert {:ok, demoted} = Accounts.demote_user(administrator, promoted)
    refute demoted.admin?

    assert {:ok, disabled} = Accounts.disable_user(administrator, demoted)
    assert disabled.status == :disabled

    assert {:ok, enabled} = Accounts.enable_user(administrator, disabled)
    assert enabled.status == :active

    now = DateTime.utc_now()

    assert {:ok, %{api_key: api_key}} =
             Accounts.create_api_key(
               enabled,
               %{name: "Lifecycle credential", expires_at: DateTime.add(now, 86_400, :second)},
               now: now
             )

    assert :ok = Accounts.revoke_user_api_keys(administrator, enabled)
    assert %ApiKey{revoked_at: %DateTime{}} = Repo.get!(ApiKey, api_key.id)

    assert {:ok, session_token, _claims} = Jwt.token_for_user(enabled, %{}, purpose: :user)
    assert :ok = Accounts.revoke_user_sessions(administrator, enabled)
    assert {:error, :invalid_token} = Accounts.Token.valid_for_purpose?(session_token, "user")
  end

  test "administrator email management and deletion reject self-targeting" do
    administrator = admin_fixture("self-target-administrator@example.com")

    assert {:error, _reason} =
             Accounts.manage_email(administrator, administrator, "different@example.com", true)

    assert {:error, _reason} = Accounts.delete_user(administrator, administrator)
    assert %{} = Repo.get(Taskman.Accounts.User, administrator.id)
  end

  test "concurrent demote, disable, and self-delete attempts preserve the final active administrator" do
    Ecto.Adapters.SQL.Sandbox.unboxed_run(Repo, fn ->
      administrator =
        admin_fixture("final-administrator-#{System.unique_integer([:positive])}@example.com")

      try do
        results =
          concurrently([:demote, :disable, :delete], fn
            :demote -> Accounts.demote_user(administrator, administrator)
            :disable -> Accounts.disable_user(administrator, administrator)
            :delete -> Accounts.delete_own_account(administrator, "password1")
          end)

        assert Enum.all?(results, &match?({:error, _reason}, &1))

        assert %{status: :active, admin?: true} =
                 Repo.get(Taskman.Accounts.User, administrator.id)
      after
        Repo.delete_all(from user in Taskman.Accounts.User, where: user.id == ^administrator.id)
      end
    end)
  end

  defp admin_fixture(email) do
    {:ok, administrator} =
      Accounts.bootstrap_admin(%{
        email: email,
        password: "password1",
        password_confirmation: "password1"
      })

    administrator
  end

  defp concurrently(inputs, operation) do
    test_pid = self()

    coordinator =
      Task.async(fn ->
        Task.async_stream(
          inputs,
          fn input ->
            :ok = Ecto.Adapters.SQL.Sandbox.checkout(Repo, sandbox: false)

            try do
              send(test_pid, {:admin_lifecycle_attempt_ready, self()})

              receive do
                :run_attempt -> operation.(input)
              end
            after
              :ok = Ecto.Adapters.SQL.Sandbox.checkin(Repo)
            end
          end,
          max_concurrency: length(inputs),
          timeout: :infinity
        )
        |> Enum.map(fn {:ok, result} -> result end)
      end)

    workers =
      for _ <- inputs do
        assert_receive {:admin_lifecycle_attempt_ready, worker}
        worker
      end

    Enum.each(workers, &send(&1, :run_attempt))
    Task.await(coordinator, :infinity)
  end
end
