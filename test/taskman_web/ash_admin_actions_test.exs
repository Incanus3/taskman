defmodule TaskmanWeb.AshAdminActionsTest do
  use TaskmanWeb.ConnCase, async: false

  import Phoenix.LiveViewTest
  import Swoosh.TestAssertions
  import Taskman.AccountsFixtures

  require Ash.Query

  alias AshAuthentication.Jwt
  alias Taskman.Accounts
  alias Taskman.Accounts.{ApiKey, Token, User}
  alias Taskman.Repo

  setup :set_swoosh_global

  test "the admin configuration exposes only Users and named lifecycle actions" do
    assert AshAdmin.Domain.show_resources(Accounts) == [User]
    assert AshAdmin.Resource.read_actions(User) == [:admin_read]
    assert AshAdmin.Resource.generic_actions(User) == []
    assert AshAdmin.Resource.create_actions(User) == [:create_pending_user]

    assert AshAdmin.Resource.update_actions(User) == []
    assert AshAdmin.Resource.destroy_actions(User) == []
    assert AshAdmin.Resource.show_sensitive_fields(User) == []

    assert AshAdmin.Resource.table_columns(User) == [
             :email,
             :status,
             :admin?,
             :confirmed_at,
             :inserted_at,
             :updated_at
           ]

    refute :hashed_password in AshAdmin.Resource.table_columns(User)
    refute :email_change_confirmed_at in AshAdmin.Resource.table_columns(User)
  end

  test "an administrator changes a pending email through the form and receives a fresh setup invitation",
       %{conn: conn} do
    administrator = admin_fixture()
    pending_user = pending_user_fixture()
    assert_receive {:email, _initial_invitation}

    view = admin_user_view(conn, administrator, pending_user)
    replacement_email = "replacement-pending@example.com"

    view
    |> form("#admin-user-manage-email", %{
      "email" => %{"email" => replacement_email, "confirmed?" => "false"}
    })
    |> render_submit()

    assert to_string(read_user!(administrator, pending_user).email) == replacement_email

    assert_receive {:email, email}
    assert email.to == [{"", replacement_email}]
    assert email.text_body =~ "/setup/"
    refute email.text_body =~ "/confirm-email/"
  end

  test "the administrator-delete form confirms and removes a different account", %{conn: conn} do
    administrator = admin_fixture()
    target = user_fixture()
    view = admin_user_view(conn, administrator, target)

    assert has_element?(view, "#admin-user-delete")
    assert has_element?(view, "#admin-user-delete input[name='delete[confirmation]']")

    view
    |> form("#admin-user-delete", %{"delete" => %{}})
    |> render_submit()

    assert {:ok, %User{}} = read_user(administrator, target)

    view
    |> form("#admin-user-delete", %{"delete" => %{"confirmation" => "no"}})
    |> render_submit()

    assert {:ok, %User{}} = read_user(administrator, target)

    view
    |> form("#admin-user-delete", %{"delete" => %{"confirmation" => "DELETE"}})
    |> render_submit()

    assert {:ok, nil} = read_user(administrator, target)
  end

  test "forged admin protocol events cannot execute a self-targeted delete", %{conn: conn} do
    administrator = admin_fixture()
    view = admin_user_view(conn, administrator, administrator)

    render_hook(view, "toggle_authorizing", %{})
    render_hook(view, "clear_actor", %{})

    view
    |> form("#admin-user-delete", %{"delete" => %{"confirmation" => "DELETE"}})
    |> render_submit()

    assert {:ok, %User{}} = read_user(administrator, administrator)
  end

  test "the inspection view does not render password hashes or confirmation internals", %{
    conn: conn
  } do
    administrator = admin_fixture()
    target = user_fixture()
    conn = log_in_user(conn, administrator)
    inspection_path = "/admin/users/#{target.id}"

    assert {:error, {:live_redirect, %{to: ^inspection_path}}} =
             live(conn, admin_action_path(target, :read, :admin_read))

    assert {:ok, view, _html} = live(conn, inspection_path)

    refute has_element?(view, "*", "fixture-password-hash")
    refute has_element?(view, "*", "Hashed Password")
    refute has_element?(view, "*", "Email Change Confirmed At")
    refute has_element?(view, "*", "Api Keys")
    refute has_element?(view, "*", "Valid Api Keys")
    refute has_element?(view, "button[phx-click='load']")

    inspected_user = read_admin_user(administrator, target)
    assert %Ash.NotLoaded{} = inspected_user.hashed_password
    assert %Ash.NotLoaded{} = inspected_user.email_change_confirmed_at
  end

  test "all named target administration controls are available only on the safe inspection view",
       %{
         conn: conn
       } do
    administrator = admin_fixture()
    target = user_fixture()
    view = admin_user_view(conn, administrator, target)

    for selector <- [
          "#admin-user-resend-invitation",
          "#admin-user-revoke-invitation",
          "#admin-user-enable",
          "#admin-user-disable",
          "#admin-user-promote",
          "#admin-user-demote",
          "#admin-user-manage-email",
          "#admin-user-revoke-sessions",
          "#admin-user-revoke-api-keys",
          "#admin-user-delete"
        ] do
      assert has_element?(view, selector)
    end

    assert_safe_admin_surface(view)
  end

  test "the AshAdmin pending-user invitation form has no original-record internals", %{conn: conn} do
    administrator = admin_fixture()

    assert {:ok, view, _html} =
             live(
               log_in_user(conn, administrator),
               "/admin?domain=Accounts&resource=User&action_type=create&action=create_pending_user"
             )

    assert has_element?(view, "#form")
    assert_safe_admin_surface(view)
  end

  test "direct generic update and destroy routes redirect before their forms can mount", %{
    conn: conn
  } do
    administrator = admin_fixture()
    target = user_fixture()
    conn = log_in_user(conn, administrator)
    inspection_path = "/admin/users/#{target.id}"

    for {action_type, action} <- [update: :manage_email, destroy: :admin_delete] do
      assert {:error, {:live_redirect, %{to: ^inspection_path}}} =
               live(conn, admin_action_path(target, action_type, action))
    end

    assert {:ok, view, _html} = live(conn, "/admin")

    assert {:error, {:live_redirect, %{to: ^inspection_path}}} =
             render_patch(view, admin_action_path(target, :update, :manage_email))
  end

  test "the inspection lifecycle controls update a target account", %{conn: conn} do
    administrator = admin_fixture()
    target = user_fixture()
    view = admin_user_view(conn, administrator, target)

    view
    |> form("#admin-user-disable")
    |> render_submit()

    assert :disabled == read_user!(administrator, target).status
  end

  test "the inspection resend control issues a fresh pending setup invitation", %{conn: conn} do
    administrator = admin_fixture()
    pending_user = pending_user_fixture()
    assert_receive {:email, _initial_invitation}
    view = admin_user_view(conn, administrator, pending_user)

    view
    |> form("#admin-user-resend-invitation")
    |> render_submit()

    assert_receive {:email, email}
    assert email.to == [{"", to_string(pending_user.email)}]
    assert email.text_body =~ "/setup/"
  end

  test "the inspection resend control shows retry guidance without a sixth invitation", %{
    conn: conn
  } do
    administrator = admin_fixture()
    pending_user = pending_user_fixture()
    assert_receive {:email, _initial_invitation}
    view = admin_user_view(conn, administrator, pending_user)

    for _ <- 1..5 do
      view
      |> form("#admin-user-resend-invitation")
      |> render_submit()

      assert_receive {:email, _invitation}
    end

    view
    |> form("#admin-user-resend-invitation")
    |> render_submit()

    assert has_element?(view, "#admin-user-resend-invitation-rate-limited", "Please try again in")
    refute_email_sent()
  end

  test "the inspection credential controls revoke browser sessions and API keys", %{conn: conn} do
    administrator = admin_fixture()
    target = user_fixture()
    api_key = api_key_fixture(target)
    assert {:ok, session_token, _claims} = Jwt.token_for_user(target, %{}, purpose: :user)
    view = admin_user_view(conn, administrator, target)

    view
    |> form("#admin-user-revoke-sessions")
    |> render_submit()

    assert {:error, :invalid_token} = Token.valid_for_purpose?(session_token, "user")

    view
    |> form("#admin-user-revoke-api-keys")
    |> render_submit()

    assert %ApiKey{revoked_at: %DateTime{}} = Repo.get!(ApiKey, api_key.id)
  end

  test "direct lifecycle calls remain denied to non-admin, disabled, and self-targeting actors" do
    target = user_fixture()
    ordinary_user = user_fixture()
    administrator = admin_fixture()
    disabled_administrator = admin_fixture()
    stale_administrator = admin_fixture()

    assert {:ok, disabled_administrator} =
             Accounts.disable_user(administrator, disabled_administrator)

    assert {:ok, _disabled_stale_administrator} =
             Accounts.disable_user(administrator, stale_administrator)

    assert {:error, _reason} = read_user(stale_administrator, target)
    assert {:error, _reason} = read_user_with_action(stale_administrator, target, :admin_read)

    for actor <- [ordinary_user, disabled_administrator] do
      assert {:error, _reason} = read_user(actor, target)
      assert {:error, _reason} = read_user_with_action(actor, target, :admin_read)

      assert {:error, _reason} =
               target
               |> Ash.Changeset.for_update(:disable, %{})
               |> Ash.update(actor: actor, authorize?: true, domain: Accounts)
    end

    assert {:error, _reason} =
             administrator
             |> Ash.Changeset.for_destroy(:admin_delete, %{})
             |> Ash.destroy(actor: administrator, authorize?: true, domain: Accounts)

    delete_target = user_fixture()

    assert {:error, _reason} =
             delete_target
             |> Ash.Changeset.for_destroy(:admin_delete, %{})
             |> Ash.destroy(actor: administrator, authorize?: true, domain: Accounts)
  end

  defp admin_user_view(conn, administrator, user) do
    assert {:ok, view, _html} =
             live(log_in_user(conn, administrator), "/admin/users/#{user.id}")

    view
  end

  defp admin_action_path(user, action_type, action) do
    primary_key = AshAdmin.Helpers.encode_primary_key(user)

    "/admin?domain=Accounts&resource=User&action_type=#{action_type}&action=#{action}&primary_key=#{primary_key}"
  end

  defp read_user!(administrator, user) do
    assert {:ok, %User{} = user} = read_user(administrator, user)
    user
  end

  defp read_user(administrator, user) do
    read_user_with_action(administrator, user, :read)
  end

  defp read_user_with_action(administrator, user, action) do
    User
    |> Ash.Query.for_read(action, %{}, actor: administrator, domain: Accounts)
    |> Ash.Query.filter(id: user.id)
    |> Ash.read_one(actor: administrator, authorize?: true, domain: Accounts)
  end

  defp read_admin_user(administrator, user) do
    assert {:ok, %User{} = user} =
             read_user_with_action(administrator, user, :admin_read)

    user
  end

  defp assert_safe_admin_surface(view) do
    for label <- [
          "Original Record",
          "Hashed Password",
          "Email Change Confirmed At",
          "Api Keys",
          "Valid Api Keys",
          "hashed_password",
          "email_change_confirmed_at",
          "api_keys",
          "valid_api_keys"
        ] do
      refute has_element?(view, "*", label)
    end

    refute has_element?(view, "button[phx-click='load']")
  end
end
