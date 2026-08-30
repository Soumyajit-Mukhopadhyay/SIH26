/**
 * The sea, at the size it actually is, with the boat at the size it actually is.
 *
 * This is the answer to a question a number cannot answer. "Significant wave
 * height 2.74 m, limit 1.5 m" is a comparison of two decimals. An 8.2 m boat in
 * a 118 m swell with crests over its gunwale is a picture, and it is the same
 * data.
 *
 * What is real here:
 *
 * * every wave parameter comes from the point forecast (see `waves.ts` for the
 *   dispersion relation and the steepness bound);
 * * the boat is scaled to the vessel's actual length overall and floats by
 *   sampling the same closed-form surface the vertex shader evaluates, so it sits
 *   on the water rather than near it;
 * * the amber plane is the vessel class's Hs limit, drawn at ±limit/2 about mean
 *   sea level. Crests punching through it are, literally, the veto.
 *
 * What is not real, and is labelled as such in the UI: the foam, the sky, and the
 * wind chop's period. Those are texture.
 *
 * Performance: one 192² plane, one material, no post-processing, no shadows. It
 * runs beside a MapLibre globe and a deck.gl overlay on the same page, and the
 * frame budget is shared three ways — hence `dpr` capped and the render loop
 * paused when the panel is not visible.
 */

import { useEffect, useMemo, useRef } from 'react';
import { Canvas, useFrame, useThree } from '@react-three/fiber';
import * as THREE from 'three';
import { sampleSurface, seaStateFrom, type SeaState as SeaStateModel } from '@/scenes/waves';

const MAX_COMPONENTS = 5;

const VERTEX = /* glsl */ `
  uniform float uTime;
  uniform vec2  uDir[${MAX_COMPONENTS}];
  uniform float uK[${MAX_COMPONENTS}];
  uniform float uAmp[${MAX_COMPONENTS}];
  uniform float uOmega[${MAX_COMPONENTS}];

  varying vec3 vWorld;
  varying vec3 vNormal;
  varying float vHeight;
  varying float vSteep;

  void main() {
    vec3 pos = position;
    float height = 0.0;
    // Analytic tangents. Finite differences would need three evaluations of the
    // whole sum per vertex and would smooth the crests we are trying to show.
    vec3 tangentX = vec3(1.0, 0.0, 0.0);
    vec3 tangentZ = vec3(0.0, 0.0, 1.0);
    float steep = 0.0;

    for (int i = 0; i < ${MAX_COMPONENTS}; i++) {
      vec2  d = uDir[i];
      float k = uK[i];
      float a = uAmp[i];
      float phase = k * dot(d, pos.xz) - uOmega[i] * uTime;
      float c = cos(phase);
      float s = sin(phase);

      // Gerstner: points move horizontally toward the crest as well as up, which
      // is what makes a crest sharp and a trough broad. A plain sine sea looks
      // like corrugated iron and reads as decoration.
      pos.x += d.x * a * c;
      pos.z += d.y * a * c;
      height += a * s;

      float ka = k * a;
      tangentX += vec3(-d.x * d.x * ka * s, d.x * ka * c, -d.x * d.y * ka * s);
      tangentZ += vec3(-d.y * d.x * ka * s, d.y * ka * c, -d.y * d.y * ka * s);
      steep += ka * max(0.0, s);
    }

    pos.y += height;
    vHeight = height;
    vSteep = steep;
    vNormal = normalize(cross(tangentZ, tangentX));

    vec4 world = modelMatrix * vec4(pos, 1.0);
    vWorld = world.xyz;
    gl_Position = projectionMatrix * viewMatrix * world;
  }
`;

