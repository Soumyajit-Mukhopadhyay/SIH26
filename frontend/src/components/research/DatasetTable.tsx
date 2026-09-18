import { ChevronRight, Download, ExternalLink } from 'lucide-react';
import { clsx } from 'clsx';
import { useState } from 'react';

import type { Intent, ResearchDataset } from './types';
import { Button, ColLabel, KV, Notice, Swatch, Tag, accessLabel, fmtDeg } from './ui';

/**
 * The registry row. Shared by Discover (ranked, with a match column) and
 * Catalogue (grouped, without one) so the two screens describe a dataset in
 * exactly the same vocabulary — only the ordering and the framing differ.
 */

// Coverage only earns a column on wide screens; below that it lives in the
// expanded detail. The other columns collapse in the same order they are
// listed, so the row never becomes a ribbon of truncated fragments.
const GRID_WITH_SCORE =
  'grid-cols-[44px_minmax(0,3fr)_96px_84px_18px] md:grid-cols-[44px_minmax(0,3fr)_minmax(0,1.2fr)_96px_84px_18px] lg:grid-cols-[44px_minmax(0,3fr)_minmax(0,1.3fr)_minmax(0,1.2fr)_96px_84px_18px] 2xl:grid-cols-[44px_minmax(0,3fr)_minmax(0,1.3fr)_minmax(0,1.1fr)_minmax(0,1.3fr)_96px_84px_18px]';
const GRID_NO_SCORE =
  'grid-cols-[44px_minmax(0,3fr)_96px_18px] md:grid-cols-[44px_minmax(0,3fr)_minmax(0,1.2fr)_96px_18px] lg:grid-cols-[44px_minmax(0,3fr)_minmax(0,1.3fr)_minmax(0,1.2fr)_96px_18px] 2xl:grid-cols-[44px_minmax(0,3fr)_minmax(0,1.3fr)_minmax(0,1.1fr)_minmax(0,1.3fr)_96px_18px]';

export function DatasetTableHeader({ showScore }: { showScore: boolean }) {
  return (
    <div
      className={clsx(
        'border-hairline-strong grid items-end gap-x-4 border-b px-2 pb-1.5',
        showScore ? GRID_WITH_SCORE : GRID_NO_SCORE,
      )}
    >
      <span />
      <ColLabel>Dataset</ColLabel>
      <ColLabel className="hidden lg:block">Variables</ColLabel>
      <ColLabel className="hidden md:block">Grid · cadence</ColLabel>
      <ColLabel className="hidden 2xl:block">Coverage</ColLabel>
      <ColLabel>Status</ColLabel>
      {showScore ? (
        <ColLabel
          className="text-right"
          title="ORCA's keyword-rule matcher: how many of the request's terms this dataset satisfies. A ranking aid, not a confidence or accuracy figure."
        >
          Match
        </ColLabel>
      ) : null}
      <span />
    </div>
  );
}

