import discord
from discord.ext import commands
from discord.ui import View, Button
import datetime, os, asyncio, json, threading, random, logging, shutil, sqlite3

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
log = logging.getLogger("TitansBot")

TOKEN = os.getenv("DISCORD_TOKEN", "YOUR_BOT_TOKEN_HERE")
ADMIN_ID = int(os.getenv("ADMIN_ID", "1494693018975076392"))
ADMIN_ROLE_ID = 1493705809496903921
DATA_DIR = os.getenv("VOLUME_PATH", ".")
DB_FILE = os.path.join(DATA_DIR, "titansbot.db")
CONFIG_FILE = os.path.join(DATA_DIR, "config.json")
ROLE_NAME = "rank"

CONFIG_LOCK = threading.Lock()

COOLDOWN_DEFAULT = 3
COOLDOWN_ADMIN = 1
COOLDOWN_LOBBIES = 2


DB_INIT = """
CREATE TABLE IF NOT EXISTS scores (
    guild_id TEXT NOT NULL,
    user_id  TEXT NOT NULL,
    name     TEXT NOT NULL DEFAULT '',
    points   INTEGER NOT NULL DEFAULT 0,
    wins     INTEGER NOT NULL DEFAULT 0,
    losses   INTEGER NOT NULL DEFAULT 0,
    mvp_wins INTEGER NOT NULL DEFAULT 0,
    mvp_losses INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (guild_id, user_id)
);
"""

def get_db():
    db = sqlite3.connect(DB_FILE, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=NORMAL")
    return db

def init_db():
    try:
        db = get_db()
        db.executescript(DB_INIT)
        db.commit()
        db.close()
        log.info("Database initialized: %s", DB_FILE)
    except Exception as e:
        log.critical("Database init failed: %s", e)
        raise

def load_scores():
    try:
        db = get_db()
        rows = db.execute("SELECT guild_id, user_id, name, points, wins, losses, mvp_wins, mvp_losses FROM scores").fetchall()
        db.close()
        data = {}
        for r in rows:
            gid = str(r["guild_id"])
            uid = str(r["user_id"])
            g = data.setdefault(gid, {})
            g[uid] = {
                "name": r["name"],
                "points": r["points"],
                "wins": r["wins"],
                "losses": r["losses"],
                "mvp_wins": r["mvp_wins"],
                "mvp_losses": r["mvp_losses"],
            }
        return data
    except Exception as e:
        log.error("load_scores error: %s", e)
        return {}

def save_scores(data):
    try:
        db = get_db()
        db.execute("DELETE FROM scores")
        for gid, users in data.items():
            for uid, u in users.items():
                db.execute(
                    "INSERT INTO scores (guild_id, user_id, name, points, wins, losses, mvp_wins, mvp_losses) VALUES (?,?,?,?,?,?,?,?)",
                    (str(gid), str(uid), u.get("name", ""), u.get("points", 0), u.get("wins", 0), u.get("losses", 0), u.get("mvp_wins", 0), u.get("mvp_losses", 0))
                )
        db.commit()
        db.close()
    except Exception as e:
        log.error("save_scores error: %s", e)

def get_user_data(guild_id, user_id, username):
    try:
        db = get_db()
        gid_str = str(guild_id)
        uid_str = str(user_id)
        row = db.execute("SELECT * FROM scores WHERE guild_id=? AND user_id=?", (gid_str, uid_str)).fetchone()
        rows_all = db.execute("SELECT * FROM scores WHERE guild_id=?", (gid_str,)).fetchall()
        g = {}
        for r in rows_all:
            g[str(r["user_id"])] = {
                "name": r["name"], "points": r["points"], "wins": r["wins"],
                "losses": r["losses"], "mvp_wins": r["mvp_wins"], "mvp_losses": r["mvp_losses"],
            }
        if row:
            db.close()
            return {gid_str: g}, g[uid_str]
        db.execute("INSERT INTO scores (guild_id, user_id, name) VALUES (?,?,?)", (gid_str, uid_str, username))
        db.commit()
        u = {"name": username, "points": 0, "wins": 0, "losses": 0, "mvp_wins": 0, "mvp_losses": 0}
        g[uid_str] = u
        db.close()
        return {gid_str: g}, u
    except Exception as e:
        log.error("get_user_data error: %s", e)
        return {str(guild_id): {}}, {"name": username, "points": 0, "wins": 0, "losses": 0, "mvp_wins": 0, "mvp_losses": 0}


def load_config():
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_config(cfg):
    with CONFIG_LOCK:
        tmp = CONFIG_FILE + ".tmp." + str(random.randint(100000, 999999))
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2)
            shutil.move(tmp, CONFIG_FILE)
        except Exception as e:
            log.error("config write failed: %s", e)
            try:
                os.remove(tmp)
            except:
                pass

cfg = load_config()
rank_message_id = cfg.get("rank_message_id", 1508197095385858120)
rank_channel_id = cfg.get("rank_channel_id", None)
rank_role_id = cfg.get("rank_role_id", 1508212570404687932)


async def safe_nick_edit(member, new_nick, retries=2):
    for attempt in range(retries + 1):
        try:
            if member.display_name != new_nick:
                await member.edit(nick=new_nick)
            return True
        except discord.Forbidden:
            return False
        except discord.HTTPException as e:
            if e.status == 429 and attempt < retries:
                await asyncio.sleep(2 ** attempt + random.uniform(0, 1))
                continue
            return False
    return False

async def safe_add_role(member, role, retries=2):
    for attempt in range(retries + 1):
        try:
            if role not in member.roles:
                await member.add_roles(role, reason="TitansBot rank")
            return True
        except discord.Forbidden:
            return False
        except discord.HTTPException as e:
            if e.status == 429 and attempt < retries:
                await asyncio.sleep(2 ** attempt + random.uniform(0, 1))
                continue
            return False
    return False

async def safe_remove_role(member, role, retries=2):
    for attempt in range(retries + 1):
        try:
            if role in member.roles:
                await member.remove_roles(role, reason="TitansBot rank")
            return True
        except discord.Forbidden:
            return False
        except discord.HTTPException as e:
            if e.status == 429 and attempt < retries:
                await asyncio.sleep(2 ** attempt + random.uniform(0, 1))
                continue
            return False
    return False

async def safe_move_member(member, channel, retries=2):
    for attempt in range(retries + 1):
        try:
            await member.move_to(channel)
            return True
        except (discord.Forbidden, discord.HTTPException):
            return False
    return False

async def safe_delete_channel(channel):
    if not channel:
        return False
    try:
        await channel.delete()
        return True
    except (discord.Forbidden, discord.NotFound, discord.HTTPException):
        return False

async def safe_send(channel, *args, **kwargs):
    if not channel:
        return None
    try:
        return await channel.send(*args, **kwargs)
    except (discord.Forbidden, discord.NotFound, discord.HTTPException):
        return None

async def safe_fetch_message(channel, message_id):
    if not channel:
        return None
    try:
        return await channel.fetch_message(message_id)
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        return None


def progress_bar(filled, total, size=10):
    if total == 0:
        return "\u2b1c" * size
    f = int((filled / total) * size)
    return "\U0001f7e6" * f + "\u2b1c" * (size - f)


