/*
 * Characters for the Sampati–Jatayu story — anime character design.
 *
 * Design language: ~5.6-head heroic proportions, pointed-chin faces, large eyes
 * (gradient iris, pupil, two highlights, heavy upper lash), hair built from
 * pointed locks with a shine band, V-shaped torsos, hands with fingers,
 * flowing cloth that sways with time, layered gold jewellery.
 *
 * person(c, spec, pose, L): origin = pelvis. Standing: head top ≈ -360, feet ≈ +290.
 * vanara(c, kind, pose, L): seated vanara (Hanuman / Angad / Jambavan), origin = seat.
 * Everything is a pure function of pose.t.
 */
(function (global) {
  'use strict';
  const S = global.Shapes, K = global.AnimeKit;
  const { TAU, lerp, clamp, hash } = S;
  const { INK, cel3, rot } = K;

  const PAL = {
    blueSkin: ['#bcd6ff', '#5a8ce0', '#3561ad'],        // Shri Ram
    fairSkin: ['#fff1e2', '#f6d2b2', '#dba684'],
    sitaSkin: ['#fff6ea', '#f9dcc2', '#e2ae8e'],
    ravanaSkin: ['#e0a088', '#a8624c', '#73392c'],
    pitambar: ['#fff5b8', '#f8c826', '#c78e06'],
    saffron: ['#ffd8a0', '#f68e24', '#bd580c'],
    saree: ['#ff9aa4', '#d42a4a', '#8c1030'],
    green: ['#b6ec96', '#45a446', '#226424'],
    purple: ['#d3a8f0', '#7438a8', '#40186a'],
    black: ['#6e6478', '#2c2434', '#130e18'],
    hair: ['#5a5e8c', '#1e1c30', '#0b0a14'],            // blue-black anime hair
    hairShine: '#8f96d8',
    gold: K.P.gold,
    red: K.P.red,
    wood: ['#e0b078', '#93602e', '#573410'],
    steel: ['#ffffff', '#cfd9e6', '#7e8ca2'],
    // vanaras
    hanumanSkin: ['#ffd9a0', '#f0a44a', '#b86a1e'],
    hanumanFace: ['#ffb08e', '#ea6a48', '#a83c24'],
    hanumanHair: ['#ffe49a', '#e8a434', '#a4600e'],
    angadSkin: ['#e8c4a0', '#b8865a', '#7e5230'],
    angadHair: ['#c89060', '#7c4a24', '#46260c'],
    bearSkin: ['#7a6a66', '#3e3230', '#1c1514'],
    bearMane: ['#ffffff', '#ebe6e0', '#b9b2b8'],
    muzzle: ['#fff3e2', '#f2d2b0', '#cfa27e'],
  };

  // ------------------------------------------------------------ helpers
  const at = (p, a, l) => ({ x: p.x + Math.cos(a) * l, y: p.y + Math.sin(a) * l });

  /** Pointed lock / leaf from root to tip (hair strands, cloth ends, fur tufts). */
  function lock(p, x0, y0, x1, y1, w, bend = 0) {
    const dx = x1 - x0, dy = y1 - y0, l = Math.hypot(dx, dy) || 1;
    const nx = -dy / l, ny = dx / l;
    const mx = x0 + dx * 0.42, my = y0 + dy * 0.42;
    p.moveTo(x0 + nx * w / 2, y0 + ny * w / 2);
    p.quadraticCurveTo(mx + nx * (w * 0.75 + bend), my + ny * (w * 0.75 + bend), x1, y1);
    p.quadraticCurveTo(mx + nx * (-w * 0.75 + bend), my + ny * (-w * 0.75 + bend), x0 - nx * w / 2, y0 - ny * w / 2);
    p.closePath();
  }

  /** Fill a union of shapes with cel shading and an outline only on the outside. */
  function massFill(c, p, pal, L, a, b, lw) {
    c.lineWidth = lw * 2; c.strokeStyle = INK; c.lineJoin = 'round'; c.stroke(p);
    cel3(c, p, pal, L, a, b, 0);
  }

  function taper(c, a, b, w0, w1, pal, L, bulge = 0, lw = 3) {
    const dx = b.x - a.x, dy = b.y - a.y, l = Math.hypot(dx, dy) || 1, nx = -dy / l, ny = dx / l;
    const m1 = { x: lerp(a.x, b.x, 0.33) + nx * bulge, y: lerp(a.y, b.y, 0.33) + ny * bulge };
    const m2 = { x: lerp(a.x, b.x, 0.7) + nx * bulge * 0.4, y: lerp(a.y, b.y, 0.7) + ny * bulge * 0.4 };
    cel3(c, S.ribbon(a, m1, m2, b, w0, w1, 14), pal, L, 5, 2.5, lw);
  }

  function jewel(c, x, y, r, gem, L) {
    cel3(c, S.ellipse(x, y, r * 1.35, r * 1.35), K.P.gold, L, 2, 1.5, 1.8);
    cel3(c, S.ellipse(x, y, r, r), gem || K.P.red, L, 1.5, 1.2, 1.4);
    c.fillStyle = '#fff'; c.beginPath(); c.arc(x - r * 0.35, y - r * 0.35, r * 0.28, 0, TAU); c.fill();
  }

  function goldBand(c, p, ang, len, thick, L, gem) {
    c.save(); c.translate(p.x, p.y); c.rotate(ang);
    const b = S.poly([[-thick / 2, -len / 2], [thick / 2, -len / 2], [thick / 2, len / 2], [-thick / 2, len / 2]]);
    cel3(c, b, K.P.gold, rot(L, -ang), 2, 1.5, 2);
    if (gem) jewel(c, 0, 0, thick * 0.45, gem, rot(L, -ang));
    c.restore();
  }

  // ------------------------------------------------------------ eyes, brows, mouth
  /**
   * Anime eye. side = -1 (viewer-left) / +1. o: { w, h, iris:[dark,mid,light], mood, look, female, closed }
   */
  function animeEye(c, x, y, side, o) {
    c.save(); c.translate(x, y); c.scale(side, 1);
    const w = o.w, h = o.h;
    const mood = o.mood || 'calm';
    if (o.closed) {
      c.strokeStyle = INK; c.lineCap = 'round'; c.lineWidth = o.female ? 4 : 3.4;
      c.beginPath(); c.moveTo(-w * 0.5, -1); c.quadraticCurveTo(0, h * 0.42, w * 0.52, -h * 0.08); c.stroke();
      if (o.female) { c.lineWidth = 2; for (const k of [0.25, 0.42]) { c.beginPath(); c.moveTo(w * k, h * 0.2); c.lineTo(w * (k + 0.06), h * 0.42); c.stroke(); } }
      c.restore(); return;
    }
    const inY = mood === 'angry' ? -h * 0.02 : 2, outY = mood === 'sad' ? -h * 0.12 : -h * 0.34;
    const topY = mood === 'angry' ? -h * 0.4 : -h * 0.66;
    const eye = new Path2D();
    eye.moveTo(-w * 0.5, inY);
    eye.quadraticCurveTo(-w * 0.05, topY, w * 0.52, outY);
    eye.quadraticCurveTo(w * 0.3, h * 0.48, -w * 0.5, inY);
    eye.closePath();
    c.fillStyle = '#fffaf6'; c.fill(eye);
    c.save(); c.clip(eye);
    const lx = (o.look || 0) * w * 0.12 * side;
    const ir = c.createLinearGradient(0, -h * 0.5, 0, h * 0.45);
    ir.addColorStop(0, o.iris[0]); ir.addColorStop(0.55, o.iris[1]); ir.addColorStop(1, o.iris[2]);
    c.fillStyle = ir; c.beginPath(); c.ellipse(lx + w * 0.04, h * 0.02, w * 0.25, h * 0.44, 0, 0, TAU); c.fill();
    c.strokeStyle = o.iris[0]; c.lineWidth = 1.6; c.stroke();
    const pk = mood === 'shock' ? 0.45 : 1;
    if (mood === 'shock') { c.fillStyle = '#fffaf6'; c.beginPath(); c.ellipse(lx + w * 0.04, h * 0.02, w * 0.2, h * 0.36, 0, 0, TAU); c.fill(); }
    c.fillStyle = '#120a14'; c.beginPath(); c.ellipse(lx + w * 0.04, h * 0.04, w * 0.11 * pk, h * 0.22 * pk, 0, 0, TAU); c.fill();
    c.fillStyle = 'rgba(40,20,60,0.22)'; c.beginPath(); c.ellipse(0, -h * 0.5, w * 0.7, h * 0.3, 0, 0, TAU); c.fill();  // lid shadow
    c.fillStyle = '#ffffff';
    c.beginPath(); c.ellipse(lx - w * 0.05, -h * 0.16, w * 0.085, h * 0.13, -0.3, 0, TAU); c.fill();
    c.beginPath(); c.arc(lx + w * 0.11, h * 0.18, w * 0.04, 0, TAU); c.fill();
    c.restore();
    // heavy upper lash with outer flick
    c.fillStyle = INK;
    c.beginPath();
    c.moveTo(-w * 0.52, inY + 1);
    c.quadraticCurveTo(-w * 0.05, topY - 1, w * 0.52, outY);
    c.lineTo(w * 0.66, outY - h * (o.female ? 0.3 : 0.16));
    c.lineTo(w * 0.54, outY + h * 0.12);
    c.quadraticCurveTo(-w * 0.05, topY + h * 0.2, -w * 0.48, inY + 3);
    c.closePath(); c.fill();
    if (o.female) {
      c.strokeStyle = INK; c.lineWidth = 2; c.lineCap = 'round';
      for (const k of [0.2, 0.36]) { c.beginPath(); c.moveTo(w * k, topY * 0.75); c.lineTo(w * (k + 0.1), topY * 0.75 - h * 0.25); c.stroke(); }
    }
    c.strokeStyle = INK; c.lineWidth = 1.5; c.lineCap = 'round';
    c.beginPath(); c.moveTo(w * 0.05, h * 0.42); c.quadraticCurveTo(w * 0.35, h * 0.36, w * 0.5, h * 0.05); c.stroke();   // lower lid
    c.strokeStyle = 'rgba(60,30,40,0.5)'; c.lineWidth = 1.4;
    c.beginPath(); c.moveTo(-w * 0.3, topY - h * 0.12); c.quadraticCurveTo(w * 0.1, topY - h * 0.3, w * 0.45, outY - h * 0.25); c.stroke(); // crease
    c.restore();
  }

  function brow(c, x, y, side, mood, color, w, thick) {
    c.save(); c.translate(x, y); c.scale(side, 1);
    const inner = mood === 'angry' ? 7 : mood === 'sad' ? -7 : 0, outer = mood === 'sad' ? 4 : mood === 'angry' ? -4 : -2;
    c.fillStyle = color;
    c.beginPath();
    c.moveTo(-w * 0.45, inner);
    c.quadraticCurveTo(w * 0.1, -thick - 4 + (inner + outer) / 2, w * 0.62, outer);
    c.quadraticCurveTo(w * 0.1, -2 + (inner + outer) / 2, -w * 0.45, inner + thick);
    c.closePath(); c.fill();
    c.restore();
  }

  /** Expressive anime mouth. expr: undefined | 'grin' | 'shout' | 'gasp'. w scales width (3/4 view). */
  function mouthShape(c, x, y, open, mood, female, expr, w = 1) {
    c.lineCap = 'round'; c.lineJoin = 'round'; c.strokeStyle = '#3a0e18';
    if (expr === 'shout' || expr === 'grin' || open > 0.05) {
      const big = expr === 'shout' ? 1 : expr === 'grin' ? 0.75 : 0.5;
      const wd = (12 + 14 * big) * w, ht = expr === 'grin' ? 14 + 6 * open : 4 + 22 * Math.max(open, big * 0.8);
      const m = new Path2D();
      if (expr === 'grin') {                        // wide D-shaped grin
        m.moveTo(x - wd, y - 2); m.quadraticCurveTo(x, y + 2, x + wd, y - 4);
        m.quadraticCurveTo(x + wd * 0.7, y + ht, x, y + ht); m.quadraticCurveTo(x - wd * 0.8, y + ht, x - wd, y - 2);
      } else {
        m.moveTo(x - wd, y); m.quadraticCurveTo(x, y - 6, x + wd, y);
        m.quadraticCurveTo(x + wd * 0.75, y + ht, x, y + ht * 1.05); m.quadraticCurveTo(x - wd * 0.75, y + ht, x - wd, y);
      }
      m.closePath();
      c.fillStyle = '#5a0c1c'; c.fill(m);
      c.save(); c.clip(m);
      c.fillStyle = '#ffffff';
      if (expr === 'grin' || expr === 'shout') { c.fillRect(x - wd, y - 8, wd * 2, 8 + ht * 0.22); }
      if (expr === 'shout') {
        c.beginPath(); c.moveTo(x - wd, y + ht * 1.1); c.lineTo(x + wd, y + ht * 1.1); c.lineTo(x + wd, y + ht * 0.86); c.lineTo(x - wd, y + ht * 0.86); c.fill();
        for (const sd of [-1, 1]) { c.beginPath(); c.moveTo(x + sd * wd * 0.55, y); c.lineTo(x + sd * wd * 0.45, y + ht * 0.4); c.lineTo(x + sd * wd * 0.35, y); c.fill(); } // fangs
      }
      c.fillStyle = '#e65a72'; c.beginPath(); c.ellipse(x, y + ht, wd * 0.62, ht * 0.38, 0, 0, TAU); c.fill();
      c.restore();
      c.lineWidth = 2.6; c.stroke(m);
    } else if (expr === 'gasp') {
      c.fillStyle = '#5a0c1c'; c.beginPath(); c.ellipse(x, y + 2, 6 * w, 8, 0, 0, TAU); c.fill(); c.lineWidth = 2.2; c.stroke();
    } else {
      c.lineWidth = female ? 2.8 : 2.6;
      c.beginPath();
      if (mood === 'sad') { c.moveTo(x - 9 * w, y + 4); c.quadraticCurveTo(x, y - 2, x + 9 * w, y + 4); }
      else if (mood === 'angry') { c.moveTo(x - 11 * w, y + 2); c.lineTo(x - 2, y - 1); c.lineTo(x + 11 * w, y + 3); }
      else { c.moveTo(x - 10 * w, y); c.quadraticCurveTo(x, y + 6, x + 10 * w, y - 1); }
      c.stroke();
      if (female) { c.fillStyle = 'rgba(214,60,90,0.5)'; c.beginPath(); c.ellipse(x, y + 5, 6 * w, 2.6, 0, 0, TAU); c.fill(); }
    }
  }

  /** Anime tear streams from both eyes (u = 0..1 strength). */
  function tearStreams(c, eyes, u, t) {
    c.save(); c.globalAlpha *= Math.min(1, u * 2);
    for (const e of eyes) {
      const len = 26 + 46 * Math.min(1, u), wob = Math.sin(t * 3 + e.x) * 3;
      const p = new Path2D();
      p.moveTo(e.x - 7, e.y + 8);
      p.bezierCurveTo(e.x - 9 + wob, e.y + len * 0.4, e.x - 2 + wob, e.y + len * 0.7, e.x + wob, e.y + len);
      p.bezierCurveTo(e.x + 4 + wob, e.y + len * 0.7, e.x + 6, e.y + len * 0.4, e.x + 5, e.y + 8);
      p.quadraticCurveTo(e.x, e.y + 11, e.x - 7, e.y + 8); p.closePath();
      const g = c.createLinearGradient(0, e.y, 0, e.y + len);
      g.addColorStop(0, 'rgba(190,230,255,0.95)'); g.addColorStop(1, 'rgba(120,190,250,0.6)');
      c.fillStyle = g; c.fill(p);
      c.strokeStyle = 'rgba(50,120,200,0.8)'; c.lineWidth = 1.3; c.stroke(p);
      c.strokeStyle = 'rgba(255,255,255,0.9)'; c.lineWidth = 1.5;
      c.beginPath(); c.moveTo(e.x - 3, e.y + 14); c.quadraticCurveTo(e.x - 3 + wob, e.y + len * 0.5, e.x - 1 + wob, e.y + len * 0.75); c.stroke();
      const k = (t * 1.3 + e.x * 0.01) % 1;
      c.beginPath(); c.arc(e.x + wob, e.y + len + 6 + k * 40, 3.5, 0, TAU);
      c.fillStyle = `rgba(160,215,255,${0.9 * (1 - k)})`; c.fill();
    }
    c.restore();
  }

  // ------------------------------------------------------------ hair
  /** Front hair (cap + bangs + side locks + spiky crown), head-local, for tr >= 0 (turned right). */
  function hairFrontPath(spec, t, tr) {
    const p = new Path2D();
    const sw = (i) => Math.sin(t * 1.6 + i * 1.3) * 1.6;
    const sweep = 10 + tr * 16;                    // bangs swept toward the far side
    p.moveTo(-52, -4); p.bezierCurveTo(-60, -80, -22, -98, 2, -98); p.bezierCurveTo(28, -98, 60, -78, 50 - tr * 10, -6);
    p.quadraticCurveTo(0, -60, -52, -4); p.closePath();
    if (spec.female) {
      lock(p, -2, -92, -40, -2 + sw(1), 30, -8); lock(p, -10, -86, -50, 34 + sw(2), 22, -5);
      lock(p, 6, -92, 34 - tr * 8, -6 + sw(3), 30, 8); lock(p, 14, -86, 44 - tr * 10, 26 + sw(4), 20, 5);
      lock(p, -44, -46, -52, 96 + sw(5), 16, -3); lock(p, 40 - tr * 10, -46, 46 - tr * 10, 80 + sw(6), 14, 3);
      return p;
    }
    // spiky crown silhouette
    if (spec.hair !== 'jata' && spec.hair !== 'crown') {
      for (let i = 0; i < 7; i++) {
        const a = -Math.PI * (0.92 - 0.14 * i);
        lock(p, Math.cos(a) * 30, -46 + Math.sin(a) * 30, Math.cos(a) * 104 + 8, -40 + Math.sin(a) * 92, 40, 8);
      }
    }
    if (spec.hair === 'crown') {                  // swept back under the crown: only short side locks
      lock(p, -46, -50, -54, 30 + sw(7), 16, -3); if (tr < 0.6) lock(p, 42 - tr * 12, -50, 48 - tr * 12, 16, 13, 3);
      return p;
    }
    // bangs: 7 sharp locks of varied length
    const n = 7;
    for (let i = 0; i < n; i++) {
      const u = i / (n - 1);
      const x0 = lerp(-42, 40 - tr * 8, u), y0 = -86 + 16 * Math.abs(u - 0.5);
      const len = [0.9, 1.15, 0.8, 1.2, 0.85, 1.05, 0.7][i];
      const x1 = x0 + sweep * (0.6 + 0.6 * u), y1 = lerp(-64, -26, len) + sw(i);
      lock(p, x0, y0, x1, y1, 26 - 4 * Math.abs(u - 0.5), 6 + tr * 4);
    }
    lock(p, -46, -50, -54, 40 + sw(7), 16, -3);                      // near side lock
    if (tr < 0.6) lock(p, 42 - tr * 12, -50, 48 - tr * 12, 20 + sw(8), 13, 3);   // far side lock
    return p;
  }

  function hairBackPath(spec, t, len) {
    const p = new Path2D();
    if (spec.hair === 'jata') {                       // two clumps falling behind the shoulders
      for (const sd of [-1, 1]) for (let i = 0; i < 3; i++) {
        const sway = Math.sin(t * 1.3 + i + sd) * 5;
        lock(p, sd * (30 + i * 6), -24 + i * 8, sd * (44 + i * 8) + sway, 48 + i * 16, 16, sd * 3);
      }
      return p;
    }
    const n = spec.female ? 9 : 7;
    for (let i = 0; i < n; i++) {
      const u = i / (n - 1) - 0.5;
      const x0 = u * 86, y0 = -30 - 20 * Math.cos(u * 2);
      const sway = Math.sin(t * 1.3 + i * 0.9) * 8;
      lock(p, x0, y0, u * (spec.female ? 118 : 120) + sway, len * (0.88 + 0.12 * Math.cos(u * 3)), spec.female ? 32 : 34, u * 8);
    }
    return p;
  }

  function hairShine(c, path, pal) {
    c.save(); c.clip(path);
    c.strokeStyle = pal; c.lineWidth = 5; c.lineCap = 'round'; c.lineJoin = 'round'; c.globalAlpha *= 0.8;
    c.beginPath();
    for (let i = 0; i <= 8; i++) {
      const a = Math.PI * (1.15 + i * 0.0875), r = i % 2 ? 40 : 50;
      const x = Math.cos(a) * r, y = -44 + Math.sin(a) * r * 0.55;
      i ? c.lineTo(x, y) : c.moveTo(x, y);
    }
    c.stroke();
    c.restore();
  }

  function jataBun(c, t, L, pal) {
    const p = new Path2D();
    for (let i = 0; i < 5; i++) {
      const u = i / 4 - 0.5;
      lock(p, u * 46, -70, u * 18 + Math.sin(t + i) * 2, -168 + Math.abs(u) * 40, 30, u * 8);
    }
    p.moveTo(30, -100); p.ellipse(0, -110, 30, 38, 0, 0, TAU);
    massFill(c, p, pal, L, 6, 3, 2.6);
    hairShine(c, p, PAL.hairShine);
    // rudraksha wraps + a small gold flower
    c.strokeStyle = '#7a3e1c'; c.lineWidth = 6; c.lineCap = 'round';
    for (const y of [-92, -126]) { c.beginPath(); c.moveTo(-30, y); c.quadraticCurveTo(0, y + 8, 30, y); c.stroke(); }
    c.fillStyle = '#a0522d';
    for (let k = -2; k <= 2; k++) { c.beginPath(); c.arc(k * 11, -89, 3.6, 0, TAU); c.fill(); }
    jewel(c, 0, -142, 5, K.P.red, L);
  }

  function crown(c, kind, L) {
    const tall = kind === 'ravana' ? 1.45 : kind === 'angad' ? 0.8 : 1;
    const p = new Path2D();
    p.moveTo(-50, -54); p.lineTo(-48, -78); p.quadraticCurveTo(-46, -100 * tall, -30, -104 * tall);
    p.quadraticCurveTo(-20, -126 * tall, -6, -150 * tall); p.lineTo(0, -170 * tall); p.lineTo(6, -150 * tall);
    p.quadraticCurveTo(20, -126 * tall, 30, -104 * tall); p.quadraticCurveTo(46, -100 * tall, 48, -78); p.lineTo(50, -54);
    p.quadraticCurveTo(0, -70, -50, -54); p.closePath();
    cel3(c, p, K.P.gold, L, 6, 3, 2.8);
    c.save(); c.clip(p);
    c.strokeStyle = K.P.gold[3]; c.lineWidth = 2;
    for (let k = 0; k < 3; k++) { c.beginPath(); c.moveTo(-50, -70 - k * 22 * tall); c.quadraticCurveTo(0, -86 - k * 22 * tall, 50, -70 - k * 22 * tall); c.stroke(); }
    c.restore();
    const gem = kind === 'ravana' ? K.P.red : ['#9cf0d0', '#18a67a', '#0b5c40'];
    jewel(c, 0, -96 * tall, 9, gem, L);
    jewel(c, -26, -80, 5, gem, L); jewel(c, 26, -80, 5, gem, L);
    c.fillStyle = '#fff6cc';
    for (const x of [-38, -14, 14, 38]) { c.beginPath(); c.arc(x, -60, 3.2, 0, TAU); c.fill(); }
  }

  // ------------------------------------------------------------ head
  /** Face outline in a three-quarter view; tr = 0 (front) .. 1 (strong turn right). */
  function facePath(tr, style) {
    const xL = -46 + tr * 4, xR = 46 - tr * 16, cx = tr * 20;
    const chinY = style ? 62 : 68;
    const p = new Path2D();
    p.moveTo(xL, -16);
    p.bezierCurveTo(xL - 2, 18, xL + 6, 40, -16 + cx * 0.6, 54);
    p.quadraticCurveTo(-6 + cx, chinY - 2, cx + 4, chinY);
    p.quadraticCurveTo(14 + cx, chinY - 6, xR - 6, 40);
    p.bezierCurveTo(xR, 26, xR + 5, 8, xR + 3, -2);
    p.lineTo(xR + 1, -16);
    p.bezierCurveTo(xR + 2, -66, 22, -88, 0, -88);
    p.bezierCurveTo(-24, -88, xL - 2, -66, xL, -16);
    p.closePath();
    return p;
  }

  /**
   * Head at the current origin (face centre).
   * pose: { turn (-1..1, default 0.28), mood: calm|sad|angry|shock, expr: grin|shout|gasp,
   *         eyes: 'closed', mouth 0..1, look, tilt, tears 0..1, dim, t }
   * spec.headStyle: undefined (human) | 'hanuman' | 'angad' | 'bear'
   */
  function humanHead(c, spec, pose, L) {
    const t = pose.t || 0, mood = pose.mood || 'calm';
    const turn = pose.turn ?? spec.turn ?? 0.28;
    c.save();
    c.rotate(pose.tilt || 0);
    L = rot(L, -(pose.tilt || 0));
    const sg = turn < 0 ? -1 : 1, tr = Math.min(1, Math.abs(turn));
    c.scale(sg, 1); L = { x: L.x * sg, y: L.y };
    const hpal = spec.hairPal || PAL.hair;
    const style = spec.headStyle;
    const cx = tr * 20, xL = -46 + tr * 4, xR = 46 - tr * 16;
    const OUT = 4.2;                                            // outer contour weight

    // back hair
    if (!pose.noHairBack && (spec.hair === 'long' || spec.hair === 'crown' || spec.hair === 'jata')) {
      massFill(c, hairBackPath(spec, t, spec.female ? 300 : spec.hair === 'jata' ? 120 : 150), hpal, L, 8, 3, OUT * 0.7);
    }
    if (style === 'bear') {
      for (const sd of [-1, 1]) {
        cel3(c, S.ellipse(sd * 50 + cx * 0.3, -72, 22, 22), spec.skin, L, 4, 2, OUT * 0.8);
        cel3(c, S.ellipse(sd * 50 + cx * 0.3, -70, 11, 11), PAL.muzzle, L, 2, 1, 0);
      }
    }
    // ears (near side always, far side only when nearly frontal)
    const ear = (x, sd) => {
      if (style === 'hanuman' || style === 'angad') {
        cel3(c, S.ellipse(x, 2, 15, 19), spec.faceSkin || spec.skin, L, 3, 2, OUT * 0.75);
        cel3(c, S.ellipse(x + sd, 3, 8, 11), PAL.muzzle, L, 2, 1, 0);
      } else if (style !== 'bear') {
        cel3(c, S.ellipse(x, 6, 8, 13), spec.skin, L, 2, 1.2, OUT * 0.7);
        c.strokeStyle = spec.skin[2]; c.lineWidth = 1.6; c.beginPath(); c.arc(x, 6, 5, -1.2, 1.2); c.stroke();
      }
    };
    ear(xL - (style ? 4 : 0), -1);
    if (tr < 0.35) ear(xR + (style ? 4 : 0), 1);
    // fur mane around vanara faces
    if (style) {
      const m = new Path2D();
      for (let i = 0; i < 15; i++) {
        const a = Math.PI * (0.02 + 0.96 * i / 14);
        const r0 = 44, r1 = 66 + 10 * hash(i, 7);
        const sd = Math.cos(a) > 0 ? 1 : -1;
        lock(m, Math.cos(a) * r0 * 0.9 + cx * 0.4, 10 + Math.sin(a) * r0 * 0.8, Math.cos(a) * r1 + sd * 8 + cx * 0.4, 14 + Math.sin(a) * r1, 22, sd * 4);
      }
      massFill(c, m, style === 'bear' ? PAL.bearMane : hpal, L, 4, 2, OUT * 0.7);
    }

    const fp = facePath(tr, style);
    const faceSkin = spec.faceSkin || spec.skin;
    // face: base + hard shadow on the side away from the light + outer contour
    cel3(c, fp, faceSkin, { x: L.x, y: L.y * 0.35 }, 9, 3.5, 0);
    c.lineWidth = OUT; c.strokeStyle = INK; c.lineJoin = 'round'; c.stroke(fp);
    const hf = style ? null : hairFrontPath(spec, t, tr);
    c.save(); c.clip(fp);
    if (hf) { c.save(); c.translate(3, 10); c.fillStyle = faceSkin[2]; c.globalAlpha *= 0.6; c.fill(hf); c.restore(); }  // fringe shadow
    c.fillStyle = faceSkin[2]; c.globalAlpha *= 0.5;                                   // shadow under the cheekbone (far side)
    c.beginPath(); c.moveTo(xR + 4, -4); c.quadraticCurveTo(xR - 14, 22, xR - 8, 42); c.lineTo(xR + 10, 42); c.closePath(); c.fill();
    c.globalAlpha = 1;
    // blush hatching
    c.strokeStyle = 'rgba(240,90,110,0.55)'; c.lineWidth = 1.6;
    for (const [bx, bw] of [[-24 + cx * 0.5, 1], [24 + cx * 0.9, 1 - 0.4 * tr]]) {
      c.fillStyle = 'rgba(255,120,140,0.18)'; c.beginPath(); c.ellipse(bx, 26, 12 * bw, 6, 0, 0, TAU); c.fill();
      for (let k = -1; k <= 1; k++) { c.beginPath(); c.moveTo(bx + k * 6 * bw - 2, 30); c.lineTo(bx + k * 6 * bw + 3, 22); c.stroke(); }
    }
    c.restore();

    // muzzle for vanaras
    if (style) {
      const mz = new Path2D();
      if (style === 'bear') mz.ellipse(cx + 4, 36, 30 - tr * 4, 26, 0, 0, TAU);
      else { const m0 = cx; mz.moveTo(-26 + m0, 20); mz.bezierCurveTo(-34 + m0, 56, -10 + m0, 66, m0 + 2, 66); mz.bezierCurveTo(14 + m0, 66, 32 + m0 - tr * 6, 56, 24 + m0 - tr * 6, 20); mz.quadraticCurveTo(m0, 10, -26 + m0, 20); mz.closePath(); }
      cel3(c, mz, PAL.muzzle, L, 3, 2, 2.4);
    }

    // eyes + brows (far eye foreshortened)
    const female = !!spec.female;
    const ew = female ? 32 : style === 'bear' ? 22 : 29, eh = female ? 30 : style === 'bear' ? 18 : 25;
    const eyeY = style ? -2 : 6;
    const eNear = { x: -20 + cx * 0.55, y: eyeY }, eFar = { x: 22 + cx * 0.85, y: eyeY };
    const eopt = (w) => ({ w, h: eh, iris: spec.iris || ['#2a1606', '#6a3a14', '#c88a3e'], mood, look: pose.look, female, closed: pose.eyes === 'closed' });
    animeEye(c, eNear.x, eNear.y, -1, eopt(ew));
    animeEye(c, eFar.x, eFar.y, 1, eopt(ew * (1 - 0.38 * tr)));
    const bc = style === 'bear' ? '#f4f0ea' : spec.browColor || hpal[1];
    const bt = style === 'bear' ? 7 : female ? 3.4 : 4.8;
    brow(c, eNear.x - 2, eyeY - eh * 0.98, -1, mood, bc, style === 'bear' ? 30 : 26, bt);
    brow(c, eFar.x, eyeY - eh * 0.98, 1, mood, bc, (style === 'bear' ? 30 : 26) * (1 - 0.35 * tr), bt);
    if (mood === 'angry' && !style) {                         // anger crease between the brows
      c.strokeStyle = faceSkin[2]; c.lineWidth = 2; c.beginPath(); c.moveTo(cx - 2, eyeY - eh * 0.9); c.lineTo(cx + 2, eyeY - eh * 0.6); c.stroke();
    }

    // nose (three-quarter: tip line toward the far side)
    if (style === 'bear') { c.fillStyle = '#1a1214'; c.beginPath(); c.ellipse(cx + 6, 26, 13, 9, 0, 0, TAU); c.fill(); c.fillStyle = '#fff'; c.beginPath(); c.arc(cx + 1, 22, 2.6, 0, TAU); c.fill(); }
    else if (style) { c.fillStyle = '#4a2018'; for (const sd of [-1, 1]) { c.beginPath(); c.ellipse(cx + 3 + sd * 5, 30, 3, 2.2, 0, 0, TAU); c.fill(); } }
    else {
      c.fillStyle = faceSkin[2]; c.beginPath(); c.moveTo(cx + 4, 12); c.lineTo(cx + 12, 30); c.lineTo(cx + 2, 31); c.closePath(); c.fill();
      c.strokeStyle = '#4a2420'; c.lineWidth = 2.2; c.lineCap = 'round';
      c.beginPath(); c.moveTo(cx + 9, 22); c.lineTo(cx + 13, 30); c.lineTo(cx + 6, 32); c.stroke();
    }
    if (spec.mustache) {
      const m = cx + 4;
      c.fillStyle = INK; c.beginPath();
      c.moveTo(m, 38); c.bezierCurveTo(m - 14, 32, m - 30, 44, m - 40, 28); c.bezierCurveTo(m - 36, 50, m - 12, 44, m, 42);
      c.bezierCurveTo(m + 10, 44, m + 26 - tr * 8, 50, m + 32 - tr * 8, 28); c.bezierCurveTo(m + 24 - tr * 6, 42, m + 12, 32, m, 38); c.fill();
    }
    mouthShape(c, cx + 3, style ? 48 : 46, pose.mouth || 0, mood, female, pose.expr, 1 - 0.25 * tr);
    if (pose.tears) tearStreams(c, [eNear, eFar], pose.tears, t);
    if (style === 'bear') {                                      // white beard of the elder
      const bd = new Path2D();
      for (let i = 0; i < 7; i++) { const u = i / 6 - 0.5; lock(bd, u * 50 + cx, 52, u * 70 + cx, 104 + 10 * Math.cos(u * 3), 18, u * 4); }
      massFill(c, bd, PAL.bearMane, L, 3, 2, OUT * 0.6);
    }

    // front hair / fur cap / crowns
    if (hf) {
      massFill(c, hf, hpal, L, 7, 3, OUT * 0.75);
      c.save(); c.clip(hf); c.strokeStyle = hpal[2]; c.lineWidth = 1.6;     // inner strand lines
      for (let i = 0; i < 6; i++) { const x = -36 + i * 14; c.beginPath(); c.moveTo(x, -84); c.quadraticCurveTo(x + 10, -50, x + 16 + tr * 8, -18); c.stroke(); }
      c.restore();
      hairShine(c, hf, spec.hairShine || PAL.hairShine);
      if (spec.female) {
        c.strokeStyle = '#d4142a'; c.lineWidth = 3; c.beginPath(); c.moveTo(2, -90); c.lineTo(2, -66); c.stroke();
        c.strokeStyle = K.P.gold[1]; c.lineWidth = 2; c.beginPath(); c.moveTo(cx * 0.3, -68); c.lineTo(cx * 0.3, -48); c.stroke();
        jewel(c, cx * 0.3, -44, 5, K.P.red, L);
      }
    } else if (style !== 'bear') {
      const cap = new Path2D();
      cap.moveTo(-48, -22); cap.bezierCurveTo(-54, -82, -20, -94, 0, -94); cap.bezierCurveTo(22, -94, 54, -82, 46 - tr * 10, -22);
      cap.quadraticCurveTo(24, -42, 14 + cx * 0.5, -32); cap.lineTo(cx * 0.5, -48); cap.lineTo(-12 + cx * 0.5, -32); cap.quadraticCurveTo(-24, -42, -48, -22); cap.closePath();
      massFill(c, cap, hpal, L, 5, 3, OUT * 0.7);
      for (let i = 0; i < 4; i++) { const tuft = new Path2D(); lock(tuft, -30 + i * 20, -80, -40 + i * 22, -112 - 8 * (i % 2), 22, -4); massFill(c, tuft, hpal, L, 3, 2, OUT * 0.6); }
    } else {
      const cap = new Path2D();
      cap.moveTo(-46, -32); cap.bezierCurveTo(-50, -88, 50, -88, 46 - tr * 8, -32); cap.quadraticCurveTo(cx, -54, -46, -32); cap.closePath();
      massFill(c, cap, spec.skin, L, 5, 3, OUT * 0.7);
    }
    if (spec.hair === 'jata') jataBun(c, t, L, hpal);
    if (spec.crown) crown(c, spec.crown, L);
    // forehead marks
    const s = cx * 0.45;
    if (spec.tilak === 'vaishnav') {
      c.strokeStyle = '#fff6dc'; c.lineWidth = 3; c.lineCap = 'round'; c.beginPath();
      c.moveTo(s - 7, -42); c.lineTo(s - 4, -24); c.lineTo(s + 4, -24); c.lineTo(s + 7, -42); c.stroke();
      c.strokeStyle = '#e0242e'; c.lineWidth = 3.4; c.beginPath(); c.moveTo(s, -44); c.lineTo(s, -26); c.stroke();
    } else if (spec.tilak === 'bindi') {
      c.fillStyle = '#d4142a'; c.beginPath(); c.arc(s, -24, 4, 0, TAU); c.fill();
    } else if (spec.tilak === 'shaiv') {
      c.strokeStyle = '#f4f0e6'; c.lineWidth = 2.8;
      for (const dy of [-40, -33, -26]) { c.beginPath(); c.moveTo(s - 15, dy); c.quadraticCurveTo(s, dy - 3, s + 13, dy); c.stroke(); }
      c.fillStyle = '#d4142a'; c.beginPath(); c.arc(s, -33, 3.6, 0, TAU); c.fill();
    } else if (spec.tilak === 'hanuman') {
      c.strokeStyle = '#d4142a'; c.lineWidth = 5; c.lineCap = 'round'; c.beginPath(); c.moveTo(s, -46); c.lineTo(s, -24); c.stroke();
    }
    // earrings (near ear; far ear when frontal)
    if (spec.earrings !== false && style !== 'bear') {
      const ears = [[xL - (style ? 6 : 2), -1]]; if (tr < 0.35) ears.push([xR + (style ? 6 : 2), 1]);
      for (const [ex, sd] of ears) {
        c.save(); c.translate(ex, style ? 22 : 20); c.rotate(Math.sin(t * 2.4 + sd) * 0.12);
        if (female) {
          cel3(c, S.ellipse(0, 4, 4, 4), K.P.gold, L, 1, 1, 1.4);
          const j = new Path2D(); j.moveTo(-8, 18); j.quadraticCurveTo(0, 6, 8, 18); j.lineTo(9, 22); j.lineTo(-9, 22); j.closePath();
          cel3(c, j, K.P.gold, L, 2, 1, 1.6);
          c.fillStyle = '#fff6dc'; for (const x of [-6, 0, 6]) { c.beginPath(); c.arc(x, 25, 1.8, 0, TAU); c.fill(); }
        } else {
          cel3(c, S.ellipse(0, 12, 8, 13), K.P.gold, L, 2, 1.5, 2);
          c.fillStyle = INK; c.beginPath(); c.ellipse(0, 12, 3.2, 6.5, 0, 0, TAU); c.fill();
        }
        c.restore();
      }
    }
    if (spec.nosering) { c.strokeStyle = K.P.gold[1]; c.lineWidth = 2; c.beginPath(); c.arc(cx + 12, 34, 6, -0.4, 2.3); c.stroke(); }
    if (pose.dim) { c.fillStyle = `rgba(30,6,30,${pose.dim})`; c.fill(fp); if (hf) c.fill(hf); }
    c.restore();
  }

  // ------------------------------------------------------------ hands
  /** Hand at wrist w pointing along ang. kind: 'open' | 'fist' | 'grip'. side mirrors the thumb. */
  function hand(c, w, ang, skin, L, side, kind = 'open', small = false) {
    c.save(); c.translate(w.x, w.y); c.rotate(ang); c.scale(small ? 0.85 : 1, side);
    const Lh = rot(L, -ang);
    const palm = S.ellipse(12, 0, 15, 13);
    const fingers = new Path2D();
    if (kind === 'fist' || kind === 'grip') {
      fingers.moveTo(18, -12); fingers.quadraticCurveTo(34, -12, 34, 0); fingers.quadraticCurveTo(34, 13, 18, 13); fingers.closePath();
    } else {
      fingers.moveTo(18, -12); fingers.lineTo(40, -9); fingers.quadraticCurveTo(46, -2, 40, 10); fingers.lineTo(18, 13); fingers.closePath();
    }
    const thumb = new Path2D(); thumb.ellipse(14, -14, 11, 6, -0.6, 0, TAU);
    cel3(c, thumb, skin, Lh, 2, 1.2, 2.2);
    cel3(c, palm, skin, Lh, 3, 1.5, 2.4);
    cel3(c, fingers, skin, Lh, 3, 1.5, 2.4);
    c.strokeStyle = skin[2]; c.lineWidth = 1.4;
    for (const y of [-5, 1, 7]) { c.beginPath(); c.moveTo(22, y); c.lineTo(kind === 'open' ? 38 : 31, y); c.stroke(); }
    c.restore();
  }

  // ------------------------------------------------------------ body
  /**
   * pose: { stance:'stand'|'kneel'|'sit', armL:[a1,a2], armR:[a1,a2], handL, handR,
   *         head:{...}, t, props:{bow:'left'|'right', bowAngle, quiver, sword}, slump }
   */
  function person(c, spec, pose, L) {
    const t = pose.t || 0;
    const br = Math.sin(t * 2.2) * 2 * (pose.breathe ?? 1);
    const female = !!spec.female;
    const big = spec.build === 'big' ? 1.12 : 1;
    const shX = (female ? 56 : 74) * big, shY = -196 + br;
    const shL = { x: -shX, y: shY }, shR = { x: shX, y: shY };
    const skin = spec.skin;
    const garment = spec.garment;
    const wind = Math.sin(t * 1.7) * 10;

    // ---- behind the body: quiver, long hair, scarf tails, tail
    if (pose.props?.quiver) {
      c.save(); c.translate(56, -170); c.rotate(0.45);
      const q = S.poly([[-16, -80], [16, -80], [13, 80], [-13, 80]]);
      cel3(c, q, PAL.wood, rot(L, -0.45), 4, 2, 2.6);
      c.save(); c.clip(q); c.strokeStyle = K.P.gold[1]; c.lineWidth = 5;
      for (const y of [-60, 60]) { c.beginPath(); c.moveTo(-20, y); c.lineTo(20, y); c.stroke(); } c.restore();
      for (let k = -2; k <= 2; k++) {
        c.strokeStyle = INK; c.lineWidth = 2; c.beginPath(); c.moveTo(k * 5, -80); c.lineTo(k * 6, -108); c.stroke();
        const f = new Path2D(); lock(f, k * 6, -100, k * 6, -124, 9, 0);
        cel3(c, f, ['#ffffff', '#f0ece4', '#c8c0b4'], L, 1, 1, 1.4);
      }
      c.restore();
    }
    if (spec.tail) {
      const sw = Math.sin(t * 1.3) * 26;
      const tl = S.ribbon({ x: -30, y: 40 }, { x: -230, y: 90 }, { x: -260 + sw, y: -170 }, { x: -150 + sw, y: -240 }, 26, 12, 24);
      cel3(c, tl, spec.skin, L, 5, 2, 2.8);
      const tuft = new Path2D(); lock(tuft, -150 + sw, -226, -140 + sw, -300, 34, 4);
      massFill(c, tuft, spec.hairPal || PAL.hair, L, 3, 2, 2.2);
    }
    const backHair = spec.hair === 'long' || spec.hair === 'crown' || spec.hair === 'jata';
    if (backHair) {                                             // back hair always behind the body
      c.save(); c.translate(0, -312 + br); c.scale(1.16, 1.16); c.rotate(pose.head?.tilt || 0);
      const len = spec.hair === 'long' ? 330 : spec.hair === 'crown' ? 170 : 120;
      massFill(c, hairBackPath(spec, t, len), spec.hairPal || PAL.hair, L, 8, 3, 2.6);
      c.restore();
    }
    if (spec.uttariya) {                                       // scarf tails behind, blowing
      for (const sd of [-1, 1]) {
        const p = new Path2D();
        lock(p, sd * (shX - 6), shY + 8, sd * (shX + 30) + wind, shY + 230, 30, sd * 8);
        cel3(c, p, spec.uttariya, L, 4, 2, 2.6);
      }
    }

    // ---- legs and lower garment
    const legSkin = skin;
    if (pose.stance === 'sit') {
      const lap = new Path2D();
      lap.moveTo(-54, -8); lap.bezierCurveTo(-150, 0, -176, 66, -120, 92); lap.quadraticCurveTo(0, 116, 120, 92);
      lap.bezierCurveTo(176, 66, 150, 0, 54, -8); lap.closePath();
      cel3(c, lap, garment, L, 9, 3, 3);
      c.save(); c.clip(lap); c.strokeStyle = garment[2]; c.lineWidth = 2;
      for (const x of [-90, -50, 50, 90]) { c.beginPath(); c.moveTo(x * 0.5, 0); c.quadraticCurveTo(x, 50, x * 1.1, 100); c.stroke(); }
      c.restore();
      for (const sd of [-1, 1]) {                              // feet tucked over the shins
        const f = new Path2D(); f.moveTo(sd * 30, 74); f.quadraticCurveTo(sd * 70, 56, sd * 104, 76); f.quadraticCurveTo(sd * 70, 96, sd * 30, 88); f.closePath();
        cel3(c, f, legSkin, L, 3, 1.5, 2.4);
        goldBand(c, { x: sd * 36, y: 82 }, 0, 18, 5, L);
      }
    } else if (pose.stance === 'kneel') {
      // left knee on the ground, right knee raised
      taper(c, { x: 64, y: 70 }, { x: 70, y: 160 }, 34, 26, legSkin, L);             // raised shin
      const f = new Path2D(); f.moveTo(52, 156); f.quadraticCurveTo(80, 150, 104, 164); f.quadraticCurveTo(80, 174, 52, 170); f.closePath();
      cel3(c, f, legSkin, L, 3, 1.5, 2.4);
      goldBand(c, { x: 70, y: 150 }, 0, 26, 6, L);
      const d = new Path2D();
      d.moveTo(-56, -10); d.bezierCurveTo(-90, 40, -96, 120, -74, 166); d.lineTo(-10, 170); d.lineTo(0, 110);
      d.quadraticCurveTo(40, 50, 92, 76); d.quadraticCurveTo(96, 40, 56, -10); d.closePath();
      cel3(c, d, garment, L, 9, 3, 3);
      c.save(); c.clip(d); c.strokeStyle = garment[2]; c.lineWidth = 2;
      for (const x of [-60, -36, -14]) { c.beginPath(); c.moveTo(x * 0.6, 0); c.quadraticCurveTo(x, 90, x * 1.1, 170); c.stroke(); }
      c.strokeStyle = K.P.gold[1]; c.lineWidth = 5; c.beginPath(); c.moveTo(-74, 162); c.lineTo(-10, 166); c.stroke();
      c.restore();
    } else {
      for (const sd of [-1, 1]) {
        taper(c, { x: sd * 30, y: 200 }, { x: sd * 34, y: 284 }, 32, 22, legSkin, L);
        const f = new Path2D(); f.moveTo(sd * 22, 280); f.quadraticCurveTo(sd * 40, 272, sd * 62, 292); f.quadraticCurveTo(sd * 40, 302, sd * 22, 296); f.closePath();
        cel3(c, f, legSkin, L, 3, 1.5, 2.4);
        goldBand(c, { x: sd * 34, y: 272 }, 0, 24, 6, L);
      }
      const d = new Path2D();
      if (female) {
        d.moveTo(-46, -12); d.bezierCurveTo(-60, 80, -74, 200, -78, 286); d.lineTo(78, 286); d.bezierCurveTo(74, 200, 60, 80, 46, -12); d.closePath();
      } else {
        d.moveTo(-56, -14); d.bezierCurveTo(-80, 60, -82, 150, -74, 214); d.lineTo(-8, 206); d.lineTo(0, 140);
        d.lineTo(8, 206); d.lineTo(74, 214); d.bezierCurveTo(82, 150, 80, 60, 56, -14); d.closePath();
      }
      cel3(c, d, garment, L, 10, 3, 3);
      c.save(); c.clip(d); c.strokeStyle = garment[2]; c.lineWidth = 2;
      for (const x of [-50, -28, 28, 50]) { c.beginPath(); c.moveTo(x * 0.6, 0); c.quadraticCurveTo(x, 100, x * 1.15, 290); c.stroke(); }
      c.strokeStyle = K.P.gold[1]; c.lineWidth = 7;
      c.beginPath(); c.moveTo(-90, female ? 280 : 208); c.lineTo(90, female ? 280 : 208); c.stroke();
      c.restore();
    }
    // central pleat fan
    const fanH = pose.stance === 'stand' ? (female ? 280 : 236) : pose.stance === 'kneel' ? 150 : 96;
    const fan = new Path2D();
    fan.moveTo(-14, 0); fan.lineTo(14, 0); fan.lineTo(26, fanH);
    for (let k = 0; k < 5; k++) fan.lineTo(26 - (k + 0.5) * 10.4, fanH + (k % 2 ? -10 : 6));
    fan.lineTo(-26, fanH); fan.closePath();
    cel3(c, fan, garment, L, 4, 2, 2.4);
    c.save(); c.clip(fan); c.strokeStyle = garment[2]; c.lineWidth = 1.6;
    for (const x of [-12, -4, 4, 12]) { c.beginPath(); c.moveTo(x * 0.5, 0); c.lineTo(x * 1.6, fanH); c.stroke(); }
    c.restore();
    c.strokeStyle = K.P.gold[1]; c.lineWidth = 4; c.stroke(fan); c.strokeStyle = INK; c.lineWidth = 1.2; c.stroke(fan);

    // ---- torso
    const tor = new Path2D();
    if (female) {
      tor.moveTo(-20, -232 + br); tor.quadraticCurveTo(-44, -214 + br, shL.x, shY);
      tor.bezierCurveTo(-60, -150, -36, -90, -36, -40); tor.lineTo(-42, -6); tor.lineTo(42, -6); tor.lineTo(36, -40);
      tor.bezierCurveTo(36, -90, 60, -150, shR.x, shY); tor.quadraticCurveTo(44, -214 + br, 20, -232 + br); tor.closePath();
    } else {
      tor.moveTo(-22, -234 + br); tor.quadraticCurveTo(-54 * big, -222 + br, shL.x, shY);
      tor.bezierCurveTo(-82 * big, -150, -54 * big, -80, -46 * big, -30); tor.lineTo(-52, -8); tor.lineTo(52, -8); tor.lineTo(46 * big, -30);
      tor.bezierCurveTo(54 * big, -80, 82 * big, -150, shR.x, shY); tor.quadraticCurveTo(54 * big, -222 + br, 22, -234 + br); tor.closePath();
    }
    cel3(c, tor, skin, L, 10, 3, 3.8);
    c.save(); c.clip(tor);
    if (!female) {
      c.strokeStyle = skin[2]; c.lineWidth = 2.4; c.lineCap = 'round';
      for (const sd of [-1, 1]) {
        c.beginPath(); c.moveTo(sd * 8, -150 + br); c.quadraticCurveTo(sd * 36, -126 + br, sd * 62 * big, -150 + br); c.stroke();   // pec
        c.beginPath(); c.moveTo(sd * 12, -210 + br); c.quadraticCurveTo(sd * 30, -204 + br, sd * 46, -212 + br); c.stroke();      // collarbone
      }
      c.lineWidth = 1.8;
      c.beginPath(); c.moveTo(0, -140 + br); c.lineTo(0, -52); c.stroke();
      for (const y of [-112, -86]) for (const sd of [-1, 1]) { c.beginPath(); c.moveTo(sd * 4, y); c.quadraticCurveTo(sd * 18, y + 4, sd * 26, y - 2); c.stroke(); }
      c.beginPath(); c.arc(0, -40, 3, 0, TAU); c.stroke();
      c.strokeStyle = '#fff6e2'; c.lineWidth = 2.6;                                  // janeu
      c.beginPath(); c.moveTo(-shX + 16, shY + 6); c.quadraticCurveTo(-10, -100, 46, -14); c.stroke();
    } else {
      // choli + bare midriff
      const ch = new Path2D();
      ch.moveTo(shL.x - 2, shY - 2); ch.quadraticCurveTo(0, shY - 16, shR.x + 2, shY - 2);
      ch.bezierCurveTo(60, -150, 48, -118, 40, -104); ch.quadraticCurveTo(0, -94, -40, -104); ch.bezierCurveTo(-48, -118, -60, -150, shL.x - 2, shY - 2); ch.closePath();
      cel3(c, ch, spec.blouse, L, 6, 2.5, 2.4);
      c.strokeStyle = K.P.gold[1]; c.lineWidth = 3; c.beginPath(); c.moveTo(-40, -104); c.quadraticCurveTo(0, -94, 40, -104); c.stroke();
      c.strokeStyle = skin[2]; c.lineWidth = 1.6; c.beginPath(); c.arc(0, -56, 2.6, 0, TAU); c.stroke();
    }
    c.restore();
    // waist sash with knot and hanging ends
    const sash = S.poly([[-50, -24], [50, -24], [54, 2], [-54, 2]]);
    cel3(c, sash, spec.sash || K.P.gold, L, 3, 2, 2.6);
    jewel(c, 0, -11, 8, spec.gem || K.P.red, L);
    for (const sd of [-1, 1]) {
      const p = new Path2D(); lock(p, sd * 22, 0, sd * 30 + wind * 0.4, 96, 16, sd * 4);
      cel3(c, p, spec.sash || K.P.gold, L, 2, 1.5, 2);
    }
    if (female) {                                               // pallu across the chest, over the left shoulder
      const pl = new Path2D();
      pl.moveTo(56, 0); pl.bezierCurveTo(30, -60, -20, -150, shL.x + 6, shY - 6);
      pl.lineTo(shL.x - 22, shY + 10); pl.bezierCurveTo(-50, -120, 0, -50, 26, 0); pl.closePath();
      cel3(c, pl, garment, L, 5, 2.5, 2.6);
      c.strokeStyle = K.P.gold[1]; c.lineWidth = 5; c.beginPath(); c.moveTo(26, 0); c.bezierCurveTo(0, -50, -50, -120, shL.x - 22, shY + 10); c.stroke();
      const tailp = new Path2D(); lock(tailp, shL.x - 10, shY, shL.x - 50 + wind, shY + 300, 54, -10);
      cel3(c, tailp, garment, L, 5, 2.5, 2.6);
    }

    // necklaces
    c.lineCap = 'round';
    for (const [r, wdt] of [[34, 4], [50, 5]]) {
      c.strokeStyle = INK; c.lineWidth = wdt + 2.5; c.beginPath(); c.arc(0, -232 + br, r, 0.22 * Math.PI, 0.78 * Math.PI); c.stroke();
      c.strokeStyle = K.P.gold[1]; c.lineWidth = wdt; c.stroke();
    }
    jewel(c, 0, -178 + br, 8, spec.gem || K.P.red, L);
    if (spec.uttariya) {                                       // scarf over the shoulders (front)
      const u = new Path2D();
      u.moveTo(shL.x - 8, shY - 2); u.quadraticCurveTo(0, shY + 40, shR.x + 8, shY - 2);
      u.lineTo(shR.x + 2, shY + 22); u.quadraticCurveTo(0, shY + 62, shL.x - 2, shY + 22); u.closePath();
      cel3(c, u, spec.uttariya, L, 4, 2, 2.4);
    }

    // ---- arms
    const ua = female ? 96 : 104, fa = female ? 88 : 94;
    const arm = (sh, ang, side, kind) => {
      const elbow = at(sh, ang[0], ua);
      const wrist = at(elbow, ang[0] + ang[1], fa);
      cel3(c, S.ellipse(sh.x, sh.y + 6, female ? 20 : 27 * big, female ? 22 : 28), skin, L, 4, 2, 2.6); // deltoid
      taper(c, sh, elbow, female ? 34 : 44 * big, female ? 26 : 32, skin, L, female ? 2 : 6 * side * -1);
      taper(c, elbow, wrist, female ? 26 : 33, female ? 18 : 23, skin, L, female ? 1 : 3 * side * -1);
      if (female) {                                            // short sleeve + bangles
        const sl = new Path2D(); lock(sl, sh.x, sh.y - 4, at(sh, ang[0], 46).x, at(sh, ang[0], 46).y, 40, 0);
        cel3(c, sl, spec.blouse, L, 3, 2, 2.2);
        for (const k of [70, 78]) goldBand(c, at(elbow, ang[0] + ang[1], k), ang[0] + ang[1], 24, 5, L);
      } else {
        goldBand(c, at(sh, ang[0], 48), ang[0], 40 * big, 10, L, spec.gem || K.P.red);
        goldBand(c, at(elbow, ang[0] + ang[1], 80), ang[0] + ang[1], 28, 8, L);
      }
      hand(c, wrist, ang[0] + ang[1], skin, L, side, kind, female);
      return wrist;
    };
    const hl = arm(shL, pose.armL || [1.85, -0.25], -1, pose.handL || 'open');
    const hr = arm(shR, pose.armR || [1.29, 0.25], 1, pose.handR || 'open');

    // ---- neck + head
    const neck = S.poly([[-17, -258 + br], [17, -258 + br], [20, -206 + br], [-20, -206 + br]]);
    cel3(c, neck, skin, L, 5, 2, 2.6);
    c.save(); c.clip(neck); c.fillStyle = skin[2]; c.beginPath(); c.ellipse(0, -256 + br, 22, 14, 0, 0, TAU); c.fill(); c.restore();
    c.save(); c.translate(0, -312 + br + (pose.slump || 0) * 8); c.scale(1.16, 1.16);
    if (spec.extraHeads) ravanaHeads(c, spec, pose, L);
    humanHead(c, spec, Object.assign({ t, noHairBack: backHair }, pose.head || {}), L);
    c.restore();

    if (pose.props?.bow) bow(c, pose.props.bow === 'left' ? hl : hr, pose.props.bowAngle ?? -0.2, L);
    if (pose.props?.sword !== undefined) sword(c, hr, pose.props.sword, L);
    return { hl, hr };
  }

  function bow(c, hnd, ang, L) {
    c.save(); c.translate(hnd.x + 20, hnd.y); c.rotate(ang);
    const Lb = rot(L, -ang);
    const b = new Path2D();
    b.moveTo(-6, -220); b.bezierCurveTo(-74, -150, -60, -40, -12, 0); b.bezierCurveTo(-60, 40, -74, 150, -6, 220);
    b.lineTo(6, 214); b.bezierCurveTo(-56, 150, -46, 40, 2, 0); b.bezierCurveTo(-46, -40, -56, -150, 6, -214); b.closePath();
    cel3(c, b, PAL.wood, Lb, 3, 2, 2.6);
    c.strokeStyle = K.P.gold[1]; c.lineWidth = 5;
    for (const y of [-120, 120]) { c.beginPath(); c.moveTo(-50, y - 8); c.lineTo(-40, y + 8); c.stroke(); }
    c.strokeStyle = '#f6f2ea'; c.lineWidth = 1.8; c.beginPath(); c.moveTo(0, -216); c.lineTo(0, 216); c.stroke();
    cel3(c, S.ellipse(-12, 0, 9, 22), K.P.gold, Lb, 2, 1.5, 2);
    c.restore();
  }

  /** Chandrahas – Ravana's curved sword, held at the hand, blade angle = swing. */
  function sword(c, hnd, swing, L) {
    c.save(); c.translate(hnd.x, hnd.y); c.rotate(swing);
    const Ls = rot(L, -swing);
    const bl = new Path2D();
    bl.moveTo(10, -9); bl.quadraticCurveTo(160, -50, 280, -14); bl.quadraticCurveTo(190, 6, 10, 10); bl.closePath();
    cel3(c, bl, PAL.steel, Ls, 3, 3, 2.6);
    c.strokeStyle = 'rgba(255,255,255,0.9)'; c.lineWidth = 2.5; c.beginPath(); c.moveTo(20, -5); c.quadraticCurveTo(160, -36, 266, -14); c.stroke();
    cel3(c, S.poly([[0, -26], [12, -26], [12, 26], [0, 26]]), K.P.gold, Ls, 2, 1.5, 2.2);
    cel3(c, S.poly([[-36, -7], [0, -7], [0, 7], [-36, 7]]), PAL.black, Ls, 2, 1.5, 2.2);
    jewel(c, 6, 0, 5, K.P.red, Ls);
    c.restore();
  }

  function ravanaHeads(c, spec, pose, L) {
    // nine more heads in an arc behind the main one, receding into shadow
    const order = [4, -4, 3, -3, 2, -2, 1, -1, 5];
    for (const k of order) {
      const a = Math.abs(k);
      c.save();
      const ang = (k === 5 ? 0 : Math.sign(k)) * (0.3 + 0.3 * a);
      c.translate(k === 5 ? 0 : Math.sin(ang) * 175, k === 5 ? -92 : 38 - Math.cos(ang) * 60);
      const s = 0.7 - a * 0.04;
      c.scale(s, s);
      humanHead(c, spec, Object.assign({ t: pose.t }, pose.head || {},
        { turn: Math.sign(k) * (0.45 + 0.1 * a) + (k === 5 ? 0.2 : 0), mouth: (pose.head?.mouth || 0) * (a % 2), dim: 0.12 + a * 0.06, noHairBack: true }), L);
      c.restore();
    }
  }

  // ------------------------------------------------------------ vanaras
  const VANARA = {
    hanuman: { skin: PAL.hanumanSkin, faceSkin: PAL.hanumanFace, hairPal: PAL.hanumanHair, headStyle: 'hanuman', garment: PAL.saree,
               crown: 'hanuman', tilak: 'hanuman', tail: true, iris: ['#3a1a04', '#9a5410', '#f0b040'], build: 'big' },
    angad: { skin: PAL.angadSkin, faceSkin: PAL.angadSkin, hairPal: PAL.angadHair, headStyle: 'angad', garment: PAL.green,
             crown: 'angad', tail: true, iris: ['#1e1206', '#5a3412', '#b07a3a'] },
    jambavan: { skin: PAL.bearSkin, hairPal: PAL.bearMane, headStyle: 'bear', garment: PAL.purple, uttariya: PAL.saffron,
                iris: ['#1a0e06', '#4a2a10', '#9a6a3a'], build: 'big', earrings: false },
  };

  /**
   * Seated vanara. pose: { t, mood, slump 0..1, headTilt, eyes, mouth, look, armUp 0..1, turn }
   */
  function vanara(c, kind, pose, L) {
    const spec = VANARA[kind];
    const up = pose.armUp || 0;
    const p = {
      stance: 'sit', t: pose.t, slump: pose.slump,
      armL: [1.75, -0.27], armR: [lerp(1.39, 0.15, up), lerp(0.27, -1.7, up)], handL: 'open', handR: up > 0.3 ? 'open' : 'open',
      head: { mood: pose.mood, eyes: pose.eyes, mouth: pose.mouth, look: pose.look, tilt: (pose.headTilt || 0) + (pose.slump || 0) * 0.12, turn: pose.turn || 0 },
    };
    if (kind === 'jambavan') {
      c.save(); c.translate(-150, -60); c.rotate(0.1);
      cel3(c, S.poly([[-7, -240], [7, -240], [8, 150], [-8, 150]]), PAL.wood, L, 3, 2, 2.6);
      const knob = new Path2D(); knob.ellipse(0, -248, 16, 20, 0, 0, TAU); cel3(c, knob, PAL.wood, L, 3, 2, 2.6);
      c.restore();
    }
    person(c, spec, p, L);
    if (kind === 'hanuman') gada(c, { x: 150, y: 60 }, -0.28, L);
  }

  function gada(c, pnt, ang, L) {
    c.save(); c.translate(pnt.x, pnt.y); c.rotate(ang);
    const Lg = rot(L, -ang);
    cel3(c, S.poly([[-8, 0], [8, 0], [7, -250], [-7, -250]]), PAL.wood, Lg, 3, 2, 2.6);
    goldBand(c, { x: 0, y: -60 }, Math.PI / 2, 22, 10, Lg);
    const head = new Path2D(); head.ellipse(0, -300, 52, 62, 0, 0, TAU);
    cel3(c, head, K.P.gold, Lg, 9, 4, 3);
    c.save(); c.clip(head); c.strokeStyle = K.P.gold[3]; c.lineWidth = 3;
    for (let k = -3; k <= 3; k++) { c.beginPath(); c.ellipse(0, -300, Math.abs(52 - Math.abs(k) * 14), 62, 0, 0, TAU); c.stroke(); }
    for (const y of [-330, -300, -270]) { c.beginPath(); c.moveTo(-60, y); c.quadraticCurveTo(0, y + 12, 60, y); c.stroke(); }
    c.restore();
    jewel(c, 0, -300, 9, K.P.red, Lg);
    const top = new Path2D(); top.moveTo(-12, -356); top.lineTo(0, -392); top.lineTo(12, -356); top.closePath();
    cel3(c, top, K.P.gold, Lg, 2, 1.5, 2.2);
    c.restore();
  }

  // ------------------------------------------------------------ Surya
  function surya(c, x, y, r, t, mood, L) {
    c.save(); c.translate(x, y);
    const glow = c.createRadialGradient(0, 0, r * 0.6, 0, 0, r * 2.6);
    glow.addColorStop(0, 'rgba(255,240,170,0.9)'); glow.addColorStop(0.4, 'rgba(255,170,60,0.35)'); glow.addColorStop(1, 'rgba(255,120,40,0)');
    c.fillStyle = glow; c.fillRect(-r * 2.6, -r * 2.6, r * 5.2, r * 5.2);
    c.save(); c.rotate(t * 0.15);
    for (let i = 0; i < 24; i++) {
      const a = (i / 24) * TAU, l = r * (1.32 + 0.12 * Math.sin(t * 3 + i * 1.7));
      c.fillStyle = i % 2 ? '#ffb02e' : '#ffd84a';
      c.beginPath(); c.moveTo(Math.cos(a - 0.11) * r * 0.95, Math.sin(a - 0.11) * r * 0.95);
      c.quadraticCurveTo(Math.cos(a) * l * 0.9, Math.sin(a) * l * 0.9, Math.cos(a + 0.04) * l, Math.sin(a + 0.04) * l);
      c.quadraticCurveTo(Math.cos(a + 0.05) * r, Math.sin(a + 0.05) * r, Math.cos(a + 0.11) * r * 0.95, Math.sin(a + 0.11) * r * 0.95);
      c.fill();
    }
    c.restore();
    const disc = c.createRadialGradient(-r * 0.25, -r * 0.3, r * 0.1, 0, 0, r);
    disc.addColorStop(0, '#fffbe0'); disc.addColorStop(0.6, '#ffd54a'); disc.addColorStop(1, '#ff9a1c');
    c.fillStyle = disc; c.beginPath(); c.arc(0, 0, r, 0, TAU); c.fill();
    c.strokeStyle = '#e06a10'; c.lineWidth = 5; c.stroke();
    // divine face: anime eyes, crown band, tilak
    const s = r / 170;
    c.save(); c.scale(s, s);
    const iris = ['#7a2a00', '#e07010', '#ffd060'];
    if (mood === 'fierce') {
      animeEye(c, -52, -4, -1, { w: 54, h: 40, iris, mood: 'angry' });
      animeEye(c, 52, -4, 1, { w: 54, h: 40, iris, mood: 'angry' });
      brow(c, -54, -40, -1, 'angry', '#b8500c', 54, 7); brow(c, 54, -40, 1, 'angry', '#b8500c', 54, 7);
      mouthShape(c, 0, 66, 0, 'angry', false);
    } else {
      animeEye(c, -52, 0, -1, { w: 54, h: 40, iris, closed: true });
      animeEye(c, 52, 0, 1, { w: 54, h: 40, iris, closed: true });
      brow(c, -54, -32, -1, 'calm', '#b8500c', 50, 6); brow(c, 54, -32, 1, 'calm', '#b8500c', 50, 6);
      c.strokeStyle = '#b8500c'; c.lineWidth = 5; c.lineCap = 'round';
      c.beginPath(); c.moveTo(-22, 62); c.quadraticCurveTo(0, 76, 22, 62); c.stroke();
    }
    c.fillStyle = 'rgba(255,90,60,0.25)'; for (const sd of [-1, 1]) { c.beginPath(); c.ellipse(sd * 80, 40, 22, 12, 0, 0, TAU); c.fill(); }
    jewel(c, 0, -76, 12, K.P.red, L);
    c.restore();
    c.restore();
  }

  global.Chars = { PAL, person, vanara, surya, crown, bow, sword, gada, humanHead, hand, animeEye, VANARA };
})(typeof window !== 'undefined' ? window : globalThis);
