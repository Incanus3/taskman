defmodule Taskman.ProjectsTest do
  use Taskman.DataCase, async: true

  alias Taskman.Projects

  @tag :tmp_dir
  test "create_project/1 normalizes and persists a valid directory", %{tmp_dir: tmp_dir} do
    relative_path = Path.relative_to(tmp_dir, File.cwd!())

    assert {:ok, project} =
             Projects.create_project(%{name: "  Taskman  ", primary_directory: relative_path})

    assert project.name == "Taskman"
    assert project.primary_directory == Path.expand(relative_path)
  end

  test "create_project/1 rejects missing fields and a non-directory path" do
    assert {:error, changeset} = Projects.create_project(%{})
    assert %{name: [_], primary_directory: [_]} = errors_on(changeset)

    assert {:error, changeset} =
             Projects.create_project(%{name: "Taskman", primary_directory: "/not/a/taskman/dir"})

    assert %{primary_directory: ["must be an existing directory"]} = errors_on(changeset)
  end

  @tag :tmp_dir
  test "list_projects/0 is stable and get_project/1 handles invalid IDs", %{tmp_dir: tmp_dir} do
    assert {:ok, first} =
             Projects.create_project(%{name: "First", primary_directory: tmp_dir})

    assert {:ok, second} =
             Projects.create_project(%{name: "Second", primary_directory: tmp_dir})

    assert Projects.list_projects() == [first, second]
    assert Projects.get_project(Integer.to_string(first.id)) == first
    assert Projects.get_project("not-an-id") == nil
    assert Projects.get_project(-1) == nil
  end
end
