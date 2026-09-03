defmodule Taskman.Accounts.AccountDeletionTest do
  use Taskman.DataCase, async: false

  alias AshAuthentication.Jwt
  alias Taskman.Accounts
  alias Taskman.Accounts.{ApiKey, Token, User}
  alias Taskman.Lists
  alias Taskman.Projects
  alias Taskman.Repo
  alias Taskman.Tasks

  test "self-deletion requires the current password" do
    {administrator, user} = administrator_and_user("self-delete")

    assert {:error, _reason} = Accounts.delete_own_account(user, "wrong-password")
    assert %User{} = Repo.get(User, user.id)

    assert :ok = Accounts.delete_own_account(user, "password1")
    assert is_nil(Repo.get(User, user.id))
    assert %User{} = Repo.get(User, administrator.id)
  end

  test "administrative deletion requires a different target" do
    administrator = administrator_fixture("admin-delete-self@example.com")

    assert {:error, _reason} = Accounts.delete_user(administrator, administrator)
    assert %User{} = Repo.get(User, administrator.id)
  end

  test "account deletion removes dependent credentials without a tombstone and preserves the shared workspace" do
    {administrator, user} = administrator_and_user("dependent-delete")
    subject = AshAuthentication.user_to_subject(user)

    assert {:ok, session_token, _claims} = Jwt.token_for_user(user, %{}, purpose: :user)
    assert :ok = Token.valid_for_purpose?(session_token, "user")

    now = DateTime.utc_now()

    assert {:ok, %{api_key: _api_key}} =
             Accounts.create_api_key(
               user,
               %{name: "Deletion credential", expires_at: DateTime.add(now, 86_400, :second)},
               now: now
             )

    assert {:ok, project} =
             Projects.create_project(%{
               name: "Shared workspace survives account deletion",
               primary_directory: File.cwd!()
             })

    assert {:ok, task_list} = Lists.create_list(project, nil, %{name: "Shared list"})
    assert {:ok, task} = Tasks.create_task(project, task_list, %{title: "Shared task"})

    assert :ok = Accounts.delete_user(administrator, user)

    assert is_nil(Repo.get(User, user.id))
    assert Repo.all(from token in Token, where: token.subject == ^subject) == []
    assert Repo.all(from key in ApiKey, where: key.user_id == ^user.id) == []
    assert Projects.get_project(project.id).id == project.id
    assert Lists.get_list_for_project(project, task_list.id).id == task_list.id
    assert Tasks.get_task_for_project(project, task.id).id == task.id
  end

  defp administrator_and_user(prefix) do
    administrator = administrator_fixture("#{prefix}-administrator@example.com")
    user = administrator_fixture("#{prefix}-user@example.com")

    assert {:ok, user} = Accounts.demote_user(administrator, user)
    {administrator, user}
  end

  defp administrator_fixture(email) do
    {:ok, administrator} =
      Accounts.bootstrap_admin(%{
        email: email,
        password: "password1",
        password_confirmation: "password1"
      })

    administrator
  end
end
