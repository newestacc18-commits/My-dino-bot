import discord
from discord.ext import commands, tasks
from datetime import datetime, timezone
from typing import Optional, Union
import os
import json

BOT_STARTED_AT = datetime.now(timezone.utc)
BOT_STATUS_FILE = "bot_status.json"
DINO_STANDINGS_FILE = "dino_standings.json"

# --- CONFIGURATION ---
TOKEN = os.environ['DISCORD_TOKEN']
intents = discord.Intents.default()
intents.members = True  # Needed to manage roles
intents.message_content = True

bot = commands.Bot(command_prefix='!', intents=intents)

# Define your ranks and the XP needed for each
# Format: {XP_Threshold: "Role Name"}
RANKS = {
    0: "Copper I",
    100: "Copper II",
    200: "Copper III",
    300: "Iron",
    400: "Steel",
    500: "Cobalt",
    600: "Obsidian",
    700: "Titanium",
    800: "Emerald",
    900: "Crystal",
    1000: "Aether",
    1100: "Zenith"
}

# Persistent storage for XP data
DATA_FILE = "user_data.json"


def load_user_data():
    if not os.path.exists(DATA_FILE):
        return {}
    try:
        with open(DATA_FILE, "r") as f:
            raw = json.load(f)
        return {int(k): int(v) for k, v in raw.items()}
    except (json.JSONDecodeError, ValueError):
        return {}


def save_user_data():
    with open(DATA_FILE, "w") as f:
        json.dump({str(k): v for k, v in user_data.items()}, f, indent=2)


user_data = load_user_data()

# --- DINO WIN/LOSS STORAGE ---
DINO_STATS_FILE = "dino_stats.json"


def load_dino_stats():
    if not os.path.exists(DINO_STATS_FILE):
        return {}
    try:
        with open(DINO_STATS_FILE, "r") as f:
            raw = json.load(f)
        return {int(uid): v for uid, v in raw.items()}
    except (json.JSONDecodeError, ValueError):
        return {}


def save_dino_stats():
    with open(DINO_STATS_FILE, "w") as f:
        json.dump({str(uid): v for uid, v in dino_stats.items()}, f, indent=2)


dino_stats = load_dino_stats()


def resolve_user_dino(member, dino_name=None):
    """Returns (dino_dict, error_message). dino_dict is None on error."""
    dinos = load_dinos()
    if not dinos:
        return None, "No dinosaurs are configured."

    member_role_ids = {r.id for r in member.roles}
    owned = [d for d in dinos if int(d["role_id"]) in member_role_ids]

    if dino_name:
        target = next(
            (d for d in dinos if d["label"].lower() == dino_name.strip().lower()),
            None,
        )
        if target is None:
            names = ", ".join(d["label"] for d in dinos)
            return None, f"No dinosaur named **{dino_name}**.\nAvailable: {names}"
        if int(target["role_id"]) not in member_role_ids:
            return None, f"You don't have the **{target['label']}** role."
        return target, None

    if not owned:
        return None, "You don't have a dinosaur role. Pick one from the selector."
    if len(owned) > 1:
        names = ", ".join(d["label"] for d in owned)
        return None, f"You have multiple dinosaur roles. Specify one:\n`!win <dino>` — {names}"
    return owned[0], None


DINO_BOARD_FILE = "dino_board_messages.json"


def load_dino_board_messages():
    if not os.path.exists(DINO_BOARD_FILE):
        return {}
    try:
        with open(DINO_BOARD_FILE, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, ValueError):
        return {}


def save_dino_board_messages():
    with open(DINO_BOARD_FILE, "w") as f:
        json.dump(dino_board_messages, f, indent=2)


dino_board_messages = load_dino_board_messages()


