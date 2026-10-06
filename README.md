# festify

Turn Instagram concert/festival posts (or screenshots of them) into one private Spotify playlist per event. Built for small local shows, where many bands share names with unrelated artists elsewhere.

**How it works**

1. **Read the poster.** Claude (vision) extracts the event, date, venue, city and lineup from the images and caption. Tagged `@handles` are used to recover bands whose logos are illegible.
2. **Identify each band.** Deezer (fan counts) and Last.fm (listeners) find candidates. When several share a name, location evidence (MusicBrainz + web search, e.g. Bandcamp) and Claude pick the most plausible local one. With `--ask` you choose yourself.
3. **Pick songs.** Top songs come from Deezer/Last.fm and are matched on Spotify by ISRC. Spotify is only the destination.
4. **Build playlists.** One private playlist per event, named `<Event> – <Date>`, with the post image as the cover.
5. **Report.** Per event: matched, picked-from-several (with evidence), possibly non-local, not on Spotify, illegible logos, and the playlist link.

> Spotify's Feb 2026 Development Mode changes are respected: no top-tracks/popularity/followers, search limit 10, `POST /me/playlists`, `POST /playlists/{id}/items`. Only the official Web API is used.

## Requirements

- Python 3.10+
- A **Spotify Premium** account (required to own a Development Mode app)
- An **Anthropic API key** (paid, usage-based; see [Cost](#cost))
- A free **Last.fm API key** (optional but recommended)

## Setup

### 1. Get the code

```bash
git clone <this-repo-url> festify
cd festify
```

### 2. Install

macOS / Linux:
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Windows (PowerShell):
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 3. Create your Spotify app

1. Open <https://developer.spotify.com/dashboard> and log in (Premium account).
2. **Create app**. Any name and description.
3. **Redirect URI:** `http://127.0.0.1:8888/callback` exactly (not `localhost`). Click **Add**.
4. Tick **Web API**, accept the terms, **Save**.
5. Open the app's **Settings** and copy the **Client ID** and **Client secret**.

Development Mode apps allow 5 authorized users; the owner needs Premium.

### 4. Get the other keys

- **Anthropic:** <https://console.anthropic.com> → API Keys → Create Key. Add credit under Billing.
- **Last.fm (free):** <https://www.last.fm/api/account/create> (log in first). Only the API key is needed.

### 5. Configure

```bash
cp .env.example .env        # Windows: copy .env.example .env
```

Edit `.env` and fill in `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET`, `ANTHROPIC_API_KEY` and `LASTFM_API_KEY`. Never commit `.env` (it is gitignored).

| Variable | Purpose |
|---|---|
| `SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET` | Your Spotify app |
| `ANTHROPIC_API_KEY` | Poster reading, picking, web search |
| `LASTFM_API_KEY` | Secondary listener/top-track source |
| `FESTIFY_MODEL` | Claude model (default `claude-sonnet-5-5`) |
| `HOME_REGION` | "Local" when a poster has no city (default `Utah, Salt Lake City, Provo`; set your own) |
| `IG_USERNAME` | Optional, see Instagram below |
| `MUSICBRAINZ_CONTACT` | Your email or URL for the MusicBrainz User-Agent |

### 6. Try it

Dry run first (no Spotify login, creates nothing):

```bash
python -m festify path/to/poster.png --dry-run
```

The first real run opens a browser to authorize Spotify; the token is cached locally in `.spotify_token_cache`.

## Usage

```bash
python -m festify poster.png --dry-run
python -m festify https://www.instagram.com/p/XXXX/ screenshots_folder/ --tracks 4
python -m festify poster.png --ask          # choose same-name artists yourself
```

| Flag | Meaning |
|---|---|
| `--dry-run` | Show everything, create nothing |
| `--ask` | Pick among same-name artists yourself. Shows genres, releases, songs, location evidence, Claude's suggestion and an `open.spotify.com` link so you can check the About tab's "Where people listen". Your picks are cached. |
| `--tracks N` | Songs per artist (default 3) |
| `--no-web` | Never use web search (cheapest, less evidence) |
| `--deep` | Also web-search single-match artists (costly) |
| `--free` | Use the Gemini free tier instead of Claude |
| `--cache FILE` | Use a different cache file (e.g. for testing) |
| `--max-searches N` | Cap web-search lookups per run (default 25) |
| `--events-file F` / `--save-events F` | Reuse/save the extracted events as JSON |

**Inputs:** Instagram post/reel URLs, image files, or folders. Each URL and each folder is one post; loose image files are treated as one post. Duplicate events across posts are merged. A multi-venue festival spread over several slides becomes one event.

### Instagram

Link fetching uses [instaloader](https://instaloader.github.io/) and often works anonymously for public posts, but Instagram can block or rate-limit it at any time. If it does, festify says so; **screenshots always work**. For better odds, run `instaloader -l <your_username>` once to save a login session and set `IG_USERNAME` (a throwaway account is safer than your main one).

### Cache

Picks and location findings are stored in `.festify_cache.json`, so each artist is resolved once. Delete an entry (or the file) to re-resolve. `--ask` overrides cached Claude picks and saves yours.

## Free mode (no paid API)

Everything except Claude is already free (Deezer, Last.fm, MusicBrainz, Spotify). To replace Claude too, use Google's **Gemini free tier**:

1. Get a free key at <https://aistudio.google.com/apikey> (no billing needed).
2. Put it in `.env` as `GEMINI_API_KEY=...`.
3. Run with `--free` (or set `LLM_PROVIDER=gemini`):

```bash
python -m festify poster.png --free --dry-run
```

Gemini then reads posters, judges same-name artists and does Google-Search-grounded location lookups. Free tiers have rate and daily limits that Google can change; festify retries, and if a quota is hit it stops cleanly and keeps finished work cached, so just re-run later. If the main model is overloaded or unavailable, festify automatically falls back to `gemini-3.7-flash`, then `gemini-3.1-flash-lite`. Free-tier prompts may be used by Google to improve its products, so don't send anything private (public posters are fine). Poster-reading accuracy on stylized lettering may differ from Claude's.

## Cost

Poster reading uses `FESTIFY_MODEL` (Sonnet-class) and costs cents per post. Everything else is built to be cheap:

- Same-name judging and web search use the small `FESTIFY_FAST_MODEL` (default `claude-haiku-4-5-20251001`).
- **Web search is the expensive step**, so it only runs for artists with several same-name candidates (use `--deep` to also search single matches), at most 2 searches each, and at most `--max-searches` (default 25) per run.
- Results are cached, so each artist is paid for once.
- Every run ends with a usage line (calls, searches, tokens). Check your Anthropic Usage page after the first run; an early version without these limits cost about $7 on one 5-post, 70-artist run.

Cheapest options: `--no-web` (no search at all) and `--ask` (you make the hard calls). Start with one poster before running many.

## Tests

```bash
python -m pytest
```

Tests use mocked API responses and need no keys.

## Privacy and safety

- Keys live only in your local `.env`. The cache and token files are gitignored.
- Playlists are created as **private**.
- Be considerate with Instagram, MusicBrainz (the client throttles to under 1 request/second), Deezer and Last.fm, and follow their terms of use.

## License

MIT, see [LICENSE](LICENSE).
