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

import { useEffect, useMemo, useState } from 'react';
import {
  AlertTriangle,
  Download,
  FileSpreadsheet,
  Loader2,
  Table2,
} from 'lucide-react';
import { clsx } from 'clsx';

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
            ? `${detail.detail.message} — ${detail.detail.cells} cells against a ${detail.detail.max_cells} limit. ${(detail.detail.suggestions ?? []).join('; ')}`
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

  return (
    <div className="space-y-3">
      {/* ---------------- variables ---------------- */}
      <section>
        <div className="label mb-1.5">Variables</div>
        <div className="flex flex-wrap gap-1">
          {available.map((name) => {
            const on = chosen.includes(name);
            return (
              <button
                key={name}
                type="button"
                onClick={() => toggle(name)}
                className={clsx(
                  'border-hairline rounded border px-2 py-0.5 text-2xs transition-colors',
                  on ? 'border-cyan/50 bg-cyan/15 text-cyan' : 'text-ink-2 hover:text-ink-0',
                )}
              >
                {name}
              </button>
            );
          })}
          {!available.length && <span className="text-ink-3 text-2xs">loading…</span>}
        </div>
        <p className="text-ink-3 mt-1 text-2xs">
          Every variable here has been fetched successfully at least once. The list is short and
          true rather than long and aspirational — a column of blanks reads as “measured and
          absent”, which is worse than not offering it.
        </p>
      </section>

      {/* ---------------- area and period ---------------- */}
      <section className="grid gap-3 sm:grid-cols-2">
        <div>
          <div className="label mb-1.5">Area</div>
          <div className="mb-1 flex flex-wrap gap-1">
            {BOXES.map((preset) => (
              <button
                key={preset.label}
                type="button"
                onClick={() => setBox(preset.box)}
                className={clsx(
                  'border-hairline rounded border px-2 py-0.5 text-2xs transition-colors',
                  box.join() === preset.box.join()
                    ? 'border-cyan/50 bg-cyan/15 text-cyan'
                    : 'text-ink-2 hover:text-ink-0',
                )}
              >
                {preset.label}
              </button>
            ))}
          </div>
          <div className="data text-ink-3 text-2xs">
            {box.map((v) => v.toFixed(1)).join(', ')}
          </div>
        </div>

        <div>
          <div className="label mb-1.5">Period</div>
          <div className="flex items-center gap-1.5">
            <input
              type="date"
              value={start}
              onChange={(event) => setStart(event.target.value)}
              className="border-hairline data bg-abyss-1 text-ink-1 rounded border px-1.5 py-0.5 text-2xs"
            />
            <span className="text-ink-3 text-2xs">to</span>
            <input
              type="text"
              value={end}
              onChange={(event) => setEnd(event.target.value)}
              placeholder="today"
              className="border-hairline data bg-abyss-1 text-ink-1 w-24 rounded border px-1.5 py-0.5 text-2xs"
            />
          </div>
          <div className="mt-1.5 flex items-center gap-3">
            <label className="text-ink-2 flex items-center gap-1 text-2xs">
              points
              <select
                value={points}
                onChange={(event) => setPoints(Number(event.target.value))}
                className="border-hairline bg-abyss-1 text-ink-1 rounded border px-1 py-0.5 text-2xs"
              >
                <option value={1}>centre only</option>
                <option value={9}>3×3 lattice</option>
              </select>
            </label>
            <label className="text-ink-2 flex items-center gap-1 text-2xs">
              every
              <input
                type="number"
                min={1}
                max={30}
                value={stepDays}
                onChange={(event) =>
                  setStepDays(Math.min(30, Math.max(1, Number(event.target.value) || 1)))
                }
                className="border-hairline data bg-abyss-1 text-ink-1 w-12 rounded border px-1 py-0.5 text-center text-2xs"
              />
              day(s)
            </label>
          </div>
        </div>
      </section>

      {/* ---------------- run ---------------- */}
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={run}
          disabled={busy || !chosen.length}
          className="border-cyan/40 bg-cyan/12 text-cyan hover:bg-cyan/20 flex items-center gap-1.5 rounded border px-3 py-1 text-2xs transition-colors disabled:opacity-40"
        >
          {busy ? (
            <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
          ) : (
            <Table2 className="h-3 w-3" aria-hidden />
          )}
          {busy ? 'building…' : 'build and preview'}
        </button>

        <button
          type="button"
          onClick={() => download('xlsx')}
          disabled={downloading !== null || !chosen.length}
          className="border-hairline text-ink-1 hover:text-ink-0 flex items-center gap-1.5 rounded border px-3 py-1 text-2xs transition-colors disabled:opacity-40"
        >
          {downloading === 'xlsx' ? (
            <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
          ) : (
            <FileSpreadsheet className="h-3 w-3" aria-hidden />
          )}
          Excel
        </button>

        <button
          type="button"
          onClick={() => download('csv')}
          disabled={downloading !== null || !chosen.length}
          className="border-hairline text-ink-1 hover:text-ink-0 flex items-center gap-1.5 rounded border px-3 py-1 text-2xs transition-colors disabled:opacity-40"
        >
          {downloading === 'csv' ? (
            <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
          ) : (
            <Download className="h-3 w-3" aria-hidden />
          )}
          CSV
        </button>
      </div>

      {error ? (
        <p className="text-amber border-hairline bg-abyss-1 rounded border px-2.5 py-1.5 text-2xs">
          {error}
        </p>
      ) : null}

      {/* ---------------- what came back ---------------- */}
      {summary ? (
        <section className="space-y-2">
          <div className="text-ink-2 text-2xs">
            <span className="data text-ink-0">{summary.rows}</span> rows ×{' '}
            <span className="data text-ink-0">{summary.variables.length}</span> variables ·{' '}
            {summary.missing_values} blank cell(s) — blanks are upstream gaps, not zeroes.
          </div>

          {/* Empty columns ABOVE the table. A researcher who finds this after
              downloading has spent a round trip learning what we already knew. */}
          {Object.entries(emptyColumns).map(([name, why]) => (
            <p key={name} className="text-amber flex items-start gap-1.5 text-2xs leading-relaxed">
              <AlertTriangle className="mt-px h-3 w-3 shrink-0" aria-hidden />
              <span>
                <span className="data">{name}</span> is empty — {why}
              </span>
            </p>
          ))}

          {summary.unavailable_variables.length > 0 ? (
            <p className="text-amber text-2xs">
              Not served at all: {summary.unavailable_variables.join(', ')}
            </p>
          ) : null}

          {rows?.length ? (
            <div className="border-hairline max-h-72 overflow-auto rounded border">
              <table className="w-full text-2xs">
                <thead className="bg-abyss-1 sticky top-0">
                  <tr>
                    {columns.map((name) => (
                      <th
                        key={name}
                        className={clsx(
                          'border-hairline border-b px-2 py-1 text-left font-medium',
                          name in emptyColumns ? 'text-amber' : 'text-ink-1',
                        )}
                      >
                        {name}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {rows.slice(0, 200).map((row, index) => (
                    <tr key={index} className="border-hairline border-b last:border-0">
                      {columns.map((name) => (
                        <td key={name} className="data text-ink-2 px-2 py-0.5 whitespace-nowrap">
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

          {rows && rows.length > 200 ? (
            <p className="text-ink-3 text-2xs">
              Showing the first 200 of {rows.length} rows. The download has all of them.
            </p>
          ) : null}

          {/* Provenance, per source, naming the columns it produced. Merging two
              ORCA files means knowing that wind_speed is ERA5 reanalysis and sst
              is a satellite analysis. */}
          <div className="space-y-1.5">
            {summary.datasets.map((source) => (
              <div key={source.endpoint} className="border-hairline rounded border px-2.5 py-1.5">
                <div className="text-ink-1 text-2xs">
                  {source.title}
                  {source.columns?.length ? (
                    <span className="text-ink-3"> — {source.columns.join(', ')}</span>
                  ) : null}
                </div>
                <div className="text-ink-3 text-2xs">
                  {source.provider} · {source.licence}
                </div>
                <div className="text-ink-3 mt-0.5 text-2xs leading-relaxed">{source.caveats}</div>
              </div>
            ))}
          </div>
        </section>
      ) : null}
    </div>
  );
}
