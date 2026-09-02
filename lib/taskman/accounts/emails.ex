defmodule Taskman.Accounts.Emails do
  import Swoosh.Email

  alias Taskman.Mailer

  @spec deliver_invitation(String.t(), String.t()) :: :ok | {:error, :delivery_failed}
  def deliver_invitation(email, token) do
    deliver(
      email,
      "Set up your Taskman account",
      setup_url(token),
      "7 days",
      "Set up your account"
    )
  end

  @spec deliver_email_change_confirmation(String.t(), String.t()) ::
          :ok | {:error, :delivery_failed}
  def deliver_email_change_confirmation(email, token) do
    deliver(
      email,
      "Confirm your new Taskman email address",
      confirmation_url(token),
      "24 hours",
      "Confirm your new email address"
    )
  end

  @spec deliver_password_reset(String.t(), String.t()) :: :ok | {:error, :delivery_failed}
  def deliver_password_reset(email, token) do
    deliver(
      email,
      "Reset your Taskman password",
      reset_url(token),
      "1 hour",
      "Reset your password"
    )
  end

  defp deliver(recipient, subject, url, expiry, heading) do
    text_body = """
    #{heading}

    Open this secure link: #{url}

    This link expires in #{expiry}. If you did not request this, you can ignore this email.
    """

    html_body = """
    <h1>#{heading}</h1>
    <p><a href="#{url}">Continue securely</a></p>
    <p>This link expires in #{expiry}. If you did not request this, you can ignore this email.</p>
    """

    new()
    |> from(Application.fetch_env!(:taskman, :mail_from))
    |> to(recipient)
    |> subject(subject)
    |> text_body(text_body)
    |> html_body(html_body)
    |> Mailer.deliver_transactional()
    |> case do
      {:ok, _result} -> :ok
      {:error, _reason} -> {:error, :delivery_failed}
    end
  rescue
    _exception -> {:error, :delivery_failed}
  end

  defp setup_url(token), do: url("setup", token)
  defp confirmation_url(token), do: url("confirm-email", token)
  defp reset_url(token), do: url("reset-password", token)

  defp url(path, token) do
    base = Application.fetch_env!(:taskman, :public_url)
    encoded_token = URI.encode(token, &URI.char_unreserved?/1)
    "#{String.trim_trailing(base, "/")}/#{path}/#{encoded_token}"
  end
end
