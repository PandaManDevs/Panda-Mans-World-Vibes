import asyncio
import logging
import os
import re
import shutil
from typing import Optional
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

import discord
from discord.ext import commands
import yt_dlp

# ------------------------------------------------------------
# Panda Man's World Vibes - Discord Music Bot
# Render-friendly version
#
# IMPORTANT:
# - Does NOT use Chrome cookies.
# - Does NOT require the Spotify Web API for Spotify links.
# - Spotify links are resolved to public metadata and then searched
#   on SoundCloud only.
# - Normal text searches use SoundCloud only.
# - YouTube is intentionally not used.
# - Designed to run as a Render Background Worker.
# ------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("PandaMusicBot")

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
SOUNDCLOUD_CLIENT_ID = os.getenv("SOUNDCLOUD_CLIENT_ID")

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents,
    help_command=None,
)

# No cookiesfrombrowser / Chrome profile is used here.
YTDL_OPTIONS = {
    # IMPORTANT: Do NOT restrict SoundCloud to webm.
    # SoundCloud commonly returns AAC/Opus/MP3/HLS formats, and
    # "bestaudio[ext=webm]" causes "No video formats found".
    "format": "bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "no_warnings": False,
    "socket_timeout": 30,
    "retries": 5,
    "fragment_retries": 5,
    "extractor_retries": 5,
    "http_headers": {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/139.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    },
}

# If Render has a SoundCloud client ID, let yt-dlp use it.
if SOUNDCLOUD_CLIENT_ID:
    YTDL_OPTIONS["extractor_args"] = {
        "soundcloud": {
            "client_id": [SOUNDCLOUD_CLIENT_ID],
        }
    }
    logger.info("SoundCloud client ID loaded.")
else:
    logger.info(
        "SOUNDCLOUD_CLIENT_ID is not set; using yt-dlp's normal "
        "SoundCloud extraction."
    )

ytdl = yt_dlp.YoutubeDL(YTDL_OPTIONS)

# Audio streaming options. FFmpeg is installed by build.sh.
FFMPEG_EXECUTABLE = shutil.which("ffmpeg") or "ffmpeg"

# These options make FFmpeg handle expiring HTTP/HLS audio URLs much more
# reliably on Render. The protocol whitelist is important for SoundCloud
# HLS streams, which can reference multiple protocols internally.
FFMPEG_BEFORE_OPTIONS = (
    "-reconnect 1 "
    "-reconnect_streamed 1 "
    "-reconnect_at_eof 1 "
    "-reconnect_on_network_error 1 "
    "-reconnect_on_http_error 4xx,5xx "
    "-reconnect_delay_max 5 "
    "-protocol_whitelist file,http,https,tcp,tls,crypto"
)
FFMPEG_OPTIONS = "-vn -sn -dn -loglevel warning"


def is_url(value: str) -> bool:
    return value.startswith(("http://", "https://"))


def is_spotify_url(value: str) -> bool:
    return "open.spotify.com/" in value.lower() or "spotify.link/" in value.lower()


def is_soundcloud_url(value: str) -> bool:
    return "soundcloud.com/" in value.lower()


def clean_spotify_title(title: str) -> str:
    """Turn Spotify's public oEmbed title into a useful search string."""
    title = re.sub(r"\s+", " ", title or "").strip()
    title = re.sub(r"\s*\|\s*Spotify\s*$", "", title, flags=re.I)
    title = re.sub(r"\s*-\s*Spotify\s*$", "", title, flags=re.I)
    return title


def spotify_metadata_sync(url: str) -> Optional[dict]:
    """
    Uses Spotify's public oEmbed endpoint instead of Spotipy.
    This avoids the Spotify API 403 that the old bot was hitting.
    """
    endpoint = (
        "https://open.spotify.com/oembed?url="
        + quote(url, safe="")
    )
    request = Request(
        endpoint,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/139 Safari/537.36"
            )
        },
    )

    with urlopen(request, timeout=15) as response:
        import json

        data = json.loads(response.read().decode("utf-8"))

    title = clean_spotify_title(data.get("title", ""))
    if not title:
        return None

    # Spotify oEmbed normally exposes the track title. Some responses
    # expose author_name as well; use it when available.
    author = (data.get("author_name") or "").strip()
    search_text = f"{title} {author}".strip()

    return {
        "title": title,
        "author": author,
        "search": search_text,
        "thumbnail": data.get("thumbnail_url"),
    }


