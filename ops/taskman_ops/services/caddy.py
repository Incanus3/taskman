"""Caddy's declarative repository state and guarded configuration replacement."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess
import hashlib
import json

from ..errors import ExitStatus, OpsError
from ..helper_client.package import temporary_helper_package
from ..helper_client.runner import invoke_helper, new_correlation_id
from ..host_protocol import PROTOCOL_VERSION, HostRequest, HostResult, ProtocolError, validate_caddy_request, validate_caddy_result
from ..host_protocol.caddy import MAIN_CONTENT, SITE_PATH, validate_caddy_authority


from ..config import EnvironmentConfig


_OPS_ROOT = Path(__file__).resolve().parents[2]
_TEMPLATE = _OPS_ROOT / "caddy" / "Caddyfile"
_RENDERER = _OPS_ROOT / "caddy" / "render-caddyfile"


@dataclass(frozen=True)
class CaddyRepository:
    key_url: str
    keyring: str
    source: str


def render_caddyfile(config: EnvironmentConfig) -> str:
    if not isinstance(config, EnvironmentConfig):
        raise TypeError("Caddy renderer requires an environment configuration")
    try:
        completed = subprocess.run(
            ("sh", str(_RENDERER), config.public_hostname, str(_TEMPLATE), str(config.application_port)),
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError:
        raise RuntimeError("Caddyfile rendering is unavailable") from None
    if completed.returncode != 0:
        raise RuntimeError("Caddyfile rendering failed")
    rendered = completed.stdout
    if "taskman.example.com" in rendered or "reverse_proxy 127.0.0.1:" not in rendered:
        raise RuntimeError("Caddyfile rendering failed")
    return rendered


@dataclass(frozen=True)
class CaddyOwnershipPlan:
    """Confirmed proxy ownership and rendered assets, independent of live dictionaries."""

    hostname: str
    application_port: int
    branch: str
    authority_json: bytes
    site_content: str
    site_sha256: str
    main_content: str | None
    repository: CaddyRepository
    repository_packages: tuple[str, ...]


def _ownership_request(config: EnvironmentConfig, *, correlation_id: str | None = None) -> HostRequest:
    content = render_caddyfile(config)
    return validate_caddy_request(HostRequest(PROTOCOL_VERSION, 'caddy', correlation_id or new_correlation_id(), {}, {},
        dict(mode='inspect', hostname=config.public_hostname, application_port=config.application_port,
             site_content=content, site_sha256=hashlib.sha256(content.encode()).hexdigest(),
             receipt=None, public_probe=None)))


def _ownership_error(status: int, reason: str, *, warnings=()) -> OpsError:
    return OpsError(ExitStatus(status), 'caddy-authority', 'Caddy ' + reason, warnings=warnings)


def inspect_caddy(remote: object, config: EnvironmentConfig) -> HostResult:
    """Inspect through the verified transient helper without Taskman prerequisites."""
    request = _ownership_request(config)
    try:
        with temporary_helper_package() as package:
            result = invoke_helper(remote, package, request)
        validate_caddy_result(request, result)
    except OpsError:
        raise
    except Exception:
        raise _ownership_error(5, 'inspection failed') from None
    if result.outcome != 'succeeded':
        raise _ownership_error(result.state['exit_code'], result.state['reason'], warnings=result.warnings)
    return result


def build_caddy_ownership_plan(config: EnvironmentConfig, authority: HostResult) -> CaddyOwnershipPlan:
    if not isinstance(config, EnvironmentConfig) or not isinstance(authority, HostResult):
        raise TypeError('Caddy ownership plan requires configuration and authority')
    try:
        request = _ownership_request(config, correlation_id=authority.correlation_id)
        validate_caddy_result(request, authority)
        if authority.outcome != 'succeeded':
            raise _ownership_error(authority.state['exit_code'], authority.state['reason'], warnings=authority.warnings)
        observed = authority.to_mapping()['state']['authority']
        validate_caddy_authority(observed)
    except ProtocolError:
        raise _ownership_error(5, 'invalid inspection authority') from None
    return CaddyOwnershipPlan(config.public_hostname, config.application_port, observed['branch'],
        json.dumps(observed, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode(),
        request.parameters['site_content'], request.parameters['site_sha256'],
        MAIN_CONTENT if observed['branch'] == 'bootstrap' else None,
        CaddyRepository(
            'https://dl.cloudsmith.io/public/caddy/stable/gpg.key',
            '/usr/share/keyrings/caddy-stable-archive-keyring.gpg',
            'deb [signed-by=/usr/share/keyrings/caddy-stable-archive-keyring.gpg] '
            'https://dl.cloudsmith.io/public/caddy/stable/deb/debian any-version main',
        ), ('ca-certificates', 'curl', 'gnupg', 'debian-keyring', 'debian-archive-keyring', 'apt-transport-https'))


def validate_caddy_plan_authority(plan: CaddyOwnershipPlan, refreshed: HostResult) -> HostResult:
    """Require new confirmation for relevant authority or literal-predicate drift."""
    if not isinstance(plan, CaddyOwnershipPlan) or not isinstance(refreshed, HostResult):
        raise TypeError('Caddy confirmation requires plan and fresh authority')
    request = validate_caddy_request(HostRequest(PROTOCOL_VERSION, 'caddy', refreshed.correlation_id, {}, {},
        dict(mode='inspect', hostname=plan.hostname, application_port=plan.application_port,
             site_content=plan.site_content, site_sha256=plan.site_sha256, receipt=None, public_probe=None)))
    try:
        validate_caddy_result(request, refreshed)
    except ProtocolError:
        raise _ownership_error(5, 'invalid refreshed authority') from None
    if refreshed.outcome != 'succeeded':
        raise _ownership_error(refreshed.state['exit_code'], refreshed.state['reason'], warnings=refreshed.warnings)
    encoded = json.dumps(refreshed.to_mapping()['state']['authority'], sort_keys=True,
                         separators=(',', ':'), ensure_ascii=True).encode()
    if encoded != plan.authority_json:
        raise _ownership_error(10, 'authority changed; require a new preview')
    return refreshed


def caddy_ownership_preview(plan: CaddyOwnershipPlan) -> dict[str, object]:
    if not isinstance(plan, CaddyOwnershipPlan):
        raise TypeError('Caddy preview requires a frozen ownership plan')
    authority = json.loads(plan.authority_json)
    installation = plan.branch != 'existing'
    automatic = authority.get('service_active') and not authority.get('service_masked') and authority.get('glob_import_present')
    return dict(branch=plan.branch, site_path=SITE_PATH, current_site_sha256=authority.get('site_sha256'),
        desired_site_sha256=plan.site_sha256, current_site_metadata=authority.get('site'),
        desired_site_metadata={'uid': 0, 'gid': 0, 'mode': 0o644},
        site_directory_metadata=authority.get('site_directory'),
        new_site_directory_metadata={'uid': 0, 'gid': 0, 'mode': 0o755},
        glob_import_present=authority.get('glob_import_present'), installation=installation,
        repository=plan.repository.source if installation else None,
        repository_packages=plan.repository_packages if installation else (),
        package_policy='authenticate upstream stable candidate; inspect package identity; bind cached version and checksum; refuse removals' if installation else None,
        masks='persistently mask caddy.service and caddy-api.service before source/package convergence; retain original masks' if installation else 'preserve service activity, enablement and masks',
        main='create initial root import' if plan.branch == 'bootstrap' else 'preserve operator main bytes and metadata',
        package_preservation='protect bounded main bytes/metadata or absence; force-confold; preservation mismatch fails with masks retained' if plan.branch == 'preserve-configuration' else None,
        validation='native validation as Caddy user; suppress stdout and stderr; grep alone proves no activation',
        activation='validate, unmask only invocation-owned masks, start and enable Caddyfile service; API service remains disabled' if plan.branch == 'bootstrap' else
                   'validate; reload once after application proof if site changed or advisory public probe is not ready; unchanged ready site skips reload' if automatic else
                   'manual validation and activation; application setup can finish with proxy/public verification pending',
        recovery='root-only original site or absence evidence; site-only restoration; retain primary failure and runtime uncertainty; never restart or blindly retry',
        application='prepare and verify on deployment host; full public verification gates successful deployment history',
        import_instruction=MAIN_CONTENT if not authority.get('glob_import_present') and plan.branch != 'bootstrap' else None,
        coordination='operator coordinates quiet activation window including watchers and automated reloaders')


__all__ = ['CaddyRepository', 'render_caddyfile', 'CaddyOwnershipPlan', 'build_caddy_ownership_plan', 'inspect_caddy',
            'validate_caddy_plan_authority', 'caddy_ownership_preview']


def declare_caddy_sources(plan: CaddyOwnershipPlan) -> None:
    """Queue only signed-source prerequisites, after guard-install has succeeded."""
    from pyinfra.operations import apt
    if not isinstance(plan, CaddyOwnershipPlan):
        raise TypeError('Caddy source declaration requires a frozen ownership plan')
    if plan.branch == 'existing':
        return
    apt.packages(packages=list(plan.repository_packages), name='Install Caddy repository prerequisites')
    apt.key(src=plan.repository.key_url, dest=plan.repository.keyring, name='Install Caddy signing key')
    apt.repo(plan.repository.source, filename='caddy-stable', name='Configure Caddy repository')
    apt.update(name='Refresh signed package indexes')


def _mutation_request(plan, mode, *, receipt=None, authority=None, public_probe=None):
    if not isinstance(plan, CaddyOwnershipPlan):
        raise TypeError('Caddy mutation requires a frozen ownership plan')
    return validate_caddy_request(HostRequest(PROTOCOL_VERSION, 'caddy', new_correlation_id(),
        json.loads(plan.authority_json) if authority is None else authority, {},
        dict(mode=mode, hostname=plan.hostname, application_port=plan.application_port,
             site_content=plan.site_content, site_sha256=plan.site_sha256, receipt=receipt, public_probe=public_probe)))


def _invoke_mutation(remote, request):
    dispatched = False
    try:
        with temporary_helper_package() as package:
            dispatched = True
            result = invoke_helper(remote, package, request)
        validate_caddy_result(request, result)
    except Exception as error:
        dispatched = getattr(error, 'helper_entry_dispatched', dispatched)
        unknown = dispatched is not False
        state = dict(mutation_state='unknown' if unknown else 'unchanged',
                     caddy_receipt=request.to_mapping()['parameters']['receipt'],
                     caddy_authority=None, unavailable_fields=('authority',))
        message = 'Caddy helper response unavailable'
        if request.parameters['mode'] == 'activate':
            state.update(reload='unknown' if unknown else 'not-attempted',
                         recovery={'status': 'unknown' if unknown else 'not-needed', 'runtime_uncertain': unknown})
            if unknown:
                message += '; activation outcome is unknown; inspect the Caddy runtime before retrying'
        elif unknown and request.parameters['mode'] == 'prepare' and request.expected_state['branch'] == 'bootstrap':
            state.update(activation='unknown', recovery={'status': 'unknown', 'runtime_uncertain': True})
            message += '; bootstrap start outcome is unknown; inspect the Caddy runtime before retrying'
        raise OpsError(getattr(error, 'status', ExitStatus.REMOTE_PREFLIGHT), 'caddy-' + request.parameters['mode'],
            message, changed=unknown or getattr(error, 'changed', False), state=state,
            warnings=getattr(error, 'warnings', ())) from None
    if result.outcome != 'succeeded':
        state = result.to_mapping()['state']
        raise OpsError(state['exit_code'], 'caddy-' + state['failed_boundary'], result.message,
            changed=state['mutation_state'] != 'unchanged', state=state, warnings=result.warnings)
    return result


def guard_caddy_install(remote: object, plan: CaddyOwnershipPlan) -> HostResult:
    return _invoke_mutation(remote, _mutation_request(plan, 'guard-install'))


def prepare_caddy(remote: object, plan: CaddyOwnershipPlan, guarded: HostResult | None = None) -> HostResult:
    receipt = None
    if guarded is not None:
        guard_request = _mutation_request(plan, 'guard-install')
        guard_request = HostRequest(PROTOCOL_VERSION, 'caddy', guarded.correlation_id,
                                    guard_request.expected_state, {}, guard_request.parameters)
        validate_caddy_result(guard_request, guarded)
        if guarded.outcome != 'succeeded':
            raise _ownership_error(10, 'successful installation guard is required')
        receipt = guarded.to_mapping()['state']['receipt']
    return _invoke_mutation(remote, _mutation_request(plan, 'prepare', receipt=receipt))


def activate_caddy(remote: object, plan: CaddyOwnershipPlan, prepared: HostResult, *, public_probe: str) -> HostResult:
    # Reconstruct the exact prepare request correlation and supplied guard receipt
    # from the result; the full result schema still owns all wire evidence.
    receipt = prepared.to_mapping()['state']['receipt']
    if plan.branch == 'existing':
        guard_receipt = None
    else:
        guard_receipt = {**receipt, 'site_changed': False, 'recovery_id': None, 'installed_package_version': None}
    prepare_request = _mutation_request(plan, 'prepare', receipt=guard_receipt)
    prepare_request = HostRequest(PROTOCOL_VERSION, 'caddy', prepared.correlation_id,
                                  prepare_request.expected_state, {}, prepare_request.parameters)
    validate_caddy_result(prepare_request, prepared)
    if prepared.outcome != 'succeeded':
        raise _ownership_error(10, 'successful preparation is required')
    return _invoke_mutation(remote, _mutation_request(plan, 'activate', receipt=receipt,
        authority=prepared.to_mapping()['state']['authority'], public_probe=public_probe))


__all__ += ['declare_caddy_sources', 'guard_caddy_install', 'prepare_caddy', 'activate_caddy']
