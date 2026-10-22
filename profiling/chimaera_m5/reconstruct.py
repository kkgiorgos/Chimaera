#!/usr/bin/env python3
"""Archive an explicit baseline git tree and emit build instructions; never run it."""
import argparse
from pathlib import Path
import subprocess

from common import sha256, write_json

REVISIONS = dict(
    original_m5='a37b6ab0a7bd1109577b217b6f73db695aac6788',
    # Original pilot is pre-streamed-protocol. Streamed/buffer pilots also have
    # retained uncommitted source snapshots; use those exact archives instead
    # of pretending the eventual squashed commit recreates their pilot trees.
    original_dds_dma='3f6bc0e',
    copy_reduced_dds_dma='c3393c82527d077e9bd41082a155703eefe93eca',
    scheduled_dds_dma='912ae01ec5b1f3bec0ef4427b8756d0a160b9f43')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--implementation', choices=REVISIONS, required=True)
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    revision = subprocess.check_output(['git', 'rev-parse', REVISIONS[args.implementation]],
                                       cwd=args.repo, text=True).strip()
    archive = args.output / 'source.tar.gz'
    paths = ['gem5-transport', 'ros/chimaera_ros_ws/src/chimaera_ros_bridge']
    if args.implementation != 'original_m5':
        paths.append('ros/chimaera_ros_ws/src/chimaera_dds_transport')
    subprocess.run(['git', 'archive', '--format=tar.gz', '-o', str(archive.resolve()), revision,
                    *paths], cwd=args.repo, check=True)
    write_json(args.output / 'reconstruction.json', dict(implementation=args.implementation,
        revision=revision, archive_sha256=sha256(archive),
        qualification='Source reconstruction only; not historical binary identity or measured equivalence.',
        build=dict(packages=['chimaera_ros_bridge'] if args.implementation == 'original_m5' else
                   ['chimaera_dds_transport', 'chimaera_ros_bridge'],
            cmake_args=['-DCMAKE_BUILD_TYPE=Release', '-DBUILD_TESTING=OFF',
                        '-DBUILD_GAZEBO_BRIDGE=ON', '-DBUILD_LATENCY_BENCHMARK=ON',
                        '-DGEM5_TRANSPORT_ROOT=ABSOLUTE_EXTRACTED_SOURCE/gem5-transport',
                        '-DGEM5_ROOT=PINNED_GEM5_ROOT', '-DGEM5_M5_LIBRARY=PINNED_LIBM5']),
        next_steps=['Extract into a new directory, source the pinned ROS/RMW SDK and colcon build.',
            'Preserve host and guest bridge/controller protocol pairing; do not link current transport '
            'libraries into a pre-streamed bridge.',
            'Stage the identical extended endpoint/common benchmark assets into new copies.',
            'Fresh-boot and qualify capability, source and delivery semantics after the catch gate.',
            'If lossless or current runner protocol cannot be supported faithfully, keep that case '
            'blocked/incompatible and report the historical latest-policy evidence separately.']))


if __name__ == '__main__':
    main()
