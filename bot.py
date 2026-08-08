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


# ============================================================
# Panda Man's World Vibes
# Discord Music Bot
# Render-friendly
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

logger = logging.getLogger("PandaMusicBot")


# ============================================================
# ENVIRONMENT
# ============================================================

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
SOUNDCLOUD_CLIENT_ID = os.getenv("SOUNDCLOUD_CLIENT_ID")


# ============================================================
# DISCORD INTENTS
# ============================================================

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True


bot = commands.Bot(
    command_prefix="!",
    intents=intents,
    help_command=None,
)


# ============================================================
# YT-DLP CONFIGURATION
# ============================================================
#
# IMPORTANT:
#
# We intentionally use YouTube's web_safari client first.
# Current yt-dlp documentation notes that web_safari can provide
# HLS formats which currently avoid some GVS PO-token requirements.
#
# We do NOT use cookies-from-browser.
# We do NOT store personal YouTube cookies.
#
# ============================================================

YTDL_OPTIONS = {
    # Prefer HLS audio when available, then fall back to normal audio.
    "format": (
        "bestaudio[protocol=m3u8]/"
        "bestaudio[ext=webm]/"
        "bestaudio/best"
    ),

    "noplaylist": True,

    "quiet": True,
    "no_warnings": True,

    "socket_timeout": 30,

    "retries": 3,
    "fragment_retries": 3,
    "extractor_retries": 3,

    "ignoreerrors": False,

    "http_headers": {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/605.1.15 "
            "(KHTML, like Gecko) "
            "Version/18.5 Safari/605.1.15"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    },

    "extractor_args": {
        "youtube": {
            "player_client": ["web_safari"],
        }
    },
}


# ============================================================
# SOUNDCLOUD
# ============================================================

if SOUNDCLOUD_CLIENT_ID:
    YTDL_OPTIONS["extractor_args"]["soundcloud"] = {
        "client_id": [SOUNDCLOUD_CLIENT_ID],
    }

    logger.info("SoundCloud client ID loaded.")

else:
    logger.info(
        "SOUNDCLOUD_CLIENT_ID is not set; using normal "
        "SoundCloud extraction as fallback."
    )


ytdl = yt_dlp.YoutubeDL(YTDL_OPTIONS)


# ============================================================
# FFMPEG
# ============================================================

FFMPEG_EXECUTABLE = shutil.which("ffmpeg") or "ffmpeg"


FFMPEG_BEFORE_OPTIONS = (
    "-reconnect 1 "
    "-reconnect_streamed 1 "
    "-reconnect_on_network_error 1 "
    "-reconnect_on_http_error 4xx,5xx "
    "-reconnect_delay_max 5 "
    "-rw_timeout 15000000 "
    "-protocol_whitelist file,http,https,tcp,tls,crypto "
    "-user_agent "
    "\"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) "
    "Version/18.5 Safari/605.1.15\""
)


FFMPEG_OPTIONS = (
    "-vn "
    "-sn "
    "-dn "
    "-f s16le "
    "-ar 48000 "
    "-ac 2 "
    "-loglevel warning"
)


# ============================================================
# URL HELPERS
# ============================================================

def is_url(value: str) -> bool:
    return value.startswith(("http://", "https://"))


def is_spotify_url(value: str) -> bool:
    value = value.lower()

    return (
        "open.spotify.com/" in value
        or "spotify.link/" in value
    )


def is_soundcloud_url(value: str) -> bool:
    return "soundcloud.com/" in value.lower()


def is_youtube_url(value: str) -> bool:
    value = value.lower()

    return (
        "youtube.com/" in value
        or "youtu.be/" in value
        or "music.youtube.com/" in value
    )


# ============================================================
# SPOTIFY
# ============================================================

