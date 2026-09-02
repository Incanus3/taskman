defmodule Taskman.Accounts.EmailsTest do
  use Taskman.DataCase, async: false

  import Swoosh.TestAssertions

  alias Taskman.Accounts.Emails

  setup :set_swoosh_global

  test "setup invitations contain a safe link, both bodies, and seven-day guidance" do
    assert :ok = Emails.deliver_invitation("invited@example.com", "setup-token")

    assert_email_sent(fn email ->
      email.to == [{"", "invited@example.com"}] and
        email.subject =~ "Set up" and
        email.text_body =~ "https://" and
        email.text_body =~ "/setup/setup-token" and
        email.text_body =~ "7 days" and
        email.html_body =~ "https://" and
        email.html_body =~ "/setup/setup-token" and
        email.html_body =~ "7 days"
    end)
  end

  test "email-change confirmations contain a safe link, both bodies, and 24-hour guidance" do
    assert :ok = Emails.deliver_email_change_confirmation("new@example.com", "confirmation-token")

    assert_email_sent(fn email ->
      email.to == [{"", "new@example.com"}] and
        email.subject =~ "Confirm" and
        email.text_body =~ "https://" and
        email.text_body =~ "/confirm-email/confirmation-token" and
        email.text_body =~ "24 hours" and
        email.html_body =~ "https://" and
        email.html_body =~ "/confirm-email/confirmation-token" and
        email.html_body =~ "24 hours"
    end)
  end

  test "password resets contain a safe link, both bodies, and one-hour guidance" do
    assert :ok = Emails.deliver_password_reset("active@example.com", "reset-token")

    assert_email_sent(fn email ->
      email.to == [{"", "active@example.com"}] and
        email.subject =~ "Reset" and
        email.text_body =~ "https://" and
        email.text_body =~ "/reset-password/reset-token" and
        email.text_body =~ "1 hour" and
        email.html_body =~ "https://" and
        email.html_body =~ "/reset-password/reset-token" and
        email.html_body =~ "1 hour"
    end)
  end
end
