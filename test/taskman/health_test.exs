defmodule Taskman.HealthTest do
  use ExUnit.Case, async: true

  import ExUnit.CaptureLog

  alias Taskman.Health

  test "reports ready only for a successful SELECT 1" do
    assert :ready =
             Health.check(
               query: fn "SELECT 1", [], [timeout: 750] ->
                 {:ok, %{}}
               end
             )
  end

  for result <- [{:error, :timeout}, {:error, :closed}] do
    test "maps #{inspect(result)} to unavailable" do
      result = unquote(Macro.escape(result))
      assert :unavailable = Health.check(query: fn _, _, _ -> result end)
    end
  end

  test "maps an unexpected query result to unavailable" do
    assert :unavailable = Health.check(query: fn _, _, _ -> :ok end)
  end

  test "maps query exceptions to unavailable without logging exception details" do
    log =
      capture_log(fn ->
        assert :unavailable =
                 Health.check(query: fn _, _, _ -> raise "database secret detail" end)
      end)

    assert log =~ "Taskman health check failed"
    refute log =~ "database secret detail"
  end

  test "maps thrown query failures to unavailable" do
    capture_log(fn ->
      assert :unavailable = Health.check(query: fn _, _, _ -> throw(:query_failed) end)
    end)
  end

  test "maps exited query failures to unavailable" do
    capture_log(fn ->
      assert :unavailable = Health.check(query: fn _, _, _ -> exit(:query_failed) end)
    end)
  end

  test "passes a configured timeout to the query" do
    test_process = self()

    assert :ready =
             Health.check(
               timeout: 25,
               query: fn sql, params, options ->
                 send(test_process, {:query, sql, params, options})
                 {:ok, %{}}
               end
             )

    assert_receive {:query, "SELECT 1", [], [timeout: 25]}
  end
end
