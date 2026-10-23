"""Ball catching: detailed execution, readiness checkpoints and KVM smoke validation."""
import argparse
import json
import os
from pathlib import Path
import re

import m5
from gem5.components.boards.x86_board import X86Board
from gem5.components.boards.mem_mode import MemMode
from gem5.components.cachehierarchies.ruby.mesi_two_level_cache_hierarchy import MESITwoLevelCacheHierarchy
from gem5.components.memory.ramulator2 import Ramulator2Memory
from gem5.components.processors.cpu_types import CPUTypes
from gem5.components.processors.simple_processor import SimpleProcessor
from gem5.components.processors.simple_core import SimpleCore
from gem5.components.processors.switchable_processor import SwitchableProcessor
from gem5.isas import ISA
from gem5.resources.resource import DiskImageResource, KernelResource
from gem5.simulate.simulator import Simulator
from gem5.simulate.exit_event import ExitEvent

from chimaera_gem5 import TimingServer, add_chimaera_arguments, configure_ticks, guest_start_script

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--gem5-root', required=True)
parser.add_argument('--experiment', required=True)
parser.add_argument('--bundle', required=True)
parser.add_argument('--image', required=True)
parser.add_argument('--kernel', required=True)
parser.add_argument('--instantiate-only', action='store_true',
                    help='Instantiate the full system at tick zero, without booting or simulating')
add_chimaera_arguments(parser, guest_command='/opt/chimaera/ball_catching/guest_start')
args = parser.parse_args()
experiment = json.loads(Path(args.experiment).read_text())
hardware = experiment['hardware']
cpu_type = hardware.get('cpu_type', 'timing')
if cpu_type not in ('kvm', 'timing', 'o3'):
    parser.error('hardware cpu_type must be kvm, timing or o3')
if cpu_type == 'kvm' and experiment['aggressor']['gbps']:
    parser.error('KVM is a functional smoke mode and cannot measure memory pressure')
configure_ticks()
restore = experiment.get('checkpoint_restore')
capture = experiment.get('checkpoint_prepare')
kvm_readiness = bool(capture and experiment.get('fixed', {}).get('checkpoint_readiness_cpu') == 'kvm')
if restore and capture:
    parser.error('Cannot prepare and restore a checkpoint together')
if cpu_type == 'kvm' and (restore or capture):
    parser.error('KVM functional validation requires a fresh boot')
if args.transport_backend == 'dma' and (restore or capture):
    parser.error('DMA requires a fresh boot; readiness checkpoints are not supported')
if capture and (experiment['aggressor']['pattern'] != 'none' or experiment['aggressor']['gbps']):
    parser.error('Readiness checkpoints require a disabled aggressor')
# Keep the detailed CPU paths identical on producer and consumers. The restore
# graph contains no KVM CPUs or KvmVM object, including switched-out objects.
if cpu_type == 'kvm':
    processor = SimpleProcessor(cpu_type=CPUTypes.KVM, isa=ISA.X86,
                                num_cores=hardware['num_cores'])
    for core in processor.get_cores():
        core.get_simobject().usePerf = False
else:
    cores = {'detailed': [SimpleCore(cpu_type=CPUTypes(cpu_type), core_id=i, isa=ISA.X86)
                          for i in range(hardware['num_cores'])]}
    if not restore:
        cores['boot'] = [SimpleCore(cpu_type=CPUTypes.KVM, core_id=i, isa=ISA.X86)
                         for i in range(hardware['num_cores'])]
        for core in cores['boot']:
            core.get_simobject().usePerf = False
    processor = SwitchableProcessor(switchable_cores=cores,
                                    starting_cores='detailed' if restore else 'boot')
CacheHierarchy = MESITwoLevelCacheHierarchy
if args.transport_backend == 'dma':
    from chimaera_dma import DmaCacheHierarchy, attach_dma
    CacheHierarchy = DmaCacheHierarchy
cache = CacheHierarchy(l1d_size=hardware['l1d_size'],
    l1i_size=hardware['l1i_size'], l1d_assoc=hardware['l1_assoc'],
    l1i_assoc=hardware['l1_assoc'], l2_size=hardware['l2_size'],
    l2_assoc=hardware['l2_assoc'], num_l2_banks=1, functional_checkpoint_writeback=True)
