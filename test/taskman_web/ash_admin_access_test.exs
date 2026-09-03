defmodule TaskmanWeb.AshAdminAccessTest do
  use TaskmanWeb.ConnCase, async: false

  import Phoenix.LiveViewTest
  import Taskman.AccountsFixtures

  alias Taskman.Accounts
  alias TaskmanWeb.AshAdminActorPlug

  test "the administration route sends guests to sign-in with its safe return path", %{conn: conn} do
    assert redirected_to(get(conn, "/admin")) == "/sign-in?return_to=%2Fadmin"
  end

  test "the administration route rejects ordinary and disabled users", %{conn: conn} do
    ordinary_user = user_fixture()
    disabled_administrator = admin_fixture()
    active_administrator = admin_fixture()
    disabled_conn = log_in_user(conn, disabled_administrator)

    assert {:ok, _disabled_administrator} =
             Accounts.disable_user(active_administrator, disabled_administrator)

    for user_conn <- [log_in_user(conn, ordinary_user), disabled_conn] do
      assert redirected_to(get(user_conn, "/admin")) ==
               "/sign-in?return_to=%2Fadmin"
    end
  end

  test "an active administrator can open the users administration surface", %{conn: conn} do
    administrator = admin_fixture()
    _user = user_fixture()

    assert {:ok, view, _html} = live(log_in_user(conn, administrator), "/admin")
    assert has_element?(view, "a[href*='resource=User']")
    refute has_element?(view, "a[href*='resource=Token']")
    refute has_element?(view, "a[href*='resource=ApiKey']")
    refute has_element?(view, "*", "Hashed Password")
    refute has_element?(view, "*", "Email Change Confirmed At")
    refute has_element?(view, "*", "Valid Api Keys")
    refute has_element?(view, "button[phx-click='load'][phx-value-relationship='api_keys']")
  end

  test "forged admin protocol events cannot reach User details or relationship loads", %{
    conn: conn
  } do
    administrator = admin_fixture()
    target = user_fixture()
    conn = log_in_user(conn, administrator)
    inspection_path = "/admin/users/#{target.id}"

    assert {:ok, view, _html} = live(conn, "/admin")

    render_hook(view, "toggle_authorizing", %{})
    render_hook(view, "clear_actor", %{})

    assert {:error, {:live_redirect, %{to: ^inspection_path}}} =
             render_patch(view, admin_read_path(target))
  end

  test "forged admin protocol events cannot preserve a revoked mounted actor", %{conn: conn} do
    administrator = admin_fixture()
    second_administrator = admin_fixture()
    conn = log_in_user(conn, administrator)

    assert {:ok, view, _html} = live(conn, "/admin")

    assert {:ok, _disabled_administrator} =
             Accounts.disable_user(second_administrator, administrator)

    render_hook(view, "toggle_authorizing", %{})
    render_hook(view, "clear_actor", %{})

    assert {:error, {:redirect, %{status: 302, to: "/sign-in?return_to=%2Fadmin"}}} =
             render_patch(
               view,
               "/admin?domain=Accounts&resource=User&action_type=read&action=admin_read"
             )
  end

  test "the AshAdmin actor plug uses the scoped user and tolerates an absent user" do
    scoped_user = user_fixture()
    current_user = user_fixture()

    assert [actor: scoped_user] ==
             AshAdminActorPlug.actor_assigns(
               %Phoenix.LiveView.Socket{
                 assigns: %{current_scope: scoped_user, current_user: current_user}
               },
               %{}
             )

    assert [actor: current_user] ==
             AshAdminActorPlug.actor_assigns(
               %Phoenix.LiveView.Socket{assigns: %{current_user: current_user}},
               %{}
             )

    assert [actor: nil] == AshAdminActorPlug.actor_assigns(%Phoenix.LiveView.Socket{}, %{})
  end

  defp admin_read_path(user) do
    primary_key = AshAdmin.Helpers.encode_primary_key(user)

    "/admin?domain=Accounts&resource=User&action_type=read&action=admin_read&primary_key=#{primary_key}"
  end
end
