import asyncio
import logging
import os
import shutil
import subprocess
from typing import Optional

import discord
from discord.ext import commands
import yt_dlp


# ============================================================
# PANDA MAN'S WORLD VIBES
# SOUNDCLOUD-ONLY DISCORD MUSIC BOT
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
)

logger = logging.getLogger("PandaMusicBot")


# ============================================================
# ENVIRONMENT
# ============================================================

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
SOUNDCLOUD_CLIENT_ID = os.getenv("SOUNDCLOUD_CLIENT_ID")


if not DISCORD_TOKEN:
    raise RuntimeError(
        "DISCORD_TOKEN is not set in Render Environment Variables."
    )


# ============================================================
# DISCORD
# ============================================================

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents,
    help_command=None
)


# ============================================================
# FFMPEG
# ============================================================

FFMPEG = shutil.which("ffmpeg") or "ffmpeg"

logger.info(
    "FFmpeg found at: %s",
    FFMPEG
)


# ============================================================
# SOUNDCLOUD YT-DLP SETTINGS
# ============================================================

YTDL_OPTIONS = {
    # SoundCloud only.
    #
    # Prefer normal HTTP streams first.
    # HLS is only a fallback.
    "format": (
        "http_aac/"
        "http_opus/"
        "http_mp3/"
        "hls_aac/"
        "hls_opus/"
        "hls_mp3/"
        "bestaudio"
    ),

    "noplaylist": True,

    "quiet": True,
    "no_warnings": False,

    "socket_timeout": 30,

    "retries": 5,
    "fragment_retries": 5,
    "extractor_retries": 5,

    "extractor_args": {
        "soundcloud": {
            "formats": (
                "http_aac,"
                "http_opus,"
                "http_mp3,"
                "hls_aac,"
                "hls_opus,"
                "hls_mp3"
            )
        }
    },

    "http_headers": {
        "User-Agent": (
            "Mozilla/5.0 "
            "(Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/139.0.0.0 "
            "Safari/537.36"
        ),

        "Accept-Language": "en-US,en;q=0.9",

    },

    "source_address": "0.0.0.0",
}


# ============================================================
# SOUNDCLOUD CLIENT ID
# ============================================================

if SOUNDCLOUD_CLIENT_ID:

    YTDL_OPTIONS["extractor_args"]["soundcloud"][
        "client_id"
    ] = SOUNDCLOUD_CLIENT_ID

    logger.info(
        "SOUNDCLOUD_CLIENT_ID loaded."
    )

else:

    logger.warning(
        "SOUNDCLOUD_CLIENT_ID is not set."
    )

    logger.warning(
        "yt-dlp will attempt normal SoundCloud extraction."
    )


ytdl = yt_dlp.YoutubeDL(
    YTDL_OPTIONS
)


# ============================================================
# FFMPEG SETTINGS
# ============================================================

FFMPEG_OPTIONS = (
    "-vn "
    "-sn "
    "-dn "
    "-loglevel warning"
)


def build_ffmpeg_before_options(http_headers=None):
    """
    Build FFmpeg input options for SoundCloud's signed stream URL.

    SoundCloud can return short-lived signed URLs. Reusing the extractor's
    headers helps FFmpeg access the stream instead of immediately receiving
    an HTTP error/EOF.
    """
    options = [
        "-reconnect", "1",
        "-reconnect_streamed", "1",
        "-reconnect_on_network_error", "1",
        "-reconnect_on_http_error", "4xx,5xx",
        "-reconnect_delay_max", "5",
        "-rw_timeout", "15000000",
        "-protocol_whitelist", "file,http,https,tcp,tls,crypto",
    ]

    headers = dict(http_headers or {})

    # FFmpeg's -headers expects CRLF-separated HTTP headers.
    # Don't duplicate User-Agent here because we set it explicitly below.
    header_lines = []
    for key, value in headers.items():
        if key.lower() == "user-agent":
            continue
        if value is None:
            continue
        header_lines.append(f"{key}: {value}")

    if header_lines:
        options.extend([
            "-headers",
            "\r\n".join(header_lines) + "\r\n"
        ])

    options.extend([
        "-user_agent",
        (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/139.0.0.0 Safari/537.36"
        ),
    ])

    return " ".join(
        f'"{x}"' if " " in x or "\r" in x or "\n" in x else x
        for x in options
    )



