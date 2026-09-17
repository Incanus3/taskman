# Operations environment inventory

This inventory owns environment obligations outside the deployment controller. It records dated
operator information, not live DNS, provider, registrar, mail or host verification. Refresh the
relevant authority before acting; external changes require operator authorization.
The [runbook](../guides/deployment.md) owns procedures and the
[acceptance report](../research/2026-09-17-operations-vps-acceptance.md) owns identified test evidence.

## Taskman host

`taskman.page` is currently used for staging acceptance. Once the Operations VPS readiness
workstream concludes, this environment will be retained for ongoing use and will no longer be
disposable. Historical disposable-host acceptance describes the test conditions; it does not
authorize later resets or resource deletion. Existing recovery retention obligations remain.

Account state recorded on 2026-09-18: the operator created a fresh administrator through the
[runbook](../guides/deployment.md#create-the-first-administrator) and confirmed successful sign-in
at `https://taskman.page`. Credentials remain operator-owned. This supersedes the earlier empty-account
observation after the reset; archived readiness-test credentials are not current credentials.
Operator sign-in confirmation does not refresh DNS/provider/mail or broader native acceptance.

DNS/registrar/mail observations dated 2026-09-06–09; these are not refreshed live state:

- Hostname: `taskman.page`; registrar: Cloudflare Registrar.
- Registration through **2027-09-05**; auto-renew recorded disabled, WHOIS redaction and
  registrar lock recorded enabled. Before that date, confirm current registration/renewal state
  and decide whether to renew or authorize enabling auto-renew if the domain should be retained.
- Provider: Hetzner; provider public TCP 80/443 recorded open.
- Cloudflare authoritative DNS and public `1.1.1.1` resolution returned DNS-only apex `A`
  `2.29.47.77` and `AAAA` `2a01:4f9:c015:6045::1` for `taskman.page`.
- Resend sending-only domain `notify.taskman.page` in `eu-west-1`, sender
  `no-reply@notify.taskman.page`; open/click tracking disabled. Verified records were DKIM TXT at
  `resend._domainkey.notify.taskman.page`, return-path MX priority 10
  `feedback-smtp.eu-west-1.amazonses.com`, and SPF TXT `v=spf1 include:amazonses.com ~all`
  at `send.notify.taskman.page`. API credentials belong to the protected secrets workflow.

## Staging recovery retention

Protected off-host reset copies and root-only `/root/taskman-fresh-install-9f2104118a5b` remain
required, including the current dump/releases/records/protected configuration/units/scheduled helper
and all six original backup pairs. No recovery-point pruning or deletion is authorized.
Archive SHA256: `858d2b414725acd3fef4f933ff1bf0cc1a63adaf19339959f08145648567e958`;
current dump SHA256: `a86b2a4fc3b965d8c19d320cd647ff78ab1c361e6261bc25895a1e93b52e82b0`.
Off-host copies are 0600 under private 0700 directories. Original application database OID was
16941; the fresh installation's OID was 17213. Compatible SSH/Caddy/PostgreSQL 18/packages/firewall/
native HBA/account/home services were retained; this is recovery from a Taskman reset, not a
pristine operating-system backup. Private receipt locations are recorded below.

## Private recovery and evidence locations

Recorded acceptance workspace: `/tmp/taskman-vps-acceptance.89wn5h8s`.
`fresh-install` holds reset/native recovery material; `fresh-install-corrected` holds the fresh
checkpoint build. These locations record provenance; the protected off-host and host-side
recovery retention requirements above remain authoritative. `final-readiness` holds source/artifact/CI binding receipts and its
`build` directory holds clean/dirty packaging receipts. Archived `derived-auth-fixed` and
`native-restore-resumed` hold authenticated restore evidence. The
[acceptance report](../research/2026-09-17-operations-vps-acceptance.md#private-evidence-provenance)
identifies the closing binding receipts.

These are recorded locations, not refreshed existence or durable backup guarantees. Temporary
receipts are not a substitute for protected durable recovery storage. Keep recipients, credentials,
signed links and cookies out of public artifacts.

No live registrar/provider/DNS/mail or broader host-acceptance refresh was performed;
only the dated account state above was updated.
