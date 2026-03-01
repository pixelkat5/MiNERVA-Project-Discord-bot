import discord
import aiohttp
import asyncio
import os
import re

TOKEN = os.environ["DISCORD_TOKEN"]
URL = "https://minerva-archive.org/"
LEADERBOARD_API = "https://minerva-archive.org/api/leaderboard"

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

# Cache
_leaderboard_cache = None
_leaderboard_cache_time = 0
CACHE_TTL = 300  # 5 minutes

async def check_site():
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(URL, timeout=aiohttp.ClientTimeout(total=10)) as response:
                return response.status == 200
    except Exception:
        return False

async def fetch_all_leaderboard():
    global _leaderboard_cache, _leaderboard_cache_time
    import time
    if _leaderboard_cache and (time.time() - _leaderboard_cache_time) < CACHE_TTL:
        return _leaderboard_cache
    entries = []
    offset = 0
    limit = 25
    async with aiohttp.ClientSession() as session:
        while True:
            url = f"{LEADERBOARD_API}?limit={limit}&offset={offset}"
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as response:
                if response.status != 200:
                    break
                batch = await response.json()
                if not batch:
                    break
                entries.extend(batch)
                if len(batch) < limit:
                    break
                offset += limit
    import time
    _leaderboard_cache = entries
    _leaderboard_cache_time = time.time()
    return entries

def bytes_to_human(b):
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if b < 1024:
            return f"{b:.2f} {unit}"
        b /= 1024
    return f"{b:.2f} PB"

def find_user(entries, username):
    username = username.lower()
    for e in entries:
        if e["discord_username"].lower() == username:
            return e
    return None

def find_user_with_fallback(entries, member):
    entry = find_user(entries, member.display_name)
    if not entry:
        entry = find_user(entries, member.name)
    return entry

def build_leaderboard_page(entries, page, per_page=10):
    total_pages = (len(entries) + per_page - 1) // per_page
    start = (page - 1) * per_page
    page_entries = entries[start:start + per_page]
    lines = [f"**Leaderboard - Page {page}/{total_pages}**"]
    for e in page_entries:
        lines.append(f"#{e['rank']} **{e['discord_username']}** - {e['total_files']:,} files, {bytes_to_human(e['total_bytes'])}")
    return "\n".join(lines), total_pages

