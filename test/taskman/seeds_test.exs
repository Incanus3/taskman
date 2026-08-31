defmodule Taskman.SeedsTest do
  use Taskman.DataCase, async: false

  alias Taskman.Lists.TaskList
  alias Taskman.Projects
  alias Taskman.Projects.Project
  alias Taskman.Repo
  alias Taskman.Tasks.Task

  @statuses [:icebox, :pending, :in_progress, :in_review, :done, :will_not_do]
  @priorities [:none, :low, :medium, :high, :urgent]

  test "seeds replace existing data with a rerunnable nested sample dataset" do
    {:ok, existing_project} =
      Projects.create_project(%{
        name: "Existing project",
        primary_directory: File.cwd!()
      })

    run_seeds()

    refute Repo.get(Project, existing_project.id)
    assert Repo.aggregate(Project, :count) == 1
    assert Repo.aggregate(TaskList, :count) >= 4
    assert Repo.aggregate(Task, :count) == 30

    task_lists = Repo.all(TaskList)
    tasks = Repo.all(Task)

    assert Enum.any?(task_lists, &is_integer(&1.parent_list_id))
    assert has_grandchild_list?(task_lists)
    assert Enum.any?(tasks, &is_integer(&1.parent_task_id))
    assert has_grandchild_task?(tasks)

    expected_combinations =
      for status <- @statuses, priority <- @priorities, do: {status, priority}

    actual_combinations =
      tasks
      |> Enum.map(&{&1.status, &1.priority})
      |> Enum.sort()

    assert actual_combinations == Enum.sort(expected_combinations)

    first_seed = dataset_signature()

    run_seeds()

    assert Repo.aggregate(Project, :count) == 1
    assert Repo.aggregate(Task, :count) == 30
    assert dataset_signature() == first_seed
  end

  defp run_seeds do
    Code.eval_file(Path.join(File.cwd!(), "priv/repo/seeds.exs"))
  end

  defp has_grandchild_list?(task_lists) do
    lists_by_id = Map.new(task_lists, &{&1.id, &1})

    Enum.any?(task_lists, fn task_list ->
      case Map.get(lists_by_id, task_list.parent_list_id) do
        %TaskList{parent_list_id: parent_id} when is_integer(parent_id) -> true
        _task_list -> false
      end
    end)
  end

  defp has_grandchild_task?(tasks) do
    tasks_by_id = Map.new(tasks, &{&1.id, &1})

    Enum.any?(tasks, fn task ->
      case Map.get(tasks_by_id, task.parent_task_id) do
        %Task{parent_task_id: parent_id} when is_integer(parent_id) -> true
        _task -> false
      end
    end)
  end

  defp dataset_signature do
    project = Repo.one!(Project)
    task_lists = Repo.all(TaskList)
    tasks = Repo.all(Task)

    list_names_by_id = Map.new(task_lists, &{&1.id, &1.name})
    task_titles_by_id = Map.new(tasks, &{&1.id, &1.title})

    %{
      project: {project.name, project.primary_directory},
      lists:
        task_lists
        |> Enum.map(&{&1.name, Map.get(list_names_by_id, &1.parent_list_id)})
        |> Enum.sort(),
      tasks:
        tasks
        |> Enum.map(fn task ->
          {
            task.title,
            task.status,
            task.priority,
            Map.get(list_names_by_id, task.list_id),
            Map.get(task_titles_by_id, task.parent_task_id)
          }
        end)
        |> Enum.sort()
    }
  end
end