async def resolve_spotify(url: str) -> dict:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
        lambda: spotify_metadata_sync(url),
    )


def extract_info_sync(query: str) -> dict:
    return ytdl.extract_info(query, download=False)


async def extract_info(query: str) -> dict:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
        lambda: extract_info_sync(query),
    )


async def search_source(search: str) -> dict:
    """Resolve a track using SoundCloud only."""
    search = search.strip()

    if not search:
        raise ValueError("Please provide a song name or SoundCloud URL.")

    # Direct URLs must be SoundCloud URLs. YouTube is intentionally rejected.
    if is_url(search):
        if not is_soundcloud_url(search):
            raise ValueError(
                "Only SoundCloud URLs are supported. YouTube is disabled."
            )

        logger.info("Resolving SoundCloud URL: %s", search)
        data = await extract_info(search)

        if data and data.get("entries"):
            entries = [e for e in data["entries"] if e]
            if entries:
                data = entries[0]

        if not data:
            raise ValueError("SoundCloud did not return a playable result.")

        return data

    # Text search: SoundCloud only.
    sc_query = f"scsearch1:{search}"
    logger.info("Searching SoundCloud: %s", search)

    try:
        data = await extract_info(sc_query)
    except Exception as exc:
        logger.exception("SoundCloud search failed")
        raise ValueError(f"SoundCloud search failed: {exc}") from exc

    if data and data.get("entries"):
        entries = [e for e in data["entries"] if e]
        if entries:
            return entries[0]

    raise ValueError(f"No SoundCloud result found for: {search}")


class Track:
    def __init__(self, data: dict, requested_by: discord.Member):
        self.data = data
        self.requested_by = requested_by

        self.title = data.get("title") or "Unknown title"
        self.webpage_url = data.get("webpage_url") or data.get("original_url") or ""
        self.duration = data.get("duration") or 0
        self.uploader = data.get("uploader") or data.get("channel") or "Unknown"
        self.source_name = self._source_name()

    def _source_name(self) -> str:
        url = self.webpage_url.lower()
        extractor = str(self.data.get("extractor_key") or self.data.get("extractor") or "").lower()

        if "soundcloud" in url or "soundcloud" in extractor:
            return "SoundCloud"
        if "youtube" in url or "youtube" in extractor:
            return "YouTube"
        return "Audio"

    @property
    def duration_text(self) -> str:
        if not self.duration:
            return "Unknown"

        seconds = int(self.duration)
        minutes, seconds = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)

        if hours:
            return f"{hours}:{minutes:02d}:{seconds:02d}"
        return f"{minutes}:{seconds:02d}"


