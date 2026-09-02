defmodule Taskman.Accounts do
  use Ash.Domain

  authorization do
    authorize :always
  end

  resources do
    resource Taskman.Accounts.User do
      define :create_pending_user, action: :create_pending_user
      define :bootstrap_user, action: :bootstrap_user
      define :create_bootstrap_admin, action: :bootstrap_admin
    end

    resource Taskman.Accounts.ApiKey do
      define :create_api_key_record, action: :create_for_bootstrap
    end

    resource Taskman.Accounts.Token
  end

  @bootstrap_actor %{accounts_bootstrap?: true}

  @spec bootstrap_admin(map()) :: {:ok, Taskman.Accounts.User.t()} | {:error, term()}
  def bootstrap_admin(params) when is_map(params) do
    params
    |> Map.take([:email, :password, :password_confirmation])
    |> create_bootstrap_admin(actor: @bootstrap_actor)
  end

  def bootstrap_admin(_params), do: {:error, :invalid_input}

  @spec sign_in_with_password(map()) :: {:ok, Taskman.Accounts.User.t()} | {:error, term()}
  def sign_in_with_password(params) when is_map(params) do
    Taskman.Accounts.User
    |> AshAuthentication.Info.strategy!(:password)
    |> AshAuthentication.Strategy.action(:sign_in, params, domain: __MODULE__)
  end

  def sign_in_with_password(_params), do: {:error, :invalid_credentials}
end
