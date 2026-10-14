"""Finite Caddy mutation procedures consuming the observed authority contract."""
from __future__ import annotations
from taskman_ops.host_helper import caddy_files, caddy_package
from taskman_ops.host_helper.commands import CommandError
import hashlib
import json
import tempfile
from pathlib import Path
from taskman_ops.host_protocol.caddy import MAIN_CONTENT
from taskman_ops.host_protocol import HostResult, validate_caddy_result
from taskman_ops.host_protocol.caddy import SITE_PATH


class CaddyExecution:
    def __init__(self, observer, request):
        self.observer, self.request = observer, request
        self.receipt = request.to_mapping()['parameters']['receipt']
        self.mutation = 'unchanged'
        self.boundary = 'inspection'
        self.validation = self.reload = 'not-attempted'
        self.recovery = {'status': 'not-needed', 'runtime_uncertain': False}

    def possible(self):
        if self.mutation != 'changed':
            self.mutation = 'unknown'

    def changed(self):
        self.mutation = 'changed'

    def result(self, authority, *, code=0, reason='compatible'):
        return validate_caddy_result(self.request, HostResult.for_request(self.request,
            'succeeded' if code == 0 else 'refused' if code in {2, 10} else 'retryable', 'Caddy ' + reason,
            dict(exit_code=code, failed_boundary=None if code == 0 else self.boundary,
                mutation_state=self.mutation, authority=authority,
                unavailable_fields=() if authority is not None else ('authority',), receipt=self.receipt,
                validation=self.validation, reload=self.reload, recovery=self.recovery, reason=reason)))

    def observe(self):
        self.observer._remaining(3)
        masks = self.receipt['created_masks'] if self.receipt is not None and self.request.expected_state['branch'] == 'bootstrap' else ()
        return self.observer.observe_caddy(bootstrap_masks=masks)

    def confirm(self, authority):
        expected = self.request.to_mapping()['expected_state']
        if self.receipt is not None and self.request.parameters['mode'] == 'prepare':
            for name in self.receipt['created_masks']:
                key = 'api_service' if name == 'caddy-api.service' else 'service'
                expected[key + '_masked'] = True
                expected[key + '_enabled'] = 'masked'
        if authority != expected:
            self.boundary = 'authority'
            raise self.observer.CaddyObservationError(10, 'authority-changed')

    def validate(self):
        self.boundary = 'validation'
        self.validation = 'unknown'
        try:
            self.observer._native(('sudo', '-n', '-u', 'caddy', '/usr/bin/caddy', 'validate',
                                   '--config', str(self.observer._path('/etc/caddy/Caddyfile')), '--adapter', 'caddyfile'),
                                  timeout_seconds=30)
        except Exception:
            self.validation = 'failed'
            raise self.observer.CaddyObservationError(8, 'validation-failed') from None
        self.validation = 'succeeded'

    def new_receipt(self, authority):
        return dict(site_path=SITE_PATH, site_sha256=self.request.parameters['site_sha256'], site_changed=False,
            recovery_id=None, original_absent=authority['site'] is None, original_sha256=authority['site_sha256'],
            original_metadata=authority['site'], created_masks=[], installed_package_version=None)

    def prepare_site(self, authority):
        if authority['glob_import_present']:
            self.validate()
        if self.receipt is None:
            self.receipt = self.new_receipt(authority)
        if authority['site_sha256'] == self.request.parameters['site_sha256']:
            return
        self.boundary = 'site'
        self.possible()
        o = self.observer
        if caddy_files.safe_directory(o, o._path('/etc/caddy'), 0o755):
            self.changed()
        if caddy_files.safe_directory(o, o._path('/etc/caddy/sites-enabled'), 0o755):
            self.changed()
        self.receipt['recovery_id'] = caddy_files.protect_site(o, self.receipt)
        self.changed()  # Protected persistent recovery evidence was published.
        try:
            caddy_files.atomic_file(o._path(SITE_PATH), self.request.parameters['site_content'].encode(),
                                   {'uid': 0, 'gid': 0, 'mode': 0o644})
        except Exception:
            if o._metadata(o._path(SITE_PATH), site=True) is not None and hashlib.sha256(o._read(o._path(SITE_PATH), 4096)).hexdigest() == self.receipt['site_sha256']:
                self.receipt['site_changed'] = True
            raise
        self.receipt['site_changed'] = True
        self.changed()
        if authority['glob_import_present']:
            self.validate()

    def recover(self):
        if self.reload != 'not-attempted':
            self.recovery['runtime_uncertain'] = True
        if self.receipt is None or not self.receipt['site_changed']:
            return
        try:
            caddy_files.restore_site(self.observer, self.receipt)
            self.changed()
            self.recovery['status'] = 'restored'
            if self.reload != 'not-attempted':
                # Disk restoration cannot prove what the native loader applied.
                self.recovery['runtime_uncertain'] = True
                self.validate()
                if not self.observe()['service_active']:
                    self.recovery['runtime_uncertain'] = True
                    self.recovery['status'] = 'unknown'
        except Exception:
            self.recovery['status'] = 'failed'
            self.recovery['runtime_uncertain'] = True

    def guard(self, authority):
        self.receipt = self.new_receipt(authority)
        if authority['branch'] == 'existing':
            self.boundary = 'authority'
            raise self.observer.CaddyObservationError(10, 'mutation-unavailable')
        self.boundary = 'mask'
        for name, key in (('caddy.service', 'service_masked'), ('caddy-api.service', 'api_service_masked')):
            prefix = 'api_service' if name == 'caddy-api.service' else 'service'
            if not authority[key] or authority[prefix + '_enabled'] == 'masked-runtime':
                self.possible()
                self.observer._native(('systemctl', 'mask', name), timeout_seconds=3)
                path = self.observer._path('/etc/systemd/system/' + name)
                if not path.is_symlink() or path.readlink().as_posix() != '/dev/null':
                    raise self.observer.CaddyObservationError(8, 'mask-failed')
                self.receipt['created_masks'].append(name)
                self.changed()

    def install(self, authority):
        self.boundary = 'package'
        o = self.observer
        with tempfile.TemporaryDirectory(prefix='taskman-caddy-package-') as temporary:
            cache = Path(temporary)
            candidate = caddy_package.inspect_candidate(o, cache)
            default = caddy_package.bootstrap_default(o, candidate, cache) if authority['branch'] == 'bootstrap' else None
            return self.install_inspected(authority, candidate, cache, default)

    def install_inspected(self, authority, candidate, cache, default):
        o = self.observer
        version = candidate.version
        self.confirm(self.observe())  # Bind main preservation immediately before installation.
        self.boundary = 'package'
        before_main = o._read(o._path('/etc/caddy/Caddyfile'), 65536) if authority['main'] is not None else None
        directory_metadata = authority['configuration_directory']
        if authority['branch'] == 'preserve-configuration':
            self.possible()
            _, evidence = caddy_files.protected_directory(o)
            if before_main is not None:
                caddy_files.atomic_file(evidence / 'main', before_main, {'uid': 0, 'gid': 0, 'mode': 0o600})
            record = dict(absent=before_main is None, metadata=authority['main'], sha256=authority.get('main_sha256'))
            caddy_files.atomic_file(evidence / 'main.json', json.dumps(record, sort_keys=True).encode(), {'uid': 0, 'gid': 0, 'mode': 0o600})
            self.changed()
        if caddy_package.archive_sha256(o, candidate.path) != candidate.sha256:
            raise ValueError('changed Caddy candidate archive')
        self.possible()
        o._native(('apt-get', 'install', '--yes', '--no-download', '--no-install-recommends', '--no-remove',
                   '-o', 'Dir::Cache::archives=' + str(cache), '-o', 'Dpkg::Options::=--force-confold',
                   '-o', 'APT::Get::AllowUnauthenticated=false', '-o', 'Acquire::AllowInsecureRepositories=false',
                   '-o', 'Acquire::AllowDowngradeToInsecureRepositories=false', 'caddy=' + version), timeout_seconds=300)
        self.changed()
        self.receipt['installed_package_version'] = version
        try:
            installed = self.observe()
        except Exception:
            raise self.observer.CaddyObservationError(8, 'package-failed') from None
        if (installed['branch'] != 'existing' or installed['package_version'] != version or installed['service_active']
                or not installed['service_masked'] or not installed['api_service_masked']
                or installed['site'] != authority['site'] or installed['site_sha256'] != authority['site_sha256']
                or installed['site_directory'] != authority['site_directory']):
            raise self.observer.CaddyObservationError(8, 'package-failed')
        if authority['branch'] == 'preserve-configuration':
            if (installed['configuration_directory'] != directory_metadata or before_main is not None and (
                    installed['main'] != authority['main'] or o._read(o._path('/etc/caddy/Caddyfile'), 65536) != before_main)):
                raise self.observer.CaddyObservationError(8, 'package-failed')
        else:
            # Only this confirmed empty bootstrap may replace this archive's unmodified default.
            if (installed['main'] != {'uid': 0, 'gid': 0, 'mode': 0o644}
                    or o._read(o._path('/etc/caddy/Caddyfile'), 65536) != default):
                raise self.observer.CaddyObservationError(8, 'package-failed')
            caddy_files.atomic_file(o._path('/etc/caddy/Caddyfile'), MAIN_CONTENT.encode(), {'uid': 0, 'gid': 0, 'mode': 0o644})
            self.changed()
            installed = self.observe()
        return installed

    def bootstrap_activate(self):
        self.validate()
        self.boundary = 'activation'
        o = self.observer
        authority = self.observe()
        if (authority['main'] != {'uid': 0, 'gid': 0, 'mode': 0o644}
                or o._read(o._path('/etc/caddy/Caddyfile'), 65536) != MAIN_CONTENT.encode()
                or authority['site_sha256'] != self.receipt['site_sha256']
                or not authority['service_masked'] or not authority['api_service_masked']
                or authority['service_active']):
            raise o.CaddyObservationError(10, 'authority-changed')
        self.possible()
        for name in self.receipt['created_masks']:
            o._native(('systemctl', 'unmask', name), timeout_seconds=3)
            self.changed()
        o._native(('systemctl', 'disable', 'caddy-api.service'), timeout_seconds=3)
        o._native(('systemctl', 'enable', 'caddy.service'), timeout_seconds=3)
        try:
            o._native(('systemctl', 'start', 'caddy.service'), timeout_seconds=30)
        except Exception:
            self.recovery['runtime_uncertain'] = True
            raise
        self.changed()

    def run(self):
        try:
            authority = self.observe()
            self.confirm(authority)
            mode = self.request.parameters['mode']
            if mode == 'guard-install':
                self.guard(authority)
            elif mode == 'prepare':
                if authority['branch'] != 'existing':
                    authority = self.install(authority)
                self.prepare_site(authority)
                if self.request.expected_state['branch'] == 'bootstrap':
                    self.bootstrap_activate()
            else:
                if authority['site_sha256'] != self.receipt['site_sha256'] or authority['site'] is None:
                    self.boundary = 'authority'
                    raise self.observer.CaddyObservationError(10, 'authority-changed')
                if authority['glob_import_present']:
                    self.validate()
                    if authority['service_active'] and not authority['service_masked'] and (
                            self.receipt['site_changed'] or self.request.parameters['public_probe'] != 'ready'):
                        self.boundary = 'activation'
                        self.reload = 'unknown'
                        self.possible()
                        try:
                            self.observer._native(('systemctl', 'reload', 'caddy.service'), timeout_seconds=30)
                        except Exception as error:
                            if isinstance(error, CommandError) and str(error) == 'command exited unsuccessfully':
                                self.reload = 'failed'
                            raise self.observer.CaddyObservationError(8, 'activation-failed') from None
                        self.reload = 'succeeded'
                        self.changed()
            self.boundary = 'inspection'
            return self.result(self.observe())
        except Exception as error:
            boundary, validation = self.boundary, self.validation
            reason = getattr(error, 'reason', {'site': 'site-failed', 'package': 'package-failed',
                'activation': 'activation-failed', 'validation': 'validation-failed', 'mask': 'mask-failed'}.get(boundary, 'inspection-failed'))
            code = getattr(error, 'exit_code', 8 if boundary in {'site', 'package', 'activation', 'validation', 'mask'} else 5)
            if boundary in {'validation', 'activation', 'site'}:
                self.recover()
            self.boundary, self.validation = boundary, validation
            try:
                authority = self.observe()
            except Exception:
                authority = None
            return self.result(authority, code=code, reason=reason)
