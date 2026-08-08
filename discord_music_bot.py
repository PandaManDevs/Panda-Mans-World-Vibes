import discord
from discord.ext import commands
import yt_dlp
import asyncio
from typing import Optional

# Configure bot with intents
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(command_prefix='!', intents=intents)

# YouTube DL configuration
ytdl_format_options = {
    'format': 'bestaudio/best',
    'noplaylist': True,
    'default_search': 'ytsearch',
    'quiet': True,
    'no_warnings': True,
    'extractaudio': True,
    'audioformat': 'mp3',
}

ytdl = yt_dlp.YoutubeDL(ytdl_format_options)

class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get('title')
        self.url = data.get('url')

    @classmethod
    async def from_url(cls, url, *, loop=None):
        loop = loop or asyncio.get_event_loop()
        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=False))
        if 'entries' in data:
            data = data['entries'][0]
        filename = data['url']
        return cls(discord.FFmpegPCMAudio(filename, before_options='-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5', options='-vn'), data=data)

class Music(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.queue = []
        self.now_playing = None

    async def cog_before_invoke(self, ctx):
        if ctx.voice_client is None:
            if ctx.author.voice:
                await ctx.author.voice.channel.connect()
            else:
                raise commands.CommandError("Author not connected to voice channel.")

    async def play_next(self, ctx):
        if self.queue:
            self.now_playing = self.queue.pop(0)
            try:
                source = await YTDLSource.from_url(self.now_playing['url'], loop=self.bot.loop)
                ctx.voice_client.play(source, after=lambda e: asyncio.run_coroutine_threadsafe(self.play_next(ctx), self.bot.loop))
                embed = discord.Embed(title="Now Playing", description=self.now_playing['title'], color=discord.Color.green())
                embed.add_field(name="Duration", value=self.now_playing.get('duration', 'N/A'), inline=False)
                await ctx.send(embed=embed)
            except Exception as e:
                await ctx.send(f"Error playing: {str(e)}")
                await self.play_next(ctx)
        else:
            self.now_playing = None

    @commands.command(name='play', help='Play a song from YouTube')
    async def play(self, ctx, *, search: str):
        """Play a song by search query or URL"""
        if ctx.voice_client is None:
            if ctx.author.voice:
                await ctx.author.voice.channel.connect()
            else:
                await ctx.send("You need to be in a voice channel!")
                return

        async with ctx.typing():
            try:
                # Extract info
                data = await asyncio.get_event_loop().run_in_executor(None, lambda: ytdl.extract_info(search, download=False))
                if 'entries' in data:
                    data = data['entries'][0]
                
                song_info = {
                    'url': data['url'],
                    'title': data.get('title', 'Unknown Title'),
                    'duration': data.get('duration', 0)
                }

                if ctx.voice_client.is_playing():
                    self.queue.append(song_info)
                    embed = discord.Embed(title="Added to Queue", description=song_info['title'], color=discord.Color.blue())
                    embed.add_field(name="Position", value=f"#{len(self.queue)}", inline=False)
                    await ctx.send(embed=embed)
                else:
                    await self.play_next(ctx)

            except Exception as e:
                await ctx.send(f"Error: {str(e)}")

    @commands.command(name='skip', help='Skip the current song')
    async def skip(self, ctx):
        """Skip current song"""
        if ctx.voice_client and ctx.voice_client.is_playing():
            ctx.voice_client.stop()
            await ctx.send("⏭️ Skipped!")
        else:
            await ctx.send("Nothing is playing!")

    @commands.command(name='pause', help='Pause the current song')
    async def pause(self, ctx):
        """Pause playback"""
        if ctx.voice_client and ctx.voice_client.is_playing():
            ctx.voice_client.pause()
            await ctx.send("⏸️ Paused!")
        else:
            await ctx.send("Nothing is playing!")

    @commands.command(name='resume', help='Resume the current song')
    async def resume(self, ctx):
        """Resume playback"""
        if ctx.voice_client and ctx.voice_client.is_paused():
            ctx.voice_client.resume()
            await ctx.send("▶️ Resumed!")
        else:
            await ctx.send("Nothing is paused!")

    @commands.command(name='stop', help='Stop the music and clear queue')
    async def stop(self, ctx):
        """Stop playback and clear queue"""
        self.queue = []
        if ctx.voice_client and ctx.voice_client.is_playing():
            ctx.voice_client.stop()
        await ctx.send("⏹️ Stopped!")

    @commands.command(name='queue', help='Show the current queue')
    async def queue_cmd(self, ctx):
        """Display the queue"""
        if not self.queue:
            await ctx.send("Queue is empty!")
            return

        embed = discord.Embed(title="Music Queue", color=discord.Color.purple())
        if self.now_playing:
            embed.add_field(name="Now Playing", value=self.now_playing['title'], inline=False)
        
        for i, song in enumerate(self.queue[:10], 1):
            embed.add_field(name=f"{i}. {song['title']}", value="⠀", inline=False)
        
        if len(self.queue) > 10:
            embed.add_field(name="And more...", value=f"{len(self.queue) - 10} more songs", inline=False)
        
        await ctx.send(embed=embed)

    @commands.command(name='volume', help='Set the volume (0-100)')
    async def volume(self, ctx, vol: int):
        """Adjust volume"""
        if not 0 <= vol <= 100:
            await ctx.send("Volume must be between 0 and 100!")
            return
        
        if ctx.voice_client and ctx.voice_client.source:
            ctx.voice_client.source.volume = vol / 100
            await ctx.send(f"🔊 Volume set to {vol}%")
        else:
            await ctx.send("Nothing is playing!")

    @commands.command(name='leave', help='Leave the voice channel')
    async def leave(self, ctx):
        """Disconnect from voice channel"""
        self.queue = []
        if ctx.voice_client:
            await ctx.voice_client.disconnect()
            await ctx.send("👋 Left voice channel!")
        else:
            await ctx.send("Not in a voice channel!")

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

async def main():
    async with bot:
        await bot.add_cog(Music(bot))
        await bot.start('MTUzMzIwOTUxMzA5MDM1MTMyNg.GuEXII.iseknYOGjMQikWJG6y5kiyFMSciGupib83r4sw')

if __name__ == '__main__':
    asyncio.run(main())
