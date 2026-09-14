/**
 * The harbour advisory board: which stretches of coast are unsafe today, and
 * for whom.
 *
 * This is the one screen in ORCA that is not about a point. Everywhere else the
 * reader has picked a place; here they have a coastline and a fleet, and the
 * question is where the line falls today.
 *
 * Two decisions carry the whole design.
 *
 * **Coastal order, never sorted by the user.** The rows run down the west coast
 * from Kutch, round Kanyakumari, and up the east coast to the Sundarbans. A
 * sortable column would let someone order by verdict and destroy the only thing
 * the board is for — a run of NO-GO harbours is legible as a stretch of coast
 * exactly because its neighbours are its neighbours.
 *
 * **The stretches are the headline, the grid is the evidence.** An officer
 * broadcasts "traditional craft should not sail anywhere between Kochi and
 * Mangaluru". They do not broadcast a spreadsheet. So the sentences come first
 * and the grid sits underneath for anyone who wants to check a specific harbour.
 */

import { useEffect, useMemo, useState } from 'react';
import { AlertTriangle, Anchor, Loader2, RefreshCw } from 'lucide-react';
import { clsx } from 'clsx';

interface Verdicts {
  verdict: 'GO' | 'CAUTION' | 'NO-GO' | 'UNVERIFIABLE';
  index: number;
  confidence: 'high' | 'low';
  reason: string | null;
}

interface BoardRow {
  name: string;
  district: string;
  state: string;
  coast: 'west' | 'east';
  lat: number;
  lon: number;
  conditions: {
    wave_m: number | null;
    wind_kn: number | null;
    visibility_km: number | null;
    cape_j_kg: number | null;
  };
  worst: string;
  verdicts: Record<string, Verdicts>;
}

interface Stretch {
  verdict: string;
  coast: string;
  from: string;
  to: string;
  harbours: string[];
  sentence: string;
}

interface Board {
  generated_at: string;
  harbours_assessed: number;
  classes: { code: string; label: string; max_wave_m: number; max_wind_kn: number }[];
  rows: BoardRow[];
  stretches: Record<string, Stretch[]>;
  counts: Record<string, Record<string, number>>;
  not_assessed: { name: string; state: string; why: string }[];
  how_to_read: string;
  what_this_is_not: string;
}

const TONE: Record<string, string> = {
  GO: 'bg-emerald-500/15 text-emerald-300 border-emerald-500/30',
  CAUTION: 'bg-amber-500/15 text-amber-300 border-amber-500/30',
  'NO-GO': 'bg-rose-500/20 text-rose-300 border-rose-500/40',
  UNVERIFIABLE: 'bg-slate-500/10 text-slate-400 border-slate-600/40',
};

/** Short names for the column headers. The full labels are long enough to make
 *  a 60-row grid unreadable, and the legend carries them. */
const SHORT: Record<string, string> = {
  'IND-TRAD': 'Trad',
  'IND-MOT-S': 'Motor',
  'IND-MECH-S': 'Mech S',
  'IND-MECH-L': 'Mech L',
  'IND-DEEPSEA': 'Deep',
};

