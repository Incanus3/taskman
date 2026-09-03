defmodule Taskman.Accounts.PasswordAuthenticationTest do
  use Taskman.DataCase, async: false

  alias AshAuthentication.{Argon2Provider, Errors.AuthenticationFailed, Info, Strategy}
  alias Taskman.Accounts
  alias Taskman.Accounts.User

  @bootstrap_actor %{accounts_bootstrap?: true}

  test "bootstrap passwords accept exactly 8 through 128 characters" do
    assert {:error, _error} =
             Accounts.bootstrap_admin("seven@example.com", String.duplicate("a", 7))

    assert {:ok, _user} =
             Accounts.bootstrap_admin("eight@example.com", String.duplicate("a", 8))

    assert {:ok, _user} =
             Accounts.bootstrap_admin(
               "one-twenty-eight@example.com",
               String.duplicate("a", 128)
             )

    assert {:error, _error} =
             Accounts.bootstrap_admin(
               "one-twenty-nine@example.com",
               String.duplicate("a", 129)
             )
  end

  test "bootstrap stores an Argon2 password hash" do
    assert {:ok, user} =
             Accounts.bootstrap_admin("argon@example.com", "password1")

    assert String.starts_with?(user.hashed_password, "$argon2id$")
    assert Argon2Provider.valid?("password1", user.hashed_password)
    refute user.hashed_password == "password1"
  end

  test "wrong email and wrong password return the same generic authentication failure" do
    assert {:ok, _user} =
             Accounts.bootstrap_admin("sign-in@example.com", "password1")

    assert {:error, wrong_email} =
             Accounts.sign_in_with_password(%{
               email: "missing@example.com",
               password: "password1"
             })

    assert {:error, wrong_password} =
             Accounts.sign_in_with_password(%{
               email: "sign-in@example.com",
               password: "incorrect"
             })

    assert %AuthenticationFailed{} = wrong_email
    assert %AuthenticationFailed{} = wrong_password
    assert Exception.message(wrong_email) == Exception.message(wrong_password)
  end

  test "pending and disabled users cannot sign in" do
    pending = account_with_password("pending@example.com", :pending, nil)
    disabled = account_with_password("disabled@example.com", :disabled, DateTime.utc_now())

    assert {:error, %AuthenticationFailed{}} =
             Accounts.sign_in_with_password(%{
               email: to_string(pending.email),
               password: "password1"
             })

    assert {:error, %AuthenticationFailed{}} =
             Accounts.sign_in_with_password(%{
               email: to_string(disabled.email),
               password: "password1"
             })
  end

  test "password authentication does not expose registration" do
    strategy = Info.strategy!(User, :password)

    assert is_nil(Ash.Resource.Info.action(User, :register_with_password))
    refute :register in Strategy.actions(strategy)
    refute Enum.any?(Strategy.routes(strategy), fn {_path, phase} -> phase == :register end)
  end

  defp account_with_password(email, status, confirmed_at) do
    {:ok, hash} = Argon2Provider.hash("password1")

    {:ok, user} =
      Accounts.bootstrap_user(
        %{
          email: email,
          hashed_password: hash,
          status: status,
          confirmed_at: confirmed_at
        },
        actor: @bootstrap_actor
      )

    user
  end
end
