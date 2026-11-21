/*
 * Characters for the Sampati–Jatayu story — round, friendly kids-anime style.
 *
 * Design rules: big round heads (~1/4 of the body), large round eyes (white oval,
 * black pupil, one bright highlight), simple smooth hair shapes with only a few
 * rounded points, tube limbs with round hands, flat colours with one soft shadow
 * tone, and a thick clean outline everywhere.
 *
 * person(c, spec, pose, L): origin = pelvis. Standing: head top ≈ -372, feet ≈ +285.
 * vanara(c, kind, pose, L): seated Hanuman / Angad / Jambavan, origin = seat.
 * Everything is a pure function of pose.t.
 */
(function (global) {
  'use strict';
  const S = global.Shapes, K = global.AnimeKit;
  const { TAU, lerp } = S;
  const { INK, cel3, rot } = K;
  const OUT = 4.5;                                   // outline weight

  const PAL = {
    blueSkin: ['#bcd6ff', '#6a9be6', '#4a78c4'],      // Shri Ram
    fairSkin: ['#fff1e2', '#ffd9bc', '#ecb994'],
    sitaSkin: ['#fff6ea', '#ffe0c8', '#efbd9c'],
    ravanaSkin: ['#e8b09a', '#c07c62', '#94543e'],
    pitambar: ['#fff5b8', '#ffd02e', '#e0a610'],
    saffron: ['#ffd8a0', '#ff9a30', '#e07414'],
    saree: ['#ffb0b8', '#ec3c5a', '#bc2040'],
    green: ['#bff0a0', '#58b84e', '#38903a'],
    purple: ['#dcb8f4', '#8a4cc4', '#64309a'],
    black: ['#7a7084', '#3a3242', '#24202c'],
    hair: ['#4a4a6a', '#24223a', '#16142a'],
    gold: ['#fff4c6', '#ffcc33', '#e0a010', '#a86a08'],
    red: K.P.red,
    wood: ['#e8bc86', '#b07a40', '#80541f'],
    steel: ['#ffffff', '#dfe6ef', '#a4b0c2'],
    white: ['#ffffff', '#f4f2ee', '#d8d4dc'],
    hanumanSkin: ['#ffe2b0', '#ffb45a', '#e08c34'],
    hanumanFace: ['#ffc0a0', '#ff8a68', '#e0603e'],
    hanumanHair: ['#ffe8a8', '#f4b444', '#d08a1c'],
    angadSkin: ['#f0d4b0', '#c8986a', '#a07448'],
    angadHair: ['#d8a070', '#9a6438', '#74441e'],
    bearSkin: ['#8a7a76', '#5a4c48', '#3e3230'],
    bearMane: ['#ffffff', '#f2eee8', '#d4ccc8'],
    muzzle: ['#fff6ea', '#ffe4c8', '#ecc4a0'],
  };

  // ------------------------------------------------------------ helpers
  const at = (p, a, l) => ({ x: p.x + Math.cos(a) * l, y: p.y + Math.sin(a) * l });
  const flat = (c, path, pal, L, lw = OUT, a = 5) => cel3(c, path, pal, L, a, 0, lw);
  const ell = (x, y, rx, ry, r = 0) => S.ellipse(x, y, rx, ry, r);

  /** Tube limb through points (cartoon arm / leg): outline then fill, round caps. */
  function tube(c, pts, w, pal) {
    const line = () => { c.beginPath(); c.moveTo(pts[0].x, pts[0].y); for (const p of pts.slice(1)) c.lineTo(p.x, p.y); };
    c.save();
    c.lineCap = 'round'; c.lineJoin = 'round';
    line(); c.strokeStyle = INK; c.lineWidth = w + OUT * 2; c.stroke();
    line(); c.strokeStyle = pal[1]; c.lineWidth = w; c.stroke();
    c.globalAlpha *= 0.45; c.translate(w * 0.22, w * 0.1);
    line(); c.strokeStyle = pal[2]; c.lineWidth = w * 0.3; c.stroke();
    c.restore();
  }

  function goldDot(c, x, y, r, L, gem) {
    flat(c, ell(x, y, r, r), PAL.gold, L, 2.4, 2);
    if (gem) { c.fillStyle = gem; c.beginPath(); c.arc(x, y, r * 0.55, 0, TAU); c.fill(); }
    c.fillStyle = '#fff'; c.beginPath(); c.arc(x - r * 0.35, y - r * 0.35, r * 0.25, 0, TAU); c.fill();
  }

  // ------------------------------------------------------------ face parts
  /** Big round cartoon eye. mood: calm | sad | angry | shock; closed → curved line. */
  function eye(c, x, y, o) {
    const rx = o.rx || 17, ry = o.ry || 21;
    if (o.closed) {
      c.strokeStyle = INK; c.lineWidth = 4; c.lineCap = 'round';
      c.beginPath(); c.arc(x, y - 4, rx * 0.9, 0.15 * Math.PI, 0.85 * Math.PI); c.stroke();
      if (o.female) { c.lineWidth = 3; c.beginPath(); c.moveTo(x + o.side * rx * 0.8, y + 2); c.lineTo(x + o.side * rx * 1.15, y - 2); c.stroke(); }
      return;
    }
    const e = ell(x, y, rx, ry);
    c.fillStyle = '#ffffff'; c.fill(e);
    c.save(); c.clip(e);
    const lx = (o.look || 0) * rx * 0.35, pk = o.mood === 'shock' ? 0.5 : 1;
    if (o.iris) { c.fillStyle = o.iris; c.beginPath(); c.ellipse(x + lx, y + ry * 0.12, rx * 0.62 * pk, ry * 0.66 * pk, 0, 0, TAU); c.fill(); }
    c.fillStyle = '#16121e'; c.beginPath(); c.ellipse(x + lx, y + ry * 0.15, rx * (o.iris ? 0.36 : 0.5) * pk, ry * (o.iris ? 0.42 : 0.55) * pk, 0, 0, TAU); c.fill();
    c.fillStyle = '#ffffff'; c.beginPath(); c.arc(x + lx - rx * 0.2, y - ry * 0.08, rx * 0.22 * (pk < 1 ? 0.6 : 1), 0, TAU); c.fill();
    if (o.mood === 'angry' || o.mood === 'sad') {             // lid cut across the top for mood
      const inner = o.side < 0 ? 1 : -1;
      const yIn = o.mood === 'angry' ? y - ry * 0.05 : y - ry * 0.95, yOut = o.mood === 'angry' ? y - ry * 0.95 : y - ry * 0.2;
      const yl = inner > 0 ? yOut : yIn, yr = inner > 0 ? yIn : yOut;
      c.fillStyle = o.skin; c.beginPath();
      c.moveTo(x - rx * 1.2, y - ry * 1.3); c.lineTo(x + rx * 1.2, y - ry * 1.3); c.lineTo(x + rx * 1.2, yr); c.lineTo(x - rx * 1.2, yl); c.closePath(); c.fill();
      c.strokeStyle = INK; c.lineWidth = 3.5; c.beginPath(); c.moveTo(x - rx * 1.2, yl); c.lineTo(x + rx * 1.2, yr); c.stroke();
    }
    c.restore();
    c.strokeStyle = INK; c.lineWidth = 3.6; c.stroke(e);
    if (o.female) {
      c.lineWidth = 3; c.lineCap = 'round';
      for (const k of [0, 1, 2]) { const a = -Math.PI / 2 + o.side * (0.6 + k * 0.32); c.beginPath(); c.moveTo(x + Math.cos(a) * rx, y + Math.sin(a) * ry); c.lineTo(x + Math.cos(a) * (rx + 9), y + Math.sin(a) * (ry + 9)); c.stroke(); }
    }
  }

  function brow(c, x, y, side, mood, color) {
    const tilt = mood === 'angry' ? 0.4 : mood === 'sad' ? -0.35 : 0;
    c.save(); c.translate(x, y); c.rotate(side * tilt);
    c.strokeStyle = color; c.lineWidth = 6; c.lineCap = 'round';
    c.beginPath(); c.moveTo(-12, 2); c.quadraticCurveTo(0, -5, 12, 2); c.stroke();
    c.restore();
  }

  function mouth(c, x, y, open, mood, expr) {
    c.lineCap = 'round'; c.lineJoin = 'round'; c.strokeStyle = INK;
    if (expr === 'shout' || expr === 'grin' || open > 0.08) {
      const w = expr === 'shout' ? 26 : expr === 'grin' ? 24 : 14, h = expr === 'shout' ? 30 : expr === 'grin' ? 20 : 8 + 16 * open;
      const m = new Path2D();
      if (expr === 'grin') { m.moveTo(x - w, y - 2); m.lineTo(x + w, y - 2); m.quadraticCurveTo(x + w, y + h, x, y + h); m.quadraticCurveTo(x - w, y + h, x - w, y - 2); }
      else { m.ellipse(x, y + h * 0.45, w * 0.8, h * 0.6, 0, 0, TAU); }
      c.fillStyle = '#7a1428'; c.fill(m);
      c.save(); c.clip(m);
      c.fillStyle = '#ff7a8a'; c.beginPath(); c.ellipse(x, y + h, w * 0.6, h * 0.45, 0, 0, TAU); c.fill();
      if (expr === 'grin' || expr === 'shout') { c.fillStyle = '#fff'; c.fillRect(x - w, y - 4, w * 2, 8); }
      c.restore();
      c.lineWidth = 3.4; c.stroke(m);
    } else if (expr === 'gasp') {
      c.fillStyle = '#7a1428'; c.beginPath(); c.ellipse(x, y + 4, 7, 9, 0, 0, TAU); c.fill(); c.lineWidth = 3; c.stroke();
    } else {
      c.lineWidth = 3.6; c.beginPath();
      if (mood === 'sad') { c.moveTo(x - 10, y + 6); c.quadraticCurveTo(x, y - 2, x + 10, y + 6); }
      else if (mood === 'angry') { c.moveTo(x - 12, y + 3); c.lineTo(x + 12, y + 1); }
      else { c.moveTo(x - 12, y); c.quadraticCurveTo(x, y + 10, x + 12, y); }
      c.stroke();
    }
  }

  function tears(c, xs, y, u, t) {
    c.save(); c.globalAlpha *= Math.min(1, u * 1.5);
    for (const x of xs) {
      const len = 20 + 40 * Math.min(1, u);
      c.fillStyle = '#a8dcff'; c.strokeStyle = '#4a8ad0'; c.lineWidth = 2.4;
      c.beginPath(); c.moveTo(x - 5, y); c.quadraticCurveTo(x - 7, y + len * 0.7, x, y + len); c.quadraticCurveTo(x + 7, y + len * 0.7, x + 5, y); c.closePath(); c.fill(); c.stroke();
      const k = (t * 1.2 + x * 0.01) % 1;
      c.save(); c.globalAlpha *= 1 - k; c.beginPath(); c.arc(x, y + len + 8 + k * 36, 5, 0, TAU); c.fill(); c.stroke(); c.restore();
    }
    c.restore();
  }

  // ------------------------------------------------------------ hair (smooth shapes)
  function hairBack(c, spec, t, L) {
    const pal = spec.hairPal || PAL.hair;
    const p = new Path2D();
    const sw = Math.sin(t * 1.4) * 6;
    if (spec.hair === 'long') {
      p.moveTo(-82, -20); p.bezierCurveTo(-100, 120, -96, 260, -70 + sw, 330);
      p.quadraticCurveTo(0, 350, 70 + sw, 330); p.bezierCurveTo(96, 260, 100, 120, 82, -20); p.closePath();
    } else if (spec.hair === 'crown') {
      p.moveTo(-84, -20); p.bezierCurveTo(-96, 60, -92, 110, -70 + sw, 140);
      for (let k = 0; k < 4; k++) p.quadraticCurveTo(-52 + k * 40 + sw, 168, -30 + k * 40 + sw, 142);
      p.bezierCurveTo(92, 110, 96, 60, 84, -20); p.closePath();
    } else if (spec.hair === 'jata') {
      p.moveTo(-82, -10); p.bezierCurveTo(-92, 50, -86, 90, -60 + sw * 0.5, 118);
      p.quadraticCurveTo(0, 96, 60 + sw * 0.5, 118); p.bezierCurveTo(86, 90, 92, 50, 82, -10); p.closePath();
    } else return;
    flat(c, p, pal, L, OUT, 8);
  }

  function hairFront(c, spec, t, L, turn) {
    const pal = spec.hairPal || PAL.hair;
    const s = turn * 14;
    const p = new Path2D();
    if (spec.female) {
      p.moveTo(-84, 10); p.bezierCurveTo(-90, -70, -40, -96, s, -88);
      p.bezierCurveTo(40, -96, 90, -70, 84, 10);
      p.quadraticCurveTo(70, -40, 20 + s, -50); p.quadraticCurveTo(4 + s, -40, s, -74);
      p.quadraticCurveTo(-4 + s, -40, -20 + s, -50); p.quadraticCurveTo(-70, -40, -84, 10); p.closePath();
    } else if (spec.hair === 'crown') {
      p.moveTo(-82, -8); p.bezierCurveTo(-88, -74, -40, -92, 0, -92); p.bezierCurveTo(40, -92, 88, -74, 82, -8);
      p.quadraticCurveTo(40, -44, 0, -46); p.quadraticCurveTo(-40, -44, -82, -8); p.closePath();
    } else if (spec.hair === 'none') {
      return;
    } else {
      p.moveTo(-84, 0); p.bezierCurveTo(-92, -78, -40, -100, 0, -100); p.bezierCurveTo(46, -100, 92, -78, 84, 0);
      p.quadraticCurveTo(76, -30, 58 + s, -34);
      p.quadraticCurveTo(48 + s, -22, 30 + s, -30); p.quadraticCurveTo(18 + s, -44, 6 + s, -24);
      p.quadraticCurveTo(-8 + s, -42, -24 + s, -28); p.quadraticCurveTo(-40 + s, -42, -56 + s, -26);
      p.quadraticCurveTo(-76, -30, -84, 0); p.closePath();
    }
    flat(c, p, pal, L, OUT, 6);
    c.save(); c.clip(p); c.strokeStyle = 'rgba(255,255,255,0.28)'; c.lineWidth = 9; c.lineCap = 'round';
    c.beginPath(); c.arc(-10, -40, 58, 1.15 * Math.PI, 1.45 * Math.PI); c.stroke(); c.restore();
  }

  function topKnot(c, t, L, pal) {
    flat(c, ell(0, -112, 34, 30), pal, L, OUT, 5);
    flat(c, S.poly([[-36, -96], [36, -96], [32, -84], [-32, -84]]), PAL.gold, L, 3, 2);
    goldDot(c, 0, -122, 7, L, '#e0243a');
  }

  function crown(c, kind, L) {
    const tall = kind === 'ravana' ? 1.35 : kind === 'angad' ? 0.8 : 1;
    const p = new Path2D();
    p.moveTo(-66, -60); p.lineTo(-62, -96 * tall); p.quadraticCurveTo(-40, -106 * tall, -26, -122 * tall);
    p.quadraticCurveTo(-12, -150 * tall, 0, -170 * tall); p.quadraticCurveTo(12, -150 * tall, 26, -122 * tall);
    p.quadraticCurveTo(40, -106 * tall, 62, -96 * tall); p.lineTo(66, -60); p.quadraticCurveTo(0, -78, -66, -60); p.closePath();
    flat(c, p, PAL.gold, L, OUT, 6);
    c.strokeStyle = PAL.gold[3]; c.lineWidth = 3;
    c.beginPath(); c.moveTo(-64, -76); c.quadraticCurveTo(0, -92, 64, -76); c.stroke();
    goldDot(c, 0, -112 * tall, 12, L, kind === 'ravana' ? '#e0243a' : '#1aa37a');
  }

  // ------------------------------------------------------------ head
  /**
   * Head at the current origin (centre of the face circle, radius ≈ 80).
   * pose: { turn -1..1, mood calm|sad|angry|shock, expr grin|shout|gasp, eyes 'closed', mouth, look, tilt, tears, dim, t, noHairBack }
   */
  function humanHead(c, spec, pose, L) {
    const t = pose.t || 0, mood = pose.mood || 'calm', turn = pose.turn ?? 0;
    c.save(); c.rotate(pose.tilt || 0); L = rot(L, -(pose.tilt || 0));
    const style = spec.headStyle, female = !!spec.female;
    const s = turn * 16;
    const skin = spec.faceSkin || spec.skin;
    if (!pose.noHairBack) hairBack(c, spec, t, L);

    if (style === 'bear') for (const sd of [-1, 1]) { flat(c, ell(sd * 60, -62, 26, 26), spec.skin, L); flat(c, ell(sd * 60, -60, 13, 13), PAL.muzzle, L, 0); }
    for (const sd of [-1, 1]) {
      if (style === 'hanuman' || style === 'angad') { flat(c, ell(sd * 84 - s * 0.3, 6, 22, 24), skin, L); flat(c, ell(sd * 84 - s * 0.3, 8, 12, 14), PAL.muzzle, L, 0); }
      else if (style !== 'bear') flat(c, ell(sd * 80 - s * 0.3, 10, 12, 16), spec.skin, L);
    }
    const head = new Path2D();
    if (style === 'bear') head.ellipse(0, 0, 86, 82, 0, 0, TAU);
    else { head.moveTo(-80, -10); head.bezierCurveTo(-82, 50, -40, 84, s * 0.4, 86); head.bezierCurveTo(40, 84, 82, 50, 80, -10); head.bezierCurveTo(80, -70, 40, -90, 0, -90); head.bezierCurveTo(-40, -90, -80, -70, -80, -10); head.closePath(); }
    const headPal = style ? (style === 'bear' ? spec.skin : (spec.hairPal || spec.skin)) : skin;
    flat(c, head, headPal, L, OUT, 7);
    if (style === 'hanuman' || style === 'angad') {
      const fa = new Path2D();
      fa.moveTo(s, -40); fa.bezierCurveTo(-30 + s, -60, -74 + s, -40, -66 + s, 10); fa.bezierCurveTo(-60 + s, 66, -20 + s, 80, s, 80);
      fa.bezierCurveTo(20 + s, 80, 60 + s, 66, 66 + s, 10); fa.bezierCurveTo(74 + s, -40, 30 + s, -60, s, -40); fa.closePath();
      flat(c, fa, skin, L, 3, 4);
    }
    if (style) flat(c, ell(s * 1.1, 46, 40, 30), PAL.muzzle, L, 3, 3);
    if (style === 'bear') {
      const bd = new Path2D(); bd.moveTo(-40 + s, 64); bd.quadraticCurveTo(s, 140, 40 + s, 64); bd.quadraticCurveTo(s, 84, -40 + s, 64); bd.closePath();
      flat(c, bd, PAL.bearMane, L, 3.4, 3);
    }
    c.fillStyle = 'rgba(255,110,130,0.35)';
    for (const sd of [-1, 1]) { c.beginPath(); c.ellipse(sd * 46 + s, 34, 14, 8, 0, 0, TAU); c.fill(); }
    const eyeY = style ? -6 : 2, ex = style === 'bear' ? 28 : 30;
    const eo = { rx: female ? 19 : 17, ry: female ? 23 : 21, mood, look: pose.look, female, iris: spec.iris, skin: skin[1], closed: pose.eyes === 'closed' };
    if (style === 'bear') { eo.rx = 12; eo.ry = 14; }
    eye(c, -ex + s, eyeY, Object.assign({ side: -1 }, eo));
    eye(c, ex + s, eyeY, Object.assign({ side: 1 }, eo));
    const bc = style === 'bear' ? '#ffffff' : spec.browColor || (spec.hairPal || PAL.hair)[1];
    brow(c, -ex + s, eyeY - 34, -1, mood, bc);
    brow(c, ex + s, eyeY - 34, 1, mood, bc);
    if (style === 'bear') { c.fillStyle = '#221a1c'; c.beginPath(); c.ellipse(s * 1.1, 34, 13, 9, 0, 0, TAU); c.fill(); }
    else if (style) { c.fillStyle = '#6a3424'; for (const sd of [-1, 1]) { c.beginPath(); c.ellipse(s * 1.1 + sd * 7, 34, 3.5, 2.6, 0, 0, TAU); c.fill(); } }
    else { c.strokeStyle = skin[2]; c.lineWidth = 3.2; c.lineCap = 'round'; c.beginPath(); c.moveTo(s * 1.1 - 3, 22); c.quadraticCurveTo(s * 1.1 + 4, 30, s * 1.1 - 2, 34); c.stroke(); }
    if (spec.mustache) {
      const m = s * 1.1;
      c.fillStyle = INK; c.beginPath();
      c.moveTo(m, 44); c.bezierCurveTo(m - 14, 36, m - 34, 48, m - 40, 34); c.bezierCurveTo(m - 36, 56, m - 12, 52, m, 50);
      c.bezierCurveTo(m + 12, 52, m + 36, 56, m + 40, 34); c.bezierCurveTo(m + 34, 48, m + 14, 36, m, 44); c.fill();
    }
    mouth(c, s * 1.1, style ? 58 : 52, pose.mouth || 0, mood, pose.expr);
    if (pose.tears) tears(c, [-ex + s, ex + s], eyeY + 22, pose.tears, t);

    if (!style) hairFront(c, spec, t, L, turn);
    else if (style !== 'bear') {
      const cap = new Path2D();
      cap.moveTo(-60, -60); cap.quadraticCurveTo(-40, -104, -14, -84); cap.quadraticCurveTo(0, -112, 14, -84);
      cap.quadraticCurveTo(40, -104, 60, -60); cap.quadraticCurveTo(0, -76, -60, -60); cap.closePath();
      flat(c, cap, spec.hairPal || spec.skin, L, OUT, 4);
    }
    if (spec.hair === 'jata') topKnot(c, t, L, spec.hairPal || PAL.hair);
    if (spec.crown) crown(c, spec.crown, L);
    const fx = s * 0.8, fy = style ? -52 : -40;
    if (spec.tilak === 'vaishnav') {
      c.strokeStyle = '#ffffff'; c.lineWidth = 4; c.lineCap = 'round';
      c.beginPath(); c.moveTo(fx - 8, fy - 14); c.lineTo(fx - 4, fy + 6); c.lineTo(fx + 4, fy + 6); c.lineTo(fx + 8, fy - 14); c.stroke();
      c.strokeStyle = '#e0242e'; c.lineWidth = 4.5; c.beginPath(); c.moveTo(fx, fy - 14); c.lineTo(fx, fy + 4); c.stroke();
    } else if (spec.tilak === 'bindi') { c.fillStyle = '#e0142a'; c.beginPath(); c.arc(fx, fy + 8, 5.5, 0, TAU); c.fill(); }
    else if (spec.tilak === 'shaiv') {
      c.strokeStyle = '#ffffff'; c.lineWidth = 3.6;
      for (const dy of [-10, -1, 8]) { c.beginPath(); c.moveTo(fx - 18, fy + dy); c.quadraticCurveTo(fx, fy + dy - 4, fx + 18, fy + dy); c.stroke(); }
    } else if (spec.tilak === 'hanuman') { c.strokeStyle = '#e0142a'; c.lineWidth = 6; c.lineCap = 'round'; c.beginPath(); c.moveTo(fx, fy - 10); c.lineTo(fx, fy + 12); c.stroke(); }
    if (female) goldDot(c, s * 0.4, -70, 7, L, '#e0243a');
    if (spec.earrings !== false && style !== 'bear') {
      for (const sd of [-1, 1]) {
        c.save(); c.translate(sd * (style ? 86 : 80) - s * 0.3, 30); c.rotate(Math.sin(t * 2.4 + sd) * 0.15);
        flat(c, ell(0, 8, 8, 11), PAL.gold, L, 3, 2);
        c.restore();
      }
    }
    if (spec.nosering) { c.strokeStyle = PAL.gold[2]; c.lineWidth = 3; c.beginPath(); c.arc(s * 1.1 + 9, 36, 7, -0.4, 2.3); c.stroke(); }
    if (pose.dim) { c.fillStyle = `rgba(40,10,40,${pose.dim})`; c.fill(head); }
    c.restore();
  }

  // ------------------------------------------------------------ hands & props
  function hand(c, w, ang, skin, L, side) {
    c.save(); c.translate(w.x, w.y); c.rotate(ang);
    flat(c, ell(10, -12 * side, 8, 6, -0.6 * side), skin, rot(L, -ang), 3, 2);
    flat(c, ell(12, 0, 17, 15), skin, rot(L, -ang), OUT * 0.8, 3);
    c.restore();
  }

  function bow(c, hnd, ang, L) {
    c.save(); c.translate(hnd.x + 16, hnd.y); c.rotate(ang);
    const Lb = rot(L, -ang);
    const b = new Path2D();
    b.moveTo(-4, -210); b.bezierCurveTo(-70, -140, -58, -40, -10, 0); b.bezierCurveTo(-58, 40, -70, 140, -4, 210);
    b.lineTo(8, 202); b.bezierCurveTo(-50, 140, -42, 40, 4, 0); b.bezierCurveTo(-42, -40, -50, -140, 8, -202); b.closePath();
    flat(c, b, PAL.wood, Lb, 3.6, 3);
    c.strokeStyle = '#ffffff'; c.lineWidth = 2.4; c.beginPath(); c.moveTo(2, -206); c.lineTo(2, 206); c.stroke();
    c.restore();
  }

  function sword(c, hnd, swing, L) {
    c.save(); c.translate(hnd.x, hnd.y); c.rotate(swing);
    const Ls = rot(L, -swing);
    const bl = new Path2D();
    bl.moveTo(12, -10); bl.quadraticCurveTo(150, -46, 270, -12); bl.quadraticCurveTo(180, 8, 12, 12); bl.closePath();
    flat(c, bl, PAL.steel, Ls, 3.6, 3);
    flat(c, S.poly([[0, -26], [14, -26], [14, 26], [0, 26]]), PAL.gold, Ls, 3, 2);
    flat(c, S.poly([[-34, -8], [0, -8], [0, 8], [-34, 8]]), PAL.black, Ls, 3, 2);
    c.restore();
  }

  function gada(c, pnt, ang, L) {
    c.save(); c.translate(pnt.x, pnt.y); c.rotate(ang);
    const Lg = rot(L, -ang);
    flat(c, S.poly([[-9, 0], [9, 0], [8, -240], [-8, -240]]), PAL.wood, Lg, 3.6, 3);
    flat(c, ell(0, -290, 54, 62), PAL.gold, Lg, OUT, 9);
    c.strokeStyle = PAL.gold[3]; c.lineWidth = 3.5;
    for (const y of [-318, -290, -262]) { c.beginPath(); c.moveTo(-50, y); c.quadraticCurveTo(0, y + 12, 50, y); c.stroke(); }
    goldDot(c, 0, -360, 10, Lg);
    c.restore();
  }

  // ------------------------------------------------------------ body
  /**
   * pose: { stance:'stand'|'kneel'|'sit', armL:[a1,a2], armR:[a1,a2], head:{...}, t,
   *         props:{bow:'left'|'right', bowAngle, quiver, sword}, slump }
   */
  function person(c, spec, pose, L) {
    const t = pose.t || 0;
    const br = Math.sin(t * 2.2) * 2.5 * (pose.breathe ?? 1);
    const female = !!spec.female, big = spec.build === 'big' ? 1.1 : 1;
    const shX = (female ? 58 : 70) * big, shY = -176 + br;
    const shL = { x: -shX, y: shY }, shR = { x: shX, y: shY };
    const skin = spec.skin, garment = spec.garment;
    const wind = Math.sin(t * 1.7) * 8;
    const wf = female ? 0.8 : 1;

    if (pose.props?.quiver) {
      c.save(); c.translate(54, -150); c.rotate(0.45);
      flat(c, S.poly([[-17, -80], [17, -80], [14, 76], [-14, 76]]), PAL.wood, rot(L, -0.45), 3.6, 3);
      for (let k = -1; k <= 1; k++) { c.fillStyle = '#ffffff'; c.strokeStyle = INK; c.lineWidth = 2.5; c.beginPath(); c.moveTo(k * 9, -80); c.lineTo(k * 9 - 6, -102); c.lineTo(k * 9 + 6, -102); c.closePath(); c.fill(); c.stroke(); }
      c.restore();
    }
    if (spec.tail) {
      const sw = Math.sin(t * 1.3) * 24;
      const tp = [];
      for (let i = 0; i <= 16; i++) tp.push(S.cubic({ x: -30, y: 30 }, { x: -230, y: 90 }, { x: -250 + sw * 0.5, y: -110 }, { x: -170 + sw, y: -170 }, i / 16));
      tube(c, tp, 22, spec.skin);
      flat(c, ell(-170 + sw, -186, 22, 26), spec.hairPal || spec.skin, L, OUT, 4);
    }
    c.save(); c.translate(0, -296 + br + (pose.slump || 0) * 8); c.scale(1.22, 1.22); c.rotate(pose.head?.tilt || 0);
    hairBack(c, spec, t, L);
    c.restore();
    if (spec.uttariya) for (const sd of [-1, 1]) {
      const p = new Path2D(); p.moveTo(sd * shX, shY); p.quadraticCurveTo(sd * (shX + 26) + wind, shY + 110, sd * (shX + 16) + wind, shY + 210);
      p.lineTo(sd * (shX - 4) + wind, shY + 200); p.quadraticCurveTo(sd * (shX + 4), shY + 100, sd * (shX - 18), shY + 4); p.closePath();
      flat(c, p, spec.uttariya, L, 3.6, 4);
    }

    if (pose.stance === 'sit') {
      const lap = new Path2D();
      lap.moveTo(-56, -10); lap.bezierCurveTo(-150, -4, -170, 70, -110, 90); lap.quadraticCurveTo(0, 110, 110, 90);
      lap.bezierCurveTo(170, 70, 150, -4, 56, -10); lap.closePath();
      flat(c, lap, garment, L, OUT, 8);
      for (const sd of [-1, 1]) flat(c, ell(sd * 64, 84, 30, 14), skin, L, OUT * 0.8, 3);
    } else if (pose.stance === 'kneel') {
      tube(c, [{ x: 50, y: 40 }, { x: 70, y: 70 }, { x: 72, y: 150 }], 30, skin);
      flat(c, ell(86, 158, 26, 12), skin, L, OUT * 0.8, 3);
      const d = new Path2D();
      d.moveTo(-58, -14); d.bezierCurveTo(-96, 40, -100, 120, -80, 160); d.lineTo(0, 164); d.quadraticCurveTo(10, 90, 84, 78);
      d.quadraticCurveTo(100, 30, 58, -14); d.closePath();
      flat(c, d, garment, L, OUT, 8);
      c.strokeStyle = PAL.gold[2]; c.lineWidth = 6; c.beginPath(); c.moveTo(-78, 154); c.lineTo(-4, 158); c.stroke();
    } else {
      for (const sd of [-1, 1]) {
        tube(c, [{ x: sd * 30, y: 150 }, { x: sd * 32, y: 262 }], 30, skin);
        flat(c, ell(sd * 42, 274, 28, 13), skin, L, OUT * 0.8, 3);
        c.strokeStyle = PAL.gold[2]; c.lineWidth = 5; c.beginPath(); c.moveTo(sd * 32 - 16, 252); c.lineTo(sd * 32 + 16, 252); c.stroke();
      }
      const d = new Path2D();
      if (female) { d.moveTo(-50, -14); d.bezierCurveTo(-66, 90, -80, 200, -84, 262); d.quadraticCurveTo(0, 278, 84, 262); d.bezierCurveTo(80, 200, 66, 90, 50, -14); d.closePath(); }
      else { d.moveTo(-58, -14); d.bezierCurveTo(-82, 60, -82, 140, -72, 196); d.quadraticCurveTo(-36, 206, -6, 194); d.lineTo(0, 150); d.lineTo(6, 194); d.quadraticCurveTo(36, 206, 72, 196); d.bezierCurveTo(82, 140, 82, 60, 58, -14); d.closePath(); }
      flat(c, d, garment, L, OUT, 8);
      c.save(); c.clip(d); c.strokeStyle = PAL.gold[2]; c.lineWidth = 9;
      c.beginPath(); c.moveTo(-100, female ? 254 : 190); c.quadraticCurveTo(0, female ? 270 : 204, 100, female ? 254 : 190); c.stroke();
      c.strokeStyle = garment[2]; c.lineWidth = 3; c.beginPath(); c.moveTo(0, 10); c.lineTo(0, female ? 250 : 140); c.stroke();
      c.restore();
    }

    const tor = new Path2D();
    tor.moveTo(shL.x + 10, shY - 10); tor.quadraticCurveTo(0, shY - 22, shR.x - 10, shY - 10);
    tor.quadraticCurveTo(shR.x + 8, shY, shR.x + 4, shY + 30);
    tor.bezierCurveTo(shR.x, -80, 54 * wf, -40, 54 * wf, -6);
    tor.lineTo(-54 * wf, -6);
    tor.bezierCurveTo(-54 * wf, -40, shL.x, -80, shL.x - 4, shY + 30);
    tor.quadraticCurveTo(shL.x - 8, shY, shL.x + 10, shY - 10); tor.closePath();
    flat(c, tor, female ? spec.blouse : skin, L, OUT, 8);
    if (female) {
      const pl = new Path2D(); pl.moveTo(shL.x + 4, shY - 6); pl.lineTo(shL.x + 40, shY - 14); pl.lineTo(48, -6); pl.lineTo(10, -6); pl.closePath();
      flat(c, pl, garment, L, 3.6, 4);
      c.strokeStyle = PAL.gold[2]; c.lineWidth = 5; c.beginPath(); c.moveTo(shL.x + 40, shY - 14); c.lineTo(48, -6); c.stroke();
    } else {
      c.strokeStyle = '#ffffff'; c.lineWidth = 3.4; c.lineCap = 'round';
      c.beginPath(); c.moveTo(shL.x + 18, shY - 4); c.quadraticCurveTo(0, -80, 48, -10); c.stroke();
    }
    flat(c, S.poly([[-56, -24], [56, -24], [58, 4], [-58, 4]]), spec.sash || PAL.gold, L, 3.6, 3);
    goldDot(c, 0, -10, 9, L, '#e0243a');
    c.strokeStyle = INK; c.lineWidth = 9; c.lineCap = 'round'; c.beginPath(); c.arc(0, shY - 30, 44, 0.25 * Math.PI, 0.75 * Math.PI); c.stroke();
    c.strokeStyle = PAL.gold[1]; c.lineWidth = 5; c.stroke();
    goldDot(c, 0, shY + 16, 8, L, '#e0243a');
    if (spec.uttariya) {
      const u = new Path2D(); u.moveTo(shL.x - 6, shY - 8); u.quadraticCurveTo(0, shY + 34, shR.x + 6, shY - 8);
      u.lineTo(shR.x, shY + 14); u.quadraticCurveTo(0, shY + 56, shL.x, shY + 14); u.closePath();
      flat(c, u, spec.uttariya, L, 3.6, 3);
    }

    const ua = 92, fa = 86;
    const arm = (sh, ang, side) => {
      const el = at(sh, ang[0], ua), wr = at(el, ang[0] + ang[1], fa);
      tube(c, [sh, el, wr], female ? 24 : 30 * big, skin);
      const bandAt = at(sh, ang[0], 40);
      const n = { x: -Math.sin(ang[0]) * 15, y: Math.cos(ang[0]) * 15 };
      c.strokeStyle = PAL.gold[2]; c.lineWidth = 6; c.lineCap = 'round';
      c.beginPath(); c.moveTo(bandAt.x - n.x, bandAt.y - n.y); c.lineTo(bandAt.x + n.x, bandAt.y + n.y); c.stroke();
      if (female) flat(c, ell(sh.x, sh.y + 8, 22, 20), spec.blouse, L, OUT * 0.8, 3);
      hand(c, wr, ang[0] + ang[1], skin, L, side);
      return wr;
    };
    const hl = arm(shL, pose.armL || [1.85, -0.2], -1);
    const hr = arm(shR, pose.armR || [1.29, 0.2], 1);

    flat(c, S.poly([[-18, shY - 34], [18, shY - 34], [20, shY - 6], [-20, shY - 6]]), skin, L, 3.6, 3);
    c.save(); c.translate(0, -296 + br + (pose.slump || 0) * 8); c.scale(1.22, 1.22);
    if (spec.extraHeads) ravanaHeads(c, spec, pose, L);
    humanHead(c, spec, Object.assign({ t, noHairBack: true }, pose.head || {}), L);
    c.restore();

    if (pose.props?.bow) bow(c, pose.props.bow === 'left' ? hl : hr, pose.props.bowAngle ?? -0.2, L);
    if (pose.props?.sword !== undefined) sword(c, hr, pose.props.sword, L);
    return { hl, hr };
  }

  function ravanaHeads(c, spec, pose, L) {
    const order = [4, -4, 3, -3, 2, -2, 1, -1, 5];
    for (const k of order) {
      const a = Math.abs(k);
      c.save();
      const ang = (k === 5 ? 0 : Math.sign(k)) * (0.32 + 0.3 * a);
      c.translate(k === 5 ? 0 : Math.sin(ang) * 150, k === 5 ? -96 : 36 - Math.cos(ang) * 50);
      const s = 0.62 - a * 0.03; c.scale(s, s);
      humanHead(c, Object.assign({}, spec, { hair: 'none' }), Object.assign({ t: pose.t }, pose.head || {},
        { turn: Math.sign(k) * 0.5, mouth: (pose.head?.mouth || 0) * (a % 2), dim: 0.1 + a * 0.05, noHairBack: true }), L);
      c.restore();
    }
  }

  // ------------------------------------------------------------ vanaras
  const VANARA = {
    hanuman: { skin: PAL.hanumanSkin, faceSkin: PAL.hanumanFace, hairPal: PAL.hanumanHair, headStyle: 'hanuman', garment: PAL.saree,
               crown: 'hanuman', tilak: 'hanuman', tail: true, build: 'big' },
    angad: { skin: PAL.angadSkin, faceSkin: PAL.muzzle, hairPal: PAL.angadHair, headStyle: 'angad', garment: PAL.green, crown: 'angad', tail: true },
    jambavan: { skin: PAL.bearSkin, hairPal: PAL.bearSkin, headStyle: 'bear', garment: PAL.purple, uttariya: PAL.saffron, build: 'big', earrings: false },
  };

  function vanara(c, kind, pose, L) {
    const spec = VANARA[kind], up = pose.armUp || 0;
    if (kind === 'jambavan') {
      c.save(); c.translate(-150, -60); c.rotate(0.1);
      flat(c, S.poly([[-8, -240], [8, -240], [9, 150], [-9, 150]]), PAL.wood, L, 3.6, 3);
      flat(c, ell(0, -250, 18, 20), PAL.wood, L, 3.6, 3);
      c.restore();
    }
    person(c, spec, {
      stance: 'sit', t: pose.t, slump: pose.slump,
      armL: [1.75, -0.27], armR: [lerp(1.39, 0.15, up), lerp(0.27, -1.7, up)],
      head: { mood: pose.mood, eyes: pose.eyes, mouth: pose.mouth, expr: pose.expr, look: pose.look, tilt: (pose.headTilt || 0) + (pose.slump || 0) * 0.1, turn: pose.turn || 0 },
    }, L);
    if (kind === 'hanuman') gada(c, { x: 150, y: 60 }, -0.28, L);
  }

  // ------------------------------------------------------------ Surya
  function surya(c, x, y, r, t, mood, L) {
    c.save(); c.translate(x, y);
    const glow = c.createRadialGradient(0, 0, r * 0.6, 0, 0, r * 2.6);
    glow.addColorStop(0, 'rgba(255,240,170,0.9)'); glow.addColorStop(0.4, 'rgba(255,170,60,0.35)'); glow.addColorStop(1, 'rgba(255,120,40,0)');
    c.fillStyle = glow; c.fillRect(-r * 2.6, -r * 2.6, r * 5.2, r * 5.2);
    c.save(); c.rotate(t * 0.15);
    for (let i = 0; i < 16; i++) {
      const a = (i / 16) * TAU, l = r * (1.3 + 0.08 * Math.sin(t * 3 + i * 1.7));
      const p = new Path2D(); p.moveTo(Math.cos(a - 0.14) * r * 0.95, Math.sin(a - 0.14) * r * 0.95);
      p.quadraticCurveTo(Math.cos(a) * l * 1.05, Math.sin(a) * l * 1.05, Math.cos(a + 0.14) * r * 0.95, Math.sin(a + 0.14) * r * 0.95); p.closePath();
      c.fillStyle = i % 2 ? '#ffb02e' : '#ffd84a'; c.fill(p); c.strokeStyle = '#e07a10'; c.lineWidth = 4; c.stroke(p);
    }
    c.restore();
    c.fillStyle = '#ffd84a'; c.beginPath(); c.arc(0, 0, r, 0, TAU); c.fill();
    c.strokeStyle = '#e07a10'; c.lineWidth = 6; c.stroke();
    const s = r / 170;
    c.save(); c.scale(s, s);
    if (mood === 'fierce') {
      eye(c, -52, -6, { rx: 26, ry: 30, mood: 'angry', side: -1, skin: '#ffd84a' });
      eye(c, 52, -6, { rx: 26, ry: 30, mood: 'angry', side: 1, skin: '#ffd84a' });
      mouth(c, 0, 66, 0, 'angry');
    } else {
      eye(c, -52, 0, { rx: 26, ry: 30, closed: true, side: -1 }); eye(c, 52, 0, { rx: 26, ry: 30, closed: true, side: 1 });
      mouth(c, 0, 60, 0, 'calm');
    }
    c.fillStyle = 'rgba(255,90,60,0.35)'; for (const sd of [-1, 1]) { c.beginPath(); c.ellipse(sd * 86, 40, 24, 13, 0, 0, TAU); c.fill(); }
    goldDot(c, 0, -76, 13, L, '#e0243a');
    c.restore();
    c.restore();
  }

  global.Chars = { PAL, person, vanara, surya, crown, bow, sword, gada, humanHead, hand, VANARA };
})(typeof window !== 'undefined' ? window : globalThis);
