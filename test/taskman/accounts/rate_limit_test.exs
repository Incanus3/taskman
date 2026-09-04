defmodule Taskman.Accounts.RateLimitTest do
  use ExUnit.Case, async: false

  alias Taskman.Accounts.RateLimit

  defmodule Limiter do
    use Hammer, backend: :ets
  end

  defmodule RemainingWindowBackend do
    def expires_at("one-millisecond-left", 1_000), do: 1_001
    def expires_at("already-expired", 1_000), do: 1_000
    def expires_at("three-and-a-quarter-seconds-left", 10_000), do: 13_250
  end

  setup do
    pid = start_supervised!({Limiter, clean_period: :timer.minutes(1)})
    _ = :sys.get_state(pid)
    [backend: Limiter]
  end

  test "sign-in limits normalized email and remote address independently", %{backend: backend} do
    email = "  PERSON@EXAMPLE.COM "
    remote_ip = {203, 0, 113, 10}

    assert RateLimit.normalized_email(email) == "person@example.com"
    assert RateLimit.normalized_ip(remote_ip) == "203.0.113.10"

    for _ <- 1..10 do
      assert :ok = RateLimit.check(:sign_in, email: email, remote_ip: remote_ip, backend: backend)
    end

    assert {:error, retry_after: retry_after} =
             RateLimit.check(:sign_in, email: email, remote_ip: remote_ip, backend: backend)

    assert is_integer(retry_after) and retry_after >= 1

    for number <- 1..60 do
      assert :ok =
               RateLimit.check(:sign_in,
                 email: "other-#{number}@example.com",
                 remote_ip: {203, 0, 113, 11},
                 backend: backend
               )
    end

    assert {:error, retry_after: retry_after} =
             RateLimit.check(:sign_in,
               email: "other-61@example.com",
               remote_ip: {203, 0, 113, 11},
               backend: backend
             )

    assert is_integer(retry_after) and retry_after >= 1
  end

  test "password reset is limited by normalized email and remote address", %{backend: backend} do
    for _ <- 1..5 do
      assert :ok =
               RateLimit.check(:password_reset,
                 email: "RECOVER@EXAMPLE.COM",
                 remote_ip: {203, 0, 113, 12},
                 backend: backend
               )
    end

    assert {:error, retry_after: retry_after} =
             RateLimit.check(:password_reset,
               email: "recover@example.com",
               remote_ip: {203, 0, 113, 12},
               backend: backend
             )

    assert is_integer(retry_after) and retry_after >= 1

    for number <- 1..20 do
      assert :ok =
               RateLimit.check(:password_reset,
                 email: "reset-#{number}@example.com",
                 remote_ip: {203, 0, 113, 13},
                 backend: backend
               )
    end

    assert {:error, retry_after: retry_after} =
             RateLimit.check(:password_reset,
               email: "reset-21@example.com",
               remote_ip: {203, 0, 113, 13},
               backend: backend
             )

    assert is_integer(retry_after) and retry_after >= 1
  end

  test "an exhausted password-reset IP cannot consume another email's allowance", %{
    backend: backend
  } do
    blocked_ip = {203, 0, 113, 14}
    fresh_ip = {203, 0, 113, 15}

    for number <- 1..20 do
      assert :ok =
               RateLimit.check(:password_reset,
                 email: "ip-budget-#{number}@example.com",
                 remote_ip: blocked_ip,
                 backend: backend
               )
    end

    for _ <- 1..5 do
      assert {:error, retry_after: retry_after} =
               RateLimit.check(:password_reset,
                 email: "target@example.com",
                 remote_ip: blocked_ip,
                 backend: backend
               )

      assert is_integer(retry_after) and retry_after >= 1
    end

    for _ <- 1..5 do
      assert :ok =
               RateLimit.check(:password_reset,
                 email: "target@example.com",
                 remote_ip: fresh_ip,
                 backend: backend
               )
    end
  end

  test "invitation and email-change resends are limited by target address and actor", %{
    backend: backend
  } do
    for action <- [:invitation_resend, :email_change_resend] do
      for _ <- 1..5 do
        assert :ok =
                 RateLimit.check(action,
                   email: "TARGET@EXAMPLE.COM",
                   actor_id: "actor-1",
                   backend: backend
                 )
      end

      assert {:error, retry_after: retry_after} =
               RateLimit.check(action,
                 email: "target@example.com",
                 actor_id: "actor-1",
                 backend: backend
               )

      assert is_integer(retry_after) and retry_after >= 1

      assert :ok =
               RateLimit.check(action,
                 email: "target@example.com",
                 actor_id: "actor-2",
                 backend: backend
               )
    end
  end

  test "invalid API keys are limited at sixty attempts per remote address per minute", %{
    backend: backend
  } do
    remote_ip = {203, 0, 113, 14}

    for _ <- 1..60 do
      assert :ok = RateLimit.check(:invalid_api_key, remote_ip: remote_ip, backend: backend)
    end

    assert {:error, retry_after: retry_after} =
             RateLimit.check(:invalid_api_key, remote_ip: remote_ip, backend: backend)

    assert is_integer(retry_after) and retry_after >= 1
  end

  test "rate-limit errors report the remaining Hammer window with a one-second floor" do
    limit = %AshRateLimiter.LimitExceeded{
      backend: RemainingWindowBackend,
      key: "one-millisecond-left",
      per: 1_000
    }

    assert RateLimit.retry_after(limit, now: 1_000) == 1

    expired_limit = %{limit | key: "already-expired"}
    assert RateLimit.retry_after(expired_limit, now: 1_000) == 1

    remaining_limit = %{limit | key: "three-and-a-quarter-seconds-left", per: 10_000}
    assert RateLimit.retry_after(remaining_limit, now: 10_000) == 4
  end
end
