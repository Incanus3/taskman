defmodule TaskmanWeb.AshAdminActionsTest do
  use TaskmanWeb.ConnCase, async: false

  import Phoenix.LiveViewTest
  import Swoosh.TestAssertions
  import Taskman.AccountsFixtures

  require Ash.Query

  alias Taskman.Accounts
  alias Taskman.Accounts.User

  setup :set_swoosh_global

  test "the admin configuration exposes only Users and named lifecycle actions" do
    assert AshAdmin.Domain.show_resources(Accounts) == [User]
    assert AshAdmin.Resource.read_actions(User) == [:admin_read]
    assert AshAdmin.Resource.generic_actions(User) == []
    assert AshAdmin.Resource.create_actions(User) == [:create_pending_user]

    assert AshAdmin.Resource.update_actions(User) == [
             :resend_invitation,
             :revoke_invitation,
             :enable,
             :disable,
             :promote,
             :demote,
             :manage_email,
             :revoke_sessions,
             :revoke_api_keys
           ]

    assert AshAdmin.Resource.destroy_actions(User) == [:admin_delete]
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

    view = admin_action_view(conn, administrator, pending_user, :update, :manage_email)
    replacement_email = "replacement-pending@example.com"

    view
    |> form("#form", %{"form" => %{"email" => replacement_email, "confirmed?" => "false"}})
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
    view = admin_action_view(conn, administrator, target, :destroy, :admin_delete)

    assert has_element?(view, "#form")
    assert has_element?(view, "#form input[name='form[confirmation]']")

    view
    |> form("#form", %{"form" => %{}})
    |> render_submit()

    assert {:ok, %User{}} = read_user(administrator, target)

    view
    |> form("#form", %{"form" => %{"confirmation" => "no"}})
    |> render_submit()

    assert {:ok, %User{}} = read_user(administrator, target)

    view
    |> form("#form", %{"form" => %{"confirmation" => "DELETE"}})
    |> render_submit()

    assert {:ok, nil} = read_user(administrator, target)
  end

  test "forged admin protocol events cannot execute a self-targeted delete", %{conn: conn} do
    administrator = admin_fixture()
    view = admin_action_view(conn, administrator, administrator, :destroy, :admin_delete)

    render_hook(view, "toggle_authorizing", %{})
    render_hook(view, "clear_actor", %{})

    view
    |> form("#form", %{"form" => %{"confirmation" => "DELETE"}})
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

  defp admin_action_view(conn, administrator, user, action_type, action) do
    assert {:ok, view, _html} =
             live(log_in_user(conn, administrator), admin_action_path(user, action_type, action))

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
end