async def recalculate_all_ranks(guild):
    try:
        data = load_scores()
        g = data.get(str(guild.id), {})
        bot_member = guild.get_member(bot.user.id) if bot.user else None
        if not bot_member:
            return
        members = [m for m in guild.members if m != bot_member and m.roles and any(ROLE_NAME in r.name for r in m.roles)]
        if not members:
            return
        scored = []
        for m in members:
            pts = g.get(str(m.id), {}).get("points", None)
            if pts is not None:
                scored.append((m, pts))
        zero_pts = [(m, 0) for m in members if str(m.id) not in g]
        all_players = sorted(scored + zero_pts, key=lambda x: (-x[1], x[0].id))
        changed = 0
        tasks = []
        for pos, (m, pts) in enumerate(all_players, 1):
            if not bot_member or m.top_role >= bot_member.top_role:
                continue
            prefix = f"Rank {pos} | "
            base = m.display_name
            if " | " in base:
                base = base.rsplit(" | ", 1)[-1]
            new_nick = f"{prefix}{base}"
            if m.display_name != new_nick:
                tasks.append((m, new_nick))
        if tasks:
            batch_size = 5
            for i in range(0, len(tasks), batch_size):
                batch = tasks[i:i+batch_size]
                results = await asyncio.gather(*[safe_nick_edit(m, n) for m, n in batch], return_exceptions=True)
                changed += sum(1 for r in results if r is True)
                if i + batch_size < len(tasks):
                    await asyncio.sleep(0.3)
        if changed:
            log.info("Updated %d/%d nicknames in %s", changed, len(all_players), guild.name)
    except Exception as e:
        log.error("recalculate_all_ranks error in %s: %s", guild.name if guild else "?", e)


class Lobby:
    def __init__(self, lid, mode, creator, channel):
        self.id = lid
        self.mode = mode
        self.creator = creator
        self.channel = channel
        self.team1: list[discord.Member] = []
        self.team2: list[discord.Member] = []
        self.max_per_team = int(mode[0])
        self.active = True
        self.started = False
        self.message_id = None
        self.category_id = None
        self.t1_vc_id = None
        self.t2_vc_id = None
        self.text_id = None
        self.original_vcs: dict[int, int] = {}
        self.match_id = ""
        self.password = ""
        self.key = ""
        self.cleanup_task = None

    @property
    def total_needed(self):
        return self.max_per_team * 2

    @property
    def total(self):
        return len(self.team1) + len(self.team2)

    @property
    def is_full(self):
        return len(self.team1) >= self.max_per_team and len(self.team2) >= self.max_per_team

    def in_lobby(self, uid):
        return uid in {m.id for m in self.team1} or uid in {m.id for m in self.team2}

    def remove(self, uid):
        self.team1 = [m for m in self.team1 if m.id != uid]
        self.team2 = [m for m in self.team2 if m.id != uid]

    def all_members(self):
        return self.team1 + self.team2


def build_embed(lobby):
    crown = "\U0001f451"
    scores = load_scores()
    g = scores.get(str(lobby.channel.guild.id) if hasattr(lobby.channel, 'guild') else "", {})
    def pts(m):
        p = g.get(str(m.id), {}).get("points", 0)
        return f"`{p}pts`"
    t1 = "\n".join(f"{crown if m.id == lobby.creator.id else ''}{m.mention} {pts(m)}" for m in lobby.team1) or "\u2514\u2500\u2500 *Empty*"
    t2 = "\n".join(f"{crown if m.id == lobby.creator.id else ''}{m.mention} {pts(m)}" for m in lobby.team2) or "\u2514\u2500\u2500 *Empty*"
    bar = progress_bar(lobby.total, lobby.total_needed)
    status = "\U0001f7e2 Waiting..." if not lobby.is_full else "\u2705 Ready!"
    clr = 0x5865F2 if not lobby.is_full else 0x3BA55C
    embed = discord.Embed(title=f"\u2694\ufe0f {lobby.mode.upper()} LOBBY", color=clr, timestamp=datetime.datetime.now())
    if lobby.creator:
        embed.set_author(name=lobby.creator.display_name, icon_url=lobby.creator.display_avatar.url)
    embed.add_field(name=f"\U0001f535 **TEAM 1** ({len(lobby.team1)}/{lobby.max_per_team})", value=t1, inline=True)
    embed.add_field(name=f"\U0001f534 **TEAM 2** ({len(lobby.team2)}/{lobby.max_per_team})", value=t2, inline=True)
    embed.add_field(name="\u200b", value="\u200b", inline=True)
    embed.add_field(name="\U0001f4ca Players", value=f"{bar} `{lobby.total}/{lobby.total_needed}`", inline=False)
    embed.add_field(name="Status", value=status, inline=False)
    return embed


async def cleanup_game(lobby, guild):
    if not lobby or not guild:
        return
    move_tasks = []
    for m in lobby.team1 + lobby.team2:
        orig_id = lobby.original_vcs.get(m.id)
        if orig_id:
            target = guild.get_channel(orig_id)
            if target:
                move_tasks.append(safe_move_member(m, target))
    if move_tasks:
        await asyncio.gather(*move_tasks, return_exceptions=True)
    delete_tasks = []
    for cid in [lobby.text_id, lobby.t1_vc_id, lobby.t2_vc_id, lobby.category_id]:
        if cid:
            ch = guild.get_channel(cid)
            if ch:
                delete_tasks.append(safe_delete_channel(ch))
    if lobby.message_id and lobby.channel:
        msg = await safe_fetch_message(lobby.channel, lobby.message_id)
        if msg:
            delete_tasks.append(msg.delete())
    if delete_tasks:
        await asyncio.gather(*delete_tasks, return_exceptions=True)
    lobbies.pop(lobby.id, None)


async def _cancel_cleanup(lobby):
    if lobby and lobby.cleanup_task and not lobby.cleanup_task.done():
        lobby.cleanup_task.cancel()
        lobby.cleanup_task = None

async def _start_cleanup_timer(lobby):
    await _cancel_cleanup(lobby)
    async def timer():
        try:
            await asyncio.sleep(300)
            if lobby.active and not lobby.started:
                lobby.active = False
                msg = await safe_fetch_message(lobby.channel, lobby.message_id) if lobby.channel else None
                if msg:
                    try:
                        embed = discord.Embed(title="\u23f0 Lobby Cancelled", description="Auto-cancelled due to inactivity (5 min)", color=0xed4245)
                        await msg.edit(embed=embed, view=None)
                    except:
                        pass
                lobbies.pop(lobby.id, None)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            log.error("cleanup timer error: %s", e)
    lobby.cleanup_task = asyncio.create_task(timer())


class MvpView(View):
    def __init__(self, team_members, label):
        super().__init__(timeout=120)
        self.mvp = None
        self.team_members = team_members
        for m in team_members:
            b = Button(label=m.display_name, style=discord.ButtonStyle.primary)
            b.callback = self._make_cb(m)
            self.add_item(b)

    def _make_cb(self, member):
        async def cb(i: discord.Interaction):
            try:
                self.mvp = member
                for child in self.children:
                    child.disabled = True
                await i.response.edit_message(content=f"MVP: {member.mention}", view=self)
                self.stop()
            except (discord.NotFound, discord.InteractionResponded):
                self.stop()
            except Exception as e:
                log.error("MVP callback error: %s", e)
                self.stop()
        return cb


