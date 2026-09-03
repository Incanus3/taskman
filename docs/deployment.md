# Deploying Taskman on a dedicated host

This runbook operates one Taskman release on one Linux host. It does not provision a host,
PostgreSQL, DNS, Caddy, a Resend account, or a backup destination. Those actions remain the
operator's responsibility.

Taskman is an authenticated shared workspace: an account grants access to the same Projects, Lists,
and Tasks; it does not create ownership or per-resource permissions. Caddy terminates public HTTPS;
the Phoenix release listens only at `127.0.0.1:4000`.

## Before building

Build the release on the same CPU architecture and compatible Linux distribution family as the
runtime host. A release includes the Erlang runtime, but native dependencies still require a
compatible libc and system libraries. The target host needs neither source code, Mix, Node, nor a
development toolchain. Build a new artifact for an OS-family, architecture, or system-library
change; do not copy one built on macOS or an incompatible Linux distribution.

Choose a DNS name such as `taskman.example.com`, arrange a private PostgreSQL instance, and install
Caddy and systemd on the host. PostgreSQL may be local or on a private network, but it must never
be Internet-accessible.

## Build and transfer a release

On the compatible build host, start from the reviewed source revision and build the artifact:

```sh
MIX_ENV=prod mix deps.get
MIX_ENV=prod mix compile --warnings-as-errors
MIX_ENV=prod mix assets.deploy
MIX_ENV=prod mix release --overwrite

release_id="$(git rev-parse --short HEAD)-$(date -u +%Y%m%d%H%M%S)"
tar -C _build/prod/rel -czf "taskman-${release_id}-linux-amd64.tar.gz" taskman
sha256sum "taskman-${release_id}-linux-amd64.tar.gz" \
  > "taskman-${release_id}-linux-amd64.tar.gz.sha256"
```

Use an artifact name appropriate to the actual target architecture. Transfer both files over an
authenticated channel, then verify the checksum on the target before extracting:

```sh
scp taskman-RELEASE-linux-amd64.tar.gz* ops/systemd/taskman.service ops/caddy/Caddyfile \
  deployer@taskman.example.com:/tmp/
ssh deployer@taskman.example.com
cd /tmp
sha256sum -c taskman-RELEASE-linux-amd64.tar.gz.sha256
```

Do not treat an unverified upload as a release candidate.

## Create the service account and directories

As root on the target, create an unprivileged account and root-managed release path. The service
account needs to read and execute releases, not modify them.

```sh
groupadd --system taskman
useradd --system --gid taskman --home-dir /var/lib/taskman --create-home \
  --shell /usr/sbin/nologin taskman
install -d -o root -g root -m 0755 /opt/taskman/releases
install -d -o taskman -g taskman -m 0700 /var/lib/taskman
install -d -o root -g taskman -m 0750 /etc/taskman
test -e /etc/taskman/taskman.env || install -o root -g root -m 0600 /dev/null /etc/taskman/taskman.env
chown root:root /etc/taskman/taskman.env
chmod 0600 /etc/taskman/taskman.env
```

`/etc/taskman/taskman.env` is a root-owned, mode `0600` secret file read by systemd. Do not commit,
copy into a release archive, expose through shell history, or make it readable by the `taskman`
user. Systemd reads it before dropping to the dedicated user and passes only its environment to the
process.

Populate it with one shell-style `NAME=value` assignment per line. Quote URL values containing shell
metacharacters according to systemd's `EnvironmentFile=` syntax.

```text
DATABASE_URL=ecto://taskman:REDACTED@127.0.0.1/taskman_prod
SECRET_KEY_BASE=at-least-64-random-bytes
ASH_AUTHENTICATION_TOKEN_SIGNING_SECRET=a-different-at-least-64-random-bytes
PHX_HOST=taskman.example.com
RESEND_API_KEY=re_REDACTED
MAIL_FROM=no-reply@taskman.example.com

# Optional; PORT defaults to 4000 and must remain a loopback-only listener.
PORT=4000
POOL_SIZE=10
# ECTO_IPV6=true
# DNS_CLUSTER_QUERY=...
```

