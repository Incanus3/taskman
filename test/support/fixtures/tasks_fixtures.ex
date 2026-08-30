defmodule Taskman.TasksFixtures do
  alias Taskman.Tasks

  def task_fixture(project, attrs \\ %{}) do
    task_fixture(project, nil, attrs)
  end

  def task_fixture(project, attrs, opts) when is_map(attrs) and is_list(opts) do
    task_fixture(project, nil, attrs, opts)
  end

  def task_fixture(project, location, attrs) do
    unique = System.unique_integer([:positive])
    attrs = Map.merge(%{title: "Task #{unique}"}, Map.new(attrs))

    {:ok, task} = Tasks.create_task(project, location, attrs)
    task
  end

  def task_fixture(project, location, attrs, opts) when is_list(opts) do
    unique = System.unique_integer([:positive])
    attrs = Map.merge(%{title: "Task #{unique}"}, Map.new(attrs))

    {:ok, task} = Tasks.create_task(project, location, attrs, opts)
    task
  end
end
