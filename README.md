# Daily Stories, For Every Age

Fully-free, script-driven pipeline that produces and publishes **one narrated
short story per day, per audience track** — kids, teens & young adults,
adults & middle-aged, and women — each with its own voice, illustration
style, and **its own YouTube channel**, then feeds performance data back into
future content decisions.

## The four tracks, four channels

Each track is a fully separate YouTube channel with its own OAuth token,
own subscribers, own branding. Earlier this ran as one shared channel
(YouTube's `made_for_kids` flag is set per video, not per channel, so that
worked mechanically) but a kids bedtime-story subscriber getting a teen
breakup drama in their feed — and vice versa — is bad for subscriber
retention and channel identity, so it's four channels now. See "Multi-channel
YouTube setup" below for how each channel/token maps to a track.

One vertical Short is produced daily per channel. There's no separate
long-form (16:9) video: our ~8-scene stories run ~70-90s, so a distinct
long-form cut added little, and uploading both would cost ~13,000 YouTube
Data API quota units/day across 4 channels — over the default 10,000/day
free cap *per channel's project* (though with separate channels each
getting its own quota, that's less of a hard constraint than it was under
one shared channel). `pipeline/assemble.py` (the long-form assembler) is
kept in the codebase, tested, but unused by default — same pattern as
`pipeline/broll.py`.

| Track | Voice | Illustration style | Made for kids? |
|---|---|---|---|
| **Kids** | Sonia (British, warm) | Watercolor storybook | Yes — every kids upload is flagged `selfDeclaredMadeForKids` |
| **Teens & young adults** | Aria (confident) | Vibrant character-focused digital art | No |
| **Adults & middle-aged** | Guy (warm, mature) | Cinematic, painterly, reflective | No |
| **Women** | Jenny (warm, comforting) | Elegant, soft, rose-gold | No |

Configured in `pipeline/config.py`'s `TRACKS` dict — voice, illustration
style prompt, YouTube category, `made_for_kids` flag, and extra tags per
track all live there.

## How it stays (nearly) free

Every stage is free, including scene illustrations — no per-image cost, no
billing account required anywhere:

