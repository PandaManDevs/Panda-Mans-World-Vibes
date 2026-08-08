import os
import re
import sys
import asyncio
import logging
import unicodedata
from dataclasses import dataclass, field
from typing import Optional

import discord
from discord.ext import commands
import yt_dlp


# ============================================================
# PANDA MANS WORLD VIBES
# Discord SoundCloud Music Bot
#
# SoundCloud ONLY
# YouTube is intentionally disabled.
#
# Designed for Render + Python 3.11+
# ============================================================


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    stream=sys.stdout,
)

logger = logging.getLogger("PandaMusicBot")


# ============================================================
# ENVIRONMENT
# ============================================================

TOKEN = os.getenv("DISCORD_TOKEN") or os.getenv("TOKEN")

if not TOKEN:
    logger.critical(
        "DISCORD_TOKEN/TOKEN environment variable is missing."
    )
    raise RuntimeError(
        "Set DISCORD_TOKEN in your Render environment variables."
    )


SOUNDCLOUD_CLIENT_ID = os.getenv("SOUNDCLOUD_CLIENT_ID", "").strip()


# ============================================================
# FFMPEG
# ============================================================

FFMPEG_EXECUTABLE = os.getenv(
    "FFMPEG_PATH",
    "/usr/bin/ffmpeg",
)

if not os.path.exists(FFMPEG_EXECUTABLE):
    logger.warning(
        "FFmpeg was not found at %s. "
        "Trying the ffmpeg command from PATH.",
        FFMPEG_EXECUTABLE,
    )
    FFMPEG_EXECUTABLE = "ffmpeg"


# These options are important for SoundCloud HLS streams.
#
# SoundCloud frequently returns an m3u8/HLS stream instead of
# a normal MP3 URL. The stream URL can also expire, which is
# why the bot refreshes it immediately before playback.
#
FFMPEG_BEFORE_OPTIONS = (
    "-reconnect 1 "
    "-reconnect_streamed 1 "
    "-reconnect_at_eof 1 "
    "-reconnect_on_network_error 1 "
    "-reconnect_on_http_error 4xx,5xx "
    "-reconnect_delay_max 10 "
    "-rw_timeout 30000000 "
    "-http_persistent 0 "
    "-protocol_whitelist file,http,https,tcp,tls,crypto "
    "-user_agent "
    "\"Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/139.0.0.0 Safari/537.36\""
)

FFMPEG_OPTIONS = (
    "-vn "
    "-sn "
    "-dn "
    "-loglevel warning "
    "-af aresample=async=1"
)


# ============================================================
# YT-DLP
# ============================================================

YTDL_OPTIONS = {
    "format": "bestaudio/best",

    # Do not download the actual file.
    "skip_download": True,

    # Do not print a huge amount of output.
    "quiet": True,
    "no_warnings": True,

    # SoundCloud extraction.
    "extract_flat": False,

    # Don't accidentally use YouTube.
    "allowed_extractors": [
        "soundcloud",
    ],

    # Network behavior.
    "socket_timeout": 30,
    "retries": 3,
    "fragment_retries": 3,

    # Prefer AAC/HLS when SoundCloud provides it.
    "concurrent_fragment_downloads": 1,

    # HTTP headers.
    "http_headers": {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/139.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    },
}


if SOUNDCLOUD_CLIENT_ID:
    logger.info(
        "SOUNDCLOUD_CLIENT_ID is configured."
    )
else:
    logger.info(
        "SOUNDCLOUD_CLIENT_ID is not set; "
        "using yt-dlp's normal SoundCloud extraction."
    )


# ============================================================
# INTENTS
# ============================================================

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True


# ============================================================
# BOT
# ============================================================

bot = commands.Bot(
    command_prefix="!",
    intents=intents,
    help_command=None,
)


# ============================================================
# MUSIC DATA
# ============================================================

@dataclass
class Track:
    title: str
    webpage_url: str
    stream_url: str
    requested_by: discord.Member
    duration: int = 0
    thumbnail: Optional[str] = None
    uploader: Optional[str] = None
    data: dict = field(default_factory=dict)


