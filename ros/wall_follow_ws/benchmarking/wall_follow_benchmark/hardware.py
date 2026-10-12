"""Hardware parameters for the post-workbegin gem5 region."""
import re

DEFAULTS = dict(cpu_type='timing', cpu_clock='3GHz', num_cores=2,
                l1d_size='16KiB', l1i_size='16KiB', l2_size='256KiB',
                l1_assoc=8, l2_assoc=16,
                memory_backend='gem5', ramulator_config='',
                aggressor_pattern='none', aggressor_interval=4, aggressor_stride=4096,
                aggressor_read_percent=80, aggressor_max_pending=128,
                aggressor_window=268435456, aggressor_seed=1,
                aggressor_duty_percent=100, aggressor_period=12000)


def validate(params):
    if params['cpu_type'] not in ('timing', 'o3'):
        raise ValueError('cpu_type must be timing or o3 (KVM is used only for boot)')
    clock = re.fullmatch(r'([0-9]+(?:\.[0-9]+)?)(MHz|GHz)', params['cpu_clock'])
    if not clock or float(clock[1]) <= 0:
        raise ValueError('cpu_clock must be a positive MHz or GHz frequency')
    if not 1 <= params['num_cores'] <= 64:
        raise ValueError('num_cores must be in [1, 64]')
    for level, assoc_key in (('l1d', 'l1_assoc'), ('l1i', 'l1_assoc'), ('l2', 'l2_assoc')):
        match = re.fullmatch(r'([1-9][0-9]*)(KiB|MiB)', params[level + '_size'])
        if not match:
            raise ValueError(f'{level}_size must be a positive KiB or MiB size')
        size = int(match[1]) * (1024 if match[2] == 'KiB' else 1024**2)
        assoc = params[assoc_key]
        sets, remainder = divmod(size, 64 * assoc) if assoc > 0 else (0, 1)
        if remainder or sets < 1 or sets & (sets - 1):
            raise ValueError(f'{level} requires positive associativity and a power-of-two number of 64-byte sets')

    if params['memory_backend'] not in ('gem5', 'ramulator2'):
        raise ValueError('memory_backend must be gem5 or ramulator2')
    if bool(params['ramulator_config']) != (params['memory_backend'] == 'ramulator2'):
        raise ValueError('ramulator2 requires ramulator_config; gem5 does not accept it')
    if params['aggressor_pattern'] not in ('none', 'stream', 'stride', 'random'):
        raise ValueError('unknown aggressor_pattern')
    if params['aggressor_pattern'] != 'none' and params['memory_backend'] != 'ramulator2':
        raise ValueError('aggressor requires ramulator2')
    for name in ('interval', 'stride', 'max_pending', 'window', 'seed', 'period'):
        value = params['aggressor_' + name]
        if type(value) is not int or not 1 <= value <= 2**32 - 1:
            raise ValueError('aggressor_' + name + ' must be a positive uint32')
    for name in ('read_percent', 'duty_percent'):
        if type(params['aggressor_' + name]) is not int or not 0 <= params['aggressor_' + name] <= 100:
            raise ValueError('aggressor_' + name + ' must be in [0, 100]')
    if params['aggressor_stride'] % 64 or params['aggressor_window'] % 64:
        raise ValueError('aggressor stride and window must align to 64 bytes')
