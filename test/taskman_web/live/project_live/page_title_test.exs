defmodule TaskmanWeb.ProjectLive.PageTitleTest do
  use TaskmanWeb.ConnCase, async: false

  import Phoenix.LiveViewTest
  import Taskman.AccountsFixtures
  import Taskman.ProjectsFixtures

  setup %{conn: conn} do
    {:ok, conn: log_in_user(conn, user_fixture())}
  end

  test "initial title reflects the selected Project or the unselected workspace", %{conn: conn} do
    project = project_fixture(%{name: "Orchard"})

    for {path, title} <- [{~p"/", "Taskman"}, {~p"/projects/#{project.id}", "Taskman · Orchard"}] do
      response = get(conn, path)

      assert response
             |> html_response(200)
             |> LazyHTML.from_document()
             |> LazyHTML.query("title")
             |> LazyHTML.text() == title
    end
  end

  test "title follows Project selection and clearing the selection", %{conn: conn} do
    orchard = project_fixture(%{name: "Orchard"})
    harbor = project_fixture(%{name: "Harbor"})
    {:ok, view, _html} = live(conn, ~p"/")
    assert page_title(view) == "Taskman"

    render_patch(view, ~p"/projects/#{orchard.id}")
    assert page_title(view) == "Taskman · Orchard"

    render_patch(view, ~p"/projects/#{harbor.id}")
    assert page_title(view) == "Taskman · Harbor"

    render_patch(view, ~p"/")
    assert page_title(view) == "Taskman"
  end

  test "title follows local and external Project renames", %{conn: conn} do
    project = project_fixture(%{name: "Orchard"})
    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}")

    view |> element("#project-edit-button") |> render_click()
    view |> form("#project-form", project: %{name: "Harbor"}) |> render_submit()
    assert page_title(view) == "Taskman · Harbor"

    assert {:ok, _project} =
             Task.async(fn -> Taskman.Projects.update_project(project, %{name: "Garden"}) end)
             |> Task.await()

    _ = :sys.get_state(view.pid)
    assert page_title(view) == "Taskman · Garden"
  end
end
