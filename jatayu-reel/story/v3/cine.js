/*
 * Cinematic-realism environment kit (code only, deterministic).
 * Noise-textured skies and clouds, layered mountains with rock texture and
 * atmospheric haze, sea with glitter and reflections, forests, fire, smoke,
 * light rays, fog. Everything is a pure function of time.
 */
(function (global) {
  'use strict';
  const S = global.Shapes;
  const { W, H, TAU, clamp, lerp, smooth, hash } = S;

  // ------------------------------------------------------------ value noise / fbm
  const perm = new Uint8Array(512);
  for (let i = 0; i < 256; i++) perm[i] = i;
  for (let i = 255; i > 0; i--) { const j = Math.floor(hash(i, 777) * (i + 1)); const t = perm[i]; perm[i] = perm[j]; perm[j] = t; }
  for (let i = 0; i < 256; i++) perm[256 + i] = perm[i];
  const grad = (h, x, y) => { const g = h & 7; const u = g < 4 ? x : y, v = g < 4 ? y : x; return ((g & 1) ? -u : u) + ((g & 2) ? -2 * v : 2 * v); };
  const fade = (t) => t * t * t * (t * (t * 6 - 15) + 10);
  function noise2(x, y) {
    const X = Math.floor(x) & 255, Y = Math.floor(y) & 255;
    x -= Math.floor(x); y -= Math.floor(y);
    const u = fade(x), v = fade(y);
    const a = perm[X] + Y, b = perm[X + 1] + Y;
    return lerp(lerp(grad(perm[a], x, y), grad(perm[b], x - 1, y), u), lerp(grad(perm[a + 1], x, y - 1), grad(perm[b + 1], x - 1, y - 1), u), v) * 0.25 + 0.5;
  }
  function fbm(x, y, oct = 5) { let s = 0, a = 0.5, f = 1, n = 0; for (let i = 0; i < oct; i++) { s += a * noise2(x * f, y * f); n += a; a *= 0.5; f *= 2.03; } return s / n; }

  const cache = {};
  const mk = (w, h) => { const c = document.createElement('canvas'); c.width = w; c.height = h; return c; };

  /** Cloud texture (white, alpha from fbm). Tileable-ish horizontally by wrapping. */
  function cloudTex(key, w, h, scale, lo, hi, oct = 6) {
    if (cache[key]) return cache[key];
    const cv = mk(w, h), cx = cv.getContext('2d'), img = cx.createImageData(w, h), d = img.data;
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
      const nx = x / w, ny = y / h;
      // blend two samples so the left/right edges wrap
      const n = lerp(fbm(nx * scale, ny * scale * 0.5, oct), fbm((nx + 1) * scale, ny * scale * 0.5, oct), nx);
      const env = Math.sin(Math.PI * ny) ** 1.5;
      const a = clamp((n - lo) / (hi - lo)) * env;
      const i = 4 * (y * w + x);
      const shade = 200 + 55 * clamp((n - lo) / (hi - lo) * 1.4);
      d[i] = d[i + 1] = d[i + 2] = shade; d[i + 3] = 255 * a * a * (3 - 2 * a);
    }
    cx.putImageData(img, 0, 0);
    return (cache[key] = cv);
  }

  /** Grayscale grain texture for rock / bark / water detail. */
  function grainTex(key, w, h, scale) {
    if (cache[key]) return cache[key];
    const cv = mk(w, h), cx = cv.getContext('2d'), img = cx.createImageData(w, h), d = img.data;
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
      const n = fbm(x / w * scale, y / h * scale * 2.2, 5);
      const v = Math.floor(255 * clamp((n - 0.3) * 1.8));
      const i = 4 * (y * w + x); d[i] = d[i + 1] = d[i + 2] = v; d[i + 3] = 255;
    }
    cx.putImageData(img, 0, 0);
    return (cache[key] = cv);
  }

  /** Tinted copy of a white texture (cached per tint). */
  function tint(tex, color, key) {
    const k = key + color;
    if (cache[k]) return cache[k];
    const cv = mk(tex.width, tex.height), cx = cv.getContext('2d');
    cx.drawImage(tex, 0, 0);
    cx.globalCompositeOperation = 'source-atop'; cx.fillStyle = color; cx.fillRect(0, 0, cv.width, cv.height);
    cx.globalCompositeOperation = 'multiply'; cx.drawImage(tex, 0, 0);
    cx.globalCompositeOperation = 'destination-in'; cx.drawImage(tex, 0, 0);
    return (cache[k] = cv);
  }

  // ------------------------------------------------------------ sky
  function sky(c, stops) {
    const g = c.createLinearGradient(0, 0, 0, H);
    for (const [o, col] of stops) g.addColorStop(o, col);
    c.fillStyle = g; c.fillRect(-300, -300, W + 600, H + 600);
  }

  function glow(c, x, y, r, inner, mid, outer = 'rgba(0,0,0,0)') {
    const g = c.createRadialGradient(x, y, 0, x, y, r);
    g.addColorStop(0, inner); if (mid) g.addColorStop(0.22, mid); g.addColorStop(1, outer);
    c.save(); c.globalCompositeOperation = 'screen'; c.fillStyle = g; c.fillRect(x - r, y - r, 2 * r, 2 * r); c.restore();
  }

  function sun(c, x, y, r, core = '#fffbea', halo = 'rgba(255,200,120,0.55)') {
    glow(c, x, y, r * 9, 'rgba(255,220,160,0.55)', 'rgba(255,170,90,0.25)');
    glow(c, x, y, r * 3.2, 'rgba(255,245,215,0.95)', halo);
    c.fillStyle = core; c.beginPath(); c.arc(x, y, r, 0, TAU); c.fill();
  }

  function rays(c, x, y, t, alpha = 0.12, n = 14, len = 2200, col = '255,236,200') {
    c.save(); c.globalCompositeOperation = 'screen';
    for (let i = 0; i < n; i++) {
      const a = (i / n) * TAU + 0.04 * Math.sin(t * 0.4 + i * 1.7);
      const w = 0.02 + 0.045 * hash(i, 501), al = alpha * (0.5 + hash(i, 502));
      const g = c.createRadialGradient(x, y, 30, x, y, len);
      g.addColorStop(0, `rgba(${col},${al})`); g.addColorStop(1, `rgba(${col},0)`);
      c.fillStyle = g; c.beginPath(); c.moveTo(x, y);
      c.lineTo(x + Math.cos(a - w) * len, y + Math.sin(a - w) * len); c.lineTo(x + Math.cos(a + w) * len, y + Math.sin(a + w) * len); c.closePath(); c.fill();
    }
    c.restore();
  }

  function stars(c, n, alpha, t, seed = 1, maxY = H * 0.6) {
    for (let i = 0; i < n; i++) {
      const tw = 0.55 + 0.45 * Math.sin(t * 2 + i * 1.7), r = 0.6 + 1.8 * hash(i, seed, 5) * tw;
      c.fillStyle = `rgba(255,250,240,${alpha * (0.3 + 0.7 * hash(i, seed, 2))})`;
      c.beginPath(); c.arc(hash(i, seed, 3) * W, hash(i, seed, 4) * maxY, r, 0, TAU); c.fill();
    }
  }

  /**
   * A layer of realistic clouds: lit tops, shadowed bellies, soft edges.
   * y = band centre, h = band height, drift = horizontal offset.
   */
  function clouds(c, y, h, drift, lit, shade, light = { x: 0, y: -1 }, alpha = 1, seed = 1, scale = 5) {
    const tex = cloudTex('cl' + seed + ':' + scale, 640, 240, scale, 0.42, 0.7);
    const tl = tint(tex, lit, 'L' + seed + scale), ts = tint(tex, shade, 'S' + seed + scale);
    const w = W * 1.6, x0 = ((-drift % w) + w) % w - w;
    c.save(); c.globalAlpha *= alpha;
    for (let x = x0; x < W + 10; x += w) {
      c.drawImage(ts, x, y - h / 2, w, h);
      c.drawImage(tl, x + light.x * h * 0.05, y - h / 2 + light.y * h * 0.05, w, h * 0.96);
    }
    c.restore();
  }

  // ------------------------------------------------------------ mountains, haze
  function ridgePts(baseY, amp, seed, off, jag, step = 14) {
    const pts = [];
    for (let x = -300; x <= W + 300; x += step) {
      const X = (x + off) * 0.0016;
      const big = Math.pow(fbm(X * 1.2 + seed, seed * 3.1, 3), 1.6 * jag);
      const det = fbm(X * 9 + seed * 7, seed, 4);
      pts.push([x, baseY - amp * (big * 1.5 - 0.2) - amp * 0.18 * (det - 0.5)]);
    }
    return pts;
  }

  /**
   * Mountain layers far → near: {y, amp, seed, col, rim, snow, haze, off, jag, rock}
   */
  function mountains(c, list, light, t = 0) {
    for (const M of list) {
      const pts = ridgePts(M.y, M.amp, M.seed, M.off || 0, M.jag ?? 1);
      const p = new Path2D(); p.moveTo(-300, H + 400);
      for (const [x, y] of pts) p.lineTo(x, y);
      p.lineTo(W + 300, H + 400); p.closePath();
      const top = Math.min(...pts.map((q) => q[1]));
      const g = c.createLinearGradient(0, top, 0, M.y + 300);
      g.addColorStop(0, M.col[0]); g.addColorStop(1, M.col[1]);
      c.fillStyle = g; c.fill(p);
      c.save(); c.clip(p);
      if (M.rock !== false) {                                   // rock texture
        c.globalAlpha = M.rock ?? 0.22; c.globalCompositeOperation = 'overlay';
        c.drawImage(grainTex('rock', 512, 512, 14), -300, top - 50, W + 600, (M.y + 400) - top);
        c.globalCompositeOperation = 'source-over'; c.globalAlpha = 1;
      }
      if (M.snow) {                                             // snow on the high parts, lit side brighter
        const sp = new Path2D(); sp.moveTo(-300, -500);
        for (const [x, y] of pts) sp.lineTo(x, y + M.amp * (0.18 + 0.1 * fbm(x * 0.01, M.seed, 3)));
        sp.lineTo(W + 300, -500); sp.closePath();
        c.fillStyle = M.snow; c.fill(sp);
      }
      if (M.rim) {                                              // lit crest
        const rg = c.createRadialGradient(light.x, light.y, 40, light.x, light.y, M.rimR || 1300);
        rg.addColorStop(0, M.rim); rg.addColorStop(1, 'rgba(0,0,0,0)');
        c.strokeStyle = rg; c.lineWidth = 7; c.stroke(p);
      }
      if (M.haze) {                                             // atmospheric haze at the foot
        const hg = c.createLinearGradient(0, top, 0, M.y + 120);
        hg.addColorStop(0, 'rgba(0,0,0,0)'); hg.addColorStop(1, M.haze);
        c.fillStyle = hg; c.fillRect(-300, top, W + 600, M.y + 400 - top);
      }
      c.restore();
    }
  }

  function fogBand(c, y, h, color, t, seed = 3, alpha = 0.6) {
    const tex = cloudTex('fog' + seed, 640, 160, 4, 0.35, 0.8, 4);
    const tt = tint(tex, color, 'F' + seed);
    const w = W * 1.8, x0 = ((-t * 14 % w) + w) % w - w;
    c.save(); c.globalAlpha *= alpha;
    for (let x = x0; x < W + 10; x += w) c.drawImage(tt, x, y - h / 2, w, h);
    const g = c.createLinearGradient(0, y - h / 2, 0, y + h / 2);
    g.addColorStop(0, 'rgba(0,0,0,0)'); g.addColorStop(0.5, color); g.addColorStop(1, 'rgba(0,0,0,0)');
    c.globalAlpha *= 0.5; c.fillStyle = g; c.fillRect(-100, y - h / 2, W + 200, h);
    c.restore();
  }

  // ------------------------------------------------------------ sea
  function sea(c, y0, t, top, bottom, sunX, sunCol = '255,215,150') {
    const g = c.createLinearGradient(0, y0, 0, H); g.addColorStop(0, top); g.addColorStop(1, bottom);
    c.fillStyle = g; c.fillRect(-300, y0, W + 600, H - y0 + 600);
    c.save();
    for (let i = 0; i < 70; i++) {                              // perspective wave strokes
      const k = i / 70, yy = y0 + 4 + k * k * (H - y0);
      const xx = ((hash(i, 61) * W * 1.4 + t * (10 + 40 * k)) % (W + 300)) - 150, l = 20 + 160 * k;
      c.strokeStyle = `rgba(180,210,255,${0.05 + 0.12 * k})`; c.lineWidth = 1 + 3 * k;
      c.beginPath(); c.moveTo(xx - l, yy); c.quadraticCurveTo(xx, yy - 3 * k, xx + l, yy); c.stroke();
      c.strokeStyle = `rgba(0,0,10,${0.08 + 0.12 * k})`; c.beginPath(); c.moveTo(xx - l, yy + 3 + 4 * k); c.quadraticCurveTo(xx, yy + 1, xx + l, yy + 3 + 4 * k); c.stroke();
    }
    if (sunX !== undefined) {                                   // glitter path
      c.globalCompositeOperation = 'screen';
      for (let i = 0; i < 160; i++) {
        const k = hash(i, 71), yy = y0 + k * k * (H - y0);
        const spread = 30 + 420 * k * k, xx = sunX + (hash(i, 72) - 0.5) * spread + Math.sin(t * 2 + i) * 6;
        const tw = 0.5 + 0.5 * Math.sin(t * 6 + i * 2.3);
        c.fillStyle = `rgba(${sunCol},${(0.25 + 0.6 * tw) * (1 - 0.5 * k)})`;
        c.fillRect(xx, yy, 6 + 40 * k, 1.5 + 3 * k);
      }
    }
    c.restore();
  }

  // ------------------------------------------------------------ vegetation
  function foliage(c, cx, cy, rx, ry, seed, col, rim, light, n = 28) {
    // one outline with a leafy, noise-broken edge (no inner seams)
    const p = new Path2D();
    const m = 160;
    for (let i = 0; i <= m; i++) {
      const a = (i / m) * TAU;
      const r = 0.62 + 0.5 * fbm(Math.cos(a) * 1.4 + seed, Math.sin(a) * 1.4 + seed * 0.3, 3) + 0.09 * (noise2(a * 26 + seed, seed) - 0.5) * 2;
      const x = cx + Math.cos(a) * rx * r, y = cy + Math.sin(a) * ry * r;
      i ? p.lineTo(x, y) : p.moveTo(x, y);
    }
    p.closePath();
    c.fillStyle = col; c.fill(p);
    c.save(); c.clip(p);                                         // leaf texture
    c.globalAlpha = 0.25; c.globalCompositeOperation = 'overlay';
    c.drawImage(grainTex('leaf', 256, 256, 22), cx - rx, cy - ry, rx * 2, ry * 2);
    c.restore();
    if (rim) {
      c.save(); c.clip(p);
      let dx = light.x - cx, dy = light.y - cy; const l = Math.hypot(dx, dy) || 1; dx /= l; dy /= l;
      c.fillStyle = rim; c.fillRect(cx - rx * 2, cy - ry * 2, rx * 4, ry * 4);
      c.translate(-dx * 10, -dy * 10); c.fillStyle = col; c.fill(p);
      c.restore();
    }
    return p;
  }

  let rbuf = null;
  /** Paint shapes with draw(ctx, colour), then light only the outer edge facing the light. */
  function rimGroup(c, draw, col, rim, light, w = 9) {
    if (!rim) { draw(c, col); return; }
    if (!rbuf) { rbuf = mk(c.canvas.width, c.canvas.height); }
    const b = rbuf.getContext('2d');
    b.setTransform(1, 0, 0, 1, 0, 0); b.clearRect(0, 0, rbuf.width, rbuf.height);
    b.setTransform(c.getTransform());
    draw(b, rim);
    b.globalCompositeOperation = 'source-atop';
    const m = c.getTransform(), lx = (light.x - W / 2), ly = (light.y - H * 0.4), l = Math.hypot(lx, ly) || 1;
    b.translate(-lx / l * w, -ly / l * w);
    draw(b, col);
    b.globalCompositeOperation = 'source-over';
    c.save(); c.setTransform(1, 0, 0, 1, 0, 0); c.drawImage(rbuf, 0, 0); c.restore();
  }

  /** Realistic tree silhouette: trunk + branches + clumped foliage. */
  function tree(c, x, y, h, seed, col, rim, light, trunk = '#1a120c') {
    rimGroup(c, (cc, fill) => treeShape(cc, x, y, h, seed, fill, fill === col ? trunk : fill), col, rim, light, 8);
  }
  function treeShape(c, x, y, h, seed, col, trunk) {
    const w = h * 0.06;
    c.fillStyle = trunk;
    c.beginPath(); c.moveTo(x - w, y); c.quadraticCurveTo(x - w * 0.6, y - h * 0.5, x - w * 0.3 + (hash(seed, 1) - 0.5) * 20, y - h * 0.72);
    c.lineTo(x + w * 0.3 + (hash(seed, 1) - 0.5) * 20, y - h * 0.72); c.quadraticCurveTo(x + w * 0.6, y - h * 0.5, x + w, y); c.closePath(); c.fill();
    c.strokeStyle = trunk; c.lineCap = 'round';
    for (let i = 0; i < 4; i++) {
      const by = y - h * (0.4 + 0.1 * i), sd = i % 2 ? 1 : -1;
      c.lineWidth = w * (0.7 - i * 0.1); c.beginPath(); c.moveTo(x, by); c.quadraticCurveTo(x + sd * h * 0.12, by - h * 0.06, x + sd * h * (0.2 + 0.05 * hash(seed, i)), by - h * 0.14); c.stroke();
    }
    const clumps = [[0, 0.78, 0.32, 0.18], [-0.2, 0.62, 0.22, 0.14], [0.22, 0.6, 0.22, 0.14], [-0.08, 0.92, 0.2, 0.12], [0.12, 0.88, 0.18, 0.11]];
    clumps.forEach(([dx, dy, rx, ry], i) => foliage(c, x + dx * h, y - dy * h, rx * h, ry * h, seed * 10 + i, col, null, null, 22));
  }

  function forestLine(c, y, h, col, rim, light, seed, density = 1) {
    rimGroup(c, (cc, fill) => {
      for (let i = 0; i < 18 * density; i++) {
        const x = -60 + (i / (18 * density)) * (W + 120) + (hash(seed, i, 7) - 0.5) * 50;
        foliage(cc, x, y - h * (0.45 + 0.35 * hash(seed, i, 8)), 70 + 50 * hash(seed, i, 9), h * 0.55, seed * 100 + i, fill, null, null, 14);
      }
      cc.fillStyle = fill; cc.fillRect(-100, y - h * 0.2, W + 200, H);
    }, col, rim, light, 8);
  }

  function grass(c, y, t, col, seed = 3, hgt = 120) {
    c.fillStyle = col;
    c.beginPath(); c.moveTo(-50, H + 10); c.lineTo(-50, y);
    for (let i = 0; i <= 120; i++) {
      const x = -50 + i * 10, h = hgt * (0.4 + 0.6 * hash(i, seed, 1)), sw = Math.sin(t * 1.4 + i * 0.5) * 6;
      c.lineTo(x + 2, y); c.quadraticCurveTo(x + 3 + sw * 0.5, y - h * 0.5, x + 5 + sw, y - h); c.quadraticCurveTo(x + 6, y - h * 0.4, x + 8, y);
    }
    c.lineTo(W + 50, y); c.lineTo(W + 50, H + 10); c.closePath(); c.fill();
  }

  // ------------------------------------------------------------ particles
  function motes(c, t, n, rgb, seed, x0 = 0, y0 = 0, w = W, h = H, size = 3) {
    c.save(); c.globalCompositeOperation = 'screen';
    for (let i = 0; i < n; i++) {
      const x = x0 + ((hash(i, seed, 1) * w + Math.sin(t * 0.5 + i) * 30 + t * 10) % w);
      const y = y0 + ((hash(i, seed, 2) * h - t * (6 + 12 * hash(i, seed, 3))) % h + h) % h;
      const a = 0.25 + 0.5 * Math.sin(t * 1.7 + i * 1.3) ** 2, r = size * (0.6 + hash(i, seed, 4));
      const g = c.createRadialGradient(x, y, 0, x, y, r * 4);
      g.addColorStop(0, `rgba(${rgb},${a})`); g.addColorStop(1, `rgba(${rgb},0)`);
      c.fillStyle = g; c.fillRect(x - r * 4, y - r * 4, r * 8, r * 8);
    }
    c.restore();
  }

  function fire(c, x, y, w, h, t, seed = 1, intensity = 1) {
    c.save(); c.globalCompositeOperation = 'lighter';
    const n = Math.round(60 * intensity);
    for (let i = 0; i < n; i++) {
      const life = 0.8 + 0.6 * hash(i, seed, 1), k = ((t + hash(i, seed, 2) * life) % life) / life;
      const px = x + (hash(i, seed, 3) - 0.5) * w * (1 - k * 0.6) + Math.sin(t * 7 + i) * 10 * k;
      const py = y - k * h * (0.6 + 0.6 * hash(i, seed, 4));
      const r = (w * 0.12) * (1 - k) + 6;
      const col = k < 0.3 ? '255,240,190' : k < 0.6 ? '255,170,60' : '220,70,20';
      const g = c.createRadialGradient(px, py, 0, px, py, r);
      g.addColorStop(0, `rgba(${col},${0.55 * (1 - k)})`); g.addColorStop(1, `rgba(${col},0)`);
      c.fillStyle = g; c.fillRect(px - r, py - r, 2 * r, 2 * r);
    }
    c.restore();
  }

  function embers(c, x, y, w, h, t, n, seed) {
    c.save(); c.globalCompositeOperation = 'lighter';
    for (let i = 0; i < n; i++) {
      const sp = 50 + 90 * hash(i, seed, 1), life = h / sp, k = ((t + hash(i, seed, 2) * life) % life) / life;
      const px = x + (hash(i, seed, 3) - 0.5) * w + Math.sin(t * 2 + i) * 22 * k, py = y - k * h;
      c.fillStyle = `rgba(255,${150 + 80 * hash(i, seed, 5)},80,${(1 - k) * 0.9})`;
      c.beginPath(); c.arc(px, py, 1.5 + 2.5 * hash(i, seed, 4), 0, TAU); c.fill();
    }
    c.restore();
  }

  function smoke(c, x, y, t, seed, n = 18, rgb = '60,55,60', spread = 1) {
    for (let i = 0; i < n; i++) {
      const life = 4, k = ((t * 0.5 + hash(i, seed, 1) * life) % life) / life;
      const px = x + (hash(i, seed, 2) - 0.5) * 60 * spread + Math.sin(t * 0.6 + i) * 40 * k * spread, py = y - k * 700;
      const r = 40 + 140 * k;
      const g = c.createRadialGradient(px, py, 0, px, py, r);
      g.addColorStop(0, `rgba(${rgb},${0.35 * (1 - k)})`); g.addColorStop(1, `rgba(${rgb},0)`);
      c.fillStyle = g; c.fillRect(px - r, py - r, 2 * r, 2 * r);
    }
  }

  // ------------------------------------------------------------ camera, post
  /** Slow cinematic camera: zoom z, focus (fx, fy); returns nothing, applies transform. */
  function camera(c, z, fx, fy, rot = 0) {
    c.translate(W / 2, H / 2); c.rotate(rot); c.scale(z, z); c.translate(-fx, -fy);
  }

  global.Cine = { rimGroup, noise2, fbm, cloudTex, grainTex, tint, sky, glow, sun, rays, stars, clouds, ridgePts, mountains, fogBand, sea,
                  foliage, tree, forestLine, grass, motes, fire, embers, smoke, camera };
})(typeof window !== 'undefined' ? window : globalThis);
