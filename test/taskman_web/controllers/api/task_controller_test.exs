defmodule TaskmanWeb.API.TaskControllerTest do
  use TaskmanWeb.ConnCase, async: true

  import Taskman.ListsFixtures
  import Taskman.ProjectsFixtures
  import Taskman.TasksFixtures

  alias Taskman.Repo
  alias Taskman.Tasks.Conflict
  alias Taskman.Tasks.Task
  alias Taskman.Tasks
  alias TaskmanWeb.API.FallbackController

  test "GET tasks lists direct Project Tasks with a project location", %{conn: conn} do
    project = project_fixture(%{})
    direct = task_fixture(project, %{title: "Direct"})
    _listed = task_fixture(project, list_fixture(project), %{title: "Listed"})

    conn = get(conn, "/api/v1/projects/#{project.id}/tasks")

    assert %{
             "data" => [
               %{
                 "id" => id,
                 "project_id" => project_id,
                 "list_id" => nil,
                 "parent_task_id" => nil,
                 "title" => "Direct",
                 "description" => "",
                 "status" => "pending",
                 "priority" => "none",
                 "due_at" => nil,
                 "location" => %{"kind" => "project", "list_id" => nil, "path" => []}
               }
             ]
           } = json_response(conn, 200)

    assert id == direct.id
    assert project_id == project.id
  end

  test "GET tasks reuses precomputed locations without querying Lists per Task", %{conn: conn} do
    project = project_fixture(%{})
    _first = task_fixture(project, %{title: "First"})
    _second = task_fixture(project, %{title: "Second"})
    test_pid = self()
    handler_id = {__MODULE__, make_ref()}

    :ok =
      :telemetry.attach(
        handler_id,
        [:taskman, :repo, :query],
        fn
          _event, _measurements, %{query: query}, ^test_pid when is_binary(query) ->
            if self() == test_pid and
                 String.starts_with?(query, "SELECT") and
                 String.contains?(query, ~s(FROM "lists")) do
              send(test_pid, {:list_query, query})
            end

          _event, _measurements, _metadata, _config ->
            :ok
        end,
        test_pid
      )

    on_exit(fn -> :telemetry.detach(handler_id) end)

    conn = get(conn, "/api/v1/projects/#{project.id}/tasks")

    assert %{"data" => [%{"title" => "First"}, %{"title" => "Second"}]} =
             json_response(conn, 200)

    refute_receive {:list_query, _query}
  end

  test "GET tasks lists direct List Tasks with its complete location path", %{conn: conn} do
    project = project_fixture(%{})
    root = list_fixture(project, %{name: "Planning"})
    child = list_fixture(project, root, %{name: "Launch"})
    task = task_fixture(project, child, %{title: "Copy"})

    conn = get(conn, "/api/v1/projects/#{project.id}/tasks?list_id=#{child.id}")

    assert %{
             "data" => [
               %{
                 "id" => id,
                 "list_id" => list_id,
                 "parent_task_id" => nil,
                 "title" => "Copy",
                 "location" => %{
                   "kind" => "list",
                   "list_id" => location_list_id,
                   "path" => ["Planning", "Launch"]
                 }
               }
             ]
           } = json_response(conn, 200)

    assert id == task.id
    assert list_id == child.id
    assert location_list_id == child.id
  end

  test "GET tasks includes descendant locations", %{conn: conn} do
    project = project_fixture(%{})
    root = list_fixture(project, %{name: "Planning"})
    child = list_fixture(project, root, %{name: "Launch"})
    task = task_fixture(project, child, %{title: "Copy"})

    conn =
      get(
        conn,
        "/api/v1/projects/#{project.id}/tasks?list_id=#{root.id}&include_descendants=true"
      )

    assert %{
             "data" => [
               %{
                 "id" => id,
                 "location" => %{
                   "kind" => "list",
                   "list_id" => list_id,
                   "path" => ["Planning", "Launch"]
                 }
               }
             ]
           } = json_response(conn, 200)

    assert id == task.id
    assert list_id == child.id
  end

  test "GET tasks treats non-true descendant query values as false", %{conn: conn} do
    project = project_fixture(%{})
    root = list_fixture(project)
    direct = task_fixture(project, root, %{title: "Direct list task"})
    _nested = task_fixture(project, list_fixture(project, root), %{title: "Nested list task"})

    conn =
      get(
        conn,
        "/api/v1/projects/#{project.id}/tasks?list_id=#{root.id}&include_descendants=yes"
      )

    assert %{"data" => [%{"id" => id}]} = json_response(conn, 200)
    assert id == direct.id
  end

  test "GET tasks filters multiple statuses and sorts by a stored Task field", %{conn: conn} do
    project = project_fixture(%{})
    alpha = task_fixture(project, %{title: "Alpha", status: :pending})
    omega = task_fixture(project, %{title: "omega", status: :done})
    _excluded = task_fixture(project, %{title: "Zulu", status: :will_not_do})

    conn =
      get(
        conn,
        "/api/v1/projects/#{project.id}/tasks?statuses[]=pending&statuses[]=done&sort=title&direction=desc"
      )

    assert %{"data" => [%{"id" => omega_id}, %{"id" => alpha_id}]} =
             json_response(conn, 200)

    assert omega_id == omega.id
    assert alpha_id == alpha.id
  end

  test "GET tasks sorts descendant Tasks by Location", %{conn: conn} do
    project = project_fixture(%{})
    planning = list_fixture(project, %{name: "Planning"})
    delivery = list_fixture(project, %{name: "delivery"})
    planning_task = task_fixture(project, planning, %{title: "Planning task"})
    delivery_task = task_fixture(project, delivery, %{title: "Delivery task"})

    conn =
      get(
        conn,
        "/api/v1/projects/#{project.id}/tasks?include_descendants=true&sort=location&direction=asc"
      )

    assert %{"data" => [%{"id" => delivery_id}, %{"id" => planning_id}]} =
             json_response(conn, 200)

    assert delivery_id == delivery_task.id
    assert planning_id == planning_task.id
  end

  test "GET tasks rejects invalid status and sort query combinations", %{conn: conn} do
    project = project_fixture(%{})

    invalid_queries = [
      "statuses[]=unknown",
      "statuses=",
      "sort=unknown&direction=asc",
      "sort=title&direction=sideways",
      "sort=title",
      "direction=asc",
      "sort=location&direction=asc"
    ]

    for query <- invalid_queries do
      response = get(recycle(conn), "/api/v1/projects/#{project.id}/tasks?#{query}")

      assert %{"error" => %{"code" => "invalid_request"}} = json_response(response, 400)
    end
  end

  test "GET task shows its owning location", %{conn: conn} do
    project = project_fixture(%{})
    root = list_fixture(project, %{name: "Planning"})
    child = list_fixture(project, root, %{name: "Launch"})
    task = task_fixture(project, child, %{title: "Copy", description: "Details"})

    conn = get(conn, "/api/v1/projects/#{project.id}/tasks/#{task.id}")

    assert %{
             "data" => %{
               "id" => id,
               "project_id" => project_id,
               "list_id" => list_id,
               "parent_task_id" => nil,
               "title" => "Copy",
               "description" => "Details",
               "status" => "pending",
               "priority" => "none",
               "due_at" => nil,
               "location" => %{
                 "kind" => "list",
                 "list_id" => location_list_id,
                 "path" => ["Planning", "Launch"]
               }
             }
           } = json_response(conn, 200)

    assert id == task.id
    assert project_id == project.id
    assert list_id == child.id
    assert location_list_id == child.id
  end

  test "GET task rejects a Task from another Project", %{conn: conn} do
    project = project_fixture(%{})
    other = project_fixture(%{})
    task = task_fixture(other)

    conn = get(conn, "/api/v1/projects/#{project.id}/tasks/#{task.id}")

    assert %{"error" => %{"code" => "not_found"}} = json_response(conn, 404)
  end

  test "POST creates a Project Task with every editable field", %{conn: conn} do
    project = project_fixture(%{})
    other = project_fixture(%{})

    conn =
      post(conn, "/api/v1/projects/#{project.id}/tasks", %{
        "task" => %{
          "title" => "  Plan release  ",
          "description" => "Release details",
          "status" => "in_review",
          "priority" => "urgent",
          "due_at" => "2026-08-03T16:00:00",
          "project_id" => other.id,
          "list_id" => nil
        }
      })

    assert %{
             "data" => %{
               "id" => id,
               "project_id" => project_id,
               "list_id" => nil,
               "parent_task_id" => nil,
               "title" => "Plan release",
               "description" => "Release details",
               "status" => "in_review",
               "priority" => "urgent",
               "due_at" => "2026-08-03T16:00:00",
               "location" => %{"kind" => "project", "list_id" => nil, "path" => []}
             }
           } = json_response(conn, 201)

    assert is_integer(id)
    assert project_id == project.id
  end

  test "POST creates a Task in a List with every editable field", %{conn: conn} do
    project = project_fixture(%{})
    root = list_fixture(project, %{name: "Planning"})

    conn =
      post(conn, "/api/v1/projects/#{project.id}/tasks", %{
        "task" => %{
          "title" => "List task",
          "description" => "Details",
          "status" => "will_not_do",
          "priority" => "high",
          "due_at" => nil,
          "list_id" => root.id
        }
      })

    assert %{
             "data" => %{
               "id" => id,
               "project_id" => project_id,
               "list_id" => list_id,
               "parent_task_id" => nil,
               "title" => "List task",
               "description" => "Details",
               "status" => "will_not_do",
               "priority" => "high",
               "due_at" => nil,
               "location" => %{
                 "kind" => "list",
                 "list_id" => location_list_id,
                 "path" => ["Planning"]
               }
             }
           } = json_response(conn, 201)

    assert is_integer(id)
    assert project_id == project.id
    assert list_id == root.id
    assert location_list_id == root.id
  end

  test "POST rejects a Task created in a foreign List", %{conn: conn} do
    project = project_fixture(%{})
    foreign_project = project_fixture(%{})
    foreign_list = list_fixture(foreign_project)

    conn =
      post(conn, "/api/v1/projects/#{project.id}/tasks", %{
        "task" => %{"title" => "No leak", "list_id" => foreign_list.id}
      })

    assert %{"error" => %{"code" => "not_found"}} = json_response(conn, 404)
  end

  test "POST reports Task changeset failures", %{conn: conn} do
    project = project_fixture(%{})

    conn =
      post(conn, "/api/v1/projects/#{project.id}/tasks", %{
        "task" => %{"title" => "", "status" => "not-a-status"}
      })

    assert %{
             "error" => %{
               "code" => "validation_failed",
               "fields" => %{"status" => [_], "title" => [_]}
             }
           } = json_response(conn, 422)
  end

  test "PATCH updates every editable field and clears due_at with JSON null", %{conn: conn} do
    project = project_fixture(%{})

    task =
      task_fixture(project, %{
        title: "Before",
        description: "Before details",
        status: :pending,
        priority: :low,
        due_at: ~N[2026-08-03 16:00:00]
      })

    conn =
      patch(conn, "/api/v1/projects/#{project.id}/tasks/#{task.id}", %{
        "task" => %{
          "title" => "  After  ",
          "description" => "After details",
          "status" => "in_progress",
          "priority" => "urgent",
          "due_at" => nil
        }
      })

    assert %{
             "data" => %{
               "id" => id,
               "project_id" => project_id,
               "list_id" => nil,
               "parent_task_id" => nil,
               "title" => "After",
               "description" => "After details",
               "status" => "in_progress",
               "priority" => "urgent",
               "due_at" => nil,
               "location" => %{"kind" => "project", "list_id" => nil, "path" => []}
             }
           } = json_response(conn, 200)

    assert id == task.id
    assert project_id == project.id
  end

  test "PATCH rejects a Task from another Project", %{conn: conn} do
    project = project_fixture(%{})
    other = project_fixture(%{})
    task = task_fixture(other)

    conn =
      patch(conn, "/api/v1/projects/#{project.id}/tasks/#{task.id}", %{
        "task" => %{"title" => "Leaked"}
      })

    assert %{"error" => %{"code" => "not_found"}} = json_response(conn, 404)
  end

  test "PATCH reports Task changeset failures", %{conn: conn} do
    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Original"})

    conn =
      patch(conn, "/api/v1/projects/#{project.id}/tasks/#{task.id}", %{
        "task" => %{"title" => ""}
      })

    assert %{
             "error" => %{"code" => "validation_failed", "fields" => %{"title" => [_]}}
           } = json_response(conn, 422)
  end

  test "POST move moves a Task to a List and then Project root", %{conn: conn} do
    project = project_fixture(%{})
    destination = list_fixture(project, %{name: "Planning"})
    task = task_fixture(project, %{title: "Move me"})

    conn =
      post(conn, "/api/v1/projects/#{project.id}/tasks/#{task.id}/move", %{
        "destination" => %{"list_id" => destination.id}
      })

    assert %{
             "data" => %{
               "id" => id,
               "list_id" => list_id,
               "parent_task_id" => nil,
               "location" => %{
                 "kind" => "list",
                 "list_id" => location_list_id,
                 "path" => ["Planning"]
               }
             }
           } = json_response(conn, 200)

    assert id == task.id
    assert list_id == destination.id
    assert location_list_id == destination.id

    conn =
      post(conn, "/api/v1/projects/#{project.id}/tasks/#{task.id}/move", %{
        "destination" => %{"list_id" => nil}
      })

    assert %{
             "data" => %{
               "id" => ^id,
               "list_id" => nil,
               "parent_task_id" => nil,
               "location" => %{"kind" => "project", "list_id" => nil, "path" => []}
             }
           } = json_response(conn, 200)
  end

  test "POST move reports unchanged location as 409", %{conn: conn} do
    project = project_fixture(%{})
    task = task_fixture(project)

    conn =
      post(conn, "/api/v1/projects/#{project.id}/tasks/#{task.id}/move", %{
        "destination" => %{"list_id" => nil}
      })

    assert %{"error" => %{"code" => "unchanged_location"}} = json_response(conn, 409)
  end

  test "Task conflicts use a stable 409 envelope without exposing the current Task", %{conn: conn} do
    current = %Task{id: 42, project_id: 7, title: "Current title", lock_version: 9}
    conflict = %Conflict{task: current, fields: [:title, :status]}

    conn = FallbackController.call(conn, {:error, conflict})

    assert json_response(conn, 409) == %{
             "error" => %{
               "code" => "concurrent_update",
               "message" => "Task changed concurrently",
               "fields" => %{
                 "status" => ["changed concurrently"],
                 "title" => ["changed concurrently"]
               }
             }
           }
  end

  test "POST move rejects a destination from another Project", %{conn: conn} do
    project = project_fixture(%{})
    other = project_fixture(%{})
    destination = list_fixture(other)
    task = task_fixture(project)

    conn =
      post(conn, "/api/v1/projects/#{project.id}/tasks/#{task.id}/move", %{
        "destination" => %{"list_id" => destination.id}
      })

    assert %{"error" => %{"code" => "not_found"}} = json_response(conn, 404)
  end

  test "nested Task routes reject malformed Project, List, and Task IDs", %{conn: conn} do
    assert %{"error" => %{"code" => "invalid_request"}} =
             conn
             |> get("/api/v1/projects/not-an-id/tasks")
             |> json_response(400)

    project = project_fixture(%{})

    assert %{"error" => %{"code" => "invalid_request"}} =
             build_conn()
             |> get("/api/v1/projects/#{project.id}/tasks?list_id=not-an-id")
             |> json_response(400)

    assert %{"error" => %{"code" => "invalid_request"}} =
             build_conn()
             |> get("/api/v1/projects/#{project.id}/tasks/not-an-id")
             |> json_response(400)

    assert %{"error" => %{"code" => "invalid_request"}} =
             build_conn()
             |> patch("/api/v1/projects/#{project.id}/tasks/0", %{
               "task" => %{"title" => "Nope"}
             })
             |> json_response(400)
  end

  test "POST move rejects malformed destination IDs", %{conn: conn} do
    project = project_fixture(%{})
    task = task_fixture(project)

    conn =
      post(conn, "/api/v1/projects/#{project.id}/tasks/#{task.id}/move", %{
        "destination" => %{"list_id" => "not-an-id"}
      })

    assert %{"error" => %{"code" => "invalid_request"}} = json_response(conn, 400)
  end

  test "Task updates keep list ownership immutable", %{conn: conn} do
    project = project_fixture(%{})
    list = list_fixture(project)
    task = task_fixture(project)

    conn =
      patch(conn, "/api/v1/projects/#{project.id}/tasks/#{task.id}", %{
        "task" => %{"title" => "Still root", "list_id" => list.id, "project_id" => 999_999}
      })

    assert %{"data" => %{"list_id" => nil, "project_id" => project_id}} = json_response(conn, 200)
    assert project_id == project.id
  end

  test "Task status lifecycle values use underscore JSON tokens", %{conn: conn} do
    project = project_fixture(%{})
    task = task_fixture(project)

    conn =
      patch(conn, "/api/v1/projects/#{project.id}/tasks/#{task.id}", %{
        "task" => %{"status" => "will_not_do"}
      })

    assert %{"data" => %{"status" => "will_not_do"}} = json_response(conn, 200)
    assert Tasks.get_task_for_project(project, task.id).status == :will_not_do
  end

  test "Task representations include a nullable parent_task_id", %{conn: conn} do
    project = project_fixture(%{})
    parent = task_fixture(project, %{title: "Parent"})
    child = task_fixture(project, %{title: "Child"}, parent: parent)

    assert %{"data" => representations} =
             conn
             |> get("/api/v1/projects/#{project.id}/tasks")
             |> json_response(200)

    assert Enum.all?(representations, &Map.has_key?(&1, "parent_task_id"))
    assert Enum.find(representations, &(&1["id"] == parent.id))["parent_task_id"] == nil

    assert %{"data" => %{"parent_task_id" => parent_id}} =
             build_conn()
             |> get("/api/v1/projects/#{project.id}/tasks/#{child.id}")
             |> json_response(200)

    assert parent_id == parent.id
  end

  test "POST creates a Task with a same-Project parent", %{conn: conn} do
    project = project_fixture(%{})
    parent = task_fixture(project, %{title: "Parent"})

    conn =
      post(conn, "/api/v1/projects/#{project.id}/tasks", %{
        "task" => %{"title" => "Child", "parent_task_id" => parent.id}
      })

    assert %{"data" => %{"id" => child_id, "parent_task_id" => parent_id}} =
             json_response(conn, 201)

    assert parent_id == parent.id
    assert Tasks.get_task_for_project(project, child_id).parent_task_id == parent.id
  end

  test "POST treats omitted and null parent_task_id as project roots", %{conn: conn} do
    project = project_fixture(%{})

    assert %{"data" => %{"parent_task_id" => nil}} =
             conn
             |> post("/api/v1/projects/#{project.id}/tasks", %{
               "task" => %{"title" => "Omitted parent"}
             })
             |> json_response(201)

    assert %{"data" => %{"parent_task_id" => nil}} =
             build_conn()
             |> post("/api/v1/projects/#{project.id}/tasks", %{
               "task" => %{"title" => "Null parent", "parent_task_id" => nil}
             })
             |> json_response(201)
  end

  test "POST rejects malformed or foreign parent_task_id", %{conn: conn} do
    project = project_fixture(%{})
    other_project = project_fixture(%{})
    foreign_parent = task_fixture(other_project)

    assert %{"error" => %{"code" => "invalid_request"}} =
             conn
             |> post("/api/v1/projects/#{project.id}/tasks", %{
               "task" => %{"title" => "Malformed", "parent_task_id" => "not-an-id"}
             })
             |> json_response(400)

    assert %{"error" => %{"code" => "not_found"}} =
             build_conn()
             |> post("/api/v1/projects/#{project.id}/tasks", %{
               "task" => %{"title" => "Foreign", "parent_task_id" => foreign_parent.id}
             })
             |> json_response(404)
  end

  test "PATCH omitting parent_task_id leaves it unchanged and null clears it", %{conn: conn} do
    project = project_fixture(%{})
    first_parent = task_fixture(project, %{title: "First parent"})
    second_parent = task_fixture(project, %{title: "Second parent"})
    task = task_fixture(project, %{title: "Child"}, parent: first_parent)

    assert %{"data" => %{"parent_task_id" => first_parent_id}} =
             conn
             |> patch("/api/v1/projects/#{project.id}/tasks/#{task.id}", %{
               "task" => %{"title" => "Still child"}
             })
             |> json_response(200)

    assert first_parent_id == first_parent.id

    assert %{"data" => %{"parent_task_id" => second_parent_id}} =
             build_conn()
             |> patch("/api/v1/projects/#{project.id}/tasks/#{task.id}", %{
               "task" => %{"parent_task_id" => second_parent.id}
             })
             |> json_response(200)

    assert second_parent_id == second_parent.id

    assert %{"data" => %{"parent_task_id" => nil}} =
             build_conn()
             |> patch("/api/v1/projects/#{project.id}/tasks/#{task.id}", %{
               "task" => %{"parent_task_id" => nil}
             })
             |> json_response(200)
  end

  test "PATCH rejects malformed or foreign parent_task_id", %{conn: conn} do
    project = project_fixture(%{})
    other_project = project_fixture(%{})
    task = task_fixture(project)
    foreign_parent = task_fixture(other_project)

    assert %{"error" => %{"code" => "invalid_request"}} =
             conn
             |> patch("/api/v1/projects/#{project.id}/tasks/#{task.id}", %{
               "task" => %{"parent_task_id" => "not-an-id"}
             })
             |> json_response(400)

    assert %{"error" => %{"code" => "not_found"}} =
             build_conn()
             |> patch("/api/v1/projects/#{project.id}/tasks/#{task.id}", %{
               "task" => %{"parent_task_id" => foreign_parent.id}
             })
             |> json_response(404)
  end

  test "PATCH reports self-parent and cycle errors on parent_task_id", %{conn: conn} do
    project = project_fixture(%{})
    parent = task_fixture(project, %{title: "Parent"})
    child = task_fixture(project, %{title: "Child"}, parent: parent)

    assert %{
             "error" => %{
               "code" => "validation_failed",
               "fields" => %{"parent_task_id" => [_message]}
             }
           } =
             conn
             |> patch("/api/v1/projects/#{project.id}/tasks/#{child.id}", %{
               "task" => %{"parent_task_id" => child.id}
             })
             |> json_response(422)

    assert %{
             "error" => %{
               "code" => "validation_failed",
               "fields" => %{"parent_task_id" => [_message]}
             }
           } =
             build_conn()
             |> patch("/api/v1/projects/#{project.id}/tasks/#{parent.id}", %{
               "task" => %{"parent_task_id" => child.id}
             })
             |> json_response(422)
  end

  test "PATCH applies ordinary and parent changes atomically on validation failure", %{conn: conn} do
    project = project_fixture(%{})
    parent = task_fixture(project, %{title: "Parent"})
    child = task_fixture(project, %{title: "Before"}, parent: parent)

    conn =
      patch(conn, "/api/v1/projects/#{project.id}/tasks/#{child.id}", %{
        "task" => %{"title" => "Should not persist", "parent_task_id" => child.id}
      })

    assert %{
             "error" => %{
               "code" => "validation_failed",
               "fields" => %{"parent_task_id" => [_message]}
             }
           } = json_response(conn, 422)

    persisted = Tasks.get_task_for_project(project, child.id)
    assert persisted.title == "Before"
    assert persisted.parent_task_id == parent.id
  end

  test "GET hierarchy returns the selected Task tree in stable sibling order", %{conn: conn} do
    project = project_fixture(%{})
    root = task_fixture(project, %{title: "Root"})
    first = task_fixture(project, %{title: "First"}, parent: root)
    second = task_fixture(project, %{title: "Second"}, parent: root)
    selected = task_fixture(project, %{title: "Selected"}, parent: first)

    conn = get(conn, "/api/v1/projects/#{project.id}/tasks/#{selected.id}/hierarchy")

    assert %{
             "data" => %{
               "selected_task_id" => selected_id,
               "root" => %{
                 "task" => %{"id" => root_id, "parent_task_id" => root_parent_id},
                 "children" => [
                   %{
                     "task" => %{"id" => first_id, "parent_task_id" => first_parent_id},
                     "children" => [
                       %{
                         "task" => %{
                           "id" => selected_node_id,
                           "parent_task_id" => selected_parent_id
                         },
                         "children" => []
                       }
                     ]
                   },
                   %{
                     "task" => %{"id" => second_id, "parent_task_id" => second_parent_id},
                     "children" => []
                   }
                 ]
               }
             }
           } = json_response(conn, 200)

    assert selected_id == selected.id
    assert root_id == root.id
    assert first_id == first.id
    assert second_id == second.id
    assert root_parent_id == nil
    assert first_parent_id == root.id
    assert selected_node_id == selected.id
    assert selected_parent_id == first.id
    assert second_parent_id == root.id
  end

  test "GET hierarchy rejects malformed, stale, and foreign Task IDs", %{conn: conn} do
    project = project_fixture(%{})
    other_project = project_fixture(%{})
    foreign_task = task_fixture(other_project)
    stale_task = task_fixture(project)
    Repo.delete!(stale_task)

    assert %{"error" => %{"code" => "invalid_request"}} =
             conn
             |> get("/api/v1/projects/#{project.id}/tasks/not-an-id/hierarchy")
             |> json_response(400)

    assert %{"error" => %{"code" => "not_found"}} =
             build_conn()
             |> get("/api/v1/projects/#{project.id}/tasks/#{foreign_task.id}/hierarchy")
             |> json_response(404)

    assert %{"error" => %{"code" => "not_found"}} =
             build_conn()
             |> get("/api/v1/projects/#{project.id}/tasks/#{stale_task.id}/hierarchy")
             |> json_response(404)
  end
end
