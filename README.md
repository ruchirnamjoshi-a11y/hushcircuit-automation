# Faceless AI-Tools YouTube Automation

Fully-free, script-driven pipeline that produces and publishes a daily faceless
YouTube video (long-form + auto-clipped Short) about AI tools & tips, then feeds
performance data back into future content decisions.

## How it stays (nearly) free

Every stage is free, including scene illustrations — no per-image cost, no
billing account required anywhere:

| Stage | Tool | Cost |
|---|---|---|
| Script writing | Written interactively in Claude Code (this repo's `scripts_queue/`) | Covered by Claude Pro/Max — no API key |
| Voiceover | `edge-tts` | Free, uncapped |
| Scene illustrations | FLUX.1-schnell via Cloudflare Workers AI (`pipeline/ai_image.py`) | Free — 10,000 Neurons/day, no card |
| Assembly/captions/animation | FFmpeg + generated brand gradient (`pipeline/textcard.py`) | Free, local |
| Thumbnail | Pillow (matches the video's gradient) | Free |
| Upload | YouTube Data API v3 | Free quota |
| Scheduling | GitHub Actions | Free tier |
| Analytics | YouTube Analytics API | Free |

**There is no Anthropic API key anywhere in this codebase.** Scripts are written
by asking Claude Code (in a normal interactive session) to batch-write JSON files
into `scripts_queue/pending/`, following the schema in `pipeline/scripts.py`. The
automated daily run only *consumes* the queue — it never calls an LLM.

**Visuals are AI-generated per scene, not stock footage.** Free stock-footage
APIs (Pexels/Pixabay) have no real footage of "using ChatGPT" — matching by
keyword only ever gets generic, often-unrelated b-roll. Instead each scene gets
one AI illustration (`pipeline/ai_image.py`, FLUX.1-schnell via Cloudflare
Workers AI) prompted from that scene's own content, animated with a Ken Burns
zoom (`pipeline/textcard.py`), plus a numbered progress badge and big
word-by-word "pop" captions with the current key term accent-colored. If the
AI API call fails or `CF_ACCOUNT_ID`/`CF_API_TOKEN` aren't set, it falls back
automatically to a generated brand gradient with drifting glow orbs — the
pipeline never hard-fails on this stage. `pipeline/broll.py` (Pexels/Pixabay)
is kept in the codebase, tested, but unused by default.

Local Stable Diffusion generation (via `diffusers`) and several other
providers (Hugging Face Inference Providers, Google Gemini/Imagen) were
evaluated and rejected before landing on Cloudflare: HF's free tier only
covers ~10 images/month, Gemini's image models have zero free-tier quota, and
local SD on an M1/16GB Mac was either too slow (~11min/image on SDXL) or too
low-quality (SD1.5) for daily production use. Cloudflare Workers AI's free
tier comfortably covers this pipeline's ~8 images/day.

## Weekly loop (the only manual step)

Once a week:
1. `run_weekly_analytics.py` (via the `weekly-analytics` GitHub Action) drops a
   report in `analytics/reports/{date}.md`.
2. Bring that report to a Claude Code session in this repo and ask for the next
   ~10 scripts. I'll write them straight into `scripts_queue/pending/`.
3. Commit and push. The daily workflow does the rest.

If the queue runs dry, `run_daily.py` logs "queue empty, refill needed" and exits
cleanly — no crash, no partial video, no wasted API calls.

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
3. YouTube upload/analytics OAuth:
   - Create a Google Cloud project, enable **YouTube Data API v3** and
     **YouTube Analytics API**, create an OAuth Desktop client, download the JSON
     as `secrets/client_secret.json`.
   - Run `python -m pipeline.upload --auth-setup` once locally to complete the
     OAuth consent flow; this writes `secrets/youtube_token.json`.
   - For GitHub Actions, base64-encode that token file and store it as the repo
     secret `YOUTUBE_TOKEN_B64`, and add `CF_ACCOUNT_ID` / `CF_API_TOKEN` as
     their own repo secrets too.
4. Drop a few royalty-free tracks (e.g. from the YouTube Audio Library) into
   `assets/music/`.

## Testing each stage standalone

```bash
pytest tests/test_scripts.py     # queue schema/helpers, no network
pytest tests/test_tts.py          # real edge-tts call, no key needed
pytest tests/test_ai_image.py      # mocked HF client, no network/cost
pytest tests/test_textcard.py       # generated backgrounds, ffmpeg only, no network
pytest tests/test_assemble.py        # ffmpeg with synthetic lavfi inputs, no network
pytest tests/test_shorts.py           # same, vertical output
pytest tests/test_thumbnail.py         # Pillow only, no network
pytest tests/test_upload.py             # --dry-run, builds payload without calling API
pytest tests/test_broll.py               # optional/unused by default; needs an API key
```

Tests requiring an API key skip automatically (not fail) if that key is unset.
`test_ai_image.py` mocks the HF client entirely, so the suite never spends
real money — only a manual `run_daily.py` actually calls the paid API.

## Running the full pipeline manually

```bash
python run_daily.py            # consumes next queued script, uploads as "private"
python run_daily.py --dry-run   # does everything except the real YouTube upload
```

## Repo layout

```
pipeline/            stage modules (see docstrings for details)
  ai_image.py           per-scene AI illustrations (hosted FLUX), gradient fallback
  textcard.py            Ken Burns zoom animation + brand-gradient fallback
  broll.py                Pexels/Pixabay fetch — tested, kept, unused by default
scripts_queue/        pending/ + used/ — the script queue
run_daily.py            orchestrator for stages 2-6
run_weekly_analytics.py  orchestrator for stage 8
.github/workflows/        daily-video.yml, weekly-analytics.yml
assets/music/               royalty-free background tracks (you provide these)
analytics/reports/            weekly performance reports
```
