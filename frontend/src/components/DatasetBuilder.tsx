/**
 * Build a dataset: pick variables, a box and a date range, see the rows, take
 * the file.
 *
 * The preview is the point, and it is why this is a page rather than a download
 * button. A researcher who downloads a spreadsheet and only then discovers that
 * three of their columns are blank has spent a round trip to learn something the
 * system knew before it started. So the preview renders the real table, and
 * every column that came back completely empty is called out ABOVE it with the
 * provider's own explanation — a monthly composite has no value on a Tuesday,
 * and that is a different fact from "no data".
 *
 * An oversized request comes back as a 413 carrying its own figures — cells
 * against the limit, and which knob to turn — and those are rendered instead of
 * "request failed". There is deliberately no cost preview before the button:
 * costing a request accurately means running `plan()` server-side, and adding a
 * round trip to tell someone their request is fine is worse than letting them
 * press the button.
 */

import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { Download, FileSpreadsheet, Table2 } from 'lucide-react';
import { clsx } from 'clsx';

import { MapAxes, RegionMap } from '@/components/research/RegionMap';
import { Button, ColLabel, KV, Lede, Notice, Title, fmtBox } from '@/components/research/ui';

/** What the JSON format of `/research/build` returns. */
interface BuildSummary {
  rows: number;
  variables: string[];
  unavailable_variables: string[];
  missing_values: number;
  missing_by_variable: Record<string, number>;
  empty_columns: Record<string, string>;
  datasets: {
    title: string;
    provider: string;
    licence: string;
    endpoint: string;
    caveats: string;
    columns?: string[];
  }[];
}

/** Sea areas worth offering as one click. Each is a real working box rather
 *  than a decorative label — these are the four a researcher on this coast
 *  actually asks for. */
const BOXES: { label: string; box: [number, number, number, number] }[] = [
  { label: 'Kerala coast', box: [74, 8, 77.5, 13] },
  { label: 'Tamil Nadu / Bay', box: [79, 8, 83, 14] },
  { label: 'Arabian Sea', box: [68, 8, 76, 22] },
  { label: 'Bay of Bengal', box: [80, 8, 92, 22] },
];

function isoDaysAgo(days: number): string {
  const when = new Date(Date.now() - days * 86_400_000);
  return when.toISOString().slice(0, 10);
}

const INPUT =
  'border-hairline-strong data bg-abyss-1 text-ink-0 focus:border-cyan/60 h-8 rounded border px-2 text-xs outline-none transition-colors';

