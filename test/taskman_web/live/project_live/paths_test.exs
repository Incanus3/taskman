defmodule TaskmanWeb.ProjectLive.PathsTest do
  use ExUnit.Case, async: true

  alias Taskman.Lists.TaskList
  alias Taskman.Projects.Project
  alias Taskman.Tasks.Task
  alias TaskmanWeb.ProjectLive.Paths

  test "builds browse, creation, and detail routes with stable query ordering" do
    project = %Project{id: 11}
    task_list = %TaskList{id: 22}
    task = %Task{id: 33}

    assert Paths.browse_path(project, nil, false) == "/projects/11"

    assert Paths.browse_path(project, task_list, true) ==
             "/projects/11/lists/22?include_children=true"

    assert Paths.new_task_path(project, task_list, true, 33) ==
             "/projects/11/lists/22/tasks/new?include_children=true&parent_task_id=33"

    assert Paths.task_detail_path(project, nil, task, true) ==
             "/projects/11/tasks/33?include_children=true"
  end

  test "recognizes only the exact selected Task route" do
    project = %Project{id: 11}
    task_list = %TaskList{id: 22}
    task = %Task{id: 33}
    params = %{"project_id" => "11", "list_id" => "22", "task_id" => "33"}

    assert Paths.selected_task_route?(params, project, task_list, task, false)
    refute Paths.selected_task_route?(params, project, nil, task, false)

    refute Paths.selected_task_route?(
             Map.put(params, "include_children", "true"),
             project,
             task_list,
             task,
             false
           )
  end
end
