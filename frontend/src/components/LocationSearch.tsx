/**
 * Prototype location search — OpenStreetMap / Nominatim via ORCA's thin proxy.
 * Only answers "where is this place?"; existing query()/map-click owns ORCA safety.
 */

import { useEffect, useId, useRef, useState } from 'react';
import { Anchor, Loader2, MapPin, Search } from 'lucide-react';
import { api } from '@/lib/api';
import type { Landmark } from '@/lib/types';

export interface LocationPick {
  name: string;
  address: string;
  lat: number;
  lon: number;
  provider: string;
}

type Props = {
  landmarks: Landmark[];
  selectedLabel?: string | null;
  onPick: (place: LocationPick) => void;
};

const DEBOUNCE_MS = 350;

export function LocationSearch({ landmarks, selectedLabel, onPick }: Props) {
  const listId = useId();
  const rootRef = useRef<HTMLDivElement>(null);
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [results, setResults] = useState<LocationPick[]>([]);
  const [usingFallback, setUsingFallback] = useState(false);

  useEffect(() => {
    const trimmed = query.trim();
    if (trimmed.length < 2) {
      setResults([]);
      setError(null);
      setLoading(false);
      setUsingFallback(false);
      return;
    }

    let cancelled = false;
    const timer = window.setTimeout(() => {
      setLoading(true);
      setError(null);
      void (async () => {
        try {
          const response = await api.geocodeSearch(trimmed, 5);
          if (cancelled) return;
          if (response.ok && response.results.length > 0) {
            setResults(response.results);
            setUsingFallback(false);
            setError(null);
            return;
          }
          // Fallback: filter curated ORCA landmarks
          const needle = trimmed.toLowerCase();
          const local = landmarks
            .filter(
              (place) =>
                place.label.toLowerCase().includes(needle) ||
                place.key.toLowerCase().includes(needle),
            )
            .slice(0, 5)
            .map(
              (place): LocationPick => ({
                name: place.label,
                address: 'ORCA curated harbour',
                lat: place.lat,
                lon: place.lon,
                provider: 'ORCA landmarks',
              }),
            );
          setResults(local);
          setUsingFallback(true);
          setError(
            response.ok
              ? local.length
                ? null
                : 'No locations found'
              : local.length
                ? 'Search unavailable — showing ORCA harbours'
                : response.error ?? 'Location search unavailable',
          );
        } catch {
          if (cancelled) return;
          const needle = trimmed.toLowerCase();
          const local = landmarks
            .filter((place) => place.label.toLowerCase().includes(needle))
            .slice(0, 5)
            .map(
              (place): LocationPick => ({
                name: place.label,
                address: 'ORCA curated harbour',
                lat: place.lat,
                lon: place.lon,
                provider: 'ORCA landmarks',
              }),
            );
          setResults(local);
          setUsingFallback(true);
          setError(
            local.length
              ? 'Search unavailable — showing ORCA harbours'
              : 'Location search unavailable',
          );
        } finally {
          if (!cancelled) setLoading(false);
        }
      })();
    }, DEBOUNCE_MS);

    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [query, landmarks]);

  useEffect(() => {
    const onDoc = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, []);

  const showMenu = open && query.trim().length >= 2;

  return (
    <div ref={rootRef} className="p-2.5">
      <div className="relative">
        <Search
          className="text-ink-3 pointer-events-none absolute top-1/2 left-2.5 h-3.5 w-3.5 -translate-y-1/2"
          aria-hidden
        />
        <input
          type="search"
          value={query}
          onChange={(event) => {
            setQuery(event.target.value);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          placeholder="Search harbour, port, city…"
          className="border-hairline bg-abyss-2 text-ink-0 placeholder:text-ink-3 focus:border-cyan/50 w-full rounded border py-2 pr-8 pl-8 text-xs outline-none"
          aria-label="Search location"
          aria-autocomplete="list"
          aria-controls={listId}
          aria-expanded={showMenu}
          autoComplete="off"
        />
        {loading && (
          <Loader2
            className="text-cyan absolute top-1/2 right-2.5 h-3.5 w-3.5 -translate-y-1/2 animate-spin"
            aria-hidden
          />
        )}
      </div>

      {showMenu && (
        <ul
          id={listId}
          role="listbox"
          className="border-hairline bg-abyss-1 mt-1.5 max-h-48 overflow-y-auto rounded border"
        >
          {loading && results.length === 0 && (
            <li className="text-ink-3 px-2.5 py-2 text-2xs">Searching…</li>
          )}
          {!loading && results.length === 0 && (
            <li className="text-ink-3 px-2.5 py-2 text-2xs">
              {error ?? 'No locations found'}
            </li>
          )}
          {results.map((place) => (
            <li key={`${place.lat},${place.lon},${place.name}`} role="option">
              <button
                type="button"
                className="hover:bg-cyan/10 flex w-full flex-col gap-0.5 px-2.5 py-2 text-left transition-colors"
                onClick={() => {
                  onPick(place);
                  setQuery('');
                  setOpen(false);
                  setResults([]);
                }}
              >
                <span className="text-ink-0 truncate text-xs font-medium">{place.name}</span>
                {place.address && (
                  <span className="text-ink-3 truncate text-2xs">{place.address}</span>
                )}
              </button>
            </li>
          ))}
          {usingFallback && results.length > 0 && (
            <li className="text-ink-3 border-hairline border-t px-2.5 py-1.5 text-2xs">
              {error ?? 'Curated ORCA harbours'}
            </li>
          )}
        </ul>
      )}

      {selectedLabel && (
        <div className="border-hairline mt-2 flex items-start gap-2 border-t pt-2">
          <MapPin className="text-cyan mt-0.5 h-3 w-3 shrink-0" aria-hidden />
          <div className="min-w-0">
            <div className="text-ink-3 text-2xs">Selected</div>
            <div className="text-ink-0 truncate text-xs leading-snug">{selectedLabel}</div>
          </div>
        </div>
      )}

      {!showMenu && landmarks.length > 0 && (
        <div className="mt-2">
          <div className="label text-ink-3 mb-1 px-0.5 text-2xs">Quick harbours</div>
          <div className="max-h-28 overflow-y-auto">
            {landmarks.slice(0, 8).map((place) => (
              <button
                key={place.key}
                type="button"
                onClick={() =>
                  onPick({
                    name: place.label,
                    address: 'ORCA curated harbour',
                    lat: place.lat,
                    lon: place.lon,
                    provider: 'ORCA landmarks',
                  })
                }
                className="text-ink-1 hover:bg-abyss-2 flex w-full items-center gap-2 rounded px-1.5 py-1 text-left text-2xs transition-colors"
              >
                <Anchor className="h-3 w-3 shrink-0 opacity-50" aria-hidden />
                <span className="truncate">{place.label}</span>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
