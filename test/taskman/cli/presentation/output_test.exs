defmodule Taskman.CLI.Presentation.OutputTest do
  use ExUnit.Case, async: true

  import Taskman.ProjectsFixtures, only: [project_response_fixture: 1]

  alias Taskman.CLI.Presentation.Output

  test "JSON success output is one API data envelope and a trailing newline" do
    data = [project_response_fixture(%{"id" => 1, "name" => "One"})]

    assert Output.success({:projects, :list}, data, true) ==
             Jason.encode!(%{data: data}) <> "\n"
  end

  test "readable Project collections retain identifying fields" do
    data = [project_response_fixture(%{"id" => 1, "name" => "One"})]

    assert Output.success({:projects, :list}, data, false) ==
             "ID\tNAME\tDESCRIPTION\tICON\tCOLOR\n1\tOne\t\tbriefcase\t#6366F1\n"
  end

  test "readable Project members use one labelled line per public field" do
    data = project_response_fixture(%{"id" => 1, "name" => "One"})

    assert Output.success({:projects, :show}, data, false) ==
             "ID: 1\nNAME: One\nDESCRIPTION: \nICON: briefcase\nCOLOR: #6366F1\n"
  end

  test "readable List members retain List fields when ID and name are present" do
    data = %{"id" => 11, "name" => "Planning", "parent_list_id" => nil}

    assert Output.success({:lists, :show}, data, false) ==
             "ID: 11\nNAME: Planning\nPARENT LIST ID: —\n"
  end

  test "readable Task collections include each parent Task ID" do
    data = [
      %{
        "id" => 51,
        "title" => "Implement parser",
        "parent_task_id" => 42,
        "status" => "pending",
        "priority" => "none",
        "location" => %{"path" => ["Delivery"]},
        "due_at" => nil
      }
    ]

    assert Output.success({:tasks, :list}, data, false) ==
             "ID\tTITLE\tPARENT\tSTATUS\tPRIORITY\tLOCATION\tDUE\n" <>
               "51\tImplement parser\t42\tpending\tnone\tDelivery\t—\n"
  end

  test "readable hierarchy output keeps API order and marks the selected Task" do
    data = %{
      "selected_task_id" => 51,
      "root" => %{
        "task" => %{"id" => 42, "title" => "Build import"},
        "children" => [
          %{
            "task" => %{"id" => 51, "title" => "Implement parser"},
            "children" => []
          }
        ]
      }
    }

    assert Output.success({:tasks, :hierarchy}, data, false) ==
             "42  Build import\n└─ 51  Implement parser  [selected]\n"
  end

  test "JSON errors preserve the API envelope and trailing newline" do
    envelope = %{error: %{code: "not_found", message: "Resource not found"}}

    assert Output.error(envelope, true) == Jason.encode!(envelope) <> "\n"
  end

  test "JSON concurrent update errors preserve fields and trailing newline" do
    envelope = %{
      "error" => %{
        "code" => "concurrent_update",
        "message" => "Task changed concurrently",
        "fields" => %{
          "status" => ["changed concurrently"],
          "title" => ["changed concurrently"]
        }
      }
    }

    assert Output.error(envelope, true) == Jason.encode!(envelope) <> "\n"
  end

  test "readable concurrent update errors render each conflicting field" do
    envelope = %{
      "error" => %{
        "code" => "concurrent_update",
        "message" => "Task changed concurrently",
        "fields" => %{
          "status" => ["changed concurrently"],
          "title" => ["changed concurrently"]
        }
      }
    }

    assert Output.error(envelope, false) ==
             "Error: Task changed concurrently (concurrent_update)\n" <>
               "STATUS: changed concurrently\nTITLE: changed concurrently\n"
  end

  test "readable errors contain a concise diagnostic" do
    assert Output.error(%{"error" => %{"code" => "not_found", "message" => "Missing"}}, false) ==
             "Error: Missing (not_found)\n"
  end

  test "readable Project output includes identity metadata" do
    data =
      project_response_fixture(%{
        "description" => "Delivery",
        "icon" => "rocket-launch",
        "color" => "#ABCDEF"
      })

    assert Output.success({:projects, :show}, data, false) ==
             "ID: 7\nNAME: CLI\nDESCRIPTION: Delivery\nICON: rocket-launch\nCOLOR: #ABCDEF\n"

    assert Output.success({:projects, :list}, [data], false) ==
             "ID\tNAME\tDESCRIPTION\tICON\tCOLOR\n7\tCLI\tDelivery\trocket-launch\t#ABCDEF\n"

    assert Jason.decode!(Output.success({:projects, :update}, data, true)) == %{"data" => data}
  end
end