const FRAGMENT = /* glsl */ `
  uniform vec3  uDeep;
  uniform vec3  uShallow;
  uniform vec3  uSky;
  uniform vec3  uFoam;
  uniform vec3  uBreach;
  uniform float uLimit;
  uniform float uTime;
  uniform vec3  uEye;

  varying vec3 vWorld;
  varying vec3 vNormal;
  varying float vHeight;
  varying float vSteep;

  /**
   * Capillary ripple, as a normal perturbation rather than as geometry.
   *
   * The mesh is 192 vertices across a ~390 m domain, so it cannot resolve
   * anything shorter than about 8 m — and the sea's visual character lives almost
   * entirely below that scale. Without this the surface is a smooth sheet: the
   * specular highlight lands as one big soft blob instead of a glitter path, and
   * the whole thing reads as plastic.
   *
   * Four octaves of crossed sinusoids, advected with time. Cheap, and unlike
   * value noise it has an exact analytic derivative, which is what we actually
   * want here.
   */
  vec3 ripple(vec2 p, float t) {
    vec2 slope = vec2(0.0);
    float amp = 0.030;
    float freq = 0.85;
    // Each octave runs at its own angle and drift so the pattern never resolves
    // into a visible grid.
    vec2 dirs[4];
    dirs[0] = vec2( 0.94,  0.34);
    dirs[1] = vec2(-0.42,  0.91);
    dirs[2] = vec2( 0.61, -0.79);
    dirs[3] = vec2(-0.88, -0.47);
    for (int i = 0; i < 4; i++) {
      vec2 d = dirs[i];
      float phase = dot(d, p) * freq + t * (1.1 + float(i) * 0.6);
      slope += d * amp * freq * cos(phase);
      amp  *= 0.62;
      freq *= 2.15;
    }
    return normalize(vec3(-slope.x, 1.0, -slope.y));
  }

  void main() {
    vec3 base = normalize(vNormal);

    // Blend the ripple into the swell normal. Weighted down with distance: at
    // 300 m the ripple is far below a pixel and keeping it there produces
    // shimmering aliasing rather than detail.
    float distance = length(uEye - vWorld);
    float detail = exp(-distance / 90.0);
    vec3 fine = ripple(vWorld.xz, uTime);
    vec3 normal = normalize(mix(base, normalize(base + fine - vec3(0.0, 1.0, 0.0)), detail));

    vec3 view = normalize(uEye - vWorld);
    vec3 sun = normalize(vec3(-0.42, 0.30, -0.85));

    // Fresnel: water is nearly a mirror at grazing angles and nearly clear
    // looking straight down. It is what makes the distance pale and the water at
    // your feet dark — but on its own it made the near field black, because
    // looking steeply down there is no sky term at all.
    float fresnel = 0.02 + 0.98 * pow(1.0 - clamp(dot(normal, view), 0.0, 1.0), 5.0);

    // Depth cue from vertical position rather than bathymetry: a trough reads
    // darker because you are looking through more water to reach it.
    float depth = smoothstep(-1.0, 1.0, vHeight / max(0.3, uLimit));
    vec3 body = mix(uDeep, uShallow, depth * 0.55);

    // Sub-surface scatter: light that entered the wave and came back out. This is
    // the term that stops close water going black, and physically it is why a
    // wave face lit from behind glows. Strongest on faces tilted toward the sun.
    float scatter = pow(max(0.0, dot(base, sun)), 1.4) * smoothstep(-0.5, 1.0, depth);
    body += uShallow * scatter * 0.55;

    // Sky-dome diffuse, so an upward-facing facet is brighter than a vertical one
    // even with no sun on it.
    body *= 0.72 + 0.5 * clamp(base.y, 0.0, 1.0);

    vec3 sky = mix(uSky, uSky * 1.5, clamp(fresnel, 0.0, 1.0));
    vec3 colour = mix(body, sky, fresnel * 0.8);

    // Two specular lobes: a tight one for the glitter path and a broad, dim one
    // for the sheen. A single broad lobe at any usable intensity blew out into a
    // white blob covering a quarter of the sea.
    float ndoth = max(0.0, dot(reflect(-sun, normal), view));
    colour += vec3(1.0, 0.96, 0.88) * pow(ndoth, 900.0) * 1.35;
    colour += vec3(0.85, 0.92, 1.0) * pow(ndoth, 24.0) * 0.09;

    // Foam on steep crests. Texture, not measurement — labelled as such in the UI.
    float foam = smoothstep(0.22, 0.42, vSteep);
    colour = mix(colour, uFoam, foam * 0.55);

    // ---- the part that IS a measurement -------------------------------------
    //
    // A contour where the surface crosses the vessel class's Hs limit, plus a
    // light wash above it. Drawn as a line rather than as a tint over everything
    // above the limit: that version put half the sea under a dark red wash, which
    // read as shadow rather than as a warning and buried the wave shape it was
    // supposed to be marking.
    //
    // The comparison is exactly the rule engine's: crest height above mean sea
    // level against limit/2.
    float threshold = uLimit * 0.5;
    float line = 1.0 - smoothstep(0.0, 0.07, abs(vHeight - threshold));
    float over = smoothstep(threshold, threshold + 0.35, vHeight);
    colour = mix(colour, uBreach, over * 0.20);
    colour = mix(colour, uBreach, line * 0.9);

    // Filmic-ish shoulder. Without it the glitter clips to flat white and the
    // highlights lose their shape.
    colour = colour / (colour + 0.85) * 1.85;

    gl_FragColor = vec4(colour, 1.0);
  }
`;