| Stage | Tool | Cost |
|---|---|---|
| Story writing | Gemini API (`pipeline/story_writer.py`), weekly, per track | Free tier — ~28 requests/week total, well inside free limits |
| Voiceover | `edge-tts`, one voice per track | Free, uncapped, but unofficial (no SLA — see Limits below) |
| Scene illustrations | FLUX.1-schnell via Cloudflare Workers AI (`pipeline/ai_image.py`) | Free — 10,000 Neurons/day, no card |
| Assembly/captions/animation | FFmpeg + generated brand gradient (`pipeline/textcard.py`) | Free, local |
| Thumbnail | Pillow (matches the video's gradient) | Free |
| Upload | YouTube Data API v3 | Free — 10,000 quota units/day (see Limits below) |
| Scheduling | GitHub Actions | Free tier — 2,000 min/month on this private repo |
| Analytics | YouTube Analytics API | Free |

### Limits worth knowing, per tool

- **Cloudflare Workers AI**: 10,000 Neurons/day. A normal day (4 tracks × 8
  scenes) needs ~32 images, comfortably inside that — but heavy iteration/
  testing burns through it fast (spot-checking prompts, re-running the
  pipeline repeatedly), and there's no way to query a reset time from the
  API. `pipeline/ai_image.py` distinguishes real rate-limiting (Cloudflare
  error 429, retries with backoff) from daily quota exhaustion (error code
  4006, fails fast — no point retrying). **Quota exhaustion aborts the
  video rather than publishing it with placeholder gradients**: `run_daily.py`
  generates each track's first scene as a real-work "probe" before doing
  any TTS/assembly; if that hits quota exhaustion, the whole run stops
  there (every other track would hit the same account-wide wall) and every
  track's script stays queued, untouched, for a later run to retry — see
  "Runs multiple times a day" below. A non-quota per-scene failure (network
  blip, etc.) still falls back to the gradient as before; that's a one-off,
  not a sign the rest of the video would fail too.
- **YouTube Data API v3**: 10,000 quota units/day by default.
  `videos.insert` costs 1,600 units; `thumbnails.set` costs 50. At one
  upload per track per day, 4 tracks cost ~6,600 units/day — comfortable.
  (Uploading a second long-form video per track, as earlier versions of
  this pipeline did, pushed that to ~13,000/day and blew the cap — this is
  why there's no long-form output anymore.) A quota increase can be
  requested for free in Google Cloud Console but isn't instant.
- **edge-tts**: not an official public API — it's a wrapper around
  Microsoft Edge's built-in read-aloud service, with no published rate
  limit and no SLA. It could change or stop working with zero notice since
  it's unofficial; there's no in-repo fallback if that happens.
- **GitHub Actions**: this repo is private, so it gets the free plan's
  2,000 minutes/month (public repos get unlimited). The daily workflow now
  runs every 4 hours (6x/day, see below), but most of those runs are
  fast — either a near-instant quota-exhaustion abort or a no-op skip for
  tracks already produced that day — so real usage is closer to one full
  ~20-30 minute run/day plus a handful of short ones, not 6x that. Worth
  spot-checking against real run durations in the Actions tab after the
  workflow's been live a few days.

### Runs multiple times a day, not once

`daily-video.yml` runs on a `0 */4 * * *` schedule (every 4 hours) instead
of a single daily cron, specifically so a quota-exhaustion abort gets
retried later the same day once Cloudflare's quota resets, without your
involvement. Two things make repeated runs safe rather than producing
duplicate content:
- `pipeline/state.py` tracks which tracks already produced a video today
  (`scripts_queue/state/<track>.json`, committed back to the repo like the
  queue itself) — a track that already succeeded earlier the same day is
  skipped on later runs.
- A track that hasn't produced yet always resumes with the *same* queued
  script (nothing was consumed on the aborted attempt), so a retry never
  skips or duplicates content.

**There is no Anthropic API key anywhere in this codebase.** Story writing
uses the Gemini API instead (`pipeline/story_writer.py`) — the *only* LLM
call in the whole pipeline. It runs weekly (`run_weekly_story_refill.py`,
chained onto the `weekly-analytics` GitHub Action), generating 7 stories per
track with Gemini's structured-output JSON mode constrained to the exact
`Script`/`Scene` schema, validating each with the same `pipeline.scripts.
validate()` a hand-written script would go through before writing it into
`scripts_queue/pending/<track>/`. The daily run (`run_daily.py`) only ever
*consumes* the queue — it never calls an LLM itself. Generated stories go
straight into the production queue with no human review step by default;
add one if you want a quality gate (see Weekly loop below). You can still
write scripts by hand the same way as before — anything matching the schema
in `pipeline/scripts.py` works regardless of how it was written.

**Visuals are AI-generated per scene, not stock footage.** Each scene gets
one AI illustration (`pipeline/ai_image.py`, FLUX.1-schnell via Cloudflare
Workers AI) prompted from that scene's own content, animated with a Ken
Burns zoom (`pipeline/textcard.py`), plus a numbered progress badge ("PART
2/6 · ...") and big word-by-word "pop" captions with the current key term
accent-colored. If the AI API call fails or `CF_ACCOUNT_ID`/`CF_API_TOKEN`
aren't set, it falls back automatically to a generated brand gradient with
drifting glow orbs — the pipeline never hard-fails on this stage.
`pipeline/broll.py` (Pexels/Pixabay) is kept in the codebase, tested, but
unused by default.

### A real limitation worth knowing: FLUX-schnell sometimes renders garbled text

Cloudflare's free FLUX.1-schnell has no negative-prompt/CFG support, so "no
text" instructions only *reduce*, not eliminate, on-image text — and it
reliably tries to render text for anything that reads like ad copy, a
banner, or (worst offender) **institutional building exteriors/signage**
(school entrances, storefronts). Two things keep this rare in practice:

1. Each `Scene` has an optional `visual` field — a concrete visual
   description used *only* for the image prompt, kept separate from the
   narrated `narration` and the on-screen `on_screen_text` badge label.
   Writing `visual` as a close, concrete description (not a quoted
   slogan, not a full sentence, not a building exterior) is what makes
   this reliable — see the seed scripts in `scripts_queue/pending/` for
   examples across all four styles.
2. `pipeline/ai_image.py` front-loads a strong no-text instruction and
   substitutes a few known trigger words (`free` → `no-cost`, `subscribe`
   → `follow`) that otherwise anchor FLUX toward badge/button training
   images.

When writing new story scripts — especially for the **teens** track, whose
"vibrant/dynamic" style is the most sign-prone — keep `visual` descriptions
character-focused (close-ups, nature, interiors) and avoid building
exteriors with implied signage.

### Why Cloudflare, not something else

Local Stable Diffusion (via `diffusers`) and several hosted providers
(Hugging Face Inference Providers, Google Gemini/Imagen) were evaluated and
rejected before landing on Cloudflare: HF's free tier only covers ~10
images/month, Gemini's image models have zero free-tier quota, and local SD
on an M1/16GB Mac was either too slow (~11min/image on SDXL) or too
low-quality (SD1.5) for daily production use. Cloudflare Workers AI's free
tier (10,000 Neurons/day) comfortably covers ~4 videos × ~8 scenes/day.

## Weekly loop (fully automated)

The `weekly-analytics` GitHub Action runs both steps in sequence, no manual
step required once `GEMINI_API_KEY` is set:
1. `run_weekly_analytics.py` pulls the last 7 days of metrics for each
   track's channel into `analytics/reports/<track>/{date}.md` (skipping any
   track whose channel isn't set up yet).
2. `run_weekly_story_refill.py` generates 7 new stories per track (28
   total) via `pipeline/story_writer.py` and writes them straight into
   `scripts_queue/pending/<track>/`.
3. Both the report and the refilled queue get committed and pushed
   automatically.

If a track's queue runs dry before the next refill, `run_daily.py` logs
"queue empty, refill needed" for that track only and moves on to the
others — no crash, no partial video, no wasted API calls. If
`GEMINI_API_KEY` isn't set, the refill step logs a message and exits
cleanly rather than failing the workflow.

**No review gate by default** — generated stories are validated against the
schema (so malformed output can't reach the queue) but not read by a human
before `run_daily.py` picks them up and uploads them. If you want a quality
checkpoint, the natural place to add one is having `run_weekly_story_refill.py`
write to a separate `scripts_queue/review/<track>/` directory instead of
`pending/` directly, and moving files over manually (or via a follow-up
script) after a look.

## One-time setup

1. `python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`
   (ffmpeg must also be installed and on `PATH` — `brew install ffmpeg` on macOS)
2. Copy `.env.example` to `.env` and fill in `CF_ACCOUNT_ID` / `CF_API_TOKEN`:
   - Free account at [dash.cloudflare.com/sign-up](https://dash.cloudflare.com/sign-up)
     (no credit card required)
   - Find your **Account ID** on the Workers & Pages overview page of the
     Cloudflare dashboard
   - Create an API token at
     [dash.cloudflare.com/profile/api-tokens](https://dash.cloudflare.com/profile/api-tokens)
     using the **"Workers AI"** template (Read access is enough)
   - No billing needed — the free tier (10,000 Neurons/day) is skipped
     automatically if these are left unset; the pipeline just falls back to
     the gradient background for every scene, no error, just a plainer look
   - `PEXELS_API_KEY` / `PIXABAY_API_KEY` are optional, only needed to
     experiment with `pipeline/broll.py` directly
3. Copy `.env.example` and fill in `GEMINI_API_KEY` for weekly story
   generation:
   - Free key at [aistudio.google.com/apikey](https://aistudio.google.com/apikey)
     — copy the "API Key" value from the key details panel exactly (the
     format isn't a fixed prefix, don't assume "starts with AIza")
   - No billing needed; if unset, the weekly refill step just skips
   - For GitHub Actions, add it as the repo secret `GEMINI_API_KEY`
4. **Multi-channel YouTube setup** — one OAuth Desktop client is shared,
   but each of the 4 tracks needs its own channel and its own token:
   - Create a Google Cloud project once, enable **YouTube Data API v3** and
     **YouTube Analytics API**, create an OAuth Desktop client, download the
     JSON as `secrets/client_secret.json`. This client is reused for all 4
     channels — you don't need 4 Cloud projects.
   - Create 4 YouTube channels (as Brand Accounts under your Google account
     is easiest to manage: youtube.com → your profile icon → "Create a
     channel"), one per track. Name/brand each for its audience.
   - For each track, authorize its channel:
     1. In [studio.youtube.com](https://studio.youtube.com), switch your
        **active channel** (profile icon → account switcher) to the one
        for this track. This step matters — the OAuth consent screen
        authorizes whichever channel is currently active, and there's no
        channel picker in the flow itself.
     2. Run `python -m pipeline.upload --auth-setup --track <kids|teens|adults|women>`
        — this opens a browser consent screen and, on approval, writes
        `secrets/youtube_token_<track>.json`.
     3. Repeat for the other 3 tracks, switching the active channel each time.
   - For GitHub Actions, base64-encode each track's token file and store it
     as its own repo secret: `YOUTUBE_TOKEN_B64_KIDS`,
     `YOUTUBE_TOKEN_B64_TEENS`, `YOUTUBE_TOKEN_B64_ADULTS`,
     `YOUTUBE_TOKEN_B64_WOMEN`. Also add `CF_ACCOUNT_ID` / `CF_API_TOKEN` as
     their own repo secrets.
   - You don't have to set all 4 up at once — `run_daily.py` and
     `run_weekly_analytics.py` both skip a track cleanly (not a failure) if
     that track's token doesn't exist yet, so you can bring channels online
     one at a time.
5. Drop a few royalty-free tracks (e.g. from the YouTube Audio Library) into
   `assets/music/`.

## Instagram — not built yet

There's no Instagram posting code in this repo yet. Unlike YouTube's
one-time OAuth, Instagram/Meta requires more upfront setup before any
posting code is useful: a Business or Creator Instagram account connected
to a Facebook Page, then a Meta Developer App with Instagram
content-publishing permissions (which can require Meta's app review for
that scope). Our vertical 1080×1920 Short output is already Reels-format
compatible, so once that account/app setup is done, adding a
`pipeline/instagram_upload.py` using the Instagram Graph API's Content
Publishing endpoints is a comparatively small addition — worth revisiting
once the Business/Creator account and Meta App exist.

## Testing each stage standalone

```bash
pytest tests/test_config.py       # per-track YouTube token path resolution, no network
pytest tests/test_scripts.py     # queue schema/helpers, no network
pytest tests/test_tts.py          # real edge-tts call, no key needed
pytest tests/test_ai_image.py      # mocked Cloudflare client, no network/cost
pytest tests/test_story_writer.py   # mocked Gemini client, no network/cost
pytest tests/test_textcard.py       # generated backgrounds, ffmpeg only, no network
pytest tests/test_assemble.py        # long-form assembler — unused by default, kept tested
pytest tests/test_shorts.py           # the actual production path: full-story vertical video
pytest tests/test_thumbnail.py         # Pillow only, no network
pytest tests/test_upload.py             # --dry-run, builds payload without calling API
pytest tests/test_state.py               # per-track daily-production state, no network
pytest tests/test_run_daily.py            # orchestration: quota-abort, daily-state gating (mocked)
pytest tests/test_broll.py               # optional/unused by default; needs an API key
```

Tests requiring an API key skip automatically (not fail) if that key is unset.
`test_ai_image.py` mocks the Cloudflare client entirely, so the suite never
spends real money — only a manual `run_daily.py` actually calls the API.

## Running the full pipeline manually

```bash
python run_daily.py                  # runs all 4 tracks/channels, uploads as "private"
python run_daily.py --track kids      # run just one track (needs that channel's token)
python run_daily.py --dry-run          # does everything except the real YouTube upload
                                         # (works even with no channels set up yet)
```

## Repo layout

```
pipeline/            stage modules (see docstrings for details)
  config.py             TRACKS config + youtube_token_path() (per-track/per-channel OAuth token)
  story_writer.py         weekly story generation via Gemini — the only LLM call in the pipeline
  state.py                 per-track "already produced today" tracking (scripts_queue/state/)
  ai_image.py            per-scene AI illustrations (hosted FLUX, track-aware style), gradient fallback
  textcard.py             Ken Burns zoom animation + brand-gradient fallback
  shorts.py                assembles the single vertical Short — the actual production output
  assemble.py               long-form (16:9) assembler — tested, kept, unused by default
  broll.py                 Pexels/Pixabay fetch — tested, kept, unused by default
scripts_queue/        pending/<track>/ + used/<track>/ — one queue per audience track
archive/ai_tools_scripts/  retired scripts from this channel's original "AI tools & tips" concept
run_daily.py            orchestrator — loops all 4 tracks, one video each
run_weekly_analytics.py  pulls YouTube Analytics metrics
run_weekly_story_refill.py  generates + queues a week of stories per track via Gemini
.github/workflows/        daily-video.yml, weekly-analytics.yml (analytics + story refill)
assets/music/               royalty-free background tracks (you provide these)
analytics/reports/            weekly performance reports
```