export function DatasetBuilder({
  bbox,
}: {
  /** From the discover tab's parsed intent, when the researcher asked there first. */
  bbox?: [number, number, number, number] | null;
}) {
  const [available, setAvailable] = useState<string[]>([]);
  const [chosen, setChosen] = useState<string[]>(['sst', 'wave_height', 'wind_speed']);
  const [box, setBox] = useState<[number, number, number, number]>(bbox ?? BOXES[0].box);
  const [start, setStart] = useState(isoDaysAgo(30));
  const [end, setEnd] = useState('today');
  const [points, setPoints] = useState(1);
  const [stepDays, setStepDays] = useState(1);

  const [rows, setRows] = useState<Record<string, unknown>[] | null>(null);
  const [summary, setSummary] = useState<BuildSummary | null>(null);
  const [busy, setBusy] = useState(false);
  const [downloading, setDownloading] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void fetch('/api/research/build/variables')
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => d && setAvailable(d.variables as string[]))
      .catch(() => setError('Could not reach the variable list.'));
  }, []);

  useEffect(() => {
    if (bbox) setBox(bbox);
  }, [bbox]);

  const body = useMemo(
    () => ({
      variables: chosen,
      west: box[0],
      south: box[1],
      east: box[2],
      north: box[3],
      start,
      end,
      points,
      step_days: stepDays,
    }),
    [chosen, box, start, end, points, stepDays],
  );

  const run = async () => {
    if (!chosen.length) return;
    setBusy(true);
    setError(null);
    try {
      const response = await fetch('/api/research/build', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...body, format: 'json' }),
      });
      if (!response.ok) {
        const detail = await response.json().catch(() => null);
        // A 413 carries the actual figure and a suggested reduction. Showing
        // that beats "request failed", which leaves the researcher to guess
        // which of five knobs to turn.
        const message =
          typeof detail?.detail === 'object'
            ? `${detail.detail.message}. ${detail.detail.cells} cells against a ${detail.detail.max_cells} limit. ${(detail.detail.suggestions ?? []).join('; ')}`
            : (detail?.detail ?? `HTTP ${response.status}`);
        throw new Error(String(message));
      }
      const data = await response.json();
      setRows(data.rows as Record<string, unknown>[]);
      setSummary(data.summary as BuildSummary);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'The build failed.');
      setRows(null);
      setSummary(null);
    } finally {
      setBusy(false);
    }
  };

  const download = async (format: 'csv' | 'xlsx') => {
    setDownloading(format);
    setError(null);
    try {
      const response = await fetch('/api/research/build', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...body, format }),
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      // Resolve "today" for the filename. `orca_2026-08-15_today.csv` sorts
      // wrongly in a folder and stops meaning anything the moment tomorrow
      // arrives, which for a file a researcher keeps for years is a real loss.
      const endLabel = /^\d{4}-\d{2}-\d{2}$/.test(end) ? end : isoDaysAgo(1);
      anchor.download = `orca_${start}_${endLabel}.${format}`;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'The download failed.');
    } finally {
      setDownloading(null);
    }
  };

  const toggle = (name: string) =>
    setChosen((current) =>
      current.includes(name) ? current.filter((v) => v !== name) : [...current, name].slice(0, 16),
    );

  const columns = rows?.length ? Object.keys(rows[0]) : [];
  const emptyColumns = summary?.empty_columns ?? {};
  const preset = BOXES.find((p) => p.box.join() === box.join());

  return (
    <div className="mx-auto w-full max-w-[1440px] px-6 py-6 lg:px-10 lg:py-8">
      <Title>Build a dataset</Title>
      <Lede>
        Several variables over a sea area and a date range, as one table, previewed here before
        it is downloaded, with every empty column explained.
      </Lede>

      <div className="mt-8 grid gap-x-12 gap-y-8 lg:grid-cols-[minmax(0,1fr)_320px]">
        {/* ------------------------------------------------------ steps */}
        <div className="divide-hairline divide-y">
          <Step n="01" title="Variables" aside={`${chosen.length} of ${available.length || '—'} selected · up to 16`}>
            {available.length ? (
              <div className="grid grid-cols-2 gap-x-6 gap-y-1 sm:grid-cols-3 xl:grid-cols-4">
                {available.map((name) => {
                  const on = chosen.includes(name);
                  return (
                    <label
                      key={name}
                      className="flex h-7 cursor-pointer items-center gap-2.5 select-none"
                    >
                      <input
                        type="checkbox"
                        checked={on}
                        onChange={() => toggle(name)}
                        className="accent-cyan h-3.5 w-3.5 shrink-0"
                      />
                      <span className={clsx('data text-xs', on ? 'text-ink-0' : 'text-ink-1')}>
                        {name}
                      </span>
                    </label>
                  );
                })}
              </div>
            ) : (
              <span className="text-ink-2 text-xs">Loading the variable list…</span>
            )}
            <p className="text-ink-2 mt-3 text-xs">
              Only variables ORCA has fetched successfully at least once are offered.
            </p>
          </Step>

          <Step n="02" title="Region" aside={fmtBox(box)}>
            <div className="grid gap-6 sm:grid-cols-[minmax(0,1fr)_220px]">
              <div>
                <div className="flex flex-wrap gap-1">
                  {BOXES.map((p) => (
                    <button
                      key={p.label}
                      type="button"
                      onClick={() => setBox(p.box)}
                      className={clsx(
                        'h-7 rounded px-2.5 text-xs transition-colors',
                        box.join() === p.box.join()
                          ? 'bg-abyss-2 text-ink-0'
                          : 'text-ink-1 hover:text-ink-0',
                      )}
                    >
                      {p.label}
                    </button>
                  ))}
                </div>
                <div className="data text-ink-1 mt-3 grid grid-cols-[max-content_1fr] gap-x-4 gap-y-0.5 text-xs">
                  <span className="text-ink-2">west</span>
                  <span>{box[0].toFixed(2)}°</span>
                  <span className="text-ink-2">south</span>
                  <span>{box[1].toFixed(2)}°</span>
                  <span className="text-ink-2">east</span>
                  <span>{box[2].toFixed(2)}°</span>
                  <span className="text-ink-2">north</span>
                  <span>{box[3].toFixed(2)}°</span>
                </div>
                {!preset ? (
                  <p className="text-ink-2 mt-3 text-xs">Carried over from the Discover query.</p>
                ) : null}
              </div>
              <div>
                <RegionMap box={box} className="border-hairline rounded border" />
                <MapAxes />
              </div>
            </div>
          </Step>

          <Step n="03" title="Period" aside={`${start} → ${end || 'today'}`}>
            <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
              <Field label="From">
                <input
                  type="date"
                  value={start}
                  onChange={(e) => setStart(e.target.value)}
                  className={INPUT}
                />
              </Field>
              <Field label="To">
                <input
                  type="text"
                  value={end}
                  onChange={(e) => setEnd(e.target.value)}
                  placeholder="today"
                  className={clsx(INPUT, 'w-32')}
                />
              </Field>
              <Field label="Every">
                <div className="flex items-center gap-2">
                  <input
                    type="number"
                    min={1}
                    max={30}
                    value={stepDays}
                    onChange={(e) =>
                      setStepDays(Math.min(30, Math.max(1, Number(e.target.value) || 1)))
                    }
                    className={clsx(INPUT, 'w-16 text-center')}
                  />
                  <span className="text-ink-1 text-xs">day{stepDays === 1 ? '' : 's'}</span>
                </div>
              </Field>
            </div>
          </Step>

          <Step
            n="04"
            title="Sampling"
            aside={points === 1 ? 'centre of the box' : '3 × 3 lattice across the box'}
          >
            <div className="flex flex-wrap gap-1">
              {[
                { v: 1, label: 'Centre point', hint: 'one row per day' },
                { v: 9, label: '3 × 3 lattice', hint: 'nine rows per day' },
              ].map((opt) => (
                <button
                  key={opt.v}
                  type="button"
                  onClick={() => setPoints(opt.v)}
                  className={clsx(
                    'flex h-9 flex-col items-start justify-center rounded px-3 text-left transition-colors',
                    points === opt.v ? 'bg-abyss-2 text-ink-0' : 'text-ink-1 hover:text-ink-0',
                  )}
                >
                  <span className="text-xs">{opt.label}</span>
                  <span className="text-ink-2 text-[11px]">{opt.hint}</span>
                </button>
              ))}
            </div>
          </Step>
        </div>

        {/* ---------------------------------------------------- request */}
        <aside className="lg:sticky lg:top-0 lg:self-start">
          <ColLabel>Request</ColLabel>
          <KV
            className="mt-2"
            rows={[
              {
                k: 'Variables',
                v: chosen.length ? (
                  <span className="data break-words">{chosen.join(', ')}</span>
                ) : (
                  <span className="text-amber">none selected</span>
                ),
              },
              { k: 'Region', v: <span className="data">{preset?.label ?? fmtBox(box)}</span> },
              {
                k: 'Period',
                v: (
                  <span className="data">
                    {start} → {end || 'today'}
                    {stepDays > 1 ? `, every ${stepDays} d` : ''}
                  </span>
                ),
              },
              { k: 'Sampling', v: points === 1 ? 'centre point' : '3 × 3 lattice' },
              {
                k: 'Output',
                v: 'one row per day per point; blanks are upstream gaps, never zeroes',
              },
            ]}
          />

          <div className="mt-6 flex flex-col gap-2">
            <Button kind="primary" onClick={run} busy={busy} disabled={!chosen.length} className="h-9 justify-center">
              <Table2 className="h-3.5 w-3.5" aria-hidden />
              Build and preview
            </Button>
            <div className="grid grid-cols-2 gap-2">
              <Button
                onClick={() => download('csv')}
                busy={downloading === 'csv'}
                disabled={downloading !== null || !chosen.length}
                className="justify-center"
              >
                <Download className="h-3.5 w-3.5" aria-hidden />
                CSV
              </Button>
              <Button
                onClick={() => download('xlsx')}
                busy={downloading === 'xlsx'}
                disabled={downloading !== null || !chosen.length}
                className="justify-center"
              >
                <FileSpreadsheet className="h-3.5 w-3.5" aria-hidden />
                Excel
              </Button>
            </div>
          </div>

          {error ? (
            <Notice tone="amber" className="mt-5">
              {error}
            </Notice>
          ) : null}
        </aside>
      </div>

      {/* ----------------------------------------------------------- result */}
      {summary ? (
        <section className="border-hairline-strong mt-12 border-t pt-6">
          <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
            <h3 className="text-ink-0 text-[13px] font-semibold">Preview</h3>
            <span className="text-ink-1 text-xs">
              <span className="data text-ink-0">{summary.rows}</span> rows ×{' '}
              <span className="data text-ink-0">{summary.variables.length}</span> variables ·{' '}
              <span className="data text-ink-0">{summary.missing_values}</span> blank cells
            </span>
            {rows && rows.length > 200 ? (
              <span className="text-ink-2 text-xs">
                first 200 shown; the download has all {rows.length}
              </span>
            ) : null}
          </div>

          {Object.keys(emptyColumns).length || summary.unavailable_variables.length ? (
            <div className="mt-4 space-y-2">
              {Object.entries(emptyColumns).map(([name, why]) => (
                <Notice key={name} tone="amber">
                  <span className="data">{name}</span> is empty: {why}
                </Notice>
              ))}
              {summary.unavailable_variables.length ? (
                <Notice tone="amber">
                  Not served at all:{' '}
                  <span className="data">{summary.unavailable_variables.join(', ')}</span>
                </Notice>
              ) : null}
            </div>
          ) : null}

          {rows?.length ? (
            <div className="border-hairline mt-4 max-h-[480px] overflow-auto rounded border">
              <table className="w-full text-xs">
                <thead className="bg-abyss-1 sticky top-0 z-10">
                  <tr>
                    {columns.map((name) => (
                      <th
                        key={name}
                        className={clsx(
                          'data border-hairline-strong border-b px-3 py-2 text-left font-medium whitespace-nowrap',
                          name in emptyColumns ? 'text-amber' : 'text-ink-0',
                        )}
                      >
                        {name}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {rows.slice(0, 200).map((row, index) => (
                    <tr key={index} className="border-hairline hover:bg-abyss-1 border-b last:border-0">
                      {columns.map((name) => (
                        <td
                          key={name}
                          className="data text-ink-1 px-3 py-1 whitespace-nowrap tabular-nums"
                        >
                          {row[name] === null || row[name] === undefined ? (
                            <span className="text-ink-3">—</span>
                          ) : (
                            String(row[name])
                          )}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}

          {/* Provenance, per source, naming the columns it produced. Merging two
              ORCA files means knowing that wind_speed is ERA5 reanalysis and sst
              is a satellite analysis. */}
          <div className="mt-8">
            <ColLabel>Sources in this file</ColLabel>
            <table className="mt-2 w-full text-xs">
              <thead>
                <tr className="border-hairline-strong border-b">
                  <th className="label py-1.5 pr-4 text-left font-medium">Dataset</th>
                  <th className="label py-1.5 pr-4 text-left font-medium">Columns</th>
                  <th className="label hidden py-1.5 pr-4 text-left font-medium md:table-cell">
                    Provider
                  </th>
                  <th className="label hidden py-1.5 text-left font-medium lg:table-cell">
                    Licence
                  </th>
                </tr>
              </thead>
              <tbody>
                {summary.datasets.map((source) => (
                  <tr key={source.endpoint} className="border-hairline border-b align-top">
                    <td className="py-2.5 pr-4">
                      <div className="text-ink-0">{source.title}</div>
                      <div className="text-ink-2 mt-0.5 max-w-xl leading-relaxed">
                        {source.caveats}
                      </div>
                    </td>
                    <td className="data text-ink-1 py-2.5 pr-4">
                      {source.columns?.length ? source.columns.join(', ') : '—'}
                    </td>
                    <td className="text-ink-1 hidden py-2.5 pr-4 md:table-cell">{source.provider}</td>
                    <td className="text-ink-1 hidden py-2.5 lg:table-cell">{source.licence}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ) : null}
    </div>
  );
}

function Step({
  n,
  title,
  aside,
  children,
}: {
  n: string;
  title: string;
  aside?: string;
  children: ReactNode;
}) {
  return (
    <section className="grid gap-x-8 gap-y-3 py-6 first:pt-0 md:grid-cols-[140px_minmax(0,1fr)]">
      <div>
        <div className="flex items-baseline gap-2">
          <span className="data text-ink-2 text-[11px]">{n}</span>
          <h3 className="text-ink-0 text-[13px] font-semibold">{title}</h3>
        </div>
        {aside ? <div className="data text-ink-2 mt-1 text-[11px] leading-snug">{aside}</div> : null}
      </div>
      <div className="min-w-0">{children}</div>
    </section>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="flex items-center gap-2.5">
      <span className="label">{label}</span>
      {children}
    </label>
  );
}
