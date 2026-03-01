import discord
from discord.ext import commands
from discord import app_commands
import aiohttp
import asyncio
import os
import re
import time
import datetime

TOKEN = os.environ["DISCORD_TOKEN"]
URL = "https://minerva-archive.org/"
API_URL = "https://api.minerva-archive.org"
GATE_URL = "https://gate.minerva-archive.org"
LEADERBOARD_API = "https://minerva-archive.org/api/leaderboard"

COMMANDS_CHANNEL_ID = 1477718885502292164
ALLOWED_ROLES = {"Project Lead", "Manager", "LORD HOARDER", "Moderator", "Developer"}

DOWN_KEYWORDS = [
    "down", "offline", "not working", "broken", "unreachable",
    "cant access", "can't access", "unavailable", "not loading",
    "wont load", "won't load", "dead"
]

UP_KEYWORDS = [
    "up", "working", "online", "accessible"
]

SITE_KEYWORDS = [
    "site", "minerva", "archive", "page", "website", "server"
]

_leaderboard_cache = None
_leaderboard_cache_time = 0
CACHE_TTL = 300

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

async def check_channel(ctx):
    # allow DMs
    if isinstance(ctx.channel, discord.DMChannel):
        return True
    if ctx.channel.id == COMMANDS_CHANNEL_ID:
        return True
    if any(r.name in ALLOWED_ROLES for r in getattr(ctx.author, 'roles', [])):
        return True
    await ctx.reply(f"Commands can only be used in <#1477718885502292164>.", ephemeral=True)
    return False

async def check_site():
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(URL, timeout=aiohttp.ClientTimeout(total=10)) as response:
                return response.status == 200
    except Exception:
        return False

async def check_url(url):
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as response:
                return response.status < 500
    except Exception:
        return False

async def fetch_all_leaderboard():
    global _leaderboard_cache, _leaderboard_cache_time
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
    _leaderboard_cache = entries
    _leaderboard_cache_time = time.time()
    return entries

async def fetch_leaderboard_fresh():
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

def parse_time_str(s):
    h = int(m.group(1)) if (m := re.search(r'(\d+)h', s, re.I)) else 0
    mins = int(m.group(1)) if (m := re.search(r'(\d+)m', s, re.I)) else 0
    secs = int(m.group(1)) if (m := re.search(r'(\d+)s', s, re.I)) else 0
    return h * 3600 + mins * 60 + secs

