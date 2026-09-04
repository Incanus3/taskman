defmodule Taskman.Accounts.User.Changes.ReportInvitationDelivery do
  @moduledoc false

  use Ash.Resource.Change

  alias Ash.Error.Changes.InvalidChanges
  alias Taskman.Accounts.{Delivery, User}

  @message "Invitation delivery failed. " <>
             "The account remains pending; open the user details to resend the invitation."

  @impl true
  def change(changeset, _opts, _context) do
    Ash.Changeset.after_transaction(changeset, fn _changeset, result ->
      report_delivery(result)
    end)
  end

  defp report_delivery({:ok, %User{}} = result) do
    case Delivery.take_unmanaged_result(:setup) do
      :managed ->
        result

      {:recorded, :ok, _token} ->
        result

      {:recorded, {:error, _reason} = error, _token} ->
        Delivery.log_failure(error)
        delivery_error()

      :missing ->
        Delivery.log_failure({:error, :missing_delivery_result})
        delivery_error()
    end
  end

  defp report_delivery(result), do: result

  defp delivery_error do
    {:error, InvalidChanges.exception(fields: [], message: @message)}
  end
end
