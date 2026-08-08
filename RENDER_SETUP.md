# Panda Man's World Vibes — Render Setup

## 1. Service type

Use a **Background Worker**, not a Web Service.

## 2. Build Command

```text
bash build.sh
```

## 3. Start Command

```text
python bot.py
```

## 4. Environment Variables

Add:

```text
DISCORD_TOKEN=YOUR_DISCORD_BOT_TOKEN
```

Recommended:

```text
SOUNDCLOUD_CLIENT_ID=YOUR_SOUNDCLOUD_CLIENT_ID
```

Do not add YouTube cookies, YouTube PO tokens, or BgUtils. This version is SoundCloud-only.

## 5. Discord permissions

The bot needs permission to:

- View Channels
- Connect
- Speak
- Send Messages
- Embed Links

Also make sure **Message Content Intent** is enabled for the bot in the Discord Developer Portal because this bot uses `!` prefix commands.

## 6. Testing

After deployment, join a voice channel and run:

```text
!play never gonna give you up
```

or:

```text
!play https://soundcloud.com/example/track
```

The Render log should show:

```text
SoundCloud search: ...
Refreshing SoundCloud stream for: ...
SoundCloud stream selected for: ...
voice.play() called.
Playing: ...
```

If FFmpeg exits immediately, the bot will report the playback error in Discord and the Render log.
