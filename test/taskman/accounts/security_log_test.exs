defmodule Taskman.Accounts.SecurityLogTest do
  use Taskman.DataCase, async: false

  import ExUnit.CaptureLog
  import Taskman.AccountsFixtures

  alias Taskman.Accounts
  alias Taskman.Accounts.SecurityLog

  setup do
    previous_level = Logger.level()
    Logger.configure(level: :info)
    on_exit(fn -> Logger.configure(level: previous_level) end)
  end

  test "records successful and rejected events with identifiers while redacting sensitive values" do
    sensitive = %{
      email: "person@example.com",
      password: "correct-horse-battery-staple",
      password_confirmation: "correct-horse-battery-staple",
      setup_token: "setup-token",
      recovery_token: "recovery-token",
      email_token: "email-token",
      api_key: "tm_secret-api-key",
      api_key_hash: "api-key-hash",
      authorization: "Bearer tm_secret-api-key",
      cookie: "session-cookie",
      secret: "another-secret"
    }

    output =
      capture_log([level: :info], fn ->
        assert :ok =
                 SecurityLog.record(:sign_in_succeeded,
                   actor_id: "00000000-0000-0000-0000-000000000001",
                   target_id: "00000000-0000-0000-0000-000000000002",
                   metadata: sensitive
                 )

        assert :ok = SecurityLog.record(:sign_in_rejected, metadata: sensitive)
      end)

    assert output =~ "security_event=sign_in_succeeded"
    assert output =~ "security_event=sign_in_rejected"
    assert output =~ "actor_id=00000000-0000-0000-0000-000000000001"
    assert output =~ "target_id=00000000-0000-0000-0000-000000000002"

    for value <- Map.values(sensitive) do
      refute output =~ value
    end
  end

  test "account hooks log API-key creation without leaking the plaintext credential" do
    user = user_fixture()
    expires_at = DateTime.add(DateTime.utc_now(), 2, :day)

    test_process = self()

    output =
      capture_log([level: :info], fn ->
        assert {:ok, %{plaintext: plaintext}} =
                 Accounts.create_api_key(user, %{name: "Security test", expires_at: expires_at})

        send(test_process, {:plaintext_api_key, plaintext})
      end)

    assert_receive {:plaintext_api_key, plaintext}

    assert output =~ "security_event=api_key_created"
    assert output =~ "actor_id=#{user.id}"
    assert output =~ "target_id=#{user.id}"
    refute output =~ plaintext
  end

  test "only UUID identifiers are rendered, preventing control-character log injection" do
    output =
      capture_log([level: :info], fn ->
        assert :ok =
                 SecurityLog.record(:sign_in_rejected,
                   actor_id: "attacker\nsecurity_event=forged",
                   target_id: "00000000-0000-0000-0000-000000000003\r\nforged=true"
                 )
      end)

    assert output =~ "security_event=sign_in_rejected"
    refute output =~ "security_event=forged"
    refute output =~ "forged=true"
  end
end