export function DatasetRow({
  dataset,
  showScore,
  maxScore,
  intent,
  onExport,
  exporting,
  thumb,
}: {
  dataset: ResearchDataset;
  showScore: boolean;
  maxScore: number;
  intent: Intent | null;
  onExport?: (dataset: ResearchDataset, fmt: 'csv' | 'json') => void;
  exporting: string | null;
  thumb?: string;
}) {
  const [open, setOpen] = useState(false);
  const status = accessLabel(dataset);
  const score = dataset.score ?? 0;
  const varNames = dataset.variables.map((v) => v.name);
  const endpointUrl = dataset.endpoint.match(/https?:\/\/\S+/)?.[0];

  return (
    <div className="border-hairline border-b">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className={clsx(
          'hover:bg-abyss-1 grid w-full items-center gap-x-4 px-2 py-2 text-left transition-colors',
          showScore ? GRID_WITH_SCORE : GRID_NO_SCORE,
          open && 'bg-abyss-1',
        )}
      >
        <Swatch dataset={dataset} src={thumb} />

        <div className="min-w-0">
          <div className="text-ink-0 line-clamp-2 text-[13px] leading-snug font-medium">
            {dataset.title}
          </div>
          <div className="text-ink-1 truncate text-xs leading-snug">
            {dataset.provider}
            <span className="text-ink-2"> · {dataset.kind}</span>
          </div>
        </div>

        <div
          className="data text-ink-1 hidden min-w-0 truncate text-[11px] lg:block"
          title={varNames.join(', ')}
        >
          {varNames.slice(0, 3).join(', ')}
          {varNames.length > 3 ? (
            <span className="text-ink-2"> +{varNames.length - 3}</span>
          ) : null}
        </div>

        <div className="data text-ink-1 hidden min-w-0 truncate text-[11px] md:block">
          {fmtDeg(dataset.resolution_deg)}
          <span className="text-ink-2"> · </span>
          {dataset.cadence}
        </div>

        <div className="text-ink-1 hidden min-w-0 truncate text-xs 2xl:block" title={dataset.coverage}>
          {dataset.coverage}
        </div>

        <div>
          <Tag tone={status.tone}>{status.text}</Tag>
        </div>

        {showScore ? (
          <div className="flex items-center justify-end gap-2">
            <span className="bg-abyss-2 h-1 w-9 overflow-hidden rounded-full">
              <span
                className="bg-ink-1 block h-full"
                style={{ width: `${maxScore > 0 ? (score / maxScore) * 100 : 0}%` }}
              />
            </span>
            <span className="data text-ink-1 w-6 text-right text-[11px]">{score.toFixed(1)}</span>
          </div>
        ) : null}

        <ChevronRight
          className={clsx('text-ink-2 h-3.5 w-3.5 transition-transform', open && 'rotate-90')}
          aria-hidden
        />
      </button>

      {open ? (
        <div className="bg-abyss-1 border-hairline grid gap-x-8 gap-y-4 border-t px-4 py-4 md:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)] md:pl-[76px]">
          <div className="space-y-4">
            <div>
              <ColLabel className="mb-1.5">Variables</ColLabel>
              <table className="w-full text-xs">
                <tbody>
                  {dataset.variables.map((v) => (
                    <tr key={v.name} className="align-baseline">
                      <td className="data text-ink-0 py-0.5 pr-3 whitespace-nowrap">{v.name}</td>
                      <td className="data text-ink-2 py-0.5 pr-3 whitespace-nowrap">{v.unit}</td>
                      <td className="text-ink-1 py-0.5">{v.description}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {dataset.why?.length ? (
              <div>
                <ColLabel className="mb-1">Why it matched</ColLabel>
                <ul className="text-ink-1 space-y-0.5 text-xs">
                  {dataset.why.map((r) => (
                    <li key={r}>{r}</li>
                  ))}
                </ul>
              </div>
            ) : null}

            <Notice tone="muted">
              <span className="text-ink-0 font-medium">Read before use. </span>
              {dataset.caveats}
            </Notice>
          </div>

          <div className="space-y-4">
            <KV
              dense
              rows={[
                { k: 'Native grid', v: dataset.native_resolution },
                { k: 'Cadence', v: dataset.cadence },
                { k: 'Coverage', v: dataset.coverage },
                { k: 'Provenance', v: <span className="data">{dataset.provenance}</span> },
                { k: 'Licence', v: dataset.licence },
                {
                  k: 'Endpoint',
                  v: <span className="data text-ink-1 break-all">{dataset.endpoint}</span>,
                },
              ]}
            />

            <div className="flex flex-wrap items-center gap-2">
              {dataset.servable && onExport ? (
                <>
                  <Button
                    kind="secondary"
                    busy={exporting === `${dataset.id}:csv`}
                    disabled={exporting !== null}
                    onClick={() => onExport(dataset, 'csv')}
                    title={
                      intent?.bbox
                        ? 'Subset to the interpreted region and dates'
                        : 'Subset over the whole Indian EEZ, latest day'
                    }
                  >
                    <Download className="h-3.5 w-3.5" aria-hidden />
                    CSV
                  </Button>
                  <Button
                    kind="secondary"
                    busy={exporting === `${dataset.id}:json`}
                    disabled={exporting !== null}
                    onClick={() => onExport(dataset, 'json')}
                  >
                    <Download className="h-3.5 w-3.5" aria-hidden />
                    JSON
                  </Button>
                </>
              ) : null}
              {endpointUrl ? (
                <a
                  href={endpointUrl}
                  target="_blank"
                  rel="noreferrer"
                  className="text-ink-1 hover:text-ink-0 inline-flex h-8 items-center gap-1.5 px-2 text-xs"
                >
                  <ExternalLink className="h-3.5 w-3.5" aria-hidden />
                  Source
                </a>
              ) : null}
              {!dataset.servable ? (
                <span className="text-ink-2 text-xs">
                  Indexed, not served. Fetch it from the provider.
                </span>
              ) : null}
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