class KeyModal(discord.ui.Modal, title="Enter Game Key"):
    key_input = discord.ui.TextInput(label="Key", placeholder="Enter the game key to join", min_length=1, max_length=20)

    def __init__(self, lobby, team):
        super().__init__()
        self.lobby = lobby
        self.team = team

    async def on_submit(self, interaction: discord.Interaction):
        try:
            l = self.lobby
            if not l or not l.active:
                return await interaction.response.send_message("This lobby is closed.", ephemeral=True)
            if l.in_lobby(interaction.user.id):
                return await interaction.response.send_message("You're already in this lobby!", ephemeral=True)
            role = interaction.guild.get_role(rank_role_id) if interaction.guild else None
            if not role or role not in interaction.user.roles:
                return await interaction.response.send_message("You need to react with 🏆 in the rank channel first to play!", ephemeral=True)
            team_members = l.team1 if self.team == 1 else l.team2
            if len(team_members) >= l.max_per_team:
                return await interaction.response.send_message("That team is full!", ephemeral=True)
            if self.key_input.value != l.key:
                return await interaction.response.send_message("Wrong key! You cannot join this lobby.", ephemeral=True)
            team_members.append(interaction.user)
            await interaction.response.defer()
            msg = await safe_fetch_message(l.channel, l.message_id) if l.channel else None
            if msg:
                try:
                    await msg.edit(embed=build_embed(l), view=LobbyView(l))
                except:
                    pass
            if l.is_full:
                await _start_cleanup_timer(l)
            try:
                await interaction.user.send(f"Joined Team {self.team} \u2022 {l.mode.upper()} lobby")
            except:
                pass
        except Exception as e:
            log.error("KeyModal on_submit error: %s", e)
            try:
                await interaction.response.send_message("Something went wrong.", ephemeral=True)
            except:
                pass


class LobbyView(View):
    def __init__(self, lobby):
        super().__init__(timeout=None)
        self.lobby = lobby
        self._build()

    def _build(self):
        self.clear_items()
        l = self.lobby
        if not l:
            return
        a = l.active
        b1 = Button(label=f"Team 1 ({len(l.team1)}/{l.max_per_team})", style=discord.ButtonStyle.blurple, emoji="\U0001f535",
                    disabled=not a or len(l.team1) >= l.max_per_team, row=0)
        b1.callback = self.join_t1; self.add_item(b1)
        b2 = Button(label=f"Team 2 ({len(l.team2)}/{l.max_per_team})", style=discord.ButtonStyle.red, emoji="\U0001f534",
                    disabled=not a or len(l.team2) >= l.max_per_team, row=0)
        b2.callback = self.join_t2; self.add_item(b2)
        b3 = Button(label="Leave", style=discord.ButtonStyle.grey, emoji="\U0001f6aa", disabled=not a, row=1)
        b3.callback = self.leave; self.add_item(b3)
        b4 = Button(label="Start Game", style=discord.ButtonStyle.green, emoji="\u2705", disabled=not a or not l.is_full, row=1)
        b4.callback = self.start; self.add_item(b4)
        b5 = Button(label="Cancel", style=discord.ButtonStyle.red, emoji="\u26d4", disabled=not a, row=1)
        b5.callback = self.cancel; self.add_item(b5)

    async def _refresh(self, i):
        if not self.lobby:
            return
        try:
            await i.edit_original_response(embed=build_embed(self.lobby), view=LobbyView(self.lobby))
        except (discord.NotFound, discord.InteractionResponded):
            pass
        except Exception as e:
            log.error("LobbyView refresh error: %s", e)

    async def _do_join(self, i, team):
        l = self.lobby
        if not l or not l.active:
            return await self._ephemeral(i, "This lobby is closed.")
        if l.in_lobby(i.user.id):
            return await self._ephemeral(i, "You're already in this lobby!")
        role = i.guild.get_role(rank_role_id) if i.guild else None
        if not role or role not in i.user.roles:
            return await self._ephemeral(i, "You need to react with 🏆 in the rank channel first to play!")
        team_members = l.team1 if team == 1 else l.team2
        if len(team_members) >= l.max_per_team:
            return await self._ephemeral(i, "That team is full!")
        if l.key:
            try:
                return await i.response.send_modal(KeyModal(l, team))
            except:
                return
        team_members.append(i.user)
        await self._defer_refresh(i)
        if l.is_full:
            await _start_cleanup_timer(l)
        try:
            await i.user.send(f"Joined Team {team} \u2022 {l.mode.upper()} lobby")
        except:
            pass

    async def _ephemeral(self, i, msg):
        try:
            await i.response.send_message(msg, ephemeral=True)
        except (discord.NotFound, discord.InteractionResponded):
            pass
        except Exception:
            pass

    async def _defer_refresh(self, i):
        try:
            await i.response.defer()
        except (discord.NotFound, discord.InteractionResponded):
            return
        except Exception:
            return
        await self._refresh(i)

    async def join_t1(self, i):
        await self._do_join(i, 1)

    async def join_t2(self, i):
        await self._do_join(i, 2)

    async def leave(self, i):
        l = self.lobby
        if not l or not l.active:
            return await self._ephemeral(i, "This lobby is closed.")
        if not l.in_lobby(i.user.id):
            return await self._ephemeral(i, "You're not in this lobby.")
        l.remove(i.user.id)
        await _cancel_cleanup(l)
        await self._defer_refresh(i)
        try:
            await i.user.send(f"Left {l.mode.upper()} lobby")
        except:
            pass

    async def start(self, i):
        l = self.lobby
        if not l:
            return
        if i.user.id != l.creator.id:
            return await self._ephemeral(i, "Only the creator can start the game.")
        if not l.is_full:
            return await self._ephemeral(i, "Not enough players!")
        if not l.active or l.started:
            return await self._ephemeral(i, "Already started or cancelled!")
        await _cancel_cleanup(l)
        guild = i.guild
        if not guild:
            return await self._ephemeral(i, "Guild not found.")
        for m in l.team1 + l.team2:
            if m.voice and m.voice.channel:
                l.original_vcs[m.id] = m.voice.channel.id
        try:
            await i.response.defer()
        except:
            return
        try:
            overwrites = {guild.default_role: discord.PermissionOverwrite(connect=False)}
            category = await guild.create_category(f"{l.creator.display_name}'s {l.mode}", overwrites=overwrites)
            l.category_id = category.id
            t1_overwrites = {guild.default_role: discord.PermissionOverwrite(connect=False, view_channel=True)}
            for m in l.team1:
                t1_overwrites[m] = discord.PermissionOverwrite(connect=True, view_channel=True)
            t2_overwrites = {guild.default_role: discord.PermissionOverwrite(connect=False, view_channel=True)}
            for m in l.team2:
                t2_overwrites[m] = discord.PermissionOverwrite(connect=True, view_channel=True)
            vc1 = await guild.create_voice_channel(f"Team 1 ({l.mode})", category=category, overwrites=t1_overwrites)
            vc2 = await guild.create_voice_channel(f"Team 2 ({l.mode})", category=category, overwrites=t2_overwrites)
            l.t1_vc_id = vc1.id
            l.t2_vc_id = vc2.id
            text = await guild.create_text_channel(f"{l.mode.lower()}-lobby", category=category)
            l.text_id = text.id
            move_tasks = []
            for m in l.team1:
                move_tasks.append(safe_move_member(m, vc1))
            for m in l.team2:
                move_tasks.append(safe_move_member(m, vc2))
            await asyncio.gather(*move_tasks, return_exceptions=True)
            msg = f"## \U0001f3ae Match Live!\n\U0001f194 **Match ID:** `{l.match_id}` \U0001f511 **Password:** `{l.password}`"
            if l.key:
                msg += f" \U0001f510 **Key:** `{l.key}`"
            msg += "\n\nClick a team button below when the match ends:"
            await safe_send(text, msg, view=PostGameView(l.id))
        except discord.Forbidden as e:
            log.warning("Missing permissions for game room creation: %s", e)
            for cid in [l.t1_vc_id, l.t2_vc_id, l.text_id, l.category_id]:
                if cid:
                    ch = guild.get_channel(cid)
                    if ch:
                        await safe_delete_channel(ch)
            try:
                embed = discord.Embed(title="Failed", description="Bot lacks permissions to create channels. Check role hierarchy.", color=discord.Color.red())
                await i.edit_original_response(embed=embed, view=None)
            except:
                pass
            l.active = True
            return
        except Exception as e:
            log.error("game room creation error: %s", e)
            for cid in [l.t1_vc_id, l.t2_vc_id, l.text_id, l.category_id]:
                if cid:
                    ch = guild.get_channel(cid)
                    if ch:
                        await safe_delete_channel(ch)
            try:
                await i.edit_original_response(embed=discord.Embed(title="Failed - Channels cleaned up", color=discord.Color.red()), view=None)
            except:
                pass
            l.active = True
            return
        l.active = False
        l.started = True
        embed = discord.Embed(title=f"\U0001f3ae {l.mode.upper()} — LIVE", color=0x5865F2)
        embed.add_field(name=f"\U0001f535 Team 1 ({len(l.team1)})", value="\n".join(m.mention for m in l.team1), inline=True)
        embed.add_field(name=f"\U0001f534 Team 2 ({len(l.team2)})", value="\n".join(m.mention for m in l.team2), inline=True)
        embed.add_field(name="\u200b", value="\u200b", inline=True)
        embed.set_footer(text="\u23f0 Game in progress \u2022 Use buttons below to end")
        try:
            await i.edit_original_response(embed=embed, view=InGameView(l.id))
        except:
            pass
        for m in l.team1 + l.team2:
            try:
                await m.send(f"{l.mode.upper()} started!")
            except:
                pass

    async def cancel(self, i):
        l = self.lobby
        if not l or not l.active:
            return await self._ephemeral(i, "This lobby is closed.")
        if i.user.id != l.creator.id:
            return await self._ephemeral(i, "Only the creator can cancel.")
        await _cancel_cleanup(l)
        l.active = False
        try:
            await i.response.send_message("Lobby cancelled.", ephemeral=True)
        except:
            pass
        try:
            await i.message.delete()
        except:
            pass
        lobbies.pop(l.id, None)


