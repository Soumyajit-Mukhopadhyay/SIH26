import { ChevronRight, ExternalLink } from 'lucide-react';
import { clsx } from 'clsx';
import { useState } from 'react';

import type { Federated, FederatedDataset, FederatedPreview } from './types';
import { Button, ColLabel, CopyButton, Notice, Tag } from './ui';

/**
 * The second tier of Discover: hits from the public ERDDAP network. Same
 * row rhythm as the curated table so the eye keeps its place, but no swatch,
 * no status column, dimmer titles — ORCA indexes these, it has not read them.
 */

const GRID = 'grid-cols-[minmax(0,2.4fr)_minmax(0,1.4fr)_88px_18px]';

export function FederatedList({ federated }: { federated: Federated }) {
  return (
    <section>
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h3 className="text-ink-0 text-[13px] font-semibold">
          Also on the public ERDDAP network
          <span className="text-ink-1 font-normal"> · {federated.found}</span>
        </h3>
        <span className="data text-ink-2 text-[11px]">
          {federated.servers_responding}/{federated.servers_queried} servers answered
        </span>
        <span className="text-ink-2 text-xs">Not reviewed by ORCA.</span>
      </div>

      <div className="mt-3">
        <div className={clsx('border-hairline-strong grid gap-x-4 border-b px-2 pb-1.5', GRID)}>
          <ColLabel>Dataset</ColLabel>
          <ColLabel className="hidden md:block">Server</ColLabel>
          <ColLabel>Protocol</ColLabel>
          <span />
        </div>
        {federated.datasets.map((dataset) => (
          <FederatedRow key={dataset.id} dataset={dataset} />
        ))}
      </div>
    </section>
  );
}

function FederatedRow({ dataset }: { dataset: FederatedDataset }) {
  const [open, setOpen] = useState(false);
  const [preview, setPreview] = useState<FederatedPreview | null>(null);
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
      setPreview((await response.json()) as FederatedPreview);
    } catch (cause) {
      setPreview({ ok: false, error: cause instanceof Error ? cause.message : 'preview failed' });
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="border-hairline border-b">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className={clsx(
          'hover:bg-abyss-1 grid w-full items-center gap-x-4 px-2 py-2 text-left transition-colors',
          GRID,
          open && 'bg-abyss-1',
        )}
      >
        <div className="min-w-0">
          <div className="text-ink-1 truncate text-[13px] leading-snug">{dataset.title}</div>
          <div className="text-ink-2 truncate text-xs leading-snug">
            {dataset.provider}
            {dataset.coverage_checked ? (
              <span className="text-ink-1"> · covers the requested box</span>
            ) : null}
            {dataset.also_on.length > 0 ? ` · mirrored on ${dataset.also_on.length} more` : null}
          </div>
        </div>
        <div className="data text-ink-2 hidden min-w-0 truncate text-[11px] md:block">
          {dataset.server}
        </div>
        <div>
          <Tag tone="muted">{dataset.protocol}</Tag>
        </div>
        <ChevronRight
          className={clsx('text-ink-2 h-3.5 w-3.5 transition-transform', open && 'rotate-90')}
          aria-hidden
        />
      </button>

      {open ? (
        <div className="bg-abyss-1 border-hairline space-y-3 border-t px-4 py-4">
          <p className="text-ink-1 max-w-3xl text-xs leading-relaxed">
            {dataset.summary || 'The provider supplied no summary.'}
          </p>
          <Notice tone="amber">{dataset.caveats}</Notice>

          <div className="flex flex-wrap items-center gap-2">
            <Button kind="secondary" onClick={fetchPreview} busy={loading}>
              Preview 25 live rows
            </Button>
            <a
              href={dataset.info}
              target="_blank"
              rel="noreferrer"
              className="text-ink-1 hover:text-ink-0 inline-flex h-8 items-center gap-1.5 px-2 text-xs"
            >
              <ExternalLink className="h-3.5 w-3.5" aria-hidden />
              Provider metadata
            </a>
            <CopyButton text={dataset.endpoint} label="Copy endpoint" />
          </div>

          {preview ? (
            preview.ok ? (
              <div className="border-hairline rounded border">
                <div className="border-hairline text-ink-2 border-b px-3 py-1.5 text-xs">
                  {preview.rows_returned} rows fetched from the provider just now; not stored by
                  ORCA.
                </div>
                <pre className="data text-ink-1 max-h-56 overflow-auto px-3 py-2 text-[11px] leading-relaxed">
                  {preview.csv}
                </pre>
              </div>
            ) : (
              <p className="text-ink-1 text-xs leading-relaxed">
                Preview unavailable: {preview.error}. {preview.hint ?? ''}
              </p>
            )
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