aggressor = {'aggressor_' + key: value for key, value in experiment['aggressor'].items()}
aggressor['aggressor_enabled'] = False
memory = Ramulator2Memory(experiment['ramulator_config'], size='3GiB', aggressor=aggressor)
board = X86Board(clk_freq=hardware['cpu_clock'], processor=processor,
                 memory=memory, cache_hierarchy=cache)
board.set_mem_mode(MemMode.TIMING if restore else MemMode.ATOMIC_NONCACHING)
# The board uses a gem5 COW disk overlay. Stage into the guest at KVM boot;
# the user's base image is never mounted or modified.
preamble = 'chimaera_root mkdir -p /opt/chimaera\n'
files = ['/tmp/chimaera-ball.tar.gz=' + args.bundle, *args.guest_file]
boot = guest_start_script('/tmp/chimaera-ball-start', files, preamble=preamble)
board.set_kernel_disk_workload(kernel=KernelResource(local_path=args.kernel),
    disk_image=DiskImageResource(local_path=args.image),
    kernel_args=['earlyprintk=ttyS0', 'console=ttyS0', 'lpj=7999923', 'root=/dev/sda2', 'mce=off']
        + (['acpi=off'] if args.transport_backend in ('dma', 'm5ops') else []),
    readfile_contents=boot, checkpoint=Path(restore) / 'cpt' if restore else None)
if args.transport_backend == 'dma':
    attach_dma(board, args.dma_h2g, args.dma_g2h)
elif args.transport_backend == 'm5ops':
    from chimaera_m5 import attach_m5
    attach_m5(board, args.dma_h2g, args.dma_g2h)

if args.instantiate_only:
    # Use the board's documented direct-instantiation lifecycle. No timing
    # server is opened and no call to m5.simulate()/Simulator.run() is made.
    root = board._pre_instantiate()
    m5.instantiate()
    board._post_instantiate()
    record = dict(cpu_type=cpu_type, num_cores=hardware['num_cores'], tick=m5.curTick(),
                  simulated=False)
    (Path(m5.options.outdir) / 'cpu_check.json').write_text(json.dumps(record, indent=2) + '\n')
    print('Full-system configuration instantiated without simulation: ' + json.dumps(record))
    raise SystemExit(0)

def record_cpu(expected=None):
    expected = expected or cpu_type
    active = processor.get_cores()
    if len(active) != hardware['num_cores'] or any(core.get_type().value != expected for core in active):
        raise RuntimeError('Detailed CPU switch did not select the requested model')
    (Path(m5.options.outdir) / 'cpu_roi.json').write_text(json.dumps(dict(
        cpu_type=expected, requested_cpu_type=cpu_type, num_cores=len(active),
        simobjects=[core.get_simobject().type for core in active], tick=m5.curTick()), indent=2) + '\n')


def prepare_clock():
    serial = (Path(m5.options.outdir) / 'board.pc.com_1.device').read_text(errors='replace')
    serial = re.sub(r'\[\s*\d+\.\d+\] [^\r\n]*(?:\r?\n|$)', '', serial)
    counts = re.findall(r'^CHIMAERA_ONLINE_CPUS=(\d+)\r?$', serial, re.MULTILINE)
    if not counts or int(counts[-1]) != hardware['num_cores']:
        raise RuntimeError('Guest CPU count differs from the fixed architecture; inspect serial log')
    if cpu_type != 'kvm' and not kvm_readiness:
        processor.switch_to_processor('detailed')
    record_cpu('kvm' if kvm_readiness else cpu_type)
    # Preserve one clock origin across readiness, the optional CPU switch and throw.
    # Boot readfile is consumed before workbegin. Reuse the existing m5 readfile
    # mechanism to give the guest the exact origin used by the timing server.
    boot_file = Path(str(board.readfile))
    (Path(m5.options.outdir) / 'boot-readfile.sh').write_bytes(boot_file.read_bytes())
    origin = m5.curTick() // 1000
    boot_file.write_text(f'CHIMAERA_TIME_ORIGIN_NS {origin}\n')
    (Path(m5.options.outdir) / 'roi_time_origin.json').write_text(json.dumps({'nanoseconds': origin}) + '\n')

