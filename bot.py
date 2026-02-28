import discord
import aiohttp
import os
import re

TOKEN = os.environ["DISCORD_TOKEN"]
URL = "https://minerva-archive.org/"

DOWN_KEYWORDS = [
    "down", "offline", "not working", "broken", "unreachable",
    "cant access", "can't access", "unavailable", "not loading",
    "isitdown", "is it down", "wont load", "won't load", "dead"
]

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

async def check_site():
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(URL, timeout=aiohttp.ClientTimeout(total=10)) as response:
                return response.status == 200
    except Exception:
        return False

@client.event
async def on_ready():
    print(f"Logged in as {client.user}")

@client.event
async def on_message(message):
    if message.author == client.user:
        return

    content = message.content.lower()

    if any(re.search(r'\b' + re.escape(keyword) + r'\b', content) for keyword in DOWN_KEYWORDS):
        is_up = await check_site()
        if not is_up:
            await message.reply("Be patient -_-")

client.run(TOKEN)
