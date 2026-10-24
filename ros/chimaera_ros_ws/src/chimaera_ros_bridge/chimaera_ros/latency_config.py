"""Validated sweep plans shared by the dashboard's preview and executor."""
import copy
import itertools
import math
import re

from .latency import MODES, validate
from .latency_workloads import BALL_DEFAULT, traffic
from .latency_budget import wall_budget

HARDWARE = dict(cpu_type='kvm', num_cores=2, cpu_clock='3GHz', l1d_size='16KiB',
                l1i_size='16KiB', l2_size='256KiB', l1_assoc=8, l2_assoc=16)
DEFAULT = dict(version=1, name='Bridge latency sweep', modes=list(MODES),
    workload='synthetic', ball_catching=dict(BALL_DEFAULT),
    payload_bytes=[64], rate_hz=[100.], interval_us=[50000, 20000], poll_us=[10000, 5000],
    duration=.5, warmup=.1, drain=.2, physics_step_ns=1000000,
    reliability='reliable', depth=128, timeout=300, repeats=1,
    gem5_configs=[dict(HARDWARE, name='KVM')], single_boot=True,
    transport_backend='m5', dma_module='', m5_module='', middleware_transport='bridge',
    dma_module_options='buffer_mb=8 receive_slots=16 transmit_slots=4',
    delivery_policy='latest', verify_payload=False, dds_shm_bytes=0, trace=False)


def number(value, name, low, high, integer=False):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f'{name} must be between {low:g} and {high:g}')
    if integer and type(value) is not int:
        raise ValueError(f'{name} must be an integer')
    return value


def memory(value, name, associativity):
    if not isinstance(value, str):
        raise ValueError(f'{name} must include units, for example 32KiB')
    match = re.fullmatch(r'([1-9][0-9]*)(B|KiB|MiB)', value)
    if not match:
        raise ValueError(f'{name} must be a positive integer with B, KiB or MiB units')
    size = int(match[1]) * {'B': 1, 'KiB': 1024, 'MiB': 1024**2}[match[2]]
    if not 64 <= size <= 512 * 1024**2 or size % (64 * associativity):
        raise ValueError(f'{name} must be divisible by its associativity × 64 bytes, and at most 512MiB')
    return value


def hardware(value):
    if not isinstance(value, dict) or set(value) - (set(HARDWARE) | {'name'}):
        raise ValueError('Unknown gem5 configuration field')
    result = dict(HARDWARE, **value)
    result.setdefault('name', result['cpu_type'].upper())
    if not isinstance(result['name'], str) or not 1 <= len(result['name'].strip()) <= 80:
        raise ValueError('Give each gem5 configuration a name of 1–80 characters')
    result['name'] = result['name'].strip()
    if result['cpu_type'] not in ('kvm', 'timing', 'o3'):
        raise ValueError('CPU model must be kvm, timing or o3')
    number(result['num_cores'], 'Core count', 1, 64, True)
    for key in ('l1_assoc', 'l2_assoc'):
        number(result[key], key, 1, 64, True)
        if result[key] & (result[key] - 1):
            raise ValueError('Cache associativity must be a power of two')
    if not isinstance(result['cpu_clock'], str) or not re.fullmatch(r'[0-9]+(?:\.[0-9]+)?(?:GHz|MHz|kHz|Hz)', result['cpu_clock']):
        raise ValueError('CPU clock must include frequency units, for example 3GHz')
    if float(re.match(r'[0-9.]+', result['cpu_clock'])[0]) <= 0:
        raise ValueError('CPU clock must be positive')
    for key in ('l1d_size', 'l1i_size', 'l2_size'):
        memory(result[key], key, result['l2_assoc' if key == 'l2_size' else 'l1_assoc'])
    return result


