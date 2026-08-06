# Faceless AI-Tools YouTube Automation

Fully-free, script-driven pipeline that produces and publishes a daily faceless
YouTube video (long-form + auto-clipped Short) about AI tools & tips, then feeds
performance data back into future content decisions.

## How it stays free

Every stage runs on a free tier except your existing Claude subscription:

| Stage | Tool | Cost |
|---|---|---|
| Script writing | Written interactively in Claude Code (this repo's `scripts_queue/`) | Covered by Claude Pro/Max — no API key |
| Voiceover | `edge-tts` | Free, uncapped |
| B-roll | Pexels API (Pixabay fallback) | Free tier |
| Assembly/captions | FFmpeg | Free, local |
| Thumbnail | Pillow (generated) | Free |
| Upload | YouTube Data API v3 | Free quota |
| Scheduling | GitHub Actions | Free tier |
| Analytics | YouTube Analytics API | Free |

**There is no Anthropic API key anywhere in this codebase.** Scripts are written
by asking Claude Code (in a normal interactive session) to batch-write JSON files
into `scripts_queue/pending/`, following the schema in `pipeline/scripts.py`. The
automated daily run only *consumes* the queue — it never calls an LLM.

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
2. Copy `.env.example` to `.env` and fill in:
   - `PEXELS_API_KEY` — free at https://www.pexels.com/api/
   - `PIXABAY_API_KEY` — optional fallback, free at https://pixabay.com/api/docs/
3. YouTube upload/analytics OAuth:
   - Create a Google Cloud project, enable **YouTube Data API v3** and
     **YouTube Analytics API**, create an OAuth Desktop client, download the JSON
     as `secrets/client_secret.json`.
   - Run `python -m pipeline.upload --auth-setup` once locally to complete the
     OAuth consent flow; this writes `secrets/youtube_token.json`.
   - For GitHub Actions, base64-encode that token file and store it as the repo
     secret `YOUTUBE_TOKEN_B64` (plus `PEXELS_API_KEY` / `PIXABAY_API_KEY` as
     their own secrets).
4. Drop a few royalty-free tracks (e.g. from the YouTube Audio Library) into
   `assets/music/`.

## Testing each stage standalone

```bash
pytest tests/test_scripts.py     # queue schema/helpers, no network
pytest tests/test_tts.py          # real edge-tts call, no key needed
pytest tests/test_broll.py         # needs PEXELS_API_KEY or PIXABAY_API_KEY
pytest tests/test_assemble.py       # ffmpeg with synthetic lavfi inputs, no network
pytest tests/test_shorts.py          # same, vertical output
pytest tests/test_thumbnail.py        # Pillow only, no network
pytest tests/test_upload.py            # --dry-run, builds payload without calling API
```

Tests requiring an API key skip automatically (not fail) if that key is unset.

## Running the full pipeline manually

```bash
python run_daily.py            # consumes next queued script, uploads as "private"
python run_daily.py --dry-run   # does everything except the real YouTube upload
```

## Repo layout

```
pipeline/            stage modules (see docstrings/plan for details)
scripts_queue/        pending/ + used/ — the script queue
run_daily.py            orchestrator for stages 2-6
run_weekly_analytics.py  orchestrator for stage 8
.github/workflows/        daily-video.yml, weekly-analytics.yml
assets/music/               royalty-free background tracks (you provide these)
analytics/reports/            weekly performance reports
```