function Ocean({
  sea,
  limitM,
  paused,
  onFrame,
}: {
  sea: SeaStateModel;
  limitM: number;
  paused: boolean;
  onFrame: (time: number) => void;
}) {
  const { camera } = useThree();
  const material = useMemo(() => {
    const padded = Array.from({ length: MAX_COMPONENTS }, (_, i) => sea.components[i]);
    return new THREE.ShaderMaterial({
      vertexShader: VERTEX,
      fragmentShader: FRAGMENT,
      uniforms: {
        uTime: { value: 0 },
        uDir: {
          value: padded.map((c) =>
            c ? new THREE.Vector2(Math.cos(c.angle), Math.sin(c.angle)) : new THREE.Vector2(1, 0),
          ),
        },
        uK: { value: padded.map((c) => c?.k ?? 0) },
        uAmp: { value: padded.map((c) => c?.amplitude ?? 0) },
        uOmega: { value: padded.map((c) => c?.omega ?? 0) },
        uDeep: { value: new THREE.Color('#0a3247') },
        uShallow: { value: new THREE.Color('#1b8ba8') },
        uSky: { value: new THREE.Color('#5c7887') },
        uFoam: { value: new THREE.Color('#cfe6ef') },
        uBreach: { value: new THREE.Color('#ff5545') },
        uLimit: { value: limitM },
        uEye: { value: new THREE.Vector3() },
      },
    });
  }, [sea, limitM]);

  // Built rotated rather than rotating the mesh: the shader reads `position.xz`
  // and displaces along `position.y`, and a rotated *mesh* leaves local space
  // still in XY, so the sea would rise sideways.
  const geometry = useMemo(() => {
    const plane = new THREE.PlaneGeometry(sea.domain * 2, sea.domain * 2, 192, 192);
    plane.rotateX(-Math.PI / 2);
    return plane;
  }, [sea.domain]);

  useEffect(() => () => material.dispose(), [material]);
  useEffect(() => () => geometry.dispose(), [geometry]);

  useFrame((state) => {
    if (paused) return;
    const t = state.clock.elapsedTime;
    material.uniforms.uTime.value = t;
    material.uniforms.uEye.value.copy(camera.position);
    onFrame(t);
  });

  // 192² over the domain: at a 118 m wavelength that is ~30 vertices per wave,
  // which is enough for a smooth crest and cheap enough to share a frame budget
  // with a MapLibre globe.
  return <mesh material={material} geometry={geometry} frustumCulled={false} />;
}

/** The vessel's Hs limit, as a plane you can watch the sea break through. */
function LimitPlane({ limitM, domain }: { limitM: number; domain: number }) {
  return (
    <mesh position={[0, limitM / 2, 0]} rotation={[-Math.PI / 2, 0, 0]}>
      <planeGeometry args={[domain * 2, domain * 2]} />
      <meshBasicMaterial
        color="#e8a33d"
        transparent
        opacity={0.12}
        side={THREE.DoubleSide}
        depthWrite={false}
      />
    </mesh>
  );
}

/**
 * The boat, floating.
 *
 * Heave from the surface height under the hull, pitch and roll from the surface
 * slope there. Sampled at the hull's own scale rather than at a point, so a boat
 * shorter than the wave does not pivot on a single crest.
 */
/**
 * The boat, floating.
 *
 * Heave from the surface height under the hull, pitch and roll from the surface
 * slope there. Sampled at the hull's own scale rather than at a point, so a boat
 * longer than the local wave does not pivot on a single crest — which is the
 * physical reason a bigger hull earns a higher limit, and worth seeing.
 *
 * The hull is a plan-view outline extruded vertically: pointed bow, straight
 * run, square transom. A box was the first version and it read as a plank; the
 * silhouette is what makes the scale legible, so it is worth the eight vertices.
 */
