/**
 * The SVG filter definitions the treatments reference.
 *
 * Mounted once, invisible, at the root. It has to be in the document (not in a
 * detached fragment or a shadow root) because `filter: url(#id)` resolves
 * against the document, and it has to be `position: absolute` with zero size
 * rather than `display: none` — a `display: none` ancestor makes the filters
 * unresolvable in Chromium and the map simply renders unfiltered, silently.
 *
 * Each filter is built from three primitives:
 *
 * * `feColorMatrix` — a 5×4 matrix. `type="matrix"` for arbitrary channel mixing,
 *   `type="saturate" values="0"` for a proper luminance-weighted greyscale
 *   (0.2126 / 0.7152 / 0.0722), which is what a palette lookup needs as input.
 * * `feComponentTransfer` — a per-channel transfer curve. `type="table"` for a
 *   palette (linear interpolation between stops) and `type="discrete"` for hard
 *   banding, which is how the bathymetric treatment gets contour bands that are
 *   genuinely quantised rather than drawn on top.
 * * `feTurbulence` — band-limited noise for grain, composited in rather than
 *   overlaid as an image so it inherits the filter region and does not tile.
 */

import { BATHYMETRIC, INTENSIFIER, IRONBOW, transferTables } from '@/lib/treatments';

function Palette({
  id,
  stops,
  discrete = false,
  // Luminance first: a palette lookup is a function of brightness, so the input
  // has to be desaturated before the transfer curves are applied. Skipping this
  // makes the palette act per-channel and the result is just a colour cast.
  desaturate = true,
}: {
  id: string;
  stops: [number, number, number][];
  discrete?: boolean;
  desaturate?: boolean;
}) {
  const tables = transferTables(stops);
  const type = discrete ? 'discrete' : 'table';
  return (
    <filter id={id} colorInterpolationFilters="sRGB">
      {desaturate && <feColorMatrix type="saturate" values="0" result="luma" />}
      <feComponentTransfer in={desaturate ? 'luma' : 'SourceGraphic'}>
        <feFuncR type={type} tableValues={tables.r} />
        <feFuncG type={type} tableValues={tables.g} />
        <feFuncB type={type} tableValues={tables.b} />
      </feComponentTransfer>
    </filter>
  );
}

