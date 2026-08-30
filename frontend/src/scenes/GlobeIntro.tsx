/**
 * The opening shot: NASA's earth, then a descent into the Bay of Bengal.
 *
 * It is six seconds long and it does one job — establish that ORCA's subject is
 * a real ocean around a real coastline, before the console fills with numbers.
 * The globe is textured with NASA GIBS Blue Marble (shaded relief and
 * bathymetry) served through ORCA's own `/imagery/earth` proxy, so the very
 * first pixel on screen has a provenance too.
 *
 * Things it deliberately does NOT do:
 *
 * * **Block.** The map mounts underneath and finishes loading during the shot,
 *   so the intro is spent time rather than wasted time. It fades out; nothing
 *   waits on it.
 * * **Repeat.** Shown once per browser session. A cinematic you have to sit
 *   through on every reload is a cinematic that gets skipped, and during a live
 *   demo the reload is exactly when you cannot afford six seconds.
 * * **Insist.** Skip is always available, and pressing Escape or clicking
 *   anywhere ends it immediately.
 *
 * If the texture fails to load — no network at the venue, a cold cache — the
 * scene runs with a plain bathymetric gradient instead of hanging on the fetch.
 * A globe that renders is worth more than a globe that is accurate about its
 * albedo.
 */

import React, { Suspense, useEffect, useMemo, useRef, useState } from 'react';
import { Canvas, useFrame, useLoader, useThree } from '@react-three/fiber';
import * as THREE from 'three';

/** Kasimedu, Chennai — the demo's home port. */
const TARGET = { lat: 13.115, lon: 80.302 };

/** Earth radius in scene units. Everything else is expressed as a multiple. */
const R = 1;

/** How long the shot lasts, in seconds. Past ~7 s a viewer starts waiting. */
const DURATION = 6.2;

const SESSION_KEY = 'orca.intro.seen';

/**
 * Geographic to Cartesian, in the frame three.js's own sphere UVs define.
 *
 * This has to match `SphereGeometry` exactly or the camera flies to the wrong
 * ocean — which is what happened first: an offset guessed at from memory put the
 * arrival over the western Pacific, and it looks like a bug in the coordinates
 * rather than in the mapping.
 *
 * `SphereGeometry` lays out u ∈ [0,1] over φ ∈ [0,2π] and v ∈ [0,1] over the
 * polar angle from the north pole, with
 *
 *     x = −r·cos(φ)·sin(θ),  y = r·cos(θ),  z = r·sin(φ)·sin(θ)
 *
 * An equirectangular texture puts longitude −180° at u = 0, so φ = (λ+180)°, and
 * latitude +90° at v = 1, so θ = (90−lat)°.
 */
function toVector(lat: number, lon: number, radius: number): THREE.Vector3 {
  const theta = ((90 - lat) * Math.PI) / 180;
  const phi = ((lon + 180) * Math.PI) / 180;
  return new THREE.Vector3(
    -radius * Math.cos(phi) * Math.sin(theta),
    radius * Math.cos(theta),
    radius * Math.sin(phi) * Math.sin(theta),
  );
}

/** Smooth, slow at both ends. The descent should decelerate into the coast. */
function easeInOutCubic(t: number): number {
  return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
}

