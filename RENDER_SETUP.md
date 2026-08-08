# Panda Man's World Vibes — Render Fixed

## Render service
Create/use a **Background Worker**, not a Web Service.

### Build Command
`bash build.sh`

### Start Command
`python discord_music_bot.py`

### Environment variable
`DISCORD_TOKEN=YOUR_DISCORD_BOT_TOKEN`

## Important
This version does not run `apt-get` during the Render build. It also pins Python to 3.11.11.

If FFmpeg is not available in your selected Render runtime, the bot must use a runtime/container image that provides FFmpeg.
