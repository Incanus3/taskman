defmodule Taskman.RepoCompatibilityTest do
  use Taskman.DataCase, async: false

  test "AshPostgres Repo preserves Ecto transactions" do
    assert {:error, :rolled_back} =
             Taskman.Repo.transaction(fn ->
               {:ok, project} =
                 Taskman.Projects.create_project(%{
                   name: "Compatibility",
                   primary_directory: File.cwd!()
                 })

               assert Taskman.Repo.get!(Taskman.Projects.Project, project.id)
               Taskman.Repo.rollback(:rolled_back)
             end)

    assert Taskman.Projects.list_projects() == []
  end

  test "Repo declares required PostgreSQL extensions" do
    assert Taskman.Repo.installed_extensions() == ["uuid-ossp", "citext"]
  end
end
