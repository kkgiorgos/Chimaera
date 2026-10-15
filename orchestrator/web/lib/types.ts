export interface Machine {
  id: string; name: string; target: string; port: number; repo: string;
  workers: number; cpus: number; memory: string; build_jobs: number; disk: string; kernel: string;
  online?: boolean; deployed?: boolean; seen?: number; error?: string;
  snapshot?: { stats: { Name: string; CPUPerc: string; MemUsage: string; MemPerc: string }[];
    tasks: { id: string; experiment: string; kind: string; status: string; container: string }[] };
}
export interface Attempt {
  id: string; machine: string; started: number; finished?: number; status: string;
  cpus: number; memory: string; collected?: boolean; oom?: boolean; exit_code?: number;
  progress?: { phase?: string; average_speed?: number; simulated_seconds?: number;
    throw_percent?: number; outcome?: string; last_update_age_seconds?: number };
}
export interface Case {
  id: string; status: string; attempts: Attempt[]; eta_seconds?: number | null; eta_basis?: string;
  point: { pattern?: string; pressure?: number; [key: string]: unknown };
}
export interface Experiment {
  id: string; name: string; status: string; machines: string[]; cases: Case[]; created: number;
  config: Record<string, unknown>; eta_seconds?: number | null; eta_basis?: string;
  results_deleted?: boolean; stop_pending?: string[];
  preparations: Record<string, { status: string; task?: string; phase?: string; error?: string }>;
  calibration?: { records: Record<string, { sustainable_gbps: number }> };
}
export interface State {
  machines: Machine[]; experiments: Experiment[]; time: number;
  settings: { progress_enabled: boolean; progress_interval: number };
  events: { id: string; message: string; level: string; time: number; notify: boolean; sent: boolean }[];
  commands: { id: string; status: string; error?: string }[];
  notifications_configured: boolean;
}
