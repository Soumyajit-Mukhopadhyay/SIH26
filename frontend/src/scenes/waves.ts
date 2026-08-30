/**
 * The sea surface, derived from the forecast rather than art-directed.
 *
 * Every number that shapes the water comes from the point forecast: significant
 * wave height sets the amplitude, peak period sets the wavelength through the
 * deep-water dispersion relation, and wave direction sets which way it travels.
 * Wind speed adds short chop on top. Nothing here is tuned to look nice — the
 * whole reason to render the sea in 3D is that an 8.2 m boat in a 2.74 m sea is
 * a fact you can *see* in a way that "2.74 m" is not.
 *
 * ## The physics that matters
 *
 * **Dispersion.** In deep water a surface gravity wave of period T has
 * wavelength L = gT²/2π, so wavenumber k = ω²/g with ω = 2π/T. An 8.7 s swell is
 * therefore ~118 m long. This is why the scene has to be hundreds of metres
 * across: at a domain of 50 m you would be looking at a fifth of one wave and it
 * would read as a flat tilting plane. It is also why the boat looks small, which
 * is the honest picture.
 *
 * **Amplitude.** Significant wave height Hs is the mean of the highest third,
 * conventionally 4√m₀. Rendering a wave *train*, not a statistic, the useful
 * reading is a crest-to-trough height of Hs, so total amplitude is Hs/2 split
 * across components.
 *
 * **Steepness.** A Gerstner wave with steepness ka = 1 has a cusped crest; above
 * that the parameterisation folds through itself and the mesh turns inside out.
 * Real waves break near that limit too (Stokes' 120° crest), so the clamp below
 * is a physical bound rather than a graphics hack — but it is a clamp, and when
 * it engages the rendered sea is gentler than the forecast. `steepnessClamped`
 * reports that, because a visual that quietly understates a dangerous sea is the
 * one failure this scene must not have.
 */

const G = 9.80665;

/** One Gerstner component: a direction, a wavenumber and an amplitude. */
export interface WaveComponent {
  /** Direction of travel, radians, 0 = +x, measured in scene space. */
  angle: number;
  /** Wavenumber, rad/m. */
  k: number;
  /** Amplitude, m (crest above mean = a). */
  amplitude: number;
  /** Angular frequency, rad/s, from the deep-water dispersion relation. */
  omega: number;
}

export interface SeaState {
  components: WaveComponent[];
  /** Crest-to-trough height actually rendered, m. */
  renderedHeight: number;
  /** Hs the forecast reported, m. */
  forecastHeight: number;
  /** Wavelength of the dominant swell, m. */
  wavelength: number;
  /** True when steepness had to be reduced, so the render is gentler than the sea. */
  steepnessClamped: boolean;
  /** Half-width of the domain needed to show ~3 wavelengths, m. */
  domain: number;
}

/**
 * Meteorological direction (degrees the waves come FROM) to scene-space travel
 * direction (radians, direction of travel).
 *
 * The convention trap that has bitten this project before: wave and wind
 * directions are reported as where the energy comes FROM, currents as where the
 * water flows TO. Getting this backwards produces a sea that travels the wrong
 * way past a boat, which nobody notices in a screenshot and every fisherman
 * notices immediately.
 */
export function travelAngle(fromDegrees: number): number {
  return ((fromDegrees + 180) * Math.PI) / 180;
}

/** Deep-water wavenumber for a wave of period T. */
export function wavenumber(periodS: number): number {
  const omega = (2 * Math.PI) / Math.max(1, periodS);
  return (omega * omega) / G;
}

/** Deep-water wavelength for a wave of period T. */
export function wavelength(periodS: number): number {
  return (G * periodS * periodS) / (2 * Math.PI);
}

/**
 * Build the component set for a forecast.
 *
 * Three swell components spread ±18° around the reported direction rather than
 * one: a single sinusoid reads as corrugated iron, and directional spreading is
 * what real sea has. Two short wind-chop components ride on top, their period
 * scaled from wind speed by the fully-developed-sea approximation Tp ≈ 0.7·U
 * (Pierson–Moskowitz), which is crude and clearly labelled as such — chop is
 * texture here, not a measurement.
 */
