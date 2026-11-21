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

      // --- carved details ---
      P.capitals = [-188, -122, -56, 56, 122, 188].map((x) => ({
        top: poly([[x - 17, -236], [x + 17, -236], [x + 13, -226], [x - 13, -226]]),
        base: poly([[x - 16, -122], [x + 16, -122], [x + 18, -112], [x - 18, -112]]),
      }));
      P.lotus = [];
      for (let x = -240; x <= 240; x += 20) {
        const q = new Path2D();
        q.moveTo(x - 9, -112); q.quadraticCurveTo(x - 8, -126, x, -131); q.quadraticCurveTo(x + 8, -126, x + 9, -112); q.closePath();
        P.lotus.push(q);
      }
      P.spires = [-178, 178].map((x) => {
        const sp = new Path2D();
        sp.moveTo(x - 30, -260); sp.quadraticCurveTo(x - 28, -295, x - 13, -318);
        sp.lineTo(x + 13, -318); sp.quadraticCurveTo(x + 28, -295, x + 30, -260); sp.closePath();
        return { body: sp, cap: ellipse(x, -322, 16, 5), top: ellipse(x, -333, 7, 9) };
      });
      const niche = (cx, by, w, h) => {
        const q = new Path2D();
        q.moveTo(cx - w / 2, by); q.lineTo(cx - w / 2, by - h * 0.55);
        q.quadraticCurveTo(cx - w / 2, by - h, cx, by - h * 1.05);
        q.quadraticCurveTo(cx + w / 2, by - h, cx + w / 2, by - h * 0.55);
        q.lineTo(cx + w / 2, by); q.closePath();
        return q;
      };
      P.niches = [niche(0, -270, 40, 50), niche(-70, -276, 22, 34), niche(70, -276, 22, 34), niche(0, -342, 30, 40), niche(0, -408, 22, 30)];
      P.bells = [-155, -89, 0, 89, 155].map((x) => ({ x, y: -228 }));
      P.garlands = [[-188, -122], [-122, -56], [56, 122], [122, 188]];
      P.frieze = [];
      for (let x = -260; x <= 260; x += 26) P.frieze.push(x);
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
  // JATAYU  (body-local coords: facing +x, origin = body centre;
  //          drawn mirrored so he faces left)
  // =====================================================================
  const JATAYU_SCALE = 1.18;

  /** Pose drivers, all in scene time. */
  function jatayuPose(t) {
    let spread, theta, talons, pitch, tailSpread, jaw, grip;
    if (t < STRIKE_T) {
      const fl = smooth((t - 1.3) / 0.4);                  // flare complete at 1.7 s
      spread = lerp(0.1, 1, fl);
      theta = lerp(3.0, 4.2, fl) + 0.05 * Math.sin(TAU * 7 * t) * (1 - fl);
      theta += 0.18 * smooth((t - FLARE_T) / 0.3);         // cupping air before the hit
      talons = smooth((t - 1.42) / 0.38);
      pitch = lerp(0.8, -0.5, smooth((t - 1.38) / 0.45));
      tailSpread = lerp(0.2, 1, fl);
      jaw = smooth((t - 1.6) / 0.25);                      // screech as he strikes
      grip = 0;
    } else {
      const s = t - STRIKE_T;
      const amp = 0.62 + 0.4 * smooth(s / 0.45);
      spread = lerp(1, 0.94, smooth(s / 0.3));
      theta = 4.38 - amp + amp * Math.cos(TAU * 2.8 * s);  // continuous at s = 0
      talons = 1 - smooth((s - 0.22) / 0.55);
      pitch = lerp(-0.5, -0.82, smooth(s / 0.35));
      pitch = lerp(pitch, -0.5, smooth((s - 0.8) / 0.8));
      tailSpread = lerp(1, 0.45, smooth((s - 0.2) / 0.6));
      jaw = 1 - smooth((s - 0.35) / 0.35);
      grip = smooth(s / 0.06) * (1 - smooth((s - 0.45) / 0.35));
    }
    return { spread, theta, talons, pitch, tailSpread, jaw, grip };
  }

  const HIP = { x: 14, y: 30 };
  function legJoints(talons) {
    return {
      knee: { x: lerp(-16, 30, talons), y: lerp(46, 58, talons) },
      foot: { x: lerp(-54, 78, talons), y: lerp(52, 80, talons) },
    };
  }
  /** The point of the talons that touches the kalash. */
  function contactLocal(talons) {
    const f = legJoints(talons).foot;
    return { x: f.x + 24, y: f.y + 4 };
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
      const off = jatayuToWorld({ x: 0, y: 0, pitch: pose.pitch, scale: JATAYU_SCALE }, contactLocal(1));
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
      const u = 0.45 * x + 0.55 * (1 - (1 - x) * (1 - x));
      p = quad({ x: 1420, y: -340 }, { x: 1120, y: 300 }, T, u);
    } else {
      const s = t - STRIKE_T;
      const v = Math.pow(clamp(s / 2.9), 1.45);
      p = quad(T, { x: T.x + 170, y: T.y - 520 }, { x: -420, y: -300 }, v);
      // recoil kick off the spire
      p.x += 26 * Math.sin(Math.min(s, 0.25) / 0.25 * Math.PI) ;
      p.y -= 18 * Math.sin(Math.min(s, 0.25) / 0.25 * Math.PI);
    }
    return Object.assign({ x: p.x, y: p.y, scale: JATAYU_SCALE }, pose);
  }

  /** Full skeleton + feather layout for drawing (body-local coords). */
  function jatayuRig(t) {
    return rigFromPose(jatayuPose(t), jatayuPose(t - 0.07).theta);
  }

  /**
   * Skeleton + feather layout for any pose
   * {spread, theta, talons, pitch, tailSpread, jaw, grip, wingScale?}.
   * lagTheta = theta a moment earlier (hand follow-through).
   */
  function rigFromPose(p, lagTheta) {
    const lag = { theta: lagTheta };
    const ws = p.wingScale ?? 1;
    const sp = p.spread;
    const La = lerp(72, 150, sp) * ws, Lh = lerp(84, 190, sp) * ws, chord = lerp(58, 96, sp) * Math.sqrt(ws);

    const wing = (shoulder, dTheta) => {
      const a1 = p.theta + dTheta;
      const a2 = lag.theta + dTheta + 0.3 * (1 - sp);
      const S0 = shoulder;
      const Wr = { x: S0.x + Math.cos(a1) * La, y: S0.y + Math.sin(a1) * La };
      const Tip = { x: Wr.x + Math.cos(a2) * Lh, y: Wr.y + Math.sin(a2) * Lh };
      const fan = lerp(0.28, 1, sp);
      const prim = [], sec = [], cov = [];
      for (let i = 0; i < 10; i++) {                     // i = 0 is the outermost primary
        const k = i / 9;
        const u = 1 - k;
        const base = { x: lerp(Wr.x, lerp(Wr.x, Tip.x, 0.55), u), y: lerp(Wr.y, lerp(Wr.y, Tip.y, 0.55), u) };
        prim.push({ x: base.x, y: base.y, a: a2 - k * (Math.PI / 2 - 0.22) * fan + 0.04 * Math.sin(i * 2.1),
                    len: lerp(Lh * 0.62, chord * 1.08, k), w: lerp(15, 23, k), finger: i < 4 });
      }
      for (let i = 0; i < 10; i++) {
        const u = (i + 0.5) / 10;
        const base = { x: lerp(S0.x, Wr.x, u), y: lerp(S0.y, Wr.y, u) };
        sec.push({ x: base.x, y: base.y, a: a1 - Math.PI / 2 + 0.12 * (u - 0.5) + 0.25 * (lag.theta - p.theta),
                   len: chord * (0.94 + 0.12 * hash(i, 51)), w: 26 });
      }
      for (let i = 0; i < 12; i++) {
        const u = (i + 0.5) / 12;
        const onHand = u > 0.75;
        const A = onHand ? Wr : S0, B = onHand ? Tip : Wr, uu = onHand ? (u - 0.75) / 0.25 * 0.35 : u / 0.75;
        const ang = onHand ? a2 : a1;
        cov.push({ x: lerp(A.x, B.x, uu), y: lerp(A.y, B.y, uu), a: ang - Math.PI / 2 + 0.1,
                   len: chord * 0.6, w: 24 });
      }
      return { S: S0, Wr, Tip, a1, a2, La, Lh, chord, prim, sec, cov };
    };

    const tail = [];
    for (let k = 0; k < 11; k++) {
      const u = k / 10 - 0.5;
      tail.push({ x: -74, y: 2, a: Math.PI + u * lerp(0.35, 1.15, p.tailSpread) - 0.06 + 0.05 * p.pitch,
                  len: 122 * (1 - 0.18 * Math.abs(u) * 2), w: 17 });
    }

    const leg = legJoints(p.talons);
    const neckBase = { x: 62, y: -14 };
    const nu = -0.5 - 0.5 * p.pitch + (p.neck ?? 0);     // neck lifts the head when diving
    const head = { x: neckBase.x + Math.cos(nu) * 60, y: neckBase.y + Math.sin(nu) * 60, rot: -0.62 * p.pitch + (p.headRot ?? 0) };

    return {
      pose: p,
      far: wing({ x: 42, y: -30 }, -0.3),
      near: wing({ x: 24, y: -24 }, 0),
      tail, hip: HIP, knee: leg.knee, foot: leg.foot,
      neckBase, head,
    };
  }

  /** Asymmetric feather vane along +x (length len, width w). */
  function featherShape(len, w, finger) {
    const p = new Path2D();
    const tipW = finger ? 0.18 : 0.35;
    p.moveTo(0, 0);
    p.bezierCurveTo(len * 0.2, -w * 0.62, len * 0.75, -w * 0.58, len, -w * tipW * 0.6);
    p.quadraticCurveTo(len * 1.03, 0, len * 0.96, w * tipW);
    p.bezierCurveTo(len * 0.75, w * 0.55, len * 0.25, w * 0.5, 0, 0);
    p.closePath();
    return p;
  }

  let _head = null;
  /** Head parts in head-local coords (origin = top of neck, facing +x). */
  function headParts() {
    if (!_head) {
      const H = {};
      H.skull = ellipse(10, -6, 30, 22, -0.08);
      const up = new Path2D();
      up.moveTo(26, -17); up.quadraticCurveTo(58, -21, 66, -2);
      up.quadraticCurveTo(68, 9, 58, 10); up.lineTo(56, 3); up.lineTo(27, 4); up.closePath();
      H.upper = up;
      H.hook = poly([[56, -6], [66, -2], [67, 6], [58, 10], [56, 3]]);
      const lo = new Path2D();
      lo.moveTo(28, 4); lo.lineTo(55, 4); lo.quadraticCurveTo(50, 13, 30, 12); lo.closePath();
      H.lower = lo;
      H.mouth = poly([[28, 3], [56, 3], [30, 14]]);
      H.nostril = ellipse(43, -7, 5.5, 2.2, -0.15);
      H.cere = [33, -16, 31, 4];
      H.eye = { x: 15, y: -11, r: 7.5 };
      H.brow = poly([[0, -20], [28, -24], [26, -15], [12, -14], [2, -15]]);
      H.wrinkles = [[-10, -14, -4, -2], [-15, -8, -9, 4], [-4, 4, 8, 8], [0, 9, 14, 11]];
      H.ear = ellipse(-5, -2, 3.5, 4.5);
      _head = H;
    }
    return _head;
  }

  /** Fluffy scalloped ruff ring around a centre. */
  function ruffPath(cx, cy, r0, r1, lobes, squash, seed) {
    const p = new Path2D();
    for (let i = 0; i <= lobes; i++) {
      const a = (i / lobes) * TAU;
      const r = r0 + (r1 - r0) * (0.6 + 0.4 * hash(i % lobes, seed));
      const x = cx + Math.cos(a) * r0, y = cy + Math.sin(a) * r0 * squash;
      if (i === 0) { p.moveTo(x, y); continue; }
      const am = a - Math.PI / lobes;
      p.quadraticCurveTo(cx + Math.cos(am) * r * 1.18, cy + Math.sin(am) * r * 1.18 * squash, x, y);
    }
    p.closePath();
    return p;
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


  /** Swing angle (radians, relative to world vertical) of hanging bell i. */
  function bellSwing(t, i) {
    const s = t - STRIKE_T;
    let a = 0.06 * Math.sin(TAU * 0.8 * t + i * 1.3);
    if (s > 0) a += 0.55 * Math.exp(-1.7 * s) * Math.sin(8.5 * s + i * 0.6);
    return a;
  }

  // ---------- cinematic time: fast dive, slow-motion flare, hit-stop ----------
  // Output time t -> scene time. Fixed so scene(2.0) == 2.0 and scene(5.0) == 5.0,
  // which keeps the strike on frame 60 for the reel.
  const _remap = (() => {
    const speed = (t) => {
      if (t < STRIKE_T) return lerp(1.08, 0.5, smooth((t - 1.4) / 0.45));
      return lerp(0.12, 1.12, smooth((t - 2.08) / 0.55));
    };
    const N = 2000, tab = new Float64Array(N + 1);
    let acc = 0, accStrike = 0;
    const dt = DUR / N;
    for (let i = 0; i < N; i++) {
      tab[i] = acc;
      const tm = (i + 0.5) * dt;
      acc += speed(tm) * dt;
      if (Math.abs((i + 1) * dt - STRIKE_T) < 1e-9) accStrike = acc;
    }
    tab[N] = acc;
    // normalise pre- and post-strike segments independently
    const iS = Math.round(STRIKE_T / dt);
    const pre = tab[iS], post = tab[N] - tab[iS];
    for (let i = 0; i <= N; i++) {
      tab[i] = i <= iS ? tab[i] / pre * STRIKE_T : STRIKE_T + (tab[i] - pre) / post * (DUR - STRIKE_T);
    }
    return { tab, dt, N };
  })();
  function sceneTime(t) {
    const x = clamp(t / _remap.dt, 0, _remap.N);
    const i = Math.min(_remap.N - 1, Math.floor(x));
    return lerp(_remap.tab[i], _remap.tab[i + 1], x - i);
  }

  // ---------- camera (output time) ----------
  const CAM_KEYS = [
    [0.0, 1.0, 540, 960, 0],
    [1.3, 1.08, 565, 885, -0.02],
    [1.95, 1.3, 540, 800, -0.035],
    [2.05, 1.46, 520, 785, 0.012],
    [2.7, 1.27, 520, 800, 0],
    [5.0, 1.12, 540, 860, 0],
  ];
  function camera(t) {
    let k = 0;
    while (k < CAM_KEYS.length - 2 && t > CAM_KEYS[k + 1][0]) k++;
    const A = CAM_KEYS[k], B = CAM_KEYS[k + 1];
    const u = smooth((t - A[0]) / (B[0] - A[0]));
    const cam = { z: lerp(A[1], B[1], u), fx: lerp(A[2], B[2], u), fy: lerp(A[3], B[3], u), rot: lerp(A[4], B[4], u), sx: 0, sy: 0 };
    const s = t - STRIKE_T;
    if (s >= 0 && s < 0.7) {                            // impact shake, seeded by frame
      const f = Math.round(t * FPS), k2 = 26 * Math.exp(-5.5 * s);
      cam.sx = (hash(f, 41) - 0.5) * 2 * k2; cam.sy = (hash(f, 42) - 0.5) * 2 * k2;
    }
    return cam;
  }
  /** Apply the camera; depth 1 = full camera (subject plane), 0 = locked to screen. */
  function applyCamera(c, cam, depth = 1) {
    const z = 1 + (cam.z - 1) * depth;
    const fx = 540 + (cam.fx - 540) * depth, fy = 960 + (cam.fy - 960) * depth;
    c.translate(W / 2 + cam.sx * depth, H / 2 + cam.sy * depth);
    c.rotate(cam.rot * depth);
    c.scale(z, z);
    c.translate(-fx, -fy);
  }

  global.Shapes = {
    W, H, FPS, DUR, FRAMES, FLARE_T, STRIKE_T, STRIKE_FRAME, TAU,
    clamp, lerp, smooth, hash, quad, cubic, ribbon, poly, ellipse, puffs,
    vimanState, vimanParts, localToWorld, kalashWorld, KALASH_LOCAL,
    jatayuState, jatayuPose, jatayuRig, rigFromPose, jatayuToWorld, contactLocal, featherShape, headParts, ruffPath,
    bellSwing, sceneTime, camera, applyCamera,
    birdsState, birdPath, feathersState, featherPath, cloudBand, cumulus,
  };
})(typeof window !== 'undefined' ? window : globalThis);
