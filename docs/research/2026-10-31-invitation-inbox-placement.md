# Invitation inbox-placement investigation

Assessed 2026-10-31 against source revision `ddc3d841` and two existing received invitations.
This is a bounded evidence assessment, not a diagnosis of Gmail's classifier or an inbox-placement
acceptance result. No messages were sent and no provider, DNS, application or recipient settings
were changed during the investigation.

## Findings

| Question | Evidence | Conclusion and limit |
| --- | --- | --- |
| Did authentication fail? | Gmail's received headers for invitations dated 2026-10-12 and 2026-10-30 report SPF, aligned sender-domain DKIM and DMARC passing. | Authentication failure does not explain either observed message. The earlier invitation was originally reported as Spam. |
| Was transport encrypted? | Both received headers record TLS 1.3 delivery to Gmail. | The observed provider-to-Gmail hops used TLS. |
| Is reverse DNS broken? | Current PTR and forward A lookups for the earlier message's sending IP agree. | No current forward/reverse mismatch was found for that IP; this is not a historical DNS or reputation measurement. |
| Does the actual invitation contain a tracking redirect or pixel? | Both received MIME messages contain text/plain and text/html alternatives, a direct HTTPS setup link and no image. The link uses a subdomain of the sender's organizational domain. | No tracking redirect, tracking image or unrelated link domain was observed in these messages. The actual account’s Configuration page offers tracking setup; no setting was changed. |
| Has placement recovered? | The earlier message is now in Inbox; the operator confirms moving it or marking it Not spam. The operator confirms the later invitation arrived in Inbox before deletion. | The later invitation establishes successful Inbox placement for that recipient after feedback; it does not establish first-contact placement or why placement changed. |
| Is the sending domain ready? | The actual sending account was inspected through its authenticated browser session. Domain, DKIM and sending records are Verified and sending is enabled. TLS is Opportunistic; both observed deliveries used TLS. | No verification or sending-enable defect was found. The separately connected Resend plugin still targets a different account. |
| Is sender reputation poor? | Actual-account Last 30 days metrics show three emails, 100% deliverability, zero bounces and zero complaints. Both invitations have Sent/Delivered events and Gmail SMTP 250 responses. | This tiny sample establishes server acceptance, not reputation or first-contact Inbox placement. No Gmail classifier reason or verified Postmaster reputation data was available. |

Current public DNS also resolves the earlier envelope-sender SPF policy through the provider's
include, publishes a sender DKIM key, and retains DMARC `p=none`. The received authentication
results are the evidence for the actual messages; DNS observation alone does not prove historical
message authentication or inbox placement.

Only two matching messages were found in the inspected recipient mailbox, including Spam and
Trash, with no further search page. This is a mailbox-specific sample, not a count of account-wide
sending or proof that other recipients had the same experience. Exact identities, routing headers,
provider inventory and setup URLs are private operational data and must not enter this repository.
Setup links were inspected as data and were not opened or replayed.

## Provider assessment

The later invitation’s Insights report lists only “Use a subdomain” under Possible improvements.
It describes separating sending purposes and their reputation. The content, link-domain, DMARC,
plain-text and tracking-related checks appear under Doing great. This is a general recommendation,
not a measured Gmail classifier reason or evidence that changing the sender would fix placement.
Do not enable tracking simply because its setup is offered.

The actual sending domain’s Configuration page shows Forge Enabled, with text explaining that
routing will start automatically once the domain is established as a good sender. The inspected
messages used the provider’s SES outbound path. An Enabled label alone does not prove which
infrastructure carried a message; no routing defect or need to migrate was established.

The account-wide metrics include one unrelated email, whose content was not inspected. The two
Taskman invitation records match their subjects, addresses and send times; their Gmail SMTP
acceptance identifiers match the received-header transactions. Neither record shows a bounce or
complaint event. No account/domain warning requiring a corrective action appeared on the inspected
domain, metrics or invitation pages. These observations do not expose Gmail’s classification logic.

## Source inspection and verification

- [Invitation and security-email construction](../../lib/taskman/accounts/emails.ex) creates both
  bodies, a purpose-specific subject, an HTTPS link from the configured public URL, expiry guidance
  and ignore-if-unexpected guidance. HTML uses the generic action label “Continue securely.” That
  label could be clearer, but no evidence links it to the observed Spam classification.
- [Runtime configuration](../../config/runtime.exs) sets the application sender identity and HTTPS
  public URL and selects the Resend adapter. The received MIME messages confirm the relevant body
  and link properties beyond source-level expectations.
- `mix test test/taskman/accounts/emails_test.exs test/taskman/runtime_config_test.exs`: 11 passed.
  These tests use local/test delivery; they do not measure provider reputation or Gmail placement.

## Guidance and implications

[Google's sender guidelines](https://support.google.com/mail/answer/81126?hl=en) require
authentication, appropriate forward/reverse DNS, TLS and valid message formatting. They describe
shared-IP reputation as a possible influence on delivery and do not guarantee that provider-sent
messages pass Gmail filtering. Their one-click-unsubscribe requirement concerns marketing and
subscribed messages; it does not justify adding unsubscribe machinery to account-setup invitations.
DMARC `p=none` is permitted even in Google's bulk-sender requirements. The observed authentication
results provide no basis for selecting stricter enforcement as a Spam-placement remedy.

[Resend's Gmail guidance](https://resend.com/guides/email/deliverability/avoid-gmail-spam-folder)
recommends simple content, trustworthy domain relationships and avoiding tracking that can hurt
placement. The inspected invitations already satisfy those observable properties; reputation and
recipient expectations still require evidence. Do not manufacture sending volume or engagement as
a warm-up experiment.

[Resend's tracking explanation](https://resend.com/blog/open-and-click-tracking) describes link
rewriting for click tracking and an image pixel for open tracking. Neither was observed in the
received samples. This is stronger message-specific evidence than unrelated-account toggles.

[Google's Postmaster dashboard guidance](https://support.google.com/mail/answer/14668346?hl=en)
warns that low-volume days can have missing data and that reporting is delayed. An empty dashboard
would not establish healthy or unhealthy reputation. Do not send extra invitations to populate it.

## Disposition and limits

Further action is not selected. Retain the existing sender/domain/provider configuration; no
support submission, additional test invitations or remediation is planned. This is a decision to
end active follow-up, not a claim that the original Spam classification is diagnosed or that
first-contact Inbox placement is guaranteed.

The later invitation reached Inbox after recipient feedback. No actionable configuration defect
was found. The generic sending-subdomain recommendation does not justify a migration on this
evidence. Exact token-free receipts and an unsent provider-support request remain in private
operator storage outside version control; no ongoing monitoring or sending commitment is created.

If the operator explicitly resumes investigation, the prepared support request asks about the
actual routing/pool, available sender/IP/domain reputation evidence for the two known message IDs,
and whether the Forge state or subdomain recommendation reflects any detected issue. Revalidate
account access and current evidence first. Sending the request needs explicit instruction.

Any later correction requires approval of its exact scope. Domain changes can affect other senders;
application changes retain design and planning gates. A controlled placement test also needs the
exact installation, recipient and invitation action authorized before sending. Record initial
placement before recipient changes and account for earlier Not spam feedback. A repeat to this
recipient cannot establish first-contact placement for new recipients. Keep token-bearing evidence
private and preserve the distinction between server acceptance and Inbox placement.