@dataclass
class GuildMusic:
    queue: list[Track] = field(default_factory=list)

    current: Optional[Track] = None

    volume: float = 0.50

    voice: Optional[discord.VoiceClient] = None

    text_channel: Optional[discord.TextChannel] = None

    playing: bool = False


music: dict[int, GuildMusic] = {}


# ============================================================
# HELPERS
# ============================================================

def get_music(guild_id: int) -> GuildMusic:
    if guild_id not in music:
        music[guild_id] = GuildMusic()

    return music[guild_id]


def normalize_search(text: str) -> str:
    """
    Normalizes fancy Unicode text.

    Example:
        𝓟𝓪𝓷𝓭𝓪 𝓜𝓪𝓷’𝓼 𝓥𝓲𝓫𝓮𝓼 🎧

    becomes approximately:

        Panda Man's Vibes
    """

    if not text:
        return ""

    text = unicodedata.normalize(
        "NFKC",
        text,
    )

    text = text.replace(
        "\u2019",
        "'",
    )

    text = text.replace(
        "\u2018",
        "'",
    )

    text = text.replace(
        "\u201c",
        '"',
    )

    text = text.replace(
        "\u201d",
        '"',
    )

    # Remove emoji/symbol characters that often hurt search.
    cleaned = []

    for char in text:
        category = unicodedata.category(char)

        if category.startswith("So"):
            continue

        if category.startswith("Cs"):
            continue

        cleaned.append(char)

    text = "".join(cleaned)

    text = re.sub(
        r"\s+",
        " ",
        text,
    ).strip()

    return text


def is_url(text: str) -> bool:
    return bool(
        re.match(
            r"^https?://",
            text.strip(),
            re.IGNORECASE,
        )
    )


def is_soundcloud_url(text: str) -> bool:
    return bool(
        re.match(
            r"^https?://(?:www\.)?soundcloud\.com/",
            text.strip(),
            re.IGNORECASE,
        )
    )


def is_youtube_url(text: str) -> bool:
    lowered = text.lower()

    return (
        "youtube.com" in lowered
        or "youtu.be" in lowered
        or "music.youtube.com" in lowered
    )


def format_duration(seconds: int) -> str:
    if not seconds:
        return "Unknown"

    seconds = int(seconds)

    minutes = seconds // 60
    remaining = seconds % 60

    if minutes >= 60:
        hours = minutes // 60
        minutes %= 60

        return f"{hours}:{minutes:02d}:{remaining:02d}"

    return f"{minutes}:{remaining:02d}"


def truncate(text: str, length: int = 90) -> str:
    if not text:
        return ""

    if len(text) <= length:
        return text

    return text[: length - 3] + "..."


# ============================================================
# YT-DLP EXTRACTION
# ============================================================

async def extract_info(url: str) -> Optional[dict]:
    """
    Runs yt-dlp outside the asyncio event loop.

    This prevents yt-dlp from blocking the Discord bot.
    """

    loop = asyncio.get_running_loop()

    options = dict(YTDL_OPTIONS)

    if SOUNDCLOUD_CLIENT_ID:
        options["extractor_args"] = {
            "soundcloud": {
                "client_id": [
                    SOUNDCLOUD_CLIENT_ID
                ]
            }
        }

    def extract():
        with yt_dlp.YoutubeDL(options) as ytdl:
            return ytdl.extract_info(
                url,
                download=False,
            )

    try:
        return await loop.run_in_executor(
            None,
            extract,
        )

    except Exception:
        logger.exception(
            "yt-dlp extraction failed for: %s",
            url,
        )

        raise


# ============================================================
# SOUNDCloud SEARCH
# ============================================================

