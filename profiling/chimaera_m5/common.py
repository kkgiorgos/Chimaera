"""Pure data and provenance helpers; importing this module runs no experiment."""
import hashlib
import json
import math
from pathlib import Path

VERSION = 1
UNSET = 2**64 - 1
PERFORMANCE_VARIANTS = (
    'original_m5', 'streamed_m5', 'initialized_m5_ready', 'integrated_dma',
    'original_dds_dma', 'copy_reduced_dds_dma', 'scheduled_dds_dma', 'direct_dds_m5ops',
)
DIAGNOSTIC_VARIANTS = ('initialized_m5',)
VARIANTS = (*PERFORMANCE_VARIANTS, *DIAGNOSTIC_VARIANTS)
IMPLEMENTATION_MODES = {
    'original_m5': ('m5', 'bridge'), 'streamed_m5': ('m5', 'bridge'),
    'initialized_m5': ('m5', 'bridge'), 'initialized_m5_ready': ('m5', 'bridge'),
    'integrated_dma': ('dma', 'bridge'),
    'original_dds_dma': ('dma', 'fastdds'), 'copy_reduced_dds_dma': ('dma', 'fastdds'),
    'scheduled_dds_dma': ('dma', 'fastdds'), 'direct_dds_m5ops': ('m5ops', 'fastdds'),
}
# Eight main comparisons; the separately pinned final pair remains diagnostic.
GEOMETRY = dict(num_cores=2, cpu_clock='3GHz', l1d_size='16KiB', l1i_size='16KiB',
                l2_size='256KiB', l1_assoc=8, l2_assoc=16)
BALL = dict(camera_width=480, camera_height=360, camera_hz=30., joint_hz=250.,
            control_hz=250., image_age_periods=2.)
PARAMETERS = dict(cameras_enabled=True, bulk_bytes=0, bulk_hz=1., bulk_burst=1,
                  bulk_direction='both')
COMMON_FILES = ('lib/chimaera_ros_bridge/latency_endpoint',
                'lib/libchimaera_latency_clock.so', 'lib/libchimaera_gem5_clock.so',
                'lib/libchimaera_gem5_clock_addr.so', 'lib/librmw_fastrtps_cpp.so',
                'lib/librmw_fastrtps_shared_cpp.so')
IMPLEMENTATION_FILES = ('lib/chimaera_ros_bridge/latency_driver',
                        'lib/chimaera_ros_bridge/guest_bridge')
MATCH_FIELDS = ('workload', 'ball_catching', 'duration', 'warmup', 'drain',
                'physics_step_ns', 'interval_us', 'poll_us', 'reliability', 'depth',
                'delivery_policy', 'verify_payload', 'benchmark_parameters',
                'dds_shm_bytes', 'dds_tuning', 'dds_max_message_bytes',
                'cpu_type', *GEOMETRY)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')
    temporary.replace(path)


def read_json(path, default=None):
    path = Path(path)
    return json.loads(path.read_text()) if path.is_file() else default


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for data in iter(lambda: handle.read(4 * 1024 * 1024), b''):
            digest.update(data)
    return digest.hexdigest()


def tree_hashes(root):
    root = Path(root)
    return {str(path.relative_to(root)): sha256(path) for path in sorted(root.rglob('*'))
            if path.is_file() and '__pycache__' not in path.parts and path.suffix != '.pyc'}


def number(value, label, minimum=0, integer=False):
    if type(value) not in ((int,) if integer else (int, float)) or not math.isfinite(value):
        raise ValueError(label + ' must be finite' + (' integer' if integer else ''))
    if value < minimum:
        raise ValueError(label + ' is too small')
    return value


def validate_config(config):
    for key in ('duration', 'drain', 'rate_hz'):
        number(config[key], key, 1e-9)
    number(config['warmup'], 'warmup')
    for key in ('payload_bytes', 'physics_step_ns', 'interval_us', 'poll_us'):
        number(config[key], key, 0 if key == 'payload_bytes' else 1, True)
    if config['workload'] != 'ball_catching' or config['ball_catching'] != BALL:
        raise ValueError('Matched suite requires fixed ball camera/control/clock geometry')
    if config['interval_us'] != 2000 or config['physics_step_ns'] != 1000000:
        raise ValueError('Matched suite requires 2ms boundaries and 1ms physics')
    if any(config.get(key) != value for key, value in GEOMETRY.items()):
        raise ValueError('CPU geometry differs from the established comparison')
    if config['cpu_type'] not in ('timing', 'kvm') or config.get('single_boot') is not False:
        raise ValueError('Explicit timing/KVM CPU and fresh boots are required')
    if config['delivery_policy'] not in ('lossless', 'latest'):
        raise ValueError('Unknown delivery policy')
    if type(config['verify_payload']) is not bool:
        raise ValueError('Explicit boolean payload verification is required')
    p = config['benchmark_parameters']
    if set(p) != set(PARAMETERS) or type(p['cameras_enabled']) is not bool:
        raise ValueError('Incomplete benchmark endpoint parameters')
    number(p['bulk_bytes'], 'bulk_bytes', 0, True)
    number(p['bulk_hz'], 'bulk_hz', 1e-9)
    number(p['bulk_burst'], 'bulk_burst', 1, True)
    if p['bulk_bytes'] > 16 * 1024**2 or p['bulk_burst'] > 128 or p['bulk_hz'] > 10000:
        raise ValueError('Bulk request exceeds endpoint bounds')
    if p['bulk_bytes'] and p['bulk_bytes'] < 16:
        raise ValueError('Verified bulk markers require at least 16 bytes')
    if p['bulk_direction'] not in ('both', 'h2g', 'g2h'):
        raise ValueError('Unknown bulk direction')
    if config['mode'] not in ('chimaera', 'native_free', 'native_stepped'):
        raise ValueError('Unknown execution mode')


def match_key(config, native=False):
    fields = [key for key in MATCH_FIELDS if not native or key not in ('cpu_type', *GEOMETRY)]
    return canonical({key: config.get(key) for key in fields})


def streams(config):
    p = config.get('benchmark_parameters', PARAMETERS)
    result = {}
    if p['cameras_enabled']:
        result.update(left_image=('host', 30., 480 * 360 * 3),
                      right_image=('host', 30., 480 * 360 * 3))
    result.update(joint_states=('host', 250., None), effort_command=('guest', 250., None),
                  clock=('host', 1000., None))
    if p['bulk_bytes']:
        for direction, side in (('h2g', 'host'), ('g2h', 'guest')):
            if p['bulk_direction'] in ('both', direction):
                result['bulk_image_' + direction] = (side, p['bulk_hz'], p['bulk_bytes'])
    return result