def build_dino_scoreboard_embed(guild, dino_label, role):
    rows = []
    for member in role.members:
        entry = dino_stats.get(member.id, {}).get(dino_label, {"wins": 0, "losses": 0})
        rows.append((member, entry.get("wins", 0), entry.get("losses", 0)))
    rows.sort(key=lambda r: (r[1], -r[2]), reverse=True)
    rows = rows[:10]

    embed = discord.Embed(
        title=f"🦖 {dino_label} Standings",
        color=EMBED_COLOR,
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_footer(text="Train hard, fight harder.")
    if guild and guild.icon:
        embed.set_thumbnail(url=guild.icon.url)

    if not rows:
        embed.description = f"No matches recorded for **{dino_label}** yet."
        return embed

    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    rank_col, player_col, score_col = [], [], []
    for i, (member, wins, losses) in enumerate(rows, start=1):
        rank_col.append(medals.get(i, f"#{i}"))
        player_col.append(member.mention)
        score_col.append(f"**{wins}W** / {losses}L")

    embed.add_field(name="Rank", value="\n".join(rank_col), inline=True)
    embed.add_field(name="Player", value="\n".join(player_col), inline=True)
    embed.add_field(name="Record", value="\n".join(score_col), inline=True)
    return embed


async def update_dino_scoreboard(guild, dino):
    """Posts or edits the live scoreboard embed in the dinosaur's channel."""
    if guild is None:
        return None

    label = dino["label"]
    role = guild.get_role(int(dino["role_id"]))
    if role is None:
        return None

    channel_name = f"{label.lower()}-leaderboard"
    channel = discord.utils.get(guild.text_channels, name=channel_name)
    if channel is None:
        return None

    embed = build_dino_scoreboard_embed(guild, label, role)

    saved = dino_board_messages.get(label)
    if saved and saved.get("channel_id") == channel.id:
        try:
            msg = await channel.fetch_message(saved["message_id"])
            await msg.edit(embed=embed)
            return channel
        except (discord.NotFound, discord.Forbidden):
            pass

    msg = await channel.send(embed=embed)
    dino_board_messages[label] = {"channel_id": channel.id, "message_id": msg.id}
    save_dino_board_messages()
    return channel


async def record_dino_result(ctx, member, key, dino_name=None):
    target, err = resolve_user_dino(member, dino_name)
    if err:
        await ctx.send(err)
        return

    bucket = dino_stats.setdefault(member.id, {})
    entry = bucket.setdefault(target["label"], {"wins": 0, "losses": 0})
    entry[key] = entry.get(key, 0) + 1
    save_dino_stats()

    target_channel = await update_dino_scoreboard(ctx.guild, target)

    icon = "✅" if key == "wins" else "❌"
    word = "win" if key == "wins" else "loss"
    if target_channel:
        await ctx.send(
            f"{icon} **{member.display_name}** — {target['label']} {word} recorded! "
            f"({entry['wins']}W / {entry['losses']}L) — see {target_channel.mention} for standings.",
            delete_after=10,
        )
    else:
        await ctx.send(
            f"{icon} **{member.display_name}** — {target['label']} {word} recorded! "
            f"({entry['wins']}W / {entry['losses']}L) — couldn't find #{target['label'].lower()}-leaderboard.",
            delete_after=10,
        )

@tasks.loop(seconds=30)
async def write_bot_status():
    last_scoreboard_update = None
    if os.path.exists(DINO_BOARD_FILE):
        last_scoreboard_update = datetime.fromtimestamp(
            os.path.getmtime(DINO_BOARD_FILE), tz=timezone.utc
        ).isoformat()

    status = {
        "bot_started_at": BOT_STARTED_AT.isoformat(),
        "uptime_sec": int((datetime.now(timezone.utc) - BOT_STARTED_AT).total_seconds()),
        "guild_count": len(bot.guilds),
        "guilds": [g.name for g in bot.guilds],
        "latency_ms": round(bot.latency * 1000),
        "last_scoreboard_update": last_scoreboard_update,
        "heartbeat_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        with open(BOT_STATUS_FILE, "w") as f:
            json.dump(status, f, indent=2)
    except Exception as e:
        print(f"Failed to write bot status: {e}")

    write_dino_standings()


def write_dino_standings():
    dinos = load_dinos()
    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dinosaurs": [],
    }
    for guild in bot.guilds:
        for dino in dinos:
            label = dino["label"]
            role_id = int(dino["role_id"])
            role = guild.get_role(role_id)
            if role is None:
                continue
            rows = []
            for member in role.members:
                entry = dino_stats.get(member.id, {}).get(label, {"wins": 0, "losses": 0})
                w = entry.get("wins", 0)
                l = entry.get("losses", 0)
                if w == 0 and l == 0:
                    continue
                rows.append({
                    "name": member.display_name,
                    "user_id": str(member.id),
                    "wins": w,
                    "losses": l,
                })
            rows.sort(key=lambda r: (r["wins"], -r["losses"]), reverse=True)
            out["dinosaurs"].append({
                "label": label,
                "role_id": str(role_id),
                "members_with_role": len(role.members),
                "top": rows[:5],
            })
        break  # only first guild

    try:
        with open(DINO_STANDINGS_FILE, "w") as f:
            json.dump(out, f, indent=2)
    except Exception as e:
        print(f"Failed to write dino standings: {e}")


@bot.event
async def on_ready():
    print(f'Logged in as {bot.user}')
    bot.add_view(DinoView())

    if not write_bot_status.is_running():
        write_bot_status.start()

    dinos = load_dinos()
    for guild in bot.guilds:
        for dino in dinos:
            try:
                await update_dino_scoreboard(guild, dino)
            except Exception as e:
                print(f"Failed to refresh {dino['label']} scoreboard: {e}")

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("⛔ You need **Administrator** permission to use that command.")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"⚠️ Missing argument: `{error.param.name}`. See `!help {ctx.command}`.")
    elif isinstance(error, commands.BadArgument):
        await ctx.send(f"⚠️ Invalid argument: {error}")
    elif isinstance(error, commands.CommandNotFound):
        return
    else:
        raise error

