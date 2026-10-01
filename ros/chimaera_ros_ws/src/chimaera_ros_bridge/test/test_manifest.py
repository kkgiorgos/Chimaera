import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from chimaera_ros.manifest import load
from chimaera_ros.runner import plan, stage, supervise


class SessionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'routes.json').write_text(json.dumps({'version': 1, 'topics': [{}]}))
        self.data = dict(version=1, routes='routes.json',
                         host=dict(domain_id=41, setup=[], processes=[]),
                         guest=dict(domain_id=42, setup=[], processes=[]))

    def load(self, data=None):
        path = self.root / 'session.json'
        path.write_text(json.dumps(data or self.data))
        return load(path)

    def test_plan_domains_and_bridge_routes(self):
        data = self.load()
        host = plan(data, 'host', '/opt/bridge')
        guest = plan(data, 'guest', '/opt/bridge')
        self.assertEqual(host['env']['ROS_DOMAIN_ID'], '41')
        self.assertEqual(guest['env']['ROS_DOMAIN_ID'], '42')
        self.assertIn('config_file:=' + str(self.root / 'routes.json'), host['processes'][0]['command'])
        self.assertNotIn('poll_us:=10000', guest['processes'][0]['command'])
        self.assertEqual(len(host['processes']), 1)
        self.assertNotIn('ratio', data['bridge'])
        self.assertFalse(any(arg.startswith('ratio:=')
                             for arg in host['processes'][0]['command']))

    def test_gazebo_and_detailed_cpu_plan(self):
        self.data['bridge'] = dict(timing_backend='gazebo', gazebo_world='arena',
                                   interval_us=50000, physics_step_ns=1000000,
                                   max_serialized_bytes=131072,
                                   m5ops='instruction', wait_for_application=True, timing_file='timing.csv')
        self.data['simulator'] = dict(gem5_root='gem5', config='custom_gem5.py',
                                     args=['--cpu-type', 'o3', '--num-cores', '4',
                                           '--controller-file', '/tmp/controller settings.yaml'])
        data = self.load()
        host = plan(data, 'host', '/opt/bridge', simulate=True)
        command = host['processes'][0]['command']
        self.assertEqual(command[0], '/opt/bridge/lib/chimaera_ros_bridge/gazebo_host_bridge')
        self.assertIn('physics_step_ns:=1000000', command)
        self.assertNotIn('m5ops:=instruction', command)
        guest = plan(data, 'guest', '/opt/bridge')['processes'][0]['command']
        self.assertIn('m5ops:=instruction', guest)
        self.assertIn('wait_for_application:=true', guest)
        self.assertIn('startup_timeout_s:=300', guest)
        self.assertNotIn('wait_for_application:=true', command)
        self.assertNotIn('timing_file:=' + str(self.root / 'timing.csv'), guest)
        simulator = host['processes'][-1]['command']
        self.assertIn('--managed-shutdown', simulator)
        self.assertEqual(simulator[simulator.index('--cpu-type') + 1], 'o3')
        self.assertEqual(simulator[simulator.index('--num-cores') + 1], '4')
        self.assertIn(str(self.root / 'custom_gem5.py'), simulator)
        self.assertEqual(simulator[simulator.index('-p') + 1],
                         '/opt/bridge/share/chimaera_ros_bridge/config')
        self.assertIn('/tmp/controller settings.yaml', simulator)

    def test_invalid_synchronization_and_simulator_configs(self):
        variants = [dict(timing_backend='unknown'), dict(timing_backend='gazebo'),
                    dict(timing_backend='gazebo', gazebo_world='arena', physics_step_ns=3000000),
                    dict(wait_for_application=1), dict(unknown_bridge_option=True), dict(m5ops='unknown')]
        for bridge in variants:
            with self.subTest(bridge=bridge), self.assertRaises(ValueError):
                self.load(self.data | {'bridge': bridge})
        for simulator in [
            dict(gem5_root='gem5'),
            dict(gem5_root='gem5', config='config.py', cpu_type='timing'),
            dict(gem5_root='gem5', config='config.py', args='--option'),
            dict(gem5_root='gem5', config='config.py', args=['--socket-path=/tmp/other.sock']),
            dict(gem5_root='gem5', config='config.py', args=['--guest-command', '/other/start']),
        ]:
            with self.subTest(simulator=simulator), self.assertRaises(ValueError):
                self.load(self.data | dict(simulator=simulator))

    def test_experiment_arguments_are_opaque(self):
        self.data['simulator'] = dict(gem5_root='gem5', config='riscv_experiment.py',
                                     args=['--custom-architecture', 'RISC-V', '--memory', '8GiB'])
        data = self.load()
        command = plan(data, 'host', '/opt/bridge', simulate=True)['processes'][-1]['command']
        self.assertEqual(command[-4:], self.data['simulator']['args'])
        self.assertIn(str(self.root / 'riscv_experiment.py'), command)

    def test_invalid_config(self):
        variants = []
        for key, value in [('poll_us', 100000), ('steps', True),
                           ('report_seconds', float('inf')), ('ratio', 1.0)]:
            data = copy.deepcopy(self.data)
            data['bridge'] = {key: value}
            variants.append(data)
        for change in ('domain', 'env', 'relative_guest', 'empty_command', 'unknown'):
            data = copy.deepcopy(self.data)
            if change == 'domain': data['guest']['domain_id'] = 41
            if change == 'env': data['host']['env'] = {'ROS_DOMAIN_ID': '42'}
            if change == 'relative_guest': data['guest']['setup'] = ['install/setup.bash']
            if change == 'empty_command': data['host']['processes'] = [dict(name='bad', command=[])]
            if change == 'unknown': data['services'] = []
            variants.append(data)
        for data in variants:
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.load(data)

    def test_stage_relocates_config_and_dereferences_install(self):
        prefix = self.root / 'prefix'
        binary = prefix / 'lib/chimaera_ros_bridge/guest_bridge'
        binary.parent.mkdir(parents=True)
        binary.write_text('#!/bin/sh\nexit 0\n'); binary.chmod(0o755)
        source = self.root / 'app'
        source.mkdir()
        (source / 'original').write_text('application')
        (source / 'linked').symlink_to(source / 'original')
        data = self.load()
        data['deploy']['install_trees'] = [dict(source=str(source), destination='app')]
        staged = stage(data, self.root / 'staged', prefix)
        self.assertEqual((staged / 'app/linked').read_text(), 'application')
        self.assertFalse((staged / 'app/linked').is_symlink())
        reloaded = load(staged / 'session.json')
        self.assertEqual(reloaded['routes'], str(staged / 'routes.json'))
        subprocess.run(['bash', '-n', str(staged / 'guest_start')], check=True)
        with self.assertRaises(ValueError):
            stage(data, self.root / 'staged', prefix)

    def test_supervisor_failure_and_setup_domain_override(self):
        setup = self.root / 'setup.bash'
        setup.write_text('export ROS_DOMAIN_ID=9\n')
        result = supervise(dict(setup=[str(setup)], env={'ROS_DOMAIN_ID': '41'}, processes=[
            dict(name='check', command=['bash', '-c', 'test "$ROS_DOMAIN_ID" = 41; exit 7'])]))
        self.assertEqual(result, 7)

    def test_supervisor_stops_other_process_group(self):
        pid = self.root / 'pid'
        result = supervise(dict(setup=[], env={}, processes=[
            dict(name='background', command=['bash', '-c', 'echo $$ > "$1"; sleep 60', 'bash', str(pid)]),
            dict(name='completed', command=['bash', '-c', 'sleep 0.3'])]))
        self.assertEqual(result, 0)
        with self.assertRaises(ProcessLookupError):
            os.kill(int(pid.read_text()), 0)


if __name__ == '__main__':
    unittest.main()
