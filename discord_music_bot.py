import discord
from discord.ext import commands
import yt_dlp
import asyncio
from typing import Optional
import os
import logging

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configure bot with intents
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(command_prefix='!', intents=intents)

# YouTube DL configuration with better headers and anti-bot detection
ytdl_format_options = {
    'format': 'bestaudio/best',
    'noplaylist': True,
    'default_search': 'ytsearch',
    'quiet': False,
    'no_warnings': False,
    'socket_timeout': 30,
    'http_headers': {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept-Language': 'en-US,en;q=0.9',
    },
    'extractor_args': {
        'youtube': {
            'player_client': ['web'],
            'player_skip': ['js', 'configs'],
        }
    },
}

ytdl = yt_dlp.YoutubeDL(ytdl_format_options)

class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get('title', 'Unknown')
        self.url = data.get('webpage_url', '')

    @classmethod
    async def from_url(cls, url, *, loop=None):
        loop = loop or asyncio.get_event_loop()
        try:
            data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=False))
            if 'entries' in data:
                data = data['entries'][0]
            
            # Get the direct audio URL
            if 'url' not in data:
                logger.error(f"No URL found in data: {data.keys()}")
                raise ValueError("Could not extract audio URL")
            
            filename = data['url']
            logger.info(f"Playing: {data.get('title', 'Unknown')} - URL: {filename[:50]}...")
            
            return cls(
                discord.FFmpegPCMAudio(
                    filename,
                    before_options='-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
                    options='-vn'
                ),
                data=data
            )
        except Exception as e:
            logger.error(f"Error extracting from URL {url}: {str(e)}")
            raise

