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
end
