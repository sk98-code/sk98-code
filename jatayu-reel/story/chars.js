/*
 * Characters for the Sampati–Jatayu story, drawn in the same anime cel style.
 * Every function draws in local coords; poses are plain numbers so scenes can
 * animate them as pure functions of time.
 *
 * person(): origin = pelvis, ~600 units tall standing (head top ≈ -330, feet ≈ +265)
 * vanara(): origin = seat, seated figure ~430 units tall
 */
(function (global) {
  'use strict';
  const S = global.Shapes, K = global.AnimeKit;
  const { TAU, lerp, smooth, hash } = S;
  const { INK, cel3, rot } = K;

  const PAL = {
    blueSkin: ['#9fc4ff', '#3f74c9', '#244a8f'],      // Shri Ram
    fairSkin: ['#ffe6cc', '#f2c49b', '#c98f66'],
    sitaSkin: ['#fff0dc', '#f6d0ad', '#d29d78'],
    ravanaSkin: ['#c58a72', '#8a4f3e', '#5a2e24'],
    pitambar: ['#fff2a0', '#f6c21c', '#c48a08'],     // yellow silk
    saffron: ['#ffcf8a', '#f4891c', '#b9560a'],
    saree: ['#ff9a8a', '#d8283c', '#8f1226'],
    green: ['#a8e48a', '#3f9a3a', '#1f5e22'],
    purple: ['#c69ae8', '#6a2f9a', '#3b1661'],
    black: ['#6b6070', '#2a2230', '#120d16'],
    hair: ['#5a4a62', '#1d1622', '#0b080d'],
    gold: K.P.gold,
    red: K.P.red,
    wood: ['#d6a46a', '#8f5a2c', '#56330f'],
    furGold: ['#ffd98a', '#e0a038', '#9c6318'],
    furBrown: ['#d0a070', '#8b5a32', '#553319'],
    furBear: ['#6d5a52', '#2f2420', '#140e0c'],
    muzzle: ['#ffe9d0', '#f3c9a0', '#c4906a'],
    hanumanFace: ['#ff9f80', '#e2603c', '#a33a1e'],
    white: ['#ffffff', '#ece8e2', '#bdb8c4'],
    steel: ['#ffffff', '#c9d4e2', '#7b8aa0'],
  };

  // ---------- small helpers ----------
  function limb(c, a, b, w0, w1, pal, L, lw = 3.5) {
    const m1 = { x: lerp(a.x, b.x, 0.33), y: lerp(a.y, b.y, 0.33) }, m2 = { x: lerp(a.x, b.x, 0.66), y: lerp(a.y, b.y, 0.66) };
    cel3(c, S.ribbon(a, m1, m2, b, w0, w1, 10), pal, L, 5, 2.5, lw);
  }
  const at = (p, a, l) => ({ x: p.x + Math.cos(a) * l, y: p.y + Math.sin(a) * l });

  function band(c, p, a, w, h, pal, L) {             // armlet / bracelet
    c.save(); c.translate(p.x, p.y); c.rotate(a);
    cel3(c, S.ellipse(0, 0, h, w), pal, rot(L, -a), 2, 1.5, 2.5);
    c.restore();
  }

  // ---------- eyes / face ----------
  function eye(c, x, y, s, mood, iris, look, flip) {
    c.save(); c.translate(x, y); c.scale(flip ? -s : s, s);
    if (mood === 'closed') {
      c.strokeStyle = INK; c.lineWidth = 3.2; c.lineCap = 'round';
      c.beginPath(); c.arc(0, -4, 11, 0.15 * Math.PI, 0.85 * Math.PI); c.stroke();
      c.restore(); return;
    }
    const lid = new Path2D();
    lid.moveTo(-14, 0); lid.quadraticCurveTo(-2, -12, 14, -4); lid.quadraticCurveTo(4, 9, -14, 0); lid.closePath();
    c.fillStyle = '#fffaf2'; c.fill(lid);
    c.save(); c.clip(lid);
    const lx = (flip ? -1 : 1) * (look || 0) * 4;
    c.fillStyle = iris; c.beginPath(); c.ellipse(lx + 1, -1, 7, 8.5, 0, 0, TAU); c.fill();
    c.fillStyle = INK; c.beginPath(); c.ellipse(lx + 1, -1, 3.4, 4.6, 0, 0, TAU); c.fill();
    c.fillStyle = '#fff'; c.beginPath(); c.arc(lx - 2, -4, 2.4, 0, TAU); c.fill();
    c.restore();
    c.strokeStyle = INK; c.lineWidth = 3.6; c.lineCap = 'round';
    c.beginPath(); c.moveTo(-15, 1); c.quadraticCurveTo(-2, -13, 15, -4); c.stroke();     // upper lash line
    c.lineWidth = 1.4; c.beginPath(); c.moveTo(-10, 4); c.quadraticCurveTo(2, 8, 12, 0); c.stroke();
    c.restore();
  }

  function brows(c, turn, mood, color, thick = 4) {
    c.strokeStyle = color; c.lineWidth = thick; c.lineCap = 'round';
    const tilt = mood === 'angry' ? 0.35 : mood === 'sad' ? -0.3 : 0.05;
    for (const sd of [-1, 1]) {
      const x = sd * 24 + turn * 10;
      c.save(); c.translate(x, -24); c.rotate(sd * tilt);
      c.beginPath(); c.moveTo(-12 * sd, 2); c.quadraticCurveTo(0, -4, 12 * sd, 0); c.stroke();
      c.restore();
    }
  }

  function mouth(c, turn, open, mood) {
    const x = turn * 8;
    c.strokeStyle = INK; c.lineCap = 'round';
    if (open > 0.05) {
      c.fillStyle = '#7a1e2a';
      c.beginPath(); c.ellipse(x, 30, 9, 2 + 7 * open, 0, 0, TAU); c.fill();
      c.lineWidth = 2.5; c.stroke();
    } else {
      c.lineWidth = 2.6; c.beginPath();
      if (mood === 'sad') { c.moveTo(x - 9, 33); c.quadraticCurveTo(x, 27, x + 9, 33); }
      else if (mood === 'angry') { c.moveTo(x - 11, 31); c.lineTo(x + 11, 29); }
      else { c.moveTo(x - 8, 29); c.quadraticCurveTo(x, 34, x + 8, 29); }
      c.stroke();
    }
  }

  /** Human face (head-local, centre of face at 0,0, ~ 96 x 116). */
  function face(c, spec, pose, L) {
    const turn = pose.turn || 0, mood = pose.mood || 'calm';
    const fp = new Path2D();
    fp.moveTo(-44, -30); fp.quadraticCurveTo(-46, 20, -26, 42); fp.quadraticCurveTo(0, 62, 26, 42);
    fp.quadraticCurveTo(46, 20, 44, -30); fp.quadraticCurveTo(0, -70, -44, -30); fp.closePath();
    // ears + earrings
    for (const sd of [-1, 1]) {
      cel3(c, S.ellipse(sd * 45, 0, 8, 14), spec.skin, L, 2, 1.5, 2.5);
      if (spec.earrings !== false) {
        const sw = Math.sin((pose.t || 0) * 3 + sd) * 0.15;
        c.save(); c.translate(sd * 46, 14); c.rotate(sw);
        cel3(c, S.ellipse(0, 12, 7, 11), K.P.gold, L, 2, 1.5, 2.2);
        c.restore();
      }
    }
    cel3(c, fp, spec.skin, L, 7, 3, 3.6);
    // blush
    c.fillStyle = 'rgba(255,110,110,0.18)';
    for (const sd of [-1, 1]) { c.beginPath(); c.ellipse(sd * 26 + turn * 6, 18, 9, 5, 0, 0, TAU); c.fill(); }
    const eyeMood = pose.eyes || 'open';
    eye(c, -21 + turn * 10, -4, spec.eyeScale || 1, eyeMood, spec.iris || '#3a2414', pose.look, true);
    eye(c, 21 + turn * 10, -4, spec.eyeScale || 1, eyeMood, spec.iris || '#3a2414', pose.look, false);
    brows(c, turn, mood, spec.browColor || '#1d1622', spec.browThick || 4);
    // nose
    c.strokeStyle = spec.skin[2]; c.lineWidth = 2.4; c.lineCap = 'round';
    c.beginPath(); c.moveTo(turn * 14 + 2, 4); c.quadraticCurveTo(turn * 14 + 6, 15, turn * 14 - 2, 17); c.stroke();
    if (spec.mustache) {
      c.fillStyle = INK; c.beginPath();
      c.moveTo(turn * 8, 22); c.bezierCurveTo(turn * 8 - 18, 16, turn * 8 - 30, 26, turn * 8 - 36, 14);
      c.bezierCurveTo(turn * 8 - 30, 34, turn * 8 - 12, 28, turn * 8, 26);
      c.bezierCurveTo(turn * 8 + 12, 28, turn * 8 + 30, 34, turn * 8 + 36, 14);
      c.bezierCurveTo(turn * 8 + 30, 26, turn * 8 + 18, 16, turn * 8, 22); c.fill();
    }
    mouth(c, turn, pose.mouth || 0, mood);
    if (spec.nosering) { c.strokeStyle = K.P.gold[1]; c.lineWidth = 2.2; c.beginPath(); c.arc(turn * 14 + 8, 20, 6, -0.5, 2.4); c.stroke(); }
    if (spec.tilak === 'vaishnav') {
      c.strokeStyle = '#fff5d8'; c.lineWidth = 3; c.beginPath();
      c.moveTo(turn * 10 - 7, -52); c.lineTo(turn * 10 - 4, -30); c.lineTo(turn * 10 + 4, -30); c.lineTo(turn * 10 + 7, -52); c.stroke();
      c.strokeStyle = '#e0242e'; c.lineWidth = 3.4; c.beginPath(); c.moveTo(turn * 10, -54); c.lineTo(turn * 10, -32); c.stroke();
    } else if (spec.tilak === 'bindi') {
      c.fillStyle = '#d4142a'; c.beginPath(); c.arc(turn * 10, -36, 4.5, 0, TAU); c.fill();
    } else if (spec.tilak === 'shaiv') {
      c.strokeStyle = '#f4f0e6'; c.lineWidth = 3;
      for (const dy of [-46, -39, -32]) { c.beginPath(); c.moveTo(turn * 10 - 16, dy); c.lineTo(turn * 10 + 16, dy); c.stroke(); }
      c.fillStyle = '#d4142a'; c.beginPath(); c.arc(turn * 10, -39, 4, 0, TAU); c.fill();
    }
  }

  function hairBack(c, spec, L) {
    if (spec.hair === 'long') {
      const p = new Path2D();
      p.moveTo(-50, -40); p.quadraticCurveTo(-78, 120, -60, 240); p.lineTo(60, 240); p.quadraticCurveTo(78, 120, 50, -40); p.closePath();
      cel3(c, p, PAL.hair, L, 8, 3, 3);
    } else if (spec.hair === 'jata') {
      for (const sd of [-1, 1]) {
        const p = new Path2D();
        p.moveTo(sd * 40, -40); p.quadraticCurveTo(sd * 64, 30, sd * 52, 96); p.lineTo(sd * 36, 90); p.quadraticCurveTo(sd * 40, 20, sd * 30, -30); p.closePath();
        cel3(c, p, PAL.hair, L, 4, 2, 3);
      }
    }
  }

  function hairFront(c, spec, pose, L) {
    if (spec.hair === 'jata') {
      const cap = new Path2D();
      cap.moveTo(-48, -20); cap.quadraticCurveTo(-50, -72, 0, -76); cap.quadraticCurveTo(50, -72, 48, -20);
      cap.quadraticCurveTo(30, -50, 0, -48); cap.quadraticCurveTo(-30, -50, -48, -20); cap.closePath();
      cel3(c, cap, PAL.hair, L, 4, 2, 3.2);
      // tall matted bun with rudraksha bands
      const bun = new Path2D();
      bun.moveTo(-26, -66); bun.quadraticCurveTo(-34, -120, -10, -142); bun.quadraticCurveTo(0, -150, 10, -142);
      bun.quadraticCurveTo(34, -120, 26, -66); bun.closePath();
      cel3(c, bun, PAL.hair, L, 5, 2.5, 3.2);
      c.strokeStyle = '#6e3b1f'; c.lineWidth = 5;
      for (const y of [-84, -110]) { c.beginPath(); c.moveTo(-26, y); c.quadraticCurveTo(0, y + 6, 26, y); c.stroke(); }
      c.fillStyle = '#8a4a26';
      for (let k = -2; k <= 2; k++) { c.beginPath(); c.arc(k * 10, -81, 3.2, 0, TAU); c.fill(); }
    } else if (spec.hair === 'long') {
      const cap = new Path2D();
      cap.moveTo(-50, -10); cap.quadraticCurveTo(-52, -74, 0, -76); cap.quadraticCurveTo(52, -74, 50, -10);
      cap.quadraticCurveTo(36, -46, 4, -50); cap.lineTo(0, -40); cap.lineTo(-4, -50); cap.quadraticCurveTo(-36, -46, -50, -10); cap.closePath();
      cel3(c, cap, PAL.hair, L, 4, 2, 3.2);
      c.strokeStyle = '#d4142a'; c.lineWidth = 3; c.beginPath(); c.moveTo(0, -72); c.lineTo(0, -44); c.stroke(); // sindoor
      c.fillStyle = K.P.gold[1]; c.beginPath(); c.arc(0, -46, 5, 0, TAU); c.fill();                           // maang tikka
    } else if (spec.hair === 'crown') {
      const cap = new Path2D();
      cap.moveTo(-48, -16); cap.quadraticCurveTo(-50, -60, 0, -62); cap.quadraticCurveTo(50, -60, 48, -16);
      cap.quadraticCurveTo(0, -44, -48, -16); cap.closePath();
      cel3(c, cap, PAL.hair, L, 4, 2, 3);
    }
    if (spec.crown) crown(c, spec.crown, L);
    if (spec.pallu) {
      const p = new Path2D();
      p.moveTo(-58, -10); p.quadraticCurveTo(-60, -86, 0, -90); p.quadraticCurveTo(60, -86, 58, -10);
      p.quadraticCurveTo(64, 80, 70, 150); p.lineTo(52, 150); p.quadraticCurveTo(48, 40, 44, -10);
      p.quadraticCurveTo(40, -62, 0, -64); p.quadraticCurveTo(-40, -62, -44, -10);
      p.quadraticCurveTo(-48, 40, -52, 150); p.lineTo(-70, 150); p.quadraticCurveTo(-64, 80, -58, -10); p.closePath();
      cel3(c, p, spec.pallu, L, 6, 3, 3.2);
      c.strokeStyle = K.P.gold[1]; c.lineWidth = 5; c.stroke(p);
      c.strokeStyle = INK; c.lineWidth = 1.5; c.stroke(p);
    }
  }

  function crown(c, kind, L) {
    const tall = kind === 'ravana' ? 1.5 : 1;
    const p = new Path2D();
    p.moveTo(-44, -44); p.lineTo(-40, -90 * tall); p.lineTo(-22, -74 * tall); p.lineTo(-12, -112 * tall);
    p.lineTo(0, -132 * tall); p.lineTo(12, -112 * tall); p.lineTo(22, -74 * tall); p.lineTo(40, -90 * tall); p.lineTo(44, -44);
    p.quadraticCurveTo(0, -58, -44, -44); p.closePath();
    cel3(c, p, K.P.gold, L, 5, 3, 3.2);
    c.fillStyle = kind === 'ravana' ? '#d4142a' : '#1aa37a';
    c.strokeStyle = INK; c.lineWidth = 2;
    c.beginPath(); c.ellipse(0, -78 * tall, 8, 11, 0, 0, TAU); c.fill(); c.stroke();
    c.fillStyle = '#fff6cc';
    for (const x of [-30, -15, 15, 30]) { c.beginPath(); c.arc(x, -56, 3.5, 0, TAU); c.fill(); }
  }

  /** Head at current origin (face centre). */
  function humanHead(c, spec, pose, L) {
    c.save();
    c.rotate(pose.tilt || 0);
    const Lh = rot(L, -(pose.tilt || 0));
    if (!pose.noHairBack) hairBack(c, spec, Lh);
    face(c, spec, pose, Lh);
    hairFront(c, spec, pose, Lh);
    c.restore();
  }

  // ---------- body ----------
  /**
   * pose: { stance: 'stand'|'kneel'|'sit', armL:[shoulderAngle, elbowBend], armR:[...],
   *         head:{tilt,turn,mood,eyes,mouth,look}, breathe, t, props:{bow,quiver,sword} }
   * Angles: 0 = pointing right, PI/2 = down (canvas).
   */
  function person(c, spec, pose, L) {
    const br = Math.sin((pose.t || 0) * 2.2) * 2 * (pose.breathe ?? 1);
    const female = !!spec.female;
    const shW = female ? 50 : 64;
    const shL = { x: -shW, y: -178 + br }, shR = { x: shW, y: -178 + br };

    if (spec.hair === 'long') {                      // long hair falls behind the body
      c.save(); c.translate(0, -290 + br); c.scale(1.32, 1.32); hairBack(c, spec, L); c.restore();
    }
    if (pose.props?.quiver) {
      c.save(); c.translate(48, -150); c.rotate(0.5);
      cel3(c, S.poly([[-14, -70], [14, -70], [12, 70], [-12, 70]]), PAL.wood, rot(L, -0.5), 4, 2, 3);
      c.strokeStyle = INK; c.lineWidth = 2;
      for (let k = -2; k <= 2; k++) {
        c.beginPath(); c.moveTo(k * 5, -70); c.lineTo(k * 6, -96); c.stroke();
        c.fillStyle = '#f4f0e6'; c.beginPath(); c.moveTo(k * 6, -96); c.lineTo(k * 6 - 4, -86); c.lineTo(k * 6 + 4, -86); c.closePath(); c.fill(); c.stroke();
      }
      c.restore();
    }

    // legs / lower garment
    const garment = spec.garment;
    if (pose.stance === 'sit') {
      const lap = new Path2D();
      lap.moveTo(-50, -10); lap.quadraticCurveTo(-150, 20, -140, 80); lap.quadraticCurveTo(0, 120, 140, 80);
      lap.quadraticCurveTo(150, 20, 50, -10); lap.closePath();
      cel3(c, lap, garment, L, 8, 3, 3.6);
      for (const sd of [-1, 1]) cel3(c, S.ellipse(sd * 70, 84, 26, 13), spec.skin, L, 3, 2, 3);
    } else if (pose.stance === 'kneel') {
      const lap = new Path2D();
      lap.moveTo(-50, -10); lap.quadraticCurveTo(-110, 40, -96, 120); lap.lineTo(110, 120);
      lap.quadraticCurveTo(116, 40, 50, -10); lap.closePath();
      cel3(c, lap, garment, L, 8, 3, 3.6);
      cel3(c, S.ellipse(-100, 118, 30, 12), spec.skin, L, 3, 2, 3);
    } else {
      for (const sd of [-1, 1]) {                    // shins + feet
        limb(c, { x: sd * 30, y: 140 }, { x: sd * 34, y: 250 }, 30, 22, spec.skin, L);
        cel3(c, S.ellipse(sd * 40, 258, 26, 11), spec.skin, L, 3, 2, 3);
        band(c, { x: sd * 34, y: 236 }, 0, 4, 15, K.P.gold, L);
      }
      const d = new Path2D();
      d.moveTo(-50, -12); d.quadraticCurveTo(-74, 70, -66, 158); d.lineTo(-6, 150); d.lineTo(0, 120);
      d.lineTo(6, 150); d.lineTo(66, 158); d.quadraticCurveTo(74, 70, 50, -12); d.closePath();
      cel3(c, d, garment, L, 9, 3, 3.6);
      c.strokeStyle = garment[2]; c.lineWidth = 2;
      for (const x of [-30, -12, 12, 30]) { c.beginPath(); c.moveTo(x * 0.6, 10); c.quadraticCurveTo(x, 80, x * 1.2, 150); c.stroke(); }
    }
    if (female) {                                     // saree pleats front
      const pl = new Path2D();
      pl.moveTo(-26, 0); pl.lineTo(26, 0); pl.lineTo(40, pose.stance === 'stand' ? 160 : 100); pl.lineTo(-40, pose.stance === 'stand' ? 160 : 100); pl.closePath();
      cel3(c, pl, spec.garment, L, 5, 2, 2.5);
      c.strokeStyle = K.P.gold[1]; c.lineWidth = 4; c.stroke(pl);
    }

    // torso
    const tor = new Path2D();
    tor.moveTo(shL.x, shL.y); tor.quadraticCurveTo(0, shL.y - 12, shR.x, shR.y);
    tor.quadraticCurveTo(shR.x + 4, -90, 40, -6); tor.lineTo(-40, -6); tor.quadraticCurveTo(shL.x - 4, -90, shL.x, shL.y); tor.closePath();
    cel3(c, tor, female ? spec.blouse : spec.skin, L, 9, 3, 3.6);
    if (!female) {
      c.strokeStyle = spec.skin[2]; c.lineWidth = 2;
      c.beginPath(); c.moveTo(-34, -120 + br); c.quadraticCurveTo(-16, -110 + br, -4, -122 + br); c.stroke();
      c.beginPath(); c.moveTo(34, -120 + br); c.quadraticCurveTo(16, -110 + br, 4, -122 + br); c.stroke();
      c.strokeStyle = '#fff8e4'; c.lineWidth = 2.5;                   // janeu (sacred thread)
      c.beginPath(); c.moveTo(-50, -176 + br); c.quadraticCurveTo(-10, -90, 38, -10); c.stroke();
    } else {
      const drape = new Path2D();                                      // pallu across the chest
      drape.moveTo(-46, -170); drape.lineTo(-20, -176); drape.lineTo(44, -10); drape.lineTo(10, -6); drape.closePath();
      cel3(c, drape, spec.garment, L, 4, 2, 2.6);
      c.strokeStyle = K.P.gold[1]; c.lineWidth = 3; c.stroke(drape);
    }
    // necklace + belt
    c.strokeStyle = INK; c.lineWidth = 6; c.beginPath(); c.arc(0, -196 + br, 44, 0.25 * Math.PI, 0.75 * Math.PI); c.stroke();
    c.strokeStyle = K.P.gold[1]; c.lineWidth = 4; c.stroke();
    cel3(c, S.ellipse(0, -150 + br, 8, 10), spec.gem ? spec.gem : K.P.red, L, 2, 1.5, 2.2);
    cel3(c, S.poly([[-46, -18], [46, -18], [48, 4], [-48, 4]]), spec.sash || K.P.gold, L, 3, 2, 3);
    if (spec.uttariya) {                                                // scarf over shoulders
      const u = new Path2D();
      u.moveTo(shL.x - 6, shL.y - 4); u.quadraticCurveTo(0, shL.y + 26, shR.x + 6, shR.y - 4);
      u.quadraticCurveTo(shR.x + 22, -80, shR.x + 12, 30); u.lineTo(shR.x - 4, 30); u.quadraticCurveTo(shR.x + 2, -80, shR.x - 6, shR.y + 14);
      u.quadraticCurveTo(0, shL.y + 44, shL.x + 6, shL.y + 14); u.quadraticCurveTo(shL.x - 2, -60, shL.x - 10, 40);
      u.lineTo(shL.x - 26, 40); u.quadraticCurveTo(shL.x - 22, -80, shL.x - 6, shL.y - 4); u.closePath();
      cel3(c, u, spec.uttariya, L, 5, 2.5, 3);
    }

    // arms
    const arm = (sh, ang, side) => {
      const elbow = at(sh, ang[0], 100);
      const hand = at(elbow, ang[0] + ang[1], 90);
      limb(c, sh, elbow, female ? 36 : 48, female ? 28 : 36, spec.skin, L);
      limb(c, elbow, hand, female ? 28 : 36, female ? 22 : 28, spec.skin, L);
      band(c, at(sh, ang[0], 50), ang[0], 5, female ? 13 : 18, K.P.gold, L);
      band(c, at(elbow, ang[0] + ang[1], 76), ang[0] + ang[1], 4, female ? 10 : 13, female ? K.P.red : K.P.gold, L);
      cel3(c, S.ellipse(hand.x, hand.y, 15, 17, ang[0] + ang[1]), spec.skin, L, 3, 2, 3);
      return hand;
    };
    const hl = arm(shL, pose.armL || [1.9, -0.2], -1);
    const hr = arm(shR, pose.armR || [1.25, 0.2], 1);

    // neck + head
    cel3(c, S.poly([[-16, -232 + br], [16, -232 + br], [18, -176 + br], [-18, -176 + br]]), spec.skin, L, 5, 2, 3);
    c.save(); c.translate(0, -290 + br); c.scale(1.32, 1.32);
    if (spec.extraHeads) ravanaHeads(c, spec, pose, L);
    humanHead(c, spec, Object.assign({ t: pose.t, noHairBack: spec.hair === 'long' }, pose.head || {}), L);
    c.restore();

    if (pose.props?.bow) bow(c, pose.props.bow === 'left' ? hl : hr, pose.props.bowAngle ?? -0.2, L);
    if (pose.props?.sword) sword(c, hr, pose.props.sword, L);
    return { hl, hr };
  }

  function bow(c, hand, ang, L) {
    c.save(); c.translate(hand.x, hand.y); c.rotate(ang);
    const b = new Path2D();
    b.moveTo(-6, -200); b.quadraticCurveTo(-60, -100, -12, 0); b.quadraticCurveTo(-60, 100, -6, 200);
    b.lineTo(4, 196); b.quadraticCurveTo(-44, 100, 0, 0); b.quadraticCurveTo(-44, -100, 4, -196); b.closePath();
    cel3(c, b, PAL.wood, rot(L, -ang), 3, 2, 3);
    c.strokeStyle = '#f4f0e6'; c.lineWidth = 1.6; c.beginPath(); c.moveTo(0, -198); c.lineTo(0, 198); c.stroke();
    cel3(c, S.ellipse(-8, 0, 8, 18), K.P.gold, rot(L, -ang), 2, 1.5, 2);
    c.restore();
  }

  /** Chandrahas – Ravana's curved sword. swing = angle of the blade. */
  function sword(c, hand, swing, L) {
    c.save(); c.translate(hand.x, hand.y); c.rotate(swing);
    const bl = new Path2D();
    bl.moveTo(0, -8); bl.quadraticCurveTo(140, -40, 250, -10); bl.quadraticCurveTo(170, 4, 0, 10); bl.closePath();
    cel3(c, bl, PAL.steel, rot(L, -swing), 3, 3, 3);
    c.strokeStyle = 'rgba(255,255,255,0.8)'; c.lineWidth = 2; c.beginPath(); c.moveTo(10, -4); c.quadraticCurveTo(140, -30, 236, -10); c.stroke();
    cel3(c, S.poly([[-6, -22], [8, -22], [8, 22], [-6, 22]]), K.P.gold, L, 2, 1.5, 2.5);
    cel3(c, S.poly([[-40, -6], [-4, -6], [-4, 6], [-40, 6]]), PAL.black, L, 2, 1.5, 2.5);
    c.restore();
  }

  function ravanaHeads(c, spec, pose, L) {
    // nine more heads in a fan behind the main one (outer ones further back)
    const order = [4, -4, 3, -3, 2, -2, 1, -1, 5];
    for (const k of order) {
      const a = Math.abs(k);
      c.save();
      c.translate(k * 52 * (a === 5 ? 0.6 : 1), 6 + a * 5 - (a === 5 ? 50 : 0));
      const s = 0.86 - a * 0.05;
      c.scale(s, s);
      const shade = 1 - a * 0.08;
      c.globalAlpha *= 1;
      humanHead(c, Object.assign({}, spec, { earrings: a < 3, skin: shade < 0.75 ? PAL.ravanaSkin.map((x, i) => PAL.ravanaSkin[Math.min(2, i + 1)]) : spec.skin }),
        Object.assign({ t: pose.t }, pose.head || {}, { turn: Math.sign(k) * 0.5, mouth: (pose.head?.mouth || 0) * (a % 2) }), L);
      c.restore();
    }
  }

  // ---------- vanaras ----------
  /**
   * kind: 'hanuman' | 'angad' | 'jambavan'. Seated, origin = seat.
   * pose: { slump 0..1, headTilt, turn, mood, eyes, mouth, t, look, armUp 0..1 }
   */
  function vanara(c, kind, pose, L) {
    const fur = kind === 'hanuman' ? PAL.furGold : kind === 'angad' ? PAL.furBrown : PAL.furBear;
    const facePal = kind === 'hanuman' ? PAL.hanumanFace : kind === 'angad' ? PAL.muzzle : PAL.furBear;
    const br = Math.sin((pose.t || 0) * 2 + (kind.length)) * 2;
    const slump = pose.slump || 0;

    if (kind !== 'jambavan') {                               // tail
      const tp = new Path2D();
      const w = Math.sin((pose.t || 0) * 1.4 + kind.length) * 20;
      const tail = S.ribbon({ x: -40, y: 40 }, { x: -200, y: 80 }, { x: -230 + w, y: -120 }, { x: -140 + w, y: -170 }, 24, 10);
      cel3(c, tail, fur, L, 5, 2, 3.2);
    }
    // folded legs
    const legs = new Path2D();
    legs.moveTo(-110, 30); legs.quadraticCurveTo(-140, 90, -40, 96); legs.lineTo(40, 96);
    legs.quadraticCurveTo(140, 90, 110, 30); legs.quadraticCurveTo(0, 0, -110, 30); legs.closePath();
    cel3(c, legs, fur, L, 8, 3, 3.6);
    if (kind !== 'jambavan') {
      const lang = new Path2D();                               // red langot / dhoti
      lang.moveTo(-60, 0); lang.lineTo(60, 0); lang.quadraticCurveTo(70, 50, 40, 70); lang.lineTo(-40, 70); lang.quadraticCurveTo(-70, 50, -60, 0); lang.closePath();
      cel3(c, lang, kind === 'hanuman' ? PAL.saree : PAL.green, L, 5, 2, 3);
    }
    // torso (furry outline)
    c.save(); c.translate(0, slump * 10);
    const tor = S.ruffPath(0, -110 + br, 82, 92, 18, 1.25, kind.length * 7);
    cel3(c, tor, fur, L, 10, 3, 3.6);
    const chest = S.ellipse(0, -100 + br, 48, 64);
    cel3(c, chest, kind === 'jambavan' ? PAL.white : PAL.muzzle, L, 5, 2, 0);
    if (kind === 'jambavan') {                                  // white crescent on bear chest
      c.save(); c.clip(chest); c.fillStyle = fur[1]; c.beginPath(); c.ellipse(0, -80 + br, 48, 52, 0, 0, TAU); c.fill(); c.restore();
    }
    if (kind !== 'jambavan') {                                   // gold chest ornament / janeu
      c.strokeStyle = K.P.gold[1]; c.lineWidth = 5; c.beginPath(); c.arc(0, -160 + br, 46, 0.2 * Math.PI, 0.8 * Math.PI); c.stroke();
    }
    // arms resting on knees / raised
    for (const sd of [-1, 1]) {
      const sh = { x: sd * 70, y: -160 + br };
      const up = sd === 1 ? (pose.armUp || 0) : 0;
      const elbow = { x: sd * lerp(100, 110, up), y: lerp(-60, -200, up) };
      const hand = { x: sd * lerp(70, 120, up), y: lerp(20, -280, up) };
      limb(c, sh, elbow, 40, 30, fur, L);
      limb(c, elbow, hand, 30, 24, fur, L);
      band(c, { x: lerp(elbow.x, hand.x, 0.7), y: lerp(elbow.y, hand.y, 0.7) }, Math.atan2(hand.y - elbow.y, hand.x - elbow.x), 4, 14, K.P.gold, L);
      cel3(c, S.ellipse(hand.x, hand.y, 17, 15), kind === 'jambavan' ? fur : PAL.muzzle, L, 3, 2, 3);
    }
    // props
    if (kind === 'hanuman') gada(c, { x: 150, y: 30 }, -0.35, L);
    if (kind === 'jambavan') {
      c.save(); c.translate(-140, -80); c.rotate(0.12);
      cel3(c, S.poly([[-7, -200], [7, -200], [8, 170], [-8, 170]]), PAL.wood, L, 3, 2, 3);
      c.restore();
    }
    // head
    c.save();
    c.translate(0, -236 + br + slump * 26);
    c.rotate(pose.headTilt || 0);
    const Lh = rot(L, -(pose.headTilt || 0));
    vanaraHead(c, kind, fur, facePal, pose, Lh);
    c.restore();
    c.restore();
  }

  function vanaraHead(c, kind, fur, facePal, pose, L) {
    const turn = pose.turn || 0;
    if (kind === 'jambavan') {
      for (const sd of [-1, 1]) cel3(c, S.ellipse(sd * 52, -50, 22, 22), fur, L, 4, 2, 3);
    }
    const head = S.ruffPath(0, 0, 64, 72, 16, 1, kind.length * 3);
    cel3(c, head, fur, L, 8, 3, 3.6);
    if (kind !== 'jambavan') for (const sd of [-1, 1]) {     // ears
      cel3(c, S.ellipse(sd * 64, -2, 14, 18), facePal, L, 3, 2, 3);
      cel3(c, S.ellipse(sd * 66, 18, 7, 11), K.P.gold, L, 2, 1.5, 2.2); // kundal
    }
    // face mask
    const fm = new Path2D();
    if (kind === 'jambavan') {
      fm.ellipse(turn * 8, 14, 34, 30, 0, 0, TAU);
    } else {
      fm.moveTo(-44, -20); fm.quadraticCurveTo(-48, 30, -20, 50); fm.quadraticCurveTo(0, 58, 20, 50);
      fm.quadraticCurveTo(48, 30, 44, -20); fm.quadraticCurveTo(22, -40, 0, -26); fm.quadraticCurveTo(-22, -40, -44, -20); fm.closePath();
    }
    cel3(c, fm, kind === 'jambavan' ? PAL.muzzle.map((x, i) => ['#cbb7a8', '#8e7666', '#5a463a'][i]) : facePal, L, 5, 2, 3.2);
    // muzzle
    const mz = S.ellipse(turn * 10, 26, kind === 'jambavan' ? 26 : 30, kind === 'jambavan' ? 20 : 22);
    cel3(c, mz, kind === 'jambavan' ? ['#e6dcd2', '#bfb0a2', '#857668'] : PAL.muzzle, L, 3, 2, 3);
    c.fillStyle = INK; c.beginPath(); c.ellipse(turn * 10, 14, kind === 'jambavan' ? 11 : 6, kind === 'jambavan' ? 8 : 4, 0, 0, TAU); c.fill();
    mouthV(c, turn, pose);
    const iris = kind === 'hanuman' ? '#8a4b10' : '#3a2414';
    eye(c, -20 + turn * 10, -8, 0.85, pose.eyes || 'open', iris, pose.look, true);
    eye(c, 20 + turn * 10, -8, 0.85, pose.eyes || 'open', iris, pose.look, false);
    brows(c, turn * 0.8, pose.mood || 'calm', kind === 'jambavan' ? '#f2efe8' : INK, kind === 'jambavan' ? 7 : 4);
    if (kind === 'jambavan') {                                  // old white beard
      const bd = S.ruffPath(turn * 8, 52, 26, 34, 10, 0.8, 4);
      cel3(c, bd, PAL.white, L, 3, 2, 2.5);
    }
    if (kind === 'hanuman') {
      c.save(); c.translate(0, -8); crown(c, 'hanuman', L); c.restore();
      c.strokeStyle = '#d4142a'; c.lineWidth = 4; c.beginPath(); c.moveTo(turn * 10, -48); c.lineTo(turn * 10, -26); c.stroke();
    }
    if (kind === 'angad') { c.save(); c.translate(0, 2); c.scale(0.8, 0.8); crown(c, 'angad', L); c.restore(); }
  }

  function mouthV(c, turn, pose) {
    c.strokeStyle = INK; c.lineWidth = 2.6; c.lineCap = 'round';
    if ((pose.mouth || 0) > 0.05) {
      c.fillStyle = '#7a1e2a'; c.beginPath(); c.ellipse(turn * 10, 38, 10, 3 + 7 * pose.mouth, 0, 0, TAU); c.fill(); c.stroke();
    } else {
      c.beginPath();
      if (pose.mood === 'sad') { c.moveTo(turn * 10 - 10, 42); c.quadraticCurveTo(turn * 10, 36, turn * 10 + 10, 42); }
      else { c.moveTo(turn * 10 - 10, 38); c.quadraticCurveTo(turn * 10, 43, turn * 10 + 10, 38); }
      c.stroke();
    }
  }

  function gada(c, p, ang, L) {
    c.save(); c.translate(p.x, p.y); c.rotate(ang);
    cel3(c, S.poly([[-7, 0], [7, 0], [6, -230], [-6, -230]]), PAL.wood, rot(L, -ang), 3, 2, 3);
    const head = S.ellipse(0, -270, 46, 56);
    cel3(c, head, K.P.gold, rot(L, -ang), 8, 4, 3.6);
    c.strokeStyle = K.P.gold[3]; c.lineWidth = 3;
    for (const y of [-300, -270, -240]) { c.beginPath(); c.ellipse(0, y, 44 - Math.abs(y + 270) * 0.5, 6, 0, 0, Math.PI); c.stroke(); }
    cel3(c, S.ellipse(0, -330, 10, 12), K.P.gold, rot(L, -ang), 2, 1.5, 2.5);
    c.restore();
  }

  // ---------- Surya ----------
  function surya(c, x, y, r, t, mood, L) {
    c.save(); c.translate(x, y);
    const glow = c.createRadialGradient(0, 0, r * 0.6, 0, 0, r * 2.6);
    glow.addColorStop(0, 'rgba(255,240,170,0.9)'); glow.addColorStop(0.4, 'rgba(255,170,60,0.35)'); glow.addColorStop(1, 'rgba(255,120,40,0)');
    c.fillStyle = glow; c.fillRect(-r * 2.6, -r * 2.6, r * 5.2, r * 5.2);
    // rays (flame tongues) rotating
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
    // serene face
    const s = r / 170;
    c.save(); c.scale(s, s);
    c.strokeStyle = '#b8500c'; c.lineWidth = 6; c.lineCap = 'round';
    for (const sd of [-1, 1]) {
      c.beginPath();
      if (mood === 'fierce') { c.moveTo(sd * 30, -20); c.lineTo(sd * 78, -34); }
      c.stroke();
      c.beginPath(); c.arc(sd * 52, -6, 24, mood === 'fierce' ? 1.15 * Math.PI : 0.15 * Math.PI, mood === 'fierce' ? 1.85 * Math.PI : 0.85 * Math.PI); c.stroke();
    }
    c.beginPath(); c.moveTo(-26, 60); c.quadraticCurveTo(0, mood === 'fierce' ? 56 : 76, 26, 60); c.stroke();
    c.fillStyle = '#e0242e'; c.beginPath(); c.arc(0, -52, 9, 0, TAU); c.fill();
    c.restore();
    c.restore();
  }

  global.Chars = { PAL, person, vanara, surya, crown, bow, sword, gada, humanHead };
})(typeof window !== 'undefined' ? window : globalThis);
