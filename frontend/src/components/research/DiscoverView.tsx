import { Search } from 'lucide-react';
import { useMemo, type FormEvent } from 'react';

import { DatasetRow, DatasetTableHeader } from './DatasetTable';
import { FederatedList } from './FederatedList';
import { MapAxes, RegionMap } from './RegionMap';
import type { DiscoverResult, Intent, ResearchDataset } from './types';
import { ColLabel, CopyButton, KV, Notice, Tag, Working, fmtBox } from './ui';

const EXAMPLES = [
  'chlorophyll blooms in the Bay of Bengal during the monsoon',
  'wave height forecasts off the Kerala coast for the next week',
  'thermal fronts near Tamil Nadu over the last 30 days',
  'scatterometer winds over the Arabian Sea in 2025',
  'what can I get to study upwelling off Gujarat?',
];

const SMALL_WORDS = new Set(['of', 'and', 'the']);
function titleCase(s: string) {
  return s
    .split(' ')
    .map((w, i) => (i > 0 && SMALL_WORDS.has(w) ? w : w.charAt(0).toUpperCase() + w.slice(1)))
    .join(' ');
}

/** One line: what the parser extracted, in the order a researcher thinks about it. */
function InterpretationLine({ intent }: { intent: Intent }) {
  const parts: { text: string; known: boolean }[] = [
    intent.variables.length
      ? { text: intent.variables.join(', '), known: true }
      : { text: 'any variable', known: false },
    intent.place
      ? { text: titleCase(intent.place), known: true }
      : intent.bbox
        ? { text: fmtBox(intent.bbox), known: true }
        : { text: 'whole Indian EEZ', known: false },
    intent.start || intent.end
      ? { text: `${intent.start ?? '…'} → ${intent.end ?? 'today'}`, known: true }
      : { text: 'any period', known: false },
    intent.kinds.length
      ? { text: intent.kinds.join(' / '), known: true }
      : { text: 'any kind', known: false },
  ];
  return (
    <div className="text-ink-0 flex flex-wrap items-baseline gap-x-2 text-sm">
      {parts.map((p, i) => (
        <span key={i} className="flex items-baseline gap-x-2">
          {i > 0 ? <span className="text-ink-3">·</span> : null}
          <span className={p.known ? undefined : 'text-ink-2'}>{p.text}</span>
        </span>
      ))}
    </div>
  );
}

