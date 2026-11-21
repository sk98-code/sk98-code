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

# Story reel: सम्पाती और जटायु (113 s)

A complete animated story in Hindi with narration, captions and all characters:
Sampati, Jatayu (young and old), Surya, Ravana (ten heads), Sita, the Pushpak Viman,
Rama, Lakshmana, Hanuman, Angad and Jambavan.

| # | Scene | Time |
|---|-------|------|
| 0 | Title: सम्पाती और जटायु | 0.0 – 3.2 |
| 1 | Aruna's two sons fly at dawn over the Himalaya | 3.2 – 11.6 |
| 2 | The race to touch the sun | 11.6 – 21.3 |
| 3 | Jatayu's wings catch fire; Sampati shields him, burns and falls on Vindhya | 21.3 – 39.4 |
| 4 | Years later: Ravana takes Sita in the Pushpak; old Jatayu fights; wings cut | 39.4 – 57.5 |
| 5 | Rama and Lakshmana; Jatayu points south and dies; last rites | 57.5 – 70.4 |
| 6 | Vindhya: Hanuman, Angad, Jambavan; Sampati hears his brother's name and weeps | 70.4 – 86.8 |
| 7 | Sampati's divine sight finds Sita in Ashok Vatika; his new wings grow | 86.8 – 99.5 |
| 8 | The brothers fly together again; जय श्री राम | 99.5 – 113.0 |

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