async def _admin_xp_change(ctx, member, delta, amount):
    if not ctx.author.guild_permissions.administrator:
        await ctx.send("⛔ You need **Administrator** permission to modify another player's XP.")
        return

    user_id = member.id
    current_xp = user_data.get(user_id, 0)
    new_xp = max(0, current_xp + delta)
    user_data[user_id] = new_xp
    save_user_data()

    new_rank_name = None
    for xp_needed in sorted(RANKS.keys(), reverse=True):
        if new_xp >= xp_needed:
            new_rank_name = RANKS[xp_needed]
            break

    if new_rank_name:
        new_role = discord.utils.get(ctx.guild.roles, name=new_rank_name)
        if new_role:
            all_rank_names = list(RANKS.values())
            roles_to_remove = [r for r in member.roles if r.name in all_rank_names and r.name != new_rank_name]
            await member.remove_roles(*roles_to_remove)
            await member.add_roles(new_role)
            verb = "gained" if delta > 0 else "lost"
            icon = "✅" if delta > 0 else "❌"
            await ctx.send(f"{icon} **{member.display_name}** {verb} {amount} XP! Rank: **{new_rank_name}**")
        else:
            await ctx.send(f"⚠️ Error: Role '{new_rank_name}' not found in server settings.")


@bot.command()
async def win(ctx):
    """Records a win for your active dinosaur role."""
    await record_dino_result(ctx, ctx.author, "wins")


@bot.command()
async def loss(ctx):
    """Records a loss for your active dinosaur role."""
    await record_dino_result(ctx, ctx.author, "losses")


@bot.command()
@commands.has_permissions(administrator=True)
async def addxp(ctx, member: discord.Member, amount: int = 25):
    """Adds XP to a player and updates their rank role."""
    await _admin_xp_change(ctx, member, amount, amount)


@bot.command()
@commands.has_permissions(administrator=True)
async def subxp(ctx, member: discord.Member, amount: int = 25):
    """Subtracts XP from a player and updates their rank role."""
    await _admin_xp_change(ctx, member, -amount, amount)

@bot.command()
@commands.has_permissions(administrator=True)
async def setxp(ctx, member: discord.Member, amount: int):
    """Sets a player's XP to an exact value and updates their rank."""
    if amount < 0:
        await ctx.send("⚠️ XP cannot be negative.")
        return

    user_id = member.id
    user_data[user_id] = amount
    save_user_data()

    # Determine what their rank should be
    new_rank_name = None
    for xp_needed in sorted(RANKS.keys(), reverse=True):
        if amount >= xp_needed:
            new_rank_name = RANKS[xp_needed]
            break

    # Update Roles
    if new_rank_name:
        new_role = discord.utils.get(ctx.guild.roles, name=new_rank_name)
        if new_role:
            all_rank_names = list(RANKS.values())
            roles_to_remove = [r for r in member.roles if r.name in all_rank_names and r.name != new_rank_name]
            await member.remove_roles(*roles_to_remove)
            await member.add_roles(new_role)
            await ctx.send(f"🛠️ **{member.display_name}** XP set to {amount}. Rank: **{new_rank_name}**")
        else:
            await ctx.send(f"⚠️ Error: Role '{new_rank_name}' not found in server settings.")

