defmodule Taskman.Tasks.GraphLock do
  @moduledoc """
  Serializes writes to Task blocking links, parentage, and status.

  Call within the write transaction, before locking a Project or reading graph state.
  """

  alias Taskman.Repo

  @task_graph_advisory_lock_key {724_150, 1}

  @spec acquire!() :: :ok
  def acquire! do
    unless Repo.in_transaction?() do
      raise ArgumentError, "Task graph lock requires a transaction"
    end

    {first_key, second_key} = @task_graph_advisory_lock_key

    Ecto.Adapters.SQL.query!(
      Repo,
      "SELECT pg_advisory_xact_lock($1, $2)",
      [first_key, second_key]
    )

    :ok
  end
end
