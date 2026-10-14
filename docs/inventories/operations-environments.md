# Operations inventory privacy

The repository is public. It records portable operating requirements and synthetic examples,
not actual deployment or workstation inventories. Real environment settings belong in encrypted
deployment configuration; machine observations, recovery receipts and operational inventories
belong in private operator storage outside version control.

## Encrypted environment configuration

Keep real host addresses, domains, SSH identities and host-key pins, sender addresses, database
identifiers and environment-specific paths/settings encrypted with SOPS. Store settings in
`<name>.config.sops.yaml` and credentials in
`<name>.secrets.sops.yaml`; the controller decrypts both in memory. Public examples must use
clearly synthetic values. Controller commands always load encrypted settings. The loader also
supports explicitly selected plaintext input for programmatic callers; this does not authorize
tracking real readable settings. Supply private decryption identities through SOPS's external
identity mechanism; never document an operator's actual identity-file location.

Required SOPS public cryptographic metadata is part of the encrypted container. It is not a place
for machine inventories, personal comments or identity-file paths. Preserve credential values when
changing repository storage; encryption does not authorize rotation, installation or redeployment.

## Private evidence and recovery

Keep host inventory, exact acceptance inputs/results, recovery locations, checksums and custody
records outside version control. Before changes with recovery consequences, verify that private
records identify the correct environment and establish usable current recovery. Generic procedures
are in the [deployment runbook](../guides/deployment.md); machine records are not public evidence.

Retain protected recovery material until separately authorized disposal. Completion of a workstream,
a local commit or a PR does not authorize deletion, restore, replay or changes to another service.
Public acceptance summaries may describe behavior and limitations without exposing the machines,
accounts, addresses or filesystem locations used for verification.

## Local work tracking and public history

Keep the repository-local Beads database and private history outside version control. Keep
`.beads/issues.jsonl` tracked, with portable issue descriptions, decisions, dependencies and
verification limits. Sanitize comments and metadata as well as issue bodies: use generic operator
identities, a portable source-repository reference, and synthetic infrastructure values instead of
actual accounts, workstation paths, host settings or recovery receipts. Manage mutations, imports
and exports through `br`; do not edit its JSONL/database state files directly.

A clean current tree does not sanitize previous commits. Keep previously private history out of a
public branch; a clean public history requires a separately authorized publication preparation.
Do not push corrections during an ongoing gradual review unless the operator requests a batch push.

## Follow-up obligations

Commissioning does not establish ongoing disaster recovery. Before meaningful hosted use, establish
encrypted off-host backup, transfer monitoring and download/decrypt/restore verification. Authentication
success does not guarantee mail inbox placement. Diagnostic and validation follow-ups retain their
own scoped acceptance criteria and are separate from completed deployment work.

Private operational detail previously recorded in this file is preserved in protected operator
storage outside version control. It is intentionally absent from the public tree.