def plan_sweep(value):
    if not isinstance(value, dict) or set(value) - set(DEFAULT):
        raise ValueError('Unknown sweep configuration field')
    config = copy.deepcopy(DEFAULT)
    config.update(value)
    if type(config['single_boot']) is not bool:
        raise ValueError('single_boot must be boolean')
    if type(config['trace']) is not bool:
        raise ValueError('trace must be boolean')
    if config['trace'] and (config['modes'] != ['chimaera'] or config['middleware_transport'] != 'fastdds'):
        raise ValueError('Transport tracing requires Chimaera Fast DDS cases only')
    if config['transport_backend'] in ('dma', 'm5ops') and config['single_boot']:
        raise ValueError('DMA/m5ops requires single_boot=false (fresh boots)')
    if config['middleware_transport'] not in ('bridge', 'fastdds'):
        raise ValueError('middleware_transport must be bridge or fastdds')
    if config['middleware_transport'] == 'fastdds' and config['single_boot']:
        raise ValueError('Fast DDS middleware routing requires single_boot=false')
    if config['version'] != 1 or type(config['version']) is not int:
        raise ValueError('Unsupported sweep version')
    if not isinstance(config['name'], str) or not 1 <= len(config['name'].strip()) <= 120:
        raise ValueError('Sweep name must contain 1–120 characters')
    config['name'] = config['name'].strip()
    if not isinstance(config['modes'], list) or not config['modes'] or any(mode not in MODES for mode in config['modes']):
        raise ValueError('Select at least one valid execution mode')
    config['modes'] = list(dict.fromkeys(config['modes']))
    if config['workload'] not in ('synthetic', 'ball_catching'):
        raise ValueError('Workload must be synthetic or ball_catching')
    settings = config['ball_catching']
    if not isinstance(settings, dict) or set(settings)-set(BALL_DEFAULT):
        raise ValueError('Unknown ball-catching traffic setting')
    settings = config['ball_catching'] = dict(BALL_DEFAULT, **settings)
    for key in ('camera_width', 'camera_height'):
        number(settings[key], key, 64, 4096, True)
    for key in ('camera_hz', 'joint_hz', 'control_hz'):
        number(settings[key], key, .001, 10000)
    number(settings['image_age_periods'], 'Image age periods', .001, 1000)
    if settings['camera_width']*settings['camera_height']*3 > 16*1024**2:
        raise ValueError('RGB image data must fit within 16MiB')
    if config['workload'] == 'ball_catching':
        config.update(payload_bytes=[settings['camera_width']*settings['camera_height']*3],
                      rate_hz=[settings['camera_hz']])
    for key, low, high, integer in [('payload_bytes', 0, 16*1024**2, True),
            ('rate_hz', .000001, 10000, False), ('interval_us', 2, 3600000000, True),
            ('poll_us', 1, 3599999999, True)]:
        values = config[key]
        if not isinstance(values, list) or not 1 <= len(values) <= 64:
            raise ValueError(f'{key} needs 1–64 values')
        for item in values:
            number(item, key, low, high, integer)
        config[key] = list(dict.fromkeys(values))
    for key, low, high, integer in [('repeats', 1, 100, True), ('duration', 1e-9, 3600, False),
            ('warmup', 0, 3600, False), ('drain', 1e-9, 3600, False),
            ('physics_step_ns', 1, 3600000000000, True), ('depth', 1, 128, True),
            ('timeout', 1, 86400, True)]:
        number(config[key], key, low, high, integer)
    if config['reliability'] not in ('reliable', 'best_effort'):
        raise ValueError('Reliability must be reliable or best_effort')
    profiles = config['gem5_configs']
    if not isinstance(profiles, list) or not 1 <= len(profiles) <= 16:
        raise ValueError('Add 1–16 gem5 configurations')
    config['gem5_configs'] = [hardware(profile) for profile in profiles]
    if len({profile['name'] for profile in config['gem5_configs']}) != len(profiles):
        raise ValueError('Gem5 configuration names must be unique')
    pairs = [(interval, poll) for interval in config['interval_us'] for poll in config['poll_us'] if poll < interval]
    if 'chimaera' in config['modes'] and not pairs:
        raise ValueError('At least one polling interval must be shorter than a synchronization interval')
    for interval in config['interval_us']:
        if interval * 1000 % config['physics_step_ns'] or interval * 1000 // config['physics_step_ns'] > 2**32 - 1:
            raise ValueError('Each synchronization interval must contain an integral, supported number of physics steps')
    if config['workload'] == 'ball_catching':
        if max(settings['camera_hz'], settings['joint_hz'], settings['control_hz']) * config['physics_step_ns'] > 1e9:
            raise ValueError('Ball-catching rates must not exceed the physics step rate')
        if settings['control_hz'] > settings['joint_hz']:
            raise ValueError('Control rate must not exceed joint feedback rate')
        if 1e9/config['physics_step_ns'] > 10000:
            raise ValueError('Ball-catching clock traffic is limited to 10,000 Hz')
    base = {key: config[key] for key in ('duration', 'warmup', 'drain', 'physics_step_ns', 'reliability', 'depth', 'timeout', 'workload', 'ball_catching',
        'transport_backend', 'middleware_transport', 'dma_module', 'dma_module_options', 'm5_module',
        'delivery_policy', 'verify_payload', 'dds_shm_bytes', 'trace')}
    cases = []
    for body, rate, repeat in itertools.product(config['payload_bytes'], config['rate_hz'], range(1, config['repeats'] + 1)):
        for mode in config['modes']:
            timings = ([(config['interval_us'][0], min(config['poll_us'][0], config['interval_us'][0] - 1))]
                if mode == 'native_free' else [(interval, min(config['poll_us'][0], interval - 1)) for interval in config['interval_us']]
                if mode == 'native_stepped' else pairs)
            for interval, poll in timings:
                for profile in config['gem5_configs'] if mode == 'chimaera' else [None]:
                    point = dict(base, mode=mode, payload_bytes=body, rate_hz=rate, repeat=repeat,
                                 interval_us=interval, poll_us=poll)
                    if profile:
                        point.update({key: value for key, value in profile.items() if key != 'name'})
                        point['gem5_name'] = profile['name']
                    validate(point)
                    cases.append(dict(id=f'case_{len(cases)+1:04d}', config=point))
                    if len(cases) > 4096:
                        raise ValueError('The sweep exceeds 4096 cases; reduce its dimensions or repetitions')
    warnings = (['Some cases request fewer than 10,000 messages per direction. Tail estimates may be unstable.']
                if min(config['rate_hz']) * config['duration'] < 10000 else [])
    warned = set()
    for case in cases:
        point = case['config']
        budget = wall_budget(point)
        case['wall_budget'] = budget
        identity = point['mode'], point['interval_us']
        if point['mode']!='native_free' and identity not in warned and budget['estimated_seconds'] > config['timeout']:
            warned.add(identity)
            label = 'native stepping' if point['mode']=='native_stepped' else 'Chimaera synchronization'
            warnings.append(f"{point['interval_us']/1000:g} ms {label} needs about "
                f"{budget['estimated_seconds']:.0f} wall seconds for {budget['synchronization_steps']} steps "
                f"with the observed Fortress statistics cadence. The {config['timeout']} s timeout may stop it early; "
                f"allow at least {budget['recommended_timeout']} s or shorten the window. This estimate excludes extra host load and detailed gem5 execution.")
    return dict(config=config, cases=cases, total=len(cases),
        counts={mode: sum(case['config']['mode'] == mode for case in cases) for mode in MODES},
        skipped_pairs=[dict(interval_us=interval, poll_us=poll)
            for interval in config['interval_us'] for poll in config['poll_us'] if poll >= interval],
        simulated_seconds=sum(config['warmup'] + config['duration'] + config['drain'] for _ in cases),
        expected_receipts_per_case=[rate * config['duration'] for rate in config['rate_hz']],
        traffic=traffic(config),
        recommended_timeout=max(wall_budget(case['config'])['recommended_timeout'] for case in cases),
        warnings=warnings)
