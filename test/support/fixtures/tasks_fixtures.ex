defmodule Taskman.TasksFixtures do
  alias Taskman.Tasks

  def task_fixture(project, attrs \\ %{}) do
    unique = System.unique_integer([:positive])
    attrs = Map.merge(%{title: "Task #{unique}"}, Map.new(attrs))

    {:ok, task} = Tasks.create_task(project, attrs)
    task
  end
end
