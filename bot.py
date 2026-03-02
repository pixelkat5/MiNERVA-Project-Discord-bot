import discord
from discord.ext import commands
from discord import app_commands
import aiohttp
import asyncio
import os
import re
import time
import datetime
import zoneinfo

# ---- CONFIG ----

TOKEN = os.environ["DISCORD_TOKEN"]

ENDPOINTS = [
    ("minerva-archive.org",              "https://minerva-archive.org/"),
    ("api.minerva-archive.org",          "https://api.minerva-archive.org"),
    ("gate.minerva-archive.org",         "https://gate.minerva-archive.org"),
    ("gate.minerva-archive.org/api/upload", "https://gate.minerva-archive.org/api/upload"),
    ("api.minerva-archive.org/api/jobs", "https://api.minerva-archive.org/api/jobs?count=4"),
]

LEADERBOARD_API = "https://minerva-archive.org/api/leaderboard"
GIST_URL        = "https://gist.github.com/rlaphoenix/257b7aa65adacc154d8b5fa0b035b1e8"
GIST_RAW_URL    = "https://gist.githubusercontent.com/rlaphoenix/257b7aa65adacc154d8b5fa0b035b1e8/raw"
SHEET_URL       = "https://docs.google.com/spreadsheets/d/1FYHw-QYXnKFuzUhIZCIe3mmg8HR7ftg2sV9Ec_9cDwU/"
SOURCE_URL      = "https://github.com/pixelkat5/MiNERVA-Project-Discord-bot"

COMMANDS_CHANNEL_ID = 1477718885502292164
ALLOWED_ROLES = {"Project Lead", "Manager", "LORD HOARDER", "Moderator", "Developer"}
CACHE_TTL = 300

DOWN_KEYWORDS = [
    "down", "offline", "not working", "broken", "unreachable",
    "cant access", "can't access", "unavailable", "not loading",
    "wont load", "won't load", "dead", "crashed", "crash",
    "error", "not responding", "timed out", "timeout"
]
UP_KEYWORDS   = ["up", "working", "online", "accessible"]
SITE_KEYWORDS = ["site", "minerva", "archive", "page", "website", "server"]

