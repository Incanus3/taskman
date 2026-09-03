defmodule Taskman.Accounts.AdminEmailManagementTest do
  use Taskman.DataCase, async: false

  import Swoosh.TestAssertions

  alias AshAuthentication.Jwt
  alias Taskman.Accounts
  alias Taskman.Accounts.{Token, User}
  alias Taskman.Repo

  setup :set_swoosh_global

  test "administrative email management covers every account state and confirmation choice" do
    administrator = admin_fixture("email-administrator@example.com")

    for status <- [:pending, :active, :disabled],
        changed? <- [true, false],
        confirmed? <- [true, false] do
      old_email = "#{status}-#{changed?}-#{confirmed?}-old@example.com"
      new_email = "#{status}-#{changed?}-#{confirmed?}-new@example.com"
      {target, original_setup_token} = target_fixture(administrator, status, old_email)

      target =
        if status != :pending and not changed? and confirmed? do
          target
          |> Ecto.Changeset.change(confirmed_at: nil)
          |> Repo.update!()
        else
          target
        end

      result =
        direct_manage_email(
          administrator,
          target,
          if(changed?, do: new_email, else: old_email),
          confirmed?
        )

      case {status, changed?, confirmed?} do
        {:pending, true, false} ->
          assert {:ok, managed} = result
          assert to_string(managed.email) == new_email
          assert is_nil(managed.confirmed_at)

          assert {:error, :invalid_token} =
                   Token.valid_for_purpose?(original_setup_token, "setup")

          assert_setup_email(new_email)

        {:pending, true, true} ->
          assert {:ok, managed} = result
          assert to_string(managed.email) == new_email
          assert %DateTime{} = managed.confirmed_at

          assert {:error, :invalid_token} =
                   Token.valid_for_purpose?(original_setup_token, "setup")

          assert_setup_email(new_email)

        {:pending, false, true} ->
          assert {:ok, managed} = result
          assert to_string(managed.email) == old_email
          assert %DateTime{} = managed.confirmed_at
          assert :ok = Token.valid_for_purpose?(original_setup_token, "setup")
          refute_receive {:email, _email}

        {:pending, false, false} ->
          assert {:error, _reason} = result
          assert :ok = Token.valid_for_purpose?(original_setup_token, "setup")
          refute_receive {:email, _email}

        {state, true, true} when state in [:active, :disabled] ->
          assert {:ok, managed} = result
          assert to_string(managed.email) == new_email
          assert %DateTime{} = managed.confirmed_at
          refute_receive {:email, _email}

        {state, true, false} when state in [:active, :disabled] ->
          assert {:ok, managed} = result
          assert to_string(managed.email) == old_email
          assert_confirmation_email(new_email)

        {state, false, true} when state in [:active, :disabled] ->
          assert {:ok, managed} = result
          assert to_string(managed.email) == old_email
          assert %DateTime{} = managed.confirmed_at
          refute_receive {:email, _email}

        {state, false, false} when state in [:active, :disabled] ->
          assert {:error, _reason} = result
          refute_receive {:email, _email}
      end
    end
  end

  test "a changed pending address replaces setup state and sends only a fresh seven-day setup invitation" do
    administrator = admin_fixture("pending-email-administrator@example.com")

    assert {:ok, pending} =
             Accounts.invite_user(administrator, %{email: "pending-before-change@example.com"})

    original_setup_token = receive_token("setup")

    assert {:ok, managed} =
             Accounts.manage_email(
               administrator,
               pending,
               "pending-after-change@example.com",
               false
             )

    assert to_string(managed.email) == "pending-after-change@example.com"
    assert {:error, :invalid_token} = Token.valid_for_purpose?(original_setup_token, "setup")

    assert_receive {:email, email}
    assert email.to == [{"", "pending-after-change@example.com"}]
    assert email.text_body =~ "/setup/"
    refute email.text_body =~ "/confirm-email/"

    replacement_token = token_from_email(email, "setup")
    assert {:ok, %{"iat" => issued_at, "exp" => expires_at}} = Jwt.peek(replacement_token)
    assert expires_at - issued_at == 7 * 86_400
  end

  test "a pending replacement survives delivery failure and remains resendable" do
    administrator = admin_fixture("delivery-administrator@example.com")

    assert {:ok, pending} =
             Accounts.invite_user(administrator, %{email: "delivery-before@example.com"})

    original_setup_token = receive_token("setup")

    mailer_delivery = Application.fetch_env!(:taskman, :mailer_delivery)
    Application.put_env(:taskman, :mailer_delivery, FailingMailer)
    on_exit(fn -> Application.put_env(:taskman, :mailer_delivery, mailer_delivery) end)

    assert {:error, {:delivery_failed, managed}} =
             Accounts.manage_email(administrator, pending, "delivery-after@example.com", false)

    assert to_string(managed.email) == "delivery-after@example.com"
    assert {:error, :invalid_token} = Token.valid_for_purpose?(original_setup_token, "setup")

    Application.put_env(:taskman, :mailer_delivery, Taskman.Mailer)
    assert {:ok, resent} = Accounts.resend_invitation(administrator, managed)
    assert resent.id == pending.id
    assert_setup_email("delivery-after@example.com")
  end

  test "the single administrative email action owns every direct email-management path" do
    administrator = admin_fixture("direct-email-administrator@example.com")
    target = active_fixture("direct-email-target@example.com")

    assert %{} = Ash.Resource.Info.action(User, :manage_email)

    for obsolete_action <- [
          :manage_confirmed_email,
          :manage_unconfirmed_email,
          :replace_pending_email,
          :replace_pending_confirmed_email,
          :confirm_existing_email
        ] do
      assert is_nil(Ash.Resource.Info.action(User, obsolete_action))
    end

    assert {:ok, managed} =
             target
             |> Ash.Changeset.for_update(:manage_email, %{
               email: "direct-email-new@example.com",
               confirmed?: false
             })
             |> Ash.update(actor: administrator, authorize?: true, domain: Accounts)

    assert to_string(managed.email) == "direct-email-target@example.com"
    assert_confirmation_email("direct-email-new@example.com")

    assert {:error, _reason} =
             direct_manage_email(administrator, administrator, "forbidden@example.com", true)
  end

  test "administrative email management selects the lifecycle from the locked target state" do
    administrator = admin_fixture("locked-email-administrator@example.com")

    assert {:ok, pending} =
             Accounts.invite_user(administrator, %{email: "locked-email-pending@example.com"})

    _setup_token = receive_token("setup")

    active_target =
      pending
      |> Ecto.Changeset.change(status: :active, confirmed_at: DateTime.utc_now())
      |> Repo.update!()

    assert {:ok, managed} =
             Accounts.manage_email(
               administrator,
               pending,
               "locked-email-replacement@example.com",
               false
             )

    assert to_string(managed.email) == to_string(active_target.email)
    assert_confirmation_email("locked-email-replacement@example.com")
  end

  defp target_fixture(administrator, :pending, email) do
    assert {:ok, pending} = Accounts.invite_user(administrator, %{email: email})
    {pending, receive_token("setup")}
  end

  defp target_fixture(_administrator, :active, email), do: {active_fixture(email), nil}

  defp target_fixture(administrator, :disabled, email) do
    active = active_fixture(email)
    assert {:ok, disabled} = Accounts.disable_user(administrator, active)
    {disabled, nil}
  end

  defp active_fixture(email) do
    {:ok, user} =
      Accounts.bootstrap_admin(email, "password1")

    user
  end

  defp admin_fixture(email), do: active_fixture(email)

  defp assert_setup_email(email) do
    assert_receive {:email, message}
    assert message.to == [{"", email}]
    assert message.text_body =~ "/setup/"
    refute message.text_body =~ "/confirm-email/"
  end

  defp assert_confirmation_email(email) do
    assert_receive {:email, message}
    assert message.to == [{"", email}]
    assert message.text_body =~ "/confirm-email/"
  end

  defp receive_token(path) do
    assert_receive {:email, email}
    token_from_email(email, path)
  end

  defp token_from_email(email, path) do
    [_, token] = Regex.run(~r{https://[^\s<]+/#{path}/([^\s<]+)}, email.text_body)
    URI.decode(token)
  end

  defp direct_manage_email(administrator, target, email, confirmed?) do
    target
    |> Ash.Changeset.for_update(:manage_email, %{email: email, confirmed?: confirmed?})
    |> Ash.update(actor: administrator, authorize?: true, domain: Accounts)
  end

  defmodule FailingMailer do
    def deliver(_email), do: {:error, :unavailable}
  end
end