function Earth({
  dayUrl,
  nightUrl,
  onReady,
}: {
  dayUrl: string;
  nightUrl: string;
  onReady: () => void;
}) {
  const [day, night] = useLoader(THREE.TextureLoader, [dayUrl, nightUrl]);

  // The flight holds until this fires. Starting the descent over an untextured
  // sphere spends the most valuable second of the shot on a grey ball.
  useEffect(onReady, [onReady]);

  const material = useMemo(() => {
    for (const map of [day, night]) {
      map.colorSpace = THREE.SRGBColorSpace;
      map.anisotropy = 4;
    }
    return new THREE.ShaderMaterial({
      uniforms: {
        uDay: { value: day },
        uNight: { value: night },
        // Noon over the Indian EEZ: the region this shot flies into should be
        // the lit one, and the terminator then falls usefully across the Pacific.
        uSun: { value: toVector(14, 76, 1).normalize() },
      },
      vertexShader: /* glsl */ `
        varying vec2 vUv;
        varying vec3 vNormal;
        void main() {
          vUv = uv;
          vNormal = normalize(mat3(modelMatrix) * normal);
          gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
        }
      `,
      fragmentShader: /* glsl */ `
        uniform sampler2D uDay;
        uniform sampler2D uNight;
        uniform vec3 uSun;
        varying vec2 vUv;
        varying vec3 vNormal;

        void main() {
          float lambert = dot(normalize(vNormal), normalize(uSun));
          // A wide terminator: a hard day/night edge on a 4096-wide texture
          // aliases into a visible staircase across the ocean.
          float daylight = smoothstep(-0.22, 0.30, lambert);

          vec3 lit = texture2D(uDay, vUv).rgb * (0.45 + 0.85 * max(0.0, lambert));
          // City lights only where it is actually dark, and dimmed: at full
          // strength they bloom over the coastline the shot is flying toward.
          vec3 dark = texture2D(uNight, vUv).rgb * 1.5 + vec3(0.012, 0.026, 0.045);

          vec3 colour = mix(dark, lit, daylight);

          // Encode to sRGB by hand. A raw ShaderMaterial does NOT get three.js's
          // automatic output-colour-space conversion, so a texture flagged sRGB
          // is decoded to linear on sampling and then written out linear —
          // roughly a factor of two dark, which is exactly how the first version
          // looked. The decode is worth keeping (the lighting maths wants linear
          // values); it is the encode on the way out that was missing.
          gl_FragColor = vec4(pow(colour, vec3(1.0 / 2.2)), 1.0);
        }
      `,
    });
  }, [day, night]);

  useEffect(() => () => material.dispose(), [material]);

  return (
    <mesh material={material}>
      <sphereGeometry args={[R, 96, 64]} />
    </mesh>
  );
}

/** The fallback globe: no imagery, and honest about being a gradient. */
function PlainEarth() {
  return (
    <mesh>
      <sphereGeometry args={[R, 64, 48]} />
      <meshStandardMaterial color="#0b3550" roughness={0.85} metalness={0.05} />
    </mesh>
  );
}

/**
 * Atmosphere: a shell slightly larger than the planet, drawn from the inside.
 *
 * Rendering the back faces with additive blending is what produces a rim that
 * brightens toward the limb, which is the single cue that makes a textured
 * sphere read as a planet rather than as a ball.
 */
function Atmosphere() {
  const material = useMemo(
    () =>
      new THREE.ShaderMaterial({
        side: THREE.BackSide,
        blending: THREE.AdditiveBlending,
        transparent: true,
        depthWrite: false,
        vertexShader: /* glsl */ `
          varying vec3 vNormal;
          varying vec3 vView;
          void main() {
            vNormal = normalize(mat3(modelMatrix) * normal);
            vec4 world = modelMatrix * vec4(position, 1.0);
            vView = normalize(cameraPosition - world.xyz);
            gl_Position = projectionMatrix * viewMatrix * world;
          }
        `,
        fragmentShader: /* glsl */ `
          varying vec3 vNormal;
          varying vec3 vView;
          void main() {
            // The shell is drawn from the INSIDE, so the fragments that survive
            // the depth test are the annulus outside the planet's silhouette.
            // Across that annulus dot(normal, view) runs from about 0 at the
            // shell's own limb to increasingly negative toward the planet's edge
            // — so this expression is faintest at the outside and brightest
            // where it meets the planet, which is how a limb actually looks.
            //
            // The first version used 1 - abs(dot(normal, view)), which peaks at
            // the shell's own limb instead and drew a hard bright ring floating
            // clear of the planet: two concentric circles, not an atmosphere.
            float rim = pow(clamp(0.55 - dot(vNormal, vView), 0.0, 1.0), 3.0);
            gl_FragColor = vec4(vec3(0.26, 0.56, 0.95) * rim * 2.6, rim * 1.6);
          }
        `,
      }),
    [],
  );
  useEffect(() => () => material.dispose(), [material]);
  return (
    <mesh material={material} scale={1.11}>
      <sphereGeometry args={[R, 64, 48]} />
    </mesh>
  );
}