async def search_soundcloud(search: str) -> Optional[dict]:
    """
    Search SoundCloud only.

    YouTube is deliberately never used.
    """

    search = normalize_search(search)

    if not search:
        return None

    logger.info(
        "Searching SoundCloud: %s",
        search,
    )

    query = f"scsearch5:{search}"

    try:
        data = await extract_info(query)

    except Exception:
        logger.exception(
            "SoundCloud search failed: %s",
            search,
        )
        return None

    if not data:
        return None

    entries = data.get("entries")

    if not entries:
        return None

    entries = [
        entry
        for entry in entries
        if entry
        and entry.get("webpage_url")
    ]

    if not entries:
        return None

    # Prefer entries with an actual audio URL.
    playable = [
        entry
        for entry in entries
        if entry.get("url")
    ]

    if playable:
        selected = playable[0]
    else:
        selected = entries[0]

    logger.info(
        "Selected SoundCloud track: %s",
        selected.get("webpage_url"),
    )

    return selected


# ============================================================
# SEARCH SOURCE
# ============================================================

async def search_source(search: str) -> Optional[dict]:
    """
    SoundCloud-only source resolver.

    YouTube URLs are rejected.
    """

    search = search.strip()

    if not search:
        raise ValueError(
            "Please provide a SoundCloud URL or search term."
        )

    if is_youtube_url(search):
        raise ValueError(
            "YouTube is disabled. "
            "Please use a SoundCloud song or SoundCloud URL."
        )

    # Direct SoundCloud URL.
    if is_url(search):

        if not is_soundcloud_url(search):
            raise ValueError(
                "Only SoundCloud URLs are supported."
            )

        logger.info(
            "Resolving SoundCloud URL: %s",
            search,
        )

        data = await extract_info(search)

        if not data:
            raise ValueError(
                "SoundCloud did not return track information."
            )

        # Handle SoundCloud playlists/sets.
        entries = data.get("entries")

        if entries:
            entries = [
                entry
                for entry in entries
                if entry
            ]

            if entries:
                return entries[0]

        return data

    # Normal SoundCloud search.
    data = await search_soundcloud(search)

    if not data:
        raise ValueError(
            f"No SoundCloud results found for `{search}`."
        )

    return data


# ============================================================
# TRACK CREATION
# ============================================================

def make_track(
    data: dict,
    requested_by: discord.Member,
) -> Track:

    webpage_url = (
        data.get("webpage_url")
        or data.get("original_url")
        or ""
    )

    stream_url = data.get("url") or ""

    title = (
        data.get("title")
        or "Unknown SoundCloud Track"
    )

    duration = int(
        data.get("duration")
        or 0
    )

    thumbnail = data.get(
        "thumbnail"
    )

    uploader = (
        data.get("uploader")
        or data.get("artist")
        or data.get("creator")
    )

    return Track(
        title=title,
        webpage_url=webpage_url,
        stream_url=stream_url,
        requested_by=requested_by,
        duration=duration,
        thumbnail=thumbnail,
        uploader=uploader,
        data=data,
    )


# ============================================================
# REFRESH SOUNDCloud STREAM
# ============================================================

async def refresh_track(track: Track) -> Track:
    """
    SoundCloud stream URLs can expire.

    We resolve the public SoundCloud page again immediately
    before playback so FFmpeg gets a fresh URL.
    """

    if not track.webpage_url:
        raise RuntimeError(
            "This track does not have a SoundCloud page URL."
        )

    logger.info(
        "Refreshing SoundCloud stream: %s",
        track.webpage_url,
    )

    fresh_data = await extract_info(
        track.webpage_url
    )

    if not fresh_data:
        raise RuntimeError(
            "SoundCloud returned no data when "
            "refreshing the track."
        )

    # If a set/playlist somehow gets returned, use the first
    # valid entry.
    entries = fresh_data.get("entries")

    if entries:
        entries = [
            entry
            for entry in entries
            if entry
        ]

        if entries:
            fresh_data = entries[0]

    fresh_url = fresh_data.get("url")

    if not fresh_url:
        raise RuntimeError(
            "SoundCloud did not return a playable "
            "audio stream."
        )

    track.stream_url = fresh_url
    track.data = fresh_data

    if fresh_data.get("title"):
        track.title = fresh_data["title"]

    if fresh_data.get("duration"):
        track.duration = int(
            fresh_data["duration"]
        )

    if fresh_data.get("thumbnail"):
        track.thumbnail = fresh_data[
            "thumbnail"
        ]

    logger.info(
        "Fresh SoundCloud stream obtained for: %s",
        track.title,
    )

    return track


