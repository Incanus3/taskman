defmodule Taskman.CredentialPrompts do
  @moduledoc false

  @email_pattern ~r/^[^\s@]+@[^\s@]+\.[^\s@]+$/u
  @password_length 8..128

  @spec prompt_for_email(module()) :: {:ok, String.t()} | {:error, :input_unavailable}
  def prompt_for_email(terminal) when is_atom(terminal) do
    case terminal.prompt("Email: ") do
      email when is_binary(email) -> validate_or_retry_email(String.trim(email), terminal)
      _result -> {:error, :input_unavailable}
    end
  rescue
    _error -> {:error, :input_unavailable}
  catch
    _kind, _reason -> {:error, :input_unavailable}
  end

  def prompt_for_email(_terminal), do: {:error, :input_unavailable}

  @spec prompt_for_password(module()) :: {:ok, String.t()} | {:error, :input_unavailable}
  def prompt_for_password(terminal) when is_atom(terminal) do
    with password when is_binary(password) <- terminal.prompt_secret("Password: "),
         confirmation when is_binary(confirmation) <-
           terminal.prompt_secret("Confirm password: ") do
      validate_or_retry_password(password, confirmation, terminal)
    else
      _result -> {:error, :input_unavailable}
    end
  rescue
    _error -> {:error, :input_unavailable}
  catch
    _kind, _reason -> {:error, :input_unavailable}
  end

  def prompt_for_password(_terminal), do: {:error, :input_unavailable}

  defp validate_or_retry_email(email, terminal) do
    if Regex.match?(@email_pattern, email) do
      {:ok, email}
    else
      prompt_for_email(terminal)
    end
  end

  defp validate_or_retry_password(password, confirmation, terminal) do
    if password == confirmation and String.length(password) in @password_length do
      {:ok, password}
    else
      prompt_for_password(terminal)
    end
  end
end
