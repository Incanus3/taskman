defmodule Taskman.Accounts.User.Senders.SendPasswordReset do
  @moduledoc "Delivers password reset tokens."

  use AshAuthentication.Sender

  alias Taskman.Accounts
  alias Taskman.Accounts.Emails

  @impl AshAuthentication.Sender
  def send(user, token, _opts) do
    result = Emails.deliver_password_reset(to_string(user.email), token)
    Accounts.record_delivery_result(:password_reset, token, result)
    :ok
  end
end
