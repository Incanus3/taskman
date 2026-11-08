defmodule Taskman.Tasks.Writes do
  @moduledoc false

  alias Taskman.{Accounts, Repo}
  alias Taskman.Accounts.User
  alias Taskman.Tasks.{Activity, Mutations, Task}

  @type affected_task :: Activity.affected_task()
  @type changed_fields :: Mutations.changed_fields()
  @type operation(result) ::
          (User.t() -> {:ok, result, [affected_task()]} | {:error, term()})
  @type mutation :: (-> Mutations.result())

  @doc """
  Validates and locks the actor, then runs the callback in an owned transaction.

  The callback receives the persisted eligible account and returns a result plus
  affected Project/Task ID pairs, or an error that rolls back the transaction.
  Raises `ArgumentError` when called inside an existing transaction.
  """
  @spec run(term(), operation(result)) ::
          {:ok, {result, [affected_task()]}} | {:error, term()}
        when result: term()
  def run(actor, operation_fun) when is_function(operation_fun, 1) do
    if Repo.in_transaction?() do
      raise ArgumentError, "Task writes must be called outside an existing transaction"
    end

    Repo.transaction(fn ->
      with {:ok, persisted_actor} <- Accounts.lock_eligible_user(actor),
           {:ok, result, affected_pairs} <- operation_fun.(persisted_actor) do
        {result, affected_pairs}
      else
        {:error, reason} -> Repo.rollback(reason)
      end
    end)
  end

  @doc """
  Runs a Task mutation and records its activity inside `run/2`'s actor-locked transaction.

  The zero-argument callback returns the saved Task, changed field names, and actual
  previous Task (`nil` for creation), or an error. The actor must be the account
  validated by `run/2`. Raises `ArgumentError` outside an existing transaction.
  """
  @spec task(User.t(), mutation()) ::
          {:ok, {Task.t(), changed_fields()}, [affected_task()]} | {:error, term()}
  def task(actor, mutation_fun) when is_function(mutation_fun, 0) do
    unless Repo.in_transaction?() do
      raise ArgumentError, "internal Task writes require an owned transaction"
    end

    with {:ok, task, fields, before_task} <- mutation_fun.(),
         {:ok, affected} <- Activity.record_task(actor, before_task, task, fields) do
      {:ok, {task, fields}, affected}
    end
  end
end
