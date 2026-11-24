/*
 * Realistic-proportion silhouette figures with a rim of light — the classic
 * backlit cinematic look (no cartoon faces). One union path per figure is
 * filled with a rim colour, then the dark body is laid over it shifted away
 * from the light, leaving a thin lit edge.
 *
 * figure(c, spec, pose, light, t): origin = between the feet, height spec.h (default 600).
 *  spec: { face: 1|-1, kind: 'man'|'woman'|'ravana'|'vanara'|'bear', hair: 'jata'|'long'|'crown',
 *          dhoti, saree, scarf, bow, quiver, sword, mace, staff, tail, build }
 *  pose: { stance: 'stand'|'walk'|'kneel'|'sit', armN:[a1,a2], armF:[a1,a2], head: tilt, step, lean }
 * Angles are in the figure's own frame (0 = forward, PI/2 = down).
 */
(function (global) {
  'use strict';
  const S = global.Shapes;
  const { TAU, lerp } = S;

  const at = (p, a, l) => ({ x: p.x + Math.cos(a) * l, y: p.y + Math.sin(a) * l });
  function limb(p, a, b, w0, w1) {
    const m1 = { x: lerp(a.x, b.x, 0.33), y: lerp(a.y, b.y, 0.33) }, m2 = { x: lerp(a.x, b.x, 0.66), y: lerp(a.y, b.y, 0.66) };
    p.addPath(S.ribbon(a, m1, m2, b, w0, w1, 8));
    p.moveTo(a.x + w0 / 2, a.y); p.arc(a.x, a.y, w0 / 2, 0, TAU);
    p.moveTo(b.x + w1 / 2, b.y); p.arc(b.x, b.y, w1 / 2, 0, TAU);
  }
  const ell = (p, x, y, rx, ry, r = 0) => { p.moveTo(x + rx, y); p.ellipse(x, y, rx, ry, r, 0, TAU); };

  /** Build the silhouette path (units: height 100, feet at 0, facing +x). */
  /** Path builder that keeps each sub-shape separately (avoids winding holes). */
  function PartPath() {
    const parts = []; let cur = new Path2D(); parts.push(cur);
    const api = {
      parts,
      moveTo(x, y) { cur = new Path2D(); parts.push(cur); cur.moveTo(x, y); },
      lineTo(x, y) { cur.lineTo(x, y); }, quadraticCurveTo(a, b, x, y) { cur.quadraticCurveTo(a, b, x, y); },
      bezierCurveTo(a, b, d, e, x, y) { cur.bezierCurveTo(a, b, d, e, x, y); },
      arc(...a) { cur.arc(...a); }, ellipse(...a) { cur.ellipse(...a); }, closePath() { cur.closePath(); },
      addPath(q, m) { if (q.parts) { for (const pp of q.parts) { const n = new Path2D(); n.addPath(pp, m); parts.push(n); } } else { const n = new Path2D(); n.addPath(q, m); parts.push(n); } },
    };
    return api;
  }

  function build(spec, pose, t) {
    const p = PartPath();
    const kind = spec.kind || 'man', female = kind === 'woman';
    const big = spec.build === 'big' ? 1.18 : 1;
    const wind = Math.sin(t * 1.6) * 2;
    const stance = pose.stance || 'stand';
    let hipY = -50, sh = { y: -81 };
    if (stance === 'kneel') hipY = -30;
    if (stance === 'sit') hipY = -14;
    const lift = -50 - hipY;                                   // how much the upper body drops
    sh.y += -lift;
    const lean = pose.lean || 0;
    const L = (x, y) => ({ x: x + lean * (hipY - y) * 0.5, y });  // lean the upper body forward

    // ---- legs
    if (stance === 'stand' || stance === 'walk') {
      const st = stance === 'walk' ? Math.sin(pose.step ?? t * 4) * 0.35 : 0.06;
      for (const sd of [-1, 1]) {
        const a = Math.PI / 2 + sd * st;
        const hip = { x: sd * 2.5, y: -50 }, knee = at(hip, a, 25), ank = at(knee, Math.PI / 2 + sd * st * 0.4, 22);
        limb(p, hip, knee, 9 * big, 6.5);
        limb(p, knee, ank, 6.5, 4);
        ell(p, ank.x + 3, ank.y + 1.5, 5, 2);
      }
    } else if (stance === 'kneel') {
      const hip = { x: 0, y: hipY };
      limb(p, hip, { x: 13, y: -16 }, 10, 7); limb(p, { x: 13, y: -16 }, { x: 14, y: -1 }, 7, 4.5); ell(p, 17, 0, 5, 2);   // front leg
      limb(p, hip, { x: -6, y: -2 }, 10, 7); limb(p, { x: -6, y: -2 }, { x: -22, y: -1 }, 6, 4);                          // knee down
    } else {
      ell(p, 0, -6, 20, 7);                                     // crossed legs
    }

    // ---- lower garments
    if (female || spec.saree) {
      const fl = stance === 'stand' || stance === 'walk';
      p.moveTo(-7, hipY + 2); p.quadraticCurveTo(-12, hipY + 30, fl ? -15 : -18, fl ? -1 : -2);
      p.lineTo(fl ? 16 : 18, fl ? -1 : -2); p.quadraticCurveTo(12, hipY + 30, 7, hipY + 2); p.closePath();
    } else if (spec.dhoti !== false) {
      const kn = stance === 'kneel' ? -14 : stance === 'sit' ? -4 : -24;
      p.moveTo(-8 * big, hipY - 2); p.quadraticCurveTo(-11 * big, hipY + 14, -10 * big, kn); p.lineTo(11 * big, kn);
      p.quadraticCurveTo(12 * big, hipY + 14, 8 * big, hipY - 2); p.closePath();
    }

    // ---- torso
    const sw = (female ? 8 : 10.5) * big;
    const neck = L(0.6, sh.y - 4);
    p.moveTo(L(-sw, sh.y + 1).x, sh.y + 1);
    p.quadraticCurveTo(L(0, sh.y - 3).x, sh.y - 3, L(sw, sh.y + 1).x, sh.y + 1);
    p.quadraticCurveTo(L(sw + 0.5, sh.y + 14).x, sh.y + 14, L(6 * big, hipY - 6).x, hipY - 6);
    p.lineTo(L(8 * big, hipY + 2).x, hipY + 2); p.lineTo(L(-8 * big, hipY + 2).x, hipY + 2); p.lineTo(L(-6 * big, hipY - 6).x, hipY - 6);
    p.quadraticCurveTo(L(-sw - 0.5, sh.y + 14).x, sh.y + 14, L(-sw, sh.y + 1).x, sh.y + 1); p.closePath();
    if (female) { ell(p, L(5, sh.y + 9).x, sh.y + 9, 4, 3.5); }
    limb(p, neck, L(0.8, sh.y - 9), 4.4, 4.2);

    // ---- head (profile: forehead, nose, lips, chin)
    const hd = L(1, sh.y - 15);
    const tilt = pose.head || 0;
    const hp = PartPath();
    if (kind === 'bear') {
      hp.ellipse(0, 0, 6.6, 6.2, 0, 0, TAU);
      hp.moveTo(4, 1); hp.quadraticCurveTo(10, 0, 11.5, 3); hp.quadraticCurveTo(10, 6, 4, 5); hp.closePath();   // snout
      hp.moveTo(-1.5, -5); hp.arc(-2.5, -6.5, 2.4, 0, TAU);
    } else if (kind === 'vanara') {
      hp.ellipse(0, 0, 5.8, 6.6, 0, 0, TAU);
      hp.moveTo(3.5, 1); hp.quadraticCurveTo(9.5, 1.5, 9, 5); hp.quadraticCurveTo(6, 7.5, 2, 6); hp.closePath();   // muzzle
      hp.moveTo(-3, -1); hp.arc(-4.5, -1, 2.6, 0, TAU);                                                       // ear
    } else {
      hp.moveTo(-5, -4); hp.quadraticCurveTo(-5.5, -8, 0, -8); hp.quadraticCurveTo(5, -8, 5.2, -3);
      hp.lineTo(5.4, -1); hp.lineTo(7, 1.6); hp.lineTo(5.6, 2.4);                                            // nose
      hp.lineTo(5.6, 3.6); hp.lineTo(6.2, 4.2); hp.lineTo(5.4, 5.2);                                         // lips
      hp.quadraticCurveTo(5.2, 7.4, 2.4, 7.6); hp.quadraticCurveTo(-3, 7, -5, 3); hp.closePath();
    }
    if (spec.hair === 'jata') { hp.moveTo(-1.5, -10.5); hp.ellipse(-1.5, -10.5, 3.4, 4.4, -0.2, 0, TAU); hp.moveTo(-5.5, -2); hp.quadraticCurveTo(-9, 6, -7, 13); hp.lineTo(-4, 12); hp.quadraticCurveTo(-5, 5, -3, -1); hp.closePath(); }
    if (spec.hair === 'long' || female) {
      hp.moveTo(-5, -5); hp.quadraticCurveTo(-10, 8, -9 + wind * 0.4, 28); hp.lineTo(-5 + wind * 0.4, 28); hp.quadraticCurveTo(-5, 10, -1, -2); hp.closePath();
    }
    if (spec.hair === 'crown' || kind === 'ravana') {
      hp.moveTo(-5.5, -6); hp.lineTo(-5, -14); hp.lineTo(-2.5, -11); hp.lineTo(0, -19); hp.lineTo(2.5, -11); hp.lineTo(5, -14); hp.lineTo(5.5, -6); hp.closePath();
      hp.moveTo(-5.5, -3); hp.quadraticCurveTo(-9, 6, -8, 12); hp.lineTo(-5, 11); hp.closePath();
    }
    p.addPath(hp, new DOMMatrix().translate(hd.x, hd.y).rotate(tilt * 180 / Math.PI));
    if (kind === 'ravana') {                                     // nine more heads fanned behind
      for (const k of [-4, -3, -2, -1, 1, 2, 3, 4, 5]) {
        const a = Math.abs(k), sd = k > 0 ? 1 : -1;
        const hx = hd.x + sd * (6 + a * 6.2) * (k === 5 ? 0 : 1), hy = hd.y + a * 1.2 - (k === 5 ? 9 : 0);
        p.addPath(hp, new DOMMatrix().translate(hx, hy).scale(0.86 - a * 0.03).rotate((tilt + sd * 0.08 * a) * 180 / Math.PI));
      }
    }

    // ---- arms (near and far)
    const shN = L(3, sh.y + 2), shF = L(-3, sh.y + 2);
    const arm = (s, ang, wa) => {
      const el = at(s, ang[0], 16), wr = at(el, ang[0] + ang[1], 15);
      limb(p, s, el, wa * big, wa * 0.75 * big); limb(p, el, wr, wa * 0.75 * big, wa * 0.55 * big);
      ell(p, at(wr, ang[0] + ang[1], 2.5).x, at(wr, ang[0] + ang[1], 2.5).y, 2.8, 2.2, ang[0] + ang[1]);
      return wr;
    };
    const wF = arm(shF, pose.armF || [1.75, -0.2], female ? 5 : 6.2);
    const wN = arm(shN, pose.armN || [1.45, 0.25], female ? 5 : 6.2);

    // ---- scarf / pallu flowing behind
    if (spec.scarf || female) {
      p.moveTo(L(-sw + 1, sh.y).x, sh.y); p.quadraticCurveTo(-20 + wind, sh.y + 10, -26 + wind * 2, sh.y + (female ? 40 : 30));
      p.lineTo(-21 + wind * 2, sh.y + (female ? 42 : 31)); p.quadraticCurveTo(-14 + wind, sh.y + 12, L(-sw + 4, sh.y + 4).x, sh.y + 4); p.closePath();
    }
    // ---- tail
    if (spec.tail) {
      const sw2 = Math.sin(t * 1.2) * 4;
      p.addPath(S.ribbon({ x: -6, y: hipY }, { x: -30, y: hipY + 8 }, { x: -36 + sw2, y: hipY - 30 }, { x: -24 + sw2, y: hipY - 42 }, 3.2, 1.6, 20));
    }
    // ---- props
    if (spec.quiver) { p.addPath(S.poly([[-6, sh.y - 6], [-2, sh.y - 7], [-9, sh.y + 22], [-13, sh.y + 21]])); }
    if (spec.bow) {
      const g = spec.bow === 'far' ? wF : wN;
      const b = PartPath(); b.moveTo(g.x + 1, g.y - 30); b.quadraticCurveTo(g.x + 9, g.y, g.x + 1, g.y + 30);
      b.lineTo(g.x + 2.4, g.y + 30); b.quadraticCurveTo(g.x + 10.6, g.y, g.x + 2.4, g.y - 30); b.closePath();
      p.addPath(b);
    }
    if (spec.mace) { const g = wN; limb(p, { x: g.x, y: g.y + 4 }, { x: g.x + 2, y: g.y - 26 }, 1.6, 1.6); ell(p, g.x + 2, g.y - 31, 5.5, 6.5); }
    if (spec.staff) { const g = wF; limb(p, { x: g.x, y: 0 }, { x: g.x + 1, y: g.y - 20 }, 1.6, 1.6); }
    return { path: p, hands: { near: wN, far: wF }, head: hd };
  }

  /**
   * Draw a figure. light = {x, y} light position in canvas coords.
   * look: { dark, rim, rimW } colours; returns the sword hand etc. in canvas coords.
   */
  let buf = null;
  function figure(c, spec, pose, light, t, look = {}) {
    const h = spec.h || 600, k = h / 100, face = spec.face || 1;
    const { path, hands, head } = build(spec, pose, t);
    if (!buf) { buf = document.createElement('canvas'); buf.width = c.canvas.width; buf.height = c.canvas.height; }
    const b = buf.getContext('2d');
    b.setTransform(1, 0, 0, 1, 0, 0); b.clearRect(0, 0, buf.width, buf.height);
    b.setTransform(c.getTransform());
    b.translate(spec.x, spec.y); b.scale(k * face, k);
    let dx = (light.x - spec.x) * face, dy = light.y - (spec.y - h * 0.7); const l = Math.hypot(dx, dy) || 1; dx /= l; dy /= l;
    // paint each sub-shape (union by painting, so overlapping parts never leave holes)
    b.fillStyle = look.rim || 'rgba(255,200,130,1)';
    for (const part of path.parts) b.fill(part);
    b.globalCompositeOperation = 'source-atop';
    const rw = (look.rimW || 6) / k;
    b.translate(-dx * rw, -dy * rw);
    b.fillStyle = look.dark || '#140c12';
    for (const part of path.parts) b.fill(part);
    b.globalCompositeOperation = 'source-over';
    c.save(); c.setTransform(1, 0, 0, 1, 0, 0); c.globalAlpha *= look.alpha ?? 1; c.drawImage(buf, 0, 0); c.restore();
    const T = (p) => ({ x: spec.x + p.x * k * face, y: spec.y + p.y * k });
    return { near: T(hands.near), far: T(hands.far), head: T(head), scale: k, face };
  }

  /** Curved sword with a moving glint, drawn from a hand point. */
  function sword(c, hand, ang, len, t, glint = 0, dark = '#120a10', rim = 'rgba(255,220,180,1)') {
    c.save(); c.translate(hand.x, hand.y); c.rotate(ang);
    const p = new Path2D(); p.moveTo(0, -4); p.quadraticCurveTo(len * 0.6, -len * 0.14, len, -len * 0.04); p.quadraticCurveTo(len * 0.65, 2, 0, 5); p.closePath();
    c.fillStyle = '#d8dde6'; c.fill(p);
    c.fillStyle = dark; c.fillRect(-28, -6, 28, 12);
    if (glint > 0) {
      const gx = len * glint;
      const g = c.createRadialGradient(gx, -len * 0.06, 0, gx, -len * 0.06, 90);
      g.addColorStop(0, 'rgba(255,255,255,1)'); g.addColorStop(1, 'rgba(255,255,255,0)');
      c.globalCompositeOperation = 'lighter'; c.fillStyle = g; c.fillRect(gx - 90, -150, 180, 180);
      c.strokeStyle = 'rgba(255,255,255,0.9)'; c.lineWidth = 2; c.beginPath(); c.moveTo(gx - 60, -len * 0.06); c.lineTo(gx + 60, -len * 0.06); c.moveTo(gx, -len * 0.06 - 60); c.lineTo(gx, -len * 0.06 + 60); c.stroke();
    }
    c.restore();
  }

  global.Figures = { figure, sword, build };
})(typeof window !== 'undefined' ? window : globalThis);
