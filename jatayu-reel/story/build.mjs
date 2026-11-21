#!/usr/bin/env node
/*
 * Builds the story timeline and the soundtrack.
 *
 *   node story/build.mjs
 *
 * - reads audio/s1..s8.mp3 (Hindi narration, one clip per scene)
 * - finds pauses with ffmpeg silencedetect and times each caption chunk over
 *   the speech (proportional to characters, skipping silences)
 * - writes timeline.js (window.TIMELINE) for story.html
 * - writes audio/mix.wav: narration placed on the timeline + tanpura-style drone
 *   + low impact hits, loudness-normalised to -14 LUFS
 */
import { execFileSync, spawnSync } from 'node:child_process';
import { writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const A = (f) => join(here, 'audio', f);

const TITLE = 3.2;          // title card before scene 1
const LEAD = 0.45;          // silence at the start of each scene before the voice
const TAIL = [1.0, 0.8, 1.3, 1.3, 1.4, 1.0, 1.2, 2.2]; // breathing room after each line

const CAPTIONS = [
  ['सूर्य के सारथी अरुण के दो पुत्र थे...', 'बड़ा भाई सम्पाती,', 'और छोटा भाई जटायु।'],
  ['एक दिन दोनों भाइयों में होड़ लगी —', 'कौन पहले सूर्य को छुएगा?', 'दोनों आकाश को चीरते हुए,', 'ऊपर... और ऊपर उड़ते गए।'],
  ['पर सूर्य का तेज असहनीय था।', 'जटायु के पंख जलने लगे!', 'तब सम्पाती ने अपने विशाल पंख फैलाकर,',
   'छोटे भाई को ढक लिया।', 'जटायु बच गया...', 'पर सम्पाती के पंख जल गए,', 'और वह विंध्य पर्वत पर जा गिरा।'],
  ['बरसों बीत गए।', 'एक दिन जटायु ने देखा —', 'रावण, माता सीता का हरण करके,', 'पुष्पक विमान में लिए जा रहा था।',
   'बूढ़ा जटायु अकेले ही रावण से भिड़ गया!', 'पर रावण ने अपनी तलवार से...', 'उसके पंख काट दिए।'],
  ['राम-लक्ष्मण पहुँचे।', 'जटायु ने अंतिम साँस में कहा —', 'रावण सीता को दक्षिण ले गया।', 'श्री राम ने स्वयं उसका अंतिम संस्कार किया।'],
  ['उधर विंध्य पर्वत पर,', 'सीता को खोजते हुए', 'हनुमान, अंगद और जाम्बवान', 'थककर बैठे थे।',
   'जटायु का नाम सुनते ही,', 'सम्पाती चौंक उठा।', 'भाई की मृत्यु का समाचार सुनकर...', 'उसकी आँखें भर आईं।'],
  ['सम्पाती ने अपनी दिव्य दृष्टि से देखा —', 'सागर के पार,', 'लंका की अशोक वाटिका में,', 'माता सीता बैठी हैं।',
   'और उसी क्षण...', 'उसके नए पंख उग आए!'],
  ['एक भाई ने सूर्य से भाई को बचाया...', 'दूसरे ने धर्म के लिए प्राण दिए।', 'यही है सम्पाती और जटायु की अमर कथा।', 'जय श्री राम!'],
];

const duration = (f) => Number(execFileSync('ffprobe', ['-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', f]).toString());

function silences(f, dur) {
  const r = spawnSync('ffmpeg', ['-hide_banner', '-i', f, '-af', 'silencedetect=noise=-36dB:d=0.2', '-f', 'null', '-'], { encoding: 'utf8' });
  const out = [];
  let start = null;
  for (const m of r.stderr.matchAll(/silence_(start|end): ([\d.]+)/g)) {
    if (m[1] === 'start') start = Number(m[2]);
    else if (start !== null) { out.push([start, Number(m[2])]); start = null; }
  }
  if (start !== null) out.push([start, dur]);
  return out;
}

/** Speech time -> real time, skipping silences. */
function speechMap(dur, sil) {
  const islands = [];
  let t = 0;
  for (const [a, b] of sil) { if (a > t) islands.push([t, a]); t = b; }
  if (t < dur) islands.push([t, dur]);
  const total = islands.reduce((s, [a, b]) => s + (b - a), 0);
  return (u) => {               // u in [0,1] of speech
    let need = u * total;
    for (const [a, b] of islands) { if (need <= b - a) return a + need; need -= b - a; }
    return dur;
  };
}

const scenes = [];
let t = TITLE;
for (let i = 0; i < 8; i++) {
  const f = A(`s${i + 1}.mp3`);
  const dur = duration(f);
  const map = speechMap(dur, silences(f, dur));
  const chunks = CAPTIONS[i];
  const weights = chunks.map((c) => c.replace(/[\s.,—!?।]/g, '').length);
  const total = weights.reduce((a, b) => a + b, 0);
  let acc = 0;
  const cues = chunks.map((text, k) => {
    const a = map(acc / total); acc += weights[k];
    const b = map(acc / total);
    return { text, start: +(LEAD + a).toFixed(3), end: +(LEAD + b).toFixed(3) };
  });
  const len = LEAD + dur + TAIL[i];
  scenes.push({ id: i + 1, start: +t.toFixed(3), length: +len.toFixed(3), voice: +(t + LEAD).toFixed(3), voiceDur: dur, cues });
  t += len;
}
const TOTAL = +t.toFixed(3);
const timeline = { title: TITLE, total: TOTAL, scenes };
writeFileSync(join(here, 'timeline.js'), `window.TIMELINE = ${JSON.stringify(timeline, null, 1)};\n`);
console.log(`timeline: ${TOTAL.toFixed(2)} s`);
for (const s of scenes) console.log(`  scene ${s.id}: ${s.start.toFixed(2)} → ${(s.start + s.length).toFixed(2)}`);

// ---------- soundtrack ----------
// Drone: Sa (110 Hz) + Pa (165 Hz) + upper Sa, slow swell; tanpura-like pluck every 1.6 s.
const hits = [scenes[2].start + scenes[2].cues[5].start, scenes[3].start + scenes[3].cues[6].start,
              scenes[3].start + scenes[3].cues[4].start, scenes[6].start + scenes[6].cues[5].start];
const inputs = [];
const filters = [];
scenes.forEach((s, i) => {
  inputs.push('-i', A(`s${i + 1}.mp3`));
  filters.push(`[${i}:a]aresample=48000,adelay=${Math.round(s.voice * 1000)}:all=1[v${i}]`);
});
const drone = `aevalsrc='0.20*sin(2*PI*110*t)*(0.6+0.4*sin(2*PI*0.07*t))+0.12*sin(2*PI*165*t)*(0.6+0.4*sin(2*PI*0.05*t+1))+0.08*sin(2*PI*220*t)+0.05*sin(2*PI*330.5*t)*exp(-mod(t,1.6)*2.2)+0.04*sin(2*PI*440*t)*exp(-mod(t+0.4,1.6)*2.6)':s=48000:d=${TOTAL}`;
inputs.push('-f', 'lavfi', '-i', drone);
const di = scenes.length;
filters.push(`[${di}:a]lowpass=f=1800,volume=0.32,afade=t=in:d=2.5,afade=t=out:st=${TOTAL - 3}:d=3,aformat=channel_layouts=mono[drone]`);
hits.forEach((h, k) => {
  inputs.push('-f', 'lavfi', '-i', `aevalsrc='0.9*sin(2*PI*(55+60*exp(-t*9))*t)*exp(-t*3.5)':s=48000:d=1.6`);
  filters.push(`[${di + 1 + k}:a]adelay=${Math.round(h * 1000)}:all=1,volume=0.8[h${k}]`);
});
const mixIn = scenes.map((_, i) => `[v${i}]`).join('') + '[drone]' + hits.map((_, k) => `[h${k}]`).join('');
filters.push(`${mixIn}amix=inputs=${scenes.length + 1 + hits.length}:normalize=0:duration=longest,apad=whole_dur=${TOTAL},atrim=0:${TOTAL},loudnorm=I=-14:TP=-1.5:LRA=11[out]`);
execFileSync('ffmpeg', ['-y', '-loglevel', 'error', ...inputs, '-filter_complex', filters.join(';'), '-map', '[out]', '-ar', '48000', '-ac', '2', A('mix.wav')]);
console.log('wrote audio/mix.wav');
