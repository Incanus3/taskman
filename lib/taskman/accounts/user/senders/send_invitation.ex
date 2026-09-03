defmodule Taskman.Accounts.User.Senders.SendInvitation do
  use AshAuthentication.Sender

  alias Taskman.Accounts
  alias Taskman.Accounts.Emails

  @impl AshAuthentication.Sender
  def send(user, token, _opts) do
    result = Emails.deliver_invitation(to_string(user.email), token)
    Accounts.record_delivery_result(:setup, token, result)
    :ok
  end
end