HELP_TEXT = (
    "**Minerva Bot Commands:**\n"
    "`!ping` - Check bot latency\n"
    "`!status` - Check if all services are up\n"
    "`!time` - Time left until Myrient deadline\n"
    "`!sheet` - Link to the tracking spreadsheet\n"
    "`!python / !bot` - Link to the bot source\n"
    "`!source / !sourcecode` - Link to the bot GitHub repo\n"
    "`!remind 1h30m (message)` - Set a reminder (supports h/m/s, max 24h)\n"
    "`!listen 2m 30s` - Track your data uploads over time (DMs you updates)\n"
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
            await interaction.response.send_message("These buttons are only for the person who ran this command.", ephemeral=True)
            return
        self.page -= 1
        self.update_buttons()
        content, _ = build_leaderboard_page(self.entries, self.page)
        await interaction.response.edit_message(content=content, view=self)

    @discord.ui.button(label="Next >", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("These buttons are only for the person who ran this command.", ephemeral=True)
            return
        self.page += 1
        self.update_buttons()
        content, _ = build_leaderboard_page(self.entries, self.page)
        await interaction.response.edit_message(content=content, view=self)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True

@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"Logged in as {bot.user}")

@bot.hybrid_command(name="ping", description="Check bot latency")
async def ping(ctx):
    if not await check_channel(ctx): return
    latency = round(bot.latency * 1000)
    await ctx.reply(f"Pong! `{latency}ms`")

@bot.hybrid_command(name="status", description="Check if all services are up")
async def status(ctx):
    if not await check_channel(ctx): return
    await ctx.defer()
    site, api, gate = await asyncio.gather(
        check_url(URL),
        check_url(API_URL),
        check_url(GATE_URL),
    )
    def mark(up): return "🟢" if up else "🔴"
    await ctx.reply(
        f"minerva-archive.org: {mark(site)}\n"
        f"api.minerva-archive.org: {mark(api)}\n"
        f"gate.minerva-archive.org: {mark(gate)}"
    )

@bot.hybrid_command(name="time", description="Time left until Myrient deadline")
async def time_cmd(ctx):
    if not await check_channel(ctx): return
    deadline = datetime.datetime(2025, 3, 31, tzinfo=datetime.timezone.utc)
    now = datetime.datetime.now(datetime.timezone.utc)
    if now < deadline:
        timestamp = int(deadline.timestamp())
        await ctx.reply(f"Myrient deadline: <t:{timestamp}:F> (<t:{timestamp}:R>)")
    else:
        await ctx.reply("The deadline has already passed.")

@bot.hybrid_command(name="sheet", description="Link to the tracking spreadsheet")
async def sheet(ctx):
    if not await check_channel(ctx): return
    await ctx.reply("https://docs.google.com/spreadsheets/d/1FYHw-QYXnKFuzUhIZCIe3mmg8HR7ftg2sV9Ec_9cDwU/")

@bot.hybrid_command(name="python", description="Link to the bot source code")
async def python_cmd(ctx):
    if not await check_channel(ctx): return
    await ctx.reply("https://gist.github.com/rlaphoenix/257b7aa65adacc154d8b5fa0b035b1e8")

@bot.command(name="bot")
async def bot_cmd(ctx):
    if not await check_channel(ctx): return
    await ctx.reply("https://gist.github.com/rlaphoenix/257b7aa65adacc154d8b5fa0b035b1e8")

@bot.hybrid_command(name="source", description="Link to the bot GitHub repo")
async def source_cmd(ctx):
    if not await check_channel(ctx): return
    await ctx.reply("https://github.com/pixelkat5/MiNERVA-Project-Discord-bot")

@bot.command(name="sourcecode")
async def sourcecode_cmd(ctx):
    if not await check_channel(ctx): return
    await ctx.reply("https://github.com/pixelkat5/MiNERVA-Project-Discord-bot")

@bot.hybrid_command(name="remind", description="Set a reminder")
@app_commands.describe(reminder="e.g. 1h30m do the thing")
async def remind(ctx, *, reminder: str):
    if not await check_channel(ctx): return
    time_match = re.match(r'^((?:\d+h)?(?:\d+m)?(?:\d+s)?)\s*(.*)?$', reminder.strip(), re.IGNORECASE)
    if not time_match or not time_match.group(1):
        await ctx.reply("Couldn't parse that time. Try something like `!remind 1h`, `!remind 30m`, `!remind 1h30m10s`")
        return
    time_str = time_match.group(1)
    remind_msg = time_match.group(2).strip() if time_match.group(2) else None
    hours = int(h.group(1)) if (h := re.search(r'(\d+)h', time_str, re.I)) else 0
    minutes = int(m.group(1)) if (m := re.search(r'(\d+)m', time_str, re.I)) else 0
    seconds = int(s.group(1)) if (s := re.search(r'(\d+)s', time_str, re.I)) else 0
    total_seconds = hours * 3600 + minutes * 60 + seconds
    if total_seconds <= 0:
        await ctx.reply("Time must be greater than 0.")
        return
    if total_seconds > 86400:
        await ctx.reply("Max reminder time is 24 hours.")
        return
    parts_str = []
    if hours: parts_str.append(f"{hours}h")
    if minutes: parts_str.append(f"{minutes}m")
    if seconds: parts_str.append(f"{seconds}s")
    await ctx.reply(f"Got it! Reminding you in {''.join(parts_str)}.")
    async def send_reminder():
        await asyncio.sleep(total_seconds)
        reminder_text = f"{ctx.author.mention}, reminder!"
        if remind_msg:
            reminder_text += f" {remind_msg}"
        await ctx.channel.send(reminder_text)
    asyncio.create_task(send_reminder())

@bot.hybrid_command(name="listen", description="Track your data uploads over time via DM")
@app_commands.describe(args="duration and optional interval e.g. '2m 30s'")
async def listen(ctx, *, args: str = None):
    if not await check_channel(ctx): return
    if not args:
        await ctx.reply("Usage: `!listen 2m` or `!listen 2m 30s`", ephemeral=True)
        return

    args = re.sub(r'^self\s+', '', args.strip(), flags=re.IGNORECASE)
    time_tokens = re.findall(r'\d+[hms]', args, re.IGNORECASE)

    if not time_tokens:
        await ctx.reply("Couldn't parse a duration. Try `!listen 2m` or `!listen 2m 30s`", ephemeral=True)
        return

    duration = parse_time_str(time_tokens[0])
    interval = parse_time_str(time_tokens[1]) if len(time_tokens) > 1 else 30

    if duration <= 0 or interval <= 0:
        await ctx.reply("Duration and interval must be greater than 0.", ephemeral=True)
        return
    if duration > 3600:
        await ctx.reply("Max listen duration is 1 hour.", ephemeral=True)
        return
    if interval < 10:
        await ctx.reply("Minimum interval is 10 seconds.", ephemeral=True)
        return

    try:
        entries = await fetch_all_leaderboard()
    except Exception:
        await ctx.reply("Couldn't reach the leaderboard API right now.", ephemeral=True)
        return

    entry = find_user_with_fallback(entries, ctx.author)
    if not entry:
        await ctx.reply("Couldn't find you on the leaderboard.", ephemeral=True)
        return

    username = entry['discord_username']
    initial_bytes = entry['total_bytes']
    snapshots = [
        f"Tracking **{username}** for {duration}s, checking every {interval}s.",
        f"**Start:** {bytes_to_human(initial_bytes)}",
    ]

    try:
        dm = await ctx.author.send("\n".join(snapshots))
    except discord.Forbidden:
        await ctx.reply("I couldn't DM you. Please enable DMs from server members.", ephemeral=True)
        return

    await ctx.reply("Check your DMs for live updates!", ephemeral=True)

    elapsed = 0
    prev_bytes = initial_bytes
    while elapsed < duration:
        await asyncio.sleep(interval)
        elapsed += interval
        try:
            fresh = await fetch_leaderboard_fresh()
            e = find_user(fresh, username)
            if e:
                diff = e['total_bytes'] - prev_bytes
                sign = "+" if diff >= 0 else "-"
                diff_str = f"{sign}{bytes_to_human(abs(diff))}"
                snapshots.append(f"**t+{elapsed}s:** {bytes_to_human(e['total_bytes'])} ({diff_str})")
                prev_bytes = e['total_bytes']
        except Exception:
            snapshots.append(f"**t+{elapsed}s:** (fetch failed)")

        new_content = "\n".join(snapshots)
        if len(new_content) > 1900:
            snapshots = [f"*(continued)*"]
            dm = await ctx.author.send("\n".join(snapshots))
        else:
            await dm.edit(content=new_content)

    snapshots.append("**Done!**")
    new_content = "\n".join(snapshots)
    if len(new_content) > 1900:
        await ctx.author.send("**Done!**")
    else:
        await dm.edit(content=new_content)

@bot.hybrid_command(name="rank", description="See leaderboard rank")
@app_commands.describe(args="username, 'list', 'data', or 'files' optionally followed by username")
async def rank(ctx, *, args: str = None):
    if not await check_channel(ctx): return
    await ctx.defer()
    try:
        entries = await fetch_all_leaderboard()
    except Exception:
        await ctx.reply("Couldn't reach the leaderboard API right now.")
        return

    subcommand = None

    if args:
        arg_parts = args.split(None, 1)
        first = arg_parts[0].lower()
        if first == "list":
            page = 1
            if len(arg_parts) > 1 and arg_parts[1].isdigit():
                page = int(arg_parts[1])
            page_content, total_pages = build_leaderboard_page(entries, page)
            view = LeaderboardView(entries, page, total_pages, ctx.author.id)
            await ctx.reply(page_content, view=view)
            return
        elif first in ["data", "files", "file"]:
            subcommand = first
            if len(arg_parts) > 1:
                entry = find_user(entries, arg_parts[1])
            else:
                entry = find_user_with_fallback(entries, ctx.author)
        else:
            entry = find_user(entries, args)
    else:
        entry = find_user_with_fallback(entries, ctx.author)

    if not entry:
        await ctx.reply("Couldn't find that user on the leaderboard.")
        return

    if subcommand == "data":
        await ctx.reply(f"**{entry['discord_username']}** has archived **{bytes_to_human(entry['total_bytes'])}** of data.")
    elif subcommand in ["files", "file"]:
        await ctx.reply(f"**{entry['discord_username']}** has archived **{entry['total_files']:,} files**.")
    else:
        await ctx.reply(
            f"**{entry['discord_username']}** is rank **#{entry['rank']}** "
            f"with {entry['total_files']:,} files and {bytes_to_human(entry['total_bytes'])} archived."
        )

@bot.hybrid_command(name="stats", description="See your archive stats")
@app_commands.describe(filter="'files' or 'data'")
async def stats(ctx, filter: str = None):
    if not await check_channel(ctx): return
    await ctx.defer()
    try:
        entries = await fetch_all_leaderboard()
    except Exception:
        await ctx.reply("Couldn't reach the leaderboard API right now.")
        return

    entry = find_user_with_fallback(entries, ctx.author)

    if not entry:
        await ctx.reply("Couldn't find you on the leaderboard.")
        return

    if filter and filter.lower() == "files":
        await ctx.reply(f"**{entry['discord_username']}** has archived **{entry['total_files']:,} files**.")
    elif filter and filter.lower() == "data":
        await ctx.reply(f"**{entry['discord_username']}** has archived **{bytes_to_human(entry['total_bytes'])}** of data.")
    else:
        await ctx.reply(
            f"Stats for **{entry['discord_username']}**:\n"
            f"Rank: **#{entry['rank']}**\n"
            f"Files: **{entry['total_files']:,}**\n"
            f"Data: **{bytes_to_human(entry['total_bytes'])}**"
        )

@bot.hybrid_command(name="help", description="Show all commands")
async def help_cmd(ctx):
    if not await check_channel(ctx): return
    await ctx.reply(HELP_TEXT)

@bot.command(name="list")
async def list_cmd(ctx):
    if not await check_channel(ctx): return
    await ctx.reply(HELP_TEXT)

@bot.command(name="cmd")
async def cmd_cmd(ctx):
    if not await check_channel(ctx): return
    await ctx.reply(HELP_TEXT)

@bot.command(name="command")
async def command_cmd(ctx):
    if not await check_channel(ctx): return
    await ctx.reply(HELP_TEXT)

@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    await bot.process_commands(message)

    content = message.content.lower().strip()

    # Myrient shutdown question
    if "myrient" in content and any(re.search(r'\b' + re.escape(w) + r'\b', content) for w in ["shutdown", "shut down", "closing", "close", "end", "when"]):
        await message.reply("March 31st.")
        return

    # Good bot / bad bot / clanker - only if replying to the bot or mentioning it
    is_bot_referenced = (
        (message.reference and message.reference.resolved and message.reference.resolved.author == bot.user)
        or bot.user in message.mentions
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
        try:
            if is_up:
                await message.add_reaction("✅")
            else:
                await message.add_reaction("❌")
        except discord.NotFound:
            pass

bot.run(TOKEN)
