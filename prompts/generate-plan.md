# Dynamic Radio Daily Plan Generation

Generate today's DJ schedule and push it to the daemon via `dj_upload_plan`.

## Plan JSON Schema

```json
{
  "date": "YYYY-MM-DD",
  "generated_at": "ISO 8601 datetime",
  "blocks": [
    {
      "start": "HH:MM",
      "end": "HH:MM",
      "mood": "vibe description",
      "energy": 0.0-1.0,
      "genres": ["genre1", "genre2"],
      "bpm_range": [low, high],
      "description": "block description"
    }
  ]
}
```

Blocks must cover 00:00–23:59 with no gaps. Each block's `end` = next block's `start`.

## User Profile

Read `~/app-user-data/axi-assistant/profile/refs/music-preferences.md` for genre affinities, energy curve, mood-to-genre mappings, reference artists, and anti-preferences. Use those values to populate plan blocks.

## Variety

Don't repeat the same plan daily. Vary genres, moods, and BPM ranges within guidelines.
Weekends: more creative/exploratory. Weekdays: more focused structure.

## How to Push

Use the `dj_upload_plan` MCP tool with the full plan JSON. The daemon saves it locally and starts using it within 5 seconds.

Output a brief summary after pushing.