/** Stars: a fixed field, so the globe's rotation is legible against something. */
function Stars({ count = 1400 }: { count?: number }) {
  const geometry = useMemo(() => {
    const positions = new Float32Array(count * 3);
    for (let i = 0; i < count; i += 1) {
      // Rejection-free uniform sphere sampling: acos of a uniform gives an even
      // distribution, where a uniform polar angle would bunch at the poles.
      const u = Math.random() * 2 - 1;
      const theta = Math.random() * Math.PI * 2;
      const r = 40 + Math.random() * 30;
      const s = Math.sqrt(1 - u * u);
      positions.set([r * s * Math.cos(theta), r * u, r * s * Math.sin(theta)], i * 3);
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    return g;
  }, [count]);

  useEffect(() => () => geometry.dispose(), [geometry]);

  return (
    <points geometry={geometry}>
      <pointsMaterial size={0.22} color="#cfe0f0" sizeAttenuation transparent opacity={0.75} />
    </points>
  );
}

/**
 * The move.
 *
 * Three beats: hold wide with the planet turning, swing round to put the target
 * on the limb, then descend. The descent is the part that has to decelerate —
 * arriving at constant speed reads as a jump cut.
 */
function Flight({ armed, onDone }: { armed: boolean; onDone: () => void }) {
  const { camera } = useThree();
  const start = useRef<number | null>(null);
  const finished = useRef(false);

  const target = useMemo(() => toVector(TARGET.lat, TARGET.lon, R), []);
  // Approach from slightly west and above, so India rotates into frame rather
  // than being there from the first frame.
  const from = useMemo(() => toVector(TARGET.lat + 26, TARGET.lon - 58, R * 5.6), []);
  // 1.55 R, not 1.26: closer than this and the planet's curvature and its
  // atmosphere both leave the frame, so the shot ends on a flat satellite image
  // and the hand-off to the map loses the one thing the globe was there for.
  const to = useMemo(() => target.clone().multiplyScalar(1.55), [target]);

  useFrame((state) => {
    // Hold the wide shot until the imagery is up. The globe still turns under
    // the hold, so the pause does not read as a freeze.
    if (!armed) {
      const drift = state.clock.elapsedTime * 0.06;
      const direction = from.clone().normalize().applyAxisAngle(new THREE.Vector3(0, 1, 0), drift);
      camera.position.copy(direction.multiplyScalar(from.length()));
      camera.lookAt(0, 0, 0);
      return;
    }
    if (start.current === null) start.current = state.clock.elapsedTime;
    const elapsed = state.clock.elapsedTime - start.current;
    const t = Math.min(1, elapsed / DURATION);

    // Great-circle interpolation of the camera's direction, with the radius
    // eased separately. Interpolating the position vector directly cuts a chord
    // through the planet and the camera briefly goes underground.
    const eased = easeInOutCubic(t);
    const direction = from.clone().normalize().lerp(to.clone().normalize(), eased).normalize();
    const radius = THREE.MathUtils.lerp(from.length(), to.length(), easeInOutCubic(t));
    camera.position.copy(direction.multiplyScalar(radius));
    camera.lookAt(0, 0, 0);

    if (t >= 1 && !finished.current) {
      finished.current = true;
      onDone();
    }
  });

  return null;
}

export function GlobeIntro({ onFinished }: { onFinished: () => void }) {
  const [failed, setFailed] = useState(false);
  const [leaving, setLeaving] = useState(false);
  const [armed, setArmed] = useState(false);

  // A network that never answers must not hold the shot forever: after this the
  // flight starts regardless and the plain globe flies instead.
  useEffect(() => {
    const timer = window.setTimeout(() => setArmed(true), 2600);
    return () => window.clearTimeout(timer);
  }, []);

  const finish = () => {
    if (leaving) return;
    setLeaving(true);
    // Matches the CSS fade below. Unmounting on the same tick would cut the
    // WebGL context out from under a visible frame.
    window.setTimeout(onFinished, 620);
  };

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape' || event.key === ' ') finish();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [leaving]);

  return (
    <div
      className="fixed inset-0 z-50 cursor-pointer"
      onClick={finish}
      style={{
        background: '#01050b',
        opacity: leaving ? 0 : 1,
        transition: 'opacity 600ms cubic-bezier(0.4, 0, 0.2, 1)',
      }}
    >
      <Canvas
        dpr={[1, 1.75]}
        camera={{ fov: 38, near: 0.01, far: 200 }}
        gl={{ antialias: true }}
        onCreated={({ gl }) => {
          gl.setClearColor('#01050b');
        }}
      >
        <ambientLight intensity={0.35} />
        <directionalLight position={[6, 3, 4]} intensity={1.1} />
        <Stars />
        {failed ? (
          <PlainEarth />
        ) : (
          <ErrorBoundary
            onError={() => {
              setFailed(true);
              setArmed(true);
            }}
          >
            {/* Suspense, because `useLoader` suspends: without a fallback the
                whole canvas is blank until a megabyte of JPEG arrives, and the
                plain globe is a better first frame than nothing. */}
            <Suspense fallback={<PlainEarth />}>
              <Earth
                dayUrl="/api/imagery/earth/bluemarble.jpg"
                nightUrl="/api/imagery/earth/nightlights.jpg"
                onReady={() => setArmed(true)}
              />
            </Suspense>
          </ErrorBoundary>
        )}
        <Atmosphere />
        <Flight armed={armed} onDone={finish} />
      </Canvas>

      <div className="pointer-events-none absolute inset-x-0 bottom-0 flex flex-col items-center gap-2 pb-12">
        <div
          className="text-center"
          style={{ animation: 'orca-rise 900ms 400ms var(--ease-out-instrument) both' }}
        >
          <div className="text-ink-0 text-2xl font-semibold tracking-[0.32em]">ORCA</div>
          <div className="text-ink-2 mt-1 text-2xs tracking-[0.22em] uppercase">
            Marine ecosystem reasoning with collaborative agents
          </div>
        </div>
        <div
          className="text-ink-3 text-2xs"
          style={{ animation: 'orca-rise 900ms 1400ms var(--ease-out-instrument) both' }}
        >
          Indian EEZ · 60–100°E, 0–25°N · imagery NASA GIBS Blue Marble
        </div>
      </div>

      <button
        type="button"
        onClick={finish}
        className="text-ink-3 hover:text-ink-0 absolute top-4 right-5 text-2xs tracking-wider uppercase transition-colors"
      >
        skip
      </button>
    </div>
  );
}

/**
 * A boundary around the texture load only.
 *
 * `useLoader` suspends and then throws on failure, and a throw from inside the
 * canvas takes the whole page down. The globe is the least important thing on
 * screen; it must not be able to break the console behind it.
 */
class ErrorBoundary extends React.Component<
  { children: React.ReactNode; onError: () => void },
  { failed: boolean }
> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch() {
    this.props.onError();
  }

  render() {
    return this.state.failed ? null : this.props.children;
  }
}

/** Whether the intro should play. False after the first play in this session. */
export function shouldPlayIntro(): boolean {
  try {
    return sessionStorage.getItem(SESSION_KEY) === null;
  } catch {
    // Private mode, or storage blocked. Play it: a missed intro is a smaller
    // problem than a crash on a storage read.
    return true;
  }
}

export function markIntroSeen(): void {
  try {
    sessionStorage.setItem(SESSION_KEY, '1');
  } catch {
    /* nothing to do; the intro simply plays again next reload */
  }
}
