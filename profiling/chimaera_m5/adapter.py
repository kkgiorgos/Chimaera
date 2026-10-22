"""Install endpoint-only workload adapters into the parent's selected orchestration.

No bridge, guest-session or gem5 configuration is changed here. In particular
the parent must implement m5ops backend support before any run can succeed.
"""
from common import PARAMETERS, streams
from legacy import LEGACY_IMPLEMENTATIONS, selected_implementation


def install(latency):
    original_parameters, original_routes = latency.parameters, latency.routes
    original_traffic = latency.traffic
    original_profile = latency.dds_profile
    original_validate_bundle = latency.validate_bundle
    from pathlib import Path
    import sys
    rewrite = Path(__file__).resolve().parents[1] / 'chimaera_rewrite'
    sys.path.insert(0, str(rewrite))
    from dds_large_messages import large_message_profile

    active = {}

    def parameters(path, values):
        if path.name in ('host.yaml', 'guest.yaml'):
            values = dict(values, **active.get('benchmark_parameters', PARAMETERS))
        return original_parameters(path, values)

    def traffic(config):
        p = config.get('benchmark_parameters', PARAMETERS)
        result = [s for s in original_traffic(config)
                  if p['cameras_enabled'] or s['name'] not in ('left_image', 'right_image')]
        for name, (side, rate, body) in streams(config).items():
            if not name.startswith('bulk_image_'):
                continue
            result.append(dict(id=6 if side == 'host' else 7, name=name, label=name,
                topic='/latency/ball/' + name, type='sensor_msgs/msg/Image',
                direction='host_to_guest' if side == 'host' else 'guest_to_host',
                rate_hz=rate, body_bytes=body, depth=128 if config['delivery_policy'] == 'lossless' else 5,
                coalesce=False))
        return result

    def routes(reliability='reliable', depth=128, config=None):
        result = original_routes(reliability, depth, config)
        if not config:
            return result
        if not config['benchmark_parameters']['cameras_enabled']:
            result['topics'] = [r for r in result['topics'] if not any(
                '/ball/' + eye + '/' in r['host_topic'] for eye in ('left_image', 'right_image'))]
        for stream in traffic(config):
            if not stream['name'].startswith('bulk_image_'):
                continue
            root = '/latency/ball/' + stream['name']
            result['topics'].append(dict(host_topic=root + '/host', guest_topic=root + '/guest',
                type=stream['type'], direction=stream['direction'], coalesce=False,
                qos=dict(history='keep_last', depth=stream['depth'], durability='volatile',
                         reliability='reliable' if config['delivery_policy'] == 'lossless' else 'best_effort')))
        if active.get('implementation') in LEGACY_IMPLEMENTATIONS:
            from legacy import adapt_routes
            result = adapt_routes(result)
        return result

    def profile(config):
        if config.get('dds_tuning') == 'large_shm_only':
            return large_message_profile(config['dds_shm_bytes'], config['dds_max_message_bytes'])
        return original_profile(config)

    def validate_bundle(bundle, backend='m5', required_features=()):
        implementation = active.get('implementation')
        if implementation not in LEGACY_IMPLEMENTATIONS:
            return original_validate_bundle(bundle, backend=backend, required_features=required_features)
        selected_implementation(implementation, active['selection']['selection'])
        if backend != 'm5':
            raise ValueError('Legacy qualification adapter is restricted to m5')
        from legacy import validate
        proof = validate(bundle, active['selection'], required_features,
                         implementation=implementation, sdk_image=active['sdk_image'])
        active['legacy_validation'] = proof
        return proof

    latency.parameters, latency.routes, latency.traffic, latency.dds_profile = parameters, routes, traffic, profile
    latency.validate_bundle = validate_bundle
    return active
