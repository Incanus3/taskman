defmodule Taskman.CLI.ClientTest do
  use ExUnit.Case, async: true

  alias Taskman.CLI.Client

  setup {Req.Test, :verify_on_exit!}

  test "returns the data from a successful JSON envelope" do
    Req.Test.expect(TaskmanCLIClient, fn conn ->
      assert conn.method == "GET"
      assert conn.request_path == "/api/v1/projects"

      Req.Test.json(conn, %{
        data: [%{id: 1, name: "One", primary_directory: "/tmp"}]
      })
    end)

    assert {:ok, [%{"id" => 1, "name" => "One", "primary_directory" => "/tmp"}]} =
             Client.request(:get, "/api/v1/projects", [],
               req_options: [plug: {Req.Test, TaskmanCLIClient}]
             )
  end

  test "maps public API client errors to exit status 3" do
    for {status, code} <- [
          {400, "invalid_request"},
          {404, "not_found"},
          {409, "unchanged_location"},
          {422, "validation_failed"}
        ] do
      Req.Test.expect(TaskmanCLIClient, fn conn ->
        conn
        |> Plug.Conn.put_status(status)
        |> Req.Test.json(%{error: %{code: code, message: "failure"}})
      end)

      assert {:error, 3, %{"error" => %{"code" => ^code, "message" => "failure"}}} =
               Client.request(:get, "/api/v1/projects", [],
                 req_options: [plug: {Req.Test, TaskmanCLIClient}]
               )
    end
  end

  test "maps mismatched API error status and code to invalid_response status 5" do
    for {status, code} <- [
          {400, "internal_error"},
          {404, "invalid_request"},
          {409, "not_found"},
          {422, "unchanged_location"},
          {500, "validation_failed"}
        ] do
      Req.Test.expect(TaskmanCLIClient, fn conn ->
        conn
        |> Plug.Conn.put_status(status)
        |> Req.Test.json(%{error: %{code: code, message: "failure"}})
      end)

      assert {:error, 5, %{"error" => %{"code" => "invalid_response"}}} =
               Client.request(:get, "/api/v1/projects", [],
                 req_options: [plug: {Req.Test, TaskmanCLIClient}]
               )
    end
  end

  test "maps unknown API error codes to invalid_response status 5" do
    for status <- [400, 404, 409, 422, 500] do
      Req.Test.expect(TaskmanCLIClient, fn conn ->
        conn
        |> Plug.Conn.put_status(status)
        |> Req.Test.json(%{error: %{code: "arbitrary_code", message: "failure"}})
      end)

      assert {:error, 5, %{"error" => %{"code" => "invalid_response"}}} =
               Client.request(:get, "/api/v1/projects", [],
                 req_options: [plug: {Req.Test, TaskmanCLIClient}]
               )
    end
  end

  test "maps unsupported HTTP statuses with known API codes to invalid_response status 5" do
    for {status, code} <- [{418, "invalid_request"}, {503, "internal_error"}] do
      Req.Test.expect(TaskmanCLIClient, fn conn ->
        conn
        |> Plug.Conn.put_status(status)
        |> Req.Test.json(%{error: %{code: code, message: "failure"}})
      end)

      assert {:error, 5, %{"error" => %{"code" => "invalid_response"}}} =
               Client.request(:get, "/api/v1/projects", [],
                 req_options: [plug: {Req.Test, TaskmanCLIClient}]
               )
    end
  end

  test "maps transport refusal and timeout to exit status 4" do
    for reason <- [:econnrefused, :timeout] do
      Req.Test.expect(TaskmanCLIClient, fn conn -> Req.Test.transport_error(conn, reason) end)

      assert {:error, 4,
              %{
                "error" => %{
                  "code" => "connection_failed",
                  "message" => message
                }
              }} =
               Client.request(:get, "/api/v1/projects", [],
                 req_options: [plug: {Req.Test, TaskmanCLIClient}]
               )

      assert message =~ "http://localhost:4000"
      assert message =~ "taskman agent onboarding"
      assert message =~ "mix phx.server"
    end
  end

  test "maps server errors to exit status 5 while preserving the API envelope" do
    Req.Test.expect(TaskmanCLIClient, fn conn ->
      conn
      |> Plug.Conn.put_status(500)
      |> Req.Test.json(%{error: %{code: "internal_error", message: "Internal Server Error"}})
    end)

    assert {:error, 5,
            %{"error" => %{"code" => "internal_error", "message" => "Internal Server Error"}}} =
             Client.request(:get, "/api/v1/projects", [],
               req_options: [plug: {Req.Test, TaskmanCLIClient}]
             )
  end

  test "maps non-JSON and malformed success responses to invalid_response status 5" do
    Req.Test.expect(TaskmanCLIClient, fn conn -> Req.Test.text(conn, "not JSON") end)

    assert {:error, 5, %{"error" => %{"code" => "invalid_response"}}} =
             Client.request(:get, "/api/v1/projects", [],
               req_options: [plug: {Req.Test, TaskmanCLIClient}]
             )

    Req.Test.expect(TaskmanCLIClient, fn conn -> Req.Test.json(conn, %{other: []}) end)

    assert {:error, 5, %{"error" => %{"code" => "invalid_response"}}} =
             Client.request(:get, "/api/v1/projects", [],
               req_options: [plug: {Req.Test, TaskmanCLIClient}]
             )
  end

  test "maps malformed API error envelopes to invalid_response status 5" do
    Req.Test.expect(TaskmanCLIClient, fn conn ->
      conn
      |> Plug.Conn.put_status(400)
      |> Req.Test.json(%{data: []})
    end)

    assert {:error, 5, %{"error" => %{"code" => "invalid_response"}}} =
             Client.request(:get, "/api/v1/projects", [],
               req_options: [plug: {Req.Test, TaskmanCLIClient}]
             )
  end

  test "requires every Task response to include a nullable positive parent ID" do
    for parent_task_id <- [nil, 41] do
      Req.Test.expect(TaskmanCLIClient, fn conn ->
        Req.Test.json(conn, %{data: task_response(%{parent_task_id: parent_task_id})})
      end)

      assert {:ok, %{"parent_task_id" => ^parent_task_id}} =
               Client.request(
                 :get,
                 "/api/v1/projects/7/tasks/42",
                 [],
                 [req_options: [plug: {Req.Test, TaskmanCLIClient}]],
                 {:member, :task}
               )
    end

    for malformed <- [
          task_response(%{}) |> Map.delete(:parent_task_id),
          task_response(%{parent_task_id: 0}),
          task_response(%{parent_task_id: "41"})
        ] do
      Req.Test.expect(TaskmanCLIClient, fn conn ->
        Req.Test.json(conn, %{data: malformed})
      end)

      assert {:error, 5, %{"error" => %{"code" => "invalid_response"}}} =
               Client.request(
                 :get,
                 "/api/v1/projects/7/tasks/42",
                 [],
                 [req_options: [plug: {Req.Test, TaskmanCLIClient}]],
                 {:member, :task}
               )
    end
  end

  test "validates every hierarchy node recursively" do
    valid = hierarchy_response() |> Jason.encode!() |> Jason.decode!()

    Req.Test.expect(TaskmanCLIClient, fn conn -> Req.Test.json(conn, %{data: valid}) end)

    assert {:ok, ^valid} =
             Client.request(
               :get,
               "/api/v1/projects/7/tasks/51/hierarchy",
               [],
               [req_options: [plug: {Req.Test, TaskmanCLIClient}]],
               :hierarchy
             )

    malformed =
      put_in(valid, ["root", "children", Access.at(0)], %{"task" => task_response(%{id: 51})})

    Req.Test.expect(TaskmanCLIClient, fn conn -> Req.Test.json(conn, %{data: malformed}) end)

    assert {:error, 5, %{"error" => %{"code" => "invalid_response"}}} =
             Client.request(
               :get,
               "/api/v1/projects/7/tasks/51/hierarchy",
               [],
               [req_options: [plug: {Req.Test, TaskmanCLIClient}]],
               :hierarchy
             )
  end

  defp task_response(overrides) do
    Map.merge(
      %{
        id: 42,
        project_id: 7,
        list_id: 11,
        parent_task_id: nil,
        title: "Task",
        description: "",
        status: "pending",
        priority: "none",
        due_at: nil,
        location: %{kind: "list", list_id: 11, path: ["Planning"]}
      },
      overrides
    )
  end

  defp hierarchy_response do
    %{
      "selected_task_id" => 51,
      "root" => %{
        "task" => task_response(%{id: 42, title: "Build import"}),
        "children" => [
          %{
            "task" => task_response(%{id: 51, parent_task_id: 42, title: "Implement parser"}),
            "children" => []
          }
        ]
      }
    }
  end
end
