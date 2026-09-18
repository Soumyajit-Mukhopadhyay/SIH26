/**
 * Response shapes for the Researcher Workspace. These mirror the backend's
 * `describe()` payloads in orca.research and orca.ml — presentation reads them,
 * never reshapes them.
 */

export type DatasetKind = 'observation' | 'reanalysis' | 'forecast' | 'derived' | 'model';

export interface Variable {
  name: string;
  unit: string;
  description: string;
}

export interface ResearchDataset {
  id: string;
  title: string;
  provider: string;
  kind: DatasetKind;
  variables: Variable[];
  resolution_deg: number;
  native_resolution: string;
  cadence: string;
  coverage: string;
  access: 'open' | 'free-signup' | 'credentialed' | 'unavailable';
  licence: string;
  provenance: string;
  endpoint: string;
  caveats: string;
  keywords: string[];
  servable: boolean;
  /** Only on discover results: rank, and the exact rules that produced it. */
  score?: number;
  why?: string[];
}

export interface Intent {
  variables: string[];
  bbox: [number, number, number, number] | null;
  place: string | null;
  start: string | null;
  end: string | null;
  kinds: string[];
  purpose?: string | null;
  assumptions: string[];
}

export interface FederatedDataset {
  id: string;
  dataset_id: string;
  title: string;
  provider: string;
  server: string;
  server_key: string;
  protocol: string;
  summary: string;
  endpoint: string;
  info: string;
  coverage_checked: boolean;
  also_on: string[];
  curated: false;
  caveats: string;
}

export interface Federated {
  servers_queried: number;
  servers_responding: number;
  found: number;
  datasets: FederatedDataset[];
  note: string;
}

export interface DiscoverResult {
  question: string;
  intent: Intent;
  parsed_by: string;
  matches: ResearchDataset[];
  federated: Federated | null;
  snippet: string | null;
  note: string;
}

export interface FederatedPreview {
  ok: boolean;
  csv?: string;
  error?: string;
  hint?: string;
  rows_returned?: number;
}

export interface BuoyStation {
  station: string;
  lat: number;
  lon: number;
  sst_degc: number;
  observed_at: string;
  array?: string;
}

export interface BuoyValidation {
  validated: boolean;
  reason?: string;
  station?: string;
  lat?: number;
  lon?: number;
  matched_pairs?: number;
  days_requested?: number;
  bias_degc?: number | null;
  rmse_degc?: number | null;
  max_abs_error_degc?: number | null;
  buoy_mean_degc?: number | null;
  product_mean_degc?: number | null;
  product?: string;
  note?: string;
}

export interface ModelStatus {
  version: string;
  torch_installed: boolean;
  weights_present: boolean;
  weights_path?: string;
  ready: boolean;
  load_error: string | null;
  history_days: number;
  lead_days: number[];
  input_channels?: string[];
  label_source: string;
  not_a_verdict: string;
  training_report: {
    trained_at?: string;
    samples_train?: number;
    samples_val?: number;
    epochs?: number;
    notes?: string;
    metrics?: Record<string, Record<string, number>>;
    baseline?: Record<string, Record<string, number>>;
  } | null;
}

export type ResearchTab = 'discover' | 'catalogue' | 'build' | 'validate' | 'models';
