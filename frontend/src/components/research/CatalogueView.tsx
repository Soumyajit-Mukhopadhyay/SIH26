import { clsx } from 'clsx';
import { useMemo, useState, type ReactNode } from 'react';

import { DatasetRow, DatasetTableHeader } from './DatasetTable';
import type { DatasetKind, ResearchDataset } from './types';
import { KIND_LABEL, KIND_ORDER, Lede, Title, Working } from './ui';

/**
 * The registry. Every dataset ORCA knows, grouped by what kind of thing it is,
 * because "is this a measurement or a forecast" is the first question a
 * researcher asks and the one the old flat list made them read a chip for.
 * Filtering is over data already in hand; nothing here calls the backend.
 */
export function CatalogueView({
  catalogue,
  onExport,
  exporting,
  thumbs,
}: {
  catalogue: ResearchDataset[];
  onExport: (dataset: ResearchDataset, fmt: 'csv' | 'json') => void;
  exporting: string | null;
  thumbs: Record<string, string>;
}) {
  const [kind, setKind] = useState<DatasetKind | 'all'>('all');
  const [servableOnly, setServableOnly] = useState(false);

  const servable = useMemo(() => catalogue.filter((d) => d.servable).length, [catalogue]);
  const groups = useMemo(() => {
    const filtered = catalogue.filter(
      (d) => (kind === 'all' || d.kind === kind) && (!servableOnly || d.servable),
    );
    return KIND_ORDER.map((k) => ({ kind: k, items: filtered.filter((d) => d.kind === k) })).filter(
      (g) => g.items.length,
    );
  }, [catalogue, kind, servableOnly]);

  const counts = useMemo(() => {
    const out: Partial<Record<DatasetKind, number>> = {};
    for (const d of catalogue) out[d.kind] = (out[d.kind] ?? 0) + 1;
    return out;
  }, [catalogue]);

  return (
    <div className="mx-auto w-full max-w-[1440px] px-6 py-6 lg:px-10 lg:py-8">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <Title>Catalogue</Title>
          <Lede>
            {catalogue.length} datasets ORCA has integrated; {servable} can be subset and downloaded
            from here, the rest are indexed with a pointer to the provider.
          </Lede>
        </div>

        <div className="flex flex-wrap items-center gap-x-1 gap-y-2">
          <FilterButton active={kind === 'all'} onClick={() => setKind('all')}>
            All <span className="data text-ink-2 ml-1">{catalogue.length}</span>
          </FilterButton>
          {KIND_ORDER.filter((k) => counts[k]).map((k) => (
            <FilterButton key={k} active={kind === k} onClick={() => setKind(k)}>
              {KIND_LABEL[k]} <span className="data text-ink-2 ml-1">{counts[k]}</span>
            </FilterButton>
          ))}
          <span className="bg-hairline-strong mx-2 h-4 w-px" aria-hidden />
          <label className="text-ink-1 flex cursor-pointer items-center gap-2 text-xs select-none">
            <input
              type="checkbox"
              checked={servableOnly}
              onChange={(e) => setServableOnly(e.target.checked)}
              className="accent-cyan h-3.5 w-3.5"
            />
            Integrated only
          </label>
        </div>
      </div>

      {!catalogue.length ? (
        <div className="mt-8">
          <Working label="Loading the catalogue…" />
        </div>
      ) : (
        <div className="mt-6">
          <DatasetTableHeader showScore={false} />
          {groups.map((group) => (
            <section key={group.kind}>
              <div className="border-hairline flex items-baseline gap-2 border-b px-2 pt-5 pb-1.5">
                <h3 className="text-ink-0 text-xs font-semibold tracking-wide uppercase">
                  {KIND_LABEL[group.kind]}
                </h3>
                <span className="data text-ink-2 text-[11px]">{group.items.length}</span>
              </div>
              {group.items.map((dataset) => (
                <DatasetRow
                  key={dataset.id}
                  dataset={dataset}
                  showScore={false}
                  maxScore={0}
                  intent={null}
                  onExport={onExport}
                  exporting={exporting}
                  thumb={thumbs[dataset.id]}
                />
              ))}
            </section>
          ))}
          {!groups.length ? (
            <p className="text-ink-1 mt-6 text-xs">Nothing matches this filter.</p>
          ) : null}
        </div>
      )}
    </div>
  );
}

function FilterButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={clsx(
        'h-7 rounded px-2.5 text-xs transition-colors',
        active ? 'bg-abyss-2 text-ink-0' : 'text-ink-1 hover:text-ink-0',
      )}
    >
      {children}
    </button>
  );
}
