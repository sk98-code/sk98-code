#!/usr/bin/env node
/*
 * Renders story.html to reel/sampati-jatayu.mp4 with the narration mix.
 *
 *   node story/render-story.mjs [--workers 4]
 *
 * Splits the frames across N headless Chromium pages running in parallel,
 * encodes each chunk, then concatenates and muxes audio/mix.wav.
 */
import { createRequire } from 'node:module';
import { spawn, execFileSync } from 'node:child_process';
import { mkdirSync, writeFileSync, rmSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import os from 'node:os';

const require = createRequire(import.meta.url);
const { chromium } = require('playwright');
const here = dirname(fileURLToPath(import.meta.url));
const args = process.argv.slice(2);
const opt = (n, d) => { const i = args.indexOf(n); return i >= 0 ? args[i + 1] : d; };
const WORKERS = Number(opt('--workers', Math.max(1, os.cpus().length)));
const FPS = 30;
const tmp = join(here, '.render'); mkdirSync(tmp, { recursive: true });
const arg = (k, d) => { const i = process.argv.indexOf(k); return i > 0 ? process.argv[i + 1] : d; };
const out = resolve(here, '..', 'reel', arg('--out', 'sampati-jatayu.mp4'));
mkdirSync(dirname(out), { recursive: true });

const browser = await chromium.launch();
const url = pathToFileURL(join(here, arg('--html', 'story.html'))).href + '?render';
const probe = await browser.newPage();
await probe.goto(url); await probe.waitForFunction(() => window.__ready === true);
const TOTAL = await probe.evaluate(() => window.TOTAL); await probe.close();
const frames = Math.ceil(TOTAL * FPS);
console.log(`${frames} frames (${TOTAL.toFixed(2)} s) on ${WORKERS} workers`);

let done = 0; const t0 = Date.now();
async function chunk(k) {
  const a = Math.floor((frames * k) / WORKERS), b = Math.floor((frames * (k + 1)) / WORKERS);
  const page = await browser.newPage({ viewport: { width: 1080, height: 1920 } });
  page.on('pageerror', (e) => console.error('page error:', e.message));
  await page.goto(url); await page.waitForFunction(() => window.__ready === true);
  const file = join(tmp, `part${k}.mp4`);
  const ff = spawn('ffmpeg', ['-y', '-loglevel', 'error', '-f', 'image2pipe', '-framerate', String(FPS), '-c:v', 'mjpeg', '-i', '-',
    '-c:v', 'libx264', '-preset', 'medium', '-crf', '19', '-pix_fmt', 'yuv420p', file], { stdio: ['pipe', 'inherit', 'inherit'] });
  const closed = new Promise((ok, no) => ff.on('close', (c) => (c ? no(new Error('ffmpeg ' + c)) : ok())));
  for (let f = a; f < b; f++) {
    const b64 = await page.evaluate((t) => { window.renderFrame(t); return document.querySelector('canvas').toDataURL('image/jpeg', 0.95).split(',')[1]; }, f / FPS);
    if (!ff.stdin.write(Buffer.from(b64, 'base64'))) await new Promise((r) => ff.stdin.once('drain', r));
    if (++done % 150 === 0) {
      const el = (Date.now() - t0) / 1000;
      console.log(`${done}/${frames}  ${el.toFixed(0)} s elapsed, ~${(el / done * (frames - done)).toFixed(0)} s left`);
    }
  }
  ff.stdin.end(); await closed; await page.close();
  return file;
}
const parts = await Promise.all(Array.from({ length: WORKERS }, (_, k) => chunk(k)));
await browser.close();
writeFileSync(join(tmp, 'list.txt'), parts.map((p) => `file '${p}'`).join('\n'));
execFileSync('ffmpeg', ['-y', '-loglevel', 'error', '-f', 'concat', '-safe', '0', '-i', join(tmp, 'list.txt'),
  '-i', join(here, arg('--audio', 'audio/mix.wav')), '-map', '0:v', '-map', '1:a', '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k',
  '-shortest', '-movflags', '+faststart', out]);
rmSync(tmp, { recursive: true, force: true });
console.log(`wrote ${out} in ${((Date.now() - t0) / 1000).toFixed(0)} s`);