class LeaderboardView(discord.ui.View):
    def __init__(self, entries, page, total_pages, author_id):
        super().__init__(timeout=60)
        self.entries = entries
        self.page = page
        self.total_pages = total_pages
        self.author_id = author_id
        self.update_buttons()

    def update_buttons(self):
        self.prev_button.disabled = self.page <= 1
        self.next_button.disabled = self.page >= self.total_pages

    @discord.ui.button(label="< Prev", style=discord.ButtonStyle.secondary)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author_id:
            await interaction.response.defer()
            return
        self.page -= 1
        self.update_buttons()
        content, _ = build_leaderboard_page(self.entries, self.page)
        await interaction.response.edit_message(content=content, view=self)

    @discord.ui.button(label="Next >", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author_id:
            await interaction.response.defer()
            return
        self.page += 1
        self.update_buttons()
        content, _ = build_leaderboard_page(self.entries, self.page)
        await interaction.response.edit_message(content=content, view=self)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True

HELP_TEXT = (
    "**Minerva Bot Commands:**\n"
    "`!ping` - Check bot latency\n"
    "`!status` - Check if the site is up\n"
    "`!time` - Time left until Myrient deadline\n"
    "`!sheet` - Link to the tracking spreadsheet\n"
    "`!remind 1h30m (message)` - Set a reminder (supports h/m/s, max 24h)\n"
    "`!rank` - See your leaderboard rank\n"
    "`!rank (username)` - See someone else's rank\n"
    "`!rank list` - Browse the leaderboard with buttons\n"
    "`!rank data / !rank files` - Your data or file count\n"
    "`!rank data (user) / !rank files (user)` - Someone else's data or file count\n"
    "`!stats` - See your full stats\n"
    "`!stats files` - See your file count\n"
    "`!stats data` - See your data amount\n"
    "`!help / !list / !cmd / !command` - Show this list\n"
)

@client.event
async def on_ready():
    print(f"Logged in as {client.user}")

@client.event
async def on_message(message):
    if message.author == client.user:
        return

    content = message.content.lower().strip()
    parts = message.content.strip().split(None, 1)
    command = parts[0].lower() if parts else ""
    arg = parts[1].strip() if len(parts) > 1 else None

    # !ping
    if content == "!ping":
        latency = round(client.latency * 1000)
        await message.reply(f"Pong! `{latency}ms`")
        return

    # !status
    if content == "!status":
        is_up = await check_site()
        if is_up:
            await message.reply("The site is up.")
        else:
            await message.reply("The site is down.")
        return

    # !time
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

    # !sheet
    if content == "!sheet":
        await message.reply("https://docs.google.com/spreadsheets/d/1FYHw-QYXnKFuzUhIZCIe3mmg8HR7ftg2sV9Ec_9cDwU/")
        return

    # !remind
    if command == "!remind":
        if not arg:
            await message.reply("Usage: `!remind 1h30m do the thing` - supports h, m, s")
            return
        time_match = re.match(r'^((?:\d+h)?(?:\d+m)?(?:\d+s)?)\s*(.*)?$', arg.strip(), re.IGNORECASE)
        if not time_match or not time_match.group(1):
            await message.reply("Couldn't parse that time. Try something like `!remind 1h`, `!remind 30m`, `!remind 1h30m10s`")
            return
        time_str = time_match.group(1)
        remind_msg = time_match.group(2).strip() if time_match.group(2) else None
        hours = int(h.group(1)) if (h := re.search(r'(\d+)h', time_str, re.I)) else 0
        minutes = int(m.group(1)) if (m := re.search(r'(\d+)m', time_str, re.I)) else 0
        seconds = int(s.group(1)) if (s := re.search(r'(\d+)s', time_str, re.I)) else 0
        total_seconds = hours * 3600 + minutes * 60 + seconds
        if total_seconds <= 0:
            await message.reply("Time must be greater than 0.")
            return
        if total_seconds > 86400:
            await message.reply("Max reminder time is 24 hours.")
            return
        parts_str = []
        if hours: parts_str.append(f"{hours}h")
        if minutes: parts_str.append(f"{minutes}m")
        if seconds: parts_str.append(f"{seconds}s")
        await message.reply(f"Got it! Reminding you in {''.join(parts_str)}.")
        async def send_reminder():
            await asyncio.sleep(total_seconds)
            reminder_text = f"{message.author.mention}, reminder!"
            if remind_msg:
                reminder_text += f" {remind_msg}"
            await message.channel.send(reminder_text)
        asyncio.create_task(send_reminder())
        return

    # !rank
    if command == "!rank":
        try:
            entries = await fetch_all_leaderboard()
        except Exception:
            await message.reply("Couldn't reach the leaderboard API right now.")
            return

        subcommand = None

        if arg:
            arg_parts = arg.split(None, 1)
            first = arg_parts[0].lower()
            if first == "list":
                page = 1
                if len(arg_parts) > 1 and arg_parts[1].isdigit():
                    page = int(arg_parts[1])
                page_content, total_pages = build_leaderboard_page(entries, page)
                view = LeaderboardView(entries, page, total_pages, message.author.id)
                await message.reply(page_content, view=view)
                return
            elif first in ["data", "files", "file"]:
                subcommand = first
                if len(arg_parts) > 1:
                    entry = find_user(entries, arg_parts[1])
                else:
                    entry = find_user_with_fallback(entries, message.author)
            else:
                entry = find_user(entries, arg)
        else:
            entry = find_user_with_fallback(entries, message.author)

        if not entry:
            await message.reply("Couldn't find that user on the leaderboard.")
            return

        if subcommand == "data":
            await message.reply(f"**{entry['discord_username']}** has archived **{bytes_to_human(entry['total_bytes'])}** of data.")
        elif subcommand in ["files", "file"]:
            await message.reply(f"**{entry['discord_username']}** has archived **{entry['total_files']:,} files**.")
        else:
            await message.reply(
                f"**{entry['discord_username']}** is rank **#{entry['rank']}** "
                f"with {entry['total_files']:,} files and {bytes_to_human(entry['total_bytes'])} archived."
            )
        return

    # !stats
    if command == "!stats":
        try:
            entries = await fetch_all_leaderboard()
        except Exception:
            await message.reply("Couldn't reach the leaderboard API right now.")
            return

        entry = find_user_with_fallback(entries, message.author)

        if not entry:
            await message.reply("Couldn't find you on the leaderboard.")
            return

        if arg and arg.lower() == "files":
            await message.reply(f"**{entry['discord_username']}** has archived **{entry['total_files']:,} files**.")
        elif arg and arg.lower() == "data":
            await message.reply(f"**{entry['discord_username']}** has archived **{bytes_to_human(entry['total_bytes'])}** of data.")
        else:
            await message.reply(
                f"Stats for **{entry['discord_username']}**:\n"
                f"Rank: **#{entry['rank']}**\n"
                f"Files: **{entry['total_files']:,}**\n"
                f"Data: **{bytes_to_human(entry['total_bytes'])}**"
            )
        return

    # !help / !list / !cmd / !command
    if content in ["!help", "!list", "!cmd", "!command"]:
        await message.reply(HELP_TEXT)
        return

    # Myrient shutdown question
    if "myrient" in content and any(re.search(r'\b' + re.escape(w) + r'\b', content) for w in ["shutdown", "shut down", "closing", "close", "end", "when"]):
        await message.reply("March 31st.")
        return

    # Good bot / bad bot / clanker - only if replying to the bot or mentioning it
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