class InGameView(View):
    def __init__(self, lobby_id):
        super().__init__(timeout=None)
        self.lobby_id = lobby_id

    @discord.ui.button(label="End Game", style=discord.ButtonStyle.red, emoji="\u26d4")
    async def end_game(self, i: discord.Interaction, b: Button):
        try:
            lobby = lobbies.get(self.lobby_id)
            if not lobby:
                return await self._ephemeral(i, "Game not found.")
            if i.user.id != lobby.creator.id:
                return await self._ephemeral(i, "Only the creator can end the game.")
            try:
                await i.response.defer()
            except:
                return
            guild = i.guild
            try:
                await i.edit_original_response(embed=discord.Embed(title="Game Ended - Cleaning up...", color=discord.Color.red()), view=None)
            except:
                pass
            if guild:
                await cleanup_game(lobby, guild)
        except Exception as e:
            log.error("InGameView end_game error: %s", e)

    async def _ephemeral(self, i, msg):
        try:
            await i.response.send_message(msg, ephemeral=True)
        except:
            pass


class PostGameView(View):
    def __init__(self, lobby_id):
        super().__init__(timeout=None)
        self.lobby_id = lobby_id

    @discord.ui.button(label="Team 1 Wins", style=discord.ButtonStyle.blurple, emoji="\U0001f3c6")
    async def t1_wins(self, i: discord.Interaction, b: Button):
        await self._pick_mvp(i, 1)

    @discord.ui.button(label="Team 2 Wins", style=discord.ButtonStyle.red, emoji="\U0001f3c6")
    async def t2_wins(self, i: discord.Interaction, b: Button):
        await self._pick_mvp(i, 2)

    async def _restore_match_view(self, i, lobby):
        if not lobby:
            return
        embed = discord.Embed(title=f"\U0001f3ae {lobby.mode.upper()} — LIVE", color=0x5865F2)
        embed.add_field(name=f"\U0001f535 Team 1 ({len(lobby.team1)})", value="\n".join(m.mention for m in lobby.team1), inline=True)
        embed.add_field(name=f"\U0001f534 Team 2 ({len(lobby.team2)})", value="\n".join(m.mention for m in lobby.team2), inline=True)
        embed.set_footer(text="\u23f0 Game in progress")
        try:
            await i.edit_original_response(content=None, embed=embed, view=PostGameView(self.lobby_id))
        except:
            pass

    async def _pick_mvp(self, i: discord.Interaction, winning_team: int):
        try:
            lobby = lobbies.get(self.lobby_id)
            if not lobby:
                return await self._ephemeral(i, "Game not found.")
            if i.user.id != lobby.creator.id:
                return await self._ephemeral(i, "Only the creator can finish.")
            if not lobby.started:
                return await self._ephemeral(i, "Game hasn't started yet.")
            if not i.guild:
                return

            win_team = lobby.team1 if winning_team == 1 else lobby.team2
            lose_team = lobby.team2 if winning_team == 1 else lobby.team1
            win_label = "Team 1" if winning_team == 1 else "Team 2"
            lose_label = "Team 2" if winning_team == 1 else "Team 1"
            we = "\U0001f535" if winning_team == 1 else "\U0001f534"
            le = "\U0001f534" if winning_team == 1 else "\U0001f535"

            try:
                await i.response.defer()
            except:
                return
            try:
                await i.edit_original_response(content="Selecting MVPs...", embed=None, view=None)
            except:
                pass

            v1 = MvpView(win_team, f"{we} {win_label}")
            try:
                await i.followup.send(f"{we} Pick MVP from **{win_label}** (winners):", view=v1, ephemeral=True)
            except:
                return
            await v1.wait()
            if not v1.mvp:
                await self._restore_match_view(i, lobby)
                try:
                    await i.followup.send("Timed out.", ephemeral=True)
                except:
                    pass
                return
            win_mvp = v1.mvp

            v2 = MvpView(lose_team, f"{le} {lose_label}")
            try:
                await i.followup.send(f"{le} Pick MVP from **{lose_label}** (losers):", view=v2, ephemeral=True)
            except:
                return
            await v2.wait()
            if not v2.mvp:
                await self._restore_match_view(i, lobby)
                try:
                    await i.followup.send("Timed out.", ephemeral=True)
                except:
                    pass
                return
            lose_mvp = v2.mvp

            gid = str(i.guild_id)
            wp = [m.id for m in win_team]
            lp = [m.id for m in lose_team]
            tracked = [m for m in lobby.team1 + lobby.team2 if m.roles and any(ROLE_NAME in r.name for r in m.roles)]
            data = load_scores()
            g = data.setdefault(gid, {})
            for m in tracked:
                u = g.setdefault(str(m.id), {"name": m.name, "points": 0, "wins": 0, "losses": 0, "mvp_wins": 0, "mvp_losses": 0})
                u["name"] = m.name
                if m.id in wp:
                    u["points"] += 5
                    u["wins"] += 1
                else:
                    u["losses"] += 1
            for mid, pts, key in [(win_mvp.id, 5, "mvp_wins"), (lose_mvp.id, 2, "mvp_losses")]:
                name = win_mvp.name if mid == win_mvp.id else lose_mvp.name
                u = g.setdefault(str(mid), {"name": name, "points": 0, "wins": 0, "losses": 0, "mvp_wins": 0, "mvp_losses": 0})
                u["points"] += pts
                u[key] += 1
            save_scores(data)
            asyncio.create_task(recalculate_all_ranks(i.guild))

            embed = discord.Embed(title=f"\U0001f3c6 {win_label} WINS!", color=0xFFD700)
            embed.add_field(name=f"{we} {win_label} (+5 each)", value="\n".join(f"{m.mention} \u2705" for m in win_team), inline=True)
            embed.add_field(name=f"{le} {lose_label}", value="\n".join(f"{m.mention}" for m in lose_team), inline=True)
            embed.add_field(name="\u200b", value="\u200b", inline=True)
            embed.add_field(name=f"\u2b50 {win_label} MVP (+5 bonus)", value=win_mvp.mention, inline=True)
            embed.add_field(name=f"\U0001f4aa {lose_label} MVP (+2)", value=lose_mvp.mention, inline=True)
            embed.add_field(name="\u200b", value="\u200b", inline=True)
            embed.set_footer(text="\u2705 Points updated!")
            try:
                await i.edit_original_response(content=None, embed=embed, view=None)
            except:
                pass
            try:
                await i.followup.send(embed=embed)
            except:
                pass
            if lobby.channel and lobby.message_id:
                orig_msg = await safe_fetch_message(lobby.channel, lobby.message_id)
                if orig_msg:
                    try:
                        done = discord.Embed(title=f"\u2705 Game Over \u2022 {lobby.mode}", color=0x808080)
                        await orig_msg.edit(embed=done, view=None)
                    except:
                        pass
            try:
                await i.followup.send("Choose next action:", view=PostGameEndView(lobby.id, i.guild), ephemeral=True)
            except:
                pass
        except Exception as e:
            log.error("_pick_mvp error: %s", e)

    async def _ephemeral(self, i, msg):
        try:
            await i.response.send_message(msg, ephemeral=True)
        except:
            pass


