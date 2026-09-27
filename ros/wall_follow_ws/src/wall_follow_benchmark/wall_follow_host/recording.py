"""Host-side recording and process observation; this module has no ROS imports."""
import csv
import json
import hashlib
import math
from pathlib import Path
import platform

NAN = float('nan')
FIELDS = ('sim_time elapsed wall_elapsed dt_sim dt_wall control_hz target_distance '
          'pose_stamp x y linear_cmd angular_cmd '
          'host_scan_stamp host_scan_age').split()


class Recorder:
    def __init__(self, directory, parameters, duration, provenance=None):
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError('duration must be positive and finite')
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.parameters = dict(parameters)
        self.duration = duration
        self.start = self.wall_start = self.last_sim = self.last_wall = None
        self.pose = dict(pose_stamp=NAN, x=NAN, y=NAN)
        self.count = 0
        self.closed = False
        self.samples_file = (self.directory / 'samples.csv').open('x', newline='')
        try:
            self.poses_file = (self.directory / 'poses.csv').open('x', newline='')
        except BaseException:
            self.samples_file.close()
            raise
        self.samples = csv.DictWriter(self.samples_file, fieldnames=FIELDS)
        self.samples.writeheader()
        self.poses = csv.DictWriter(self.poses_file, fieldnames=['sim_time', 'stamp', 'x', 'y'])
        self.poses.writeheader()
        self.metadata = dict(schema_version=3, implementation='cpp', parameters=dict(parameters),
            final_parameters=dict(parameters), duration=duration, completed=False,
            parameter_events=[], observation='host_command_receipt',
            host=dict(platform=platform.platform(), machine=platform.machine()),
            provenance=provenance or {},
            host_source_sha256={p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                for p in Path(__file__).parent.glob('*.py')},
            unavailable=['guest_compute_ms', 'guest_scan_age', 'guest_internal_state'])
        self.save_metadata()

    def save_metadata(self):
        temporary = self.directory / 'metadata.json.tmp'
        temporary.write_text(json.dumps(self.metadata, indent=2, allow_nan=False) + '\n')
        temporary.replace(self.directory / 'metadata.json')

    def update_parameters(self, updates, sim):
        self.parameters.update(updates)
        self.metadata['final_parameters'] = dict(self.parameters)
        self.metadata['parameter_events'].append(dict(sim_time=sim, parameters=dict(self.parameters)))
        self.save_metadata()

    def record_pose(self, sim, stamp, x, y):
        self.pose = dict(pose_stamp=stamp, x=x, y=y)
        self.poses.writerow(dict(sim_time=sim, stamp=stamp, x=x, y=y))
        self.poses_file.flush()

    def record_command(self, sim, wall, linear, angular, host_scan_stamp=NAN):
        if self.start is None:
            self.start, self.wall_start = sim, wall
        if self.last_sim is not None and sim < self.last_sim:
            raise ValueError('Simulation clock moved backwards; start a new attempt')
        row = dict.fromkeys(FIELDS, NAN)
        row.update(sim_time=sim, elapsed=sim-self.start, wall_elapsed=wall-self.wall_start,
                   dt_sim=sim-self.last_sim if self.last_sim is not None else NAN,
                   dt_wall=wall-self.last_wall if self.last_wall is not None else NAN,
                   control_hz=self.parameters['control_hz'],
                   target_distance=self.parameters['target_distance'],
                   linear_cmd=linear, angular_cmd=angular,
                   **self.pose, host_scan_stamp=host_scan_stamp, host_scan_age=sim-host_scan_stamp)
        self.samples.writerow(row)
        self.samples_file.flush()
        self.last_sim, self.last_wall = sim, wall
        self.count += 1

    def finish(self, completed=False, reason='interrupted'):
        if self.closed:
            return
        self.samples_file.close()
        self.poses_file.close()
        self.metadata.update(completed=bool(completed and self.count >= 2 and self.last_sim > self.start),
                             finish_reason=reason, sample_count=self.count)
        self.save_metadata()
        self.closed = True
