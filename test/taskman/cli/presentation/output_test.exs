defmodule Taskman.CLI.Presentation.OutputTest do
  use ExUnit.Case, async: true

  alias Taskman.CLI.Presentation.Output

  test "JSON success output is one API data envelope and a trailing newline" do
    data = [%{"id" => 1, "name" => "One", "primary_directory" => "/tmp"}]

    assert Output.success({:projects, :list}, data, true) ==
             Jason.encode!(%{data: data}) <> "\n"
  end

  test "readable Project collections retain identifying fields" do
    data = [%{"id" => 1, "name" => "One", "primary_directory" => "/tmp"}]

    assert Output.success({:projects, :list}, data, false) ==
             "ID\tNAME\tPRIMARY DIRECTORY\n1\tOne\t/tmp\n"
  end

  test "readable Project members use one labelled line per public field" do
    data = %{"id" => 1, "name" => "One", "primary_directory" => "/tmp"}

    assert Output.success({:projects, :show}, data, false) ==
             "ID: 1\nNAME: One\nPRIMARY DIRECTORY: /tmp\n"
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
end