HELP_TEXT = (
    "**Minerva Bot Commands:**\n"
    "`!ping` - Check bot latency\n"
    "`!status` - Check if all services are up\n"
    "`!status fast 1-4` - Check a single endpoint\n"
    "`!time` - Time left until Myrient deadline\n"
    "`!sheet` - Link to the tracking spreadsheet\n"
    "`!python / !bot` - Link to the bot source\n"
    "`!source / !sourcecode` - Link to the bot GitHub repo\n"
    "`!script` - Show current upload script version\n"
    "`!script notify` - Toggle DM pings for script updates\n"
    "`!remind 1h30m (message)` - Set a reminder by duration\n"
    "`!remind 4/1/26` - Set a reminder by date (midnight in your timezone)\n"
    "`!timezone US/Central` - Set your timezone for date reminders\n"
    "`!listen 2m 30s` - Track your data uploads over time (DMs you updates)\n"
    "`!pfp (@user)` - Show a user's profile picture\n"
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

# ---- STATE ----

script_notify_users = set()
_last_known_version = None
user_timezones      = {}
_leaderboard_cache  = None
_leaderboard_cache_time = 0

# ---- BOT SETUP ----

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

# ---- HELPERS ----

def has_keyword(content, keywords):
    return any(re.search(r'\b' + re.escape(kw) + r'\b', content) for kw in keywords)

def bytes_to_human(b):
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if b < 1024:
            return f"{b:.2f} {unit}"
        b /= 1024
    return f"{b:.2f} PB"

def parse_time_str(s):
    h    = int(m.group(1)) if (m := re.search(r'(\d+)h', s, re.I)) else 0
    mins = int(m.group(1)) if (m := re.search(r'(\d+)m', s, re.I)) else 0
    secs = int(m.group(1)) if (m := re.search(r'(\d+)s', s, re.I)) else 0
    return h * 3600 + mins * 60 + secs

def format_duration(total_seconds):
    h, rem = divmod(int(total_seconds), 3600)
    m, s   = divmod(rem, 60)
    parts  = []
    if h: parts.append(f"{h}h")
    if m: parts.append(f"{m}m")
    if s: parts.append(f"{s}s")
    return "".join(parts)

def find_user(entries, username):
    return next((e for e in entries if e["discord_username"].lower() == username.lower()), None)

def find_user_with_fallback(entries, member):
    return find_user(entries, member.display_name) or find_user(entries, member.name)

def build_leaderboard_page(entries, page, per_page=10):
    total_pages = (len(entries) + per_page - 1) // per_page
    start = (page - 1) * per_page
    lines = [f"**Leaderboard - Page {page}/{total_pages}**"]
    for e in entries[start:start + per_page]:
        lines.append(f"#{e['rank']} **{e['discord_username']}** - {e['total_files']:,} files, {bytes_to_human(e['total_bytes'])}")
    return "\n".join(lines), total_pages

async def check_channel(ctx):
    if isinstance(ctx.channel, discord.DMChannel):
        return True
    if ctx.channel.id == COMMANDS_CHANNEL_ID:
        return True
    if any(r.name in ALLOWED_ROLES for r in getattr(ctx.author, 'roles', [])):
        return True
    await ctx.reply("Commands can only be used in <#1477718885502292164>.", ephemeral=True)
    return False

async def check_url(url):
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                return r.status < 500
    except Exception:
        return False

async def check_all_endpoints():
    return all(await asyncio.gather(*[check_url(url) for _, url in ENDPOINTS]))

async def fetch_leaderboard_pages():
    entries, offset, limit = [], 0, 25
    async with aiohttp.ClientSession() as session:
        while True:
            async with session.get(f"{LEADERBOARD_API}?limit={limit}&offset={offset}", timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status != 200: break
                batch = await r.json()
                if not batch: break
                entries.extend(batch)
                if len(batch) < limit: break
                offset += limit
    return entries

async def fetch_all_leaderboard():
    global _leaderboard_cache, _leaderboard_cache_time
    if _leaderboard_cache and (time.time() - _leaderboard_cache_time) < CACHE_TTL:
        return _leaderboard_cache
    entries = await fetch_leaderboard_pages()
    _leaderboard_cache, _leaderboard_cache_time = entries, time.time()
    return entries

async def fetch_script_version():
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(GIST_RAW_URL, timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status != 200: return None
                for line in (await r.text()).splitlines():
                    if line.strip().startswith("VERSION"):
                        if m := re.search(r'["\']([^"\']+)["\']', line):
                            return m.group(1)
    except Exception:
        pass
    return None

async def get_leaderboard_or_error(ctx):
    try:
        return await fetch_all_leaderboard()
    except Exception:
        await ctx.reply("Couldn't reach the leaderboard API right now.")
        return None

def make_link_command(name, url, description):
    @bot.hybrid_command(name=name, description=description)
    async def _cmd(ctx):
        if not await check_channel(ctx): return
        await ctx.reply(url)
    _cmd.__name__ = f"{name}_cmd"
    return _cmd

# ---- BACKGROUND TASKS ----

async def version_watcher():
    global _last_known_version
    await bot.wait_until_ready()
    while not bot.is_closed():
        version = await fetch_script_version()
        if version and _last_known_version and version != _last_known_version:
            for user_id in list(script_notify_users):
                try:
                    user = await bot.fetch_user(user_id)
                    await user.send(f"Script updated! New version: `{version}` (was `{_last_known_version}`)\n{GIST_URL}")
                except Exception:
                    pass
        if version:
            _last_known_version = version
        await asyncio.sleep(300)

async def status_watcher():
    await bot.wait_until_ready()
    last_status = None
    while not bot.is_closed():
        is_up = await check_all_endpoints()
        if is_up != last_status:
            await bot.change_presence(
                status=discord.Status.online if is_up else discord.Status.do_not_disturb,
                activity=discord.Activity(
                    type=discord.ActivityType.watching,
                    name=f"minerva-archive.org {'✅' if is_up else '🔴'}"
                )
            )
            last_status = is_up
        await asyncio.sleep(60)

# ---- UI ----

class LeaderboardView(discord.ui.View):
    def __init__(self, entries, page, total_pages, author_id):
        super().__init__(timeout=60)
        self.entries, self.page, self.total_pages, self.author_id = entries, page, total_pages, author_id
        self.update_buttons()

    def update_buttons(self):
        self.prev_button.disabled = self.page <= 1
        self.next_button.disabled = self.page >= self.total_pages

    async def _check_author(self, interaction):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("These buttons are only for the person who ran this command.", ephemeral=True)
            return False
        return True

    async def _flip(self, interaction, delta):
        if not await self._check_author(interaction): return
        self.page += delta
        self.update_buttons()
        content, _ = build_leaderboard_page(self.entries, self.page)
        await interaction.response.edit_message(content=content, view=self)

    @discord.ui.button(label="< Prev", style=discord.ButtonStyle.secondary)
    async def prev_button(self, interaction, button): await self._flip(interaction, -1)

    @discord.ui.button(label="Next >", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction, button): await self._flip(interaction, +1)

    async def on_timeout(self):
        for item in self.children: item.disabled = True

# ---- EVENTS ----

@bot.event
async def on_ready():
    await bot.tree.sync()
    bot.loop.create_task(version_watcher())
    bot.loop.create_task(status_watcher())
    print(f"Logged in as {bot.user}")

@bot.event
async def on_message(message):
    if message.author == bot.user:
        return
    await bot.process_commands(message)
    content = message.content.lower().strip()

    # Simple keyword responses — no channel restriction
    if "boing" in content:
        await message.reply("boing boing")
        return

    if "426" in content:
        await message.reply("Uploads are currently disabled -_-")
        return

    if "carl" in content:
        await message.reply("I hate that dude -3-")
        return

    if "love" in content and "minerva" in content:
        await message.reply("I love that dude1!11!1,.!!")
        return

    if "myrient" in content and has_keyword(content, ["shutdown", "shut down", "closing", "close", "end", "when"]):
        await message.reply("March 31st.")
        return

    is_bot_referenced = (
        (message.reference and message.reference.resolved and message.reference.resolved.author == bot.user)
        or bot.user in message.mentions
    )
    if is_bot_referenced:
        responses = {
            "good bot":  "thank you :)",
            "bad bot":   "sorry...",
        }
        for trigger, reply in responses.items():
            if trigger in content:
                await message.reply(reply)
                return
        if "clanker" in content or "hate" in content:
            await message.reply(":(")
        elif "love" in content:
            await message.reply("awww thanks <3")
        elif content.strip() in {"hi", "hello", "hey", "hiya"}:
            await message.reply("hello :)")
        return

    if not has_keyword(content, SITE_KEYWORDS):
        return

    is_up = await check_all_endpoints()
    if has_keyword(content, DOWN_KEYWORDS):
        if not is_up:
            await message.reply("Be patient -_-")
    elif has_keyword(content, UP_KEYWORDS):
        try:
            await message.add_reaction("✅" if is_up else "❌")
        except discord.NotFound:
            pass

# ---- COMMANDS ----

@bot.hybrid_command(name="ping", description="Check bot latency")
async def ping(ctx):
    if not await check_channel(ctx): return
    await ctx.reply(f"Pong! `{round(bot.latency * 1000)}ms`")

@bot.hybrid_command(name="status", description="Check if all services are up")
@app_commands.describe(mode="leave blank for full check, or 'fast 1-4' for a single endpoint")
async def status(ctx, mode: str = None, endpoint: int = None):
    if not await check_channel(ctx): return
    await ctx.defer()
    mark = lambda up: "🟢" if up else "🔴"
    if mode and mode.lower() == "fast" and endpoint:
        if not 1 <= endpoint <= 4:
            await ctx.reply("Endpoint must be 1-4.")
            return
        name, url = ENDPOINTS[endpoint - 1]
        await ctx.reply(f"{name}: {mark(await check_url(url))}")
    else:
        results = await asyncio.gather(*[check_url(url) for _, url in ENDPOINTS])
        await ctx.reply("\n".join(f"{name}: {mark(up)}" for (name, _), up in zip(ENDPOINTS, results)))

@bot.hybrid_command(name="time", description="Time left until Myrient deadline")
async def time_cmd(ctx):
    if not await check_channel(ctx): return
    deadline = datetime.datetime(2025, 3, 31, tzinfo=datetime.timezone.utc)
    now = datetime.datetime.now(datetime.timezone.utc)
    if now < deadline:
        ts = int(deadline.timestamp())
        await ctx.reply(f"Myrient deadline: <t:{ts}:F> (<t:{ts}:R>)")
    else:
        await ctx.reply("The deadline has already passed.")

# Link commands
make_link_command("sheet",  SHEET_URL,  "Link to the tracking spreadsheet")
make_link_command("source", SOURCE_URL, "Link to the bot GitHub repo")
make_link_command("python", GIST_URL,   "Link to the bot source code (gist)")

@bot.command(name="bot")
async def bot_cmd(ctx):
    if not await check_channel(ctx): return
    await ctx.reply(GIST_URL)

@bot.command(name="sourcecode")
async def sourcecode_cmd(ctx):
    if not await check_channel(ctx): return
    await ctx.reply(SOURCE_URL)

@bot.hybrid_command(name="script", description="Show script version or toggle update notifications")
@app_commands.describe(action="leave blank for version, or 'notify' to toggle update pings")
async def script(ctx, action: str = None):
    if not await check_channel(ctx): return
    if action and action.lower() == "notify":
        if ctx.author.id in script_notify_users:
            script_notify_users.discard(ctx.author.id)
            await ctx.reply("You'll no longer be pinged for script updates.", ephemeral=True)
        else:
            script_notify_users.add(ctx.author.id)
            await ctx.reply("You'll now be pinged via DM when the script updates.", ephemeral=True)
    else:
        version = await fetch_script_version()
        await ctx.reply(f"Current script version: `{version}`\n{GIST_URL}" if version else "Couldn't fetch the script version right now.")

@bot.hybrid_command(name="timezone", description="Set your timezone for date reminders")
@app_commands.describe(tz="e.g. US/Central, US/Eastern, US/Pacific, UTC")
async def timezone_cmd(ctx, tz: str):
    if not await check_channel(ctx): return
    try:
        zoneinfo.ZoneInfo(tz)
        user_timezones[ctx.author.id] = tz
        await ctx.reply(f"Timezone set to `{tz}`.", ephemeral=True)
    except Exception:
        await ctx.reply(f"Unknown timezone `{tz}`. Try `US/Central`, `US/Eastern`, `US/Pacific`, or `UTC`.", ephemeral=True)

@bot.hybrid_command(name="pfp", description="Show a user's profile picture")
@app_commands.describe(user="The user whose pfp to show (leave blank for yourself)")
async def pfp(ctx, user: discord.Member = None):
    if not await check_channel(ctx): return
    target = user or ctx.author
    embed = discord.Embed()
    embed.set_image(url=target.display_avatar.url)
    embed.set_footer(text=f"{target.display_name}'s profile picture")
    await ctx.reply(embed=embed)

@bot.hybrid_command(name="remind", description="Set a reminder by duration or date")
@app_commands.describe(reminder="e.g. 1h30m, 30m do the thing, or 4/1/26")
async def remind(ctx, *, reminder: str):
    if not await check_channel(ctx): return

    date_match = re.match(r'^(\d{1,2})/(\d{1,2})/(\d{2}|\d{4})$', reminder.strip())
    if date_match:
        month, day, year = int(date_match.group(1)), int(date_match.group(2)), int(date_match.group(3))
        if year < 100: year += 2000
        tz_name = user_timezones.get(ctx.author.id, "US/Central")
        try:
            tz = zoneinfo.ZoneInfo(tz_name)
            remind_dt = datetime.datetime(year, month, day, tzinfo=tz)
        except Exception:
            await ctx.reply("Invalid date.")
            return
        total_seconds = (remind_dt - datetime.datetime.now(tz=tz)).total_seconds()
        if total_seconds <= 0:
            await ctx.reply("That date is in the past.")
            return
        await ctx.reply(f"Got it! Reminding you on <t:{int(remind_dt.timestamp())}:D> at midnight {tz_name}.")
        async def send_date_reminder():
            await asyncio.sleep(total_seconds)
            await ctx.channel.send(f"{ctx.author.mention}, reminder! It's {month}/{day}/{year}.")
        asyncio.create_task(send_date_reminder())
        return

    time_match = re.match(r'^((?:\d+h)?(?:\d+m)?(?:\d+s)?)\s*(.*)?$', reminder.strip(), re.IGNORECASE)
    if not time_match or not time_match.group(1):
        await ctx.reply("Couldn't parse that. Try `!remind 1h30m`, `!remind 30m do the thing`, or `!remind 4/1/26`.")
        return
    total_seconds = parse_time_str(time_match.group(1))
    remind_msg = time_match.group(2).strip() or None
    if total_seconds <= 0:
        await ctx.reply("Time must be greater than 0.")
        return
    if total_seconds > 86400:
        await ctx.reply("Max reminder time is 24 hours.")
        return
    await ctx.reply(f"Got it! Reminding you in {format_duration(total_seconds)}.")
    async def send_reminder():
        await asyncio.sleep(total_seconds)
        await ctx.channel.send(f"{ctx.author.mention}, reminder!{' ' + remind_msg if remind_msg else ''}")
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

    entries = await get_leaderboard_or_error(ctx)
    if entries is None: return
    entry = find_user_with_fallback(entries, ctx.author)
    if not entry:
        await ctx.reply("Couldn't find you on the leaderboard.", ephemeral=True)
        return

    username, prev_bytes = entry['discord_username'], entry['total_bytes']
    snapshots = [
        f"Tracking **{username}** for {format_duration(duration)}, checking every {format_duration(interval)}.",
        f"**Start:** {bytes_to_human(prev_bytes)}",
    ]

    try:
        dm = await ctx.author.send("\n".join(snapshots))
    except discord.Forbidden:
        await ctx.reply("I couldn't DM you. Please enable DMs from server members.", ephemeral=True)
        return

    if not isinstance(ctx.channel, discord.DMChannel):
        await ctx.reply("Check your DMs for live updates!", ephemeral=True)

    elapsed = 0
    while elapsed < duration:
        await asyncio.sleep(interval)
        elapsed += interval
        try:
            fresh = await fetch_leaderboard_pages()
            e = find_user(fresh, username)
            if e:
                diff = e['total_bytes'] - prev_bytes
                snapshots.append(f"**t+{elapsed}s:** {bytes_to_human(e['total_bytes'])} ({'+' if diff >= 0 else '-'}{bytes_to_human(abs(diff))})")
                prev_bytes = e['total_bytes']
        except Exception:
            snapshots.append(f"**t+{elapsed}s:** (fetch failed)")

        new_content = "\n".join(snapshots)
        if len(new_content) > 1900:
            snapshots = ["*(continued)*"]
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
    entries = await get_leaderboard_or_error(ctx)
    if entries is None: return

    subcommand = None
    if args:
        arg_parts = args.split(None, 1)
        first = arg_parts[0].lower()
        if first == "list":
            page = int(arg_parts[1]) if len(arg_parts) > 1 and arg_parts[1].isdigit() else 1
            content, total_pages = build_leaderboard_page(entries, page)
            await ctx.reply(content, view=LeaderboardView(entries, page, total_pages, ctx.author.id))
            return
        elif first in ["data", "files", "file"]:
            subcommand = first
            entry = find_user(entries, arg_parts[1]) if len(arg_parts) > 1 else find_user_with_fallback(entries, ctx.author)
        else:
            entry = find_user(entries, args)
    else:
        entry = find_user_with_fallback(entries, ctx.author)

    if not entry:
        await ctx.reply("Couldn't find that user on the leaderboard.")
        return

    name = entry['discord_username']
    if subcommand == "data":
        await ctx.reply(f"**{name}** has archived **{bytes_to_human(entry['total_bytes'])}** of data.")
    elif subcommand in ["files", "file"]:
        await ctx.reply(f"**{name}** has archived **{entry['total_files']:,} files**.")
    else:
        await ctx.reply(f"**{name}** is rank **#{entry['rank']}** with {entry['total_files']:,} files and {bytes_to_human(entry['total_bytes'])} archived.")

@bot.hybrid_command(name="stats", description="See your archive stats")
@app_commands.describe(filter="'files' or 'data'")
async def stats(ctx, filter: str = None):
    if not await check_channel(ctx): return
    await ctx.defer()
    entries = await get_leaderboard_or_error(ctx)
    if entries is None: return
    entry = find_user_with_fallback(entries, ctx.author)
    if not entry:
        await ctx.reply("sorry but we couldn't find you on the leaderboard. pwp")
        return

    name = entry['discord_username']
    if filter and filter.lower() == "files":
        await ctx.reply(f"**{name}** has archived **{entry['total_files']:,} files**.")
    elif filter and filter.lower() == "data":
        await ctx.reply(f"**{name}** has archived **{bytes_to_human(entry['total_bytes'])}** of data.")
    else:
        await ctx.reply(
            f"Stats for **{name}**:\n"
            f"Rank: **#{entry['rank']}**\n"
            f"Files: **{entry['total_files']:,}**\n"
            f"Data: **{bytes_to_human(entry['total_bytes'])}**"
        )

async def _send_help(ctx):
    if not await check_channel(ctx): return
    await ctx.reply(HELP_TEXT)

@bot.hybrid_command(name="help", description="Show all commands")
async def help_cmd(ctx): await _send_help(ctx)

@bot.command(name="list")
async def list_cmd(ctx): await _send_help(ctx)

@bot.command(name="cmd")
async def cmd_cmd(ctx): await _send_help(ctx)

@bot.command(name="command")
async def command_cmd(ctx): await _send_help(ctx)

bot.run(TOKEN)