`DATABASE_URL`, `SECRET_KEY_BASE`, `ASH_AUTHENTICATION_TOKEN_SIGNING_SECRET`, `PHX_HOST`,
`RESEND_API_KEY`, and `MAIL_FROM` are required. The signing secrets must be distinct and each at
least 64 bytes. `MAIL_FROM` must be exactly one email address at a Resend-verified sending domain.
Taskman deliberately has no setting that trusts forwarded headers from a public peer: the supported
topology is Caddy as the immediate loopback proxy.

Give the PostgreSQL role only the access it needs for Taskman's database. Keep PostgreSQL bound to
localhost or a private network, restrict its host firewall and `pg_hba.conf`, and never open port
5432 to the Internet.

## Configure Resend and DNS

In Resend, add the sending domain and publish the DNS verification records shown by Resend. Wait for
verification before creating the API key, then create a scoped API key and put it only in the
protected environment file. Set `MAIL_FROM` to an address at that verified domain. After the first
administrator invites a controlled test address, verify that the invitation arrives with an HTTPS
link; do not use a production recipient for a configuration test.

Create public DNS `A` (and, where used, `AAAA`) records for `taskman.example.com` pointing to the
host. Permit only TCP 80 and 443 at the public firewall. Keep TCP 4000 and PostgreSQL private. Caddy
uses ports 80/443 for ACME validation, certificate renewal, and HTTPS; Phoenix's loopback listener
is not a public service.

## Install the release and services

Install each verified artifact under a new immutable versioned directory. Replace `RELEASE` with the
release identifier used during packaging.

```sh
install -d -o root -g root -m 0755 /opt/taskman/releases/RELEASE
tar -xzf /tmp/taskman-RELEASE-linux-amd64.tar.gz \
  -C /opt/taskman/releases/RELEASE --strip-components=1
chown -R root:taskman /opt/taskman/releases/RELEASE
chmod -R g+rX,o-rwx /opt/taskman/releases/RELEASE

ln -s releases/RELEASE /opt/taskman/current.next
mv -Tf /opt/taskman/current.next /opt/taskman/current

install -o root -g root -m 0644 /tmp/taskman.service /etc/systemd/system/taskman.service
install -o root -g root -m 0644 /tmp/Caddyfile /etc/caddy/Caddyfile
caddy validate --config /etc/caddy/Caddyfile
systemctl daemon-reload
systemctl enable --now taskman.service
systemctl reload caddy.service
```

The atomic rename of `current.next` makes `/opt/taskman/current` select exactly one versioned
release. The service executes `current/bin/migrate` before it starts `current/bin/server`; a failed
migration prevents the new server from starting. Never edit a directory selected by `current`.
The service also sets `RELEASE_TMP=/var/lib/taskman`, its dedicated writable state directory, so the
root-managed immutable release remains read-only at runtime.

Create the first administrator only on the server console. This command lets systemd read the
root-only environment file while the release itself runs as `taskman`:

```sh
systemd-run --wait --pipe --collect \
  --property=User=taskman --property=Group=taskman \
  --property=WorkingDirectory=/opt/taskman/current \
  --property=EnvironmentFile=/etc/taskman/taskman.env \
  /opt/taskman/current/bin/create-admin
```

The command prompts for the email and password; never pass a password in an argument, environment
variable, transcript, or ticket. It is also the break-glass recovery path for an administrator.

## Reverse proxy, HTTPS, and LiveView

The supplied [`ops/caddy/Caddyfile`](../ops/caddy/Caddyfile) intentionally contains only:

```caddyfile
taskman.example.com {
  reverse_proxy 127.0.0.1:4000
}
```

Caddy's reverse proxy supports WebSocket upgrades and supplies the forwarded request headers used
by Phoenix. Do not add manual `X-Forwarded-For` or `X-Forwarded-Proto` overrides: Taskman's trusted
proxy plug accepts them only when its immediate peer is loopback, then derives the client address and
HTTPS scheme. Exposing Phoenix directly, adding a second proxy, or binding it to a public address
invalidates that trust boundary and requires an explicit configuration/design change.

Caddy obtains and renews certificates automatically after public DNS and ports 80/443 work. Phoenix
sets secure cookies and HSTS after it sees Caddy's forwarded HTTPS scheme. Verify both HTTPS and
HSTS, then sign in and navigate a LiveView route to confirm the WebSocket connection remains live:

```sh
curl --fail --head https://taskman.example.com/
curl --fail --silent --show-error --head https://taskman.example.com/ \
  | grep -i '^strict-transport-security:'
```