export function DiscoverView({
  question,
  setQuestion,
  ask,
  busy,
  error,
  result,
  catalogueSize,
  onExport,
  exporting,
  thumbs,
}: {
  question: string;
  setQuestion: (q: string) => void;
  ask: (q: string) => void;
  busy: boolean;
  error: string | null;
  result: DiscoverResult | null;
  catalogueSize: number;
  onExport: (dataset: ResearchDataset, fmt: 'csv' | 'json') => void;
  exporting: string | null;
  thumbs: Record<string, string>;
}) {
  const maxScore = useMemo(
    () => Math.max(0, ...(result?.matches.map((m) => m.score ?? 0) ?? [0])),
    [result],
  );

  const submit = (event: FormEvent) => {
    event.preventDefault();
    ask(question);
  };

  return (
    <div className="mx-auto w-full max-w-[1440px] px-6 py-6 lg:px-10 lg:py-8">
      {/* -------------------------------------------------------- search */}
      <form onSubmit={submit} className="flex items-stretch gap-2">
        <label className="border-hairline-strong bg-abyss-1 focus-within:border-cyan/60 flex h-11 flex-1 items-center gap-3 rounded border px-3.5 transition-colors">
          <Search className="text-ink-2 h-4 w-4 shrink-0" aria-hidden />
          <input
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            placeholder="Describe the data you need: variable, sea area, period"
            className="text-ink-0 placeholder:text-ink-2 h-full w-full bg-transparent text-sm outline-none"
            autoFocus
            spellCheck={false}
          />
        </label>
        <button
          type="submit"
          disabled={busy || !question.trim()}
          className="bg-cyan text-abyss-0 hover:bg-cyan/85 h-11 rounded px-5 text-sm font-medium transition-colors disabled:opacity-40"
        >
          Search
        </button>
      </form>

      {error ? (
        <Notice tone="red" className="mt-4">
          {error}
        </Notice>
      ) : null}

      {/* --------------------------------------------------- empty state */}
      {!result && !busy ? (
        <div className="mt-10 grid gap-10 lg:grid-cols-[minmax(0,1fr)_320px]">
          <div>
            <ColLabel className="mb-3">Try</ColLabel>
            <ul className="divide-hairline divide-y">
              {EXAMPLES.map((example) => (
                <li key={example}>
                  <button
                    type="button"
                    onClick={() => {
                      setQuestion(example);
                      ask(example);
                    }}
                    className="text-ink-1 hover:text-ink-0 block w-full py-2.5 text-left text-[13px] transition-colors"
                  >
                    {example}
                  </button>
                </li>
              ))}
            </ul>
          </div>
          <div className="text-ink-1 space-y-3 text-xs leading-relaxed lg:pt-7">
            <p>
              A language model reads the question into variables, a sea area and dates. The
              datasets are then chosen by a deterministic matcher against the{' '}
              <span className="text-ink-0">{catalogueSize || '—'}</span> sources ORCA has actually
              integrated, so nothing here is invented.
            </p>
            <p>
              The public ERDDAP network is searched as a second, unreviewed tier.
            </p>
          </div>
        </div>
      ) : null}

      {busy ? (
        <div className="mt-8">
          <Working label="Parsing the question and matching the catalogue…" />
        </div>
      ) : null}

      {/* -------------------------------------------------------- results */}
      {result && !busy ? (
        <div className="mt-6 grid gap-10 lg:grid-cols-[minmax(0,1fr)_300px]">
          <div className="min-w-0">
            {/* interpretation */}
            <div className="border-hairline-strong border-b pb-4">
              <div className="flex items-baseline justify-between gap-4">
                <ColLabel>Interpretation</ColLabel>
                <span
                  className="data text-ink-2 text-[11px]"
                  title="Which parser produced this reading of the question"
                >
                  {result.parsed_by}
                </span>
              </div>
              <div className="mt-1.5">
                <InterpretationLine intent={result.intent} />
              </div>
              {result.intent.assumptions.length ? (
                <ul className="mt-2.5 space-y-1">
                  {result.intent.assumptions.map((a) => (
                    <li key={a} className="text-ink-2 flex items-start gap-2 text-xs leading-snug">
                      <span className="text-ink-3 shrink-0 select-none" aria-hidden>
                        Assumed
                      </span>
                      {a}
                    </li>
                  ))}
                </ul>
              ) : null}
            </div>

            {/* curated matches */}
            <section className="mt-6">
              <div className="mb-3">
                <h3 className="text-ink-0 text-[13px] font-semibold">
                  {result.matches.length} matching dataset{result.matches.length === 1 ? '' : 's'}
                  <span className="text-ink-1 font-normal"> of {catalogueSize} integrated</span>
                </h3>
              </div>

              {result.matches.length ? (
                <div>
                  <DatasetTableHeader showScore />
                  {result.matches.map((dataset) => (
                    <DatasetRow
                      key={dataset.id}
                      dataset={dataset}
                      showScore
                      maxScore={maxScore}
                      intent={result.intent}
                      onExport={onExport}
                      exporting={exporting}
                      thumb={thumbs[dataset.id]}
                    />
                  ))}
                </div>
              ) : (
                <p className="text-ink-1 text-xs leading-relaxed">
                  No integrated dataset matches this reading of the question. {result.note}
                </p>
              )}
            </section>

            {result.federated && result.federated.found > 0 ? (
              <div className="mt-10">
                <FederatedList federated={result.federated} />
              </div>
            ) : null}

            {result.snippet ? (
              <details className="border-hairline mt-10 border-t pt-4">
                <summary className="text-ink-1 hover:text-ink-0 cursor-pointer text-xs select-none">
                  Reproduce this search in Python
                </summary>
                <div className="border-hairline mt-3 rounded border">
                  <div className="border-hairline flex items-center justify-between border-b px-3 py-1">
                    <span className="data text-ink-2 text-[11px]">python</span>
                    <CopyButton text={result.snippet} />
                  </div>
                  <pre className="data text-ink-1 overflow-x-auto px-3 py-2.5 text-[11px] leading-relaxed">
                    {result.snippet}
                  </pre>
                </div>
              </details>
            ) : null}
          </div>

          {/* ------------------------------------------------- context */}
          <aside className="lg:sticky lg:top-0 lg:self-start">
            <ColLabel className="mb-2">Request context</ColLabel>
            <RegionMap box={result.intent.bbox} className="border-hairline rounded border" />
            <MapAxes />
            <KV
              className="mt-4"
              rows={[
                {
                  k: 'Region',
                  v: result.intent.bbox ? (
                    <>
                      {result.intent.place ? (
                        <div className="text-ink-0">{titleCase(result.intent.place)}</div>
                      ) : null}
                      <div className="data text-ink-1">{fmtBox(result.intent.bbox)}</div>
                    </>
                  ) : (
                    <span className="text-ink-2">Not specified. Whole EEZ assumed</span>
                  ),
                },
                {
                  k: 'Period',
                  v:
                    result.intent.start || result.intent.end ? (
                      <span className="data">
                        {result.intent.start ?? '…'} → {result.intent.end ?? 'today'}
                      </span>
                    ) : (
                      <span className="text-ink-2">Not specified</span>
                    ),
                },
                {
                  k: 'Variables',
                  v: result.intent.variables.length ? (
                    <div className="flex flex-wrap gap-1">
                      {result.intent.variables.map((v) => (
                        <Tag key={v} tone="neutral">
                          {v}
                        </Tag>
                      ))}
                    </div>
                  ) : (
                    <span className="text-ink-2">Not specified</span>
                  ),
                },
                {
                  k: 'Kind',
                  v: result.intent.kinds.length ? (
                    result.intent.kinds.join(', ')
                  ) : (
                    <span className="text-ink-2">Any</span>
                  ),
                },
                ...(result.federated
                  ? [
                      {
                        k: 'Federation',
                        v: (
                          <span className="data">
                            {result.federated.servers_responding}/
                            {result.federated.servers_queried} servers · {result.federated.found}{' '}
                            hits
                          </span>
                        ),
                      },
                    ]
                  : []),
              ]}
            />
          </aside>
        </div>
      ) : null}
    </div>
  );
}