class BallTimingServer(TimingServer):
    detailed_started = False
    pressure_started = False
    restore_boundary_tick = None
    checkpoint_pending = False

    def begin_pressure(self):
        if not self.roi_started or self.finished or self.detailed_started or self.pressure_started or capture:
            raise RuntimeError('Cannot enable pressure outside a restored prelaunch session')
        for controller in memory.get_memory_controllers():
            controller.setAggressorEnabled(True)
        self.pressure_started = True
        (Path(m5.options.outdir) / 'pressure_start.json').write_text(
            json.dumps({'tick': m5.curTick(), 'frequency': 1e12}) + '\n')
        return f'PRESSURE {m5.curTick()}'

    def begin_throw(self):
        if not self.roi_started or self.finished:
            raise RuntimeError('Cannot launch outside an active timing session')
        if self.detailed_started:
            raise RuntimeError('Detailed throw already started')
        if capture:
            raise RuntimeError('Checkpoint preparation must never throw the ball')
        if restore and not self.pressure_started:
            raise RuntimeError('Restored launch requires the pressure warmup barrier')
        record_cpu()
        if not restore:
            self.begin_pressure()
        self.detailed_started = True
        m5.stats.reset()
        (Path(m5.options.outdir) / 'measurement_start.json').write_text(
            json.dumps({'tick': m5.curTick(), 'frequency': 1e12}) + '\n')
        return f'READY {m5.curTick()}'

    def run_for_ticks(self, ticks):
        if self.restore_boundary_tick is None:
            return super().run_for_ticks(ticks)
        start = self.restore_boundary_tick
        remaining = max(0, start + ticks - m5.curTick())
        response = super().run_for_ticks(remaining)
        self.restore_boundary_tick = None
        if response.startswith('OK '):
            return f'OK {start} {m5.curTick()}'
        return response

    def _run_once(self):
        event = super()._run_once()
        if event == ExitEvent.CHECKPOINT and self.checkpoint_pending:
            self.finished = False
        if not self.roi_started:
            serial = (Path(m5.options.outdir) / 'board.pc.com_1.device').read_text(errors='replace')
            if 'CHIMAERA_GUEST_APPLICATION_EXIT=' in serial:
                raise RuntimeError('Guest application exited before workbegin; inspect serial log')
        return event

    def save_ready_checkpoint(self):
        if not capture or restore or not self.roi_started or self.detailed_started:
            raise RuntimeError('Not a readiness checkpoint preparation session')
        if args.transport_backend == 'm5ops':
            from readiness_checkpoint import records_drained
            if not records_drained(capture):
                raise RuntimeError('Application record prefix has not drained at the checkpoint barrier')
        boundary = m5.curTick()
        directory = Path(capture)
        stage = directory / 'checkpoint-stage.json'
        stage.write_text(json.dumps({'phase': 'Waiting for guest checkpoint barrier'}) + '\n')
        Path(str(board.readfile)).write_text('CHIMAERA_CHECKPOINT\n')
        self.checkpoint_pending = True
        # A bounded guest-only interval lets the bridge finish its current
        # exchange and reset its handshake before issuing m5 checkpoint.
        limit = boundary + 1_000_000_000_000
        while m5.curTick() < limit and not self.finished:
            self.simulator.set_max_ticks(limit - m5.curTick())
            if self._run_once() == ExitEvent.CHECKPOINT:
                break
        else:
            raise RuntimeError('Guest did not reach the checkpoint transport barrier')
        self.checkpoint_pending = False
        if kvm_readiness:
            processor.switch_to_processor('detailed')
        record_cpu()
        stage.write_text(json.dumps({'phase': 'Saving guest memory and device state'}) + '\n')
        m5.checkpoint(str(directory / 'cpt'))
        origin = json.loads((Path(m5.options.outdir) / 'roi_time_origin.json').read_text())
        (directory / 'checkpoint.json').write_text(json.dumps(dict(
            version=2 if args.transport_backend == 'm5ops' else 1,
            transport_backend=args.transport_backend, hardware=hardware, boundary_tick=boundary, saved_tick=m5.curTick(),
            readiness_cpu='kvm' if kvm_readiness else cpu_type,
            time_origin_ns=origin['nanoseconds'], aggressor_enabled=False), indent=2) + '\n')
        stage.write_text(json.dumps({'phase': 'Readiness checkpoint saved'}) + '\n')
        return f'CHECKPOINT {m5.curTick()}'

    def exit_handlers(self):
        return {**super().exit_handlers(), ExitEvent.CHECKPOINT: lambda: True}

    def handle_command(self, line):
        if line == 'BENCHMARK_RECORDS_READY':
            if (not restore or args.transport_backend != 'm5ops' or self.pressure_started or
                    not (Path(m5.options.outdir).parent / 'checkpoint-record-readers-ready').is_file()):
                raise RuntimeError('Record reader barrier requires m5ops restore')
            Path(str(board.readfile)).write_text('CHIMAERA_CHECKPOINT_RECORDS_READY\n')
            return 'RECORDS READY'
        if line in ('BENCHMARK_FREEZE', 'BENCHMARK_RECORDS_DRAINED'):
            if not capture or restore or args.transport_backend != 'm5ops':
                raise RuntimeError('Application freeze requires m5ops checkpoint preparation')
            if line == 'BENCHMARK_FREEZE':
                Path(str(board.readfile)).write_text('CHIMAERA_CHECKPOINT_FREEZE\n')
                return 'FREEZING'
            from readiness_checkpoint import records_drained
            return 'DRAINED ' + str(int(records_drained(capture)))
        if line == 'CHIMAERA_EGRESS' and args.transport_backend == 'm5ops':
            if not self.roi_started or self.finished:
                raise ValueError('m5ops egress requires a live, paused ROI')
            return 'EGRESS ' + str(board.chimaera_m5.flush_egress())
        if line == 'CHIMAERA_INGRESS' and args.transport_backend in ('dma', 'm5ops'):
            if not self.roi_started or self.finished:
                raise ValueError('DMA ingress requires a live, paused ROI')
            if args.transport_backend == 'dma':
                board.chimaera_dma.ingress()
            else:
                board.chimaera_m5.ingress()
                if os.environ.get('CHIMAERA_M5_DIAGNOSTICS'):
                    count = getattr(self, '_m5_diagnostics_count', 0) + 1
                    self._m5_diagnostics_count = count
                    if count <= 4 or count % 256 == 0:
                        with (Path(m5.options.outdir) / 'chimaera-m5-state.txt').open('a') as log:
                            log.write(board.chimaera_m5.diagnostics() + '\n')
            return 'OK INGRESS'
        if line == 'BENCHMARK_RESTORE':
            if not restore or self.restore_boundary_tick is None or self.pressure_started:
                raise RuntimeError('No pending restored host boundary')
            return f'RESTORE {self.restore_boundary_tick} {m5.curTick()}'
        if line == 'BENCHMARK_BEGIN':
            return self.begin_throw()
        if line == 'BENCHMARK_PRESSURE':
            return self.begin_pressure()
        if line == 'BENCHMARK_CHECKPOINT':
            return self.save_ready_checkpoint()
        if line == 'BENCHMARK_DUMP':
            m5.stats.dump()
            return f'DUMP {m5.curTick()}'
        return super().handle_command(line)