def clean_spotify_title(title: str) -> str:
    title = re.sub(
        r"\s+",
        " ",
        title or "",
    ).strip()

    title = re.sub(
        r"\s*\|\s*Spotify\s*$",
        "",
        title,
        flags=re.I,
    )

    title = re.sub(
        r"\s*-\s*Spotify\s*$",
        "",
        title,
        flags=re.I,
    )

    return title


def spotify_metadata_sync(url: str) -> Optional[dict]:
    """
    Resolve Spotify public metadata using oEmbed.

    This does not require the Spotify Web API.
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
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) "
                "Chrome/139.0.0.0 Safari/537.36"
            )
        },
    )

    with urlopen(
        request,
        timeout=15,
    ) as response:

        import json

        data = json.loads(
            response.read().decode("utf-8")
        )

    title = clean_spotify_title(
        data.get("title", "")
    )

    if not title:
        return None

    author = (
        data.get("author_name") or ""
    ).strip()

    search_text = (
        f"{title} {author}"
    ).strip()

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


# ============================================================
# YT-DLP HELPERS
# ============================================================

def extract_info_sync(query: str) -> dict:
    logger.info(
        "yt-dlp extracting: %s",
        query,
    )

    return ytdl.extract_info(
        query,
        download=False,
    )


async def extract_info(query: str) -> dict:
    loop = asyncio.get_running_loop()

    return await loop.run_in_executor(
        None,
        lambda: extract_info_sync(query),
    )


# ============================================================
# SOURCE SEARCH
# ============================================================

async def search_youtube(search: str) -> Optional[dict]:
    """
    Search YouTube first.
    """

    try:

        logger.info(
            "Searching YouTube: %s",
            search,
        )

        data = await extract_info(
            f"ytsearch1:{search}"
        )

        if not data:
            return None

        entries = [
            entry
            for entry in data.get("entries", [])
            if entry
        ]

        if not entries:
            return None

        result = entries[0]

        logger.info(
            "YouTube result selected: %s",
            result.get("title", "Unknown"),
        )

        return result

    except Exception as exc:

        logger.warning(
            "YouTube search failed: %s",
            exc,
        )

        return None


async def search_soundcloud(search: str) -> Optional[dict]:
    """
    SoundCloud fallback.

    This is deliberately NOT used first because your Render logs
    showed SoundCloud HLS returning:

        error=End of file
    """

    try:

        logger.info(
            "Trying SoundCloud fallback: %s",
            search,
        )

        data = await extract_info(
            f"scsearch1:{search}"
        )

        if not data:
            return None

        entries = [
            entry
            for entry in data.get("entries", [])
            if entry
        ]

        if not entries:
            return None

        return entries[0]

    except Exception as exc:

        logger.warning(
            "SoundCloud search failed: %s",
            exc,
        )

        return None


async def search_source(search: str) -> dict:
    """
    Resolve a playable track.

    Priority:

        1. Direct YouTube URL
        2. Direct SoundCloud URL
        3. YouTube search
        4. SoundCloud search fallback
    """

    search = search.strip()

    # --------------------------------------------------------
    # DIRECT URL
    # --------------------------------------------------------

    if is_url(search):

        # Direct YouTube URL
        if is_youtube_url(search):

            logger.info(
                "Direct YouTube URL detected."
            )

            data = await extract_info(
                search
            )

            if data and data.get("entries"):

                entries = [
                    entry
                    for entry in data["entries"]
                    if entry
                ]

                if entries:
                    data = entries[0]

            if not data:
                raise ValueError(
                    "YouTube did not return a playable result."
                )

            return data

        # ----------------------------------------------------
        # Direct SoundCloud URL
        # ----------------------------------------------------

        if is_soundcloud_url(search):

            logger.info(
                "Direct SoundCloud URL detected."
            )

            try:

                data = await extract_info(
                    search
                )

                if data and data.get("entries"):

                    entries = [
                        entry
                        for entry in data["entries"]
                        if entry
                    ]

                    if entries:
                        data = entries[0]

                if data:

                    title = (
                        data.get("title")
                        or ""
                    )

                    uploader = (
                        data.get("uploader")
                        or data.get("channel")
                        or ""
                    )

                    query = (
                        f"{title} {uploader}"
                    ).strip()

                    # Try to find a YouTube copy.
                    if query:

                        youtube = await search_youtube(
                            query
                        )

                        if youtube:

                            logger.info(
                                "Using YouTube copy of SoundCloud track."
                            )

                            return youtube

                    # Fall back to SoundCloud only if necessary.
                    return data

            except Exception as exc:

                logger.warning(
                    "SoundCloud URL resolution failed: %s",
                    exc,
                )

                raise

        # ----------------------------------------------------
        # Other direct URL
        # ----------------------------------------------------

        data = await extract_info(
            search
        )

        if data and data.get("entries"):

            entries = [
                entry
                for entry in data["entries"]
                if entry
            ]

            if entries:
                return entries[0]

        return data

    # --------------------------------------------------------
    # NORMAL SEARCH
    # --------------------------------------------------------

    youtube = await search_youtube(
        search
    )

    if youtube:
        return youtube

    soundcloud = await search_soundcloud(
        search
    )

    if soundcloud:
        return soundcloud

    raise ValueError(
        "Could not find a playable result."
    )


# ============================================================
# TRACK CLASS
# ============================================================

class Track:

    def __init__(
        self,
        data: dict,
        requested_by: discord.Member,
    ):

        self.data = data

        self.requested_by = requested_by

        self.title = (
            data.get("title")
            or "Unknown title"
        )

        self.webpage_url = (
            data.get("webpage_url")
            or data.get("original_url")
            or ""
        )

        self.duration = (
            data.get("duration")
            or 0
        )

        self.uploader = (
            data.get("uploader")
            or data.get("channel")
            or "Unknown"
        )

        self.source_name = (
            self._source_name()
        )

    def _source_name(self) -> str:

        url = (
            self.webpage_url
            or ""
        ).lower()

        extractor = str(
            self.data.get(
                "extractor_key"
            )
            or self.data.get(
                "extractor"
            )
            or ""
        ).lower()

        if (
            "soundcloud" in url
            or "soundcloud" in extractor
        ):
            return "SoundCloud"

        if (
            "youtube" in url
            or "youtube" in extractor
        ):
            return "YouTube"

        return "Audio"

    @property
    def duration_text(self) -> str:

        if not self.duration:
            return "Unknown"

        seconds = int(
            self.duration
        )

        minutes, seconds = divmod(
            seconds,
            60,
        )

        hours, minutes = divmod(
            minutes,
            60,
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

    def __init__(
        self,
        bot_: commands.Bot,
    ):

        self.bot = bot_

        self.queues: dict[
            int,
            list[Track]
        ] = {}

        self.now_playing: dict[
            int,
            Optional[Track]
        ] = {}

        self.volumes: dict[
            int,
            float
        ] = {}

        self.next_locks: dict[
            int,
            asyncio.Lock
        ] = {}

    # --------------------------------------------------------
    # QUEUE
    # --------------------------------------------------------

    def get_queue(
        self,
        guild_id: int,
    ) -> list[Track]:

        return self.queues.setdefault(
            guild_id,
            [],
        )

    # --------------------------------------------------------
    # LOCK
    # --------------------------------------------------------

    def get_lock(
        self,
        guild_id: int,
    ) -> asyncio.Lock:

        return self.next_locks.setdefault(
            guild_id,
            asyncio.Lock(),
        )

    # --------------------------------------------------------
    # VOICE
    # --------------------------------------------------------

    async def ensure_voice(
        self,
        ctx: commands.Context,
    ) -> Optional[discord.VoiceClient]:

        if not ctx.guild:

            await ctx.send(
                "❌ This command can only be used in a server."
            )

            return None

        if (
            not ctx.author.voice
            or not ctx.author.voice.channel
        ):

            await ctx.send(
                "❌ Join a voice channel first."
            )

            return None

        target = (
            ctx.author.voice.channel
        )

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
                    target.id,
                )

                voice = await target.connect(
                    timeout=30,
                    reconnect=True,
                )

                logger.info(
                    "Voice connection established."
                )

                return voice

            except Exception as exc:

                logger.exception(
                    "Voice connection failed"
                )

                await ctx.send(
                    "❌ I couldn't join the voice channel:\n"
                    f"`{str(exc)[:1000]}`"
                )

                return None

        # ----------------------------------------------------
        # MOVE CHANNEL
        # ----------------------------------------------------

        if (
            voice.channel
            and voice.channel.id != target.id
        ):

            try:

                await voice.move_to(
                    target
                )

            except Exception as exc:

                logger.exception(
                    "Voice move failed"
                )

                await ctx.send(
                    "❌ I couldn't move voice channels:\n"
                    f"`{str(exc)[:1000]}`"
                )

                return None

        # ----------------------------------------------------
        # RECONNECT
        # ----------------------------------------------------

        if not voice.is_connected():

            try:

                await voice.disconnect(
                    force=True
                )

            except Exception:
                pass

            try:

                voice = await target.connect(
                    timeout=30,
                    reconnect=True,
                )

                return voice

            except Exception as exc:

                logger.exception(
                    "Voice reconnect failed"
                )

                await ctx.send(
                    "❌ Voice reconnect failed:\n"
                    f"`{str(exc)[:1000]}`"
                )

                return None

        return voice

    # --------------------------------------------------------
    # MAKE AUDIO SOURCE
    # --------------------------------------------------------

    async def make_source(
        self,
        track: Track,
    ) -> discord.AudioSource:

        data = track.data

        direct_url = data.get(
            "url"
        )

        # ----------------------------------------------------
        # RE-EXTRACT EXPIRED URL
        # ----------------------------------------------------

        if not direct_url:

            if not track.webpage_url:

                raise ValueError(
                    "No playable source URL was found."
                )

            data = await extract_info(
                track.webpage_url
            )

            if (
                data
                and data.get("entries")
            ):

                entries = [
                    entry
                    for entry in data["entries"]
                    if entry
                ]

                if entries:
                    data = entries[0]

            direct_url = data.get(
                "url"
            )

        if not direct_url:

            raise ValueError(
                "The audio stream URL could not be extracted."
            )

        logger.info(
            "Starting FFmpeg playback: "
            "guild=%s source=%s",
            track.requested_by.guild.id,
            track.source_name,
        )

        logger.info(
            "Audio URL: %s",
            direct_url[:250],
        )

        source = discord.FFmpegPCMAudio(
            direct_url,

            executable=FFMPEG_EXECUTABLE,

            before_options=(
                FFMPEG_BEFORE_OPTIONS
            ),

            options=(
                FFMPEG_OPTIONS
            ),
        )

        volume = self.volumes.get(
            track.requested_by.guild.id,
            0.5,
        )

        return discord.PCMVolumeTransformer(
            source,
            volume=volume,
        )

    # --------------------------------------------------------
    # PLAY NEXT
    # --------------------------------------------------------

    async def play_next(
        self,
        guild: discord.Guild,
        channel: discord.abc.Messageable,
    ):

        guild_id = guild.id

        lock = self.get_lock(
            guild_id
        )

        async with lock:

            queue = self.get_queue(
                guild_id
            )

            voice = guild.voice_client

            if (
                voice is None
                or not voice.is_connected()
            ):

                self.now_playing[
                    guild_id
                ] = None

                return

            while queue:

                track = queue.pop(0)

                self.now_playing[
                    guild_id
                ] = track

                try:

                    source = await self.make_source(
                        track
                    )

                    # ------------------------------------------------
                    # CALLBACK
                    # ------------------------------------------------

                    def after_playback(
                        error: Optional[Exception]
                    ):

                        if error:

                            logger.error(
                                "Playback error in guild %s: %s",
                                guild_id,
                                error,
                            )

                        else:

                            logger.info(
                                "Playback finished: guild=%s title=%s",
                                guild_id,
                                track.title,
                            )

                        future = (
                            asyncio.run_coroutine_threadsafe(
                                self.play_next(
                                    guild,
                                    channel,
                                ),
                                self.bot.loop,
                            )
                        )

                        def done_callback(
                            done_future
                        ):

                            try:

                                done_future.result()

                            except Exception:

                                logger.exception(
                                    "Error advancing queue "
                                    "in guild %s",
                                    guild_id,
                                )

                        future.add_done_callback(
                            done_callback
                        )

                    # ------------------------------------------------
                    # START PLAYBACK
                    # ------------------------------------------------

                    voice.play(
                        source,
                        after=after_playback,
                    )

                    logger.info(
                        "🎵 voice.play() called | "
                        "playing=%s paused=%s",
                        voice.is_playing(),
                        voice.is_paused(),
                    )

                    # ------------------------------------------------
                    # EMBED
                    # ------------------------------------------------

                    embed = discord.Embed(
                        title="🎵 Now Playing",
                        description=(
                            f"**{track.title}**"
                        ),
                        color=discord.Color.green(),
                    )

                    embed.add_field(
                        name="Artist / Uploader",
                        value=(
                            track.uploader[:1024]
                        ),
                        inline=True,
                    )

                    embed.add_field(
                        name="Duration",
                        value=(
                            track.duration_text
                        ),
                        inline=True,
                    )

                    embed.add_field(
                        name="Source",
                        value=(
                            track.source_name
                        ),
                        inline=True,
                    )

                    embed.set_footer(
                        text=(
                            "Requested by "
                            f"{track.requested_by.display_name}"
                        )
                    )

                    await channel.send(
                        embed=embed
                    )

                    return

                except Exception as exc:

                    logger.exception(
                        "Could not play '%s' "
                        "in guild %s",
                        track.title,
                        guild_id,
                    )

                    try:

                        await channel.send(
                            "❌ Couldn't play "
                            f"**{track.title}**. "
                            "Skipping it.\n"
                            f"`{str(exc)[:900]}`"
                        )

                    except Exception:
                        pass

            self.now_playing[
                guild_id
            ] = None

    # ========================================================
    # !PLAY
    # ========================================================

    @commands.command(
        name="play",
        help="Play a song, URL, or Spotify link",
    )
    async def play(
        self,
        ctx: commands.Context,
        *,
        search: str,
    ):

        voice = await self.ensure_voice(
            ctx
        )

        if voice is None:
            return

        async with ctx.typing():

            try:

                original = search.strip()

                # ------------------------------------------------
                # SPOTIFY
                # ------------------------------------------------

                if is_spotify_url(
                    original
                ):

                    logger.info(
                        "Resolving Spotify link: %s",
                        original,
                    )

                    try:

                        spotify = (
                            await resolve_spotify(
                                original
                            )
                        )

                    except Exception as exc:

                        logger.warning(
                            "Spotify oEmbed failed: %s",
                            exc,
                        )

                        await ctx.send(
                            "❌ I couldn't read that Spotify link. "
                            "Try the song name and artist instead."
                        )

                        return

                    if (
                        not spotify
                        or not spotify.get("search")
                    ):

                        await ctx.send(
                            "❌ Spotify didn't provide enough "
                            "public information to find that track."
                        )

                        return

                    data = await search_source(
                        spotify["search"]
                    )

                else:

                    data = await search_source(
                        original
                    )

                # ------------------------------------------------
                # ENTRIES
                # ------------------------------------------------

                if data and data.get(
                    "entries"
                ):

                    entries = [
                        entry
                        for entry in data["entries"]
                        if entry
                    ]

                    if entries:
                        data = entries[0]

                if not data:

                    await ctx.send(
                        "❌ I couldn't find that song."
                    )

                    return

                # ------------------------------------------------
                # TRACK
                # ------------------------------------------------

                track = Track(
                    data,
                    ctx.author,
                )

                queue = self.get_queue(
                    ctx.guild.id
                )

                # ------------------------------------------------
                # QUEUE
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

                    embed = discord.Embed(
                        title="⏳ Added to Queue",
                        description=(
                            f"**{track.title}**"
                        ),
                        color=discord.Color.blurple(),
                    )

                    embed.add_field(
                        name="Position",
                        value=(
                            f"#{len(queue)}"
                        ),
                        inline=True,
                    )

                    embed.add_field(
                        name="Source",
                        value=(
                            track.source_name
                        ),
                        inline=True,
                    )

                    await ctx.send(
                        embed=embed
                    )

                else:

                    queue.append(
                        track
                    )

                    await self.play_next(
                        ctx.guild,
                        ctx.channel,
                    )

            except Exception as exc:

                logger.exception(
                    "Play command error"
                )

                error_text = str(exc)

                # Friendly YouTube error
                if (
                    "Sign in to confirm" in
                    error_text
                ):

                    message = (
                        "YouTube rejected the request "
                        "with an anti-bot check. "
                        "Try another track or direct URL."
                    )

                elif (
                    "End of file" in
                    error_text
                ):

                    message = (
                        "The audio source closed before "
                        "FFmpeg could finish reading it."
                    )

                else:

                    message = (
                        error_text[:1200]
                    )

                await ctx.send(
                    "❌ **Couldn't play that track.**\n"
                    f"`{message}`"
                )

    # ========================================================
    # !SKIP
    # ========================================================

    @commands.command(
        name="skip",
        help="Skip the current song",
    )
    async def skip(
        self,
        ctx: commands.Context,
    ):

        voice = ctx.voice_client

        if voice and (
            voice.is_playing()
            or voice.is_paused()
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
        name="pause",
        help="Pause playback",
    )
    async def pause(
        self,
        ctx: commands.Context,
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
        name="resume",
        help="Resume playback",
    )
    async def resume(
        self,
        ctx: commands.Context,
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
        name="stop",
        help="Stop music and clear the queue",
    )
    async def stop(
        self,
        ctx: commands.Context,
    ):

        guild_id = ctx.guild.id

        self.get_queue(
            guild_id
        ).clear()

        self.now_playing[
            guild_id
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
        name="queue",
        help="Show the music queue",
    )
    async def queue_cmd(
        self,
        ctx: commands.Context,
    ):

        guild_id = ctx.guild.id

        queue = self.get_queue(
            guild_id
        )

        current = self.now_playing.get(
            guild_id
        )

        if not current and not queue:

            await ctx.send(
                "❌ The queue is empty."
            )

            return

        embed = discord.Embed(
            title="🎵 Music Queue",
            color=discord.Color.purple(),
        )

        if current:

            embed.add_field(
                name="▶️ Now Playing",
                value=(
                    f"**{current.title}**\n"
                    f"{current.source_name}"
                ),
                inline=False,
            )

        if queue:

            lines = []

            for index, track in enumerate(
                queue[:10],
                1,
            ):

                lines.append(
                    f"**{index}.** "
                    f"{track.title}"
                )

            embed.add_field(
                name=(
                    f"⏳ Up Next "
                    f"({len(queue)})"
                ),
                value="\n".join(lines),
                inline=False,
            )

            if len(queue) > 10:

                embed.set_footer(
                    text=(
                        f"+ {len(queue) - 10} "
                        "more song(s)"
                    )
                )

        await ctx.send(
            embed=embed
        )

    # ========================================================
    # !VOLUME
    # ========================================================

    @commands.command(
        name="volume",
        help="Set volume from 0 to 100",
    )
    async def volume(
        self,
        ctx: commands.Context,
        vol: int,
    ):

        if not 0 <= vol <= 100:

            await ctx.send(
                "❌ Volume must be between 0 and 100."
            )

            return

        self.volumes[
            ctx.guild.id
        ] = vol / 100

        if (
            ctx.voice_client
            and isinstance(
                ctx.voice_client.source,
                discord.PCMVolumeTransformer,
            )
        ):

            ctx.voice_client.source.volume = (
                vol / 100
            )

        await ctx.send(
            f"🔊 Volume set to **{vol}%**."
        )

    # ========================================================
    # !LEAVE
    # ========================================================

    @commands.command(
        name="leave",
        help="Leave the voice channel",
    )
    async def leave(
        self,
        ctx: commands.Context,
    ):

        guild_id = ctx.guild.id

        self.get_queue(
            guild_id
        ).clear()

        self.now_playing[
            guild_id
        ] = None

        if ctx.voice_client:

            try:

                await ctx.voice_client.disconnect(
                    force=True
                )

            except Exception:

                logger.exception(
                    "Voice disconnect failed"
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
        name="help",
        help="Show music commands",
    )
    async def help_cmd(
        self,
        ctx: commands.Context,
    ):

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

        await ctx.send(
            embed=embed
        )


# ============================================================
# READY EVENT
# ============================================================

@bot.event
async def on_ready():

    logger.info(
        "========================================"
    )

    logger.info(
        "🐼 %s connected to Discord!",
        bot.user,
    )

    logger.info(
        "discord.py version: %s",
        discord.__version__,
    )

    logger.info(
        "========================================"
    )

    logger.info(
        "Commands:"
    )

    logger.info(
        "!play [song/URL] - YouTube/SoundCloud/Spotify"
    )

    logger.info(
        "!skip"
    )

    logger.info(
        "!pause"
    )

    logger.info(
        "!resume"
    )

    logger.info(
        "!stop"
    )

    logger.info(
        "!queue"
    )

    logger.info(
        "!volume [0-100]"
    )

    logger.info(
        "!leave"
    )

    logger.info(
        "!help"
    )

    logger.info(
        "========================================"
    )


# ============================================================
# VOICE DIAGNOSTICS
# ============================================================

@bot.event
async def on_voice_state_update(
    member: discord.Member,
    before: discord.VoiceState,
    after: discord.VoiceState,
):

    if not bot.user:
        return

    if member.id != bot.user.id:
        return

    # Bot left voice
    if (
        before.channel
        and not after.channel
    ):

        logger.warning(
            "Bot left voice: "
            "guild=%s old_channel=%s",
            member.guild.id,
            before.channel.id,
        )

    # Bot joined/moved
    elif (
        after.channel
        and before.channel != after.channel
    ):

        logger.info(
            "Bot voice channel: "
            "guild=%s channel=%s",
            member.guild.id,
            after.channel.id,
        )


# ============================================================
# MAIN
# ============================================================

async def main():

    if not DISCORD_TOKEN:

        raise RuntimeError(
            "DISCORD_TOKEN environment variable "
            "is not set."
        )

    ffmpeg_path = shutil.which(
        "ffmpeg"
    )

    if not ffmpeg_path:

        raise RuntimeError(
            "FFmpeg is not installed or is not on PATH. "
            "Make sure Render has FFmpeg installed."
        )

    logger.info(
        "FFmpeg found at: %s",
        ffmpeg_path,
    )

    # Modern discord.py requires add_cog to be awaited.
    async with bot:

        await bot.add_cog(
            Music(bot)
        )

        await bot.start(
            DISCORD_TOKEN,
            reconnect=True,
        )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    try:

        asyncio.run(
            main()
        )

    except KeyboardInterrupt:

        logger.info(
            "Bot stopped."
        )