FFMPEG_BEFORE_OPTIONS = build_ffmpeg_before_options()


# ============================================================
# TRACK
# ============================================================

class Track:

    def __init__(
        self,
        data: dict,
        requested_by: discord.Member
    ):

        self.data = data

        self.requested_by = requested_by

        self.title = (
            data.get("title")
            or "Unknown Track"
        )

        self.url = (
            data.get("webpage_url")
            or data.get("original_url")
            or ""
        )

        self.stream_url = (
            data.get("url")
            or ""
        )

        self.http_headers = (
            data.get("http_headers")
            or {}
        )

        self.duration = (
            data.get("duration")
            or 0
        )

        self.uploader = (
            data.get("uploader")
            or data.get("artist")
            or "Unknown Artist"
        )

    @property
    def duration_text(self):

        if not self.duration:
            return "Unknown"

        seconds = int(
            self.duration
        )

        minutes, seconds = divmod(
            seconds,
            60
        )

        hours, minutes = divmod(
            minutes,
            60
        )

        if hours:

            return (
                f"{hours}:"
                f"{minutes:02d}:"
                f"{seconds:02d}"
            )

        return (
            f"{minutes}:"
            f"{seconds:02d}"
        )


# ============================================================
# MUSIC COG
# ============================================================

class Music(commands.Cog):

    def __init__(self, bot):

        self.bot = bot

        self.queues = {}

        self.now_playing = {}

        self.volumes = {}

    # ========================================================
    # QUEUE
    # ========================================================

    def get_queue(self, guild_id):

        if guild_id not in self.queues:

            self.queues[guild_id] = []

        return self.queues[guild_id]

    # ========================================================
    # VOICE
    # ========================================================

    async def ensure_voice(self, ctx):

        if not ctx.guild:

            await ctx.send(
                "❌ This command can only be used in a server."
            )

            return None

        if not ctx.author.voice:

            await ctx.send(
                "❌ Join a voice channel first."
            )

            return None

        channel = ctx.author.voice.channel

        voice = ctx.voice_client

        # ----------------------------------------------------
        # CONNECT
        # ----------------------------------------------------

        if voice is None:

            try:

                logger.info(
                    "Connecting to voice: "
                    "guild=%s channel=%s",
                    ctx.guild.id,
                    channel.id
                )

                voice = await channel.connect(
                    timeout=30,
                    reconnect=True
                )

                logger.info(
                    "Voice connection established."
                )

                return voice

            except Exception as e:

                logger.exception(
                    "Voice connection failed."
                )

                await ctx.send(
                    "❌ I couldn't join the voice channel:\n"
                    f"`{str(e)[:1000]}`"
                )

                return None

        # ----------------------------------------------------
        # MOVE
        # ----------------------------------------------------

        if voice.channel != channel:

            try:

                await voice.move_to(
                    channel
                )

            except Exception as e:

                logger.exception(
                    "Failed moving voice channel."
                )

                await ctx.send(
                    "❌ I couldn't move to your voice channel."
                )

                return None

        return voice

    # ========================================================
    # SOUNDCLOUD SEARCH
    # ========================================================

    async def search_soundcloud(
        self,
        search: str
    ):

        loop = asyncio.get_running_loop()

        logger.info(
            "SoundCloud search: %s",
            search
        )

        def extract():

            return ytdl.extract_info(
                f"scsearch1:{search}",
                download=False
            )

        data = await loop.run_in_executor(
            None,
            extract
        )

        if not data:

            raise RuntimeError(
                "SoundCloud returned no results."
            )

        entries = data.get(
            "entries",
            []
        )

        entries = [
            entry
            for entry in entries
            if entry
        ]

        if not entries:

            raise RuntimeError(
                "No SoundCloud tracks were found."
            )

        return entries[0]

    # ========================================================
    # SOUNDCLOUD URL
    # ========================================================

    async def get_soundcloud_url(
        self,
        url: str
    ):

        loop = asyncio.get_running_loop()

        logger.info(
            "SoundCloud URL: %s",
            url
        )

        def extract():

            return ytdl.extract_info(
                url,
                download=False
            )

        data = await loop.run_in_executor(
            None,
            extract
        )

        if not data:

            raise RuntimeError(
                "SoundCloud did not return the track."
            )

        if data.get("entries"):

            entries = [
                entry
                for entry in data["entries"]
                if entry
            ]

            if entries:

                data = entries[0]

        return data

    # ========================================================
    # EXTRACT TRACK
    # ========================================================

    async def get_track(
        self,
        query: str,
        requested_by
    ):

        query = query.strip()

        # ----------------------------------------------------
        # DIRECT SOUNDCLOUD URL
        # ----------------------------------------------------

        if (
            query.startswith(
                "https://soundcloud.com/"
            )
            or
            query.startswith(
                "http://soundcloud.com/"
            )
            or
            query.startswith(
                "https://m.soundcloud.com/"
            )
        ):

            data = await self.get_soundcloud_url(
                query
            )

        else:

            # ------------------------------------------------
            # SEARCH SOUNDCLOUD
            # ------------------------------------------------

            data = await self.search_soundcloud(
                query
            )

        return Track(
            data,
            requested_by
        )

    # ========================================================
    # CREATE AUDIO SOURCE
    # ========================================================

    async def create_source(
        self,
        track: Track
    ):

        # SoundCloud stream URLs are signed and can expire.
        # Always refresh the track immediately before playback.
        logger.info(
            "Refreshing SoundCloud stream for: %s",
            track.title
        )

        data = await self.get_soundcloud_url(
            track.url
        )

        stream_url = (
            data.get("url")
            or ""
        )

        if not stream_url:
            raise RuntimeError(
                "SoundCloud did not provide a playable audio stream."
            )

        track.data = data
        track.stream_url = stream_url
        track.http_headers = (
            data.get("http_headers")
            or {}
        )

        logger.info(
            "SoundCloud stream selected for '%s'.",
            track.title
        )

        source = discord.FFmpegPCMAudio(
            stream_url,
            executable=FFMPEG,
            before_options=build_ffmpeg_before_options(
                track.http_headers
            ),
            options=FFMPEG_OPTIONS
        )

        volume = self.volumes.get(
            track.requested_by.guild.id,
            0.5
        )

        return discord.PCMVolumeTransformer(
            source,
            volume=volume
        )

    # ========================================================
    # PLAY NEXT
    # ========================================================

    async def play_next(
        self,
        guild,
        channel
    ):

        queue = self.get_queue(
            guild.id
        )

        voice = guild.voice_client

        if not voice:

            return

        if not queue:

            self.now_playing[
                guild.id
            ] = None

            return

        track = queue.pop(0)

        self.now_playing[
            guild.id
        ] = track

        try:

            source = await self.create_source(
                track
            )

            def after(error):

                if error:

                    logger.error(
                        "Playback error: %s",
                        error
                    )

                else:

                    logger.info(
                        "Finished: %s",
                        track.title
                    )

                asyncio.run_coroutine_threadsafe(
                    self.play_next(
                        guild,
                        channel
                    ),
                    self.bot.loop
                )

            voice.play(
                source,
                after=after
            )

            logger.info(
                "voice.play() called."
            )

            logger.info(
                "Playing: %s",
                track.title
            )

            embed = discord.Embed(
                title="🎵 Now Playing",
                description=(
                    f"**{track.title}**"
                ),
                color=discord.Color.orange()
            )

            embed.add_field(
                name="Artist",
                value=track.uploader[:1024],
                inline=True
            )

            embed.add_field(
                name="Duration",
                value=track.duration_text,
                inline=True
            )

            embed.add_field(
                name="Source",
                value="SoundCloud",
                inline=True
            )

            embed.set_footer(
                text=(
                    "🐼 Panda Man's World Vibes"
                )
            )

            await channel.send(
                embed=embed
            )

        except Exception as e:

            logger.exception(
                "Could not play track."
            )

            await channel.send(
                "❌ Couldn't play "
                f"**{track.title}**.\n"
                f"`{str(e)[:900]}`"
            )

            # Try the next queued track automatically.
            # Schedule it on the event loop rather than recursively nesting
            # coroutines in the same call stack.
            if self.get_queue(guild.id):
                await asyncio.sleep(1)
                await self.play_next(
                    guild,
                    channel
                )

    # ========================================================
    # !PLAY
    # ========================================================

    @commands.command(
        name="play"
    )
    async def play(
        self,
        ctx,
        *,
        query: str
    ):

        voice = await self.ensure_voice(
            ctx
        )

        if voice is None:

            return

        async with ctx.typing():

            try:

                track = await self.get_track(
                    query,
                    ctx.author
                )

                queue = self.get_queue(
                    ctx.guild.id
                )

                # ------------------------------------------------
                # CURRENTLY PLAYING
                # ------------------------------------------------

                if (
                    voice.is_playing()
                    or voice.is_paused()
                    or self.now_playing.get(
                        ctx.guild.id
                    )
                ):

                    queue.append(
                        track
                    )

                    await ctx.send(
                        "⏳ Added to queue:\n"
                        f"**{track.title}**\n"
                        f"Position: #{len(queue)}"
                    )

                    return

                # ------------------------------------------------
                # START
                # ------------------------------------------------

                queue.append(
                    track
                )

                await self.play_next(
                    ctx.guild,
                    ctx.channel
                )

            except Exception as e:

                logger.exception(
                    "Play command failed."
                )

                error = str(e)

                await ctx.send(
                    "❌ **Couldn't play that SoundCloud track.**\n"
                    f"`{error[:1200]}`"
                )

    # ========================================================
    # !SKIP
    # ========================================================

    @commands.command(
        name="skip"
    )
    async def skip(
        self,
        ctx
    ):

        voice = ctx.voice_client

        if (
            voice
            and (
                voice.is_playing()
                or voice.is_paused()
            )
        ):

            voice.stop()

            await ctx.send(
                "⏭️ Skipped!"
            )

        else:

            await ctx.send(
                "❌ Nothing is playing."
            )

    # ========================================================
    # !PAUSE
    # ========================================================

    @commands.command(
        name="pause"
    )
    async def pause(
        self,
        ctx
    ):

        voice = ctx.voice_client

        if voice and voice.is_playing():

            voice.pause()

            await ctx.send(
                "⏸️ Paused!"
            )

        else:

            await ctx.send(
                "❌ Nothing is playing."
            )

    # ========================================================
    # !RESUME
    # ========================================================

    @commands.command(
        name="resume"
    )
    async def resume(
        self,
        ctx
    ):

        voice = ctx.voice_client

        if voice and voice.is_paused():

            voice.resume()

            await ctx.send(
                "▶️ Resumed!"
            )

        else:

            await ctx.send(
                "❌ Nothing is paused."
            )

    # ========================================================
    # !STOP
    # ========================================================

    @commands.command(
        name="stop"
    )
    async def stop(
        self,
        ctx
    ):

        self.get_queue(
            ctx.guild.id
        ).clear()

        self.now_playing[
            ctx.guild.id
        ] = None

        if ctx.voice_client:

            ctx.voice_client.stop()

        await ctx.send(
            "⏹️ Stopped and cleared the queue."
        )

    # ========================================================
    # !QUEUE
    # ========================================================

    @commands.command(
        name="queue"
    )
    async def queue_command(
        self,
        ctx
    ):

        queue = self.get_queue(
            ctx.guild.id
        )

        current = self.now_playing.get(
            ctx.guild.id
        )

        if not current and not queue:

            await ctx.send(
                "❌ Queue is empty."
            )

            return

        embed = discord.Embed(
            title="🎵 Panda Music Queue",
            color=discord.Color.orange()
        )

        if current:

            embed.add_field(
                name="▶️ Now Playing",
                value=(
                    f"**{current.title}**"
                ),
                inline=False
            )

        if queue:

            text = []

            for i, track in enumerate(
                queue[:10],
                1
            ):

                text.append(
                    f"**{i}.** {track.title}"
                )

            embed.add_field(
                name=f"⏳ Up Next ({len(queue)})",
                value="\n".join(text),
                inline=False
            )

        await ctx.send(
            embed=embed
        )

    # ========================================================
    # !VOLUME
    # ========================================================

    @commands.command(
        name="volume"
    )
    async def volume(
        self,
        ctx,
        value: int
    ):

        if value < 0 or value > 100:

            await ctx.send(
                "❌ Volume must be between 0 and 100."
            )

            return

        volume = value / 100

        self.volumes[
            ctx.guild.id
        ] = volume

        voice = ctx.voice_client

        if (
            voice
            and voice.source
            and isinstance(
                voice.source,
                discord.PCMVolumeTransformer
            )
        ):

            voice.source.volume = volume

        await ctx.send(
            f"🔊 Volume set to **{value}%**."
        )

    # ========================================================
    # !LEAVE
    # ========================================================

    @commands.command(
        name="leave"
    )
    async def leave(
        self,
        ctx
    ):

        self.get_queue(
            ctx.guild.id
        ).clear()

        self.now_playing[
            ctx.guild.id
        ] = None

        if ctx.voice_client:

            await ctx.voice_client.disconnect(
                force=True
            )

            await ctx.send(
                "👋 Left the voice channel."
            )

        else:

            await ctx.send(
                "❌ I'm not in a voice channel."
            )

    # ========================================================
    # !HELP
    # ========================================================

    @commands.command(
        name="help"
    )
    async def help_command(
        self,
        ctx
    ):

        embed = discord.Embed(
            title="🐼 Panda Man's World Vibes",
            description=(
                "**SoundCloud Music Bot**"
            ),
            color=discord.Color.orange()
        )

        embed.add_field(
            name="🎵 Music",
            value=(
                "`!play <song>`\n"
                "`!play <SoundCloud URL>`\n"
                "`!skip`\n"
                "`!pause`\n"
                "`!resume`\n"
                "`!stop`"
            ),
            inline=False
        )

        embed.add_field(
            name="📋 Queue",
            value="`!queue`",
            inline=True
        )

        embed.add_field(
            name="🔊 Volume",
            value="`!volume <0-100>`",
            inline=True
        )

        embed.add_field(
            name="🚪 Voice",
            value="`!leave`",
            inline=True
        )

        await ctx.send(
            embed=embed
        )