class PostGameEndView(View):
    def __init__(self, lobby_id, guild):
        super().__init__(timeout=None)
        self.lobby_id = lobby_id
        self.guild = guild

    @discord.ui.button(label="\U0001f504 Rematch", style=discord.ButtonStyle.green)
    async def rematch(self, i: discord.Interaction, b: Button):
        try:
            lobby = lobbies.get(self.lobby_id)
            if not lobby:
                return await self._ephemeral(i, "Lobby gone.")
            if i.user.id != lobby.creator.id:
                return await self._ephemeral(i, "Only creator.")
            try:
                await i.response.defer(ephemeral=True)
            except:
                return
            new_lid = f"{lobby.creator.id}_{datetime.datetime.now().timestamp()}"
            new_lobby = Lobby(new_lid, lobby.mode, lobby.creator, lobby.channel)
            new_lobby.match_id = lobby.match_id
            new_lobby.password = lobby.password
            new_lobby.key = lobby.key
            for m in lobby.team1: new_lobby.team1.append(m)
            for m in lobby.team2: new_lobby.team2.append(m)
            lobbies[new_lid] = new_lobby
            if lobby.channel:
                msg = await safe_send(lobby.channel, embed=build_embed(new_lobby), view=LobbyView(new_lobby))
                if msg:
                    new_lobby.message_id = msg.id
            try:
                await i.followup.send(f"Rematch created!", ephemeral=True)
            except:
                pass
        except Exception as e:
            log.error("rematch error: %s", e)

    @discord.ui.button(label="\u26d4 End Game", style=discord.ButtonStyle.red)
    async def end(self, i: discord.Interaction, b: Button):
        try:
            lobby = lobbies.get(self.lobby_id)
            if not lobby:
                return await self._ephemeral(i, "Lobby gone.")
            if i.user.id != lobby.creator.id:
                return await self._ephemeral(i, "Only creator.")
            try:
                await i.response.defer(ephemeral=True)
            except:
                return
            if self.guild:
                await cleanup_game(lobby, self.guild)
            try:
                await i.followup.send("Game ended.", ephemeral=True)
            except:
                pass
        except Exception as e:
            log.error("end game error: %s", e)

    async def _ephemeral(self, i, msg):
        try:
            await i.response.send_message(msg, ephemeral=True)
        except:
            pass


class GameModal(discord.ui.Modal, title="Game Credentials"):
    match_id = discord.ui.TextInput(label="Match ID", placeholder="123456", min_length=1, max_length=20)
    password = discord.ui.TextInput(label="Password", placeholder="7890", min_length=1, max_length=20)
    key = discord.ui.TextInput(label="Key (optional)", placeholder="Only those with the key can join", required=False, max_length=20)

    def __init__(self, mode):
        super().__init__()
        self.mode = mode

    async def on_submit(self, interaction: discord.Interaction):
        try:
            if not self.match_id.value.isdigit() or not self.password.value.isdigit():
                return await interaction.response.send_message("Match ID and Password must be numbers only!", ephemeral=True)
            role = interaction.guild.get_role(rank_role_id) if interaction.guild else None
            if not role or role not in interaction.user.roles:
                return await interaction.response.send_message("You need to react with 🏆 in the rank channel first to play!", ephemeral=True)
            for l in list(lobbies.values()):
                if l.creator.id == interaction.user.id and (l.active or l.started):
                    return await interaction.response.send_message("You already have a lobby/game running! Use /stop to end it.", ephemeral=True)
            lid = f"{interaction.user.id}_{datetime.datetime.now().timestamp()}"
            lobby = Lobby(lid, self.mode, interaction.user, interaction.channel)
            lobby.match_id = self.match_id.value
            lobby.password = self.password.value
            lobby.key = self.key.value.strip()
            lobbies[lid] = lobby
            try:
                await interaction.response.send_message(embed=build_embed(lobby), view=LobbyView(lobby))
                msg = await interaction.original_response()
                lobby.message_id = msg.id
            except Exception as e:
                lobbies.pop(lid, None)
                log.error("failed to send lobby message: %s", e)
                try:
                    await interaction.response.send_message("Failed to create lobby. Check bot permissions.", ephemeral=True)
                except:
                    pass
        except Exception as e:
            log.error("GameModal on_submit error: %s", e)


intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.voice_states = True
bot = commands.Bot(command_prefix="!", intents=intents)
lobbies: dict[str, Lobby] = {}


async def error_boundary(interaction: discord.Interaction, fn, *args, **kwargs):
    try:
        await fn(interaction, *args, **kwargs)
    except discord.NotFound:
        pass
    except discord.InteractionResponded:
        pass
    except discord.Forbidden as e:
        log.warning("Forbidden in %s: %s", interaction.command.name if interaction.command else "?", e)
        try:
            await interaction.followup.send("Bot lacks permissions for this action.", ephemeral=True)
        except:
            pass
    except discord.HTTPException as e:
        if e.status == 429:
            log.warning("Rate limited in %s, retrying later", interaction.command.name if interaction.command else "?")
            try:
                await interaction.followup.send("Rate limited. Try again in a moment.", ephemeral=True)
            except:
                pass
        elif e.status == 500:
            log.error("Discord 500 error in %s", interaction.command.name if interaction.command else "?")
            try:
                await interaction.followup.send("Discord internal error. Try again.", ephemeral=True)
            except:
                pass
        else:
            log.error("HTTP error in %s: %s", interaction.command.name if interaction.command else "?", e)
            try:
                await interaction.followup.send("An error occurred.", ephemeral=True)
            except:
                pass
    except Exception as e:
        log.error("Unhandled error in %s: %s", interaction.command.name if interaction.command else "?", e)
        try:
            await interaction.followup.send("Something went wrong. This has been logged.", ephemeral=True)
        except:
            pass


def admin_check(interaction: discord.Interaction) -> bool:
    if interaction.user.id == ADMIN_ID:
        return True
    guild = interaction.guild
    if guild:
        member = guild.get_member(interaction.user.id)
        if member and any(r.id == ADMIN_ROLE_ID for r in member.roles):
            return True
    return False


def ratelimit(key: str, limit: int, window: float = 5.0):
    if not hasattr(ratelimit, "_buckets"):
        ratelimit._buckets = {}
    now = datetime.datetime.now().timestamp()
    bucket = ratelimit._buckets.get(key, [])
    bucket = [t for t in bucket if now - t < window]
    if len(bucket) >= limit:
        return True
    bucket.append(now)
    ratelimit._buckets[key] = bucket
    return False


