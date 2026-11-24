#!/usr/bin/env node
/*
 * Storyboard v2 timeline + soundtrack (realistic cut).
 *   node story/v3/build3.mjs
 * Reads v3/audio/*.mp3 (Viraj narration + character dialogue), lays out the 25 shots
 * from STORYBOARD.md, writes v3/timeline3.js and v3/audio/mix.wav (-14 LUFS).
 */
import { execFileSync } from 'node:child_process';
import { writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const A = (f) => join(here, 'audio', f + '.mp3');
const dur = (f) => Number(execFileSync('ffprobe', ['-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', f]).toString());

const SPK = { n: '', yj: 'जटायु', ys: 'सम्पाती', oj: 'जटायु', os: 'सम्पाती', sita: 'सीता', rav: 'रावण', rama: 'श्री राम', angad: 'अंगद' };
// [shot id, min length, lines: [file, start offset (s) or '+gap' after the previous line, caption]]
const SHOTS = [
  ['01', 7.6, [['n_h1', 0.5, 'कहते हैं... सच्चा भाई वही है, जो अपने भाई के लिए सूरज से भी टकरा जाए।']]],
  ['02', 4.6, [['n_h2', 0.3, 'यह कथा है... ऐसे ही दो भाइयों की।']]],
  ['T', 3.4, []],
  ['03', 7.6, [['n_03', 0.5, 'सूर्य के सारथी अरुण के दो पुत्र थे... बड़े सम्पाती, और छोटे जटायु।']]],
  ['04', 8.4, [['yj_04', 0.3, 'भैया! आज देखते हैं... सूर्य को पहले कौन छूता है!'], ['ys_04', '+0.2', 'जटायु, रुको...! ज़िद मत करो!']]],
  ['05', 4.0, [['n_05', 0.3, 'दोनों ऊपर... और ऊपर उड़ते गए।']]],
  ['06', 4.4, [['yj_06', 0.3, 'भैया...! मेरे पंख... जल रहे हैं!']]],
  ['07', 6.4, [['n_07', 0.4, 'तब सम्पाती ने अपने विशाल पंख फैलाकर... छोटे भाई को ढक लिया।']]],
  ['08', 8.0, [['n_08', 0.4, 'जटायु बच गया... पर सम्पाती के पंख जल गए, और वह विंध्य पर्वत पर जा गिरा।']]],
  ['09', 4.2, [['n_09', 0.6, 'बरसों बीत गए...']]],
  ['10', 4.4, [['sita_10', 0.6, 'हे राम...! कोई तो बचाओ!']]],
  ['11', 5.8, [['oj_11', 0.3, 'रुक जा, रावण! पराई स्त्री का हरण... यह अधर्म है!']]],
  ['12', 6.0, [['rav_12', 0.3, 'एक बूढ़ा गिद्ध... रावण को रोकेगा? हट जा मेरे रास्ते से!']]],
  ['13', 5.6, [['oj_13', 0.7, 'जब तक प्राण हैं... सीता माँ को नहीं ले जाने दूँगा!']]],
  ['14', 2.9, [['rav_14', 0.6, 'तो ले... मर!']]],
  ['15', 5.0, [['n_15', 1.2, '...और रावण ने, उसके पंख काट दिए।']]],
  ['16', 3.6, [['rama_16', 1.0, 'तात जटायु...!']]],
  ['17', 7.4, [['oj_17', 0.5, 'प्रभु... रावण... सीता माँ को... दक्षिण की ओर ले गया...']]],
  ['18', 7.2, [['n_18', 0.8, 'श्री राम ने स्वयं अपने हाथों से... जटायु का अंतिम संस्कार किया।']]],
  ['19', 6.0, [['angad_19', 0.6, 'धन्य था जटायु... जिसने राम-काज में प्राण दे दिए।']]],
  ['20', 5.0, [['os_20', 0.4, 'जटायु...? मेरे भाई का नाम... किसने लिया?']]],
  ['21', 6.2, [['n_21', 0.4, 'भाई की मृत्यु का समाचार सुनकर... सम्पाती की आँखें भर आईं।']]],
  ['22', 9.0, [['os_22', 0.6, 'सौ योजन दूर, समुद्र के पार... लंका की अशोक वाटिका में... माता सीता बैठी हैं।']]],
  ['23', 5.4, [['n_23', 0.5, 'और उसी क्षण... उसके नए पंख उग आए।']]],
  ['24', 7.0, [['n_24', 0.5, 'एक भाई ने सूर्य से भाई को बचाया... दूसरे ने धर्म के लिए प्राण दिए।']]],
  ['25', 6.2, [['n_25', 0.6, 'ऐसी ही और कथाओं के लिए... फॉलो करें। जय श्री राम।']]],
];

const shots = [], voices = [];
let t = 0;
for (const [id, min, lines] of SHOTS) {
  const sh = { id, start: +t.toFixed(3), lines: [] };
  let cursor = 0;
  for (const [file, off, text] of lines) {
    const d = dur(A(file));
    const st = typeof off === 'string' ? cursor + Number(off) : off;
    cursor = st + d;
    sh.lines.push({ who: SPK[file.split('_')[0]], text, start: +st.toFixed(3), end: +(st + d).toFixed(3), file });
    voices.push({ file: A(file), at: t + st });
  }
  sh.length = +Math.max(min, cursor + 0.7).toFixed(3);
  shots.push(sh);
  t += sh.length;
}
const TOTAL = +t.toFixed(3);
writeFileSync(join(here, 'timeline3.js'), `window.TIMELINE3 = ${JSON.stringify({ total: TOTAL, shots }, null, 1)};\n`);
console.log(`timeline: ${TOTAL.toFixed(2)} s (${Math.floor(TOTAL / 60)}:${String(Math.round(TOTAL % 60)).padStart(2, '0')})`);
const S = (id) => shots.find((s) => s.id === id);
for (const s of shots) console.log(`  ${s.id}: ${s.start.toFixed(2)} +${s.length.toFixed(2)}`);

// ---------------------------------------------------------------- soundtrack
const inputs = [], filters = [], mix = [];
let n = 0;
const add = (args, chain) => { inputs.push(...args); const l = `a${n}`; filters.push(`[${n}:a]${chain}[${l}]`); mix.push(`[${l}]`); n++; };
const ms = (s) => Math.max(0, Math.round(s * 1000));
for (const v of voices) add(['-i', v.file], `aresample=48000,aformat=channel_layouts=mono,volume=1.15,adelay=${ms(v.at)}:all=1`);
// devotional drone bed (tanpura-like) — quieter under dialogue-heavy fight
add(['-f', 'lavfi', '-i', `aevalsrc='0.18*sin(2*PI*110*t)*(0.6+0.4*sin(2*PI*0.07*t))+0.1*sin(2*PI*165*t)*(0.6+0.4*sin(2*PI*0.05*t+1))+0.07*sin(2*PI*220*t)+0.05*sin(2*PI*330.5*t)*exp(-mod(t,2)*2)+0.035*sin(2*PI*440*t)*exp(-mod(t+0.5,2)*2.4)+0.03*sin(2*PI*247.5*t)*exp(-mod(t+1,4)*1.2)':s=48000:d=${TOTAL}`],
  `lowpass=f=1600,volume=0.32,afade=t=in:d=2,afade=t=out:st=${TOTAL - 3}:d=3`);
const noise = (d, hp, lp, vol, fi, fo, color = 'pink') => [['-f', 'lavfi', '-i', `anoisesrc=d=${d}:c=${color}:a=0.8`],
  `highpass=f=${hp},lowpass=f=${lp},afade=t=in:d=${fi},afade=t=out:st=${Math.max(0, d - fo)}:d=${fo},volume=${vol}`];
const at = (kind, time, vol = 1, d) => {
  const sfx = {
    boom: `aevalsrc='0.9*sin(2*PI*(40+70*exp(-t*6))*t)*exp(-t*2.2)':s=48000:d=2`,
    thump: `aevalsrc='0.9*sin(2*PI*(60+90*exp(-t*14))*t)*exp(-t*7)':s=48000:d=0.7`,
    shing: `aevalsrc='0.3*(sin(2*PI*1320*t)+0.7*sin(2*PI*2790*t)+0.5*sin(2*PI*4150*t))*exp(-t*2.4)':s=48000:d=1.8`,
    chime: `aevalsrc='0.22*(sin(2*PI*880*t)+0.6*sin(2*PI*1318*t)+0.4*sin(2*PI*1760*t))*exp(-t*1.2)':s=48000:d=3`,
    screech: `aevalsrc='0.25*sin(2*PI*(1800+450*sin(2*PI*12*t)-600*t)*t)*exp(-t*1.5)*(1-exp(-t*30))':s=48000:d=1.3`,
  };
  if (sfx[kind]) { add(['-f', 'lavfi', '-i', sfx[kind]], `volume=${vol},adelay=${ms(time)}:all=1`); return; }
  const [args, chain] = kind === 'whoosh' ? noise(0.7, 300, 2400, vol, 0.3, 0.35)
    : kind === 'wind' ? noise(d, 150, 900, vol * 0.5, 1.2, 1.2, 'brown')
    : kind === 'fire' ? noise(d, 400, 5000, vol * 0.25, 0.8, 1.0, 'white')
    : kind === 'sea' ? noise(d, 80, 700, vol * 0.6, 1.5, 1.5, 'brown')
    : kind === 'slash' ? noise(0.35, 2500, 9000, vol, 0.02, 0.3, 'white')
    : noise(2.2, 200, 6000, vol * 0.5, 2.0, 0.1);           // riser
  const lfo = kind === 'sea' ? ',tremolo=f=0.18:d=0.7' : kind === 'fire' ? ',tremolo=f=9:d=0.6' : '';
  add(args, `${chain}${lfo},adelay=${ms(time)}:all=1`);
};
const span = (id) => [S(id).start, S(id).start + S(id).length];
// ambience
for (const id of ['01', '03', '04', '05', '06', '07', '08', '10', '11', '12', '13', '14', '15', '23', '24']) { const [a, b] = span(id); at('wind', a, 0.8, b - a + 0.4); }
for (const id of ['06', '07', '08']) { const [a, b] = span(id); at('fire', a, 0.7, b - a); }
{ const [a, b] = span('18'); at('fire', a, 1.1, b - a); }
for (const id of ['19', '20', '21', '22']) { const [a, b] = span(id); at('sea', a, 1, b - a + 0.5); }
// accents
at('chime', S('T').start + 0.2, 1);
at('riser', S('05').start + S('05').length - 2.2, 0.6); at('boom', S('07').start + 0.3, 0.7);
at('screech', S('06').start + 0.2, 0.6); at('whoosh', S('08').start + 0.6, 0.8); at('boom', S('08').start + S('08').length - 1.4, 0.6);
at('screech', S('11').start + 0.1, 0.8); at('whoosh', S('13').start + 0.1, 1); at('thump', S('13').start + 1.6, 1); at('thump', S('13').start + 3.4, 0.8);
at('shing', S('14').start + 0.2, 1); at('slash', S('15').start + 0.05, 1); at('boom', S('15').start + 0.05, 1);
at('riser', S('22').start + S('22').length - 2.2, 0.6); at('chime', S('23').start + 0.3, 1); at('boom', S('23').start + 0.3, 0.5);
at('chime', S('25').start + 0.4, 0.8);
// fight drums (shots 13–15)
{
  const a = S('12').start + S('12').length - 0.8, d = S('13').length + S('14').length + 1.5;
  add(['-f', 'lavfi', '-i', `aevalsrc='0.8*sin(2*PI*(52+90*exp(-mod(t,0.5)*20))*mod(t,0.5))*exp(-mod(t,0.5)*8)+0.4*sin(2*PI*(120+70*exp(-mod(t+0.25,1)*24))*mod(t+0.25,1))*exp(-mod(t+0.25,1)*14)':s=48000:d=${d}`],
    `afade=t=in:d=0.6,afade=t=out:st=${d - 1}:d=1,volume=0.55,adelay=${ms(a)}:all=1`);
}
filters.push(`${mix.join('')}amix=inputs=${mix.length}:normalize=0:duration=longest,apad=whole_dur=${TOTAL},atrim=0:${TOTAL},loudnorm=I=-14:TP=-2:LRA=11[out]`);
execFileSync('ffmpeg', ['-y', '-loglevel', 'error', ...inputs, '-filter_complex', filters.join(';'), '-map', '[out]', '-ar', '48000', '-ac', '2', join(here, 'audio', 'mix.wav')]);
console.log(`wrote v3/audio/mix.wav (${mix.length} layers)`);
