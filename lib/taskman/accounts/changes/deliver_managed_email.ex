defmodule Taskman.Accounts.Changes.DeliverManagedEmail do
  @moduledoc false

  use Ash.Resource.Change

  alias Taskman.Accounts.{Emails, User}

  @impl true
  def change(changeset, _opts, _context) do
    Ash.Changeset.after_transaction(changeset, fn _changeset, result ->
      deliver_email(result)
    end)
  end

  defp deliver_email({:ok, %User{} = user} = result) do
    case Ash.Resource.get_metadata(user, :managed_email_delivery) do
      {:setup, email, token} ->
        deliver(result, user, fn -> Emails.deliver_invitation(email, token) end)

      {:email_change, email, token} ->
        deliver(result, user, fn -> Emails.deliver_email_change_confirmation(email, token) end)

      nil ->
        result
    end
  end

  defp deliver_email(result), do: result

  defp deliver(result, user, delivery) do
    case delivery.() do
      :ok ->
        result

      {:error, _reason} = error ->
        Taskman.Accounts.log_delivery_failure(error)
        {:error, {:delivery_failed, user}}
    end
  end
end
