/*
 * रंगीन जटायु / सम्पाती — v3 reel वाले colourful (real-mode) पक्षी, अकेले।
 * Rig: Shapes.rigFromPose (shapes.js), Paint: AnimeKit.drawBird (anime-kit.js) in 'real' mode.
 * URL: ?pal=youngJ | oldJ | youngS | golden   (youngJ = जवान जटायु, youngS = जवान सम्पाती)
 */
(function () {
  const S = window.Shapes, K = window.AnimeKit, TAU = Math.PI * 2;
  const PALS = {
    youngJ: { feather: ['#b89070', '#6a4a32', '#33221a', '#e8d6c0'], featherFar: ['#8a6a50', '#4a3424', '#22160e', '#b8a080'], covert: ['#c8a07a', '#7a5638', '#3e2818', '#f0dcc4'], body: ['#a07a5a', '#5a3e2a', '#2a1a10'], skin: ['#f0c8c0', '#d89a98', '#a86870'] },
    oldJ:   { feather: ['#c8c0b8', '#7e7670', '#3e3834', '#efe8e0'], featherFar: ['#948c86', '#57504c', '#2c2826', '#c0b8b0'], covert: ['#dcd4cc', '#968e86', '#544c46', '#f6f0e8'], body: ['#aaa29a', '#655e58', '#34302c'], skin: ['#f6d8d6', '#dca4a8', '#a8707a'] },
    youngS: { feather: ['#c48a5c', '#7a4428', '#3a1a0c', '#f0d0b0'], featherFar: ['#94603e', '#552a16', '#2a1208', '#c49a78'], covert: ['#d8a070', '#925430', '#4e2810', '#f8e0c4'], body: ['#b07a52', '#6a3820', '#36180a'], ruff: ['#fff6e0', '#efe0c0', '#c8b088'] },
    golden: { feather: ['#ffe8a8', '#e0a848', '#8a5a1a', '#fff6d8'], featherFar: ['#d8b070', '#9a6828', '#4e340e', '#f4dca8'], covert: ['#fff0c4', '#ecbc6c', '#a4702a', '#fffbe8'], body: ['#dca060', '#8e5828', '#4a2c0e'] },
  };
  // पंख फड़फड़ाने का pose (theta = पंख का कोण, lag = पीछे चलने वाले पंखों के लिए)
  function flyPose(t, o = {}) {
    const f = o.freq ?? 1.8, amp = o.amp ?? 0.85, cen = o.center ?? 3.62, ph = o.phase ?? 0;
    const th = (tt) => cen + amp * Math.cos(TAU * (f * tt + ph));
    return { pose: { spread: 1, theta: th(t), talons: 0, pitch: 0, tailSpread: 0.5, jaw: o.jaw ?? 0, grip: 0, wingScale: 1, headRot: 0, neck: 0 }, lag: th(t - 0.07) };
  }
  // x,y = स्थान, s = आकार, heading = दिशा (radian)
  function drawJatayu(c, x, y, s, heading, fp, opt = {}) {
    const R = S.rigFromPose(fp.pose, fp.lag);
    c.save(); c.translate(x, y); c.rotate(heading); c.scale(opt.flip ? 1 : -1, 1);
    K.setMode('real');
    K.drawBird(c, { x: 0, y: 0, scale: s, pitch: 0 }, R, opt);
    K.setMode('cel');
    c.restore();
  }
  const pal = PALS[new URLSearchParams(location.search).get('pal')] || PALS[window.DEFAULT_PAL || 'youngJ'];
  const cv = document.getElementById('c'), c = cv.getContext('2d');
  function frame(t) {
    c.setTransform(1, 0, 0, 1, 0, 0);
    const g = c.createLinearGradient(0, 0, 0, cv.height);
    g.addColorStop(0, '#14306a'); g.addColorStop(0.5, '#4a76b8'); g.addColorStop(0.8, '#f0b890'); g.addColorStop(1, '#ffe0b8');
    c.fillStyle = g; c.fillRect(0, 0, cv.width, cv.height);
    drawJatayu(c, cv.width / 2, cv.height / 2 + 20 * Math.sin(t * 1.5), 1.4, -0.1, flyPose(t), { pal });
  }
  window.renderFrame = frame; window.__ready = true; window.TOTAL = 4; window.PALS = PALS; window.drawJatayu = drawJatayu; window.flyPose = flyPose;
  const t0 = performance.now();
  (function loop(now) { frame((now - t0) / 1000); requestAnimationFrame(loop); })(t0);
})();
