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

## Styles

| # | Style | File | Status |
|---|-------|------|--------|
| 06 | Anime | `anims/anime.html` → `assets/clips/anime.mp4` | done |