export function seaStateFrom({
  waveHeightM,
  periodS,
  directionFromDeg,
  windSpeedKn,
}: {
  waveHeightM: number;
  periodS: number;
  directionFromDeg: number;
  windSpeedKn: number;
}): SeaState {
  const hs = Math.max(0.05, waveHeightM);
  const tp = Math.max(2, periodS);
  const heading = travelAngle(directionFromDeg);
  const kSwell = wavenumber(tp);
  const lambda = wavelength(tp);

  // Wind chop: a fully developed sea under U m/s peaks near 0.7·U seconds. Short
  // and low, because its job is to break up the specular highlight.
  const windMs = Math.max(0, windSpeedKn) * 0.514444;
  const chopPeriod = Math.max(1.4, 0.7 * windMs * 0.35);
  const kChop = wavenumber(chopPeriod);
  const chopAmplitude = Math.min(0.18, 0.006 * windMs * windMs);

  // Amplitudes sum to Hs/2: crest-to-trough of the train is then Hs.
  const swellTotal = hs / 2;
  const spread = [
    { weight: 0.55, offset: 0 },
    { weight: 0.28, offset: 0.31 }, // ~18°
    { weight: 0.17, offset: -0.24 },
  ];

  let components: WaveComponent[] = [
    ...spread.map(({ weight, offset }) => ({
      angle: heading + offset,
      k: kSwell * (1 + offset * 0.35),
      amplitude: swellTotal * weight,
      omega: 0,
    })),
    { angle: heading + 0.9, k: kChop, amplitude: chopAmplitude, omega: 0 },
    { angle: heading - 1.15, k: kChop * 1.7, amplitude: chopAmplitude * 0.6, omega: 0 },
  ];

  // Total steepness Σkᵢaᵢ must stay under 1 or the surface self-intersects.
  const steepness = components.reduce((sum, c) => sum + c.k * c.amplitude, 0);
  const ceiling = 0.85;
  const clamped = steepness > ceiling;
  if (clamped) {
    const scale = ceiling / steepness;
    components = components.map((c) => ({ ...c, amplitude: c.amplitude * scale }));
  }

  // ω from k, not from T, so a scaled component still travels at its own phase
  // speed and the train does not shear.
  components = components.map((c) => ({ ...c, omega: Math.sqrt(G * c.k) }));

  const rendered = components.reduce((sum, c) => sum + c.amplitude, 0) * 2;

  return {
    components,
    renderedHeight: rendered,
    forecastHeight: hs,
    wavelength: lambda,
    steepnessClamped: clamped,
    // Three dominant wavelengths across the view, floored so a 3 s wind sea does
    // not put the camera inside a puddle.
    domain: Math.max(120, Math.min(900, lambda * 1.6)),
  };
}

/**
 * Surface height and slope at a point, on the CPU.
 *
 * This must agree with the vertex shader in `SeaState.tsx`, and the duplication
 * is deliberate: the boat has to sit on the water, and reading vertex positions
 * back from the GPU to find out where the water is would cost a stall every
 * frame. Both implementations evaluate the same closed form, so the risk is a
 * divergent edit rather than a divergent result — hence this note.
 *
 * Returns the Gerstner displacement, which moves points horizontally as well as
 * vertically. The horizontal part is why crests look sharp and troughs broad.
 */
export function sampleSurface(
  components: WaveComponent[],
  x: number,
  z: number,
  time: number,
): { height: number; slopeX: number; slopeZ: number } {
  let height = 0;
  let slopeX = 0;
  let slopeZ = 0;

  for (const c of components) {
    const dirX = Math.cos(c.angle);
    const dirZ = Math.sin(c.angle);
    const phase = c.k * (dirX * x + dirZ * z) - c.omega * time;

    height += c.amplitude * Math.sin(phase);
    // ∂η/∂x and ∂η/∂z of the vertical displacement: what the hull tilts to.
    const gradient = c.k * c.amplitude * Math.cos(phase);
    slopeX += dirX * gradient;
    slopeZ += dirZ * gradient;
  }

  return { height, slopeX, slopeZ };
}
