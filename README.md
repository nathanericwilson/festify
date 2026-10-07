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

## The app (no terminal needed)

Double-click **`Festify.bat`** (Windows), or run `python -m festify.gui`. A page opens in your browser at `http://127.0.0.1:8765` (only reachable from your own computer):

- paste Instagram links and/or drop screenshots (all dropped images count as one post),
- tick options (free mode, dry run, refresh, no web search, resume last run, songs per artist),
- pin wrong bands under "Fix a wrong band",
- watch the live log; playlist links appear as clickable cards when done.

Every run is saved to `logs/festify_<date>_<time>.log`, and **Copy log** puts the whole log on your clipboard for troubleshooting. Picking between same-name bands interactively (`--ask`) still needs the terminal.

Extracted events are added to `events.json`, so **Resume last run** (or `--events-file events.json`) can refresh any earlier playlist without re-reading posters. Existing playlists are matched even if a poster is read with a slightly different event name, so they aren't duplicated.

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
| `--pin "Name=<spotify link>"` | Lock an artist to an exact Spotify artist (fixes wrong same-name picks) |
| `--refresh` | Re-fill playlists already created, after fixing picks |
| `--max-searches N` | Cap web-search lookups per run (default 25) |
| `--events-file F` / `--save-events F` | Reuse / add to the extracted events JSON |
| `--events-only` | Just read posters and save events (no Spotify) |

**Inputs:** Instagram post/reel URLs, image files, or folders. Each URL and each folder is one post; loose image files are treated as one post. Duplicate events across posts are merged. A multi-venue festival spread over several slides becomes one event.

### Instagram

Link fetching uses [instaloader](https://instaloader.github.io/) and often works anonymously for public posts, but Instagram can block or rate-limit it at any time. If it does, festify says so; **screenshots always work**. For better odds, run `instaloader -l <your_username>` once to save a login session and set `IG_USERNAME` (a throwaway account is safer than your main one).

### Finding bands through their Instagram

When the poster tags a band's account, festify looks for that band's own Spotify link: first in the Instagram bio and bio links (following a Linktree or website), then in a Linktree with the same name as the handle that links back to the account. A link found this way wins over name-based guessing (your own `--pin` still wins over everything), as long as the Spotify artist's name matches the poster.

Instagram blocks anonymous profile lookups quickly, so for this to work reliably, log in once (ideally with a spare account, since Instagram sometimes flags automated use):

```bash
instaloader -l YOUR_INSTAGRAM_USERNAME
```

and set `IG_USERNAME=YOUR_INSTAGRAM_USERNAME` in `.env`.

If that login fails (Instagram often refuses it for new accounts with a bare "fail"), log in to instagram.com in **Firefox** instead, close Firefox, and import that login (no password typed in the terminal):

```bash
instaloader --load-cookies firefox
```

Even when logged in, Instagram may rate-limit profile lookups for a while after many requests; festify then skips them for that run and you can re-run later. Results are cached per handle, so each account is looked up once. Use `--no-instagram` to turn this off.

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

## Fixing wrong matches

Many small bands share names with unrelated artists. festify handles this in three ways:

- A name with **one exact match** is accepted. If the evidence suggests it's a different, non-local band, the report flags it ("possibly NOT local") but its songs stay in the playlist.
- A name with **several same-name artists** only gets songs verifiably by the chosen artist: ISRC matches, or that artist's own Spotify page when it can be tied to them (an ISRC credit, a MusicBrainz link, or a confident pick with only one Spotify artist of that name). If the picker isn't highly confident, no songs are added and the report lists the candidates under **NEEDS YOUR PICK**.
- You can always override: `--ask` lets you choose interactively, and `--pin "Name=https://open.spotify.com/artist/<id>"` locks an artist to an exact Spotify artist. Then re-run with `--refresh` to update playlists that were already created. Playlists are never duplicated: finished events are remembered in the cache.

## Troubleshooting and limits

- **Spotify rate limit.** Development Mode apps have strict request limits. If you hit them, Spotify can ban the app for many hours (we saw about 23). festify spaces its Spotify calls, caps lookups per artist, and stops with a clear message instead of waiting; finished work is cached, so re-run later. Matched songs are cached to keep repeat runs small, and a dry run uses Spotify calls too, so don't run many back to back.
- **Gemini free quota.** Free limits (requests per minute/day, per model) are listed in your AI Studio rate-limit page and can change. festify spaces calls, retries, falls back to other free models, then stops cleanly when a quota is used up. Re-run later. Google Search grounding has its own, much smaller free quota; when it runs out festify keeps going without web search (MusicBrainz evidence only) instead of stopping.
- **Claude billing errors** (no credit or a bad key) also stop the run immediately and are never cached as results.
- **Instagram blocks.** Use screenshots instead; they always work.
- **Resume without re-reading posters:** `python -m festify --events-file events.json` (save it first with `--save-events events.json`).

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
