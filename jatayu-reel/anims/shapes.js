/*
 * Shared scene for every style: Jatayu x Pushpak Viman.
 *
 * Everything here is a pure function of time t (seconds). Styles reuse these
 * shapes, poses and motion paths and only change the paint (colours, lines,
 * textures, strike effect), so all ten clips line up frame for frame.
 *
 * Canvas: 1080x1920, 30 fps, 5 s (150 frames). Flare at 1.7 s, strike at 2.0 s.
 */
(function (global) {
  'use strict';

  const W = 1080, H = 1920, FPS = 30, DUR = 5, FRAMES = FPS * DUR;
  const FLARE_T = 1.7, STRIKE_T = 2.0, STRIKE_FRAME = Math.round(STRIKE_T * FPS);
  const TAU = Math.PI * 2;

  // ---------- maths helpers ----------
  const clamp = (x, a = 0, b = 1) => Math.max(a, Math.min(b, x));
  const lerp = (a, b, u) => a + (b - a) * u;
  const smooth = (u) => { u = clamp(u); return u * u * (3 - 2 * u); };
  const quad = (p0, p1, p2, u) => {
    const v = 1 - u;
    return { x: v * v * p0.x + 2 * v * u * p1.x + u * u * p2.x,
             y: v * v * p0.y + 2 * v * u * p1.y + u * u * p2.y };
  };
  const cubic = (p0, p1, p2, p3, u) => {
    const v = 1 - u;
    const a = v * v * v, b = 3 * v * v * u, c = 3 * v * u * u, d = u * u * u;
    return { x: a * p0.x + b * p1.x + c * p2.x + d * p3.x,
             y: a * p0.y + b * p1.y + c * p2.y + d * p3.y };
  };
  /** Deterministic hash -> [0,1). Use instead of Math.random. */
  function hash(a, b = 0, c = 0) {
    let h = 0x9e3779b9 ^ Math.imul(a | 0, 374761393) ^ Math.imul(b | 0, 668265263) ^ Math.imul(c | 0, 1274126177);
    h = Math.imul(h ^ (h >>> 13), 1274126177);
    h = Math.imul(h ^ (h >>> 16), 2246822519);
    h ^= h >>> 15;
    return (h >>> 0) / 4294967296;
  }

  /** Thick tapered ribbon along a cubic curve (for necks and curls). */
  function ribbon(p0, p1, p2, p3, w0, w1, n = 28) {
    const L = [], R = [];
    for (let i = 0; i <= n; i++) {
      const u = i / n;
      const p = cubic(p0, p1, p2, p3, u);
      const q = cubic(p0, p1, p2, p3, Math.min(1, u + 0.01));
      const r = cubic(p0, p1, p2, p3, Math.max(0, u - 0.01));
      let dx = q.x - r.x, dy = q.y - r.y;
      const len = Math.hypot(dx, dy) || 1; dx /= len; dy /= len;
      const w = lerp(w0, w1, u) / 2;
      L.push({ x: p.x - dy * w, y: p.y + dx * w });
      R.push({ x: p.x + dy * w, y: p.y - dx * w });
    }
    const path = new Path2D();
    path.moveTo(L[0].x, L[0].y);
    for (const p of L.slice(1)) path.lineTo(p.x, p.y);
    for (const p of R.reverse()) path.lineTo(p.x, p.y);
    path.closePath();
    return path;
  }

  function poly(points) {
    const p = new Path2D();
    p.moveTo(points[0][0], points[0][1]);
    for (let i = 1; i < points.length; i++) p.lineTo(points[i][0], points[i][1]);
    p.closePath();
    return p;
  }

  function ellipse(cx, cy, rx, ry, rot = 0) {
    const p = new Path2D();
    p.ellipse(cx, cy, rx, ry, rot, 0, TAU);
    return p;
  }

  // =====================================================================
  // PUSHPAK VIMAN  (local coords: origin = keel centre, sails toward +x)
  // =====================================================================
  const KALASH_LOCAL = { x: 0, y: -492 };

  function vimanState(t) {
    let x = lerp(430, 600, t / DUR);
    let y = 1290 + 8 * Math.sin(TAU * t / 2.5);
    let rot = 0.015 * Math.sin(TAU * t / 3.1);
    const s = t - STRIKE_T;
    if (s > 0) {
      rot += -0.16 * Math.sin(9 * s) * Math.exp(-3.2 * s);   // lurch: tilts back...
      y += 50 * (4 * s) * Math.exp(1 - 4 * s);                // ...drops, then recovers
    }
    return { x, y, rot, scale: 1 };
  }

  function localToWorld(st, p) {
    const c = Math.cos(st.rot), s = Math.sin(st.rot), k = st.scale;
    return { x: st.x + (p.x * c - p.y * s) * k, y: st.y + (p.x * s + p.y * c) * k };
  }

  function kalashWorld(t) { return localToWorld(vimanState(t), KALASH_LOCAL); }

  let _vimanStatic = null;
  function vimanParts(t) {
    if (!_vimanStatic) {
      const P = {};
      const hull = new Path2D();
      hull.moveTo(-292, -95); hull.lineTo(292, -95);
      hull.bezierCurveTo(286, -40, 232, 0, 150, 0);
      hull.lineTo(-150, 0);
      hull.bezierCurveTo(-232, 0, -286, -40, -292, -95);
      hull.closePath();
      P.hull = hull;
      P.hullBand = poly([[-282, -78], [282, -78], [270, -56], [-270, -56]]);
      P.hullDots = [];
      for (let i = -5; i <= 5; i++) P.hullDots.push({ x: i * 46, y: -30, r: 8 });

      // swan-neck prow (right)
      P.swanNeck = ribbon({ x: 262, y: -88 }, { x: 372, y: -150 }, { x: 248, y: -236 }, { x: 318, y: -300 }, 42, 24);
      P.swanHead = ellipse(330, -306, 32, 23, -0.15);
      const beak = new Path2D();
      beak.moveTo(352, -316); beak.quadraticCurveTo(390, -310, 398, -296);
      beak.lineTo(352, -296); beak.closePath();
      P.swanBeak = beak;
      P.swanEye = { x: 336, y: -312, r: 4.5 };

      // peacock-curl stern (left)
      P.peacock = ribbon({ x: -268, y: -88 }, { x: -380, y: -120 }, { x: -400, y: -250 }, { x: -312, y: -268 }, 44, 22);
      P.peacockCurl = ellipse(-300, -258, 30, 30);
      P.peacockEyes = [
        { x: -330, y: -150, rx: 14, ry: 19, rot: 0.6 },
        { x: -352, y: -205, rx: 13, ry: 17, rot: 0.2 },
        { x: -300, y: -258, rx: 12, ry: 12, rot: 0 },
      ];

      P.deck = poly([[-252, -112], [252, -112], [262, -95], [-262, -95]]);
      P.mandapaBack = poly([[-200, -236], [200, -236], [200, -112], [-200, -112]]);
      P.pillars = [-188, -122, -56, 56, 122, 188].map((x) =>
        poly([[x - 10, -236], [x + 10, -236], [x + 12, -112], [x - 12, -112]]));
      P.roof = poly([[-228, -236], [228, -236], [206, -260], [-206, -260]]);

      const tier = (wb, wt, yb, yt) => {
        const p = new Path2D();
        p.moveTo(-wb / 2, yb);
        p.quadraticCurveTo(-wb / 2 + 4, (yb + yt) / 2, -wt / 2, yt);
        p.lineTo(wt / 2, yt);
        p.quadraticCurveTo(wb / 2 - 4, (yb + yt) / 2, wb / 2, yb);
        p.closePath();
        return p;
      };
      P.tiers = [tier(262, 196, -260, -332), tier(204, 140, -332, -398), tier(146, 82, -398, -452)];
      P.tierLedges = [
        poly([[-104, -332], [104, -332], [98, -340], [-98, -340]]),
        poly([[-76, -398], [76, -398], [70, -405], [-70, -405]]),
      ];
      P.amalaka = ellipse(0, -460, 50, 13);
      P.kalashPot = ellipse(0, -490, 23, 21);
      P.kalashNeck = poly([[-9, -522], [9, -522], [10, -506], [-10, -506]]);
      P.kalashLip = ellipse(0, -523, 16, 5);
      P.kalashBud = ellipse(0, -535, 9, 12);
      P.flagPole = { x0: 0, y0: -546, x1: -12, y1: -652 };
      _vimanStatic = P;
    }
    const P = Object.assign({}, _vimanStatic);
    // saffron flag streams back (toward stern) and waves
    const w = Math.sin(TAU * 2.2 * t), w2 = Math.sin(TAU * 2.2 * t + 1.3);
    const flag = new Path2D();
    flag.moveTo(-12, -652);
    flag.quadraticCurveTo(-55, -652 + 10 * w2, -104, -630 + 12 * w);
    flag.quadraticCurveTo(-55, -618 + 8 * w2, -11, -604);
    flag.closePath();
    P.flag = flag;
    return P;
  }

  // =====================================================================
  // JATAYU  (local coords: facing +x; drawn mirrored so he faces left)
  // =====================================================================
  const JATAYU_SCALE = 1.15;
  const FOOT_EXT = { x: 90, y: 82 };     // talons fully forward
  const FOOT_TUCK = { x: -50, y: 46 };

  function jatayuPose(t) {
    let spread, theta, talons, pitch;
    if (t < STRIKE_T) {
      const fl = smooth((t - 1.35) / 0.35);              // flare complete at 1.7 s
      spread = lerp(0.18, 1, fl);
      theta = lerp(2.95, 4.15, fl) + 0.04 * Math.sin(TAU * 7 * t) * (1 - fl);
      theta += 0.2 * smooth((t - FLARE_T) / 0.3);        // cupping air before the hit
      talons = smooth((t - 1.45) / 0.3);
      pitch = lerp(0.75, -0.45, smooth((t - 1.45) / 0.4));
    } else {
      const s = t - STRIKE_T;
      const amp = 0.6 + 0.4 * smooth(s / 0.4);
      spread = 0.95;
      theta = 4.35 - amp + amp * Math.cos(TAU * 3 * s);  // continuous at s=0
      talons = 1 - smooth((s - 0.15) / 0.5);
      pitch = lerp(-0.45, -0.78, smooth(s / 0.35));
      pitch = lerp(pitch, -0.5, smooth((s - 0.8) / 0.8));
    }
    return { spread, theta, talons, pitch };
  }

  function footLocal(talons) {
    return { x: lerp(FOOT_TUCK.x, FOOT_EXT.x, talons), y: lerp(FOOT_TUCK.y, FOOT_EXT.y, talons) };
  }

  /** Body-local point -> world, for a state {x,y,pitch,scale} (mirrored to face left). */
  function jatayuToWorld(st, p) {
    const c = Math.cos(st.pitch), s = Math.sin(st.pitch);
    const rx = p.x * c - p.y * s, ry = p.x * s + p.y * c;
    return { x: st.x - rx * st.scale, y: st.y + ry * st.scale };
  }

  let _target = null;
  function strikeBodyTarget() {
    if (!_target) {
      const K = kalashWorld(STRIKE_T);
      const pose = jatayuPose(STRIKE_T - 1e-6);
      const f = footLocal(1);
      const off = jatayuToWorld({ x: 0, y: 0, pitch: pose.pitch, scale: JATAYU_SCALE }, { x: f.x + 8, y: f.y - 2 });
      _target = { x: K.x - off.x, y: K.y - off.y };
    }
    return _target;
  }

  function jatayuState(t) {
    const pose = jatayuPose(t);
    const T = strikeBodyTarget();
    let p;
    if (t <= STRIKE_T) {
      const x = t / STRIKE_T;
      const u = 0.5 * x + 0.5 * (1 - (1 - x) * (1 - x));
      p = quad({ x: 1380, y: -300 }, { x: 1040, y: 260 }, T, u);
    } else {
      const v = Math.pow(clamp((t - STRIKE_T) / 2.9), 1.45);
      p = quad(T, { x: T.x + 170, y: T.y - 520 }, { x: -380, y: -260 }, v);
    }
    return Object.assign({ x: p.x, y: p.y, scale: JATAYU_SCALE }, pose);
  }

  /** Wing outline in wing-local coords (x along the wing, trailing edge at -y). */
  function wingPaths(L, c) {
    const arm = new Path2D();
    arm.moveTo(0, 12);
    arm.quadraticCurveTo(0.35 * L, 22, 0.72 * L, 14);
    arm.lineTo(0.72 * L, -0.5 * c);
    // scalloped secondaries
    const n = 5;
    for (let i = 0; i < n; i++) {
      const x0 = 0.72 * L - (i / n) * 0.72 * L, x1 = 0.72 * L - ((i + 1) / n) * 0.72 * L;
      const y0 = -lerp(0.5, 0.95, i / n) * c, y1 = -lerp(0.5, 0.95, (i + 1) / n) * c;
      arm.quadraticCurveTo((x0 + x1) / 2, Math.min(y0, y1) - 0.16 * c, x1, y1);
    }
    arm.lineTo(0, -0.8 * c);
    arm.closePath();

    const coverts = new Path2D();
    coverts.moveTo(0, 12);
    coverts.quadraticCurveTo(0.35 * L, 22, 0.72 * L, 14);
    coverts.lineTo(0.7 * L, -0.18 * c);
    coverts.quadraticCurveTo(0.35 * L, -0.2 * c, 0, -0.35 * c);
    coverts.closePath();

    const fingers = new Path2D();
    for (let k = 0; k < 5; k++) {
      const by = lerp(8, -0.48 * c, k / 4);
      const phi = -0.06 - 0.15 * k;
      const fl = 0.36 * L * (1 - 0.07 * k);
      const cx = 0.66 * L + Math.cos(phi) * fl / 2, cy = by + Math.sin(phi) * fl / 2;
      fingers.ellipse(cx, cy, fl / 2, 9 + 0.012 * L, phi, 0, TAU);
    }
    return { arm, coverts, fingers };
  }

  let _jStatic = null;
  function jatayuParts() {
    if (!_jStatic) {
      const J = {};
      J.body = ellipse(0, 0, 98, 50, -0.06);
      J.belly = ellipse(10, 18, 70, 26, -0.06);
      J.tail = poly([[-78, -16], [-184, -36], [-198, -12], [-190, 12], [-78, 22]]);
      J.tailLines = [[-110, -4, -188, -24], [-110, 4, -192, 0]];
      J.thigh = ellipse(20, 36, 38, 26, 0.45);
      J.neck = ribbon({ x: 66, y: -12 }, { x: 92, y: -24 }, { x: 108, y: -40 }, { x: 128, y: -50 }, 30, 22);
      const ruff = [];
      for (let i = 0; i < 26; i++) {
        const a = (i / 26) * TAU, r = (i % 2 ? 31 : 40) + 3 * Math.sin(i * 1.7);
        ruff.push([64 + Math.cos(a) * r, -14 + Math.sin(a) * r * 0.8]);
      }
      J.ruff = poly(ruff);
      J.head = ellipse(140, -54, 28, 21, -0.1);
      const beak = new Path2D();
      beak.moveTo(158, -66); beak.quadraticCurveTo(194, -66, 197, -46);
      beak.quadraticCurveTo(194, -36, 186, -42);
      beak.lineTo(160, -42); beak.closePath();
      J.beak = beak;
      J.beakTip = poly([[186, -54], [197, -46], [192, -38], [184, -43]]);
      J.eye = { x: 146, y: -60, r: 5.5 };
      J.brow = [134, -70, 156, -63];
      J.hip = { x: 16, y: 32 };
      _jStatic = J;
    }
    return _jStatic;
  }

  // =====================================================================
  // SMALL BIRDS, FEATHERS, CLOUDS
  // =====================================================================
  const NBIRDS = 14;
  function birdsState(t) {
    const K = kalashWorld(STRIKE_T);
    const out = [];
    for (let i = 0; i < NBIRDS; i++) {
      const ox = (hash(i, 1) - 0.5) * 300, oy = (hash(i, 2) - 0.5) * 200;
      const bx = 250 + ox + 34 * t, by = 640 + oy + 10 * Math.sin(TAU * (0.3 * t + hash(i, 3)));
      let x = bx, y = by, dir = 1;
      let flapHz = 3.2 + hash(i, 4);
      const s = t - STRIKE_T - 0.05 * hash(i, 5);
      if (s > 0) {
        const b0 = { x: 250 + ox + 34 * STRIKE_T, y: 640 + oy };
        let dx = b0.x - K.x, dy = b0.y - K.y - 120;
        const len = Math.hypot(dx, dy) || 1; dx /= len; dy /= len;
        const v = 260 + 300 * hash(i, 6);
        const d = v * s + 180 * s * s;
        x = bx + dx * d; y = by + dy * d;
        dir = dx < 0 ? -1 : 1;
        flapHz = 7 + 2 * hash(i, 7);
      }
      const flap = Math.sin(TAU * (flapHz * t + hash(i, 8)));
      out.push({ x, y, dir, flap, size: 22 + 10 * hash(i, 9) });
    }
    return out;
  }

  /** Small bird ("M" shape) centred at 0,0 facing +x; flap in [-1,1]. */
  function birdPath(size, flap) {
    const p = new Path2D();
    const wy = -size * 0.55 * flap;
    p.moveTo(-size, wy);
    p.quadraticCurveTo(-size * 0.45, wy * 0.2 - size * 0.15, 0, 0);
    p.quadraticCurveTo(size * 0.45, wy * 0.2 - size * 0.15, size, wy);
    p.quadraticCurveTo(size * 0.45, wy * 0.1 + size * 0.05, size * 0.15, size * 0.18);
    p.lineTo(size * 0.35, size * 0.12);
    p.lineTo(size * 0.12, size * 0.28);
    p.lineTo(-size * 0.25, size * 0.2);
    p.quadraticCurveTo(-size * 0.45, wy * 0.1 + size * 0.05, -size, wy);
    p.closePath();
    return p;
  }

  const NFEATHERS = 30;
  function feathersState(t) {
    const s = t - STRIKE_T;
    if (s <= 0) return [];
    const K = kalashWorld(STRIKE_T);
    const out = [];
    for (let i = 0; i < NFEATHERS; i++) {
      const a = -Math.PI * 0.95 + hash(i, 11) * Math.PI * 1.5;   // mostly up/sideways
      const v = 320 + 520 * hash(i, 12);
      const drag = 2.6;
      const r = v * (1 - Math.exp(-drag * s)) / drag;
      const x = K.x + Math.cos(a) * r + 26 * Math.sin(3 * s + hash(i, 13) * 6) * s;
      const y = K.y + Math.sin(a) * r + 70 * s * s;
      const rot = hash(i, 14) * TAU + (hash(i, 15) - 0.5) * 9 * s;
      const alpha = 1 - smooth((s - 1.6) / 1.1);
      out.push({ x, y, rot, alpha, size: 0.6 + 0.7 * hash(i, 16), light: hash(i, 17) < 0.3 });
    }
    return out;
  }

  /** Feather leaf centred at 0,0 along +x. */
  function featherPath(size) {
    const L = 46 * size, w = 11 * size;
    const p = new Path2D();
    p.moveTo(-L / 2, 0);
    p.quadraticCurveTo(0, -w, L / 2, 0);
    p.quadraticCurveTo(0, w, -L / 2, 0);
    p.closePath();
    return p;
  }

  /** Union of circles as one path (nonzero fill = union). */
  function puffs(circles) {
    const p = new Path2D();
    for (const c of circles) { p.moveTo(c.x + c.r, c.y); p.arc(c.x, c.y, c.r, 0, TAU); }
    return p;
  }

  /** Cloud-sea band: puffs scrolling left (the viman sails right). */
  function cloudBand(t, layer, baseY, radius, spacing, speed) {
    const span = W + spacing * 3;
    const circles = [];
    const n = Math.ceil(span / spacing) + 1;
    for (let j = 0; j < n; j++) {
      const raw = j * spacing - speed * t;
      const x = ((raw % span) + span) % span - spacing * 1.5;
      const idx = Math.floor((j * spacing - speed * t - x) / span); // which wrap
      const k = j + idx * 1000;
      const r = radius * (0.75 + 0.5 * hash(k, layer, 21));
      circles.push({ x, y: baseY - r * 0.35 + 30 * hash(k, layer, 22), r });
    }
    const p = puffs(circles);
    p.rect(-50, baseY, W + 100, H - baseY + 50);
    return p;
  }

  /** A towering cumulus made of stacked puffs, drifting slowly. */
  function cumulus(cx, cy, size, seed) {
    const circles = [
      { x: cx, y: cy - size * 0.55, r: size * 0.8 },
      { x: cx - size * 0.6, y: cy - size * 0.1, r: size * 0.6 },
      { x: cx + size * 0.6, y: cy - size * 0.1, r: size * 0.6 },
    ];
    const n = 15;
    for (let i = 0; i < n; i++) {               // bumpy dome outline
      const a = Math.PI * (i / (n - 1));
      const r = size * (0.24 + 0.16 * hash(seed, i, 1));
      circles.push({ x: cx + Math.cos(a) * size * 1.08, y: cy + size * 0.05 - Math.sin(a) * size * 1.38, r });
    }
    for (let i = 0; i < 6; i++) {               // flat-ish base
      circles.push({ x: cx + (i / 5 - 0.5) * size * 2.2, y: cy + size * 0.12, r: size * (0.3 + 0.08 * hash(seed, i, 2)) });
    }
    return puffs(circles);
  }

  global.Shapes = {
    W, H, FPS, DUR, FRAMES, FLARE_T, STRIKE_T, STRIKE_FRAME, TAU,
    clamp, lerp, smooth, hash, quad, cubic, ribbon, poly, ellipse, puffs,
    vimanState, vimanParts, localToWorld, kalashWorld, KALASH_LOCAL,
    jatayuState, jatayuPose, jatayuParts, wingPaths, footLocal, jatayuToWorld,
    birdsState, birdPath, feathersState, featherPath, cloudBand, cumulus,
  };
})(typeof window !== 'undefined' ? window : globalThis);
