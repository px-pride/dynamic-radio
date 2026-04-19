# Agentic Track Selection

Refill the daemon's track queue when it runs low. The daemon triggers this automatically when the queue runs dry.

## When to Run

Check `dj_status()`. If `needs_tracks` is true (< 60 min of queued music), run this flow.

## Flow

1. **Get context in parallel:**
   - `dj_status()` — current block (genres, energy, BPM range, mood), queue depth
   - `dj_feedback(hours=24)` — recent plays, likes, dislikes, skip patterns
   - Read `~/app-user-data/axi-assistant/profile/refs/music-preferences.md` — genre weights, reference artists, anti-preferences

2. **Build search queries** from the current plan block + music preferences:
   - Use the block's genres as primary queries
   - Add reference artists that match the block's mood/energy
   - Vary queries across refill cycles — don't repeat the same searches
   - Weight toward high-affinity genres from music-preferences.md

3. **Search Tidal** via `dj_search(query, limit=20)`:
   - Run 2-4 searches with different queries for variety
   - Collect all results into a candidate pool

4. **Select a batch** (~50 tracks) from the candidates:
   - Match the block's energy level and BPM range
   - Respect anti-preferences (avoid disliked tracks, specific avoids from prefs)
   - Mix familiar (liked, previously played well) with discovery (~2/3 new per prefs)
   - Avoid tracks from recent play history (dj_feedback shows recent plays)
   - Avoid repeating the same artist within the batch
   - Consider key compatibility between adjacent tracks (Camelot wheel)
   - Order the batch for smooth transitions (BPM progression, energy flow)

5. **Push the batch** via `dj_queue_tracks(tracks)` with tidal_id, name, artist for each track.

## Guidelines

- **Batch size:** ~50 tracks to cover several hours between refills.
- **Don't over-queue:** If queue has >30 min of music, push fewer tracks. If empty, push the full ~50.
- **Skip patterns matter:** If feedback shows tracks from a genre getting skipped early (<30s play duration), reduce that genre.
- **Liked tracks:** Can be replayed but not within the same 24h window (the daemon filters these).
- **Discovery balance:** ~2/3 new tracks, ~1/3 familiar (per music-preferences.md). "New" means not in recent play history.
- **Deep web search:** Use web search to find new artists/tracks that match the mood, then look them up on Tidal via `dj_search`. This is mandatory — it adds variety beyond Tidal's search algorithm and prevents repetitive selections.
