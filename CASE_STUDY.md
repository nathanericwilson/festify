# Case study: festify

**Paste an Instagram show poster, get a Spotify playlist of the bands on it.**
Built by directing an AI coding agent (Claude Code). I set the goals and rules, tested every result against reality, and corrected it until it worked.

## The problem

I go to small local shows in Utah. Posters list five or six bands I've never heard of, and I wanted a quick way to hear them before deciding whether to go.

Making a playlist from a list of names sounds trivial. It isn't, because of **identity**:

- Tiny bands share names with other artists. Searching "Skrude", "World Record" or "Disillusioned" returns the wrong band first.
- Spotify's February 2026 API changes removed the signals you'd normally use to tell them apart: top tracks, popularity and follower counts.
- No service exposes *where* an artist's listeners are.
- The best evidence, a band's own Instagram bio linking to its Spotify, sits behind Instagram's rate limits.

## How it works

1. **Read the poster.** Google Gemini (free tier) reads the images and caption: event, date, city, lineup. The bands' tagged Instagram accounts fill in logos that are hard to read.
2. **Find the band's own link.** The band's Instagram bio is read through Tavily's page reader (Instagram blocks direct lookups). Linktree pages and album or track links are followed to the band's Spotify artist page.
3. **Find candidates.** If there's no link, festify collects every artist with exactly that name on Deezer, Spotify and Last.fm.
4. **Measure distance.** Location evidence comes from MusicBrainz, web search snippets and Bandcamp. It's geocoded with OpenStreetMap and measured against a list of cities I care about.
5. **Decide without asking.** Deterministic rules come first; for example, if exactly one candidate is local, it wins. An LLM only breaks ties. If several same-name artists are plausible and nearby, up to 3 are included with fewer songs each.
6. **Pick songs.** Top tracks are matched across services by **ISRC** (a recording's unique code), so the songs really belong to the chosen artist.
7. **Build the playlist.** Each post gets one private playlist with the poster as its cover. Anything uncertain is written into the **playlist description**, e.g. "Maybe another band with the same name: CornerCut".

## Decisions I made, and why

| Decision | Why |
|---|---|
| **Never ask the user anything** | I want to paste a link and walk away. Uncertainty goes into the playlist description instead of a prompt. |
| **A flagged guess beats a missing band** | The playlist is for discovery. A possibly-wrong song I can skip costs less than a band I never hear. |
| **At most 3 same-name artists, and only nearby ones** | Including every plausible match floods the playlist; far-away artists are never added as extras. |
| **Distance from my own list of cities** | "Local" is personal. Listener-location data doesn't exist in any API, so distance is the best substitute. |
| **Free tier only** | An early version on a paid model cost about $7 in one run. I had it rebuilt to run free (Gemini free tier, Tavily's 1,000 free searches a month, and keyless Deezer and MusicBrainz), with every answer cached. |
| **No anti-bot workarounds** | I asked for research into how others read Instagram. Proxies and scrapers were ruled out; reading public profile pages through a search provider was the legitimate route. |
| **Rebuild, don't patch** | When the first design kept producing wrong bands, I told the agent nothing was sacred, and it rebuilt matching around distance and the never-ask rules. |

## How I tested it

I built a **truth set** (`tests/golden.json`) from bands I checked myself, and an evaluator (`python -m festify.eval`). It re-decides every band from cached evidence, ignoring my manual fixes, and prints right and wrong.

| Stage | Correct |
|---|---|
| First version of the new design | 6 / 9 |
| After reading bios through Tavily, finding untagged bands' handles in search results, and following album links | 8 / 9 |
| After deciding a flagged guess counts as acceptable | 9 / 9 |
| After adding a new show (4 more bands, all right on the first run) | **13 / 13** |

Thirteen bands is a small sample, so it's a regression check, not a claim about general accuracy. The set grows each time I confirm a new show.

### Errors I caught along the way

- **The wrong Skrude and Disillusioned.** The first version matched by name and confidently picked bands from elsewhere. I supplied the real Spotify links, which became the first truth-set entries.
- **"Verifiably false."** The agent said World Record's Instagram bio had no Spotify link. I sent a screenshot showing an album link. The fix was supporting album and track links, and recognizing that the page reader sometimes misses a profile's link section.
- **A stale app.** A run failed with an old error because a copy of the app from the day before was still running. Now a newly opened window replaces the old one.
- **Rate limits.** Spotify's developer mode banned the app for about 14 hours after one large refresh. Calls are now spaced and capped, and results cached, so reruns are small.

## What I'd do next

- Grow the truth set to 25–30 bands across more shows.
- Add a short demo recording to the README.
- If it's ever shared beyond my own PC: Instagram's official Graph API ("Business Discovery") is the sanctioned way to read business and creator accounts' bios.

## Stack

Python · Spotify Web API · Deezer · Last.fm · MusicBrainz · OpenStreetMap Nominatim · Tavily · Google Gemini (free tier) · a small local web app.
