defmodule Taskman.Accounts.Delivery do
  @moduledoc false

  require Logger

  @delivery_capture_key {Taskman.Accounts, :delivery_capture}
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
    previous_capture = Process.get(@delivery_capture_key)
    Process.delete(@delivery_result_key)
    Process.put(@delivery_capture_key, purpose)

    try do
      result = operation.()

      case Process.delete(@delivery_result_key) do
        {^purpose, delivery_result, token} -> handle.(result, delivery_result, token)
        nil -> handle.(result, nil, nil)
      end
    after
      Process.delete(@delivery_result_key)
      restore_capture(previous_capture)
    end
  end

  @doc false
  @spec take_unmanaged_result(atom()) ::
          :managed | {:recorded, :ok | {:error, term()}, String.t()} | :missing
  def take_unmanaged_result(purpose) do
    case {Process.get(@delivery_capture_key), Process.get(@delivery_result_key)} do
      {^purpose, _result} ->
        :managed

      {_capture, {^purpose, delivery_result, token}} ->
        Process.delete(@delivery_result_key)
        {:recorded, delivery_result, token}

      {_capture, _result} ->
        :missing
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

  defp restore_capture(nil), do: Process.delete(@delivery_capture_key)
  defp restore_capture(previous_capture), do: Process.put(@delivery_capture_key, previous_capture)
end