# ============================================================
# MAKE FFMPEG SOURCE
# ============================================================

async def make_source(
    state: GuildMusic,
    track: Track,
) -> discord.AudioSource:

    # Always refresh.
    track = await refresh_track(
        track
    )

    direct_url = track.stream_url

    if not direct_url:
        raise RuntimeError(
            "SoundCloud returned an empty stream URL."
        )

    logger.info(
        "Starting FFmpeg playback: "
        "guild=%s source=SoundCloud title=%s",
        track.requested_by.guild.id,
        track.title,
    )

    logger.info(
        "SoundCloud stream URL begins with: %s",
        direct_url[:180],
    )

    source = discord.FFmpegPCMAudio(
        direct_url,
        executable=FFMPEG_EXECUTABLE,
        before_options=FFMPEG_BEFORE_OPTIONS,
        options=FFMPEG_OPTIONS,
    )

    transformed = discord.PCMVolumeTransformer(
        source,
        volume=state.volume,
    )

    return transformed


# ============================================================
# PLAY NEXT
# ============================================================

async def play_next(
    guild: discord.Guild,
) -> None:

    state = get_music(
        guild.id
    )

    voice = guild.voice_client

    if not voice:
        state.playing = False
        state.current = None
        return

    if not state.queue:
        state.playing = False
        state.current = None

        try:
            if state.text_channel:
                await state.text_channel.send(
                    "⏹️ Queue finished."
                )
        except Exception:
            pass

        return

    track = state.queue.pop(0)

    state.current = track
    state.playing = True

    try:
        source = await make_source(
            state,
            track,
        )

    except Exception as exc:
        logger.exception(
            "Could not create audio source."
        )

        try:
            if state.text_channel:
                await state.text_channel.send(
                    "❌ I couldn't play that SoundCloud track.\n"
                    f"```{truncate(str(exc), 500)}```"
                )
        except Exception:
            pass

        state.current = None
        state.playing = False

        # Try the next track automatically.
        if state.queue:
            await asyncio.sleep(1)
            await play_next(guild)

        return

    def after_play(error):
        if error:
            logger.error(
                "FFmpeg playback error: %s",
                error,
            )

        future = asyncio.run_coroutine_threadsafe(
            handle_track_finished(guild),
            bot.loop,
        )

        try:
            future.result()
        except Exception:
            logger.exception(
                "Error finishing track."
            )

    try:
        voice.play(
            source,
            after=after_play,
        )

    except Exception:
        logger.exception(
            "Discord voice.play() failed."
        )

        try:
            source.cleanup()
        except Exception:
            pass

        state.current = None
        state.playing = False

        if state.queue:
            await asyncio.sleep(1)
            await play_next(guild)

        return

    logger.info(
        "Now playing: %s",
        track.title,
    )

    try:
        if state.text_channel:
            embed = discord.Embed(
                title="🎵 Now Playing",
                description=(
                    f"**{track.title}**"
                ),
                color=0xFF5500,
            )

            if track.uploader:
                embed.add_field(
                    name="Artist",
                    value=truncate(
                        track.uploader,
                        100,
                    ),
                    inline=True,
                )

            if track.duration:
                embed.add_field(
                    name="Duration",
                    value=format_duration(
                        track.duration
                    ),
                    inline=True,
                )

            embed.add_field(
                name="Requested by",
                value=track.requested_by.mention,
                inline=True,
            )

            if track.webpage_url:
                embed.add_field(
                    name="SoundCloud",
                    value=track.webpage_url,
                    inline=False,
                )

            if track.thumbnail:
                embed.set_thumbnail(
                    url=track.thumbnail
                )

            await state.text_channel.send(
                embed=embed
            )

    except Exception:
        logger.exception(
            "Could not send now-playing message."
        )


