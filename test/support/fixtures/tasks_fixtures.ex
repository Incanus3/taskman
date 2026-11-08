defmodule Taskman.TasksFixtures do
  @moduledoc """
  Creates Task and relationship fixtures through the application write boundary.

  Write helpers supply a fresh eligible actor when `:actor` is omitted and return
  the context result unchanged. Explicit actors and all other options are preserved.
  `task_fixture/4` unwraps successful creation for fixture setup.
  """
  alias Taskman.{AccountsFixtures, Tasks}

  @doc "Creates a Task with a default actor, preserving the context result."
  def create_task(project, location, attrs, opts \\ []) do
    Tasks.create_task(project, location, attrs, with_default_actor(opts))
  end

  @doc "Updates a Task with a default actor, preserving the context result."
  def update_task(project, task, attrs, opts \\ []) do
    Tasks.update_task(project, task, attrs, with_default_actor(opts))
  end

  @doc "Moves a Task with a default actor, preserving the context result."
  def move_task(project, task, destination, opts \\ []) do
    Tasks.move_task(project, task, destination, with_default_actor(opts))
  end

  @doc "Adds a blocking relationship with a default actor, preserving the context result."
  def add_block(project, blocker, blocked, opts \\ []) do
    Tasks.add_block(project, blocker, blocked, with_default_actor(opts))
  end

  @doc "Removes a blocking relationship with a default actor, preserving the context result."
  def remove_block(project, blocker, blocked, opts \\ []) do
    Tasks.remove_block(project, blocker, blocked, with_default_actor(opts))
  end

  def task_fixture(project, attrs \\ %{}) do
    task_fixture(project, nil, attrs)
  end

  def task_fixture(project, attrs, opts) when is_map(attrs) and is_list(opts) do
    task_fixture(project, nil, attrs, opts)
  end

  def task_fixture(project, location, attrs) do
    task_fixture(project, location, attrs, [])
  end

  def task_fixture(project, location, attrs, opts) when is_list(opts) do
    unique = System.unique_integer([:positive])
    attrs = Map.merge(%{title: "Task #{unique}"}, Map.new(attrs))
    {:ok, task} = create_task(project, location, attrs, opts)
    task
  end

  def loaded_task_baselines(project, task) do
    {
      Tasks.get_task_for_project(project, task.id),
      Tasks.get_task_for_project(project, task.id)
    }
  end

  defp with_default_actor(opts) do
    Keyword.put_new_lazy(opts, :actor, &AccountsFixtures.user_fixture/0)
  end
end
