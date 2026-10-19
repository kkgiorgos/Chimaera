"""Ownership and ordering of one booted gem5 VM per hardware configuration."""
import os
import json
import signal
import socket
import subprocess
import tarfile


HARDWARE = dict(cpu_type='kvm', num_cores=2, cpu_clock='3GHz', l1d_size='16KiB',
                l1i_size='16KiB', l2_size='256KiB', l1_assoc=8, l2_assoc=16)


def validate_bundle(bundle):
    if bundle is None:
        raise ValueError('Single-boot sweeps require a staged v4 --guest-bundle; rebuild and stage the runtime')
    try:
        with tarfile.open(bundle) as archive:
            with archive.extractfile('opt/chimaera/latency/latency-runtime.json') as source:
                version = json.load(source).get('version')
        if version != 4:
            raise ValueError('outdated guest bundle')
    except (OSError, KeyError, AttributeError, TypeError, ValueError, tarfile.TarError) as error:
        raise ValueError('Single-boot sweeps require a staged v4 guest bundle; rebuild and stage the runtime') from error


def hardware_key(config):
    return tuple(config.get(key, default) for key, default in HARDWARE.items())


def ordered_cases(cases, enabled):
    if not enabled:
        return cases
    # Only one VM may own the fixed transport paths at a time. Keep each
    # hardware group together; retain case IDs for reports and resumed jobs.
    return sorted(cases, key=lambda case: (case['config']['mode'] == 'chimaera',
        hardware_key(case['config']) if case['config']['mode'] == 'chimaera' else ()))


class GuestSession:
    def __init__(self):
        self.process = None
        self.log = None
        self.directory = None

    def command(self, command, timeout):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(timeout)
            connection.connect('/tmp/chimaera_time.sock')
            connection.sendall((command + '\n').encode())
            response = bytearray()
            while not response.endswith(b'\n'):
                data = connection.recv(1)
                if not data or len(response) >= 4096:
                    raise RuntimeError('Incomplete gem5 session response')
                response.extend(data)
            value = response.decode().strip()
            if value.startswith('ERROR '):
                raise RuntimeError(value)
            return value

    def close(self):
        if self.process is not None:
            if self.process.poll() is None:
                try:
                    self.command('QUIT', 5)
                except (OSError, RuntimeError):
                    pass
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(self.process.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    try:
                        self.process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        try:
                            os.killpg(self.process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        self.process.wait()
            self.process = None
        if self.log:
            self.log.close()
            self.log = None


class SweepSessions:
    def __init__(self, enabled=True):
        self.enabled = enabled
        self.key = None
        self.guest = None

    def for_case(self, config):
        key = hardware_key(config) if self.enabled and config['mode'] == 'chimaera' else None
        if key != self.key:
            self.close()
            self.key = key
            if key is not None:
                self.guest = GuestSession()
        return self.guest

    def close(self):
        if self.guest:
            self.guest.close()
        self.guest = None
        self.key = None

    def __enter__(self):
        return self

    def __exit__(self, *error):
        self.close()