async def handle_track_finished(
    guild: discord.Guild,
) -> None:

    state = get_music(
        guild.id
    )

    state.current = None
    state.playing = False

    await asyncio.sleep(0.5)

    voice = guild.voice_client

    if not voice:
        return

    if state.queue:
        await play_next(guild)


# ============================================================
# VOICE CONNECT
# ============================================================

async def connect_to_user_channel(
    ctx: commands.Context,
) -> discord.VoiceClient:

    if not ctx.author.voice:
        raise RuntimeError(
            "You need to join a voice channel first."
        )

    channel = ctx.author.voice.channel

    permissions = channel.permissions_for(
        ctx.guild.me
    )

    if not permissions.connect:
        raise RuntimeError(
            "I don't have permission to connect "
            "to that voice channel."
        )

    if not permissions.speak:
        raise RuntimeError(
            "I don't have permission to speak "
            "in that voice channel."
        )

    voice = ctx.guild.voice_client

    if voice:

        if voice.channel.id != channel.id:
            await voice.move_to(channel)

        return voice

    logger.info(
        "Connecting to voice: "
        "guild=%s channel=%s",
        ctx.guild.id,
        channel.id,
    )

    try:
        voice = await channel.connect(
            reconnect=True
        )

    except Exception:
        logger.exception(
            "Voice connection failed."
        )
        raise

    state = get_music(
        ctx.guild.id
    )

    state.voice = voice

    logger.info(
        "Voice connection complete: "
        "guild=%s channel=%s",
        ctx.guild.id,
        channel.id,
    )

    return voice


# ============================================================
# PLAY COMMAND
# ============================================================

@bot.command(name="play")
async def play(
    ctx: commands.Context,
    *,
    search: str,
):
    """
    !play <SoundCloud URL or search>
    """

    if not ctx.guild:
        await ctx.send(
            "❌ This command can only be used in a server."
        )
        return

    state = get_music(
        ctx.guild.id
    )

    state.text_channel = ctx.channel

    # Connect first.
    try:
        voice = await connect_to_user_channel(
            ctx
        )

    except Exception as exc:
        await ctx.send(
            "❌ I couldn't join the voice channel:\n"
            f"```{truncate(str(exc), 500)}```"
        )
        return

    # Resolve SoundCloud.
    try:
        search = normalize_search(
            search
        )

        # Prevent YouTube.
        if is_youtube_url(search):
            await ctx.send(
                "❌ YouTube is disabled.\n"
                "Please use a **SoundCloud** song or URL."
            )
            return

        async with ctx.typing():
            data = await search_source(
                search
            )

        if not data:
            raise ValueError(
                "No SoundCloud result was found."
            )

        track = make_track(
            data,
            ctx.author,
        )

    except Exception as exc:
        logger.exception(
            "Play command search error."
        )

        await ctx.send(
            "❌ Couldn't find that SoundCloud track.\n"
            f"```{truncate(str(exc), 700)}```"
        )
        return

    # Put in queue.
    state.queue.append(
        track
    )

    position = len(
        state.queue
    )

    # If something is already playing, just queue it.
    if voice.is_playing() or voice.is_paused():
        embed = discord.Embed(
            title="📥 Added to Queue",
            description=(
                f"**{track.title}**"
            ),
            color=0xFF5500,
        )

        embed.add_field(
            name="Position",
            value=str(position),
            inline=True,
        )

        if track.duration:
            embed.add_field(
                name="Duration",
                value=format_duration(
                    track.duration
                ),
                inline=True,
            )

        await ctx.send(
            embed=embed
        )

        return

    # Nothing is playing.
    await ctx.send(
        f"🔎 Found **{track.title}** on SoundCloud.\n"
        "▶️ Starting playback..."
    )

    await play_next(
        ctx.guild
    )


