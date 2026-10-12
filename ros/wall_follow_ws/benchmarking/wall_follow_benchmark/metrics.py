"""Focused presentation metrics; raw diagnostic metrics remain in exports."""
TASK = {
    'first_command_sim_s': 'First command receipt (world simulated s)',
    'command_gap_p95_ms': 'Command receipt gap p95 (simulated ms)',
    'command_gap_max_ms': 'Maximum command receipt gap (simulated ms)',
    'command_late_fraction': 'Command gaps > 1.5 control periods (fraction)',
    'host_scan_age_p95_ms': 'Host scan age at command receipt p95 (ms)',
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

MEMORY = {
    'gem5_aggressor_gbps': 'Achieved aggressor traffic (GB/s)',
    'gem5_cpu_dram_read_ns': 'Guest DRAM read latency mean (ns)',
    'gem5_aggressor_blocked_fraction': 'Aggressor offers blocked (fraction)',
    'gem5_aggressor_requests': 'Accepted aggressor transactions',
    'gem5_aggressor_rejected': 'Blocked aggressor offers',
    'gem5_cpu_dram_reads': 'Completed guest DRAM reads',
    'gem5_cpu_dram_retries': 'Guest DRAM request retries',
}