# ============================================================
# COMMAND ERROR HANDLER
# ============================================================

@bot.event
async def on_command_error(ctx, error):

    if isinstance(error, commands.CommandNotFound):
        return

    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(
            "❌ Missing something. Try `!help`."
        )
        return

    if isinstance(error, commands.BadArgument):
        await ctx.send(
            "❌ Invalid command argument. Try `!help`."
        )
        return

    logger.exception(
        "Command error in %s: %s",
        getattr(ctx.command, "qualified_name", "unknown"),
        error
    )

    try:
        await ctx.send(
            "❌ Something went wrong while running that command."
        )
    except Exception:
        pass


# ============================================================
# VOICE DISCONNECT CLEANUP
# ============================================================

@bot.event
async def on_voice_state_update(member, before, after):

    if bot.user and member.id == bot.user.id:
        if before.channel and after.channel is None:
            guild_id = before.channel.guild.id
            logger.warning(
                "Bot was disconnected from voice: guild=%s",
                guild_id
            )


# ============================================================
# BOT READY
# ============================================================

@bot.event
async def on_ready():

    logger.info(
        "========================================"
    )

    logger.info(
        "🐼 %s connected to Discord!",
        bot.user
    )

    logger.info(
        "discord.py version: %s",
        discord.__version__
    )

    logger.info(
        "SoundCloud-only mode enabled."
    )

    logger.info(
        "========================================"
    )


# ============================================================
# MAIN
# ============================================================

async def main():

    # Fail early if FFmpeg is unavailable.
    try:
        result = subprocess.run(
            [FFMPEG, "-version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=10,
            check=False,
        )
        first_line = (result.stdout or "").splitlines()
        logger.info(
            "FFmpeg check: %s",
            first_line[0] if first_line else "FFmpeg responded."
        )
    except Exception as e:
        logger.error(
            "FFmpeg check failed: %s",
            e
        )

    await bot.add_cog(
        Music(bot)
    )

    await bot.start(
        DISCORD_TOKEN,
        reconnect=True
    )


if __name__ == "__main__":

    try:

        asyncio.run(
            main()
        )

    except KeyboardInterrupt:

        logger.info(
            "Bot stopped."
        )
