/**
 * The researcher's workspace — the second audience the problem statement names.
 *
 * A fisherman and a researcher want opposite things from the same system. The
 * fisherman wants one sentence and a colour. The researcher wants the array, its
 * provenance, its caveats, and something they can paste into a paper. Trying to
 * serve both from one panel produces a screen that is too technical to act on
 * and too shallow to cite, so this is a full-screen mode rather than another
 * floating rail.
 *
 * Three things it does that a dataset list does not:
 *
 * **It shows the parse.** When you ask in prose, the panel shows the variables,
 * the box and the dates it extracted BEFORE the results — and every assumption
 * it had to make. A researcher who cannot see how their question was interpreted
 * cannot tell a good match from a lucky one.
 *
 * **It leads with the caveats.** Every entry states what it is not good for.
 * A catalogue that lists only capabilities is how someone ends up compositing
 * monthly chlorophyll to answer a daily bloom question.
 *
 * **It gives a citation and a snippet, not just a download.** The output of a
 * data search is rarely the data — it is the ability to get the data again,
 * reproducibly, and to say where it came from.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  AlertTriangle,
  Binary,
  BookMarked,
  BrainCircuit,
  Check,
  Copy,
  Database,
  Download,
  ExternalLink,
  Eye,
  Globe,
  Loader2,
  Search,
  Sparkles,
  Thermometer,
  X,
} from 'lucide-react';
import { clsx } from 'clsx';

/* ------------------------------------------------------------------ types */

interface Variable {
  name: string;
  unit: string;
  description: string;
}

export interface ResearchDataset {
  id: string;
  title: string;
  provider: string;
  kind: 'observation' | 'reanalysis' | 'forecast' | 'derived' | 'model';
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
  score?: number;
  why?: string[];
}

