#!/usr/bin/env node
/*
 * Frame-by-frame renderer for the style animations.
 *
 *   node render.mjs anims/anime.html assets/clips/anime.mp4
 *   node render.mjs anims/anime.html --frames 0.6,2.0,4 --out previews
 *
 * Loads the HTML in headless Chromium, calls window.renderFrame(t) for each of
 * the 150 frames (t = frame / 30) and pipes the PNGs into FFmpeg (H.264, yuv420p).
 * Because every frame is a pure function of t, renders are identical every run.
 */
import { createRequire } from 'node:module';
import { spawn } from 'node:child_process';
import { mkdirSync, writeFileSync } from 'node:fs';
import { resolve, dirname, basename, join } from 'node:path';
import { pathToFileURL } from 'node:url';

const require = createRequire(import.meta.url);
const { chromium } = require('playwright');

const args = process.argv.slice(2);
const opt = (name) => { const i = args.indexOf(name); return i >= 0 ? args[i + 1] : undefined; };
const positional = args.filter((a, i) => !a.startsWith('--') && !(i > 0 && args[i - 1].startsWith('--')));
const [input, output] = positional;
if (!input) {
  console.error('usage: node render.mjs <anim.html> [out.mp4] [--frames 0.6,2,4 --out dir] [--fps 30 --dur 5]');
  process.exit(1);
}
const FPS = Number(opt('--fps') ?? 30);
const DUR = Number(opt('--dur') ?? 5);

const browser = await chromium.launch(process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : {});
const page = await browser.newPage({ viewport: { width: 1080, height: 1920 } });
page.on('pageerror', (e) => console.error('page error:', e.message));
await page.goto(pathToFileURL(resolve(input)).href + '?render');
await page.waitForFunction(() => window.__ready === true);

const grab = (t) => page.evaluate((t) => {
  window.renderFrame(t);
  return document.querySelector('canvas').toDataURL('image/png').split(',')[1];
}, t);

const stills = opt('--frames');
if (stills) {
  const outDir = opt('--out') ?? 'previews';
  mkdirSync(outDir, { recursive: true });
  const name = basename(input, '.html');
  for (const s of stills.split(',')) {
    const t = Number(s);
    const file = join(outDir, `${name}-t${t.toFixed(2)}.png`);
    writeFileSync(file, Buffer.from(await grab(t), 'base64'));
    console.log('wrote', file);
  }
  await browser.close();
  process.exit(0);
}

const out = output ?? `assets/clips/${basename(input, '.html')}.mp4`;
mkdirSync(dirname(out), { recursive: true });
const ff = spawn('ffmpeg', [
  '-y', '-loglevel', 'error',
  '-f', 'image2pipe', '-framerate', String(FPS), '-c:v', 'png', '-i', '-',
  '-c:v', 'libx264', '-preset', 'slow', '-crf', '18', '-pix_fmt', 'yuv420p',
  '-movflags', '+faststart', out,
], { stdio: ['pipe', 'inherit', 'inherit'] });
const done = new Promise((ok, fail) => ff.on('close', (c) => (c === 0 ? ok() : fail(new Error('ffmpeg exit ' + c)))));

const total = Math.round(FPS * DUR);
const started = Date.now();
for (let f = 0; f < total; f++) {
  const buf = Buffer.from(await grab(f / FPS), 'base64');
  if (!ff.stdin.write(buf)) await new Promise((r) => ff.stdin.once('drain', r));
  if (f % 15 === 14 || f === total - 1) process.stdout.write(`\rframe ${f + 1}/${total}`);
}
ff.stdin.end();
await done;
await browser.close();
console.log(`\nwrote ${out} in ${((Date.now() - started) / 1000).toFixed(1)} s`);