timing = BallTimingServer(on_ready=prepare_clock)
simulator = Simulator(board=board, on_exit_event=timing.exit_handlers())
if restore:
    record = json.loads((Path(restore) / 'checkpoint.json').read_text())
    if (record['version'] != (2 if args.transport_backend == 'm5ops' else 1) or
            record.get('transport_backend', 'm5') != args.transport_backend or
            record['hardware'] != hardware or record['aggressor_enabled'] is not False):
        raise RuntimeError('Readiness checkpoint is incompatible with the requested architecture')
    simulator._instantiate()
    # Complete Ruby/cache and device startup while the aggressor is still
    # disabled. Otherwise the first pressured step would also rebuild caches.
    m5.simulate(0)
    if m5.curTick() != record['saved_tick']:
        raise RuntimeError('Restored checkpoint tick differs from its metadata')
    timing.simulator = simulator
    timing.roi_started = True
    timing.restore_boundary_tick = record['boundary_tick']
    Path(str(board.readfile)).write_text(f'CHIMAERA_TIME_ORIGIN_NS {record["time_origin_ns"]}\n')
    (Path(m5.options.outdir) / 'roi_time_origin.json').write_text(
        json.dumps({'nanoseconds': record['time_origin_ns']}) + '\n')
    record_cpu()
    timing.serve(args.socket_path, managed_shutdown=args.managed_shutdown)
else:
    timing.run(simulator, args.socket_path, managed_shutdown=args.managed_shutdown)
