# Panda Man's World Vibes - Fixed Discord Music Bot

## Render setup

Create a **Background Worker** on Render (not a Web Service).

Build command:
```text
bash build.sh
```

Start command:
```text
python discord_music_bot.py
```

Required environment variable:
```text
DISCORD_TOKEN=your_discord_bot_token
```

Optional:
```text
SOUNDCLOUD_CLIENT_ID=your_soundcloud_client_id
```

## What was fixed

- Upgraded discord.py from 2.3.2 to 2.7.1.
- Added Discord voice reconnect handling.
- Removed Chrome cookie extraction completely.
- Added FFmpeg installation during Render build.
- Removed the Spotify Web API dependency that caused the 403 Premium error.
- Spotify links now use Spotify's public oEmbed metadata and search for the track on SoundCloud/YouTube.
- Added SoundCloud-first text search with YouTube fallback.
- Added per-server queues.
- Fixed queue progression and callback handling.
- Added safer voice reconnect/move behavior.
- Added `!help`.
- Configured Render as a Background Worker so it does not require an HTTP port.

## Commands

`!play <song / URL>`
`!skip`
`!pause`
`!resume`
`!stop`
`!queue`
`!volume <0-100>`
`!leave`
`!help`

## Audio playback fix

This version explicitly locates FFmpeg and uses it to decode remote HTTP/HLS audio streams. It also enables reconnect/protocol options needed by SoundCloud HLS streams. Render must run `bash build.sh` before `python discord_music_bot.py`.

Use a **Background Worker** on Render. Do not use a Web Service unless you add an HTTP health server.
