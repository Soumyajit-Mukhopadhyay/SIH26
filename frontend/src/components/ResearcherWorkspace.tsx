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
 * Five jobs, five different page shapes under one type system:
 *
 *   Discover   search → one-line interpretation → ranked registry rows → ERDDAP tier
 *   Catalogue  the registry, grouped by kind
 *   Build      a numbered workbench with the request summarised beside it
 *   Validate   a mooring map beside four numbers that deserve to be large
 *   Models     a registry table and an availability checklist
 *
 * This file owns the shell, the shared fetches (catalogue, models, raster
 * thumbnails) and the Discover request/export handlers; the views under
 * ./research are presentation only.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { X } from 'lucide-react';
import { clsx } from 'clsx';

import { DatasetBuilder } from '@/components/DatasetBuilder';
import { CatalogueView } from '@/components/research/CatalogueView';
import { DiscoverView } from '@/components/research/DiscoverView';
import { ModelsView } from '@/components/research/ModelsView';
import type {
  DiscoverResult,
  ModelStatus,
  ResearchDataset,
  ResearchTab,
} from '@/components/research/types';
import { ValidateView } from '@/components/research/ValidateView';
import { api } from '@/lib/api';

export type { FederatedDataset, ResearchDataset } from '@/components/research/types';

const TABS: { id: ResearchTab; label: string }[] = [
  { id: 'discover', label: 'Discover' },
  { id: 'catalogue', label: 'Catalogue' },
  { id: 'build', label: 'Build' },
  { id: 'validate', label: 'Ground truth' },
  { id: 'models', label: 'Models' },
];

/** Catalogue entries for which ORCA generates a raster of the same field, so
 *  the row can show the real latest image rather than a legend swatch. */
const RASTER_FOR_DATASET: Record<string, string> = {
  mur_sst: 'sst',
  esacci_chl_monthly: 'chlorophyll',
  orca_fronts_sied: 'sst_gradient',
  orca_pfz: 'pfz_rank',
};

export function ResearcherWorkspace({ onClose }: { onClose: () => void }) {
  const [tab, setTab] = useState<ResearchTab>('discover');
  const [question, setQuestion] = useState('');
  const [result, setResult] = useState<DiscoverResult | null>(null);
  const [catalogue, setCatalogue] = useState<ResearchDataset[]>([]);
  const [models, setModels] = useState<ModelStatus[] | null>(null);
  const [policy, setPolicy] = useState<string | null>(null);
  const [thumbs, setThumbs] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [exporting, setExporting] = useState<string | null>(null);

  // Escape closes it. This is a `fixed inset-0 z-50` overlay covering the whole
  // console, so without this the only way out is one small button in a corner.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  useEffect(() => {
    void fetch('/api/research/catalogue')
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => d && setCatalogue(d.datasets as ResearchDataset[]))
      .catch(() => setError('Could not reach the catalogue.'));
    void fetch('/api/ml/models')
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (!d) return;
        setModels((d.models as ModelStatus[]) ?? []);
        setPolicy(typeof d.policy === 'string' ? d.policy : null);
      })
      .catch(() => {
        /* the models view stays in its "asking" state */
      });
    void api
      .rasterCatalogue()
      .then((catalogue) => {
        const byVariable: Record<string, string> = {};
        for (const v of catalogue.variables) byVariable[v.variable] = `/api${v.png}`;
        const out: Record<string, string> = {};
        for (const [datasetId, variable] of Object.entries(RASTER_FOR_DATASET)) {
          if (byVariable[variable]) out[datasetId] = byVariable[variable];
        }
        setThumbs(out);
      })
      .catch(() => {
        /* no rasters generated yet; rows fall back to legend swatches */
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
    async (dataset: ResearchDataset, format: 'csv' | 'json' = 'csv') => {
      setExporting(`${dataset.id}:${format}`);
      const box = result?.intent.bbox ?? [60, 0, 100, 25];
      const params = new URLSearchParams({
        dataset: dataset.id,
        west: String(box[0]),
        south: String(box[1]),
        east: String(box[2]),
        north: String(box[3]),
        step: '0.25',
        format,
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
        anchor.download = `orca_${dataset.id}.${format}`;
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

  const servable = useMemo(() => catalogue.filter((d) => d.servable).length, [catalogue]);
  const frontcastEntry = useMemo(
    () => catalogue.find((d) => d.id === 'orca_frontcast'),
    [catalogue],
  );

  return (
    <div className="bg-abyss-0 fixed inset-0 z-50 flex flex-col">
      <header className="border-hairline flex h-12 shrink-0 items-stretch border-b px-5">
        <div className="flex items-center gap-2.5 pr-8">
          <span className="data text-cyan text-sm font-bold tracking-[0.18em]">ORCA</span>
          <span className="text-ink-3">/</span>
          <span className="text-ink-0 text-[13px] font-semibold">Researcher workspace</span>
        </div>

        <nav className="flex items-stretch gap-1" role="tablist" aria-label="Research workflows">
          {TABS.map((t) => {
            const active = tab === t.id;
            return (
              <button
                key={t.id}
                type="button"
                role="tab"
                aria-selected={active}
                onClick={() => setTab(t.id)}
                className={clsx(
                  'relative px-3 text-xs font-medium transition-colors',
                  active ? 'text-ink-0' : 'text-ink-1 hover:text-ink-0',
                )}
              >
                {t.label}
                {active ? (
                  <span className="bg-cyan absolute inset-x-3 bottom-0 h-0.5" aria-hidden />
                ) : null}
              </button>
            );
          })}
        </nav>

        <div className="ml-auto flex items-center gap-5">
          <span className="data text-ink-2 hidden text-[11px] sm:block">
            {catalogue.length ? `${catalogue.length} datasets · ${servable} integrated` : ''}
          </span>
          <button
            type="button"
            onClick={onClose}
            className="text-ink-1 hover:text-ink-0 hover:bg-abyss-2 flex h-7 items-center gap-1.5 rounded px-2 text-xs transition-colors"
            title="Back to the map (Esc)"
          >
            <X className="h-3.5 w-3.5" aria-hidden />
            <span className="hidden sm:inline">Close</span>
          </button>
        </div>
      </header>

      <main className="min-h-0 flex-1 overflow-y-auto">
        {tab === 'discover' ? (
          <DiscoverView
            question={question}
            setQuestion={setQuestion}
            ask={(q) => void ask(q)}
            busy={busy}
            error={error}
            result={result}
            catalogueSize={catalogue.length}
            onExport={(d, f) => void exportDataset(d, f)}
            exporting={exporting}
            thumbs={thumbs}
          />
        ) : null}
        {tab === 'catalogue' ? (
          <CatalogueView
            catalogue={catalogue}
            onExport={(d, f) => void exportDataset(d, f)}
            exporting={exporting}
            thumbs={thumbs}
          />
        ) : null}
        {tab === 'build' ? <DatasetBuilder bbox={result?.intent.bbox ?? null} /> : null}
        {tab === 'validate' ? <ValidateView /> : null}
        {tab === 'models' ? (
          <ModelsView models={models} policy={policy} catalogueEntry={frontcastEntry} />
        ) : null}
      </main>
    </div>
  );
}