class Music(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.queue = []
        self.now_playing = None
        self.is_playing = False

    async def play_next(self, ctx):
        if self.queue:
            self.now_playing = self.queue.pop(0)
            self.is_playing = True
            try:
                source = await YTDLSource.from_url(self.now_playing['url'], loop=self.bot.loop)
                
                def after_playback(error):
                    if error:
                        logger.error(f"Playback error: {error}")
                    self.is_playing = False
                    asyncio.run_coroutine_threadsafe(self.play_next(ctx), self.bot.loop)
                
                ctx.voice_client.play(source, after=after_playback)
                
                embed = discord.Embed(
                    title="🎵 Now Playing",
                    description=self.now_playing['title'],
                    color=discord.Color.green()
                )
                duration = self.now_playing.get('duration', 0)
                if duration:
                    minutes = duration // 60
                    seconds = duration % 60
                    embed.add_field(name="Duration", value=f"{minutes}:{seconds:02d}", inline=False)
                
                await ctx.send(embed=embed)
            except Exception as e:
                logger.error(f"Error playing track: {str(e)}")
                await ctx.send(f"❌ Error playing: {str(e)}")
                self.is_playing = False
                await self.play_next(ctx)
        else:
            self.now_playing = None
            self.is_playing = False

    @commands.command(name='play', help='Play a song from YouTube')
    async def play(self, ctx, *, search: str):
        """Play a song by search query or URL"""
        if ctx.voice_client is None:
            if ctx.author.voice:
                try:
                    await ctx.author.voice.channel.connect()
                except Exception as e:
                    await ctx.send(f"❌ Could not connect to voice channel: {str(e)}")
                    return
            else:
                await ctx.send("❌ You need to be in a voice channel!")
                return

        async with ctx.typing():
            try:
                logger.info(f"Searching for: {search}")
                
                # Extract info with better error handling
                data = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: ytdl.extract_info(search, download=False)
                )
                
                if data is None:
                    await ctx.send("❌ Could not find that song!")
                    return
                
                if 'entries' in data:
                    data = data['entries'][0]
                
                song_info = {
                    'url': search,  # Use the original search/URL
                    'title': data.get('title', 'Unknown Title'),
                    'duration': data.get('duration', 0)
                }

                if ctx.voice_client.is_playing() or self.is_playing:
                    self.queue.append(song_info)
                    embed = discord.Embed(
                        title="⏳ Added to Queue",
                        description=song_info['title'],
                        color=discord.Color.blue()
                    )
                    embed.add_field(name="Position", value=f"#{len(self.queue)}", inline=False)
                    await ctx.send(embed=embed)
                else:
                    await self.play_next(ctx)

            except Exception as e:
                logger.error(f"Play command error: {str(e)}")
                await ctx.send(f"❌ Error: {str(e)}")

    @commands.command(name='skip', help='Skip the current song')
    async def skip(self, ctx):
        """Skip current song"""
        if ctx.voice_client and ctx.voice_client.is_playing():
            ctx.voice_client.stop()
            await ctx.send("⏭️ Skipped!")
        else:
            await ctx.send("❌ Nothing is playing!")

    @commands.command(name='pause', help='Pause the current song')
    async def pause(self, ctx):
        """Pause playback"""
        if ctx.voice_client and ctx.voice_client.is_playing():
            ctx.voice_client.pause()
            await ctx.send("⏸️ Paused!")
        else:
            await ctx.send("❌ Nothing is playing!")

    @commands.command(name='resume', help='Resume the current song')
    async def resume(self, ctx):
        """Resume playback"""
        if ctx.voice_client and ctx.voice_client.is_paused():
            ctx.voice_client.resume()
            await ctx.send("▶️ Resumed!")
        else:
            await ctx.send("❌ Nothing is paused!")

    @commands.command(name='stop', help='Stop the music and clear queue')
    async def stop(self, ctx):
        """Stop playback and clear queue"""
        self.queue = []
        self.is_playing = False
        if ctx.voice_client and ctx.voice_client.is_playing():
            ctx.voice_client.stop()
        await ctx.send("⏹️ Stopped!")

    @commands.command(name='queue', help='Show the current queue')
    async def queue_cmd(self, ctx):
        """Display the queue"""
        if not self.queue and not self.now_playing:
            await ctx.send("❌ Queue is empty!")
            return

        embed = discord.Embed(title="🎵 Music Queue", color=discord.Color.purple())
        if self.now_playing:
            embed.add_field(name="Now Playing", value=self.now_playing['title'], inline=False)
        
        if self.queue:
            for i, song in enumerate(self.queue[:10], 1):
                embed.add_field(name=f"{i}. {song['title']}", value="⠀", inline=False)
            
            if len(self.queue) > 10:
                embed.add_field(name="And more...", value=f"{len(self.queue) - 10} more songs", inline=False)
        
        await ctx.send(embed=embed)

    @commands.command(name='volume', help='Set the volume (0-100)')
    async def volume(self, ctx, vol: int):
        """Adjust volume"""
        if not 0 <= vol <= 100:
            await ctx.send("❌ Volume must be between 0 and 100!")
            return
        
        if ctx.voice_client and ctx.voice_client.source:
            ctx.voice_client.source.volume = vol / 100
            await ctx.send(f"🔊 Volume set to {vol}%")
        else:
            await ctx.send("❌ Nothing is playing!")

    @commands.command(name='leave', help='Leave the voice channel')
    async def leave(self, ctx):
        """Disconnect from voice channel"""
        self.queue = []
        self.is_playing = False
        if ctx.voice_client:
            await ctx.voice_client.disconnect()
            await ctx.send("👋 Left voice channel!")
        else:
            await ctx.send("❌ Not in a voice channel!")

@bot.event
async def on_ready():
    print(f'{bot.user} has connected to Discord!')
    print('------')
    print('Commands:')
    print('!play [song/URL] - Play a song')
    print('!skip - Skip current song')
    print('!pause - Pause playback')
    print('!resume - Resume playback')
    print('!stop - Stop and clear queue')
    print('!queue - Show queue')
    print('!volume [0-100] - Set volume')
    print('!leave - Leave voice channel')
    print('------')

async def main():
    token = os.getenv('DISCORD_TOKEN')
    if not token:
        print("ERROR: DISCORD_TOKEN environment variable not set!")
        return
    
    async with bot:
        await bot.add_cog(Music(bot))
        await bot.start(token)

if __name__ == '__main__':
    asyncio.run(main())
