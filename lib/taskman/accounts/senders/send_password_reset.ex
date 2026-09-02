defmodule Taskman.Accounts.Senders.SendPasswordReset do
  use AshAuthentication.Sender

  alias Taskman.Accounts
  alias Taskman.Accounts.Emails

  @impl AshAuthentication.Sender
  def send(user, token, _opts) do
    result = Emails.deliver_password_reset(to_string(user.email), token)
    Accounts.record_delivery_result(:password_reset, token, result)
    Accounts.log_delivery_failure(result)
    :ok
  end
end
