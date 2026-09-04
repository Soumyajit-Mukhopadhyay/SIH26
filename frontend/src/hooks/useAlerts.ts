/**
 * The alert stream: the one channel where ORCA speaks first.
 *
 * `EventSource` is the right tool here, unlike the agent stream — this is a plain
 * GET with no body, and `EventSource` reconnects on its own, which for a channel
 * that must not miss anything is worth more than the AbortController that a
 * hand-rolled `fetch` parser buys.
 *
 * Reconnection is why the server replays its held alerts on connect: a client
 * that drops for thirty seconds and comes back would otherwise silently miss the
 * alert that was the entire reason to be watching. The replay is deduplicated by
 * alert id here, so a reconnect does not double up what is already on screen.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

export type AlertSeverity = 'info' | 'advisory' | 'warning' | 'critical';

export interface OrcaAlert {
  id: string;
  at: string;
  watch_id: string;
  severity: AlertSeverity;
  kind: string;
  headline: string;
  detail: string;
  lat: number;
  lon: number;
  label: string | null;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  acknowledged: boolean;
}

export interface MonitorStatus {
  monitor_version: string;
  running: boolean;
  poll_seconds: number;
  polls: number;
  watches: number;
  alerts_held: number;
  policy: string;
  delivery: string;
  last_error: string | null;
}

/** Worst first. Explicit, because sorting severities as strings puts
 *  'advisory' above 'critical' and inverts the entire rail. */
const SEVERITY_RANK: Record<AlertSeverity, number> = {
  critical: 0,
  warning: 1,
  advisory: 2,
  info: 3,
};

export function useAlerts() {
  const [alerts, setAlerts] = useState<OrcaAlert[]>([]);
  const [status, setStatus] = useState<MonitorStatus | null>(null);
  const [connected, setConnected] = useState(false);
  /** Alerts that arrived while the rail was closed, so it can badge itself. */
  const [unseen, setUnseen] = useState(0);
  const seen = useRef<Set<string>>(new Set());

  const absorb = useCallback((incoming: OrcaAlert[], { live }: { live: boolean }) => {
    const fresh = incoming.filter((alert) => !seen.current.has(alert.id));
    if (fresh.length === 0) return;
    for (const alert of fresh) seen.current.add(alert.id);
    setAlerts((previous) =>
      [...fresh, ...previous]
        .sort((a, b) => {
          const bySeverity = SEVERITY_RANK[a.severity] - SEVERITY_RANK[b.severity];
          return bySeverity !== 0 ? bySeverity : b.at.localeCompare(a.at);
        })
        .slice(0, 60),
    );
    if (live) setUnseen((count) => count + fresh.length);
  }, []);

  useEffect(() => {
    const source = new EventSource('/api/alerts/stream');

    source.addEventListener('open', () => setConnected(true));
    source.addEventListener('error', () => setConnected(false));

    source.addEventListener('alerts_open', (event) => {
      try {
        const payload = JSON.parse((event as MessageEvent).data) as {
          status: MonitorStatus;
          backlog: OrcaAlert[];
        };
        setStatus(payload.status);
        setConnected(true);
        // Backlog, not live: a reconnect must not re-badge alerts the user has
        // already read.
        absorb(payload.backlog ?? [], { live: false });
      } catch {
        /* a malformed frame must not kill the stream */
      }
    });

    source.addEventListener('alert', (event) => {
      try {
        absorb([JSON.parse((event as MessageEvent).data) as OrcaAlert], { live: true });
      } catch {
        /* likewise */
      }
    });

    return () => source.close();
  }, [absorb]);

  const markAllSeen = useCallback(() => setUnseen(0), []);

  /**
   * Re-read the monitor's status.
   *
   * `alerts_open` delivers the status once, at connect — which is before any
   * watch exists. Leaving it at that snapshot left the UI believing there were
   * zero watches for the rest of the session, and the "check now" button stayed
   * disabled after the user had explicitly asked for a position to be watched.
   */
  const refreshStatus = useCallback(async () => {
    try {
      const response = await fetch('/api/alerts/watches');
      if (!response.ok) return;
      const payload = (await response.json()) as { status: MonitorStatus };
      setStatus(payload.status);
    } catch {
      /* the rail degrades to its last known status rather than breaking */
    }
  }, []);

  const acknowledge = useCallback(async (id: string) => {
    setAlerts((previous) =>
      previous.map((alert) => (alert.id === id ? { ...alert, acknowledged: true } : alert)),
    );
    try {
      await fetch(`/api/alerts/${id}/acknowledge`, { method: 'POST' });
    } catch {
      /* the local state is what the user sees; a failed POST costs nothing here */
    }
  }, []);

  /** Register a position to be monitored. */
  const watch = useCallback(
    async (args: {
      lat: number;
      lon: number;
      boatClassCode?: string | null;
      loaM?: number | null;
      label?: string | null;
    }) => {
      const response = await fetch('/api/alerts/watch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          lat: args.lat,
          lon: args.lon,
          ...(args.loaM != null ? { loa_m: args.loaM } : {}),
          ...(args.boatClassCode != null ? { boat_class_code: args.boatClassCode } : {}),
          label: args.label,
        }),
      });
      if (!response.ok) throw new Error(`watch failed: HTTP ${response.status}`);
      const payload = (await response.json()) as { watch: { id: string } };
      void refreshStatus();
      return payload.watch.id;
    },
    [refreshStatus],
  );

  const unwatch = useCallback(
    async (id: string) => {
      await fetch(`/api/alerts/watch/${id}`, { method: 'DELETE' });
      void refreshStatus();
    },
    [refreshStatus],
  );

  /**
   * Keep a watch's position and vessel in step with the console.
   *
   * Without this a watch keeps judging the boat it was registered with. A skipper
   * who switches from a large mechanised trawler to a small motorised boat would go
   * on getting alerts computed against the trawler's limit — advice about a
   * boat they are no longer in.
   */
  const retarget = useCallback(
    async (
      id: string,
      args: {
        lat?: number;
        lon?: number;
        loaM?: number | null;
        boatClassCode?: string | null;
      },
    ) => {
      await fetch(`/api/alerts/watch/${id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          lat: args.lat,
          lon: args.lon,
          ...(args.loaM != null ? { loa_m: args.loaM } : {}),
          ...(args.boatClassCode != null ? { boat_class_code: args.boatClassCode } : {}),
        }),
      });
    },
    [],
  );

  /**
   * Force one monitor cycle.
   *
   * The poll loop is on a two-minute timer because the upstream models are
   * hourly, and a demo cannot stand around for two minutes waiting for the
   * feature to prove itself. This hits the same code path the timer does.
   */
  const checkNow = useCallback(async () => {
    const response = await fetch('/api/alerts/check', { method: 'POST' });
    if (!response.ok) throw new Error(`check failed: HTTP ${response.status}`);
    const payload = (await response.json()) as {
      checked: number;
      raised: OrcaAlert[];
      note: string | null;
    };
    // The forced check runs the same code path as the timer, which pushes through
    // the stream — but absorb it here too so a dropped stream still shows it.
    absorb(payload.raised ?? [], { live: true });
    void refreshStatus();
    return payload;
  }, [absorb, refreshStatus]);

  return {
    alerts,
    status,
    connected,
    unseen,
    markAllSeen,
    acknowledge,
    watch,
    unwatch,
    retarget,
    checkNow,
  };
}