export function TreatmentFilters() {
  return (
    <svg
      aria-hidden
      width="0"
      height="0"
      // Not `display: none`: a hidden ancestor makes `filter: url(#id)`
      // unresolvable in Chromium and the map renders unfiltered with no error.
      style={{ position: 'absolute', width: 0, height: 0, overflow: 'hidden' }}
    >
      <defs>
        <Palette id="orca-thermal" stops={IRONBOW} />

        {/* Night vision: intensifier palette, then grain and a slight bloom.
            Grain is added at low amplitude and only in the green channel, which
            is what a monochrome tube actually does. */}
        <filter id="orca-nightvision" colorInterpolationFilters="sRGB">
          <feColorMatrix type="saturate" values="0" result="luma" />
          <feComponentTransfer in="luma" result="tinted">
            <feFuncR type="table" tableValues={transferTables(INTENSIFIER).r} />
            <feFuncG type="table" tableValues={transferTables(INTENSIFIER).g} />
            <feFuncB type="table" tableValues={transferTables(INTENSIFIER).b} />
          </feComponentTransfer>
          {/* Bloom: blur the bright end and screen it back, so highlights halo
              the way an intensifier's do rather than just being brighter. */}
          <feGaussianBlur in="tinted" stdDeviation="2.4" result="bloom" />
          <feComposite in="bloom" in2="tinted" operator="arithmetic" k2="0.35" k3="1" result="lit" />
          <feTurbulence
            type="fractalNoise"
            baseFrequency="0.9"
            numOctaves={2}
            seed={7}
            result="noise"
          />
          <feColorMatrix
            in="noise"
            type="matrix"
            values="0 0 0 0 0
                    0 0 0 0.22 0
                    0 0 0 0 0
                    0 0 0 0 1"
            result="grain"
          />
          <feBlend in="lit" in2="grain" mode="screen" />
        </filter>

        {/* Radar: hard phosphor green, crushed to a few levels so it reads as a
            PPI display rather than a green photograph. */}
        <filter id="orca-radar" colorInterpolationFilters="sRGB">
          <feColorMatrix type="saturate" values="0" result="luma" />
          <feComponentTransfer in="luma" result="crushed">
            {/* Eight levels, not five, and a peak well under full green. At five
                levels topping out at 0.92 the Bay of Bengal became one flat sheet
                of pure #00ff00 with every raster gradient destroyed — a PPI
                display shows returns of different strength, and a treatment that
                erases all of them is not showing the data at all. */}
            <feFuncR type="discrete" tableValues="0 0.01 0.02 0.04 0.06 0.09 0.12 0.16" />
            <feFuncG type="discrete" tableValues="0.02 0.10 0.19 0.29 0.39 0.50 0.60 0.70" />
            <feFuncB type="discrete" tableValues="0.01 0.05 0.09 0.14 0.19 0.25 0.31 0.38" />
          </feComponentTransfer>
          <feGaussianBlur in="crushed" stdDeviation="0.9" result="glow" />
          <feComposite in="glow" in2="crushed" operator="arithmetic" k2="0.34" k3="1" />
        </filter>

        {/* Bathymetric: discrete bands. The banding IS the treatment — it is a
            quantised transfer curve, not contour lines drawn over the top. */}
        <Palette id="orca-bathymetric" stops={BATHYMETRIC} discrete />

        {/* CRT: split the channels and offset two of them by a pixel, which is
            what channel misregistration on a shadow-mask tube looks like, then
            lift the black point so the glass never reaches true black. */}
        <filter id="orca-crt" colorInterpolationFilters="sRGB">
          <feColorMatrix
            type="matrix"
            values="1 0 0 0 0
                    0 0 0 0 0
                    0 0 0 0 0
                    0 0 0 1 0"
            result="red"
          />
          <feColorMatrix
            type="matrix"
            values="0 0 0 0 0
                    0 1 0 0 0
                    0 0 0 0 0
                    0 0 0 1 0"
            result="green"
          />
          <feColorMatrix
            type="matrix"
            values="0 0 0 0 0
                    0 0 0 0 0
                    0 0 1 0 0
                    0 0 0 1 0"
            result="blue"
          />
          <feOffset in="red" dx="-1.1" dy="0" result="redShift" />
          <feOffset in="blue" dx="1.1" dy="0" result="blueShift" />
          <feBlend in="redShift" in2="green" mode="screen" result="rg" />
          <feBlend in="rg" in2="blueShift" mode="screen" result="rgb" />
          <feComponentTransfer in="rgb">
            {/* Slope under 1 with an intercept: contrast down, black point up. */}
            <feFuncR type="linear" slope="0.94" intercept="0.045" />
            <feFuncG type="linear" slope="0.98" intercept="0.05" />
            <feFuncB type="linear" slope="0.9" intercept="0.06" />
          </feComponentTransfer>
        </filter>

        {/* Noir: luminance, then a hard S-curve via a table. Gamma alone lifts or
            crushes the whole range; a table can steepen the midtones while
            leaving both ends intact, which is what an S-curve is. */}
        <filter id="orca-noir" colorInterpolationFilters="sRGB">
          <feColorMatrix type="saturate" values="0" result="luma" />
          <feComponentTransfer in="luma" result="curved">
            <feFuncR type="table" tableValues="0 0.03 0.12 0.34 0.66 0.88 0.97 1" />
            <feFuncG type="table" tableValues="0 0.03 0.12 0.34 0.66 0.88 0.97 1" />
            <feFuncB type="table" tableValues="0 0.035 0.13 0.35 0.67 0.89 0.975 1" />
          </feComponentTransfer>
          <feTurbulence
            type="fractalNoise"
            baseFrequency="1.4"
            numOctaves={1}
            seed={19}
            result="noise"
          />
          <feColorMatrix
            in="noise"
            type="matrix"
            values="0 0 0 0 0.5
                    0 0 0 0 0.5
                    0 0 0 0 0.5
                    0 0 0 0.09 0"
            result="grain"
          />
          <feBlend in="curved" in2="grain" mode="overlay" />
        </filter>
      </defs>
    </svg>
  );
}
