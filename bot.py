import discord
import aiohttp
import os
import re

TOKEN = os.environ["DISCORD_TOKEN"]
URL = "https://minerva-archive.org/"

# Primary trigger words
DOWN_KEYWORDS = [
    "down", "offline", "not working", "broken", "unreachable",
    "cant access", "can't access", "unavailable", "not loading",
    "wont load", "won't load", "dead"
]

# Up keywords
UP_KEYWORDS = [
    "up", "working", "online", "accessible"
]

# Must also contain one of these to confirm they're talking about the site
SITE_KEYWORDS = [
    "site", "minerva", "archive", "page", "website", "server"
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

    # Commands
    if content == "!ping":
        latency = round(client.latency * 1000)
        await message.reply(f"Current ping is around `{latency}ms`")
        return

    if content == "!time":
        import datetime
        deadline = datetime.datetime(2025, 3, 31, tzinfo=datetime.timezone.utc)
        now = datetime.datetime.now(datetime.timezone.utc)
        if now < deadline:
            timestamp = int(deadline.timestamp())
            await message.reply(f"Myrient deadline: <t:{timestamp}:F> (<t:{timestamp}:R>)")
        else:
            await message.reply("The deadline has already passed.")
        return

    if content == "!status":
        is_up = await check_site()
        if is_up:
            await message.reply("✅ The site is up!")
        else:
            await message.reply("❌ The site is down!")
        return

    # Myrient shutdown
    if "myrient" in content and any(w in content for w in ["shutdown", "shut down", "closing", "close", "end", "when"]):
        await message.reply("March 31st.")
        return

    # Auto-remove links
    if re.search(r'https?://|www\.', message.content):
        await message.delete()
        await message.channel.send(f"{message.author.mention}, sorry but we autoremove links to prevent piracy and our server getting taken down.")
        return

    # Good bot / bad bot / clanker — only if replying to the bot or mentioning it
    is_bot_referenced = (
        (message.reference and message.reference.resolved and message.reference.resolved.author == client.user)
        or client.user in message.mentions
    )

    if is_bot_referenced:
        if "good bot" in content:
            await message.reply("thank you :)")
            return
        elif "bad bot" in content:
            await message.reply("sorry...")
            return
        elif "clanker" in content or "hate" in content:
            await message.reply(":(")
            return
        elif "love" in content:
            await message.reply("awww thanks <3")
            return
        elif content.strip() in ["hi", "hello", "hey", "hiya"]:
            await message.reply("hello :)")
            return

    # Site status keywords
    has_site_keyword = any(re.search(r'\b' + re.escape(kw) + r'\b', content) for kw in SITE_KEYWORDS)

    if not has_site_keyword:
        return

    has_down_keyword = any(re.search(r'\b' + re.escape(kw) + r'\b', content) for kw in DOWN_KEYWORDS)
    has_up_keyword = any(re.search(r'\b' + re.escape(kw) + r'\b', content) for kw in UP_KEYWORDS)

    is_up = await check_site()

    if has_down_keyword:
        if not is_up:
            await message.reply("Be patient -_-")

    elif has_up_keyword:
        if is_up:
            await message.add_reaction("✅")
        else:
            await message.add_reaction("❌")

client.run(TOKEN)