interface Intent {
  variables: string[];
  bbox: [number, number, number, number] | null;
  place: string | null;
  start: string | null;
  end: string | null;
  kinds: string[];
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

interface Federated {
  servers_queried: number;
  servers_responding: number;
  found: number;
  datasets: FederatedDataset[];
  note: string;
}

interface DiscoverResult {
  question: string;
  intent: Intent;
  parsed_by: string;
  matches: ResearchDataset[];
  federated: Federated | null;
  snippet: string | null;
  note: string;
}

interface BuoyValidation {
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

interface ModelStatus {
  version: string;
  torch_installed: boolean;
  weights_present: boolean;
  ready: boolean;
  load_error: string | null;
  history_days: number;
  lead_days: number[];
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

const KIND_TONE: Record<ResearchDataset['kind'], string> = {
  observation: 'text-live border-live/35 bg-live/10',
  reanalysis: 'text-violet border-violet/35 bg-violet/10',
  forecast: 'text-cyan border-cyan/35 bg-cyan/10',
  derived: 'text-amber border-amber/35 bg-amber/10',
  model: 'text-jade border-jade/35 bg-jade/10',
};

const EXAMPLES = [
  'chlorophyll blooms in the Bay of Bengal during the monsoon',
  'wave height forecasts off the Kerala coast for the next week',
  'thermal fronts near Tamil Nadu over the last 30 days',
  'scatterometer winds over the Arabian Sea in 2025',
  'what can I get to study upwelling off Gujarat?',
];

/* ------------------------------------------------------------- small parts */

function Chip({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <span
      className={clsx(
        'data inline-flex items-center gap-1 rounded border px-1.5 py-px text-2xs whitespace-nowrap',
        className,
      )}
    >
      {children}
    </span>
  );
}

function CopyButton({ text, label = 'copy' }: { text: string; label?: string }) {
  const [done, setDone] = useState(false);
  return (
    <button
      type="button"
      onClick={() => {
        void navigator.clipboard
          .writeText(text)
          .then(() => {
            setDone(true);
            window.setTimeout(() => setDone(false), 1400);
          })
          .catch(() => {
            /* clipboard is blocked in some embeds; the text is on screen anyway */
          });
      }}
      className="border-hairline text-ink-2 hover:text-cyan hover:border-cyan/40 flex items-center gap-1 rounded border px-1.5 py-0.5 text-2xs transition-colors"
    >
      {done ? <Check className="h-2.5 w-2.5" aria-hidden /> : <Copy className="h-2.5 w-2.5" aria-hidden />}
      {done ? 'copied' : label}
    </button>
  );
}

/* ---------------------------------------------------------- dataset record */

function DatasetCard({
  dataset,
  intent,
  onExport,
  exporting,
}: {
  dataset: ResearchDataset;
  intent: Intent | null;
  onExport: (d: ResearchDataset) => void;
  exporting: string | null;
}) {
  const [open, setOpen] = useState(false);
  return (
    <article className="border-hairline bg-abyss-1 hover:border-hairline-strong rounded border transition-colors">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-start gap-2.5 px-3 py-2.5 text-left"
      >
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-ink-0 text-xs font-semibold">{dataset.title}</span>
            <Chip className={KIND_TONE[dataset.kind]}>{dataset.kind}</Chip>
            {dataset.servable && (
              <Chip className="text-jade border-jade/35 bg-jade/10">ORCA serves this</Chip>
            )}
            {dataset.access === 'free-signup' && (
              <Chip className="text-amber border-amber/35 bg-amber/10">free account</Chip>
            )}
          </div>
          <div className="text-ink-2 mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-2xs">
            <span>{dataset.provider}</span>
            <span className="data">{dataset.resolution_deg}° · {dataset.cadence}</span>
            {dataset.why?.length ? (
              <span className="text-cyan">{dataset.why[0]}</span>
            ) : null}
          </div>
        </div>
        {typeof dataset.score === 'number' && (
          <span className="data text-ink-3 shrink-0 text-2xs">{dataset.score.toFixed(1)}</span>
        )}
      </button>

      {open && (
        <div className="border-hairline space-y-2.5 border-t px-3 py-2.5">
          <div className="flex flex-wrap gap-1">
            {dataset.variables.map((v) => (
              <Chip key={v.name} className="border-hairline text-ink-1">
                {v.name} <span className="text-ink-3">{v.unit}</span>
              </Chip>
            ))}
          </div>

          {/* Caveats first, and in amber. A catalogue that leads with
              capabilities is how a monthly composite ends up answering a daily
              question. */}
          <p className="text-amber flex items-start gap-1.5 text-2xs leading-relaxed">
            <AlertTriangle className="mt-px h-3 w-3 shrink-0" aria-hidden />
            <span>{dataset.caveats}</span>
          </p>

          <dl className="text-2xs grid grid-cols-[7rem_minmax(0,1fr)] gap-x-3 gap-y-1">
            <dt className="text-ink-3">Native</dt>
            <dd className="text-ink-1">{dataset.native_resolution}</dd>
            <dt className="text-ink-3">Coverage</dt>
            <dd className="text-ink-1">{dataset.coverage}</dd>
            <dt className="text-ink-3">Licence</dt>
            <dd className="text-ink-1">{dataset.licence}</dd>
            <dt className="text-ink-3">Endpoint</dt>
            <dd className="data text-ink-2 break-all">{dataset.endpoint}</dd>
          </dl>

          <div className="flex flex-wrap items-center gap-1.5">
            {dataset.servable && (
              <button
                type="button"
                onClick={() => onExport(dataset)}
                disabled={exporting === dataset.id}
                className="border-cyan/40 bg-cyan/12 text-cyan hover:bg-cyan/20 flex items-center gap-1 rounded border px-2 py-0.5 text-2xs transition-colors disabled:opacity-40"
              >
                {exporting === dataset.id ? (
                  <Loader2 className="h-2.5 w-2.5 animate-spin" aria-hidden />
                ) : (
                  <Download className="h-2.5 w-2.5" aria-hidden />
                )}
                download CSV
              </button>
            )}
            <CopyButton text={dataset.endpoint} label="copy endpoint" />
            <CopyButton
              text={`${dataset.provider}. ${dataset.title}. Accessed via ORCA. ${dataset.licence}.`}
              label="copy citation"
            />
          </div>
          {intent?.bbox && dataset.servable && (
            <p className="text-ink-3 text-2xs">
              Download uses the box from your question:{' '}
              <span className="data">{intent.bbox.map((v) => v.toFixed(1)).join(', ')}</span>
              {intent.start ? <> from <span className="data">{intent.start}</span></> : null}
            </p>
          )}
        </div>
      )}
    </article>
  );
}

/* ------------------------------------------------------ federated results */

/**
 * A dataset on somebody else's server.
 *
 * Rendered differently from a curated entry on purpose. The curated rows carry
 * caveats a person wrote; these carry the provider's own summary and nothing
 * more, and showing them identically would imply a level of vetting that does
 * not exist. Hence the muted treatment, the "not reviewed" line, and the
 * separate section heading.
 */
function FederatedCard({ dataset }: { dataset: FederatedDataset }) {
  const [open, setOpen] = useState(false);
  const [preview, setPreview] = useState<{ ok: boolean; csv?: string; error?: string; hint?: string; rows_returned?: number } | null>(null);
  const [loading, setLoading] = useState(false);

  const fetchPreview = async () => {
    setLoading(true);
    setPreview(null);
    try {
      const params = new URLSearchParams({
        server: dataset.server_key,
        dataset_id: dataset.dataset_id,
        protocol: dataset.protocol,
        rows: '25',
      });
      const response = await fetch(`/api/research/federation/preview?${params}`);
      setPreview(await response.json());
    } catch (cause) {
      setPreview({ ok: false, error: cause instanceof Error ? cause.message : 'preview failed' });
    } finally {
      setLoading(false);
    }
  };

  return (
    <article className="border-hairline bg-abyss-1/60 hover:border-hairline-strong rounded border transition-colors">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-start gap-2.5 px-3 py-2 text-left"
      >
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-ink-1 text-xs font-medium">{dataset.title}</span>
            <Chip className="border-hairline text-ink-3">{dataset.protocol}</Chip>
            {dataset.coverage_checked && (
              <Chip className="text-live border-live/30 bg-live/8">covers your box</Chip>
            )}
          </div>
          <div className="text-ink-3 mt-0.5 flex flex-wrap items-center gap-x-3 text-2xs">
            <span>{dataset.provider}</span>
            <span className="data">{dataset.server}</span>
            {dataset.also_on.length > 0 && <span>also on {dataset.also_on.length} more</span>}
          </div>
        </div>
      </button>

      {open && (
        <div className="border-hairline space-y-2 border-t px-3 py-2.5">
          <p className="text-ink-2 text-2xs leading-relaxed">{dataset.summary || 'No summary provided.'}</p>
          <p className="text-amber flex items-start gap-1.5 text-2xs leading-relaxed">
            <AlertTriangle className="mt-px h-3 w-3 shrink-0" aria-hidden />
            <span>{dataset.caveats}</span>
          </p>
          <div className="flex flex-wrap items-center gap-1.5">
            <button
              type="button"
              onClick={fetchPreview}
              disabled={loading}
              className="border-cyan/40 bg-cyan/12 text-cyan hover:bg-cyan/20 flex items-center gap-1 rounded border px-2 py-0.5 text-2xs transition-colors disabled:opacity-40"
            >
              {loading ? <Loader2 className="h-2.5 w-2.5 animate-spin" aria-hidden /> : <Eye className="h-2.5 w-2.5" aria-hidden />}
              preview live rows
            </button>
            <a
              href={dataset.info}
              target="_blank"
              rel="noreferrer"
              className="border-hairline text-ink-2 hover:text-cyan hover:border-cyan/40 flex items-center gap-1 rounded border px-2 py-0.5 text-2xs transition-colors"
            >
              <ExternalLink className="h-2.5 w-2.5" aria-hidden />
              provider metadata
            </a>
            <CopyButton text={dataset.endpoint} label="copy endpoint" />
          </div>

          {preview && (
            preview.ok ? (
              <div className="border-hairline rounded border">
                <div className="border-hairline text-ink-3 border-b px-2 py-1 text-2xs">
                  {preview.rows_returned} live rows, fetched from the provider and not stored
                </div>
                <pre className="data text-ink-1 max-h-48 overflow-auto px-2 py-1.5 text-2xs leading-relaxed">
                  {preview.csv}
                </pre>
              </div>
            ) : (
              <p className="text-ink-3 text-2xs leading-relaxed">
                Preview unavailable: {preview.error}. {preview.hint ?? ''}
              </p>
            )
          )}
        </div>
      )}
    </article>
  );
}

/* ----------------------------------------------------------- model section */

function ModelCard({ status }: { status: ModelStatus | null }) {
  if (!status) {
    return (
      <p className="text-ink-3 text-2xs">Asking the backend which models are loaded…</p>
    );
  }
  const report = status.training_report;
  const leads = Object.keys(report?.metrics ?? {});

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-1.5">
        <BrainCircuit className="text-jade h-3.5 w-3.5" aria-hidden />
        <span className="text-ink-0 text-xs font-semibold">FrontCast</span>
        <Chip className="data border-hairline text-ink-2">{status.version}</Chip>
        <Chip
          className={
            status.ready
              ? 'text-jade border-jade/35 bg-jade/10'
              : 'text-amber border-amber/35 bg-amber/10'
          }
        >
          {status.ready ? 'loaded' : 'not available here'}
        </Chip>
      </div>

      <p className="text-ink-1 text-2xs leading-relaxed">
        Reads <span className="data">{status.history_days}</span> days of sea-surface temperature
        and predicts where thermal fronts will be at{' '}
        <span className="data">{status.lead_days.map((d) => `+${d}d`).join(', ')}</span>. A CNN
        encoder per day, a transformer over the day axis, then one sigmoid head per lead time —
        independent, because a front can persist across all three days and a softmax would make
        that unrepresentable.
      </p>

      {!status.ready && (
        <p className="text-amber text-2xs leading-relaxed">
          {status.load_error ??
            (!status.torch_installed
              ? 'PyTorch is not installed on this deployment, so the model cannot run here. The rest of ORCA is unaffected — nothing in the safety path depends on it.'
              : 'The trained weights are not present on this deployment.')}
        </p>
      )}

      {/* Skill, always beside the baseline it must beat. */}
      {leads.length > 0 && (
        <div className="border-hairline overflow-x-auto rounded border">
          <table className="w-full text-2xs">
            <thead>
              <tr className="text-ink-3 border-hairline border-b">
                <th className="px-2 py-1 text-left font-medium">Lead</th>
                <th className="px-2 py-1 text-right font-medium">Model F1</th>
                <th className="px-2 py-1 text-right font-medium">Model IoU</th>
                <th className="px-2 py-1 text-right font-medium">Persistence F1</th>
                <th className="px-2 py-1 text-left font-medium">Verdict</th>
              </tr>
            </thead>
            <tbody className="data">
              {leads.map((lead) => {
                const m = report?.metrics?.[lead] ?? {};
                const b = report?.baseline?.[lead] ?? {};
                const beats = (m.f1 ?? 0) > (b.f1 ?? 0);
                return (
                  <tr key={lead} className="border-hairline border-b last:border-0">
                    <td className="text-ink-0 px-2 py-1">{lead}</td>
                    <td className="text-ink-0 px-2 py-1 text-right">{(m.f1 ?? 0).toFixed(3)}</td>
                    <td className="text-ink-1 px-2 py-1 text-right">{(m.iou ?? 0).toFixed(3)}</td>
                    <td className="text-ink-2 px-2 py-1 text-right">{(b.f1 ?? 0).toFixed(3)}</td>
                    <td className={clsx('px-2 py-1', beats ? 'text-jade' : 'text-amber')}>
                      {beats ? 'beats persistence' : 'does not beat persistence'}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      <p className="text-ink-2 text-2xs leading-relaxed">
        <span className="text-ink-0 font-semibold">Persistence</span> is the forecast
        &ldquo;tomorrow&rsquo;s fronts are today&rsquo;s fronts&rdquo;. It is shown on every row
        because a skill score with nothing to compare it against is not information — and at one
        day it is a genuinely strong baseline.
      </p>

      <div className="border-hairline space-y-1.5 rounded border px-2.5 py-2">
        <p className="text-ink-2 text-2xs leading-relaxed">
          <span className="text-ink-0 font-semibold">Where the labels come from.</span>{' '}
          {status.label_source}
        </p>
        <p className="text-ink-2 text-2xs leading-relaxed">
          <span className="text-ink-0 font-semibold">What it cannot do.</span>{' '}
          {status.not_a_verdict}
        </p>
      </div>

      {report?.notes && <p className="text-ink-3 text-2xs leading-relaxed">{report.notes}</p>}
    </div>
  );
}

/* ------------------------------------------------------------ ground truth */

/**
 * Satellite against thermometer.
 *
 * This is the answer to the hardest question anyone can ask ORCA: how do you
 * know your numbers are right? Every other cross-check in the system compares
 * one inference with another — a model against a satellite analysis — and can
 * only ever show that they agree. A moored buoy is an instrument in the water.
 *
 * The lag is stated up front rather than buried, because it decides what the
 * number means: the array runs about a month behind, so this validates the
 * product's track record and not today's field.
 */
function GroundTruth() {
  const [place, setPlace] = useState({ lat: 15, lon: 89, label: 'Bay of Bengal (15°N 90°E)' });
  const [result, setResult] = useState<BuoyValidation | null>(null);
  const [stations, setStations] = useState<{ station: string; lat: number; lon: number; sst_degc: number; observed_at: string }[]>([]);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    void fetch('/api/research/insitu/buoys?west=60&south=0&east=100&north=25')
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => d && setStations(d.stations ?? []))
      .catch(() => {});
  }, []);

  const run = useCallback(async (lat: number, lon: number) => {
    setBusy(true);
    setResult(null);
    try {
      const r = await fetch(`/api/research/insitu/validate-sst?lat=${lat}&lon=${lon}&days=30`);
      setResult(await r.json());
    } catch {
      setResult({ validated: false, reason: 'The validation request failed.' });
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    void run(place.lat, place.lon);
  }, [place, run]);

  return (
    <div className="space-y-4">
      <div>
        <div className="flex items-center gap-1.5">
          <Thermometer className="text-jade h-3.5 w-3.5" aria-hidden />
          <span className="text-ink-0 text-xs font-semibold">Satellite against a thermometer</span>
        </div>
        <p className="text-ink-1 mt-1 max-w-3xl text-2xs leading-relaxed">
          Every other cross-check in ORCA compares one inference with another — a model against a
          satellite analysis — and can only ever show that they <em>agree</em>. NOAA PMEL&rsquo;s
          RAMA moored buoys are instruments in the water. This compares ORCA&rsquo;s satellite SST
          against one, day by day.
        </p>
        <p className="text-amber mt-1.5 max-w-3xl text-2xs leading-relaxed">
          The array runs about a month behind, so this validates the product&rsquo;s{' '}
          <span className="font-semibold">track record</span>, not today&rsquo;s field. And it is
          sparse: 13 stations exist inside the Indian EEZ and {stations.length} reported in the last
          four months.
        </p>
      </div>

      <div className="flex flex-wrap gap-1.5">
        {stations.map((s) => (
          <button
            key={s.station}
            type="button"
            onClick={() => setPlace({ lat: s.lat, lon: s.lon, label: `${s.station} (${s.lat}°N ${s.lon}°E)` })}
            className="border-hairline text-ink-2 hover:text-jade hover:border-jade/40 rounded border px-2 py-1 text-2xs transition-colors"
          >
            <span className="data">{s.station}</span> · {s.sst_degc}°C
          </button>
        ))}
        {stations.length === 0 && (
          <span className="text-ink-3 text-2xs">No mooring has reported recently.</span>
        )}
      </div>

      {busy && (
        <p className="text-ink-3 flex items-center gap-1.5 text-2xs">
          <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
          Matching satellite days against {place.label}…
        </p>
      )}

      {result && !busy && (
        result.validated ? (
          <div className="border-hairline rounded border">
            <div className="border-hairline flex flex-wrap items-baseline gap-2 border-b px-3 py-2">
              <span className="label">RAMA {result.station}</span>
              <span className="data text-ink-3 text-2xs">
                {result.lat}°N {result.lon}°E · {result.matched_pairs} matched days
              </span>
            </div>
            <div className="grid grid-cols-2 gap-x-6 gap-y-2 px-3 py-3 sm:grid-cols-4">
              {[
                ['bias', result.bias_degc, 'satellite minus buoy'],
                ['RMSE', result.rmse_degc, 'typical error'],
                ['max error', result.max_abs_error_degc, 'worst day'],
              ].map(([label, value, hint]) => (
                <div key={String(label)}>
                  <div className="label text-2xs">{label}</div>
                  <div className="data text-jade text-sm">
                    {value === null || value === undefined ? '—' : `${value} °C`}
                  </div>
                  <div className="text-ink-3 text-2xs">{hint}</div>
                </div>
              ))}
              <div>
                <div className="label text-2xs">means</div>
                <div className="data text-ink-0 text-sm">
                  {result.buoy_mean_degc} / {result.product_mean_degc}
                </div>
                <div className="text-ink-3 text-2xs">buoy / satellite</div>
              </div>
            </div>
            <p className="text-ink-2 border-hairline border-t px-3 py-2 text-2xs leading-relaxed">
              {result.note}
            </p>
          </div>
        ) : (
          <p className="text-ink-2 border-hairline rounded border px-3 py-2 text-2xs leading-relaxed">
            {result.reason}
          </p>
        )
      )}
    </div>
  );
}

/* -------------------------------------------------------------- workspace */

export function ResearcherWorkspace({ onClose }: { onClose: () => void }) {
  const [question, setQuestion] = useState('');
  const [result, setResult] = useState<DiscoverResult | null>(null);
  const [catalogue, setCatalogue] = useState<ResearchDataset[]>([]);
  const [models, setModels] = useState<ModelStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [exporting, setExporting] = useState<string | null>(null);
  const [tab, setTab] = useState<'discover' | 'catalogue' | 'ground truth' | 'models'>('discover');

  useEffect(() => {
    void fetch('/api/research/catalogue')
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => d && setCatalogue(d.datasets as ResearchDataset[]))
      .catch(() => setError('Could not reach the catalogue.'));
    void fetch('/api/ml/models')
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => d && setModels(d.models?.[0] ?? null))
      .catch(() => {
        /* the models panel degrades to its "asking" state */
      });
  }, []);

  const ask = useCallback(
    async (text: string) => {
      const q = text.trim();
      if (!q || busy) return;
      setBusy(true);
      setError(null);
      setTab('discover');
      try {
        const response = await fetch('/api/research/discover', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ question: q }),
        });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        setResult((await response.json()) as DiscoverResult);
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : 'Discovery failed.');
        setResult(null);
      } finally {
        setBusy(false);
      }
    },
    [busy],
  );

