/*
 * Anime paint kit shared by anims/anime.html and story/story.html:
 * three-tone cel shading, viman, bird (Jatayu / Sampati) drawing, effects.
 */
(function (global) {
  'use strict';
  const S = global.Shapes;
  const { TAU, clamp, lerp, smooth, hash } = S;
  const INK = '#1a1222';
  // [rim light, base, shadow, (detail)]
  const P = {
    gold: ['#fff4c6', '#ffc83a', '#c97b13', '#8a4f08'],
    goldDeep: ['#ffe28e', '#eaa52a', '#a8620e'],
    red: ['#ff7a7a', '#d9263a', '#8c1124'],
    maroon: ['#a7303f', '#6c1024', '#3f0718'],
    saffron: ['#ffc477', '#ff7a14', '#c64a06'],
    teal: ['#7cf0e4', '#14a7a0', '#0b5f78'],
    leaf: ['#b6f08a', '#5bbd3a', '#2c7a20'],
    feather: ['#9a7656', '#4e3628', '#291a12', '#d8c3a4'],
    featherFar: ['#664b38', '#36251b', '#1c110b', '#8c775f'],
    covert: ['#b38d66', '#6d4d37', '#3d291c', '#e6d3b6'],
    body: ['#8c6a4e', '#4a3326', '#26180f'],
    skin: ['#ffd6da', '#f29ba8', '#c45c71'],
    ruff: ['#ffffff', '#f2efe8', '#c3c6d6'],
    beak: ['#fff7da', '#e9d9ab', '#ad945c'],
    leg: ['#efe3c4', '#c2b086', '#86744d'],
    claw: ['#7a7480', '#2a2530', '#0d0b10'],
    cloud: ['#fffdf3', '#f2f6ff', '#b3c8ec'],
  };
  // direction toward the sun (world space, upper right)
  const LW = (() => { const x = 0.56, y = -0.83, l = Math.hypot(x, y); return { x: x / l, y: y / l }; })();
  const rot = (v, a) => ({ x: v.x * Math.cos(a) - v.y * Math.sin(a), y: v.x * Math.sin(a) + v.y * Math.cos(a) });

  /**
   * Three-tone cel fill. L = unit vector toward the light in the current local coords.
   * a = shadow depth, b = rim-light width, lw = ink width.
   */
  // Render modes: 'cel' (default anime), 'real' (soft painted, no ink lines),
  // 'sil' (backlit silhouette: dark body with a rim of light on the lit edge).
  let MODE = 'cel', SIL = { dark: '#120a10', rim: 'rgba(255,200,120,1)', w: 3.5 };
  function setMode(m, opt) { MODE = m; if (opt) SIL = Object.assign({}, SIL, opt); }
  const mixC = (a, b, u) => {
    const h = (x) => [1, 3, 5].map((i) => parseInt(x.slice(i, i + 2), 16));
    if (a[0] !== '#' || b[0] !== '#') return a;
    const A = h(a), B = h(b);
    return '#' + A.map((v, i) => Math.round(v + (B[i] - v) * u).toString(16).padStart(2, '0')).join('');
  };
  function cel3(c, path, pal, L, a = 6, b = 3, lw = 0) {
    if (MODE === 'sil') {
      c.save(); c.clip(path);
      c.fillStyle = SIL.rim; c.fillRect(-1e4, -1e4, 2e4, 2e4);
      c.translate(-L.x * SIL.w, -L.y * SIL.w); c.fillStyle = SIL.dark; c.fill(path);
      c.restore();
      return;
    }
    if (MODE === 'real') {                         // soft two-tone, no outline
      c.save(); c.clip(path);
      c.fillStyle = mixC(pal[2], pal[1], 0.35); c.fillRect(-1e4, -1e4, 2e4, 2e4);
      c.translate(L.x * a * 1.4, L.y * a * 1.4); c.fillStyle = pal[1]; c.fill(path);
      c.translate(L.x * a * 0.8, L.y * a * 0.8); c.globalAlpha *= 0.35; c.fillStyle = pal[0]; c.fill(path);
      c.restore();
      return;
    }
    // shadow everywhere; rim = (path shifted toward light) minus (path shifted away);
    // base = intersection of both. Built from nested clips, so unions of puffs work.
    c.save();
    c.clip(path);
    c.fillStyle = pal[2]; c.fillRect(-1e4, -1e4, 2e4, 2e4);
    c.translate(L.x * a, L.y * a); c.clip(path); c.translate(-L.x * a, -L.y * a);
    if (b > 0) {
      c.fillStyle = pal[0]; c.fillRect(-1e4, -1e4, 2e4, 2e4);
      c.translate(-L.x * b, -L.y * b); c.clip(path); c.translate(L.x * b, L.y * b);
    }
    c.fillStyle = pal[1]; c.fillRect(-1e4, -1e4, 2e4, 2e4);
    c.restore();
    if (lw) { c.lineWidth = lw; c.strokeStyle = INK; c.lineJoin = 'round'; c.lineCap = 'round'; c.stroke(path); }
  }

  function bell(c, L) {
    c.strokeStyle = INK; c.lineWidth = 2.5; c.beginPath(); c.moveTo(0, 0); c.lineTo(0, 16); c.stroke();
    const b = new Path2D();
    b.moveTo(-5, 16); b.quadraticCurveTo(-7, 24, -11, 32); b.lineTo(11, 32); b.quadraticCurveTo(7, 24, 5, 16); b.closePath();
    cel3(c, b, P.gold, L, 3, 2, 2.5);
    c.fillStyle = INK; c.beginPath(); c.arc(0, 34, 3, 0, TAU); c.fill();
  }

  function viman(c, tau, st = S.vimanState(tau), opt = {}) {
    const V = S.vimanParts(tau);
    c.save();
    c.translate(st.x, st.y); c.rotate(st.rot);
    const L = rot(LW, -st.rot);
    const lw = 5;

    // flag
    const fp = V.flagPole;
    c.lineCap = 'round';
    c.strokeStyle = INK; c.lineWidth = 10; c.beginPath(); c.moveTo(fp.x0, fp.y0); c.lineTo(fp.x1, fp.y1); c.stroke();
    c.strokeStyle = P.gold[1]; c.lineWidth = 4.5; c.beginPath(); c.moveTo(fp.x0, fp.y0); c.lineTo(fp.x1, fp.y1); c.stroke();
    cel3(c, V.flag, P.saffron, L, 6, 3, 4.5);
    c.fillStyle = P.gold[1]; c.strokeStyle = INK; c.lineWidth = 3;
    c.beginPath(); c.arc(fp.x1, fp.y1 - 4, 6, 0, TAU); c.fill(); c.stroke();

    // stern (peacock) and prow (swan)
    cel3(c, V.peacock, P.gold, L, 9, 4, lw);
    cel3(c, V.peacockCurl, P.gold, L, 6, 3, lw);
    for (const e of V.peacockEyes) {
      cel3(c, S.ellipse(e.x, e.y, e.rx, e.ry, e.rot), P.teal, L, 3, 2, 3);
      c.fillStyle = '#0a2768'; c.beginPath(); c.ellipse(e.x, e.y + 2, e.rx * 0.42, e.ry * 0.42, e.rot, 0, TAU); c.fill();
      c.fillStyle = '#fff'; c.beginPath(); c.arc(e.x - 2, e.y - 3, 2, 0, TAU); c.fill();
    }
    cel3(c, V.swanNeck, P.gold, L, 9, 4, lw);
    cel3(c, V.swanBeak, P.saffron, L, 3, 2, 4);
    cel3(c, V.swanHead, P.gold, L, 6, 3, lw);
    c.fillStyle = INK; c.beginPath(); c.arc(V.swanEye.x, V.swanEye.y, V.swanEye.r, 0, TAU); c.fill();
    c.fillStyle = '#fff'; c.beginPath(); c.arc(V.swanEye.x - 1, V.swanEye.y - 1.5, 1.5, 0, TAU); c.fill();

    // pavilion interior with warm lamp glow
    cel3(c, V.mandapaBack, P.maroon, L, 10, 0, lw);
    c.save(); c.clip(V.mandapaBack);
    const lamp = c.createRadialGradient(0, -150, 5, 0, -150, 150);
    const flick = 0.75 + 0.25 * Math.sin(TAU * 3.1 * tau) * Math.sin(TAU * 1.7 * tau + 1);
    lamp.addColorStop(0, `rgba(255,190,90,${0.75 * flick})`); lamp.addColorStop(1, 'rgba(255,120,40,0)');
    c.fillStyle = lamp; c.fillRect(-200, -240, 400, 130);
    c.restore();
    if (opt.inPavilion) opt.inPavilion(c, L);
    for (const p of V.pillars) cel3(c, p, P.gold, L, 6, 3, 4);
    for (const cap of V.capitals) { cel3(c, cap.top, P.goldDeep, L, 3, 2, 3); cel3(c, cap.base, P.goldDeep, L, 3, 2, 3); }

    // garlands (marigold torans) and bells sway with the lurch
    V.garlands.forEach(([x1, x2], i) => {
      const sw = S.bellSwing(tau, i + 7);
      const cx = (x1 + x2) / 2 + 26 * Math.sin(sw - st.rot), cy = -226 + 34;
      c.strokeStyle = '#2f6b1f'; c.lineWidth = 4;
      c.beginPath(); c.moveTo(x1 + 12, -226); c.quadraticCurveTo(cx, cy, x2 - 12, -226); c.stroke();
      for (let k = 1; k < 8; k++) {
        const u = k / 8, v = 1 - u;
        const px = v * v * (x1 + 12) + 2 * v * u * cx + u * u * (x2 - 12);
        const py = v * v * -226 + 2 * v * u * cy + u * u * -226;
        c.fillStyle = k % 2 ? '#ff8c1a' : '#ffc928'; c.strokeStyle = INK; c.lineWidth = 2;
        c.beginPath(); c.arc(px, py, 5.5, 0, TAU); c.fill(); c.stroke();
      }
    });
    V.bells.forEach((b, i) => {
      c.save(); c.translate(b.x, b.y);
      const a = S.bellSwing(tau, i) - st.rot;
      c.rotate(a); bell(c, rot(L, -a)); c.restore();
    });

    // deck + lotus petals
    cel3(c, V.deck, P.gold, L, 5, 3, lw);
    for (const q of V.lotus) cel3(c, q, P.goldDeep, L, 3, 2, 2.5);

    // hull
    cel3(c, V.hull, P.gold, L, 16, 5, lw);
    cel3(c, V.hullBand, P.red, L, 5, 2, 4);
    c.save(); c.clip(V.hull);
    c.strokeStyle = P.gold[3]; c.lineWidth = 2.5;
    for (const x of V.frieze) {                      // small carved arches under the band
      c.beginPath(); c.moveTo(x - 10, -18); c.quadraticCurveTo(x, -44, x + 10, -18); c.stroke();
    }
    c.restore();
    for (const d of V.hullDots) {
      c.fillStyle = P.red[1]; c.strokeStyle = INK; c.lineWidth = 2.5;
      c.beginPath(); c.arc(d.x, d.y - 37, 6, 0, TAU); c.fill(); c.stroke();
      c.fillStyle = P.gold[0]; c.beginPath(); c.arc(d.x - 1.5, d.y - 39, 2, 0, TAU); c.fill();
    }

    // shikhara
    cel3(c, V.roof, P.gold, L, 7, 3, lw);
    for (const sp of V.spires) {
      cel3(c, sp.body, P.gold, L, 6, 3, 4);
      cel3(c, sp.cap, P.goldDeep, L, 2, 2, 3);
      cel3(c, sp.top, P.gold, L, 3, 2, 3);
    }
    V.tiers.forEach((p, i) => {
      cel3(c, p, P.gold, L, 14 - 3 * i, 4, 0);
      c.save(); c.clip(p);
      c.strokeStyle = 'rgba(140,70,8,0.55)'; c.lineWidth = 2.5;
      for (let k = -4; k <= 4; k++) { c.beginPath(); c.moveTo(k * (30 - 6 * i), -260); c.lineTo(k * (20 - 4 * i), -460); c.stroke(); }
      c.strokeStyle = 'rgba(140,70,8,0.4)'; c.lineWidth = 2;
      for (let yy = -270 - 66 * i; yy > -332 - 66 * i; yy -= 12) { c.beginPath(); c.moveTo(-150, yy); c.lineTo(150, yy); c.stroke(); }
      c.restore();
      c.lineWidth = lw; c.strokeStyle = INK; c.stroke(p);
    });
    for (const q of V.niches) {
      cel3(c, q, P.maroon, L, 4, 0, 3);
      c.save(); c.clip(q);
      c.fillStyle = `rgba(255,170,70,${0.35 * flick})`; c.fill(q);
      c.restore();
    }
    for (const p of V.tierLedges) cel3(c, p, P.red, L, 3, 2, 3.5);
    cel3(c, V.amalaka, P.goldDeep, L, 4, 3, 4);
    c.save(); c.clip(V.amalaka); c.strokeStyle = P.gold[3]; c.lineWidth = 2;
    for (let x = -45; x <= 45; x += 9) { c.beginPath(); c.moveTo(x, -473); c.lineTo(x * 0.9, -447); c.stroke(); }
    c.restore();
    cel3(c, V.kalashNeck, P.gold, L, 3, 2, 3.5);
    cel3(c, V.kalashPot, P.gold, L, 7, 4, 4.5);
    c.strokeStyle = P.red[1]; c.lineWidth = 3; c.beginPath(); c.ellipse(0, -490, 22, 5, 0, 0, Math.PI); c.stroke();
    cel3(c, V.kalashLip, P.gold, L, 2, 2, 3.5);
    cel3(c, V.kalashBud, P.leaf, L, 3, 2, 3.5);
    c.restore();
  }

  // =================================================================
  // JATAYU
  // =================================================================
  function feather(c, fe, pal, Lb, lw = 2, rachis = true) {
    c.save();
    c.translate(fe.x, fe.y); c.rotate(fe.a);
    const p = S.featherShape(fe.len, fe.w, fe.finger);
    cel3(c, p, pal, rot(Lb, -fe.a), 3.5, 2.2, lw);
    if (rachis && pal[3]) {
      c.strokeStyle = pal[3]; c.globalAlpha *= 0.7; c.lineWidth = 1.4;
      c.beginPath(); c.moveTo(3, 0); c.lineTo(fe.len * 0.9, -fe.w * 0.05); c.stroke();
    }
    c.restore();
  }

  function wing(c, w, Lb, far, Pb = P) {
    const palP = far ? Pb.featherFar : Pb.feather;
    const palC = far ? Pb.featherFar : Pb.covert;
    for (let i = w.prim.length - 1; i >= 0; i--) feather(c, w.prim[i], palP, Lb);
    for (const fe of w.sec) feather(c, fe, palP, Lb);
    for (const fe of w.cov) feather(c, fe, palC, Lb, 1.8, false);
    // marginal (leading-edge) coverts band, bowed forward like a real wing
    const hand = { x: lerp(w.Wr.x, w.Tip.x, 0.3), y: lerp(w.Wr.y, w.Tip.y, 0.3) };
    const lead = { x: Math.cos(w.a1 + Math.PI / 2) * 12, y: Math.sin(w.a1 + Math.PI / 2) * 12 };
    const m1 = { x: lerp(w.S.x, w.Wr.x, 0.35) + lead.x, y: lerp(w.S.y, w.Wr.y, 0.35) + lead.y };
    const m2 = { x: lerp(w.S.x, w.Wr.x, 0.8) + lead.x * 1.2, y: lerp(w.S.y, w.Wr.y, 0.8) + lead.y * 1.2 };
    const band = S.ribbon(w.S, m1, m2, hand, 46, 20);
    cel3(c, band, far ? Pb.featherFar : Pb.covert, Lb, 4, 3, 3);
    c.save(); c.clip(band);
    c.strokeStyle = far ? 'rgba(0,0,0,0.25)' : 'rgba(40,20,10,0.35)'; c.lineWidth = 1.5;
    for (let k = 1; k < 9; k++) {
      const u = k / 9, px = lerp(w.S.x, hand.x, u), py = lerp(w.S.y, hand.y, u);
      c.beginPath(); c.arc(px, py, 9, 0, Math.PI); c.stroke();
    }
    c.restore();
  }

  function legDraw(c, R, Lb, far, Pb = P) {
    const { hip, knee, foot } = R, g = R.pose.grip;
    const dx = far ? 10 : 0, dy = far ? -6 : 0;
    c.save(); c.translate(dx, dy);
    const d = Math.atan2(foot.y - knee.y, foot.x - knee.x);
    // tarsus
    c.lineCap = 'round';
    c.strokeStyle = INK; c.lineWidth = 17; c.beginPath(); c.moveTo(knee.x, knee.y); c.lineTo(foot.x, foot.y); c.stroke();
    c.strokeStyle = far ? P.leg[2] : P.leg[1]; c.lineWidth = 10; c.beginPath(); c.moveTo(knee.x, knee.y); c.lineTo(foot.x, foot.y); c.stroke();
    c.strokeStyle = far ? '#5e5036' : P.leg[2]; c.lineWidth = 1.5;
    for (let k = 1; k < 6; k++) {                    // scutes
      const u = 0.25 + k * 0.12, px = lerp(knee.x, foot.x, u), py = lerp(knee.y, foot.y, u);
      const nx = -Math.sin(d) * 4.5, ny = Math.cos(d) * 4.5;
      c.beginPath(); c.moveTo(px - nx, py - ny); c.lineTo(px + nx, py + ny); c.stroke();
    }
    // toes: three forward, hallux back
    const toe = (a, l1, l2, curl) => {
      const x1 = foot.x + Math.cos(a) * l1, y1 = foot.y + Math.sin(a) * l1;
      const a2 = a + curl, x2 = x1 + Math.cos(a2) * l2, y2 = y1 + Math.sin(a2) * l2;
      c.strokeStyle = INK; c.lineWidth = 11;
      c.beginPath(); c.moveTo(foot.x, foot.y); c.lineTo(x1, y1); c.lineTo(x2, y2); c.stroke();
      c.strokeStyle = far ? P.leg[2] : P.leg[1]; c.lineWidth = 6;
      c.beginPath(); c.moveTo(foot.x, foot.y); c.lineTo(x1, y1); c.lineTo(x2, y2); c.stroke();
      // claw
      const a3 = a2 + 0.5 + 0.6 * curl;
      c.save(); c.translate(x2, y2); c.rotate(a3);
      const cl = new Path2D();
      cl.moveTo(0, -4); cl.quadraticCurveTo(12, -5, 16, 6); cl.quadraticCurveTo(9, 1, 0, 4); cl.closePath();
      cel3(c, cl, P.claw, rot(Lb, -a3), 2, 1.5, 2);
      c.restore();
    };
    const spreadT = 1 - 0.45 * g, curl = 0.25 + 0.95 * g;
    toe(d + Math.PI - 0.5, 12, 9, -curl * 0.7);
    for (const o of [-0.55, -0.05, 0.45]) toe(d + o * spreadT, 17, 14, curl);
    c.restore();
    // feathered "trousers"
    const th = S.ruffPath(hip.x + 6 + dx, hip.y + 8 + dy, 24, 30, 9, 0.85, far ? 79 : 77);
    cel3(c, th, far ? Pb.featherFar : Pb.body, Lb, 5, 3, 3.5);
  }

  function head(c, R, Lb, Pb = P, opt = {}) {
    const hd = R.head, Hp = S.headParts(), jaw = R.pose.jaw;
    c.save(); c.translate(hd.x, hd.y); c.rotate(hd.rot);
    const L = rot(Lb, -hd.rot);
    // open beak: mouth interior and lower mandible
    if (jaw > 0.02) {
      c.save(); c.translate(28, 4); c.rotate(jaw * 0.5); c.translate(-28, -4);
      c.fillStyle = '#7a0f22'; c.fill(Hp.mouth);
      c.fillStyle = '#e8566c'; c.beginPath(); c.ellipse(36, 8, 7, 3, 0.3, 0, TAU); c.fill(); // tongue
      cel3(c, Hp.lower, P.beak, L, 3, 2, 3);
      c.restore();
    } else {
      cel3(c, Hp.lower, P.beak, L, 3, 2, 3);
    }
    cel3(c, Hp.skull, Pb.skin, L, 6, 3, 3.8);
    c.strokeStyle = P.skin[2]; c.lineWidth = 1.8; c.lineCap = 'round';
    for (const w of Hp.wrinkles) { c.beginPath(); c.moveTo(w[0], w[1]); c.quadraticCurveTo((w[0] + w[2]) / 2 + 3, (w[1] + w[3]) / 2, w[2], w[3]); c.stroke(); }
    c.fillStyle = '#7a2a3a'; c.fill(Hp.ear);
    cel3(c, Hp.upper, P.beak, L, 3, 2, 3.5);
    c.fillStyle = '#2b2226'; c.fill(Hp.hook);
    c.strokeStyle = INK; c.lineWidth = 3.5; c.stroke(Hp.hook);
    c.fillStyle = '#3a2c2a'; c.fill(Hp.nostril);
    c.strokeStyle = P.beak[2]; c.lineWidth = 2;
    c.beginPath(); c.moveTo(Hp.cere[0], Hp.cere[1]); c.quadraticCurveTo(Hp.cere[0] - 4, -6, Hp.cere[2], Hp.cere[3]); c.stroke();
    // eye: amber iris, pupil, two highlights
    const e = Hp.eye;
    if (opt.eyeClosed) {
      c.strokeStyle = INK; c.lineWidth = 3.5; c.lineCap = 'round';
      c.beginPath(); c.arc(e.x, e.y - 4, e.r + 1, 0.25 * Math.PI, 0.8 * Math.PI); c.stroke();
      c.fillStyle = opt.brow || '#9b3f52'; c.fill(Hp.brow); c.lineWidth = 3; c.stroke(Hp.brow);
      if (opt.tear) tearDrop(c, e, opt.tear);
      c.restore();
      return;
    }
    c.fillStyle = INK; c.beginPath(); c.arc(e.x, e.y, e.r + 2, 0, TAU); c.fill();
    const ig = c.createRadialGradient(e.x, e.y + 2, 1, e.x, e.y, e.r);
    ig.addColorStop(0, '#ffd34d'); ig.addColorStop(1, '#c46a10');
    c.fillStyle = ig; c.beginPath(); c.arc(e.x, e.y, e.r, 0, TAU); c.fill();
    c.fillStyle = INK; c.beginPath(); c.ellipse(e.x + 1.5, e.y, 3, 4.2, 0, 0, TAU); c.fill();
    c.fillStyle = '#fff'; c.beginPath(); c.arc(e.x - 2, e.y - 3, 2.2, 0, TAU); c.fill();
    c.beginPath(); c.arc(e.x + 3, e.y + 3, 1, 0, TAU); c.fill();
    // heavy brow (fierce) – or raised/soft for sorrow
    c.save();
    if (opt.sad) { c.translate(e.x, e.y); c.rotate(-0.35); c.translate(-e.x, -e.y - 3); }
    c.fillStyle = opt.brow || '#9b3f52'; c.fill(Hp.brow);
    c.strokeStyle = INK; c.lineWidth = 3; c.stroke(Hp.brow);
    c.restore();
    if (opt.tear) tearDrop(c, e, opt.tear);
    c.restore();
  }

  /** Tear welling then rolling down (u in 0..1, loops if > 1). */
  function tearDrop(c, e, u) {
    const k = u % 1;
    const well = Math.min(1, k * 3), fall = Math.max(0, (k - 0.33) / 0.67);
    const x = e.x + 4 + fall * 6, y = e.y + e.r + 2 + fall * fall * 70, r = 3 + 2.5 * well;
    c.save(); c.globalAlpha *= 1 - Math.max(0, (fall - 0.8) / 0.2);
    c.fillStyle = 'rgba(170,220,255,0.95)'; c.strokeStyle = '#3b6fa8'; c.lineWidth = 1.5;
    c.beginPath(); c.moveTo(x, y - r * 2.1); c.quadraticCurveTo(x + r, y - r * 0.6, x + r, y);
    c.arc(x, y, r, 0, Math.PI); c.quadraticCurveTo(x - r, y - r * 0.6, x, y - r * 2.1); c.fill(); c.stroke();
    c.fillStyle = '#fff'; c.beginPath(); c.arc(x - r * 0.35, y - r * 0.2, r * 0.3, 0, TAU); c.fill();
    c.restore();
  }

  function drawJatayu(c, tau, alpha = 1) {
    drawBird(c, S.jatayuState(tau), S.jatayuRig(tau), { alpha });
  }

  function drawBird(c, st, R, opt = {}) {
    const alpha = opt.alpha ?? 1;
    const Pb = Object.assign({}, P, opt.pal || {});
    c.save();
    c.globalAlpha = alpha;
    c.translate(st.x, st.y); c.scale(-st.scale, st.scale); c.rotate(st.pitch);
    const Lb = rot({ x: -LW.x, y: LW.y }, -st.pitch);

    wing(c, R.far, Lb, true, Pb);
    if (!opt.noLegs) legDraw(c, R, Lb, true, Pb);
    for (let k = 0; k < R.tail.length; k++) {         // outer tail feathers first
      const idx = k % 2 ? R.tail.length - 1 - (k >> 1) : (k >> 1);
      feather(c, R.tail[idx], Pb.feather, Lb, 2.6);
    }
    // body + mantle feathering
    const body = S.ellipse(0, 0, 100, 50, -0.06);
    cel3(c, body, Pb.body, Lb, 12, 4, 4.5);
    c.save(); c.clip(body);
    c.strokeStyle = 'rgba(210,180,140,0.35)'; c.lineWidth = 1.6;
    for (let row = 0; row < 5; row++) {
      for (let k = 0; k < 9; k++) {
        const x = -80 + k * 20 + (row % 2) * 10, y = -40 + row * 14;
        c.beginPath(); c.arc(x, y, 9, 0.15 * Math.PI, 0.85 * Math.PI); c.stroke();
      }
    }
    const belly = S.ellipse(14, 22, 72, 24, -0.06);
    c.fillStyle = 'rgba(160,120,90,0.45)'; c.fill(belly);
    c.restore();
    if (!opt.noLegs) legDraw(c, R, Lb, false, Pb);

    // neck, ruff, gold collar, head
    const nb = R.neckBase, hd = R.head;
    const neck = S.ribbon(nb, { x: lerp(nb.x, hd.x, 0.35) + 6, y: lerp(nb.y, hd.y, 0.35) + 4 },
      { x: lerp(nb.x, hd.x, 0.7) - 2, y: lerp(nb.y, hd.y, 0.7) + 2 }, { x: hd.x - 4, y: hd.y + 2 }, 30, 20);
    cel3(c, neck, Pb.skin, Lb, 5, 2.5, 4);
    c.save(); c.clip(neck); c.strokeStyle = P.skin[2]; c.lineWidth = 1.6;
    for (let k = 1; k < 5; k++) {
      const u = k / 5, px = lerp(nb.x, hd.x, u), py = lerp(nb.y, hd.y, u);
      c.beginPath(); c.arc(px, py, 12, 1.3, 2.6); c.stroke();
    }
    c.restore();
    const ruffBack = S.ruffPath(nb.x - 2, nb.y + 2, 36, 46, 15, 0.82, 5);
    cel3(c, ruffBack, Pb.ruff, Lb, 7, 3, 3.5);
    const ruffFront = S.ruffPath(nb.x + 8, nb.y - 4, 22, 29, 11, 0.8, 9);
    cel3(c, ruffFront, Pb.ruff, Lb, 4, 2, 2.5);
    // gold collar with ruby (Jatayu is a king among birds)
    const cx = lerp(nb.x, hd.x, 0.3) + 4, cy = lerp(nb.y, hd.y, 0.3);
    const ang = Math.atan2(hd.y - nb.y, hd.x - nb.x);
    const collar = S.ellipse(cx, cy, 6, 17, ang);
    if (opt.collar !== false) {
      cel3(c, collar, P.gold, Lb, 3, 2, 3);
      cel3(c, S.ellipse(cx + 3, cy + 6, 4.5, 4.5), P.red, Lb, 2, 1.5, 2);
    }
    head(c, R, Lb, Pb, opt);

    wing(c, R.near, Lb, false, Pb);
    if (opt.after) opt.after(c, R, Lb);
    c.restore();
  }

  // =================================================================
  // BIRDS, FEATHERS
  // =================================================================
  function birds(c, tau) {
    for (const b of S.birdsState(tau)) {
      c.save(); c.translate(b.x, b.y); c.scale(b.dir, 1);
      const near = (b.size - 22) / 10;
      c.fillStyle = near > 0.5 ? '#251e36' : '#3d3a5c';
      c.fill(S.birdPath(b.size * 1.15, b.flap));
      c.restore();
    }
  }

  function burstFeathers(c, tau) {
    for (const f of S.feathersState(tau)) {
      if (f.alpha <= 0) continue;
      c.save(); c.globalAlpha = f.alpha;
      const fe = { x: f.x, y: f.y, a: f.rot, len: 46 * f.size, w: 13 * f.size, finger: false };
      feather(c, fe, f.light ? P.ruff : P.feather, LW, 2);
      c.restore();
    }
  }

  // =================================================================
  // EFFECTS
  // =================================================================
  function speedLines(c, t, f) {
    const a = 1 - smooth((t - 1.25) / 0.3);
    if (a <= 0) return;
    const dx = -0.6, dy = 0.8;
    c.save(); c.lineCap = 'round';
    for (let i = 0; i < 55; i++) {
      const cx = hash(f, i, 1) * (W + 500) - 250, cy = hash(f, i, 2) * H * 0.8;
      const len = 300 + 800 * hash(f, i, 3);
      c.globalAlpha = a * (0.25 + 0.5 * hash(f, i, 4));
      c.strokeStyle = '#ffffff'; c.lineWidth = 1.5 + 5 * hash(f, i, 5);
      c.beginPath(); c.moveTo(cx - dx * len / 2, cy - dy * len / 2); c.lineTo(cx + dx * len / 2, cy + dy * len / 2); c.stroke();
    }
    c.restore();
  }

  function starBurst(c, x, y, r0, r1, spikes, rot0) {
    c.beginPath();
    for (let i = 0; i < spikes * 2; i++) {
      const a = rot0 + (i / (spikes * 2)) * TAU, r = i % 2 ? r0 : r1 * (0.75 + 0.25 * hash(i, spikes));
      const px = x + Math.cos(a) * r, py = y + Math.sin(a) * r;
      i ? c.lineTo(px, py) : c.moveTo(px, py);
    }
    c.closePath();
  }

  function radialLines(c, x, y, f, color) {
    c.save(); c.fillStyle = color;
    for (let i = 0; i < 80; i++) {
      const a = (i / 80) * TAU + hash(f, i, 31) * 0.07;
      const w = 0.005 + 0.018 * hash(f, i, 32);
      const r0 = 110 + 280 * hash(f, i, 33);
      c.beginPath();
      c.moveTo(x + Math.cos(a) * r0, y + Math.sin(a) * r0);
      c.lineTo(x + Math.cos(a - w) * 2600, y + Math.sin(a - w) * 2600);
      c.lineTo(x + Math.cos(a + w) * 2600, y + Math.sin(a + w) * 2600);
      c.closePath(); c.fill();
    }
    c.restore();
  }
  global.AnimeKit = { setMode, INK, P, LW, rot, cel3, viman, drawJatayu, drawBird, feather, wing, legDraw, head,
                      birds, burstFeathers, speedLines, starBurst, radialLines, bell };
})(typeof window !== 'undefined' ? window : globalThis);
