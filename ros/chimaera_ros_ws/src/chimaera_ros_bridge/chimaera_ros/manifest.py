"""Strict versioned session configuration. Relative host paths use the manifest directory."""
import json
import math
from pathlib import Path

DEFAULTS = dict(interval_us=100000, poll_us=10000, steps=0,
                startup_timeout_s=300, status_bar=True, report_seconds=1.0,
                timing_socket="/tmp/chimaera_time.sock", max_serialized_bytes=4096, max_pending_messages=128,
                m5ops="address", timing_backend="gem5",
                gazebo_world="", physics_step_ns=1000000, timing_file="")


def fields(value, allowed, required=()):
    if not isinstance(value, dict) or set(value) - set(allowed) or set(required) - set(value):
        raise ValueError(f"Expected object with fields {sorted(allowed)}; required {sorted(required)}")


def string(value):
    if not isinstance(value, str) or not value or '\0' in value:
        raise ValueError("Expected nonempty string without NUL")
    return value


def integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"Expected integer in {low}..{high}")
    return value


def strings(value):
    if not isinstance(value, list):
        raise ValueError("Expected array of strings")
    return [string(item) for item in value]


def load(path):
    path = Path(path).resolve()
    data = json.loads(path.read_text())
    fields(data, {'version', 'routes', 'host', 'guest', 'bridge', 'simulator', 'deploy', 'builds'},
           {'version', 'routes', 'host', 'guest'})
    if type(data['version']) is not int or data['version'] != 1:
        raise ValueError("Unsupported session version")
    def local(value):
        return str((path.parent / string(value)).resolve())
    data['routes'] = local(data['routes'])
    # The C++ bridge remains the authority for route/QoS validation.
    routes = json.loads(Path(data['routes']).read_text())
    if not isinstance(routes, dict) or routes.get('version') != 1 or not routes.get('topics'):
        raise ValueError("Expected version 1 topic route config")
    for side in ('host', 'guest'):
        config = data[side]
        fields(config, {'domain_id', 'setup', 'env', 'processes'}, {'domain_id', 'processes'})
        integer(config['domain_id'], 0, 232)
        config['setup'] = strings(config.get('setup', []))
        if side == 'host':
            config['setup'] = [local(p) for p in config['setup']]
        elif any(not Path(p).is_absolute() for p in config['setup']):
            raise ValueError("Guest setup paths must be absolute guest paths")
        env = config.setdefault('env', {})
        if not isinstance(env, dict):
            raise ValueError("env must be an object")
        for key, value in env.items():
            string(key); string(value)
            if not key.isidentifier() or key in {'ROS_DOMAIN_ID', 'ROS_LOCALHOST_ONLY'}:
                raise ValueError("Invalid or reserved environment key: " + key)
        if not isinstance(config['processes'], list):
            raise ValueError("processes must be an array")
        names = set()
        for process in config['processes']:
            fields(process, {'name', 'command'}, {'name', 'command'})
            name = string(process['name'])
            if name in names:
                raise ValueError("Duplicate process name: " + name)
            names.add(name)
            if not strings(process['command']):
                raise ValueError("Process command cannot be empty")
    if data['host']['domain_id'] == data['guest']['domain_id']:
        raise ValueError("Host and guest ROS domains must differ")
    bridge = data.setdefault('bridge', {})
    fields(bridge, DEFAULTS)
    bridge = data['bridge'] = DEFAULTS | bridge
    integer(bridge['max_serialized_bytes'], 4, 64 * 1024 * 1024 - 2056)
    integer(bridge['max_pending_messages'], 1, 1000000)
    integer(bridge['interval_us'], 2, 3600000000)
    integer(bridge['poll_us'], 1, bridge['interval_us'] - 1)
    integer(bridge['steps'], 0, 2**63 - 1)
    integer(bridge['startup_timeout_s'], 1, 2**31 - 1)
    value = bridge['report_seconds']
    if type(value) not in (float, int) or not math.isfinite(value) or value <= 0:
        raise ValueError("report_seconds must be finite and positive")
    if type(bridge['status_bar']) is not bool or not Path(string(bridge['timing_socket'])).is_absolute():
        raise ValueError("Invalid status_bar or timing_socket")
    if bridge['m5ops'] not in ('address', 'instruction'):
        raise ValueError("m5ops must be address or instruction")
    if bridge['timing_backend'] not in ('gem5', 'gazebo'):
        raise ValueError("timing_backend must be gem5 or gazebo")
    integer(bridge['physics_step_ns'], 1, 3600000000000)
    if bridge['timing_backend'] == 'gazebo':
        string(bridge['gazebo_world'])
        if bridge['interval_us'] * 1000 % bridge['physics_step_ns']:
            raise ValueError("interval_us must contain an integral number of physics steps")
    elif not isinstance(bridge['gazebo_world'], str):
        raise ValueError("gazebo_world must be a string")
    if not isinstance(bridge['timing_file'], str) or '\0' in bridge['timing_file']:
        raise ValueError("timing_file must be a path string")
    if bridge['timing_file']:
        bridge['timing_file'] = local(bridge['timing_file'])
    if 'simulator' in data:
        sim = data['simulator']
        fields(sim, {'gem5_root', 'config', 'args', 'outdir'}, {'gem5_root', 'config'})
        for key in ('gem5_root', 'config', 'outdir'):
            if key in sim:
                sim[key] = local(sim[key])
        sim['args'] = strings(sim.get('args', []))
        # Transport flags belong to the session runner. Hardware/workload arguments
        # are otherwise opaque and interpreted by the experiment's gem5 config.
        reserved = {'--gem5-root', '--socket-path', '--guest-command', '--managed-shutdown'}
        if any(arg.split('=', 1)[0] in reserved for arg in sim['args']):
            raise ValueError("Simulator args cannot override session transport/startup flags")
    deploy = data.setdefault('deploy', {})
    fields(deploy, {'guest_root', 'install_trees'})
    deploy.setdefault('guest_root', '/opt/chimaera/session')
    root = Path(string(deploy['guest_root']))
    if not root.is_absolute() or '..' in root.parts or str(root) == '/':
        raise ValueError("guest_root must be an absolute non-root guest path")
    trees = deploy.setdefault('install_trees', [])
    if not isinstance(trees, list):
        raise ValueError("install_trees must be an array")
    destinations = set()
    for tree in trees:
        fields(tree, {'source', 'destination'}, {'source', 'destination'})
        tree['source'] = local(tree['source'])
        dest = Path(string(tree['destination']))
        if dest.is_absolute() or '..' in dest.parts or str(dest) == '.':
            raise ValueError("Install destination must be relative to guest_root")
        for previous in destinations:
            if dest == previous or dest in previous.parents or previous in dest.parents:
                raise ValueError("Overlapping install destinations")
        if dest.parts[0] in {'runtime', 'session.json', 'routes.json', 'guest_start'}:
            raise ValueError("Install destination overlaps session runtime")
        destinations.add(dest)
    builds = data.setdefault('builds', [])
    if not isinstance(builds, list):
        raise ValueError("builds must be an array")
    for build in builds:
        fields(build, {'sources', 'output', 'setup', 'colcon_args'}, {'sources', 'output', 'setup'})
        build['sources'] = [local(p) for p in strings(build['sources'])]
        if not build['sources']:
            raise ValueError("Build sources cannot be empty")
        build['output'] = local(build['output'])
        build['setup'] = [local(p) for p in strings(build['setup'])]
        build['colcon_args'] = strings(build.get('colcon_args', []))
        if any(p.startswith(('--install-base', '--build-base', '--log-base', '--base-paths'))
               for p in build['colcon_args']):
            raise ValueError("Build output/source overrides belong in the manifest")
    return data
