/**
 * Load raster PNGs into `ImageBitmap`s ourselves, rather than handing deck.gl a
 * URL string and hoping.
 *
 * Why not the built-in async prop: passing `image: "/api/rasters/sst/latest.png"`
 * to a `BitmapLayer` produced no network request at all and no error — the layer
 * was constructed and handed to deck (confirmed by logging the layer ids), and
 * deck silently never resolved it. Rather than keep guessing at deck's async
 * prop machinery, we fetch the image, decode it, and pass a decoded bitmap,
 * which `BitmapLayer` accepts directly.
 *
 * That swap is worth having on its own merits:
 *
 * - a failed raster becomes a visible error instead of an empty map;
 * - we know when an image is still decoding, so the layer rail can say so;
 * - the browser cache is bypassed deliberately per ingest, rather than us hoping
 *   a query string is enough.
 */

import { useEffect, useRef, useState } from 'react';

export interface RasterImages {
  images: Record<string, ImageBitmap>;
  loading: Set<string>;
  errors: Record<string, string>;
}

/**
 * Decode the PNGs for `wanted`, keyed by variable name.
 *
 * `epoch` busts both the browser cache and our own memo when the ingest job
 * rewrites `latest.png` under an unchanged URL.
 */
export function useRasterImages(
  wanted: string[],
  urls: Record<string, string>,
  epoch: number,
): RasterImages {
  const [images, setImages] = useState<Record<string, ImageBitmap>>({});
  const [loading, setLoading] = useState<Set<string>>(new Set());
  const [errors, setErrors] = useState<Record<string, string>>({});

  // Which (variable, epoch) pairs we have already started, so a re-render does
  // not refetch a 300 KB image on every state change.
  const started = useRef<Set<string>>(new Set());

  const key = `${wanted.slice().sort().join(',')}|${epoch}`;

  useEffect(() => {
    let cancelled = false;

    for (const variable of wanted) {
      const url = urls[variable];
      if (!url) continue;
      const token = `${variable}@${epoch}`;
      if (started.current.has(token)) continue;
      started.current.add(token);

      setLoading((prev) => new Set(prev).add(variable));

      void (async () => {
        try {
          const response = await fetch(`${url}?v=${epoch}`, { cache: 'no-cache' });
          if (!response.ok) throw new Error(`HTTP ${response.status}`);
          const blob = await response.blob();
          const bitmap = await createImageBitmap(blob);
          if (cancelled) {
            bitmap.close();
            return;
          }
          setImages((prev) => {
            // Release the previous decode rather than leaking it: these are
            // 800x500 RGBA bitmaps and an ingest every few minutes would add up.
            prev[variable]?.close();
            return { ...prev, [variable]: bitmap };
          });
          setErrors((prev) => {
            if (!(variable in prev)) return prev;
            const next = { ...prev };
            delete next[variable];
            return next;
          });
        } catch (cause) {
          if (cancelled) return;
          setErrors((prev) => ({
            ...prev,
            [variable]: cause instanceof Error ? cause.message : 'failed to decode',
          }));
        } finally {
          if (!cancelled) {
            setLoading((prev) => {
              const next = new Set(prev);
              next.delete(variable);
              return next;
            });
          }
        }
      })();
    }

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  return { images, loading, errors };
}
