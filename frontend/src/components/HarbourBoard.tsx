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
 * the board is for: a run of NO-GO harbours is legible as a stretch of coast
 * exactly because its neighbours are its neighbours.
 *
 * **The stretches are the headline, the grid is the evidence.** An officer
 * broadcasts "traditional craft should not sail anywhere between Kochi and
 * Mangaluru". They do not broadcast a spreadsheet. So the sentences come first
 * and the grid sits underneath for anyone who wants to check a specific harbour.
 */

import { useEffect, useMemo, useState } from 'react';
import {
  Anchor,
  Loader2,
  Navigation,
  RefreshCw,
  Sailboat,
  Ship,
  ShipWheel,
  X,
  type LucideIcon,
} from 'lucide-react';
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

type Verdict = 'GO' | 'CAUTION' | 'NO-GO' | 'UNVERIFIABLE';

const CLASS_LABEL: Record<string, string> = {
  'IND-TRAD': 'Traditional',
  'IND-MOT-S': 'Small motorised',
  'IND-MECH-S': 'Small mechanised',
  'IND-MECH-L': 'Large mechanised',
  'IND-DEEPSEA': 'Deep-sea',
};

const CLASS_ICON: Record<string, LucideIcon> = {
  'IND-TRAD': Sailboat,
  'IND-MOT-S': Navigation,
  'IND-MECH-S': Anchor,
  'IND-MECH-L': Ship,
  'IND-DEEPSEA': ShipWheel,
};

const CLASS_SHORT: Record<string, string> = {
  'IND-TRAD': 'Trad',
  'IND-MOT-S': 'Motor',
  'IND-MECH-S': 'Mech S',
  'IND-MECH-L': 'Mech L',
  'IND-DEEPSEA': 'Deep',
};

const VERDICT_ORDER: Verdict[] = ['NO-GO', 'CAUTION', 'GO', 'UNVERIFIABLE'];

const VERDICT_TEXT: Record<string, string> = {
  GO: 'text-jade',
  CAUTION: 'text-amber',
  'NO-GO': 'text-red',
  UNVERIFIABLE: 'text-ink-2',
};

const VERDICT_RAIL: Record<string, string> = {
  GO: 'bg-jade',
  CAUTION: 'bg-amber',
  'NO-GO': 'bg-red',
  UNVERIFIABLE: 'bg-ink-3',
};

function prose(text: string) {
  return text.replace(/\s+[—–]\s+/g, '; ');
}

function fmtWhen(iso: string) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return `${d.toISOString().slice(0, 16).replace('T', ' ')} UTC`;
}

function stretchKey(stretch: Stretch, index: number) {
  return `${stretch.coast}:${stretch.from}:${stretch.to}:${index}`;
}

