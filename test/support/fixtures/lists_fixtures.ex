defmodule Taskman.ListsFixtures do
  alias Taskman.Lists

  def list_fixture(project), do: list_fixture(project, nil, %{})

  def list_fixture(project, attrs) when is_map(attrs) and not is_struct(attrs) do
    list_fixture(project, nil, attrs)
  end

  def list_fixture(project, parent_or_nil), do: list_fixture(project, parent_or_nil, %{})

  def list_fixture(project, parent_or_nil, attrs) do
    unique = System.unique_integer([:positive])
    attrs = Map.merge(%{name: "List #{unique}"}, Map.new(attrs))

    {:ok, task_list} = Lists.create_list(project, parent_or_nil, attrs)
    task_list
  end
end
