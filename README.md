# Panda Man's World Vibes

SoundCloud-only Discord music bot for Render.

## Commands

- `!play <song>` — searches SoundCloud
- `!play <SoundCloud URL>` — plays a direct SoundCloud track
- `!skip`
- `!pause`
- `!resume`
- `!stop`
- `!queue`
- `!volume <0-100>`
- `!leave`
- `!help`

## Render

Use a **Background Worker**.

### Build Command

```text
bash build.sh
```

### Start Command

```text
python bot.py
```

### Environment Variables

Required:

```text
DISCORD_TOKEN=your_bot_token
```

Optional but recommended for SoundCloud extraction:

```text
SOUNDCLOUD_CLIENT_ID=your_soundcloud_client_id
```

YouTube is intentionally not used by this bot. YouTube cookies, PO tokens, and BgUtils are not required.

## Audio

The bot:

1. Searches SoundCloud only.
2. Refreshes the SoundCloud stream immediately before playback so signed URLs are fresh.
3. Prefers SoundCloud HTTP audio formats before HLS.
4. Passes SoundCloud's extractor HTTP headers to FFmpeg when available.
5. Uses FFmpeg reconnect options for transient stream/network failures.

Make sure FFmpeg is available in the Render runtime. The included `build.sh` checks for it.
