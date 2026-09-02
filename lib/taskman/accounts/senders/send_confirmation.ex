defmodule Taskman.Accounts.Senders.SendConfirmation do
  use AshAuthentication.Sender

  alias Ash.Changeset
  alias Taskman.Accounts
  alias Taskman.Accounts.Emails

  @impl AshAuthentication.Sender
  def send(_user, token, opts) do
    changeset = Keyword.fetch!(opts, :changeset)
    email = changeset |> Changeset.get_attribute(:email) |> to_string()
    result = Emails.deliver_email_change_confirmation(email, token)
    Accounts.record_delivery_result(:email_change, token, result)
    Accounts.log_delivery_failure(result)
    :ok
  end
end