export function HarbourBoard() {
  const [board, setBoard] = useState<Board | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [state, setState] = useState<string>('');
  const [focus, setFocus] = useState('IND-TRAD');

  const load = async (forState: string) => {
    setBusy(true);
    setError(null);
    try {
      const query = forState ? `?state=${encodeURIComponent(forState)}` : '';
      const response = await fetch(`/api/harbours/board${query}`);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      setBoard((await response.json()) as Board);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'The board could not be built.');
      setBoard(null);
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    void load('');
    // Loaded once on mount; the state filter re-loads explicitly.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const states = useMemo(
    () => [...new Set((board?.rows ?? []).map((r) => r.state))],
    [board],
  );

  const codes = board?.classes.map((c) => c.code) ?? [];
  const stretches = board?.stretches[focus] ?? [];

  return (
    <div className="space-y-3">
      <header className="flex flex-wrap items-center gap-2">
        <Anchor className="text-cyan h-4 w-4" aria-hidden />
        <span className="label">Harbour advisory board</span>
        {board ? (
          <span className="text-ink-3 text-2xs">
            {board.harbours_assessed} harbours · {new Date(board.generated_at).toUTCString()}
          </span>
        ) : null}
        <div className="ml-auto flex items-center gap-1.5">
          <select
            value={state}
            onChange={(event) => {
              setState(event.target.value);
              void load(event.target.value);
            }}
            className="border-hairline bg-abyss-1 text-ink-1 rounded border px-1.5 py-0.5 text-2xs"
          >
            <option value="">whole coast</option>
            {states.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
          <button
            type="button"
            onClick={() => void load(state)}
            disabled={busy}
            className="border-hairline text-ink-2 hover:text-ink-0 flex items-center gap-1 rounded border px-2 py-0.5 text-2xs transition-colors disabled:opacity-40"
          >
            {busy ? (
              <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
            ) : (
              <RefreshCw className="h-3 w-3" aria-hidden />
            )}
            refresh
          </button>
        </div>
      </header>

      {error ? (
        <p className="text-amber border-hairline bg-abyss-1 rounded border px-2.5 py-1.5 text-2xs">
          {error}
        </p>
      ) : null}

      {busy && !board ? (
        <p className="text-ink-3 text-2xs">Running the rule engine at every harbour…</p>
      ) : null}

      {board ? (
        <>
          {/* ---------- the advisory itself ---------- */}
          <section>
            <div className="mb-1.5 flex flex-wrap items-center gap-1">
              <span className="text-ink-3 text-2xs">Advisory for</span>
              {board.classes.map((entry) => (
                <button
                  key={entry.code}
                  type="button"
                  onClick={() => setFocus(entry.code)}
                  title={entry.label}
                  className={clsx(
                    'border-hairline rounded border px-2 py-0.5 text-2xs transition-colors',
                    focus === entry.code
                      ? 'border-cyan/50 bg-cyan/15 text-cyan'
                      : 'text-ink-2 hover:text-ink-0',
                  )}
                >
                  {SHORT[entry.code] ?? entry.code}
                </button>
              ))}
              <span className="text-ink-3 ml-2 text-2xs">
                {Object.entries(board.counts[focus] ?? {})
                  .map(([verdict, count]) => `${count} ${verdict}`)
                  .join(' · ')}
              </span>
            </div>

            <ul className="space-y-1">
              {stretches.map((stretch, index) => (
                <li
                  key={`${stretch.from}-${index}`}
                  className={clsx(
                    'rounded border px-2.5 py-1 text-2xs',
                    TONE[stretch.verdict] ?? TONE.UNVERIFIABLE,
                  )}
                >
                  {stretch.sentence}
                  <span className="opacity-60"> · {stretch.coast} coast</span>
                </li>
              ))}
            </ul>
          </section>

          {/* ---------- the grid ---------- */}
          <section>
            <p className="text-ink-3 mb-1 text-2xs leading-relaxed">{board.how_to_read}</p>
            <div className="border-hairline max-h-[26rem] overflow-auto rounded border">
              <table className="w-full text-2xs">
                <thead className="bg-abyss-1 sticky top-0 z-10">
                  <tr>
                    <th className="border-hairline text-ink-1 border-b px-2 py-1 text-left font-medium">
                      Harbour
                    </th>
                    <th className="border-hairline text-ink-3 border-b px-2 py-1 text-right font-medium">
                      Hs
                    </th>
                    <th className="border-hairline text-ink-3 border-b px-2 py-1 text-right font-medium">
                      wind
                    </th>
                    {codes.map((code) => (
                      <th
                        key={code}
                        className="border-hairline text-ink-1 border-b px-2 py-1 text-center font-medium"
                      >
                        {SHORT[code] ?? code}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {board.rows.map((entry) => (
                    <tr key={entry.name} className="border-hairline border-b last:border-0">
                      <td className="px-2 py-0.5 whitespace-nowrap">
                        <span className="text-ink-1">{entry.name}</span>
                        <span className="text-ink-3"> · {entry.state}</span>
                      </td>
                      <td className="data text-ink-2 px-2 py-0.5 text-right">
                        {entry.conditions.wave_m ?? '—'}
                      </td>
                      <td className="data text-ink-2 px-2 py-0.5 text-right">
                        {entry.conditions.wind_kn ?? '—'}
                      </td>
                      {codes.map((code) => {
                        const cell = entry.verdicts[code];
                        return (
                          <td key={code} className="px-1 py-0.5 text-center">
                            <span
                              title={cell?.reason ?? undefined}
                              className={clsx(
                                'inline-block rounded border px-1.5 py-px',
                                TONE[cell?.verdict] ?? TONE.UNVERIFIABLE,
                              )}
                            >
                              {cell?.verdict === 'UNVERIFIABLE' ? '?' : cell?.verdict}
                            </span>
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          {board.not_assessed.length > 0 ? (
            <p className="text-amber flex items-start gap-1.5 text-2xs leading-relaxed">
              <AlertTriangle className="mt-px h-3 w-3 shrink-0" aria-hidden />
              <span>
                Not assessed: {board.not_assessed.map((h) => h.name).join(', ')} —{' '}
                {board.not_assessed[0].why}
              </span>
            </p>
          ) : null}

          <p className="text-ink-3 text-2xs leading-relaxed">{board.what_this_is_not}</p>
        </>
      ) : null}
    </div>
  );
}