class Music(commands.Cog):
    def __init__(self, bot_: commands.Bot):
        self.bot = bot_
        # One queue per guild.
        self.queues: dict[int, list[Track]] = {}
        self.now_playing: dict[int, Optional[Track]] = {}
        self.volumes: dict[int, float] = {}
        self.next_locks: dict[int, asyncio.Lock] = {}

    def get_queue(self, guild_id: int) -> list[Track]:
        return self.queues.setdefault(guild_id, [])

    def get_lock(self, guild_id: int) -> asyncio.Lock:
        return self.next_locks.setdefault(guild_id, asyncio.Lock())

    async def ensure_voice(self, ctx: commands.Context) -> Optional[discord.VoiceClient]:
        if not ctx.guild:
            await ctx.send("❌ This command can only be used in a server.")
            return None

        if not ctx.author.voice or not ctx.author.voice.channel:
            await ctx.send("❌ Join a voice channel first.")
            return None

        target = ctx.author.voice.channel
        voice = ctx.voice_client

        if voice is None:
            try:
                logger.info(
                    "Connecting to voice: guild=%s channel=%s",
                    ctx.guild.id,
                    target.id,
                )
                voice = await target.connect(
                    timeout=30,
                    reconnect=True,
                )
                return voice
            except Exception as exc:
                logger.exception("Voice connection failed")
                await ctx.send(f"❌ I couldn't join the voice channel: `{exc}`")
                return None

        # Move if the user is in a different channel.
        if voice.channel and voice.channel.id != target.id:
            try:
                await voice.move_to(target)
            except Exception as exc:
                logger.exception("Voice move failed")
                await ctx.send(f"❌ I couldn't move voice channels: `{exc}`")
                return None

        # If Discord dropped the voice connection, reconnect it.
        if not voice.is_connected():
            try:
                await voice.disconnect(force=True)
            except Exception:
                pass

            try:
                voice = await target.connect(
                    timeout=30,
                    reconnect=True,
                )
                return voice
            except Exception as exc:
                logger.exception("Voice reconnect failed")
                await ctx.send(f"❌ Voice reconnect failed: `{exc}`")
                return None

        return voice

    async def make_source(self, track: Track) -> discord.AudioSource:
        # SoundCloud stream URLs are signed/temporary. Always re-extract the
        # page immediately before playback instead of reusing an old stream URL
        # that may have expired while the track was waiting in the queue.
        if not track.webpage_url or not is_soundcloud_url(track.webpage_url):
            raise ValueError("This track is not a valid SoundCloud track.")

        logger.info("Refreshing SoundCloud stream: %s", track.webpage_url)
        data = await extract_info(track.webpage_url)

        if data and data.get("entries"):
            data = next((e for e in data["entries"] if e), data)

        if not data:
            raise ValueError("SoundCloud returned no track information.")

        direct_url = data.get("url")
        if not direct_url:
            raise ValueError(
                "SoundCloud returned no playable audio format for this track."
            )

        # Keep the refreshed metadata for later retries.
        track.data = data
        track.title = data.get("title") or track.title
        track.duration = data.get("duration") or track.duration
        track.uploader = data.get("uploader") or data.get("channel") or track.uploader

        # yt-dlp gives us the headers required for signed SoundCloud streams.
        # Passing them to FFmpeg prevents many HTTP 401/403/EOF failures.
        headers = data.get("http_headers") or {}
        user_agent = headers.get("User-Agent") or YTDL_OPTIONS["http_headers"]["User-Agent"]
        accept_language = headers.get("Accept-Language") or YTDL_OPTIONS["http_headers"]["Accept-Language"]
        referer = headers.get("Referer")

        header_lines = [
            f"User-Agent: {user_agent}",
            f"Accept-Language: {accept_language}",
        ]
        if referer:
            header_lines.append(f"Referer: {referer}")

        ffmpeg_headers = "\r\n".join(header_lines) + "\r\n"
        before_options = (
            FFMPEG_BEFORE_OPTIONS
            + " -headers "
            + '"' + ffmpeg_headers.replace('"', '\\"') + '"'
        )

        logger.info(
            "Starting FFmpeg playback: guild=%s source=SoundCloud format=%s protocol=%s url=%s",
            track.requested_by.guild.id,
            data.get("format_id", "unknown"),
            data.get("protocol", "unknown"),
            direct_url[:180],
        )

        source = discord.FFmpegPCMAudio(
            direct_url,
            executable=FFMPEG_EXECUTABLE,
            before_options=before_options,
            options=FFMPEG_OPTIONS,
        )

        return discord.PCMVolumeTransformer(
            source,
            volume=self.volumes.get(track.requested_by.guild.id, 0.5),
        )

    async def play_next(self, guild: discord.Guild, channel: discord.abc.Messageable):
        guild_id = guild.id
        lock = self.get_lock(guild_id)

        async with lock:
            queue = self.get_queue(guild_id)
            voice = guild.voice_client

            if voice is None or not voice.is_connected():
                self.now_playing[guild_id] = None
                return

            while queue:
                track = queue.pop(0)
                self.now_playing[guild_id] = track

                try:
                    source = await self.make_source(track)

                    def after_playback(error: Optional[Exception]):
                        if error:
                            logger.error(
                                "Playback error in guild %s: %s",
                                guild_id,
                                error,
                            )

                        future = asyncio.run_coroutine_threadsafe(
                            self.play_next(guild, channel),
                            self.bot.loop,
                        )

                        def done_callback(done_future):
                            try:
                                done_future.result()
                            except Exception:
                                logger.exception(
                                    "Error advancing queue in guild %s",
                                    guild_id,
                                )

                        future.add_done_callback(done_callback)

                    voice.play(source, after=after_playback)

                    embed = discord.Embed(
                        title="🎵 Now Playing",
                        description=f"**{track.title}**",
                        color=discord.Color.green(),
                    )
                    embed.add_field(
                        name="Artist / Uploader",
                        value=track.uploader[:1024],
                        inline=True,
                    )
                    embed.add_field(
                        name="Duration",
                        value=track.duration_text,
                        inline=True,
                    )
                    embed.add_field(
                        name="Source",
                        value=track.source_name,
                        inline=True,
                    )
                    embed.set_footer(
                        text=f"Requested by {track.requested_by.display_name}"
                    )

                    await channel.send(embed=embed)
                    return

                except Exception as exc:
                    logger.exception(
                        "Could not play '%s' in guild %s",
                        track.title,
                        guild_id,
                    )
                    try:
                        await channel.send(
                            f"❌ Couldn't play **{track.title}**. Skipping it.\n"
                            f"`{str(exc)[:900]}`"
                        )
                    except Exception:
                        pass

            self.now_playing[guild_id] = None

    @commands.command(name="play", help="Play a SoundCloud song, URL, or Spotify link")
    async def play(self, ctx: commands.Context, *, search: str):
        voice = await self.ensure_voice(ctx)
        if voice is None:
            return

        async with ctx.typing():
            try:
                original = search.strip()

                # Spotify: public oEmbed metadata -> search on SoundCloud only.
                if is_spotify_url(original):
                    logger.info("Resolving Spotify link: %s", original)
                    try:
                        spotify = await resolve_spotify(original)
                    except Exception as exc:
                        logger.warning("Spotify oEmbed failed: %s", exc)
                        await ctx.send(
                            "❌ I couldn't read that Spotify link. "
                            "Try the song name and artist instead."
                        )
                        return

                    if not spotify or not spotify.get("search"):
                        await ctx.send(
                            "❌ Spotify didn't provide enough public information "
                            "to find that track."
                        )
                        return

                    search_text = spotify["search"]
                    data = await search_source(search_text)

                else:
                    data = await search_source(original)

                if data.get("entries"):
                    data = next(
                        (entry for entry in data["entries"] if entry),
                        None,
                    )

                if not data:
                    await ctx.send("❌ I couldn't find that song.")
                    return

                track = Track(data, ctx.author)
                queue = self.get_queue(ctx.guild.id)

                if voice.is_playing() or voice.is_paused() or self.now_playing.get(ctx.guild.id):
                    queue.append(track)

                    embed = discord.Embed(
                        title="⏳ Added to Queue",
                        description=f"**{track.title}**",
                        color=discord.Color.blurple(),
                    )
                    embed.add_field(
                        name="Position",
                        value=f"#{len(queue)}",
                        inline=True,
                    )
                    embed.add_field(
                        name="Source",
                        value=track.source_name,
                        inline=True,
                    )
                    await ctx.send(embed=embed)
                else:
                    # Keep the resolved track in the queue and let the
                    # normal queue runner start it.
                    queue.append(track)
                    await self.play_next(ctx.guild, ctx.channel)

            except Exception as exc:
                logger.exception("Play command error")
                await ctx.send(
                    "❌ **Couldn't play that track.**\n"
                    f"`{str(exc)[:1200]}`"
                )

    @commands.command(name="skip", help="Skip the current song")
    async def skip(self, ctx: commands.Context):
        voice = ctx.voice_client
        if voice and (voice.is_playing() or voice.is_paused()):
            voice.stop()
            await ctx.send("⏭️ Skipped!")
        else:
            await ctx.send("❌ Nothing is playing.")

    @commands.command(name="pause", help="Pause playback")
    async def pause(self, ctx: commands.Context):
        voice = ctx.voice_client
        if voice and voice.is_playing():
            voice.pause()
            await ctx.send("⏸️ Paused!")
        else:
            await ctx.send("❌ Nothing is playing.")

    @commands.command(name="resume", help="Resume playback")
    async def resume(self, ctx: commands.Context):
        voice = ctx.voice_client
        if voice and voice.is_paused():
            voice.resume()
            await ctx.send("▶️ Resumed!")
        else:
            await ctx.send("❌ Nothing is paused.")

    @commands.command(name="stop", help="Stop music and clear the queue")
    async def stop(self, ctx: commands.Context):
        guild_id = ctx.guild.id
        self.get_queue(guild_id).clear()
        self.now_playing[guild_id] = None

        if ctx.voice_client:
            ctx.voice_client.stop()

        await ctx.send("⏹️ Stopped and cleared the queue.")

    @commands.command(name="queue", help="Show the music queue")
    async def queue_cmd(self, ctx: commands.Context):
        guild_id = ctx.guild.id
        queue = self.get_queue(guild_id)
        current = self.now_playing.get(guild_id)

        if not current and not queue:
            await ctx.send("❌ The queue is empty.")
            return

        embed = discord.Embed(
            title="🎵 Music Queue",
            color=discord.Color.purple(),
        )

        if current:
            embed.add_field(
                name="▶️ Now Playing",
                value=f"**{current.title}**\n{current.source_name}",
                inline=False,
            )

        if queue:
            lines = []
            for index, track in enumerate(queue[:10], 1):
                lines.append(f"**{index}.** {track.title}")

            embed.add_field(
                name=f"⏳ Up Next ({len(queue)})",
                value="\n".join(lines),
                inline=False,
            )

            if len(queue) > 10:
                embed.set_footer(
                    text=f"+ {len(queue) - 10} more song(s)"
                )

        await ctx.send(embed=embed)

    @commands.command(name="volume", help="Set volume from 0 to 100")
    async def volume(self, ctx: commands.Context, vol: int):
        if not 0 <= vol <= 100:
            await ctx.send("❌ Volume must be between 0 and 100.")
            return

        self.volumes[ctx.guild.id] = vol / 100

        if ctx.voice_client and isinstance(
            ctx.voice_client.source,
            discord.PCMVolumeTransformer,
        ):
            ctx.voice_client.source.volume = vol / 100

        await ctx.send(f"🔊 Volume set to **{vol}%**.")

    @commands.command(name="leave", help="Leave the voice channel")
    async def leave(self, ctx: commands.Context):
        guild_id = ctx.guild.id
        self.get_queue(guild_id).clear()
        self.now_playing[guild_id] = None

        if ctx.voice_client:
            try:
                await ctx.voice_client.disconnect(force=True)
            except Exception:
                logger.exception("Voice disconnect failed")

            await ctx.send("👋 Left the voice channel.")
        else:
            await ctx.send("❌ I'm not in a voice channel.")

    @commands.command(name="help", help="Show music commands")
    async def help_cmd(self, ctx: commands.Context):
        embed = discord.Embed(
            title="🐼 Panda Man's World Vibes",
            description="Music commands",
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="🎵 Playback",
            value=(
                "`!play <song / URL>`\n"
                "`!skip`\n"
                "`!pause`\n"
                "`!resume`\n"
                "`!stop`"
            ),
            inline=False,
        )
        embed.add_field(
            name="📋 Queue",
            value="`!queue`",
            inline=True,
        )
        embed.add_field(
            name="🔊 Audio",
            value="`!volume <0-100>`",
            inline=True,
        )
        embed.add_field(
            name="🚪 Voice",
            value="`!leave`",
            inline=True,
        )
        await ctx.send(embed=embed)