export function HarbourBoard({ onClose }: { onClose: () => void }) {
  const [board, setBoard] = useState<Board | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [state, setState] = useState<string>('');
  const [focus, setFocus] = useState('IND-TRAD');
  const [picked, setPicked] = useState<string | null>(null);
  const [elapsed, setElapsed] = useState(0);

  const load = async (forState: string) => {
    setBusy(true);
    setError(null);
    try {
      const query = forState ? `?state=${encodeURIComponent(forState)}` : '';
      const response = await fetch(`/api/harbours/board${query}`);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      setBoard((await response.json()) as Board);
      setPicked(null);
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

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  useEffect(() => {
    if (!busy || board) {
      setElapsed(0);
      return;
    }
    const id = window.setInterval(() => setElapsed((s) => s + 1), 1000);
    return () => window.clearInterval(id);
  }, [busy, board]);

  const states = useMemo(
    () => [...new Set((board?.rows ?? []).map((r) => r.state))],
    [board],
  );

  const codes = board?.classes.map((c) => c.code) ?? [];
  const stretches = board?.stretches[focus] ?? [];
  const west = stretches.filter((s) => s.coast === 'west');
  const east = stretches.filter((s) => s.coast === 'east');
  const counts = board?.counts[focus] ?? {};
  const activeClass = board?.classes.find((c) => c.code === focus);
  const highlighted = useMemo(() => {
    if (!picked) return null;
    const stretch = stretches.find((s, i) => stretchKey(s, i) === picked);
    return stretch ? new Set(stretch.harbours) : null;
  }, [picked, stretches]);

  return (
    <div className="bg-abyss-0 fixed inset-0 z-50 flex flex-col">
      <header className="border-hairline flex h-12 shrink-0 items-stretch border-b px-5">
        <div className="flex items-center gap-2.5 pr-8">
          <span className="data text-cyan text-sm font-bold tracking-[0.18em]">ORCA</span>
          <span className="text-ink-3">/</span>
          <span className="text-ink-0 text-[13px] font-semibold">Harbour advisory board</span>
          <span className="text-ink-2 hidden text-xs lg:inline">Coastal authority view</span>
        </div>
        <div className="ml-auto flex items-center gap-5">
          {board ? (
            <span className="data text-ink-2 hidden text-[11px] sm:block">
              {board.harbours_assessed} harbours · {fmtWhen(board.generated_at)}
            </span>
          ) : null}
          <button
            type="button"
            onClick={onClose}
            className="text-ink-1 hover:text-ink-0 hover:bg-abyss-2 flex h-7 items-center gap-1.5 rounded px-2 text-xs transition-colors"
            title="Back to the map (Esc)"
            aria-label="Close the harbour advisory board"
          >
            <X className="h-3.5 w-3.5" aria-hidden />
            <span className="hidden sm:inline">Close</span>
          </button>
        </div>
      </header>

      <main className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-[1200px] px-6 py-6 lg:px-10 lg:py-8">
          {error ? (
            <div className="border-hairline-strong mb-6 flex flex-wrap items-center gap-3 border-b pb-4">
              <p className="text-red text-xs">{error}</p>
              <button
                type="button"
                onClick={() => void load(state)}
                className="text-ink-1 hover:text-ink-0 text-xs"
              >
                Retry
              </button>
            </div>
          ) : null}

          {/* controls */}
          <div className="flex flex-wrap items-end justify-between gap-4">
            <div>
              <h1 className="text-ink-0 text-lg font-semibold tracking-tight">
                Which stretches of coast are unsafe today, and for whom
              </h1>
              <p className="text-ink-1 mt-1 max-w-xl text-xs leading-relaxed">
                Same rule engine as a single-point verdict, run at every harbour and read along
                the coast. Choose the vessel class the advisory is for.
              </p>
            </div>
            <div className="flex items-center gap-2">
              <select
                value={state}
                onChange={(event) => {
                  setState(event.target.value);
                  void load(event.target.value);
                }}
                className="border-hairline-strong bg-abyss-1 text-ink-0 focus:border-cyan/60 h-8 rounded border px-2 text-xs outline-none"
                aria-label="Filter by state"
              >
                <option value="">Whole coast</option>
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
                className="border-hairline-strong text-ink-0 hover:bg-abyss-2 flex h-8 items-center gap-1.5 rounded border px-3 text-xs font-medium transition-colors disabled:opacity-40"
              >
                {busy ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
                ) : (
                  <RefreshCw className="h-3.5 w-3.5" aria-hidden />
                )}
                Refresh
              </button>
            </div>
          </div>

          <nav
            className="border-hairline mt-5 flex flex-wrap items-stretch gap-1 border-b"
            role="tablist"
            aria-label="Vessel class"
          >
            {(
              board?.classes ??
              Object.keys(CLASS_LABEL).map((code) => ({ code, label: CLASS_LABEL[code] }))
            ).map((entry) => {
              const active = focus === entry.code;
              const limits = board?.classes.find((c) => c.code === entry.code) ?? null;
              const Icon = CLASS_ICON[entry.code] ?? Ship;
              return (
                <button
                  key={entry.code}
                  type="button"
                  role="tab"
                  aria-selected={active}
                  title={entry.label}
                  onClick={() => {
                    setFocus(entry.code);
                    setPicked(null);
                  }}
                  className={clsx(
                    'relative flex items-center gap-2 px-3 py-2.5 text-left transition-colors',
                    active ? 'text-ink-0' : 'text-ink-1 hover:text-ink-0',
                  )}
                >
                  <Icon
                    className={clsx('h-4 w-4 shrink-0', active ? 'text-cyan' : 'text-ink-2')}
                    strokeWidth={1.75}
                    aria-hidden
                  />
                  <span className="text-xs font-medium whitespace-nowrap">
                    {CLASS_LABEL[entry.code] ?? entry.code}
                  </span>
                  {limits ? (
                    <span className="data text-ink-2 hidden text-[10px] whitespace-nowrap xl:inline">
                      Hs ≤ {limits.max_wave_m} m · {limits.max_wind_kn} kn
                    </span>
                  ) : null}
                  {active ? (
                    <span className="bg-cyan absolute inset-x-3 bottom-0 h-0.5" aria-hidden />
                  ) : null}
                </button>
              );
            })}
            {board ? (
              <div className="ml-auto flex items-center gap-x-4 self-center px-1">
                <span className="text-ink-2 hidden text-[11px] sm:inline">
                  {activeClass?.label ?? CLASS_LABEL[focus] ?? focus}
                </span>
                <span className="data flex items-baseline gap-x-3 text-[11px]">
                  {VERDICT_ORDER.filter((v) => counts[v]).map((v) => (
                    <span key={v} className={VERDICT_TEXT[v]}>
                      <span className="text-sm font-semibold">{counts[v]}</span>{' '}
                      {v === 'UNVERIFIABLE' ? 'n/a' : v}
                    </span>
                  ))}
                </span>
              </div>
            ) : null}
          </nav>

          {busy && !board ? (
            <LoadingState elapsed={elapsed} />
          ) : null}

          {board ? (
            <>
              {busy ? (
                <div className="text-ink-2 mt-4 flex items-center gap-2 text-xs">
                  <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
                  Refreshing the board. The current advisory stays until the new one arrives.
                </div>
              ) : null}

              <section className="mt-8">
                <h2 className="text-ink-0 text-[15px] font-semibold tracking-tight">
                  Coastal advisory
                </h2>
                <p className="text-ink-2 mt-1 text-[13px]">
                  Contiguous runs of the same verdict, in coastal order. Select a stretch to mark
                  its harbours in the evidence below.
                </p>

                <div className="mt-5 grid gap-x-12 gap-y-8 lg:grid-cols-2">
                  <CoastLedger
                    title="West coast"
                    hint="Kutch to Kanyakumari"
                    stretches={west}
                    picked={picked}
                    onPick={setPicked}
                  />
                  <CoastLedger
                    title="East coast"
                    hint="Kanyakumari to the Sundarbans"
                    stretches={east}
                    picked={picked}
                    onPick={setPicked}
                  />
                </div>
              </section>

              <section className="border-hairline-strong mt-12 border-t pt-6">
                <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1">
                  <h2 className="text-ink-0 text-[15px] font-semibold tracking-tight">
                    Evidence
                  </h2>
                  <span className="text-ink-2 text-xs">
                    {board.rows.length} harbours · one row each, in coastal order
                  </span>
                </div>

                <HowToRead text={board.how_to_read} />

                <div className="border-hairline-strong mt-5 max-h-[32rem] overflow-auto border-y">
                  <table className="w-full text-xs">
                    <thead className="bg-abyss-0 sticky top-0 z-10">
                      <tr>
                        <th className="label border-hairline-strong border-b px-3 py-2 text-left font-medium">
                          Harbour
                        </th>
                        <th className="label border-hairline-strong border-b px-3 py-2 text-left font-medium">
                          State
                        </th>
                        <th className="label border-hairline-strong border-b px-3 py-2 text-right font-medium">
                          Hs (m)
                        </th>
                        <th className="label border-hairline-strong border-b px-3 py-2 text-right font-medium">
                          Wind (kn)
                        </th>
                        {codes.map((code) => (
                          <th
                            key={code}
                            title={CLASS_LABEL[code] ?? code}
                            className={clsx(
                              'label border-b px-2 py-2 text-center font-medium',
                              code === focus
                                ? 'border-cyan text-ink-0 bg-abyss-1/80'
                                : 'border-hairline-strong',
                            )}
                          >
                            {CLASS_SHORT[code] ?? code}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {board.rows.map((entry, index) => {
                        const coastBreak =
                          index === 0 || entry.coast !== board.rows[index - 1].coast;
                        const on = highlighted?.has(entry.name) ?? false;
                        return (
                          <RowGroup
                            key={entry.name}
                            coastBreak={coastBreak}
                            coast={entry.coast}
                            colSpan={4 + codes.length}
                            entry={entry}
                            codes={codes}
                            focus={focus}
                            on={on}
                          />
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </section>

              <div className="data text-ink-2 mt-3 flex flex-wrap gap-x-5 gap-y-1 text-[11px]">
                <span className="text-jade">GO</span>
                <span className="text-amber">CAUTION</span>
                <span className="text-red">NO-GO</span>
                <span>n/a: no sea cell answered, so no verdict</span>
                <span className="sm:ml-auto">Hover a verdict for the veto that set it</span>
              </div>

              <div className="border-hairline mt-10 space-y-6 border-t pt-5">
                <div title={prose(board.what_this_is_not)}>
                  <div className="label">Data and authority note</div>
                  <p className="text-ink-1 mt-1.5 text-xs whitespace-nowrap">
                    Not an official advisory. ORCA's rule engine on model conditions at
                    ORCA-geocoded positions; State Fisheries and IMD advisories carry legal force.
                  </p>
                </div>
                {board.not_assessed.length > 0 ? (
                  <div>
                    <div className="label">Not assessed</div>
                    <p className="text-ink-1 mt-1.5 text-xs leading-relaxed">
                      <span className="text-ink-0">
                        {board.not_assessed.map((h) => h.name).join(', ')}.
                      </span>{' '}
                      {board.not_assessed[0].why}
                    </p>
                  </div>
                ) : null}
              </div>
            </>
          ) : null}
        </div>
      </main>
    </div>
  );
}

/**
 * The board's own reading guide, split into the three things it says. `text` is
 * the sentence the API ships; it stays attached as the tooltip so the split
 * never drifts from the source of truth silently.
 */
function HowToRead({ text }: { text: string }) {
  const steps = [
    [
      'Coastal order',
      'Rows run down the west coast from Kutch, round Kanyakumari, and up the east coast to the Sundarbans.',
    ],
    ['One column per class', 'Each column is a boat class. The class you chose above is shaded.'],
    [
      'Advisory boundary',
      'The column where the verdict changes is the advisory: everything smaller than that class should stay in today.',
    ],
  ];
  return (
    <ol
      className="border-hairline mt-4 grid gap-x-8 gap-y-3 border-t pt-4 sm:grid-cols-3"
      title={prose(text)}
    >
      {steps.map(([head, body], i) => (
        <li key={head} className="flex gap-3">
          <span className="data text-cyan/80 pt-px text-[11px]">0{i + 1}</span>
          <span>
            <span className="text-ink-0 block text-xs font-medium">{head}</span>
            <span className="text-ink-2 mt-0.5 block text-[11px] leading-relaxed">{body}</span>
          </span>
        </li>
      ))}
    </ol>
  );
}

function CoastLedger({
  title,
  hint,
  stretches,
  picked,
  onPick,
}: {
  title: string;
  hint: string;
  stretches: Stretch[];
  picked: string | null;
  onPick: (key: string | null) => void;
}) {
  return (
    <div>
      <div className="border-hairline-strong flex items-baseline justify-between gap-3 border-b pb-1.5">
        <h3 className="text-ink-0 text-xs font-semibold tracking-wide uppercase">{title}</h3>
        <span className="text-ink-2 text-[11px]">
          {hint}
          {stretches.length ? (
            <span className="data">
              {' '}
              · {stretches.length} stretch{stretches.length === 1 ? '' : 'es'}
            </span>
          ) : null}
        </span>
      </div>
      {stretches.length ? (
        <ul>
          {stretches.map((stretch, index) => {
            const key = stretchKey(stretch, index);
            const active = picked === key;
            const many = stretch.harbours.length > 1;
            const range = many ? `${stretch.from} → ${stretch.to}` : stretch.from;
            return (
              <li key={key} className="border-hairline border-b">
                <button
                  type="button"
                  onClick={() => onPick(active ? null : key)}
                  aria-pressed={active}
                  title={stretch.sentence}
                  className={clsx(
                    'grid w-full grid-cols-[72px_minmax(0,1fr)_auto] items-baseline gap-x-4 py-2.5 text-left transition-colors',
                    active ? 'bg-abyss-1' : 'hover:bg-abyss-1/70',
                  )}
                >
                  <span className="flex items-center gap-2">
                    <span
                      className={clsx(
                        'inline-block h-3.5 w-0.5 shrink-0',
                        VERDICT_RAIL[stretch.verdict] ?? VERDICT_RAIL.UNVERIFIABLE,
                      )}
                      aria-hidden
                    />
                    <span
                      className={clsx(
                        'data text-[11px] font-medium',
                        VERDICT_TEXT[stretch.verdict] ?? VERDICT_TEXT.UNVERIFIABLE,
                      )}
                    >
                      {stretch.verdict}
                    </span>
                  </span>
                  <span
                    className={clsx(
                      'min-w-0 truncate text-[13px] leading-snug',
                      stretch.verdict === 'GO' ? 'text-ink-1' : 'text-ink-0 font-medium',
                    )}
                  >
                    {range}
                  </span>
                  <span className="data text-ink-2 text-[11px] whitespace-nowrap">
                    {stretch.harbours.length} harbour{stretch.harbours.length === 1 ? '' : 's'}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      ) : (
        <p className="text-ink-2 py-3 text-xs">No stretches on this coast for the current filter.</p>
      )}
    </div>
  );
}

function RowGroup({
  coastBreak,
  coast,
  colSpan,
  entry,
  codes,
  focus,
  on,
}: {
  coastBreak: boolean;
  coast: 'west' | 'east';
  colSpan: number;
  entry: BoardRow;
  codes: string[];
  focus: string;
  on: boolean;
}) {
  return (
    <>
      {coastBreak ? (
        <tr>
          <td
            colSpan={colSpan}
            className="label bg-abyss-0 text-ink-2 border-hairline border-b px-3 pt-3 pb-1"
          >
            {coast === 'west' ? 'West coast · Kutch to Kanyakumari' : 'East coast · Kanyakumari to the Sundarbans'}
          </td>
        </tr>
      ) : null}
      <tr
        className={clsx(
          'border-hairline border-b last:border-0',
          on ? 'bg-abyss-2' : 'hover:bg-abyss-1',
        )}
      >
        <td
          className={clsx(
            'px-3 py-1.5 whitespace-nowrap',
            on ? 'text-cyan' : 'text-ink-0',
          )}
        >
          {entry.name}
        </td>
        <td className="text-ink-2 px-3 py-1.5 whitespace-nowrap">{entry.state}</td>
        <td className="data text-ink-1 px-3 py-1.5 text-right">
          {entry.conditions.wave_m ?? '—'}
        </td>
        <td className="data text-ink-1 px-3 py-1.5 text-right">
          {entry.conditions.wind_kn ?? '—'}
        </td>
        {codes.map((code) => {
          const cell = entry.verdicts[code];
          const verdict = cell?.verdict ?? 'UNVERIFIABLE';
          return (
            <td
              key={code}
              className={clsx(
                'data px-2 py-1.5 text-center whitespace-nowrap',
                VERDICT_TEXT[verdict],
                code === focus && 'font-medium',
                code === focus && !on && 'bg-abyss-1/80',
                verdict === 'GO' && code !== focus && 'opacity-70',
              )}
              title={cell?.reason ?? verdict}
            >
              {verdict === 'UNVERIFIABLE' ? 'n/a' : verdict}
            </td>
          );
        })}
      </tr>
    </>
  );
}

function LoadingState({ elapsed }: { elapsed: number }) {
  return (
    <div className="mt-10">
      <div className="text-ink-1 flex items-center gap-2 text-xs">
        <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
        Sampling sea state at every harbour, then running the rule engine for each vessel class.
        {elapsed > 0 ? <span className="data text-ink-2">{elapsed}s</span> : null}
      </div>
      <div className="mt-8 grid gap-10 lg:grid-cols-2">
        {['West coast', 'East coast'].map((title) => (
          <div key={title}>
            <div className="border-hairline-strong text-ink-2 border-b pb-1.5 text-xs font-semibold tracking-wide uppercase">
              {title}
            </div>
            {Array.from({ length: 6 }).map((_, i) => (
              <div key={i} className="border-hairline flex items-center gap-4 border-b py-2.5">
                <span className="bg-abyss-2 h-3 w-14" />
                <span className="bg-abyss-2 h-3 flex-1" />
                <span className="bg-abyss-2 h-3 w-16" />
              </div>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}