function Boat({ sea, loaM, timeRef }: { sea: SeaStateModel; loaM: number; timeRef: { t: number } }) {
  const group = useRef<THREE.Group>(null);
  const beam = loaM * 0.34;
  const depth = loaM * 0.16;

  const hull = useMemo(() => {
    const half = loaM / 2;
    const halfBeam = beam / 2;
    const shape = new THREE.Shape();
    shape.moveTo(half, 0); // stem
    shape.quadraticCurveTo(half * 0.55, halfBeam * 0.95, half * 0.1, halfBeam);
    shape.lineTo(-half * 0.86, halfBeam);
    shape.lineTo(-half, halfBeam * 0.78); // transom corner
    shape.lineTo(-half, -halfBeam * 0.78);
    shape.lineTo(-half * 0.86, -halfBeam);
    shape.lineTo(half * 0.1, -halfBeam);
    shape.quadraticCurveTo(half * 0.55, -halfBeam * 0.95, half, 0);

    const geometry = new THREE.ExtrudeGeometry(shape, {
      depth,
      bevelEnabled: true,
      bevelThickness: depth * 0.18,
      bevelSize: beam * 0.06,
      bevelSegments: 2,
    });
    // Extrusion runs along +Z; the hull has to lie flat with its depth vertical.
    geometry.rotateX(-Math.PI / 2);
    geometry.translate(0, depth, 0);
    geometry.computeVertexNormals();
    return geometry;
  }, [loaM, beam, depth]);

  useEffect(() => () => hull.dispose(), [hull]);

  useFrame(() => {
    const node = group.current;
    if (!node) return;
    const t = timeRef.t;
    const half = loaM / 2;

    const bow = sampleSurface(sea.components, half, 0, t);
    const stern = sampleSurface(sea.components, -half, 0, t);
    const mid = sampleSurface(sea.components, 0, 0, t);

    node.position.y = (bow.height + stern.height) / 2;
    node.rotation.z = Math.atan2(bow.height - stern.height, loaM);
    node.rotation.x = Math.atan(mid.slopeZ) * 0.7;
  });

  return (
    <group ref={group}>
      <mesh geometry={hull} position={[0, -depth * 0.55, 0]}>
        <meshStandardMaterial color="#e9f0f3" roughness={0.6} metalness={0.04} />
      </mesh>
      {/* Sheer stripe, in the console's own cyan: it reads the hull's attitude in
          the water at a glance, which a single flat colour does not. */}
      <mesh position={[0, depth * 0.42, 0]}>
        <boxGeometry args={[loaM * 0.9, depth * 0.16, beam * 1.005]} />
        <meshStandardMaterial color="#39c2d8" roughness={0.4} />
      </mesh>
      {/* Wheelhouse, so the scale reads as a boat rather than a hull form. */}
      <mesh position={[-loaM * 0.16, depth * 1.05, 0]}>
        <boxGeometry args={[loaM * 0.26, depth * 1.3, beam * 0.66]} />
        <meshStandardMaterial color="#b9d7e2" roughness={0.45} />
      </mesh>
      {/* Mast: the tallest thing aboard, and a height reference against a crest. */}
      <mesh position={[-loaM * 0.16, depth * 3.0, 0]}>
        <cylinderGeometry args={[loaM * 0.011, loaM * 0.011, depth * 4.2, 6]} />
        <meshStandardMaterial color="#9fb3bd" />
      </mesh>
    </group>
  );
}

/**
 * The camera sits at eye height on a small boat, and that is the whole point.
 *
 * The first version put it 37 m up. From there a 2.66 m swell with a 122 m
 * wavelength is a nearly flat plane — arithmetically true (a slope of about
 * 1:23) and completely misleading, because it is not how that sea is
 * experienced. From 4 m above the water the same crests rise into the horizon
 * line and periodically hide it, which is exactly what makes a 2.66 m sea a
 * problem for an 8.2 m boat.
 *
 * So: fixed eye height, a short baseline, and a slow arc rather than an orbit —
 * enough parallax to read the surface as three-dimensional, not so much that the
 * viewer loses track of which way the swell is running.
 */
const EYE_HEIGHT_M = 4.2;