@bot.event
async def on_ready():
    logger.info("========================================")
    logger.info("🐼 %s connected to Discord!", bot.user)
    logger.info("discord.py version: %s", discord.__version__)
    logger.info("========================================")
    logger.info("Commands:")
    logger.info("!play [song/URL] - YouTube/SoundCloud/Spotify")
    logger.info("!skip")
    logger.info("!pause")
    logger.info("!resume")
    logger.info("!stop")
    logger.info("!queue")
    logger.info("!volume [0-100]")
    logger.info("!leave")
    logger.info("!help")
    logger.info("========================================")


@bot.event
async def on_voice_state_update(
    member: discord.Member,
    before: discord.VoiceState,
    after: discord.VoiceState,
):
    # Helpful diagnostics for Render/Discord voice issues.
    if member.id == bot.user.id:
        if before.channel and not after.channel:
            logger.warning(
                "Bot left voice: guild=%s old_channel=%s",
                member.guild.id,
                before.channel.id,
            )
        elif after.channel and before.channel != after.channel:
            logger.info(
                "Bot voice channel: guild=%s channel=%s",
                member.guild.id,
                after.channel.id,
            )


async def main():
    if not DISCORD_TOKEN:
        raise RuntimeError(
            "DISCORD_TOKEN environment variable is not set."
        )

    ffmpeg_path = shutil.which("ffmpeg")
    if not ffmpeg_path:
        raise RuntimeError(
            "FFmpeg is not installed or is not on PATH. "
            "Make sure Render runs build.sh before starting the bot."
        )
    logger.info("FFmpeg found at: %s", ffmpeg_path)

    # add_cog is awaited in modern discord.py.
    async with bot:
        await bot.add_cog(Music(bot))
        await bot.start(DISCORD_TOKEN, reconnect=True)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped.")