@bot.command()
async def rank(ctx, member: discord.Member = None):
    """Shows a player's current XP and rank."""
    member = member or ctx.author
    current_xp = user_data.get(member.id, 0)

    # Determine current rank and next rank
    current_rank_name = None
    next_threshold = None
    for xp_needed in sorted(RANKS.keys()):
        if current_xp >= xp_needed:
            current_rank_name = RANKS[xp_needed]
        elif next_threshold is None:
            next_threshold = xp_needed

    current_rank_name = current_rank_name or "Unranked"

    if next_threshold is not None:
        xp_to_next = next_threshold - current_xp
        next_rank_name = RANKS[next_threshold]
        progress_line = f"\nNext: **{next_rank_name}** in {xp_to_next} XP"
    else:
        progress_line = "\nMax rank achieved!"

    await ctx.send(
        f"**{member.display_name}**\n"
        f"XP: **{current_xp}**\n"
        f"Rank: **{current_rank_name}**"
        f"{progress_line}"
    )

EMBED_COLOR = discord.Color(0x800080)


def build_leaderboard_embed(description=None):
    embed = discord.Embed(
        title="🏆 AFC Official Leaderboard",
        description=description,
        color=EMBED_COLOR,
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_footer(text="Train hard, fight harder.")
    return embed


@bot.command()
async def leaderboard(ctx, top: int = 10):
    """Shows the top XP earners in the server."""
    if not user_data:
        await ctx.send(embed=build_leaderboard_embed("No XP recorded yet."))
        return

    top = max(1, min(top, 25))
    ranked = sorted(user_data.items(), key=lambda kv: kv[1], reverse=True)

    entries = []
    place = 0
    for user_id, xp in ranked:
        member = ctx.guild.get_member(user_id) if ctx.guild else None
        if member is None:
            continue
        place += 1

        rank_name = "Unranked"
        for xp_needed in sorted(RANKS.keys(), reverse=True):
            if xp >= xp_needed:
                rank_name = RANKS[xp_needed]
                break

        entries.append((place, member, xp, rank_name))
        if place >= top:
            break

    if not entries:
        await ctx.send(embed=build_leaderboard_embed("No ranked players in this server yet."))
        return

    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    rank_col = []
    player_col = []
    xp_col = []
    for place, member, xp, rank_name in entries:
        prefix = medals.get(place, f"#{place}")
        rank_col.append(f"{prefix} {rank_name}")
        player_col.append(member.mention)
        xp_col.append(f"**{xp}**")

    embed = build_leaderboard_embed()
    embed.add_field(name="Rank", value="\n".join(rank_col), inline=True)
    embed.add_field(name="Player", value="\n".join(player_col), inline=True)
    embed.add_field(name="XP", value="\n".join(xp_col), inline=True)

    if ctx.guild and ctx.guild.icon:
        embed.set_thumbnail(url=ctx.guild.icon.url)

    await ctx.send(embed=embed)

@bot.command()
async def dinoboard(ctx, *, dino_name: str = None):
    """Shows the XP leaderboard for a specific dinosaur role."""
    dinos = load_dinos()
    if not dinos:
        await ctx.send("No dinosaurs are configured.")
        return

    if not dino_name:
        names = ", ".join(d["label"] for d in dinos)
        await ctx.send(f"Usage: `!dinoboard <dinosaur>`\nAvailable: {names}")
        return

    target = next(
        (d for d in dinos if d["label"].lower() == dino_name.strip().lower()),
        None,
    )
    if target is None:
        names = ", ".join(d["label"] for d in dinos)
        await ctx.send(f"No dinosaur named **{dino_name}**.\nAvailable: {names}")
        return

    role = ctx.guild.get_role(int(target["role_id"])) if ctx.guild else None
    if role is None:
        await ctx.send(f"The **{target['label']}** role isn't in this server.")
        return

    label = target["label"]
    rows = []
    for member in role.members:
        entry = dino_stats.get(member.id, {}).get(label, {"wins": 0, "losses": 0})
        rows.append((member, entry.get("wins", 0), entry.get("losses", 0)))

    rows.sort(key=lambda r: (r[1], -r[2]), reverse=True)
    rows = rows[:10]

    embed = discord.Embed(
        title=f"🦖 {label} Leaderboard",
        color=EMBED_COLOR,
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_footer(text="Train hard, fight harder.")
    if ctx.guild and ctx.guild.icon:
        embed.set_thumbnail(url=ctx.guild.icon.url)

    if not rows:
        embed.description = f"No one has the **{label}** role yet."
        await ctx.send(embed=embed)
        return

    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    rank_col, player_col, score_col = [], [], []
    for i, (member, wins, losses) in enumerate(rows, start=1):
        rank_col.append(medals.get(i, f"#{i}"))
        player_col.append(member.mention)
        score_col.append(f"**{wins}W** / {losses}L")

    embed.add_field(name="Rank", value="\n".join(rank_col), inline=True)
    embed.add_field(name="Player", value="\n".join(player_col), inline=True)
    embed.add_field(name="Record", value="\n".join(score_col), inline=True)

    await ctx.send(embed=embed)


@bot.command()
async def ranks(ctx):
    """Shows the full XP ladder."""
    sorted_ranks = sorted(RANKS.items())
    tier_col = "\n".join(name for _, name in sorted_ranks)
    xp_col = "\n".join(f"**{xp}** XP" for xp, _ in sorted_ranks)

    embed = discord.Embed(
        title="🏆 AFC Official Leaderboard",
        description="The path from Copper to Zenith.",
        color=EMBED_COLOR,
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(name="Tier", value=tier_col, inline=True)
    embed.add_field(name="XP Required", value=xp_col, inline=True)
    embed.set_footer(text="Train hard, fight harder.")

    if ctx.guild and ctx.guild.icon:
        embed.set_thumbnail(url=ctx.guild.icon.url)

    await ctx.send(embed=embed)

# --- DINOSAUR ROLE SELECTOR ---
DINOS_FILE = "dinos.json"


def load_dinos():
    try:
        with open(DINOS_FILE, "r") as f:
            data = json.load(f)
        return [d for d in data if d.get("label") and d.get("role_id")]
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        return []


class DinoSelect(discord.ui.Select):
    def __init__(self):
        dinos = load_dinos()
        if dinos:
            options = [
                discord.SelectOption(label=d["label"], value=str(d["role_id"]))
                for d in dinos[:25]
            ]
        else:
            options = [discord.SelectOption(label="(no dinosaurs configured)", value="0")]

        super().__init__(
            placeholder="Choose your dinosaurs...",
            min_values=1,
            max_values=min(len(options), 25),
            options=options,
            custom_id="dino_role_select",
        )

    async def callback(self, interaction: discord.Interaction):
        roles = [interaction.guild.get_role(int(role_id)) for role_id in self.values]
        valid_roles = [r for r in roles if r is not None]

        await interaction.user.add_roles(*valid_roles)
        await interaction.response.send_message(
            f"Successfully added roles: {', '.join([r.name for r in valid_roles])}!",
            ephemeral=True
        )

class DinoView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(DinoSelect())

DINO_MENU_FILE = "dino_menu.json"
DINO_MENU_TEXT = "### Select your Dinosaur Roles below!\nYou can pick one or multiple."


def save_dino_menu(channel_id: int, message_id: int):
    with open(DINO_MENU_FILE, "w") as f:
        json.dump({"channel_id": channel_id, "message_id": message_id}, f, indent=2)


def load_dino_menu():
    if not os.path.exists(DINO_MENU_FILE):
        return None
    try:
        with open(DINO_MENU_FILE, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, ValueError):
        return None


@bot.command()
@commands.has_permissions(administrator=True)
async def setup_dinos(ctx):
    TARGET_CHANNEL_ID = 1498751961212911626
    channel = bot.get_channel(TARGET_CHANNEL_ID)

    if channel:
        message = await channel.send(DINO_MENU_TEXT, view=DinoView())
        save_dino_menu(channel.id, message.id)
        await ctx.send(f"Menu sent to {channel.mention}!", ephemeral=True)
    else:
        await ctx.send("I couldn't find that channel. Make sure I have access to it!", ephemeral=True)


@bot.command()
@commands.has_permissions(administrator=True)
async def refresh_dinos(ctx):
    """Updates the existing dinosaur menu with the current options."""
    saved = load_dino_menu()
    if not saved:
        await ctx.send("No saved dinosaur menu found. Run `!setup_dinos` first.")
        return

    channel = bot.get_channel(saved["channel_id"])
    if channel is None:
        await ctx.send("Saved channel is no longer accessible.")
        return

    try:
        message = await channel.fetch_message(saved["message_id"])
    except discord.NotFound:
        await ctx.send("Saved menu message was deleted. Run `!setup_dinos` to post a new one.")
        return
    except discord.Forbidden:
        await ctx.send("I don't have permission to read that channel.")
        return

    await message.edit(content=DINO_MENU_TEXT, view=DinoView())
    await ctx.send(f"Refreshed dinosaur menu in {channel.mention}.")

@bot.command()
@commands.has_permissions(administrator=True)
async def setup_dino_channels(ctx):
    """Creates one leaderboard text channel per dinosaur under a dedicated category."""
    dinos = load_dinos()
    if not dinos:
        await ctx.send("No dinosaurs are configured.")
        return

    category_name = "🦖 Dinosaur Leaderboards"
    category = discord.utils.get(ctx.guild.categories, name=category_name)
    if category is None:
        try:
            category = await ctx.guild.create_category(category_name)
        except discord.Forbidden:
            await ctx.send("I don't have permission to create categories.")
            return

    created, existed, failed = [], [], []
    for d in dinos:
        channel_name = f"{d['label'].lower()}-leaderboard"
        existing = discord.utils.get(ctx.guild.text_channels, name=channel_name)
        if existing:
            existed.append(existing.mention)
            continue
        try:
            ch = await ctx.guild.create_text_channel(channel_name, category=category)
            created.append(ch.mention)
        except discord.Forbidden:
            failed.append(channel_name)

    lines = []
    if created:
        lines.append(f"Created: {', '.join(created)}")
    if existed:
        lines.append(f"Already existed: {', '.join(existed)}")
    if failed:
        lines.append(f"Failed (missing perms): {', '.join(failed)}")

    await ctx.send("\n".join(lines) or "Nothing to do.")


@bot.command()
async def whois(ctx, member: discord.Member = None):
    """Shows a player's profile card."""
    member = member or ctx.author

    current_xp = user_data.get(member.id, 0)
    current_rank_name = "Unranked"
    for xp_needed in sorted(RANKS.keys(), reverse=True):
        if current_xp >= xp_needed:
            current_rank_name = RANKS[xp_needed]
            break

    dino_role_ids = {int(d["role_id"]) for d in load_dinos()}
    dino_roles = [r for r in member.roles if r.id in dino_role_ids]
    dino_value = ", ".join(r.name for r in dino_roles) if dino_roles else "_None_"

    joined_value = (
        f"<t:{int(member.joined_at.timestamp())}:D>" if member.joined_at else "_Unknown_"
    )
    created_value = f"<t:{int(member.created_at.timestamp())}:D>"

    embed = discord.Embed(
        title=f"🏆 {member.display_name}",
        color=EMBED_COLOR,
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="Rank", value=current_rank_name, inline=True)
    embed.add_field(name="XP", value=f"**{current_xp}**", inline=True)
    embed.add_field(name="\u200b", value="\u200b", inline=True)
    embed.add_field(name="Dinosaurs", value=dino_value, inline=False)
    embed.add_field(name="Joined Server", value=joined_value, inline=True)
    embed.add_field(name="Account Created", value=created_value, inline=True)
    embed.set_footer(text="Train hard, fight harder.")

    await ctx.send(embed=embed)

@bot.command()
async def myrecord(ctx, member: discord.Member = None):
    """Shows a player's full win/loss record across every dinosaur they've played."""
    member = member or ctx.author
    bucket = dino_stats.get(member.id, {})

    embed = discord.Embed(
        title=f"📊 {member.display_name}'s Record",
        color=EMBED_COLOR,
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text="Train hard, fight harder.")

    played = {label: stats for label, stats in bucket.items() if stats.get("wins", 0) or stats.get("losses", 0)}

    if not played:
        embed.description = "No matches recorded yet."
        await ctx.send(embed=embed)
        return

    rows = sorted(
        played.items(),
        key=lambda kv: (kv[1].get("wins", 0), -kv[1].get("losses", 0)),
        reverse=True,
    )

    dino_col, win_col, loss_col = [], [], []
    total_w = total_l = 0
    for label, stats in rows:
        w = stats.get("wins", 0)
        l = stats.get("losses", 0)
        total_w += w
        total_l += l
        dino_col.append(label)
        win_col.append(f"**{w}**")
        loss_col.append(f"{l}")

    embed.add_field(name="Dinosaur", value="\n".join(dino_col), inline=True)
    embed.add_field(name="Wins", value="\n".join(win_col), inline=True)
    embed.add_field(name="Losses", value="\n".join(loss_col), inline=True)

    total_games = total_w + total_l
    win_rate = f"{(total_w / total_games * 100):.1f}%" if total_games else "—"
    embed.add_field(
        name="Total",
        value=f"**{total_w}W** / {total_l}L  •  Win rate: **{win_rate}**",
        inline=False,
    )

    await ctx.send(embed=embed)


# --- DYNAMIC PER-DINOSAUR WIN/LOSS COMMANDS ---
def _register_dino_commands():
    for dino in load_dinos():
        label = dino["label"]
        slug = label.lower()

        def make_handler(dino_label, key):
            async def handler(ctx):
                await record_dino_result(ctx, ctx.author, key, dino_name=dino_label)
            handler.__name__ = f"{slug}_{key[:-1]}"
            return handler

        win_cmd = commands.Command(
            make_handler(label, "wins"),
            name=f"{slug}_win",
            help=f"Records a win for {label}.",
        )
        loss_cmd = commands.Command(
            make_handler(label, "losses"),
            name=f"{slug}_loss",
            help=f"Records a loss for {label}.",
        )

        if win_cmd.name not in bot.all_commands:
            bot.add_command(win_cmd)
        if loss_cmd.name not in bot.all_commands:
            bot.add_command(loss_cmd)


_register_dino_commands()


@bot.command()
@commands.has_permissions(administrator=True)
async def announce_box(ctx, title, *, message):
    """Posts a titled blue announcement embed and deletes the trigger message."""
    try:
        await ctx.message.delete()
    except (discord.Forbidden, discord.NotFound):
        pass
    embed = discord.Embed(title=title, description=message, color=discord.Color.blue())
    await ctx.send(embed=embed)


@bot.command()
@commands.has_permissions(administrator=True)
async def post_commands(ctx):
    """Posts the full command list to a private admin-only channel."""
    channel_name = "bot-commands"
    channel = discord.utils.get(ctx.guild.text_channels, name=channel_name)

    if channel is None:
        try:
            overwrites = {
                ctx.guild.default_role: discord.PermissionOverwrite(view_channel=False),
                ctx.guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True),
            }
            channel = await ctx.guild.create_text_channel(
                channel_name,
                overwrites=overwrites,
                topic="Private bot command reference (admins only).",
            )
        except discord.Forbidden:
            await ctx.send("⛔ I don't have permission to create channels.")
            return

    dino_lines = [f"`!{d['label'].lower()}_win` / `!{d['label'].lower()}_loss`" for d in load_dinos()]

    embed = discord.Embed(
        title="🤖 AFC Bot Command Reference",
        description="Complete list of every command and what it does.",
        color=EMBED_COLOR,
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_footer(text="Train hard, fight harder.")

    embed.add_field(
        name="🏆 XP Leaderboard",
        value=(
            "`!leaderboard [N]` — top N XP earners (default 10, max 25).\n"
            "`!ranks` — shows the full rank ladder Copper I → Zenith.\n"
            "`!rank [@player]` — shows your or another player's XP and rank."
        ),
        inline=False,
    )

    embed.add_field(
        name="🛠️ XP Admin (Administrator only)",
        value=(
            "`!addxp @player [amount]` — adds XP and updates rank role (default 25).\n"
            "`!subxp @player [amount]` — subtracts XP and updates rank role.\n"
            "`!setxp @player <amount>` — sets exact XP value."
        ),
        inline=False,
    )

    embed.add_field(
        name="🦖 Dinosaur Win/Loss Tracking",
        value=(
            "`!win` — records a win for your active dinosaur role.\n"
            "`!loss` — records a loss for your active dinosaur role.\n"
            "Per-species shortcuts (use these if you have multiple dino roles):\n"
            + "\n".join(dino_lines)
        ),
        inline=False,
    )

    embed.add_field(
        name="📊 Stats & Profile",
        value=(
            "`!myrecord [@player]` — full win/loss history across all dinosaurs.\n"
            "`!whois [@player]` — profile card (rank, XP, dinosaurs, join date).\n"
            "`!dinoboard <dino>` — win/loss leaderboard for one species."
        ),
        inline=False,
    )

    embed.add_field(
        name="📣 Announcements (Administrator only)",
        value='`!announce_box "Title" <message>` — posts a blue embed announcement and deletes your command message.',
        inline=False,
    )

    embed.add_field(
        name="⚙️ Setup (Administrator only)",
        value=(
            "`!setup_dinos` — posts the dinosaur role selector dropdown.\n"
            "`!refresh_dinos` — updates the live menu after editing dinos.json.\n"
            "`!setup_dino_channels` — creates the 9 dinosaur leaderboard channels.\n"
            "`!post_commands` — posts this message in #bot-commands."
        ),
        inline=False,
    )

    saved = dino_board_messages.get("__commands__")
    sent_msg = None
    if saved and saved.get("channel_id") == channel.id:
        try:
            existing = await channel.fetch_message(saved["message_id"])
            await existing.edit(embed=embed)
            sent_msg = existing
        except (discord.NotFound, discord.Forbidden):
            sent_msg = None

    if sent_msg is None:
        sent_msg = await channel.send(embed=embed)
        dino_board_messages["__commands__"] = {
            "channel_id": channel.id,
            "message_id": sent_msg.id,
        }
        save_dino_board_messages()

    action = "updated" if saved else "posted"
    await ctx.send(f"📬 Command reference {action} in {channel.mention}.")

# --- THE WELCOME EVENT HANDLER ---
@bot.event
async def on_member_join(member):
    welcome_channel_id = 1347411990425931837
    channel = member.guild.get_channel(welcome_channel_id)
    if channel is not None:
        member_count = member.guild.member_count
        msg = f"Glad to have you with us, {member.mention}!\n\n**Next Steps:**\n🔹 Check out the <#1347411991098167451>\n🔹 Grab your roles in <#1503853100857823412>\n🔹 Say hi in <#1347411991613931586>\n\n*You are member #{member_count}!*"
        embed = discord.Embed(title="Welcome to #𝐁𝐞𝐥𝐭𝐓𝐞𝐚𝐦!", description=msg, color=discord.Color.from_str("#2F3136"))
        embed.set_image(url="https://cdn.discordapp.com/attachments/1424571289208885414/1507469760428904570/Screenshot_20260512-134256.png?ex=6a12041e&is=6a10b29e&hm=ac6009138896d562c94093616fc60e08542631b6fb421ab79f84fd7d4a2cbe86&")
        await channel.send(embed=embed)

# --- QUICK TEST EVENT (FOOLPROOF) ---
@bot.event
async def on_message(message):
    if message.content == "!testwelcome":
        member_count = message.guild.member_count
        msg = f"Glad to have you with us, {message.author.mention}!\n\n**Next Steps:**\n🔹 Check out the <#1347411991098167451>\n🔹 Grab your roles in <#1503853100857823412>\n🔹 Say hi in <#1347411991613931586>\n\n*You are member #{member_count}!*"
        embed = discord.Embed(title="Welcome to #𝐁𝐞𝐥𝐭𝐓𝐞𝐚𝐦!", description=msg, color=discord.Color.from_str("#2F3136"))
        embed.set_image(url="https://cdn.discordapp.com/attachments/1424571289208885414/1507469760428904570/Screenshot_20260512-134256.png?ex=6a12041e&is=6a10b29e&hm=ac6009138896d562c94093616fc60e08542631b6fb421ab79f84fd7d4a2cbe86&")
        await message.channel.send(embed=embed)
    await bot.process_commands(message)

# --- START THE BOT ---
bot.run(TOKEN)