function Rig({ domain, loaM }: { domain: number; loaM: number }) {
  const { camera } = useThree();
  const distance = Math.max(loaM * 3.4, 26);

  useEffect(() => {
    camera.position.set(distance * 0.6, EYE_HEIGHT_M, distance);
    camera.lookAt(0, 1.2, 0);
    if (camera instanceof THREE.PerspectiveCamera) {
      camera.near = 0.4;
      camera.far = domain * 8;
      camera.updateProjectionMatrix();
    }
  }, [camera, domain, distance]);

  useFrame((state) => {
    const angle = Math.sin(state.clock.elapsedTime * 0.05) * 0.5;
    camera.position.x = Math.sin(angle + 0.55) * distance;
    camera.position.z = Math.cos(angle + 0.55) * distance;
    camera.position.y = EYE_HEIGHT_M;
    camera.lookAt(0, 1.2, 0);
  });
  return null;
}

/**
 * Sky, so the sea has a horizon to break.
 *
 * A gradient inside a large sphere rather than a photograph: the point is a
 * datum for the eye, and a photographic sky would imply a weather condition ORCA
 * has not been asked about. Deliberately overcast-neutral for that reason.
 */
function Sky({ radius }: { radius: number }) {
  const material = useMemo(
    () =>
      new THREE.ShaderMaterial({
        side: THREE.BackSide,
        depthWrite: false,
        vertexShader: /* glsl */ `
          varying float vY;
          void main() {
            vY = normalize(position).y;
            gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
          }
        `,
        fragmentShader: /* glsl */ `
          varying float vY;
          void main() {
            // Haze at the horizon, deeper blue above, and a touch of warmth right
            // at the waterline where the sun sits.
            vec3 horizon = vec3(0.36, 0.47, 0.55);
            vec3 zenith  = vec3(0.05, 0.13, 0.22);
            vec3 colour = mix(horizon, zenith, smoothstep(-0.05, 0.55, vY));
            colour += vec3(0.10, 0.07, 0.02) * exp(-abs(vY) * 22.0);
            gl_FragColor = vec4(colour, 1.0);
          }
        `,
      }),
    [],
  );
  useEffect(() => () => material.dispose(), [material]);
  return (
    <mesh material={material} frustumCulled={false}>
      <sphereGeometry args={[radius, 32, 16]} />
    </mesh>
  );
}

export interface SeaStateInputs {
  waveHeightM: number;
  periodS: number;
  directionFromDeg: number;
  windSpeedKn: number;
  limitM: number;
  loaM: number;
}

export function SeaStateScene({
  inputs,
  paused = false,
}: {
  inputs: SeaStateInputs;
  paused?: boolean;
}) {
  const sea = useMemo(() => seaStateFrom(inputs), [inputs]);
  // The boat reads the clock the ocean wrote, so hull and water cannot disagree
  // by a frame — which at an 8 m hull and a 2.7 m sea is visible as the boat
  // sinking through the surface.
  const timeRef = useRef({ t: 0 });

  return (
    <Canvas
      // Capped: this canvas shares a frame with MapLibre and deck.gl, and on a
      // retina laptop an uncapped dpr costs 4x the fill for no visible gain at
      // this panel size.
      dpr={[1, 1.6]}
      gl={{ antialias: true, powerPreference: 'high-performance' }}
      camera={{ fov: 42 }}
      frameloop={paused ? 'never' : 'always'}
    >
      <color attach="background" args={['#5c7887']} />
      {/* Fog in the HORIZON colour, not the panel colour: its job is to dissolve
          the edge of a finite plane into the sky. It has to reach full opacity
          WELL INSIDE the plane's edge — ending at the edge itself left a visible
          band where the ocean stopped, because the eye finds a 6% contrast step
          along a perfectly straight line without any trouble at all. */}
      <fog attach="fog" args={['#5c7887', sea.domain * 0.18, sea.domain * 0.68]} />
      <hemisphereLight args={['#9dc4d8', '#04121f', 1.2]} />
      <directionalLight position={[-40, 22, -80]} intensity={1.3} color="#ffe9c4" />

      <Sky radius={sea.domain * 5} />
      <Rig domain={sea.domain} loaM={inputs.loaM} />
      <Ocean
        sea={sea}
        limitM={inputs.limitM}
        paused={paused}
        onFrame={(t) => {
          timeRef.current.t = t;
        }}
      />
      <LimitPlane limitM={inputs.limitM} domain={sea.domain} />
      <Boat sea={sea} loaM={inputs.loaM} timeRef={timeRef.current} />
    </Canvas>
  );
}

export { seaStateFrom };
