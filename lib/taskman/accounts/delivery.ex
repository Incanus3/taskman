defmodule Taskman.Accounts.Delivery do
  @moduledoc false

  require Logger

  @delivery_result_key {Taskman.Accounts, :delivery_result}

  @doc false
  @spec record_result(atom(), String.t(), :ok | {:error, term()}) :: :ok
  def record_result(purpose, token, result) do
    Process.put(@delivery_result_key, {purpose, result, token})
    :ok
  end

  @doc false
  @spec with_result(atom(), (-> term()), (term(), term(), term() -> term())) :: term()
  def with_result(purpose, operation, handle)
      when is_atom(purpose) and is_function(operation, 0) do
    Process.delete(@delivery_result_key)
    result = operation.()

    case Process.delete(@delivery_result_key) do
      {^purpose, delivery_result, token} -> handle.(result, delivery_result, token)
      nil -> handle.(result, nil, nil)
    end
  end

  @doc false
  @spec log_failure(:ok | {:error, term()}) :: :ok
  def log_failure({:error, {:delivery_failed, delivery_class}}) when is_atom(delivery_class) do
    Logger.warning("Transactional email delivery failed (class=#{delivery_class})")
    :ok
  end

  def log_failure({:error, _reason}) do
    Logger.warning("Transactional email delivery failed (class=unknown)")
    :ok
  end

  def log_failure(:ok), do: :ok
end