# ============================================================
# SKIP
# ============================================================

@bot.command(name="skip")
async def skip(
    ctx: commands.Context,
):
    state = get_music(
        ctx.guild.id
    )

    voice = ctx.guild.voice_client

    if not voice or not voice.is_playing():
        await ctx.send(
            "❌ Nothing is currently playing."
        )
        return

    voice.stop()

    await ctx.send(
        "⏭️ Skipped the current track."
    )


# ============================================================
# PAUSE
# ============================================================

@bot.command(name="pause")
async def pause(
    ctx: commands.Context,
):
    voice = ctx.guild.voice_client

    if not voice or not voice.is_playing():
        await ctx.send(
            "❌ Nothing is currently playing."
        )
        return

    voice.pause()

    await ctx.send(
        "⏸️ Music paused."
    )


# ============================================================
# RESUME
# ============================================================

@bot.command(name="resume")
async def resume(
    ctx: commands.Context,
):
    voice = ctx.guild.voice_client

    if not voice or not voice.is_paused():
        await ctx.send(
            "❌ Music isn't paused."
        )
        return

    voice.resume()

    await ctx.send(
        "▶️ Music resumed."
    )


# ============================================================
# STOP
# ============================================================

@bot.command(name="stop")
async def stop(
    ctx: commands.Context,
):
    state = get_music(
        ctx.guild.id
    )

    voice = ctx.guild.voice_client

    state.queue.clear()
    state.current = None
    state.playing = False

    if voice and (
        voice.is_playing()
        or voice.is_paused()
    ):
        voice.stop()

    await ctx.send(
        "⏹️ Stopped music and cleared the queue."
    )


# ============================================================
# QUEUE
# ============================================================

@bot.command(name="queue")
async def queue_command(
    ctx: commands.Context,
):
    state = get_music(
        ctx.guild.id
    )

    embed = discord.Embed(
        title="🎵 Panda Music Queue",
        color=0xFF5500,
    )

    if state.current:
        embed.add_field(
            name="▶️ Now Playing",
            value=(
                f"**{truncate(state.current.title, 70)}**"
            ),
            inline=False,
        )

    if not state.queue:
        if not state.current:
            embed.description = (
                "The queue is empty."
            )

        await ctx.send(
            embed=embed
        )
        return

    lines = []

    for index, track in enumerate(
        state.queue[:15],
        start=1,
    ):
        duration = ""

        if track.duration:
            duration = (
                f" — {format_duration(track.duration)}"
            )

        lines.append(
            f"`{index}.` "
            f"**{truncate(track.title, 65)}**"
            f"{duration}"
        )

    if len(state.queue) > 15:
        lines.append(
            f"\n...and "
            f"{len(state.queue) - 15} more."
        )

    embed.add_field(
        name="📋 Up Next",
        value="\n".join(lines),
        inline=False,
    )

    await ctx.send(
        embed=embed
    )


# ============================================================
# VOLUME
# ============================================================

@bot.command(name="volume")
async def volume(
    ctx: commands.Context,
    amount: int,
):
    if amount < 0 or amount > 100:
        await ctx.send(
            "❌ Volume must be between 0 and 100."
        )
        return

    state = get_music(
        ctx.guild.id
    )

    state.volume = amount / 100.0

    voice = ctx.guild.voice_client

    if voice and voice.source:
        source = voice.source

        if isinstance(
            source,
            discord.PCMVolumeTransformer,
        ):
            source.volume = state.volume

    await ctx.send(
        f"🔊 Volume set to **{amount}%**."
    )


# ============================================================
# LEAVE
# ============================================================

@bot.command(name="leave")
async def leave(
    ctx: commands.Context,
):
    state = get_music(
        ctx.guild.id
    )

    voice = ctx.guild.voice_client

    state.queue.clear()
    state.current = None
    state.playing = False

    if not voice:
        await ctx.send(
            "❌ I'm not in a voice channel."
        )
        return

    try:
        if voice.is_playing() or voice.is_paused():
            voice.stop()

        await voice.disconnect(
            force=True
        )

    except Exception:
        logger.exception(
            "Error disconnecting voice."
        )

    state.voice = None

    await ctx.send(
        "👋 Left the voice channel and cleared the queue."
    )