In a browser, sign in, open a Project, and check that the LiveView WebSocket remains connected.
Avoid a short reverse-proxy read timeout that would terminate long-lived LiveView connections. Test
uploads or unusually large requests deliberately before adding a Caddy request-size limit; the
initial configuration does not invent one.

## Browser, API, and CLI smoke checks

1. Sign in as the bootstrap administrator over HTTPS and invite a controlled user. Complete setup
   through the email link and verify the user can open the shared workspace.
2. In Account settings, create an API key. Its `tm_` plaintext is shown once. Copy it directly into
   the CLI's non-echoing prompt; never save it in screenshots, shell history, URLs, logs, or support
   reports. Revoke it and create a replacement if it is lost.
3. On the client machine, configure and test the CLI:

   ```sh
   taskman config set-url https://taskman.example.com
   taskman config set-key
   taskman config show
   taskman projects list --json
   ```

   The CLI stores its URL and key in protected XDG configuration. `TASKMAN_API_URL` and
   `TASKMAN_API_KEY` are appropriate secret-injected overrides for CI or containers. Ordinary API
   commands return exit status `7` when authentication is absent, rejected, or forbidden.
4. If an HTTP API check is necessary, inject the key through a protected environment instead of a
   URL and confirm the standard JSON envelope:

   ```sh
   curl --fail --silent --show-error \
     -H "Authorization: Bearer $TASKMAN_API_KEY" \
     https://taskman.example.com/api/v1/projects
   ```

   Clear temporary shell variables when finished. A missing, malformed, revoked, expired, or
   disabled-user key must return HTTP 401; browser cookies never authenticate this API.

## Roll forward, rollback, and database safety

For every rollout, build and verify a new artifact, extract it into a new release directory, point
`current` at it atomically, then restart the service:

```sh
ln -s releases/NEW_RELEASE /opt/taskman/current.next
mv -Tf /opt/taskman/current.next /opt/taskman/current
systemctl restart taskman.service
systemctl status taskman.service --no-pager
```

Keep the previously working release directory until the new version has passed browser, API, CLI,
and log checks. To roll application code back, point `current` at that previous release and restart:

```sh
ln -s releases/PREVIOUS_RELEASE /opt/taskman/current.next
mv -Tf /opt/taskman/current.next /opt/taskman/current
systemctl restart taskman.service
```

This is safe only when every migration made by the new release is compatible with the immediately
previous release. Treat destructive, data-rewriting, or otherwise irreversible migrations as a
release gate: document a tested reverse migration or restore a verified database backup before
rolling application code back. Do not delete a release or mutate schema state just to make a
rollback appear clean.

Back up PostgreSQL before each migration and on a tested schedule. Use encrypted, access-controlled
storage outside the host, retain the matching release identifier, and rehearse restoring into an
isolated database. A typical logical backup is:

```sh
sudo -u postgres pg_dump --format=custom --file /secure/backups/taskman-YYYYMMDD.dump taskman_prod
```

Adapt the command to the actual PostgreSQL role, host, encryption policy, and retention policy; do
not place backups in the release directory.

## Logs and troubleshooting

Use journald as the application log source:

```sh
journalctl -u taskman.service -f
journalctl -u taskman.service -b --no-pager
systemctl status taskman.service --no-pager
systemctl status caddy.service --no-pager
caddy validate --config /etc/caddy/Caddyfile
```

- A startup error naming an environment variable means repair the root-only environment file, not
  the release code. Do not print its values while diagnosing it.
- A migration failure intentionally leaves the server stopped. Restore the selected release only
  after assessing migration compatibility and the database backup.
- Certificate failures usually mean public DNS, ports 80/443, or Caddy permissions are wrong; keep
  port 4000 private while investigating.
- Login email failures require a verified Resend sender domain, a valid `MAIL_FROM`, and a working
  `RESEND_API_KEY`. Check the provider dashboard and journald without copying credentials.
- A client address or HTTPS-scheme problem indicates the supported loopback Caddy topology was
  changed. Do not make Phoenix trust arbitrary forwarded headers as a workaround.
- A disconnected LiveView usually requires checking the browser WebSocket, Caddy proxy path, and
  account/session status. Disabled accounts deliberately lose both browser sessions and API keys.
