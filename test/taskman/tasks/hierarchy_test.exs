defmodule Taskman.Tasks.HierarchyTest do
  use Taskman.DataCase, async: true

  import Taskman.ProjectsFixtures
  import Taskman.ListsFixtures
  import Taskman.TasksFixtures

  alias Taskman.Tasks
  alias Taskman.Tasks.Hierarchy
  alias Taskman.Tasks.HierarchyNode
  alias Taskman.Tasks.TaskWithLocation

  test "empty parent search returns eligible tasks in stable order and honors the limit" do
    project = project_fixture(%{})
    first = task_fixture(project, %{title: "First"})
    second = task_fixture(project, %{title: "Second"})
    third = task_fixture(project, %{title: "Third"})

    assert [%TaskWithLocation{task: ^first}, %TaskWithLocation{task: ^second}] =
             Tasks.search_parent_candidates(project, nil, "  ", limit: 2)

    assert [
             %TaskWithLocation{task: ^first},
             %TaskWithLocation{task: ^second},
             %TaskWithLocation{task: ^third}
           ] =
             Tasks.search_parent_candidates(project, nil, "", limit: 20)
  end

  test "parent search matches titles case-insensitively" do
    project = project_fixture(%{})
    matching = task_fixture(project, %{title: "Launch Checklist"})
    _other = task_fixture(project, %{title: "Unrelated"})

    assert [%TaskWithLocation{task: ^matching}] =
             Tasks.search_parent_candidates(project, nil, "  launch  ")
  end

  test "positive exact Task ID match is first without dropping title matches" do
    project = project_fixture(%{})
    title_match = task_fixture(project, %{title: "Placeholder"})
    exact = task_fixture(project, %{title: "A title"})

    {:ok, title_match} =
      Tasks.update_task(project, title_match, %{title: "Task #{exact.id} follow-up"})

    assert [%TaskWithLocation{task: ^exact}, %TaskWithLocation{task: ^title_match}] =
             Tasks.search_parent_candidates(project, nil, Integer.to_string(exact.id))
  end

  test "parent search returns no more than 20 candidates by default" do
    project = project_fixture(%{})
    tasks = for n <- 1..21, do: task_fixture(project, %{title: "Candidate #{n}"})

    assert Enum.map(Tasks.search_parent_candidates(project, nil, "Candidate"), & &1.task.id) ==
             tasks |> Enum.take(20) |> Enum.map(& &1.id)
  end

  test "parent search matches ID and title terms while retaining non-exact insertion order" do
    project = project_fixture(%{})
    exact = task_fixture(project, %{title: "Publish second"})
    id_term = String.slice(Integer.to_string(exact.id), 0, 1)
    first = task_fixture(project, %{title: "#{id_term} Publish first"})
    last = task_fixture(project, %{title: "#{id_term} Publish last"})

    assert [exact.id, first.id, last.id] ==
             project
             |> Tasks.search_parent_candidates(nil, "#{id_term} publish")
             |> Enum.map(& &1.task.id)
  end

  test "parent search treats percent and underscore literally" do
    project = project_fixture(%{})
    percent = task_fixture(project, %{title: "Ready %"})
    underscore = task_fixture(project, %{title: "Ready _"})
    _plain = task_fixture(project, %{title: "Ready x"})

    assert [%TaskWithLocation{task: ^percent}] =
             Tasks.search_parent_candidates(project, nil, "%")

    assert [%TaskWithLocation{task: ^underscore}] =
             Tasks.search_parent_candidates(project, nil, "_")
  end

  test "parent search is Project-scoped, includes List paths, and excludes current descendants" do
    project = project_fixture(%{})
    other_project = project_fixture(%{})
    planning = list_fixture(project, nil, %{name: "Planning"})
    launch = list_fixture(project, planning, %{name: "Launch"})

    current = task_fixture(project, %{title: "Current"})
    descendant = task_fixture(project, launch, %{title: "Descendant"}, parent: current)
    eligible = task_fixture(project, planning, %{title: "Eligible"})
    foreign = task_fixture(other_project, %{title: "Eligible"})

    results = Tasks.search_parent_candidates(project, current, "")

    assert [%TaskWithLocation{task: ^eligible, location_path: [^planning]}] = results
    refute Enum.any?(results, &(&1.task.id in [current.id, descendant.id, foreign.id]))
  end

  test "hierarchy returns the selected Task as the disconnected root" do
    project = project_fixture(%{})
    selected = task_fixture(project, %{title: "Standalone"})

    assert {:ok,
            %Hierarchy{
              selected_task_id: selected_id,
              root: %HierarchyNode{task: root_task, children: []}
            }} = Tasks.get_task_hierarchy(project, selected)

    assert selected_id == selected.id
    assert root_task.id == selected.id
  end

  test "hierarchy discovers the topmost root, paths, complete tree, and sibling order" do
    project = project_fixture(%{})
    planning = list_fixture(project, nil, %{name: "Planning"})
    root = task_fixture(project, planning, %{title: "Root"})
    first = task_fixture(project, %{title: "First"}, parent: root)
    second = task_fixture(project, %{title: "Second"}, parent: root)
    selected = task_fixture(project, planning, %{title: "Selected"}, parent: first)
    second_child = task_fixture(project, %{title: "Second child"}, parent: second)
    _unrelated = task_fixture(project, %{title: "Unrelated"})
    second_child_id = second_child.id

    assert {:ok,
            %Hierarchy{
              selected_task_id: selected_id,
              root: %HierarchyNode{
                task: %{id: root_id},
                location_path: [^planning],
                children: [
                  %HierarchyNode{
                    task: %{id: first_id},
                    location_path: [],
                    children: [
                      %HierarchyNode{
                        task: %{id: selected_id},
                        location_path: [^planning]
                      }
                    ]
                  },
                  %HierarchyNode{
                    task: %{id: second_id},
                    location_path: [],
                    children: [%HierarchyNode{task: %{id: ^second_child_id}, location_path: []}]
                  }
                ]
              }
            }} = Tasks.get_task_hierarchy(project, selected)

    assert selected_id == selected.id
    assert root_id == root.id
    assert first_id == first.id
    assert second_id == second.id
  end

  test "hierarchy returns not found for a foreign or stale selected Task" do
    project = project_fixture(%{})
    other_project = project_fixture(%{})
    foreign = task_fixture(other_project, %{title: "Foreign"})
    stale = task_fixture(project, %{title: "Stale"})
    Repo.delete!(stale)

    assert {:error, :not_found} = Tasks.get_task_hierarchy(project, foreign)
    assert {:error, :not_found} = Tasks.get_task_hierarchy(project, stale)
  end
end
