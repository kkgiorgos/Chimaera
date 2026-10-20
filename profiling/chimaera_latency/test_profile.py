"""Exercise trace identity, clock separation, and missing-path accounting."""
import csv
import io
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import analyze


class AttributionTest(unittest.TestCase):
    def receipt(self):
        return dict(side='guest', stream='synthetic', sequence=7, scheduled_ns=0,
                    publish_ns=100, callback_ns=700)

    def rows(self):
        return [dict(side=side, stage=stage, event='I', id=0, stream=0,
                     direction=0, sequence=7, scheduled_ns=0, sim_ns=time, os_ns=999)
                for (side, stage, _), time in zip(analyze.PATHS[0][:-1], [200, 200, 400, 450, 500])]

    def test_stage_means_close_to_same_message_latency(self):
        result = analyze.decompose([self.receipt()], self.rows())['host_to_guest/synthetic']
        self.assertEqual(result['complete_paths'], 1)
        self.assertAlmostEqual(sum(s['mean_ms'] for s in result['stages'].values()),
                               result['latency']['mean_ms'])

    def test_missing_stage_is_censored_and_skew_is_reported(self):
        key = 'host_to_guest/synthetic'
        partial = analyze.decompose([self.receipt()], self.rows()[:-1])[key]
        self.assertEqual(partial['complete_paths'], 0)
        self.assertEqual(partial['incomplete_paths'], 1)
        rows = self.rows()
        rows[0]['sim_ns'] = 99
        skewed = analyze.decompose([self.receipt()], rows)[key]
        self.assertEqual(skewed['nonmonotonic_paths'], 1)
        self.assertIsNone(skewed['latency']['mean_ms'])

    def test_wall_partition_ignores_nested_worker_spans_and_closes(self):
        rows = []

        def span(stage, identity, begin, end):
            for event, wall in [('B', begin), ('E', end)]:
                rows.append(dict(side='host', stage=stage, id=identity, event=event,
                                 sim_ns=0, os_ns=wall))

        span('whole_step', 1, 0, 1000)
        names = ['host_boundary_ingress', 'gem5_phase', 'host_boundary_egress',
                 'settle_before_physics', 'gazebo_request', 'gazebo_wait',
                 'settle_after_physics']
        for index, name in enumerate(names):
            span(name, index+2, index*100+1, index*100+101)
        span('host_batch_encode', 100, 115, 130)
        result = analyze.wall_phases(sorted(rows, key=lambda r:r['os_ns']), 0, 1)
        self.assertEqual(result['steps'], 1)
        self.assertAlmostEqual(sum(r['mean_ms'] for r in result['phases'].values()),
                               result['whole_step']['mean_ms'])
        self.assertEqual(result['phases']['unassigned']['mean_ms'], 300/1e6)

    @unittest.skipUnless(shutil.which('g++'), 'C++20 compiler required')
    def test_real_cpp_trace_preserves_packet_identity_and_clocks(self):
        source = r'''
#include "profile.hpp"
#include <iostream>
int main() {
    setenv("CHIMAERA_PROFILE", "1", 1);
    using namespace chimaera::profile;
    initialize("guest");
    state().clock = [] { return uint64_t{123456}; };
    state().writer = [](const std::string& text) { std::cout << text; };
    std::vector<std::byte> payload(56);
    const uint64_t words[] = {0x3141544144424c43ULL, 7, 42, 50, 0};
    for (int j=0;j<5;++j) for(int i=0;i<8;++i)
        payload[16+j*8+i] = std::byte(words[j] >> (8*i));
    chimaera::profile::payload("callback", "UInt8MultiArray host_to_guest", payload);
    std::vector<std::byte> typed(12);
    typed[1] = std::byte{1}; typed[4] = std::byte{2}; typed[8] = std::byte{42};
    chimaera::profile::payload("typed", "/ball/left_image/ host_to_guest", typed);
    finish();
}'''
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root/'test.cpp').write_text(source)
            subprocess.run(['g++', '-std=c++20', '-pthread', '-I', str(Path(__file__).parent),
                            str(root/'test.cpp'), '-o', str(root/'test')], check=True)
            text = subprocess.check_output([str(root/'test')], text=True)
        rows = list(csv.DictReader(io.StringIO(text)))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['sequence'], '7')
        self.assertEqual(rows[0]['scheduled_ns'], '42')
        self.assertEqual(rows[0]['sim_ns'], '123456')
        self.assertGreater(int(rows[0]['os_ns']), int(rows[0]['sim_ns']))
        self.assertEqual(rows[1]['stream'], '1')
        self.assertEqual(rows[1]['scheduled_ns'], '2000000042')


if __name__ == '__main__':
    unittest.main()
