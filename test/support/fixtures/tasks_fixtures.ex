defmodule Taskman.TasksFixtures do
  alias Taskman.Tasks

  def task_fixture(project, attrs \\ %{}) do
    task_fixture(project, nil, attrs)
  end

  def task_fixture(project, location, attrs) do
    unique = System.unique_integer([:positive])
    attrs = Map.merge(%{title: "Task #{unique}"}, Map.new(attrs))

    {:ok, task} = Tasks.create_task(project, location, attrs)
    task
  end
end
