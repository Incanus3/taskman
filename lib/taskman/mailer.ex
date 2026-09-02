defmodule Taskman.Mailer do
  use Swoosh.Mailer, otp_app: :taskman

  @spec deliver_transactional(Swoosh.Email.t()) :: {:ok, term()} | {:error, term()}
  def deliver_transactional(email) do
    Application.fetch_env!(:taskman, :mailer_delivery).deliver(email)
  end
end
