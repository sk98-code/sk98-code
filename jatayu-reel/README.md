# Jatayu × Pushpak Viman — 10 animation styles

One 5-second shot (1080×1920, 30 fps, 150 frames): Jatayu dives at the golden
Pushpak Viman, flares at 1.7 s, strikes the kalash at **2.0 s (frame 60)**, then
climbs away while a flock scatters.

```
anims/shapes.js     shared scene: viman, Jatayu, poses, motion paths, birds, feathers, clouds
anims/<style>.html  one file per style, only changes the paint
render.mjs          frame-by-frame renderer (headless Chromium -> FFmpeg)
assets/clips/       rendered 5 s clips
previews/           still frames for checking before a render
```

## Setup

Needs Node.js 18+ and FFmpeg on PATH.

```bash
npm install
npx playwright install chromium
```

## Use

```bash
# check frames before rendering
node render.mjs anims/anime.html --frames 0.6,2.0,4 --out previews

# render the clip
node render.mjs anims/anime.html assets/clips/anime.mp4
```

Open `anims/anime.html` in Chrome to watch a looping preview; add `?t=2.0` to
freeze a frame.

Every frame is a pure function of time (randomness comes from a hash of the
frame number), so renders are identical each run.

## Shared cinematic timing (`shapes.js`)

- `sceneTime(t)` remaps output time: a fast dive, slow motion into the flare, a
  hit-stop on the strike, then back to speed. It is pinned so the strike stays on
  **frame 60** and the clip still ends at 5.0 s.
- `camera(t)` gives push-in → punch zoom at the strike → impact shake → slow pull-out.
  `applyCamera(ctx, cam, depth)` adds parallax (depth < 1 for background layers).
- `jatayuRig(t)` gives the skeleton and per-feather layout: two-segment wings whose
  hand trails the arm (follow-through), fanned tail, legs/toes that grip on contact,
  stabilised head with an opening beak.

## Styles

| # | Style | File | Status |
|---|-------|------|--------|
| 06 | Anime | `anims/anime.html` → `assets/clips/anime.mp4` | done |

---

# Story reel: सम्पाती और जटायु (2:43)

A complete animated story in Hindi with narration, captions and all characters:
Sampati, Jatayu (young and old), Surya, Ravana (ten heads), Sita, the Pushpak Viman,
Rama, Lakshmana, Hanuman, Angad and Jambavan.

| Part | What happens |
|------|--------------|
| Hook (0–6 s) | Four fast teaser cuts with big text: burning wings, Ravana's sword, Sampati weeping, "ये दोनों सगे भाई थे!" |
| Title | सम्पाती और जटायु — दो भाइयों की अमर कथा |
| 1–3 | Aruna's sons at dawn; the race to the sun; Sampati shields Jatayu, burns and falls on Vindhya |
| Card | "पर असली परीक्षा... अभी बाकी थी" |
| 4 | Ravana takes Sita; ~24 s fight with no narration (crown knocked off, arrows dodged, bow snapped, claw strike, wing blasts, battle cry, Chandrahas swings), then the wings are cut |
| 5–6 | Rama and Lakshmana, last rites; Vindhya: the search party meets Sampati |
| Card | "और फिर... वो हुआ जिसकी किसी ने कल्पना भी नहीं की थी" |
| 7–8 | Divine sight finds Sita; new wings; the brothers fly together — जय श्री राम |
| End | "ऐसी और कथाओं के लिए Follow करें" |

The soundtrack (built by `build.mjs`) mixes the narration with a tanpura-style drone,
taiko-style fight drums and synthesised sound effects timed to the fight beats.

```
story/audio/s1..s8.mp3   narration (ElevenLabs, "Ayush M – Deep Mythology Narrator", eleven_v4)
story/build.mjs          builds timeline.js (scene + caption timing from the speech) and audio/mix.wav
story/chars.js           the human / vanara / Surya characters
story/story.html         all scenes, captions, transitions, grading
story/render-story.mjs   parallel renderer -> reel/sampati-jatayu.mp4
anims/anime-kit.js       shared cel shading, birds and viman (also used by anims/anime.html)
```

```bash
node story/build.mjs                       # after changing narration clips
node story/render-story.mjs --workers 4    # ~10–15 min on 4 cores
node render.mjs story/story.html --frames 25,50 --out previews   # check stills
```
