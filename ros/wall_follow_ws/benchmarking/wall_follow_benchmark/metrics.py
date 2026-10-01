"""Focused presentation metrics; raw diagnostic metrics remain in exports."""
TASK = {
    'rmse_m': 'Wall tracking RMSE (m)',
    'mae_m': 'Wall tracking MAE (m)',
    'max_abs_error_m': 'Maximum wall error (m)',
    'path_m': 'Progress: distance travelled (m)',
    'gt_coverage': 'Ground-truth coverage (fraction)',
}
ARCHITECTURE = {
    'gem5_ipc': 'Instructions per cycle (IPC)',
    'gem5_instructions': 'Executed instructions',
    'gem5_cycles': 'CPU cycles',
    'gem5_l1i_mpki': 'L1 instruction misses / 1,000 instructions',
    'gem5_l1d_mpki': 'L1 data misses / 1,000 instructions',
    'gem5_l2_mpki': 'L2 misses / 1,000 instructions',
    'gem5_l1i_misses': 'L1 instruction cache misses',
    'gem5_l1d_misses': 'L1 data cache misses',
    'gem5_l2_misses': 'L2 cache misses',
}
SIMULATION = {
    'timing_cosim_realtime_factor': 'Overall co-simulation rate (sim s / wall s)',
    'timing_gem5_phase_realtime_factor': 'gem5 phase rate (sim s / phase wall s)',
    'timing_gazebo_phase_realtime_factor': 'Gazebo phase rate (sim s / phase wall s)',
    'timing_gem5_wall_seconds': 'gem5 phase wall time (s)',
    'timing_gazebo_wall_seconds': 'Gazebo phase wall time (s)',
    'timing_other_wall_seconds': 'Settling / other wall time (s)',
    'timing_elapsed_wall_seconds': 'Co-simulation elapsed wall time (s)',
    'timing_startup_wall_seconds': 'Startup wall time (s)',
    'real_time_factor': 'Collection rate (sim s / wall s)',
}
