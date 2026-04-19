# Dynamic Radio — Task Records

## Completed (2026-04-11)

All committed in `ac86283` and pushed to master.

- [x] Plan: two-tier queue (selector buffer + visible agentic queue)
- [x] Implement two-tier queue in daemon.py
- [x] Fix: asyncio event loop blocking — wrapped Tidal/MB API calls in asyncio.to_thread()
- [x] Fix: MCP server timeout 10s → 120s
- [x] Fix: SQLite check_same_thread=False for thread safety
- [x] Diagnose song gaps (_get_stream_url blocking event loop)
- [x] Fix gaps: wrap _get_stream_url() in asyncio.to_thread()
- [x] Fix: refill trigger fires when queue packed (add depth check + duration enrichment)
- [x] Test: refill agent end-to-end — 11/11 dj_search calls succeeded
- [x] Commit and push all changes
- [x] Delete stale SYSTEM_PROMPT.md
- [x] Fix refill agent missing MCP tools (mcp_servers field in trigger payload)

## Completed (2026-04-11, session 2)

- [x] Disable scheduled daily plan generation (`dynamic-radio-daily-plan` — was already disabled)
- [x] Disable agentic refill trigger in daemon.py:119-121 (commented out `_trigger_refill_agent()` call in `_tick()`)
- Daemon now defaults to non-agentic selection: queue → selector buffer → `_select_from_tidal()` (MusicBrainz + Tidal)

## Completed (2026-04-12)

- [x] Commit and push the agentic refill disable change (commit `c6397a4`)
- [x] Restart daemon on 127.0.0.1 to pick up the change
- [x] Diagnose dead Icecast stream (SEGV crash loop since 06:00 Apr 11, stale systemd PID)
- [x] Restart Icecast + daemon services — stream restored, FLAC + MP3 pipelines running

## Completed (2026-04-12, continued)

- [x] Confirm stream is audible — both FLAC and MP3 endpoints working

## Completed (2026-04-12, analysis)

- [x] Investigate: FLAC on iOS Safari — iOS CAN decode FLAC (since iOS 13), but Icecast infinite streams only work for MP3 (ICY protocol has special browser support). Lossless on iOS would require HLS architecture change. Tidal plays FLAC via native AVFoundation, not HTML5 audio.
- [x] Implement: side-by-side layout for Up Next and Recently Played columns (CSS flex container, responsive stacking <500px)
- [x] Implement: MediaSession API in poll() — sets title/artist/album so car displays and lock screens show track info

## Completed (2026-04-12, Discord voice integration)

- [x] Add `stream_url` field to `/status` API response (api.py:164-167) — returns Icecast MP3 mount URL when streamer is running
- [x] `stream_url` also exposed in MCP `dj_status` (proxied from /status automatically), updated tool description
- [x] Verified `--stream` is already default-on in systemd service (`ExecStart=... --stream`), no change needed

## Completed (2026-04-12, deploy)

- [x] Commit and push all changes (commit `ca5ca38`)
- [x] Restart daemon — `stream_url` confirmed in dj_status response
- [x] Deploy now-playing.html (served from repo via handle_now_playing, no separate copy needed)

## Completed (2026-04-12, testing)

- [x] Test: car display metadata via MediaSession API — confirmed working
- [x] Auto-populate queue when agentic refill is off — batch_refill(5) runs when buffered tracks < 3, all selector buffer tracks feed into visible queue (commit `17c0821`)
- [x] Daemon restarted — confirmed 5 tracks selected, 4 showing in "Up Next", playing Rafael Anton Irisarri

## Completed (2026-04-12, cleanup)

- [x] Remove redundant batch_refill(15) from mood change handler — tick loop handles refill automatically

## Pending — user verification

- [ ] Test: reload now-playing page to verify side-by-side layout with Up Next populated
- [ ] Commit and push mood handler cleanup + queue auto-populate changes

## Completed (2026-04-12, search quality)

- [x] Fix: MB search not verifying genre tags on results — search_recordings(tag="drone") returns recordings ranked by relevance but code never checked if "drone" actually appeared in the recording's tag-list. Added post-filter: skip recordings where the searched tag isn't in their verified tag-list (count > 0). Also skip recordings with no tags at all.

## Completed (2026-04-12, deploy)

- [x] Commit and push all pending changes (commit `5304195`) — MB tag verification, mood handler cleanup, queue auto-populate
- [x] Restart daemon — verified tag filter working: queue now shows Arovane, Clark (legit ambient/drone), no more mismatched results

## Completed (2026-04-12, search quality continued)

- [x] Remove Tidal keyword fallback entirely — deleted `_search_via_tidal` method
- [x] Replace with MB retry: when first MB pass yields < 5 candidates, retry with limit=100 (up from 50) and different offset, passing seen_ids to avoid duplicates
- [x] `_search_via_musicbrainz` now accepts `limit` and `seen_ids` params for retry path

## Completed (2026-04-12, stream fix #2)

- [x] Diagnose stream drop + Axi VC silence — same root cause: Icecast crashed silently, systemd stale PID (PID 1218784 dead but reported active). All ffmpeg pipelines dead.
- [x] Restart Icecast + daemon — stream restored, ffmpeg pipelines running

## Completed (2026-04-12, agentic refill)

- [x] Re-enable agentic refill trigger in tick loop — auto-populate kept as fallback for agent startup delay

## Completed (2026-04-14, deploy)

- [x] Commit and push all pending changes (commit `9a97a6b`) — Tidal fallback removal, MB retry, agentic refill re-enable, mood handler cleanup
- [x] Restart daemon — verified running, resumed playback

## Pending (future work)

- [ ] Add streamer watchdog — restart ffmpeg pipelines if they die (no monitoring exists today)
- [ ] Preference learning: preference weights, skip signal analysis, mood pattern detection, testing (4 MinFlow cards remain open)
