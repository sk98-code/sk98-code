/* Sone ki Lanka on Mount Trikuta (shared by the realistic cut). */
(function (global) {
  'use strict';
  const S = global.Shapes; const { W, TAU, hash } = S;
  function lanka(c, x, baseY, s, t, night = 0.5, reflect = true) {
    const draw = (cc) => {
      cc.save(); cc.translate(x, baseY); cc.scale(s, s);
      // glow behind the city
      const gl = cc.createRadialGradient(0, -260, 40, 0, -260, 620);
      gl.addColorStop(0, `rgba(255,210,110,${0.55 + 0.25 * night})`); gl.addColorStop(1, 'rgba(255,170,60,0)');
      cc.fillStyle = gl; cc.fillRect(-700, -900, 1400, 900);
      // Mount Trikuta: three peaks
      const m = new Path2D();
      m.moveTo(-560, 0); m.quadraticCurveTo(-430, -260, -300, -330); m.quadraticCurveTo(-200, -380, -120, -480);
      m.quadraticCurveTo(0, -640, 110, -500); m.quadraticCurveTo(200, -400, 300, -360); m.quadraticCurveTo(440, -260, 560, 0); m.closePath();
      cc.fillStyle = '#123028'; cc.fill(m);
      cc.save(); cc.clip(m); cc.fillStyle = 'rgba(255,190,110,0.85)'; cc.fillRect(-600, -700, 1200, 700);
      cc.translate(-6, 10); cc.fillStyle = '#123028'; cc.fill(m); cc.restore();
      for (let i = 0; i < 14; i++) {                          // jungle tufts on the slopes
        const px = -480 + i * 72 + (hash(i, 301) - 0.5) * 30, py = -40 - Math.max(0, 300 - Math.abs(px) * 0.55) * hash(i, 302);
        cc.fillStyle = i % 2 ? '#1c4a36' : '#163e2e'; cc.beginPath(); cc.arc(px, py, 26 + 14 * hash(i, 303), 0, TAU); cc.fill();
      }
      // tiers of golden palaces climbing the mountain
      const tiers = [[-0, 420, 12], [-120, 330, 9], [-230, 240, 7], [-330, 150, 4]];
      tiers.forEach(([y0, halfW, n], r) => {
        for (let i = 0; i < n; i++) {
          const bx = -halfW + (i + 0.5) * (2 * halfW / n), seed = r * 31 + i;
          const w = (2 * halfW / n) * (0.78 + 0.15 * hash(seed, 1)), h = 70 + 70 * hash(seed, 2) + (n - Math.abs(i - (n - 1) / 2) * 2) * 6;
          const top = y0 - h;
          const g = cc.createLinearGradient(0, top, 0, y0); g.addColorStop(0, '#ffe58a'); g.addColorStop(0.5, '#f2b02a'); g.addColorStop(1, '#a8620c');
          cc.fillStyle = g; cc.fillRect(bx - w / 2, top, w, h);
          cc.strokeStyle = 'rgba(90,46,6,0.55)'; cc.lineWidth = 3; cc.strokeRect(bx - w / 2, top, w, h);
          cc.fillStyle = 'rgba(120,50,0,0.35)'; cc.fillRect(bx + w * 0.15, top, w * 0.35, h);            // side shade
          // windows with lamps
          for (let wy = top + 18; wy < y0 - 14; wy += 26) for (let wx = -w / 2 + 12; wx < w / 2 - 10; wx += 18) {
            const lit = hash(seed, (wx * 7 + wy) | 0) < 0.35 + 0.5 * night;
            cc.fillStyle = lit ? `rgba(255,${200 + 40 * Math.sin(t * 3 + wx)},120,${0.6 + 0.4 * night})` : '#6a3608';
            cc.beginPath(); cc.moveTo(bx + wx, wy + 12); cc.lineTo(bx + wx, wy + 3); cc.quadraticCurveTo(bx + wx + 4, wy - 3, bx + wx + 8, wy + 3); cc.lineTo(bx + wx + 8, wy + 12); cc.closePath(); cc.fill();
          }
          // roof: dome or spire
          const kind = hash(seed, 3);
          cc.fillStyle = '#ffd04a'; cc.strokeStyle = 'rgba(90,46,6,0.55)'; cc.lineWidth = 3;
          if (kind < 0.45) {
            cc.beginPath(); cc.moveTo(bx - w * 0.42, top); cc.bezierCurveTo(bx - w * 0.42, top - w * 0.6, bx + w * 0.42, top - w * 0.6, bx + w * 0.42, top); cc.closePath(); cc.fill(); cc.stroke();
            cc.beginPath(); cc.moveTo(bx, top - w * 0.45); cc.lineTo(bx, top - w * 0.45 - 22); cc.stroke();
            cc.fillStyle = '#fff2b0'; cc.beginPath(); cc.arc(bx, top - w * 0.45 - 24, 5, 0, TAU); cc.fill();
          } else {
            const sh = w * (0.9 + 0.6 * hash(seed, 4));
            cc.beginPath(); cc.moveTo(bx - w * 0.38, top); cc.quadraticCurveTo(bx - w * 0.3, top - sh * 0.6, bx, top - sh); cc.quadraticCurveTo(bx + w * 0.3, top - sh * 0.6, bx + w * 0.38, top); cc.closePath(); cc.fill(); cc.stroke();
            cc.fillStyle = 'rgba(150,70,0,0.35)'; cc.beginPath(); cc.moveTo(bx, top - sh); cc.quadraticCurveTo(bx + w * 0.3, top - sh * 0.6, bx + w * 0.38, top); cc.lineTo(bx + w * 0.05, top); cc.closePath(); cc.fill();
            cc.fillStyle = '#ff5a2a'; cc.beginPath(); cc.moveTo(bx, top - sh); cc.lineTo(bx, top - sh - 26); cc.lineTo(bx + 18 + 3 * Math.sin(t * 4 + seed), top - sh - 20); cc.lineTo(bx, top - sh - 14); cc.fill();
          }
        }
      });
      // crowning palace of Ravana on the summit
      const py = -430;
      const pg = cc.createLinearGradient(0, py - 120, 0, py); pg.addColorStop(0, '#fff0a0'); pg.addColorStop(1, '#d48a10');
      cc.fillStyle = pg; cc.fillRect(-90, py - 110, 180, 110); cc.strokeStyle = 'rgba(90,46,6,0.55)'; cc.lineWidth = 3; cc.strokeRect(-90, py - 110, 180, 110);
      cc.fillStyle = '#ffd04a';
      cc.beginPath(); cc.moveTo(-80, py - 110); cc.bezierCurveTo(-80, py - 230, 80, py - 230, 80, py - 110); cc.closePath(); cc.fill(); cc.stroke();
      for (const sx of [-110, 110]) { cc.beginPath(); cc.moveTo(sx - 20, py); cc.lineTo(sx - 20, py - 120); cc.quadraticCurveTo(sx, py - 190, sx + 20, py - 120); cc.lineTo(sx + 20, py); cc.closePath(); cc.fill(); cc.stroke(); }
      cc.fillStyle = '#fff6c0'; cc.beginPath(); cc.arc(0, py - 210, 9, 0, TAU); cc.fill();
      cc.strokeStyle = 'rgba(90,46,6,0.55)'; cc.beginPath(); cc.moveTo(0, py - 218); cc.lineTo(0, py - 270); cc.stroke();
      cc.fillStyle = '#ff3a2a'; cc.beginPath(); cc.moveTo(0, py - 270); cc.lineTo(38 + 5 * Math.sin(t * 4), py - 258); cc.lineTo(0, py - 246); cc.fill();
      // crenellated golden fort wall with towers and the main gate
      const wy = 0, wh = 70;
      const wg = cc.createLinearGradient(0, wy - wh, 0, wy); wg.addColorStop(0, '#ffd24a'); wg.addColorStop(1, '#9a5a08');
      cc.fillStyle = wg; cc.fillRect(-520, wy - wh, 1040, wh); cc.strokeStyle = 'rgba(90,46,6,0.55)'; cc.lineWidth = 3; cc.strokeRect(-520, wy - wh, 1040, wh);
      cc.fillStyle = '#ffd24a';
      for (let bx = -520; bx < 520; bx += 34) { cc.fillRect(bx, wy - wh - 16, 18, 16); cc.strokeRect(bx, wy - wh - 16, 18, 16); }
      for (const tx of [-500, -250, 250, 500]) {
        cc.fillStyle = wg; cc.fillRect(tx - 34, wy - wh - 70, 68, wh + 70); cc.strokeRect(tx - 34, wy - wh - 70, 68, wh + 70);
        cc.fillStyle = '#ffd04a'; cc.beginPath(); cc.moveTo(tx - 40, wy - wh - 70); cc.quadraticCurveTo(tx, wy - wh - 140, tx + 40, wy - wh - 70); cc.closePath(); cc.fill(); cc.stroke();
        cc.fillStyle = `rgba(255,220,140,${0.5 + 0.5 * night})`; cc.fillRect(tx - 8, wy - wh - 40, 16, 22);
      }
      cc.fillStyle = '#3a1a04'; cc.beginPath(); cc.moveTo(-50, wy); cc.lineTo(-50, wy - 50); cc.quadraticCurveTo(0, wy - 100, 50, wy - 50); cc.lineTo(50, wy); cc.closePath(); cc.fill();
      cc.strokeStyle = '#ffd24a'; cc.lineWidth = 5; cc.stroke();
      // twinkling lamps along the wall
      for (let i = 0; i < 30; i++) {
        const lx = -510 + i * 35, a = 0.5 + 0.5 * Math.sin(t * 4 + i * 1.7);
        cc.fillStyle = `rgba(255,230,150,${(0.4 + 0.6 * a) * (0.4 + 0.6 * night)})`; cc.beginPath(); cc.arc(lx, wy - wh - 22, 4, 0, TAU); cc.fill();
      }
      cc.restore();
    };
    if (reflect) {                                             // shimmering reflection in the sea
      c.save();
      c.beginPath(); c.rect(-200, baseY, W + 400, 900 * s); c.clip();
      for (let k = 0; k < 24; k++) {
        const y0 = baseY + k * 16 * s;
        c.save(); c.beginPath(); c.rect(-200, y0, W + 400, 16 * s + 1); c.clip();
        c.translate(Math.sin(t * 2 + k * 0.7) * 8 * s, 0);
        c.translate(0, 2 * baseY); c.scale(1, -1);
        c.globalAlpha = 0.32 * (1 - k / 24);
        draw(c);
        c.restore();
      }
      c.restore();
    }
    draw(c);
  }

  global.Lanka = { lanka };
})(typeof window !== 'undefined' ? window : globalThis);