# ============================================================
# HELP
# ============================================================

@bot.command(name="help")
async def help_command(
    ctx: commands.Context,
):
    embed = discord.Embed(
        title="🐼 Panda Mans World Vibes",
        description=(
            "🎵 **SoundCloud Music Bot**\n\n"
            "YouTube playback is disabled. "
            "Use SoundCloud links or search terms."
        ),
        color=0xFF5500,
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
        inline=True,
    )

    embed.add_field(
        name="📋 Queue",
        value=(
            "`!queue`\n"
            "`!volume <0-100>`\n"
            "`!leave`"
        ),
        inline=True,
    )

    embed.add_field(
        name="🔊 Example",
        value=(
            "`!play Panda Man Vibes`\n"
            "`!play https://soundcloud.com/...`"
        ),
        inline=False,
    )

    await ctx.send(
        embed=embed
    )


# ============================================================
# ERROR HANDLER
# ============================================================

@bot.event
async def on_command_error(
    ctx: commands.Context,
    error: Exception,
):

    if isinstance(
        error,
        commands.CommandNotFound,
    ):
        return

    if isinstance(
        error,
        commands.MissingRequiredArgument,
    ):
        await ctx.send(
            "❌ You're missing something.\n"
            "Try `!help`."
        )
        return

    if isinstance(
        error,
        commands.BadArgument,
    ):
        await ctx.send(
            "❌ Invalid command arguments.\n"
            "Try `!help`."
        )
        return

    logger.exception(
        "Command error",
        exc_info=error,
    )

    try:
        await ctx.send(
            "❌ Something went wrong while running "
            "that command.\n"
            f"```{truncate(str(error), 500)}```"
        )
    except Exception:
        pass


# ============================================================
# READY
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
        "FFmpeg executable: %s",
        FFMPEG_EXECUTABLE,
    )

    logger.info(
        "SoundCloud only: ENABLED"
    )

    logger.info(
        "YouTube playback: DISABLED"
    )

    logger.info(
        "========================================"
    )

    logger.info(
        "Commands:"
    )

    logger.info(
        "!play [song/URL] - SoundCloud"
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
# VOICE STATE DEBUGGING
# ============================================================

@bot.event
async def on_voice_state_update(
    member: discord.Member,
    before: discord.VoiceState,
    after: discord.VoiceState,
):

    if member.id != bot.user.id:
        return

    if before.channel != after.channel:

        if after.channel:
            logger.info(
                "Bot voice channel: "
                "guild=%s channel=%s",
                member.guild.id,
                after.channel.id,
            )

        else:
            logger.info(
                "Bot left voice channel: "
                "guild=%s",
                member.guild.id,
            )


# ============================================================
# SHUTDOWN
# ============================================================

async def shutdown():

    logger.info(
        "Shutting down Panda Music Bot..."
    )

    for guild_id, state in list(
        music.items()
    ):

        state.queue.clear()
        state.current = None
        state.playing = False

        voice = state.voice

        if voice:
            try:
                if voice.is_playing():
                    voice.stop()

                await voice.disconnect(
                    force=True
                )

            except Exception:
                logger.exception(
                    "Failed to disconnect "
                    "guild %s",
                    guild_id,
                )


# ============================================================
# START BOT
# ============================================================

async def main():

    try:

        async with bot:

            await bot.start(
                TOKEN
            )

    except KeyboardInterrupt:

        logger.info(
            "Keyboard interrupt received."
        )

    except Exception:

        logger.exception(
            "Fatal bot error."
        )

        raise

    finally:

        try:
            await shutdown()
        except Exception:
            logger.exception(
                "Error during shutdown."
            )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    asyncio.run(
        main()
    )
