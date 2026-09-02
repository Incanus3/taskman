defmodule Taskman.Accounts do
  use Ash.Domain

  authorization do
    authorize :always
  end

  resources do
    resource Taskman.Accounts.User do
      define :create_pending_user, action: :create_pending_user
      define :bootstrap_user, action: :bootstrap_user
    end

    resource Taskman.Accounts.ApiKey do
      define :create_api_key_record, action: :create_for_bootstrap
    end

    resource Taskman.Accounts.Token
  end
end
