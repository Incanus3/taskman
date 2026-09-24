defmodule Taskman.ProjectsFixtures do
  def project_fixture(attrs) do
    unique = System.unique_integer([:positive])

    attrs =
      Map.merge(
        %{name: "Project #{unique}"},
        Map.new(attrs)
      )

    {:ok, project} = Taskman.Projects.create_project(attrs)
    project
  end
end