@bot.tree.command(name="servers", description="Show all servers the bot is in")
async def cmd_servers(interaction: discord.Interaction):
    try:
        embed = discord.Embed(
            title=f"I'm in {len(bot.guilds)} server(s)",
            description="\n".join(f"{i}. {g.name} (`{g.id}`)" for i, g in enumerate(bot.guilds, 1)),
            color=discord.Color.blurple()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
    except Exception as e:
        log.error("servers error: %s", e)
        try:
            await interaction.response.send_message("Error listing servers.", ephemeral=True)
        except:
            pass


@bot.tree.command(name="rank", description="Show your rank and points")
async def cmd_rank(interaction: discord.Interaction, member: discord.Member = None):
    try:
        target = member or interaction.user
        if not target.roles or not any(ROLE_NAME in r.name for r in target.roles):
            return await interaction.response.send_message("This member doesn't have the rank role.", ephemeral=True)
        data, u = get_user_data(interaction.guild_id, target.id, target.name)
        g = data.get(str(interaction.guild_id), {})
        sorted_ids = sorted(g, key=lambda uid: g[uid]["points"], reverse=True)
        rank_pos = next((i+1 for i, uid in enumerate(sorted_ids) if uid == str(target.id)), "?")
        embed = discord.Embed(title=f"Rank #{rank_pos} - {target.name}", color=discord.Color.gold())
        embed.add_field(name="Points", value=str(u["points"]), inline=True)
        embed.add_field(name="Wins", value=str(u["wins"]), inline=True)
        embed.add_field(name="Losses", value=str(u["losses"]), inline=True)
        embed.add_field(name="MVP (Win)", value=str(u["mvp_wins"]), inline=True)
        embed.add_field(name="MVP (Loss)", value=str(u["mvp_losses"]), inline=True)
        await interaction.response.send_message(embed=embed)
    except Exception as e:
        log.error("rank error: %s", e)
        try:
            await interaction.response.send_message("Error fetching rank.", ephemeral=True)
        except:
            pass


@bot.tree.command(name="leaderboard", description="Show the server leaderboard")
async def cmd_lb(interaction: discord.Interaction):
    try:
        data = load_scores()
        g = data.get(str(interaction.guild_id), {})
        if not g:
            return await interaction.response.send_message("No scores yet!")
        sorted_players = sorted(g.items(), key=lambda x: x[1]["points"], reverse=True)[:10]
        desc = ""
        for i, (uid, u) in enumerate(sorted_players, 1):
            desc += f"{i}. <@{uid}> - {u['points']} pts ({u['wins']}W/{u['losses']}L)\n"
        embed = discord.Embed(title="Leaderboard", description=desc, color=discord.Color.gold())
        await interaction.response.send_message(embed=embed)
    except Exception as e:
        log.error("leaderboard error: %s", e)
        try:
            await interaction.response.send_message("Error fetching leaderboard.", ephemeral=True)
        except:
            pass


@bot.tree.command(name="stop", description="Stop the current game and clean up")
async def cmd_stop(interaction: discord.Interaction):
    try:
        found = False
        for lid, lobby in list(lobbies.items()):
            if lobby.creator.id == interaction.user.id:
                guild = interaction.guild
                if lobby.started and guild:
                    try:
                        await interaction.response.send_message("Stopping game...", ephemeral=True)
                    except:
                        pass
                    await cleanup_game(lobby, guild)
                else:
                    lobby.active = False
                    await _cancel_cleanup(lobby)
                    if lobby.channel and lobby.message_id:
                        msg = await safe_fetch_message(lobby.channel, lobby.message_id)
                        if msg:
                            try:
                                await msg.edit(embed=discord.Embed(title="\u26d4 Cancelled", color=0xed4245), view=None)
                            except:
                                pass
                    lobbies.pop(lid, None)
                    try:
                        await interaction.response.send_message("Lobby cancelled.", ephemeral=True)
                    except:
                        pass
                found = True
                break
        if not found:
            try:
                await interaction.response.send_message("No active game found.", ephemeral=True)
            except:
                pass
    except Exception as e:
        log.error("stop error: %s", e)


@bot.tree.command(name="joinvc", description="Make the bot sit in your voice channel")
async def cmd_joinvc(interaction: discord.Interaction):
    try:
        if not interaction.user.voice or not interaction.user.voice.channel:
            return await interaction.response.send_message("You're not in a voice channel!", ephemeral=True)
        vc = interaction.user.voice.channel
        try:
            if interaction.guild.voice_client:
                await interaction.guild.voice_client.move_to(vc)
            else:
                await vc.connect()
            await interaction.response.send_message(f"Joined {vc.name}!", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("I don't have permission to join that voice channel.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"Failed to join: {e}", ephemeral=True)
    except Exception as e:
        log.error("joinvc error: %s", e)


@bot.tree.command(name="leavevc", description="Make the bot leave voice channel")
async def cmd_leavevc(interaction: discord.Interaction):
    try:
        if interaction.guild.voice_client:
            await interaction.guild.voice_client.disconnect()
            await interaction.response.send_message("Left voice channel.", ephemeral=True)
        else:
            await interaction.response.send_message("Not in a voice channel.", ephemeral=True)
    except Exception as e:
        log.error("leavevc error: %s", e)


@bot.tree.command(name="refreshratings", description="Sync rank roles + refresh nicknames")
async def cmd_refresh(interaction: discord.Interaction):
    try:
        if not admin_check(interaction):
            return await interaction.response.send_message("Only admin.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        if not guild:
            return await interaction.followup.send("Guild not found.", ephemeral=True)
        role = guild.get_role(rank_role_id)
        if not role:
            return await interaction.followup.send("Rank role not found. Run /syncrank to fix.", ephemeral=True)
        channel = guild.get_channel(rank_channel_id) if rank_channel_id else None
        if not channel:
            channel = discord.utils.get(guild.text_channels, name="get-rank")
        if not channel:
            return await interaction.followup.send("Rank channel not found. Use /setrankchannel to set it.", ephemeral=True)
        reacted_ids = set()
        try:
            async for msg in channel.history(limit=200):
                react = discord.utils.get(msg.reactions, emoji="🏆")
                if react:
                    async for u in react.users():
                        if not u.bot:
                            reacted_ids.add(u.id)
        except discord.Forbidden:
            return await interaction.followup.send("Missing permissions to read message history.", ephemeral=True)
        except Exception as e:
            log.error("refresh scan error: %s", e)
        added = 0
        removed = 0
        bot_member = guild.get_member(bot.user.id) if bot.user else None
        if reacted_ids:
            for m in guild.members:
                if m.bot:
                    continue
                if bot_member and m.top_role >= bot_member.top_role:
                    continue
                has_role = role in m.roles
                should_have = m.id in reacted_ids
                if should_have and not has_role:
                    if await safe_add_role(m, role):
                        added += 1
                elif has_role and not should_have:
                    if await safe_remove_role(m, role):
                        removed += 1
        await recalculate_all_ranks(guild)
        reply = f"✅ Nicknames refreshed."
        if added or removed:
            reply += f" ({added} roles added, {removed} removed)"
        if not reacted_ids:
            reply += " No 🏆 reactions found, roles untouched."
        await interaction.followup.send(reply, ephemeral=True)
    except Exception as e:
        log.error("refreshratings error: %s", e)
        try:
            await interaction.followup.send("Error during refresh.", ephemeral=True)
        except:
            pass


@bot.tree.command(name="syncrank", description="Give rank role to ALL members and recalculate nicknames")
async def cmd_syncrank(interaction: discord.Interaction):
    try:
        if not admin_check(interaction):
            return await interaction.response.send_message("Only admin.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        if not guild:
            return
        role = guild.get_role(rank_role_id)
        if not role:
            return await interaction.followup.send("Rank role not found.", ephemeral=True)
        added = 0
        bot_member = guild.get_member(bot.user.id) if bot.user else None
        for m in guild.members:
            if m.bot:
                continue
            if bot_member and m.top_role >= bot_member.top_role:
                continue
            if role not in m.roles:
                if await safe_add_role(m, role):
                    added += 1
        await recalculate_all_ranks(guild)
        await interaction.followup.send(f"✅ Rank role given to {added} members. Nicknames refreshed.", ephemeral=True)
    except Exception as e:
        log.error("syncrank error: %s", e)


@bot.tree.command(name="setrankchannel", description="Set the channel where rank reactions are tracked")
async def cmd_setrankchannel(interaction: discord.Interaction, channel: discord.TextChannel):
    try:
        global rank_message_id, rank_channel_id
        if not admin_check(interaction):
            return await interaction.response.send_message("Only admin.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        rank_channel_id = channel.id
        cfg = load_config()
        cfg["rank_channel_id"] = channel.id
        msg = None
        try:
            async for m in channel.history(limit=50):
                react = discord.utils.get(m.reactions, emoji="🏆")
                if react:
                    msg = m
                    break
        except:
            pass
        if msg:
            rank_message_id = msg.id
            cfg["rank_message_id"] = msg.id
            save_config(cfg)
            n = 0
            react = discord.utils.get(msg.reactions, emoji="🏆")
            if react:
                try:
                    async for u in react.users():
                        if not u.bot:
                            n += 1
                except:
                    pass
            await interaction.followup.send(f"✅ Rank channel set to {channel.mention}. Found rank message with {n} 🏆 reactions. Use /refreshratings to sync roles.", ephemeral=True)
        else:
            save_config(cfg)
            await interaction.followup.send(f"✅ Channel set to {channel.mention}, but no 🏆 message found in last 50. React with 🏆 on a message there, or use /syncrank.", ephemeral=True)
    except Exception as e:
        log.error("setrankchannel error: %s", e)


@bot.tree.command(name="addpoints", description="Add or remove points from a player")
async def cmd_addpoints(interaction: discord.Interaction, member: discord.Member, amount: int):
    try:
        if not admin_check(interaction):
            return await interaction.response.send_message("Only admins can use this.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        data, u = get_user_data(interaction.guild_id, member.id, member.name)
        u["points"] += amount
        save_scores(data)
        guild = interaction.guild
        if guild:
            await recalculate_all_ranks(guild)
        await interaction.followup.send(f"{'+' if amount >= 0 else ''}{amount} points for {member.mention}. Total: {u['points']}", ephemeral=True)
    except Exception as e:
        log.error("addpoints error: %s", e)


@bot.tree.command(name="1v1", description="Create a 1v1 lobby")
async def cmd_1v1(interaction: discord.Interaction):
    try:
        if ratelimit(f"lobby_{interaction.user.id}", 2, 10.0):
            return await interaction.response.send_message("You're creating lobbies too fast. Slow down.", ephemeral=True)
        await interaction.response.send_modal(GameModal("1v1"))
    except Exception as e:
        log.error("1v1 error: %s", e)


@bot.tree.command(name="2v2", description="Create a 2v2 lobby")
async def cmd_2v2(interaction: discord.Interaction):
    try:
        if ratelimit(f"lobby_{interaction.user.id}", 2, 10.0):
            return await interaction.response.send_message("You're creating lobbies too fast. Slow down.", ephemeral=True)
        await interaction.response.send_modal(GameModal("2v2"))
    except Exception as e:
        log.error("2v2 error: %s", e)


@bot.tree.command(name="3v3", description="Create a 3v3 lobby")
async def cmd_3v3(interaction: discord.Interaction):
    try:
        if ratelimit(f"lobby_{interaction.user.id}", 2, 10.0):
            return await interaction.response.send_message("You're creating lobbies too fast. Slow down.", ephemeral=True)
        await interaction.response.send_modal(GameModal("3v3"))
    except Exception as e:
        log.error("3v3 error: %s", e)


@bot.tree.command(name="4v4", description="Create a 4v4 lobby")
async def cmd_4v4(interaction: discord.Interaction):
    try:
        if ratelimit(f"lobby_{interaction.user.id}", 2, 10.0):
            return await interaction.response.send_message("You're creating lobbies too fast. Slow down.", ephemeral=True)
        await interaction.response.send_modal(GameModal("4v4"))
    except Exception as e:
        log.error("4v4 error: %s", e)


class AdminLobbyView(View):
    def __init__(self, lobbies_copy):
        super().__init__(timeout=120)
        for lid, lobby in lobbies_copy.items():
            status = "🟢 Waiting" if lobby.active else ("🔴 Live" if lobby.started else "⚫ Ended")
            label = f"{lobby.mode} by {lobby.creator.display_name} ({status})"
            b = Button(label=label, style=discord.ButtonStyle.grey, row=0)
            b.callback = self._make_cb(lid, lobby)
            self.add_item(b)

    def _make_cb(self, lid, lobby):
        async def cb(i: discord.Interaction):
            try:
                if not admin_check(i):
                    return await i.response.send_message("Only admin.", ephemeral=True)
                view = AdminActionView(lid, lobby)
                t = "🎮 **Active Lobby**" if lobby.active else ("⚔️ **Live Game**" if lobby.started else "**Ended**")
                try:
                    await i.response.edit_message(content=f"{t} — {lobby.mode} by {lobby.creator.mention}\nTeams: {len(lobby.team1)}v{len(lobby.team2)}", view=view)
                except:
                    pass
            except Exception as e:
                log.error("admin lobby callback error: %s", e)
        return cb


class AdminActionView(View):
    def __init__(self, lid, lobby):
        super().__init__(timeout=60)
        self.lid = lid
        self.lobby = lobby
        c = Button(label="❌ Cancel Lobby", style=discord.ButtonStyle.red, disabled=not lobby.active)
        c.callback = self.cancel_lobby; self.add_item(c)
        e = Button(label="⏹ End Game", style=discord.ButtonStyle.grey, disabled=not lobby.started)
        e.callback = self.end_game; self.add_item(e)
        b = Button(label="🔙 Back", style=discord.ButtonStyle.blurple)
        b.callback = self.go_back; self.add_item(b)

    async def cancel_lobby(self, i):
        try:
            if not admin_check(i): return
            l = self.lobby
            if not l.active:
                return await i.response.send_message("Already inactive.", ephemeral=True)
            l.active = False
            await _cancel_cleanup(l)
            if l.channel and l.message_id:
                msg = await safe_fetch_message(l.channel, l.message_id)
                if msg:
                    try:
                        await msg.edit(embed=discord.Embed(title="❌ Cancelled by Admin", color=0xed4245), view=None)
                    except:
                        pass
            lobbies.pop(self.lid, None)
            await i.response.send_message("Lobby cancelled.", ephemeral=True)
        except Exception as e:
            log.error("admin cancel error: %s", e)

    async def end_game(self, i):
        try:
            if not admin_check(i): return
            l = self.lobby
            if not l.started:
                return await i.response.send_message("Not started.", ephemeral=True)
            guild = i.guild
            await i.response.defer(ephemeral=True)
            if guild:
                await cleanup_game(l, guild)
            await i.followup.send("Game ended and cleaned up.", ephemeral=True)
        except Exception as e:
            log.error("admin end_game error: %s", e)

    async def go_back(self, i):
        try:
            if not admin_check(i): return
            await i.response.edit_message(content="Select a lobby:", view=AdminLobbyView({k: v for k, v in lobbies.items()}))
        except Exception as e:
            log.error("admin go_back error: %s", e)


@bot.tree.command(name="admin", description="Admin panel (hidden)")
async def cmd_admin(interaction: discord.Interaction):
    try:
        if not admin_check(interaction):
            return await interaction.response.send_message("You don't have permission.", ephemeral=True)
        if not lobbies:
            return await interaction.response.send_message("No active lobbies or games.", ephemeral=True)
        await interaction.response.send_message("Select a lobby:", view=AdminLobbyView(dict(lobbies)), ephemeral=True)
    except Exception as e:
        log.error("admin command error: %s", e)


@bot.tree.command(name="backup", description="Backup scores to a JSON file")
async def cmd_backup(interaction: discord.Interaction):
    try:
        if not admin_check(interaction):
            return await interaction.response.send_message("Only admin.", ephemeral=True)
        data = load_scores()
        if not data:
            return await interaction.response.send_message("No scores found.", ephemeral=True)
        tmp = os.path.join(DATA_DIR, "backup_export.json")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            await interaction.response.send_message(file=discord.File(tmp, filename="scores_backup.json"), ephemeral=True)
            os.remove(tmp)
        except Exception as e:
            await interaction.response.send_message(f"Failed to send backup: {e}", ephemeral=True)
    except Exception as e:
        log.error("backup error: %s", e)


@bot.tree.command(name="restore", description="Restore scores from a backup file")
async def cmd_restore(interaction: discord.Interaction, attachment: discord.Attachment):
    try:
        if not admin_check(interaction):
            return await interaction.response.send_message("Only admin.", ephemeral=True)
        if not attachment.filename.endswith(".json"):
            return await interaction.response.send_message("Must be a .json file.", ephemeral=True)
        raw = await attachment.read()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return await interaction.response.send_message("Invalid JSON.", ephemeral=True)
        if not isinstance(data, dict):
            return await interaction.response.send_message("Invalid format.", ephemeral=True)
        save_scores(data)
        for guild in bot.guilds:
            asyncio.create_task(recalculate_all_ranks(guild))
        player_count = sum(len(g) for g in data.values()) if data else 0
        await interaction.response.send_message(f"✅ Restored scores for {player_count} players! Nicknames being refreshed.", ephemeral=True)
    except Exception as e:
        log.error("restore error: %s", e)


async def ensure_rank_role(guild):
    global rank_role_id
    try:
        role = guild.get_role(rank_role_id)
        if not role:
            role = discord.utils.get(guild.roles, name=ROLE_NAME)
        if not role:
            bot_member = guild.get_member(bot.user.id) if bot.user else None
            if bot_member and bot_member.guild_permissions.manage_roles:
                role = await guild.create_role(name=ROLE_NAME, reason="Auto-created rank role")
            else:
                log.warning("Missing manage_roles permission in %s", guild.name)
                return None
        if role.id != rank_role_id:
            rank_role_id = role.id
            c = load_config()
            c["rank_role_id"] = role.id
            save_config(c)
        return role
    except discord.Forbidden:
        log.warning("Forbidden to create/manage rank role in %s", guild.name)
        return None
    except Exception as e:
        log.error("ensure_rank_role error in %s: %s", guild.name, e)
        return None


async def ensure_get_rank_channel(guild):
    global rank_message_id, rank_channel_id
    try:
        target = None
        if rank_channel_id:
            target = guild.get_channel(rank_channel_id)
        if not target:
            target = discord.utils.get(guild.text_channels, name="get-rank")
        if not target:
            bot_member = guild.get_member(bot.user.id) if bot.user else None
            if bot_member and bot_member.guild_permissions.manage_channels:
                try:
                    target = await guild.create_text_channel("get-rank")
                except discord.Forbidden:
                    log.warning("Missing manage_channels in %s", guild.name)
                    return None
            else:
                return None
        if rank_message_id:
            try:
                msg = await target.fetch_message(rank_message_id)
                return target
            except:
                pass
        try:
            msg = await target.send("React with 🏆 to get your **rank** role!\n\nYour nickname will be updated to show your rank based on points.")
            await msg.add_reaction("🏆")
            rank_message_id = msg.id
            rank_channel_id = target.id
            c = load_config()
            c["rank_message_id"] = msg.id
            c["rank_channel_id"] = target.id
            save_config(c)
        except discord.Forbidden:
            log.warning("Missing send_message/add_reaction in #get-rank in %s", guild.name)
            return None
        return target
    except Exception as e:
        log.error("ensure_get_rank_channel error in %s: %s", guild.name, e)
        return None


async def _handle_rank_reaction(guild, user_id, add):
    try:
        if not guild or user_id == (bot.user.id if bot.user else None):
            return
        member = guild.get_member(user_id)
        if not member:
            try:
                member = await guild.fetch_member(user_id)
            except:
                return
        if not member or member.bot:
            return
        role = guild.get_role(rank_role_id)
        if not role:
            return
        bot_member = guild.get_member(bot.user.id) if bot.user else None
        if bot_member and role.position >= bot_member.top_role.position:
            log.warning("Rank role is above bot's top role in %s", guild.name)
            return
        if add:
            ok = await safe_add_role(member, role)
            if not ok:
                log.warning("Failed to add rank role to %s in %s", member.id, guild.name)
        else:
            ok = await safe_remove_role(member, role)
            if not ok:
                log.warning("Failed to remove rank role from %s in %s", member.id, guild.name)
            if ok:
                base = member.display_name
                if " | " in base:
                    base = base.rsplit(" | ", 1)[-1]
                    if member.display_name != base and bot_member and member.top_role < bot_member.top_role:
                        await safe_nick_edit(member, base)
        asyncio.create_task(recalculate_all_ranks(guild))
    except Exception as e:
        log.error("_handle_rank_reaction error: %s", e)


def _is_rank_channel(guild, channel_id):
    global rank_channel_id
    if rank_channel_id:
        return channel_id == rank_channel_id
    ch = guild.get_channel(channel_id)
    return ch is not None and ch.name == "get-rank"


@bot.event
async def on_ready():
    log.info("%s is online! (%d guilds)", bot.user, len(bot.guilds))
    for g in bot.guilds:
        log.info("  - %s (%s)", g.name, g.id)
    for guild in bot.guilds:
        await ensure_rank_role(guild)
        await ensure_get_rank_channel(guild)
        try:
            bot.tree.copy_global_to(guild=guild)
            await bot.tree.sync(guild=guild)
        except Exception as e:
            log.error("sync failed for %s: %s", guild.name, e)
    log.info("Commands synced to all guilds")


@bot.event
async def on_raw_reaction_add(payload):
    try:
        if str(payload.emoji) != "🏆":
            return
        guild = bot.get_guild(payload.guild_id)
        if not guild or not _is_rank_channel(guild, payload.channel_id):
            return
        await _handle_rank_reaction(guild, payload.user_id, True)
    except Exception as e:
        log.error("on_raw_reaction_add error: %s", e)


@bot.event
async def on_raw_reaction_remove(payload):
    try:
        if str(payload.emoji) != "🏆":
            return
        guild = bot.get_guild(payload.guild_id)
        if not guild or not _is_rank_channel(guild, payload.channel_id):
            return
        await _handle_rank_reaction(guild, payload.user_id, False)
    except Exception as e:
        log.error("on_raw_reaction_remove error: %s", e)


@bot.event
async def on_command_error(ctx, error):
    log.warning("Prefix command error: %s", error)


if __name__ == "__main__":
    init_db()
    async def start():
        for i in range(10):
            try:
                await bot.start(TOKEN)
                return
            except discord.HTTPException as e:
                if e.status == 429:
                    wait = min(30 * (2 ** i), 600)
                    jitter = random.uniform(0, 5)
                    log.warning("Rate limited, retrying in %.0fs (attempt %d/10)", wait + jitter, i+1)
                    await asyncio.sleep(wait + jitter)
                else:
                    raise
            except Exception as e:
                log.critical("Fatal startup error: %s", e)
                raise
    asyncio.run(start())
