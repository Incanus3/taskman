defmodule TaskmanWeb.ProjectSelectorTest do
  use ExUnit.Case, async: true
  use Phoenix.Component

  import Phoenix.LiveViewTest

  alias Taskman.Projects.Project
  alias TaskmanWeb.ProjectSelector

  test "selector keeps metadata truncatable and chooses a contrasting icon foreground" do
    project = %Project{
      id: 7,
      name: String.duplicate("A", 80),
      description: "Description",
      icon: "beaker",
      color: "#777777"
    }

    document =
      render_component(&ProjectSelector.selector/1, %{
        selected_project: project,
        projects: [{"projects-7", project}],
        projects_empty?: false,
        open?: true
      })
      |> LazyHTML.from_fragment()

    refute Enum.empty?(LazyHTML.query(document, "#project-selector-identity.min-w-0.flex-1"))
    refute Enum.empty?(LazyHTML.query(document, "#project-selector-name.block.truncate"))
    refute Enum.empty?(LazyHTML.query(document, "#project-selector-description.block.truncate"))

    refute Enum.empty?(
             LazyHTML.query(document, "#select-project-7[aria-current='page'] .hero-beaker")
           )

    assert [style] =
             LazyHTML.attribute(
               LazyHTML.query(document, "#project-selector > div > div"),
               "style"
             )

    assert style =~ "color: #000000"
  end

  test "unsupported stored identity values never enter icon or color markup" do
    project = %Project{id: 9, name: "Legacy", icon: "not-real", color: "red; position: fixed"}

    document =
      render_component(&ProjectSelector.selector/1, %{
        selected_project: project,
        projects: [{"projects-9", project}],
        projects_empty?: false,
        open?: true
      })
      |> LazyHTML.from_fragment()

    refute Enum.empty?(LazyHTML.query(document, "#project-selector .hero-briefcase"))

    assert [style] =
             LazyHTML.attribute(
               LazyHTML.query(document, "#project-selector > div > div"),
               "style"
             )

    assert style == "background-color: #6366F1; color: #FFFFFF"
  end
end
