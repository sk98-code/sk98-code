#!/usr/bin/env node
/*
 * Builds the story timeline and the soundtrack.
 *
 *   node story/build.mjs
 *
 * Timeline (≈2:40): teaser hook → title → scenes 1–3 → mid-hook card → scene 4
 * (narration split around a ~24 s Ravana–Jatayu fight with no voice) → scenes 5–6 →
 * mid-hook card → scenes 7–8 → follow card.
 *
 * - reads audio/s1..s8.mp3 (Hindi narration, one clip per scene)
 * - times each caption chunk over the speech (silencedetect + character weights)
 * - writes timeline.js (window.TIMELINE) for story.html, including fight beats
 * - writes audio/mix.wav: narration + tanpura drone + fight drums + sound effects, -14 LUFS
 */
import { execFileSync, spawnSync } from 'node:child_process';
import { writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const A = (f) => join(here, 'audio', f);

const TITLE = 3.2;
const LEAD = 0.45;
const TAIL = [1.2, 1.2, 2.2, 1.4, 2.6, 1.8, 1.8, 2.4];
const FIGHT = 28.5;                     // seconds of fight inserted into scene 4

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

// fight beats, seconds after Jatayu's first strike (shared with story.html)
const BEATS = {
  crown: 0.0, arrows: [6.0, 7.0, 8.0], bow: 11.4,
  slashIn: 13.8, slash: 14.4, slashOut: 16.2,
  buffet: [16.8, 17.4], cryIn: 18.4, cry: 18.6, cryOut: 20.2,
  draw: 23.0, swings: [24.6, 26.2], charge: 27.4,
};
// narrated lines inside the fight: [file, start (s after first strike), caption chunks]
const FIGHT_LINES = [
  ['f1.mp3', 0.25, ['जटायु ने झपटकर,', 'रावण का मुकुट गिरा दिया!']],
  ['f2.mp3', 4.1, ['रावण ने बाणों की वर्षा कर दी...', 'पर बूढ़ा जटायु, हर बाण से बच निकला!']],
  ['f3.mp3', 10.6, ['उसने अपनी चोंच से रावण का धनुष तोड़ डाला...', 'और पंजों से उसका कवच चीर दिया!']],
  ['f4.mp3', 20.6, ['तब क्रोध से पागल रावण ने,', 'अपनी चंद्रहास तलवार निकाली...']],
];
const HOOK_LINES = ['एक भाई ने अपने भाई के लिए...', 'पंख जला दिए।', 'दूसरे ने सीता माँ के लिए...', 'पंख कटवा दिए!', 'और ये दोनों...', 'सगे भाई थे।'];

const duration = (f) => Number(execFileSync('ffprobe', ['-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', f]).toString());

function silences(f, dur) {
  const r = spawnSync('ffmpeg', ['-hide_banner', '-i', f, '-af', 'silencedetect=noise=-36dB:d=0.2', '-f', 'null', '-'], { encoding: 'utf8' });
  const out = []; let start = null;
  for (const m of r.stderr.matchAll(/silence_(start|end): ([\d.]+)/g)) {
    if (m[1] === 'start') start = Number(m[2]);
    else if (start !== null) { out.push([start, Number(m[2])]); start = null; }
  }
  if (start !== null) out.push([start, dur]);
  return out;
}

function speechMap(dur, sil) {
  const islands = []; let t = 0;
  for (const [a, b] of sil) { if (a > t) islands.push([t, a]); t = b; }
  if (t < dur) islands.push([t, dur]);
  const total = islands.reduce((s, [a, b]) => s + (b - a), 0);
  return (u) => { let need = u * total; for (const [a, b] of islands) { if (need <= b - a) return a + need; need -= b - a; } return dur; };
}

function clipCues(file, chunks, offset) {
  const dur = duration(file), map = speechMap(dur, silences(file, dur));
  const weights = chunks.map((c) => c.replace(/[\s.,—!?।]/g, '').length);
  const total = weights.reduce((a, b) => a + b, 0);
  let acc = 0;
  return { dur, cues: chunks.map((text, k) => { const a = map(acc / total); acc += weights[k]; return { text, start: +(offset + a).toFixed(3), end: +(offset + map(acc / total)).toFixed(3) }; }) };
}

const items = [];
const voices = [];
let t = 0;
const push = (it) => { it.start = +t.toFixed(3); items.push(it); t += it.length; return it; };

{
  const h = clipCues(A('hook.mp3'), HOOK_LINES, 0.25);
  const it = push({ kind: 'hook', length: 0.25 + h.dur + 0.5, cues: h.cues, noCaption: true });
  voices.push({ file: A('hook.mp3'), at: it.start + 0.25 });
}
push({ kind: 'title', length: TITLE });
const scenes = [];
function card(id, file, lines) {
  const d = duration(A(file));
  const it = push({ kind: 'card', id, lines, length: 0.4 + d + 0.6, noCaption: true });
  voices.push({ file: A(file), at: it.start + 0.4 });
  return it;
}
for (let i = 0; i < 8; i++) {
  if (i === 3) card('c1', 'c1.mp3', ['पर असली परीक्षा...', 'अभी बाकी थी']);
  if (i === 6) card('c2', 'c2.mp3', ['और फिर...', 'वो हुआ जिसकी', 'किसी ने कल्पना भी नहीं की थी']);
  const f = A(`s${i + 1}.mp3`);
  const dur = duration(f);
  const map = speechMap(dur, silences(f, dur));
  const chunks = CAPTIONS[i];
  const weights = chunks.map((c) => c.replace(/[\s.,—!?।]/g, '').length);
  const total = weights.reduce((a, b) => a + b, 0);
  let acc = 0;
  const cues = chunks.map((text, k) => {
    const a = map(acc / total); acc += weights[k];
    return { text, start: LEAD + a, end: LEAD + map(acc / total) };
  });
  const sc = { kind: 'scene', id: i + 1, length: LEAD + dur + TAIL[i], voiceDur: dur };
  if (i === 3) {                                       // open a gap for the fight before "पर रावण ने..."
    const split = cues[5].start - LEAD - 0.05;
    for (const q of cues.slice(5)) { q.start += FIGHT; q.end += FIGHT; }
    sc.length += FIGHT;
    sc.fight = { from: +(cues[4].start + 0.9).toFixed(3), to: +cues[5].start.toFixed(3), beats: BEATS };
    sc.split = split;
    sc.fightVoices = []; sc.extraCues = [];
    for (const [file, at, chunks] of FIGHT_LINES) {
      const fc = clipCues(A(file), chunks, sc.fight.from + at);
      sc.extraCues.push(...fc.cues);
      sc.fightVoices.push({ file: A(file), rel: sc.fight.from + at });
    }
  }
  sc.cues = cues.map((q) => ({ text: q.text, start: +q.start.toFixed(3), end: +q.end.toFixed(3) }));
  push(sc);
  if (i === 3) {
    voices.push({ file: f, at: sc.start + LEAD, from: 0, to: sc.split });
    voices.push({ file: f, at: sc.start + LEAD + sc.split + FIGHT, from: sc.split });
    for (const v of sc.fightVoices) voices.push({ file: v.file, at: sc.start + v.rel });
    delete sc.fightVoices;
  } else voices.push({ file: f, at: sc.start + LEAD });
  scenes.push(sc);
}
card('end', 'end.mp3', ['ऐसी और कथाओं के लिए', 'Follow करें', 'जय श्री राम']);

const TOTAL = +t.toFixed(3);
writeFileSync(join(here, 'timeline.js'), `window.TIMELINE = ${JSON.stringify({ total: TOTAL, items }, null, 1)};\n`);
console.log(`timeline: ${TOTAL.toFixed(2)} s (${Math.floor(TOTAL / 60)}:${String(Math.round(TOTAL % 60)).padStart(2, '0')})`);
for (const it of items) console.log(`  ${it.kind}${it.id ? ' ' + it.id : ''}: ${it.start.toFixed(2)} → ${(it.start + it.length).toFixed(2)}`);

// ---------------------------------------------------------------- soundtrack
const S4 = scenes[3];
const F0 = S4.start + S4.fight.from;                    // first strike, absolute
const inputs = [], filters = [], mix = [];
let n = 0;
const add = (args, chain, label) => { inputs.push(...args); filters.push(`[${n}:a]${chain}[${label}]`); mix.push(`[${label}]`); n++; };
const ms = (s) => Math.max(0, Math.round(s * 1000));

voices.forEach((v, k) => {
  const trim = v.to !== undefined ? `atrim=0:${v.to},` : v.from ? `atrim=${v.from},asetpts=PTS-STARTPTS,` : '';
  add(['-i', v.file], `${trim}aresample=48000,aformat=channel_layouts=mono,adelay=${ms(v.at)}:all=1`, `v${k}`);
});
// drone (tanpura-like), ducked a little during the fight
add(['-f', 'lavfi', '-i', `aevalsrc='0.20*sin(2*PI*110*t)*(0.6+0.4*sin(2*PI*0.07*t))+0.12*sin(2*PI*165*t)*(0.6+0.4*sin(2*PI*0.05*t+1))+0.08*sin(2*PI*220*t)+0.05*sin(2*PI*330.5*t)*exp(-mod(t,1.6)*2.2)+0.04*sin(2*PI*440*t)*exp(-mod(t+0.4,1.6)*2.6)':s=48000:d=${TOTAL}`],
  `lowpass=f=1800,volume=0.3,afade=t=in:d=2,afade=t=out:st=${TOTAL - 3}:d=3`, 'drone');
// fight drums (taiko-style), 0.5 s pulse with off-beat accents
const fightLen = S4.fight.to - S4.fight.from + 1.5;
add(['-f', 'lavfi', '-i', `aevalsrc='0.85*sin(2*PI*(52+90*exp(-mod(t,0.5)*20))*mod(t,0.5))*exp(-mod(t,0.5)*8)+0.45*sin(2*PI*(120+70*exp(-mod(t+0.25,1)*24))*mod(t+0.25,1))*exp(-mod(t+0.25,1)*14)+0.3*sin(2*PI*(90+50*exp(-mod(t+0.125,2)*24))*mod(t+0.125,2))*exp(-mod(t+0.125,2)*10)':s=48000:d=${fightLen}`],
  `afade=t=in:d=1.2,afade=t=out:st=${fightLen - 1.5}:d=1.5,volume=0.75,adelay=${ms(F0 - 1.2)}:all=1`, 'drums');

const sfx = {
  boom: `aevalsrc='0.9*sin(2*PI*(42+80*exp(-t*7))*t)*exp(-t*2.6)':s=48000:d=1.8`,
  thump: `aevalsrc='0.9*sin(2*PI*(60+100*exp(-t*14))*t)*exp(-t*7)':s=48000:d=0.7`,
  clang: `aevalsrc='0.35*(sin(2*PI*1180*t)+0.7*sin(2*PI*2630*t)+0.5*sin(2*PI*3970*t)+0.3*sin(2*PI*5210*t))*exp(-t*3.2)':s=48000:d=1.4`,
  screech: `aevalsrc='0.32*sin(2*PI*(1900+500*sin(2*PI*13*t)-700*t)*t)*exp(-t*1.4)*(1-exp(-t*30))':s=48000:d=1.4`,
};
const noise = (d, hp, lp, vol, fadeIn, fadeOut) => [['-f', 'lavfi', '-i', `anoisesrc=d=${d}:c=pink:a=0.8`],
  `highpass=f=${hp},lowpass=f=${lp},afade=t=in:d=${fadeIn},afade=t=out:st=${Math.max(0, d - fadeOut)}:d=${fadeOut},volume=${vol}`];
let k = 0;
const at = (kind, time, vol = 1) => {
  if (sfx[kind]) add(['-f', 'lavfi', '-i', sfx[kind]], `volume=${vol},adelay=${ms(time)}:all=1`, `x${k++}`);
  else {
    const [args, chain] = kind === 'whoosh' ? noise(0.55, 300, 2600, vol, 0.25, 0.3)
      : kind === 'whiz' ? noise(0.35, 1500, 7000, vol * 0.8, 0.1, 0.2)
      : kind === 'crack' ? noise(0.18, 1800, 9000, vol * 1.4, 0.005, 0.15)
      : kind === 'slash' ? noise(0.3, 2500, 9000, vol, 0.02, 0.25)
      : noise(2.2, 200, 6000, vol * 0.6, 2.0, 0.1);      // riser
    add(args, `${chain},adelay=${ms(time)}:all=1`, `x${k++}`);
  }
};
// hook: a boom on every teaser cut, riser into the title
const HK = items[0];
for (const k of [0, 2, 4, 5]) { const h = HK.cues[k].start; at('boom', h, 0.8); at('whoosh', Math.max(0, h - 0.25), 0.6); }
at('riser', HK.length - 2.2, 0.7); at('boom', HK.length, 1);
// cards
for (const it of items.filter((x) => x.kind === 'card')) { at('riser', it.start - 1.4, 0.5); at('boom', it.start + 0.1, 0.8); }
// story hits
at('boom', scenes[2].start + scenes[2].cues[5].start, 0.8);
at('screech', S4.start + S4.cues[4].start + 0.1, 0.8);
// fight
const B = BEATS, F = (s) => F0 + s;
at('whoosh', F(-0.5)); at('thump', F(B.crown)); at('clang', F(B.crown), 0.6);
at('screech', F(2.0), 0.6);
for (const a of B.arrows) { at('whiz', F(a)); at('whoosh', F(a + 0.15), 0.5); }
at('whoosh', F(B.bow - 0.5)); at('crack', F(B.bow)); at('thump', F(B.bow), 0.7);
at('slash', F(B.slash)); at('slash', F(B.slash + 0.12)); at('slash', F(B.slash + 0.24)); at('boom', F(B.slashIn), 0.6);
for (const b of B.buffet) { at('whoosh', F(b), 0.9); at('thump', F(b + 0.1), 0.7); }
at('screech', F(B.cry), 0.9); at('boom', F(B.cryIn), 0.6);
at('clang', F(B.draw), 0.9);
for (const s of B.swings) { at('whoosh', F(s), 1); at('slash', F(s + 0.1), 0.6); }
at('screech', F(B.charge), 0.7); at('riser', F(B.charge - 0.5), 0.6);
// wings cut
at('boom', S4.start + S4.cues[6].start, 1); at('slash', S4.start + S4.cues[6].start - 0.1, 1);
// wings regrow
at('riser', scenes[6].start + scenes[6].cues[4].start, 0.7); at('boom', scenes[6].start + scenes[6].cues[5].start, 0.9);

filters.push(`${mix.join('')}amix=inputs=${mix.length}:normalize=0:duration=longest,apad=whole_dur=${TOTAL},atrim=0:${TOTAL},loudnorm=I=-14:TP=-1.5:LRA=11[out]`);
execFileSync('ffmpeg', ['-y', '-loglevel', 'error', ...inputs, '-filter_complex', filters.join(';'), '-map', '[out]', '-ar', '48000', '-ac', '2', A('mix.wav')]);
console.log(`wrote audio/mix.wav (${mix.length} layers)`);