  const exportDataset = useCallback(
    async (dataset: ResearchDataset) => {
      setExporting(dataset.id);
      const box = result?.intent.bbox ?? [60, 0, 100, 25];
      const params = new URLSearchParams({
        dataset: dataset.id,
        west: String(box[0]),
        south: String(box[1]),
        east: String(box[2]),
        north: String(box[3]),
        step: '0.25',
        format: 'csv',
      });
      if (result?.intent.start) params.set('start', result.intent.start);
      try {
        const response = await fetch(`/api/research/export?${params}`);
        if (!response.ok) {
          const detail = await response.text();
          throw new Error(detail.slice(0, 220));
        }
        const blob = await response.blob();
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement('a');
        anchor.href = url;
        anchor.download = `orca_${dataset.id}.csv`;
        anchor.click();
        URL.revokeObjectURL(url);
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : 'Export failed.');
      } finally {
        setExporting(null);
      }
    },
    [result],
  );

  const shown = useMemo(
    () => (tab === 'catalogue' ? catalogue : (result?.matches ?? [])),
    [tab, catalogue, result],
  );

  return (
    <div className="bg-abyss-0 fixed inset-0 z-50 flex flex-col">
      {/* header */}
      <div className="border-hairline glass flex shrink-0 items-center gap-3 border-b px-3 py-2">
        <BookMarked className="text-cyan h-4 w-4" aria-hidden />
        <span className="label">Researcher workspace</span>
        <span className="text-ink-3 hidden text-2xs sm:inline">
          Ask in plain language. ORCA returns datasets it has actually integrated.
        </span>
        <div className="ml-auto flex items-center gap-1">
          {(['discover', 'catalogue', 'ground truth', 'models'] as const).map((key) => (
            <button
              key={key}
              type="button"
              onClick={() => setTab(key)}
              className={clsx(
                'rounded px-2 py-1 text-2xs tracking-wider uppercase transition-colors',
                tab === key ? 'bg-cyan/15 text-cyan' : 'text-ink-2 hover:text-ink-0',
              )}
            >
              {key === 'models' ? 'models' : key}
            </button>
          ))}
          <button
            type="button"
            onClick={onClose}
            className="text-ink-2 hover:text-ink-0 ml-1 rounded p-1 transition-colors"
            aria-label="Close the researcher workspace"
          >
            <X className="h-4 w-4" aria-hidden />
          </button>
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-5xl px-4 py-4">
          {tab === 'ground truth' ? (
            <GroundTruth />
          ) : tab === 'models' ? (
            <ModelCard status={models} />
          ) : (
            <>
              {tab === 'discover' && (
                <>
                  <form
                    onSubmit={(event) => {
                      event.preventDefault();
                      void ask(question);
                    }}
                    className="flex items-center gap-2"
                  >
                    <div className="border-hairline bg-abyss-1 focus-within:border-cyan/50 flex flex-1 items-center gap-2 rounded border px-2.5 py-1.5 transition-colors">
                      <Search className="text-ink-3 h-3.5 w-3.5 shrink-0" aria-hidden />
                      <input
                        value={question}
                        onChange={(event) => setQuestion(event.target.value)}
                        placeholder="e.g. chlorophyll blooms in the Bay of Bengal during the monsoon"
                        className="text-ink-0 placeholder:text-ink-3 min-w-0 flex-1 bg-transparent text-xs outline-none"
                      />
                    </div>
                    <button
                      type="submit"
                      disabled={busy || !question.trim()}
                      className="border-cyan/40 bg-cyan/15 text-cyan hover:bg-cyan/25 flex items-center gap-1.5 rounded border px-3 py-1.5 text-2xs transition-colors disabled:opacity-30"
                    >
                      {busy ? (
                        <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
                      ) : (
                        <Sparkles className="h-3 w-3" aria-hidden />
                      )}
                      find data
                    </button>
                  </form>

                  {!result && !busy && (
                    <div className="mt-3 flex flex-wrap gap-1.5">
                      {EXAMPLES.map((example) => (
                        <button
                          key={example}
                          type="button"
                          onClick={() => {
                            setQuestion(example);
                            void ask(example);
                          }}
                          className="border-hairline text-ink-2 hover:text-cyan hover:border-cyan/40 rounded border px-2 py-1 text-2xs transition-colors"
                        >
                          {example}
                        </button>
                      ))}
                    </div>
                  )}

                  {/* The parse, shown before the results. */}
                  {result && (
                    <div className="border-hairline bg-abyss-1 mt-3 rounded border px-3 py-2.5">
                      <div className="mb-1.5 flex items-center gap-1.5">
                        <Binary className="text-cyan h-3 w-3" aria-hidden />
                        <span className="label">How ORCA read your question</span>
                        <Chip className="data border-hairline text-ink-2 ml-auto">
                          {result.parsed_by}
                        </Chip>
                      </div>
                      <div className="flex flex-wrap gap-1.5">
                        {result.intent.variables.map((v) => (
                          <Chip key={v} className="text-cyan border-cyan/35 bg-cyan/10">
                            {v}
                          </Chip>
                        ))}
                        {result.intent.place && (
                          <Chip className="text-jade border-jade/35 bg-jade/10">
                            {result.intent.place}
                          </Chip>
                        )}
                        {result.intent.bbox && (
                          <Chip className="border-hairline text-ink-2">
                            {result.intent.bbox.map((v) => v.toFixed(1)).join(', ')}
                          </Chip>
                        )}
                        {(result.intent.start || result.intent.end) && (
                          <Chip className="border-hairline text-ink-2">
                            {result.intent.start ?? '…'} → {result.intent.end ?? '…'}
                          </Chip>
                        )}
                      </div>
                      {result.intent.assumptions.length > 0 && (
                        <ul className="mt-1.5 space-y-0.5">
                          {result.intent.assumptions.map((a) => (
                            <li key={a} className="text-amber flex items-start gap-1 text-2xs">
                              <span aria-hidden>·</span>
                              <span>{a}</span>
                            </li>
                          ))}
                        </ul>
                      )}
                      <p className="text-ink-3 mt-1.5 text-2xs leading-relaxed">{result.note}</p>
                    </div>
                  )}
                </>
              )}

              {error && (
                <p className="border-red/40 bg-red/8 text-red mt-3 rounded border px-2.5 py-1.5 text-2xs">
                  {error}
                </p>
              )}

              <div className="mt-3 flex items-center gap-2">
                <Database className="text-ink-3 h-3 w-3" aria-hidden />
                <span className="label">
                  {tab === 'catalogue'
                    ? `everything ORCA can serve — ${catalogue.length} datasets`
                    : `${shown.length} matches`}
                </span>
              </div>

              <div className="mt-2 space-y-1.5">
                {shown.map((dataset) => (
                  <DatasetCard
                    key={dataset.id}
                    dataset={dataset}
                    intent={result?.intent ?? null}
                    onExport={exportDataset}
                    exporting={exporting}
                  />
                ))}
              </div>

              {/* The second tier, deliberately below and visually quieter than the
                  curated matches. Same list, same weight would imply the same
                  vetting. */}
              {tab === 'discover' && result?.federated && result.federated.found > 0 && (
                <div className="mt-5">
                  <div className="flex flex-wrap items-center gap-2">
                    <Globe className="text-ink-3 h-3 w-3" aria-hidden />
                    <span className="label">
                      {result.federated.found} more on the public ERDDAP network
                    </span>
                    <Chip className="border-hairline text-ink-3">
                      {result.federated.servers_responding}/{result.federated.servers_queried} servers
                    </Chip>
                  </div>
                  <p className="text-ink-3 mt-1 max-w-3xl text-2xs leading-relaxed">
                    {result.federated.note}
                  </p>
                  <div className="mt-2 space-y-1">
                    {result.federated.datasets.map((dataset) => (
                      <FederatedCard key={dataset.id} dataset={dataset} />
                    ))}
                  </div>
                </div>
              )}

              {result?.snippet && tab === 'discover' && (
                <div className="border-hairline bg-abyss-1 mt-4 rounded border">
                  <div className="border-hairline flex items-center gap-2 border-b px-3 py-1.5">
                    <span className="label">Reproduce it</span>
                    <div className="ml-auto">
                      <CopyButton text={result.snippet} />
                    </div>
                  </div>
                  <pre className="data text-ink-1 overflow-x-auto px-3 py-2 text-2xs leading-relaxed">
                    {result.snippet}
                  </pre>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
