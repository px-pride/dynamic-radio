# Dynamic Radio: 24/7 Automated Music Service — Design Document

**Date:** 2026-03-07
**Status:** Research & Design (no implementation yet)

---

## Table of Contents

1. [Overview](#1-overview)
2. [Music API Comparison](#2-music-api-comparison)
3. [Unofficial API Deep Dive](#3-unofficial-api-deep-dive)
4. [Recommendation: Tidal (Primary)](#4-recommendation-tidal-primary)
5. [Architecture](#5-architecture)
6. [Smart Scheduling](#6-smart-scheduling)
7. [Token Budget & LLM Efficiency](#7-token-budget--llm-efficiency)
8. [Track Selection Data Flow](#8-track-selection-data-flow)
9. [User Override Mechanism](#9-user-override-mechanism)
10. [Open Questions & Tradeoffs](#10-open-questions--tradeoffs)

---

## 1. Overview

A service that continuously plays contextually appropriate music 24/7, adapting to time of day, mood, and activity. Designed for a single user — electronic music producer, bass player, meditator — running on Arch Linux.

**Core constraints:**
- Minimal LLM token usage (design goal: <$1/month)
- Easy user override at any time
- No manual playlist management day-to-day
- Legal enough for personal use
- High audio quality preferred

---

## 2. Music API Comparison

### Evaluation Matrix

| Criterion | Spotify | Tidal | YouTube Music | Local (MPD) |
|---|---|---|---|---|
| **Official API** | Excellent — full Player, Search, Audio Features, Recommendations | Limited — catalog/metadata only, no playback | None for YT Music; YT Data API is data-only | N/A (local files) |
| **Unofficial API** | librespot/spotifyd (headless playback) | **tidalapi** (v0.8.11) — full access: streams, search, mixes, BPM/key | **ytmusicapi** (v1.11.5) + **yt-dlp** — metadata + audio extraction | N/A |
| **Playback control** | Spotify Connect API (official) | Direct stream URLs from tidalapi → mpv | yt-dlp piped to mpv | MPD protocol |
| **Audio quality** | Up to 320kbps OGG / lossless FLAC (Premium) | Up to 24-bit/192kHz FLAC (HiFi Plus) | Up to 256kbps AAC (Premium) | Whatever you source |
| **BPM & Key from API** | Yes (deprecated but functional) | **Yes — native from tidalapi** | No — need external analysis | Need external analysis |
| **Audio features** | energy, valence, danceability, tempo, key, etc. (deprecated) | BPM, key, key_scale, replay_gain, peak | Only loudnessDb | Need Essentia |
| **Recommendations** | Tunable seed-based (deprecated) | Track radio, artist radio, daily/discovery mixes, mood pages | Radio via watch_playlist, personalized home feed | No |
| **Linux playback** | spotifyd (unofficial) or official client | tidalapi stream URLs → mpv (headless, no GUI needed) | yt-dlp → mpv (headless) | MPD (native) |
| **Cost** | $12.99/month (NEW sub needed) | Already subscribed | Already subscribed | Free (own files) |
| **ToS risk** | Gray area — no crossfading; deprecated features | Gray area — unofficial API, personal use | Gray area — yt-dlp violates ToS | None |
| **Auth for 24/7** | OAuth token refresh | OAuth auto-refresh built in | OAuth auto-refresh built in | N/A |
| **Rate limits** | ~100-180 req/30s (undisclosed) | 429 with Retry-After (undisclosed) | Undisclosed (risk of blocking) | None |
| **Catalog size** | ~100M+ tracks | ~100M+ tracks | ~100M+ (includes YouTube content, uploads, bootlegs) | Your library |
| **Maintenance risk** | Official API — stable but deprecating features | Credential rotation 2-3x/year; active maintainer fixes quickly | yt-dlp 150K stars, ytmusicapi 2.5K stars; very active | None |

### Per-Service Notes

**Spotify** — The only service with an official API covering metadata AND playback. Audio Features provide algorithmic selection data. However: Audio Features, Recommendations are **deprecated** (March 2026), Developer Policy prohibits crossfading/mixing/broadcasting, and **user would need a new subscription ($12.99/mo)**. spotifyd (unofficial librespot) is needed for headless Linux playback.

**Tidal (via tidalapi)** — **User already subscribed.** The unofficial `tidalapi` (v0.8.11, Jan 2026) is actively maintained and provides direct stream URLs (no yt-dlp needed), BPM/key metadata from the API, track/artist radio, daily/discovery mixes, and lossless FLAC quality. OAuth auto-refresh supports 24/7 operation. Stream URLs can be fed directly to mpv. The `dj_ready` and `stem_ready` flags on tracks are a unique bonus. Main risk: Tidal rotates embedded API credentials 2-3x/year, requiring library updates.

**YouTube Music (via ytmusicapi + yt-dlp)** — **User already subscribed.** ytmusicapi (v1.11.5, Jan 2026) provides search, library, playlists, personalized radio, and home feed. However, it provides NO audio analysis data (no BPM, key, energy). Audio requires yt-dlp extraction (1.5-4s latency per track). The broadest catalog (includes user uploads, bootlegs, live recordings). Mopidy-YTMusic is **dead/archived** — do not use.

**Local Library (MPD)** — Zero legal risk, zero API dependency, perfect Linux support. Good for own productions and Bandcamp purchases. MPD supports gapless playback, crossfade, ReplayGain, PipeWire output.

---

## 3. Unofficial API Deep Dive

### tidalapi (python-tidal) — v0.8.11

**Repo:** github.com/tamland/python-tidal (maintained by tehkillerbee under EbbLabs)
**Status:** Actively maintained — 4 releases in 3 months (Oct 2025 – Jan 2026)
**License:** LGPL-3.0

**Key capabilities for Dynamic Radio:**

| Feature | Details |
|---|---|
| **Stream URLs** | `track.get_url()` returns direct HTTP URL to audio file (m4a/FLAC). For HI_RES, returns MPEG-DASH manifest. |
| **BPM & Key** | `track.bpm`, `track.key`, `track.key_scale` — native from API. Not all tracks have it, but many do. |
| **Quality tiers** | LOW (96k AAC), HIGH (320k AAC), LOSSLESS (16-bit FLAC), HI_RES_LOSSLESS (24-bit/192kHz FLAC) |
| **Track radio** | `track.get_track_radio(limit=100)` — endless similar tracks from a seed |
| **Artist radio** | `artist.get_radio(limit=100)` — similar to artist |
| **Personalized mixes** | `session.mixes()` — daily mix, discovery mix, history mixes, producer/songwriter mixes |
| **Browse/discover** | `session.home()`, `session.for_you()`, `session.moods()`, `session.genres()`, `session.explore()` |
| **Search** | Full search across tracks, albums, artists, playlists (up to 300 results) |
| **DJ-ready flags** | `track.dj_ready`, `track.stem_ready` — Tidal marks tracks suitable for DJ use |
| **Replay gain** | `track.replay_gain`, `track.peak`, stream-level `album_replay_gain` |
| **Auth** | OAuth2 device flow (headless-friendly). Auto-refresh on expired tokens. Session persistence to file. |
| **Rate limiting** | 429 with Retry-After header. Library raises `TooManyRequests` exception — caller must handle. |

**Auth longevity for 24/7:** OAuth refresh tokens are long-lived (no expiry unless revoked). The library auto-refreshes access tokens transparently on every request. Session file persists across restarts.

**Breakage risk:** Tidal rotates embedded client credentials 2-3x/year. When this happens, a new tidalapi release is needed (typically within days). You can also provide your own credentials via `Config()`.

**Playback integration:** BTS stream URLs are direct HTTPS links to audio files — feed to mpv, ffplay, GStreamer, or download to buffer. No yt-dlp needed.

### ytmusicapi — v1.11.5

**Repo:** github.com/sigma67/ytmusicapi
**Status:** Actively maintained — 6 releases in 8 months, last commit Feb 2026
**License:** MIT

**Key capabilities:**

| Feature | Details |
|---|---|
| **Search** | Full-text with filters (songs, videos, albums, artists, playlists, uploads) |
| **Personalized home** | `get_home()` — recommendation rows ("Your morning music", Supermix, etc.) |
| **Radio** | `get_watch_playlist(videoId, radio=True)` — algorithmic radio from seed track |
| **Mood browsing** | `get_mood_categories()` + `get_mood_playlists()` — "Chill", "Dance & Electronic", etc. |
| **Library** | Full access to library songs, albums, playlists, history, uploads |
| **Lyrics** | `get_lyrics()` with optional timestamps |
| **Auth** | OAuth with `RefreshingToken` — auto-refreshes within 60s of expiry, persists to file |

**What it does NOT have:** No BPM, key, energy, danceability, or any audio features. Only `loudnessDb` from streaming metadata. Would need Essentia/librosa for audio analysis.

**Audio extraction:** Requires yt-dlp. Latency: 1.5-4s from URL to first audio. Can pipe to mpv (`mpv --no-video URL`). Premium cookies needed for higher quality.

**Mopidy-YTMusic:** DEAD. Archived, depends on pytube (abandoned) and ancient ytmusicapi versions. Do not use.

### Comparison: Tidal vs YouTube Music for DJ System

| Criterion | Tidal (tidalapi) | YouTube Music (ytmusicapi + yt-dlp) |
|---|---|---|
| **BPM/Key from API** | Yes (native) | No (need external analysis) |
| **Audio extraction** | Direct URL from API (no extra tool) | Requires yt-dlp (extra dependency) |
| **Playback latency** | ~0.5-1s (direct HTTP URL) | ~1.5-4s (yt-dlp resolution) |
| **Audio quality** | Up to 24-bit/192kHz FLAC | Up to 256kbps AAC |
| **Gapless potential** | Easy (pre-fetch next URL, feed to mpv) | Harder (yt-dlp resolution delay) |
| **Catalog breadth** | ~100M tracks (mainstream + indie) | ~100M+ (includes YouTube content, bootlegs, user uploads) |
| **Discovery** | Track/artist radio, daily mixes, mood pages | Radio, personalized home, mood categories |
| **Own music** | No upload feature | Can upload to YTM library |
| **Dependencies** | tidalapi only | ytmusicapi + yt-dlp (two moving parts) |
| **Auth robustness** | Auto-refresh, session file persistence | Auto-refresh, token file persistence |

**Winner for DJ system: Tidal** — native BPM/key, direct stream URLs, higher quality, simpler stack. YouTube Music is the better fallback for catalog breadth.

---

## 4. Recommendation: Tidal (Primary) + YouTube Music (Fallback)

**Primary: Tidal via tidalapi + mpv**

Rationale:
- **Already subscribed** — no new cost
- **BPM and key from the API** — critical for harmonic mixing, no external analysis needed
- **Direct stream URLs** — no yt-dlp dependency, lower latency, simpler architecture
- **Lossless FLAC quality** — audiophile-grade for a music producer
- **Track radio + daily mixes** — good algorithmic discovery
- **DJ-ready/stem-ready flags** — Tidal specifically marks DJ-suitable content
- **Auto-refresh auth** — designed for 24/7 operation
- **Single dependency** — just `tidalapi` for both metadata and audio

**Secondary: YouTube Music via ytmusicapi + yt-dlp**

For when:
- Track not found on Tidal (bootlegs, remixes, YouTube-only content)
- Want to play own uploaded music from YTM library
- Tidal API is temporarily broken (credential rotation)

**Spotify: Available as last resort**

If both Tidal and YTM are down, or if Spotify's audio features become critical. Would require new subscription.

**Local Library (MPD): Offline fallback**

For own productions, Bandcamp purchases, and internet outages.

### Why Tidal Over Spotify?

| Factor | Tidal | Spotify |
|---|---|---|
| Subscription cost | Already paying | +$12.99/month |
| BPM/Key | Native from API | Deprecated endpoint (may die) |
| Audio quality | 24-bit/192kHz FLAC | 24-bit/44.1kHz FLAC |
| Stream access | Direct URLs (simple) | Must use Connect SDK (complex) |
| Playback control | Full control via mpv | Locked to Spotify client/SDK |
| Crossfading | Your player, your rules | Explicitly prohibited by ToS |
| Rate limits | Comparable | Comparable |
| API stability | Unofficial but actively maintained | Official but deprecating key features |

The main thing Spotify has that Tidal doesn't: energy/valence/danceability/acousticness features and a tunable recommendations API. But BPM + key covers the most important DJ dimensions, and Tidal's track radio handles discovery.

---

## 5. Architecture

### System Diagram

```
┌──────────────────────────────────────────────────────────┐
│                     Dynamic Radio Service                       │
│                   (systemd daemon)                         │
│                                                           │
│  ┌──────────────┐    ┌──────────────┐   ┌──────────────┐ │
│  │  Scheduler    │    │ Track        │   │ Playback     │ │
│  │  Engine       │───▶│ Selector     │──▶│ Controller   │ │
│  │              │    │              │   │              │ │
│  │ - Daily plan  │    │ - BPM/Key    │   │ - mpv (IPC)  │ │
│  │ - Time interp │    │   matching   │   │ - Pre-buffer │ │
│  │ - Modifiers   │    │ - Diversity  │   │ - Gapless    │ │
│  └──────┬───────┘    │ - Transition │   │ - Volume     │ │
│         │            └──────────────┘   └──────┬───────┘ │
│         │                                       │         │
│  ┌──────▼───────┐    ┌──────────────┐          │         │
│  │  Claude Agent │    │ Override     │          │         │
│  │  SDK (Claude  │    │ Manager     │──────────┘         │
│  │  Code login)  │    │             │                     │
│  │              │    │ - Discord   │                     │
│  │ - Daily plan  │    │   commands  │                     │
│  │ - Adjustments │    │ - Resume    │                     │
│  │ - $0 extra    │    │   logic     │                     │
│  └──────────────┘    │   logic     │                     │
│                      └──────────────┘                     │
│                                                           │
│  ┌──────────────┐    ┌──────────────┐   ┌──────────────┐ │
│  │  Data Store   │    │ Tidal Client │   │ YTM Client   │ │
│  │  (SQLite)     │    │ (tidalapi)   │   │ (ytmusicapi  │ │
│  │              │    │             │   │  + yt-dlp)   │ │
│  │ - Track cache │    │ - Search     │   │             │ │
│  │ - BPM/Key     │    │ - Stream URL │   │ - Fallback   │ │
│  │ - Play history│    │ - Radio      │   │   catalog    │ │
│  │ - DJ plans    │    │ - Mixes      │   │ - Uploads    │ │
│  │ - User prefs  │    │ - BPM/Key    │   │ - Bootlegs   │ │
│  └──────────────┘    └──────────────┘   └──────────────┘ │
└──────────────────────────────────────────────────────────┘
          │                                    │
          │ Discord (Axi integration)          │ Direct stream URLs
          ▼                                    ▼
   ┌─────────────┐                     ┌─────────────┐
   │ User         │                     │ mpv         │
   │ (via Discord)│                     │ (IPC socket) │
   └─────────────┘                     │      │      │
                                       │      ▼      │
                                       │  PipeWire   │
                                       │  ──▶ Audio  │
                                       └─────────────┘
```

### Deployment

- **Runs as:** systemd user service (`dynamic-radio.service`)
- **Language:** Python (best ecosystem for tidalapi, ytmusicapi, LLM clients)
- **Integration:** Axi bot for Discord commands (override, status, mood). LLM calls (daily plans, adjustments) use the Claude Agent SDK directly, reusing Claude Code login — no separate API key or billing needed
- **Playback:** mpv with JSON IPC socket — receives direct stream URLs from tidalapi, handles gapless playback via playlist append
- **Audio output:** mpv → PipeWire → speakers
- **Fallback chain:** Tidal → YouTube Music → Local files (MPD)
- **Data store:** SQLite (track cache, BPM/key, play history, DJ plans, user preferences)

### Why integrate with Axi?

- Axi already has Discord infrastructure — override commands come naturally
- Axi already runs as a persistent service
- User already interacts with Axi daily — no new interface to learn
- Can share scheduling infrastructure
- LLM calls use the Claude Agent SDK directly (same Claude Code login as Axi)

---

## 6. Smart Scheduling

### Daily DJ Plan (LLM-Generated)

Once per day (e.g., at 5:00 AM PT), the system generates a "DJ Plan" — a structured JSON schedule dividing the day into 1-2 hour time blocks, each with mood/genre/energy parameters.

**User profile drives the plan:**

```
┌─────┬─────────────┬────────┬──────────────────────────────────┐
│Time │ Activity    │ Energy │ Music Character                   │
├─────┼─────────────┼────────┼──────────────────────────────────┤
│ 5-7 │ Meditation  │ 0.1    │ Drone, deep ambient, silence-OK  │
│ 7-9 │ Morning     │ 0.25   │ Ambient, downtempo, lo-fi        │
│9-12 │ Deep work   │ 0.45   │ Minimal, IDM, deep house         │
│12-1 │ Lunch       │ 0.55   │ Jazz fusion, dub, world          │
│ 1-4 │ Afternoon   │ 0.40   │ Downtempo, IDM, minimal          │
│ 4-6 │ Creative    │ 0.50   │ Techno, deep house, dub          │
│ 6-8 │ Evening     │ 0.35   │ Downtempo, lo-fi, jazz fusion    │
│8-10 │ Wind-down   │ 0.20   │ Ambient, drone, downtempo        │
│10-5 │ Sleep/off   │ 0.05   │ Silence or very quiet drone      │
└─────┴─────────────┴────────┴──────────────────────────────────┘
```

This is the **default profile**, but the LLM can modify it based on:
- Day of week (weekend = more flexible, Friday evening = higher energy)
- Weather (rainy = lower energy, introspective)
- Calendar context (meeting in 30min = calmer; creative session = matched energy)
- Yesterday's feedback (user said "too much ambient" → adjust)
- Season (summer evenings = longer, warmer vibes)

### Plan Format (JSON)

```json
{
  "date": "2026-03-07",
  "generated_at": "2026-03-07T05:00:00-08:00",
  "blocks": [
    {
      "start": "05:00",
      "end": "07:00",
      "mood": "contemplative",
      "energy": 0.10,
      "genres": ["drone", "ambient"],
      "bpm_range": [55, 75],
      "features": {
        "valence": 0.2,
        "instrumentalness": 0.95,
        "danceability": 0.05,
        "acousticness": 0.6
      },
      "description": "Pre-dawn meditation. Spacious, minimal, breath-like."
    }
  ]
}
```

### Interpolation Between Blocks

No hard cuts between time blocks. In the last 15 minutes of each block, parameters linearly interpolate toward the next block. This creates smooth energy transitions throughout the day.

---

## 7. Token Budget & LLM Efficiency

### Design Principle

**LLM for creative planning, rules + embeddings for execution.**

The LLM is called for high-level decisions (what kind of music for each part of the day). Track-level selection is handled by vector similarity search and rule-based filtering — zero LLM cost per track.

### LLM Integration: Claude Agent SDK (Zero Additional Cost)

The Dynamic Radio uses the **Claude Agent SDK** (`claude_agent_sdk`) for LLM calls, reusing the existing Claude Code login — no separate API key or billing needed. This is the same approach Axi uses.

**How it works:**

The Agent SDK authenticates implicitly by inheriting Claude Code's credentials from the environment. The Dynamic Radio creates a `ClaudeSDKClient` for plan generation, same as any other Agent SDK consumer:

```python
from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions

options = ClaudeAgentOptions(
    model="haiku",
    cwd=DATA_DIR,
    system_prompt=DJ_PLANNER_PROMPT,
)
client = ClaudeSDKClient(options=options)
```

**LLM call points:**

1. **Daily plan generation** — Cron or systemd timer at 5 AM PT. The Dynamic Radio daemon calls the Agent SDK to generate a DJ plan JSON based on day of week, season, and user feedback. Writes to `~/.local/share/dynamic-radio/plans/YYYY-MM-DD.json`.

2. **Real-time adjustments** — When the user says `/dj mood more energy`, the daemon calls the Agent SDK to adjust the remaining plan in-place.

3. **Track selection** — Handled entirely by rules + BPM/key matching. Zero LLM involvement per track.

**Cost: $0.00/month** — all LLM calls use the existing Claude Code subscription. No separate API key needed.

### Token Estimates (for reference)

Even if using the API directly, the cost would be minimal:

| Call Type | Input Tokens | Output Tokens | Frequency |
|---|---|---|---|
| Daily plan generation | ~600 | ~1,000 | 1x/day |
| Plan adjustment (feedback) | ~1,800 | ~600 | 0-2x/day |
| **Total per day** | **~4,200-6,400** | | |

This would be ~$0.36/month on Claude 3.5 Haiku — but routed through Axi, it's free.

### LLM Call Decision Tree

```
Event occurs
  │
  ├─ New day (5 AM cron) ──────────────▶ Agent SDK call: Generate daily plan
  │
  ├─ Track ended ───────────────────────▶ Rule engine + BPM/key matching (NO LLM)
  │
  ├─ User skipped 1 track ─────────────▶ Pick next candidate (NO LLM)
  │
  ├─ User skipped 3+ in 10 min ────────▶ Agent SDK call: Adjust remaining plan
  │
  ├─ User says "more energy" ──────────▶ Agent SDK call: Adjust remaining plan
  │
  ├─ User likes track ─────────────────▶ Boost similar tracks locally (NO LLM)
  │
  └─ Context change (calendar event) ──▶ Agent SDK call: Adjust remaining plan
```

---

## 8. Track Selection Data Flow

### Per-Track Selection (every ~3-5 minutes, zero LLM)

```
1. GET CURRENT CONTEXT
   ├── Current time → look up active DJ plan block
   ├── Interpolate if near block boundary
   └── Result: target { energy, genres, bpm_range, key_preference, ... }

2. QUERY CANDIDATES (Tidal API)
   ├── Primary: track.get_track_radio(limit=50) from recent good seed track
   ├── Alternate: session.mixes() → daily/discovery mix tracks
   ├── Alternate: session.moods() → mood-matched playlist tracks
   ├── Cache results in SQLite (avoid re-fetching same radio)
   └── Result: ~20-50 candidate tracks with metadata

3. ENRICH WITH BPM/KEY
   ├── For each candidate, check SQLite cache for BPM/key
   ├── If not cached, tidalapi provides track.bpm, track.key, track.key_scale
   ├── Store in SQLite for future use
   └── Result: candidates with BPM + key data

4. FILTER
   ├── Remove tracks played in last 24 hours
   ├── Remove same artist played in last 2 hours
   ├── Remove tracks user has disliked
   ├── BPM within plan block's bpm_range
   ├── BPM within ±15 of previous track (smooth transition)
   ├── Compatible key (Camelot wheel, ±1 position)
   └── Result: ~5-10 viable candidates

5. RANK
   ├── Weighted score:
   │   ├── BPM closeness to target (30%)
   │   ├── BPM compatibility with previous track (20%)
   │   ├── Key compatibility with previous track (20%)
   │   ├── Genre match to plan block (15%)
   │   ├── User affinity (play count, dj_ready flag) (10%)
   │   └── Novelty bonus (haven't heard recently) (5%)
   └── Result: ranked list

6. SELECT
   ├── Weighted random from top 5 (avoid determinism)
   └── Result: single track

7. PLAY
   ├── Get stream URL: track.get_url() → direct HTTPS link
   ├── Queue in mpv via IPC: loadfile <url> append
   ├── Log to play history (SQLite)
   └── Pre-resolve next track's stream URL for gapless playback

8. FALLBACK (if track unavailable on Tidal)
   ├── Search YouTube Music via ytmusicapi
   ├── Extract audio via yt-dlp → pipe to mpv
   └── Higher latency (~2-4s) but broader catalog
```

### Building the Track Database

On first run and periodically:

1. Fetch user's Tidal favorites, playlists, and mix contents
2. For each track, store: `(tidal_id, name, artist, album, bpm, key, key_scale, duration, dj_ready, stem_ready)`
3. Populate via track radio seeds to grow the candidate pool
4. Index BPM and key for fast filtering

Over time, the database grows organically as track radio introduces new tracks and user interactions (likes/skips) refine preferences.

### Missing vs Spotify: Energy/Valence/Danceability

Tidal doesn't provide Spotify-style energy/valence/danceability features. Mitigation:

1. **BPM + key** covers the most important DJ dimensions (tempo matching, harmonic mixing)
2. **Genre + mood** from Tidal's browse pages provides categorical energy proxies
3. **LLM daily plan** describes the vibe in text — this guides seed track selection, which then drives track radio
4. **Optional: Essentia analysis** — run on cached audio for energy/danceability if needed later
5. **User feedback loop** — likes/skips teach the system what "energizing" means for this user

---

## 9. User Override Mechanism

### Design Goals
- **Transparent:** User always knows whether Dynamic Radio or override is active
- **Instant:** Override takes effect immediately
- **Graceful resume:** When override ends, Dynamic Radio resumes from current plan context
- **No friction:** Overriding should be easier than switching to Spotify directly

### Override States

```
┌──────────────┐     user plays     ┌──────────────┐
│              │    something via    │              │
│   AUTO-DJ    │───────────────────▶│   OVERRIDE   │
│   (active)   │    Discord/Spotify │   (paused)   │
│              │◀───────────────────│              │
└──────────────┘   override ends    └──────────────┘
                  (timeout/command)
```

### Override Triggers

1. **Discord command:** `/dj play <query>` — searches Tidal and plays the result via mpv
2. **Discord command:** `/dj queue <query>` — adds to mpv queue without stopping Dynamic Radio
3. **Discord command:** `/dj pause` — pauses Dynamic Radio and mpv playback
4. **Discord command:** `/dj mood <description>` — triggers LLM plan adjustment without stopping
5. **Discord command:** `/dj yt <query>` — searches YouTube Music as fallback (for content not on Tidal)

### Resume Behavior

When override ends:
1. Dynamic Radio checks the current time against the DJ plan
2. Finds the appropriate block for the current time
3. Considers what the user was just listening to (key, BPM, energy) as the "previous track" for transition smoothing
4. Selects the next track based on current plan block, smoothing from override context
5. Resumes normal operation

### Override Detection

Since we control mpv directly (not a third-party player), override detection is simpler than the Spotify approach:
- Dynamic Radio tracks which items in the mpv playlist it queued
- `/dj play` clears the Dynamic Radio queue and plays the requested track
- `/dj pause` stops the Dynamic Radio scheduling loop
- `/dj resume` restarts it from the current time context
- Configurable idle timeout: if mpv has been idle for 30 minutes, resume Dynamic Radio

### Discord Commands (via Axi)

| Command | Effect |
|---|---|
| `/dj` or `/dj status` | Show current state: Dynamic Radio active/paused, current block, current track |
| `/dj play <query>` | Override: search Tidal and play immediately |
| `/dj queue <query>` | Add to mpv queue (doesn't pause Dynamic Radio) |
| `/dj yt <query>` | Search YouTube Music and play (for Tidal-missing content) |
| `/dj skip` | Skip current track (stays in Dynamic Radio mode) |
| `/dj pause` | Pause Dynamic Radio (music keeps playing if already on) |
| `/dj resume` | Resume Dynamic Radio from current time context |
| `/dj mood <text>` | Adjust plan: "more energy", "something chill", "focus music" |
| `/dj history` | Show last 10 tracks played |
| `/dj plan` | Show today's DJ plan (time blocks) |
| `/dj like` / `/dj dislike` | Rate current track (affects future selection) |

### Status Display

Dynamic Radio reports its state via Discord channel status emoji:
- 🎵 Dynamic Radio active
- ⏸️ Override/paused
- 🔇 Quiet hours (sleep block)

---

## 10. Open Questions & Tradeoffs

### Must Decide Before Implementation

1. **Tidal quality tier: LOSSLESS vs HI_RES_LOSSLESS**
   - LOSSLESS (16-bit/44.1kHz FLAC) works with OAuth device flow (headless-friendly)
   - HI_RES_LOSSLESS (24-bit/192kHz FLAC) requires PKCE auth (browser-based initial login)
   - **Recommendation:** Start with LOSSLESS via OAuth. Upgrade to HI_RES later if desired — the one-time PKCE login can be done via a local web server.

2. **mpv gapless strategy**
   - Option A: Pre-resolve next track's stream URL during current playback, `loadfile append` to mpv
   - Option B: Keep a rolling buffer of 2-3 pre-resolved tracks
   - Stream URLs expire in ~hours, so don't pre-resolve too far ahead
   - **Recommendation:** Option A. Pre-resolve 1 track ahead. Resolve the next while the current plays.

3. **BPM/key coverage gaps**
   - Not all Tidal tracks have BPM/key populated
   - For tracks missing this data: skip them in harmonic mixing, or run Essentia analysis
   - **Recommendation:** Accept missing data gracefully. Use BPM/key when available, fall back to genre/mood matching when not. Add Essentia analysis as a V2 enhancement.

4. **YouTube Music as fallback: when to trigger?**
   - On explicit user request (`/dj yt <query>`)
   - When Tidal search returns no results
   - When Tidal API is down (credential rotation)
   - **Recommendation:** All three. Make the fallback chain automatic and transparent.

5. **Sleep hours behavior**
   - Option A: Silence (pause mpv)
   - Option B: Very quiet drone/ambient
   - Option C: Configurable per-user
   - **Recommendation:** Option C, defaulting to silence.

6. **Rate limit handling**
   - tidalapi raises `TooManyRequests` with `retry_after` value
   - Need to implement backoff in the Dynamic Radio service layer
   - **Recommendation:** Exponential backoff with jitter. For track selection, keep a pre-fetched candidate pool so a rate limit doesn't interrupt playback.

### Technical Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Tidal rotates API credentials | tidalapi stops working until updated | Pin version, monitor releases, auto-notify on failure |
| Tidal rate-limits aggressively | Can't fetch tracks/radio | Pre-fetch candidate pools; exponential backoff |
| tidalapi breaks with Tidal API change | No metadata/streams | YouTube Music fallback; library update typically within days |
| BPM/key data missing on many tracks | Weaker harmonic mixing | Graceful degradation to genre/mood matching |
| mpv crashes after days of operation | Playback stops | Watchdog process restarts mpv; systemd auto-restart |
| yt-dlp breaks with YouTube update | YTM fallback unavailable | yt-dlp community fixes fast (150K stars); pin working version |
| Claude Code outage | No daily plan | Cache previous plans; rule-based fallback profile |
| Stream URL expiry mid-playback | Track cuts off | mpv handles reconnection; URLs valid for hours |

### Future Enhancements (Not in V1)

- **BPM-matched transitions** — crossfade in mpv with tempo-aligned timing + key-compatible selection
- **Essentia audio analysis** — compute energy/danceability/mood from cached audio for richer selection
- **Calendar integration** — pull calendar events to automatically adjust plan
- **Weather integration** — fetch weather to influence mood parameters
- **Learning from feedback** — track likes/dislikes/skips to refine user profile over time
- **Stem separation** — use Tidal's `stem_ready` tracks for creative mixing (isolate bass, drums, etc.)
- **Multiple listeners** — guest mode blending preferences
- **Local file integration** — play own productions and Bandcamp purchases via MPD
- **Visualization** — web dashboard showing current plan, energy curve, track history

---

## Appendix A: tidalapi Methods Used

| Method | Purpose | Frequency |
|---|---|---|
| `session.login_oauth_simple()` | Initial auth + session persistence | Once |
| `track.get_url()` | Get stream URL for playback | Every ~3-5min |
| `track.get_track_radio(limit)` | Discover similar tracks from seed | Every ~15-30min |
| `session.search(query)` | User search (override) | On demand |
| `session.mixes()` | Get daily/discovery mixes | Daily |
| `session.moods()` / `session.genres()` | Browse mood/genre playlists | Periodic |
| `session.home()` / `session.for_you()` | Personalized recommendations | Daily |
| `track.bpm` / `track.key` | Get BPM and key for mixing | Per track (cached) |
| `session.user.favorites` | Get user's saved tracks | On startup + periodic |

## Appendix B: Estimated Costs

| Item | Monthly Cost |
|---|---|
| Tidal subscription | Already paying (existing sub) |
| YouTube Music subscription | Already paying (existing sub) |
| Claude LLM (via Axi/Claude Code) | $0.00 (covered by existing subscription) |
| Infrastructure (runs on existing machine) | $0.00 |
| **Total new cost** | **$0.00/month** |

## Appendix C: Key Dependencies

| Package | Purpose | Version | Maintenance |
|---|---|---|---|
| `tidalapi` | Tidal API client | 0.8.11 | Active (monthly releases) |
| `ytmusicapi` | YouTube Music API client | 1.11.5 | Active (monthly releases) |
| `yt-dlp` | YouTube audio extraction (fallback) | 2026.03.03 | Very active (150K stars) |
| `mpv` | Audio playback via IPC | System package | Stable, packaged in Arch |
| `claude_agent_sdk` | LLM for daily plans (Claude Code login) | Latest | Official Anthropic SDK |
