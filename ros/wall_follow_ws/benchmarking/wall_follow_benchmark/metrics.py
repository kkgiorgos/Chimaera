"""Focused presentation metrics; raw diagnostic metrics remain in exports."""
TASK = {
    'rmse_m': 'Wall tracking RMSE (m)',
    'mae_m': 'Wall tracking MAE (m)',
    'max_abs_error_m': 'Maximum wall error (m)',
    'path_m': 'Progress: distance travelled (m)',
    'gt_coverage': 'Ground-truth coverage (fraction)',
}
ARCHITECTURE = {
    'gem5_ipc': 'IPC (instructions / summed core-cycles)',
    'gem5_instructions': 'Executed instructions (all cores)',
    'gem5_instructions_per_sim_second': 'System throughput (instructions / simulated s)',
    'gem5_cycles': 'Core-cycles (sum across cores)',
    'gem5_l1i_mpki': 'L1 instruction MPKI (all cores)',
    'gem5_l1d_mpki': 'L1 data MPKI (all cores)',
    'gem5_l2_mpki': 'L2 MPKI (all banks)',
    'gem5_l1i_misses': 'L1 instruction cache misses (all cores)',
    'gem5_l1d_misses': 'L1 data cache misses (all cores)',
    'gem5_l2_misses': 'L2 cache misses (all banks)',
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
