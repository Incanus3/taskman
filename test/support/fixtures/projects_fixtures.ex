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

  def project_response_fixture(overrides \\ %{}) do
    Map.merge(
      %{
        "id" => 7,
        "name" => "CLI",
        "description" => "",
        "icon" => "briefcase",
        "color" => "#6366F1"
      },
      Map.new(overrides)
    )
  end
end
