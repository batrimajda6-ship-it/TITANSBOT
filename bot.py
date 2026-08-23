import discord
from discord.ext import commands
from discord.ui import View, Button
import datetime, os, asyncio, json, threading, random, logging, shutil, sqlite3, hashlib, hmac, sys, time, re
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

try:
    import yt_dlp
except ImportError:
    yt_dlp = None

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
log = logging.getLogger("TitansBot")

def _load_env_file(path=".env"):
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())
    except FileNotFoundError:
        pass

_load_env_file(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
_load_env_file(".env")

TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    TOKEN = os.getenv("DISCORD_BOT_TOKEN")
if not TOKEN:
    TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    log.critical("No Discord token found! Set DISCORD_TOKEN env var.")
    sys.exit(1)
ADMIN_ID = int(os.getenv("ADMIN_ID", "1494693018975076392"))
ADMIN_ROLE_ID = 1493705809496903921
ADMIN_ROLE_ID_2 = 1533757074738122864
ADMIN_ROLE_ID_3 = 1487088638117417049
DATA_DIR = os.getenv("VOLUME_PATH", ".")
DB_FILE = os.path.join(DATA_DIR, "titansbot.db")
CONFIG_FILE = os.path.join(DATA_DIR, "config.json")
ROLE_NAME = "APOSTADO PLAYER"
APOSTADO_ROLE_NAME = "APOSTADO PLAYER"

CONFIG_LOCK = threading.Lock()
SCORE_LOCK = threading.Lock()

COOLDOWN_DEFAULT = 3
COOLDOWN_ADMIN = 1
COOLDOWN_LOBBIES = 2

# ── Score cache ───────────────────────────────────────────────────────
_score_cache = None  # in-memory source of truth (lazily loaded once)

def get_scores_cached():
    return load_scores()


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
CREATE TABLE IF NOT EXISTS licenses (
    key_id       TEXT PRIMARY KEY,
    machine_id   TEXT DEFAULT NULL,
    generated_by TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    activated_at TEXT DEFAULT NULL
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
    global _score_cache
    if _score_cache is not None:
        return _score_cache
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
        _score_cache = data
        return data
    except Exception as e:
        log.error("load_scores error: %s", e)
        return {}

def save_scores(data):
    try:
        global _score_cache
        _score_cache = data
        db = get_db()
        for gid, users in data.items():
            for uid, u in users.items():
                db.execute(
                    "INSERT INTO scores (guild_id, user_id, name, points, wins, losses, mvp_wins, mvp_losses) "
                    "VALUES (?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(guild_id, user_id) DO UPDATE SET "
                    "name=excluded.name, points=excluded.points, wins=excluded.wins, losses=excluded.losses, "
                    "mvp_wins=excluded.mvp_wins, mvp_losses=excluded.mvp_losses",
                    (str(gid), str(uid), u.get("name", ""), u.get("points", 0), u.get("wins", 0), u.get("losses", 0), u.get("mvp_wins", 0), u.get("mvp_losses", 0))
                )
        db.commit()
        db.close()
    except Exception as e:
        log.error("save_scores error: %s", e)

def update_scores(guild_id, mutator):
    with SCORE_LOCK:
        data = load_scores()
        g = data.setdefault(str(guild_id), {})
        mutator(g)
        save_scores(data)
    return g

def get_user_data(guild_id, user_id, username):
    try:
        data = load_scores()
        gid_str = str(guild_id)
        uid_str = str(user_id)
        g = data.setdefault(gid_str, {})
        if uid_str in g:
            return {gid_str: g}, g[uid_str]
        u = {"name": username, "points": 0, "wins": 0, "losses": 0, "mvp_wins": 0, "mvp_losses": 0}
        g[uid_str] = u
        save_scores(data)
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

def _guild_settings(guild_id):
    try:
        return (load_config().get("guild_settings") or {}).get(str(guild_id)) or {}
    except Exception:
        return {}

def _guild_setting(guild_id, key, default=None):
    return _guild_settings(guild_id).get(key, default)

def _set_guild_setting(guild_id, **kwargs):
    try:
        c = load_config()
        gs = c.setdefault("guild_settings", {})
        g = gs.setdefault(str(guild_id), {})
        g.update(kwargs)
        save_config(c)
    except Exception as e:
        log.error("set guild setting error: %s", e)

cfg = load_config()
rank_message_id = cfg.get("rank_message_id", 1508197095385858120)
rank_channel_id = cfg.get("rank_channel_id", None)
rank_role_id = cfg.get("rank_role_id", 1508212570404687932)
apostado_role_id = cfg.get("apostado_role_id", None)
apostado_channel_id = cfg.get("apostado_channel_id", 1534172808882556988)
stay_vc_id = cfg.get("stay_vc_id", 1535125369949134848)
stay_music_url = cfg.get("stay_music_url", "https://www.youtube.com/watch?v=gIYaTs1Kw90")
admin_ids = set(cfg.get("admin_ids", []) or [])


_RANK_PREFIX_RE = re.compile(r"^Rank \d+ \| ")

def strip_rank_prefix(name):
    return _RANK_PREFIX_RE.sub("", name, count=1)

def build_rank_nick(rank_pos, current_name):
    prefix = f"Rank {rank_pos} | "
    base = strip_rank_prefix(current_name)
    if len(base) > 32 - len(prefix):
        base = base[:max(0, 32 - len(prefix))]
    return f"{prefix}{base}"


_nick_editing: set = set()

async def safe_nick_edit(member, new_nick, retries=2):
    if member.id in _nick_editing:
        return True
    _nick_editing.add(member.id)
    try:
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
    finally:
        _nick_editing.discard(member.id)

_role_op_lock = asyncio.Lock()

async def safe_add_role(member, role, retries=2):
    for attempt in range(retries + 1):
        try:
            async with _role_op_lock:
                if role not in member.roles:
                    await member.add_roles(role, reason="TitansBot rank")
            return True
        except discord.Forbidden:
            return False
        except discord.HTTPException as e:
            if e.status == 429 and attempt < retries:
                retry_after = (getattr(e, "retry_after", None) or (2 ** attempt)) + 0.5
                await asyncio.sleep(min(retry_after, 30))
                continue
            return False
    return False

async def safe_remove_role(member, role, retries=2):
    for attempt in range(retries + 1):
        try:
            async with _role_op_lock:
                if role in member.roles:
                    await member.remove_roles(role, reason="TitansBot rank")
            return True
        except discord.Forbidden:
            return False
        except discord.HTTPException as e:
            if e.status == 429 and attempt < retries:
                retry_after = (getattr(e, "retry_after", None) or (2 ** attempt)) + 0.5
                await asyncio.sleep(min(retry_after, 30))
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


def compute_rankings(guild, data):
    g = data.get(str(guild.id), {})
    bot_member = guild.get_member(bot.user.id) if bot.user else None
    members = [m for m in guild.members if m != bot_member and m.roles and any(ROLE_NAME in r.name for r in m.roles)]
    players = []
    for m in members:
        pts = g.get(str(m.id), {}).get("points", 0)
        players.append((m, pts))
    players.sort(key=lambda x: (-x[1], x[0].id))
    return players


_background_tasks: set = set()

def _spawn(coro):
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    def _done(t):
        _background_tasks.discard(t)
        if not t.cancelled():
            exc = t.exception()
            if exc:
                log.error("Background task failed: %s", exc)
    task.add_done_callback(_done)
    return task

def _setup_loop_handler():
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    def _handler(loop_, context):
        exc = context.get("exception")
        msg = context.get("message", "Unknown event loop error")
        if exc:
            log.error("Unhandled event loop error: %s: %s", msg, exc)
        else:
            log.error("Event loop error: %s", msg)
    loop.set_exception_handler(_handler)

_recalc_locks: dict = {}
_maintenance_started = False

RECALC_MIN_INTERVAL = 30
_recalc_pending: dict = {}

def schedule_recalc(guild):
    if not guild:
        return
    gid = guild.id
    if _recalc_pending.get(gid):
        return
    _recalc_pending[gid] = True
    _spawn(_run_recalc_later(guild))

async def _run_recalc_later(guild):
    try:
        await asyncio.sleep(RECALC_MIN_INTERVAL)
        await recalculate_all_ranks(guild)
    except Exception as e:
        log.error("scheduled recalc error in %s: %s", guild.name, e)
    finally:
        _recalc_pending.pop(guild.id, None)


async def recalculate_all_ranks(guild):
    if not guild:
        return
    lock = _recalc_locks.setdefault(guild.id, asyncio.Lock())
    async with lock:
        try:
            data = load_scores()
            bot_member = guild.get_member(bot.user.id) if bot.user else None
            if not bot_member:
                return
            all_players = compute_rankings(guild, data)
            if not all_players:
                return
            changed = 0
            tasks = []
            for pos, (m, pts) in enumerate(all_players, 1):
                if not bot_member or m.top_role >= bot_member.top_role:
                    continue
                new_nick = build_rank_nick(pos, m.display_name)
                if m.display_name != new_nick:
                    tasks.append((m, new_nick))
            for attempt in range(2):
                if not tasks:
                    break
                failed = []
                batch_size = 5
                for i in range(0, len(tasks), batch_size):
                    batch = tasks[i:i+batch_size]
                    results = await asyncio.gather(*[safe_nick_edit(m, n) for m, n in batch], return_exceptions=True)
                    for (m, n), r in zip(batch, results):
                        if r is True:
                            changed += 1
                        else:
                            failed.append((m, n))
                    if i + batch_size < len(tasks):
                        await asyncio.sleep(0.3)
                tasks = failed
                if tasks and attempt == 0:
                    await asyncio.sleep(5)
            if changed:
                log.info("Updated %d nicknames in %s (%d failed)", changed, guild.name, len(tasks))
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
        self.vote_id = None
        self.cancel_vote = None
        self.original_vcs: dict[int, int] = {}
        self.voice_warnings: dict[int, int] = {}
        self.paused = False
        self.finished = False
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
    scores = get_scores_cached()
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


async def _move_back_players(lobby, guild):
    move_tasks = []
    for m in lobby.team1 + lobby.team2:
        orig_id = lobby.original_vcs.get(m.id)
        if orig_id:
            target = guild.get_channel(orig_id)
            if target:
                move_tasks.append(safe_move_member(m, target))
    if move_tasks:
        await asyncio.gather(*move_tasks, return_exceptions=True)


async def cleanup_game(lobby, guild):
    if not lobby or not guild:
        return
    await _move_back_players(lobby, guild)
    delete_tasks = []
    for cid in [lobby.text_id, lobby.vote_id, lobby.t1_vc_id, lobby.t2_vc_id, lobby.category_id]:
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


class MatchVote:
    def __init__(self, lobby, question, options, voters):
        self.lobby = lobby
        self.question = question
        self.options = options          # list of (value, label)
        self.voters = set(voters)
        self.t1_voters = {m.id for m in (lobby.team1 if lobby else [])} & self.voters
        self.t2_voters = {m.id for m in (lobby.team2 if lobby else [])} & self.voters
        self.votes = {}
        self.closed = False
        self.result = None
        self.message_id = None
        self.channel = None

    @property
    def tally(self):
        tally = {val: 0 for val, _ in self.options}
        for val in self.votes.values():
            if val in tally:
                tally[val] += 1
        return tally

    def is_complete(self):
        voted = set(self.votes)
        t1_ok = not self.t1_voters or bool(voted & self.t1_voters)
        t2_ok = not self.t2_voters or bool(voted & self.t2_voters)
        return t1_ok and t2_ok

    def most_votes(self):
        tally = self.tally
        top = max(tally.values()) if tally else 0
        winners = [val for val, c in tally.items() if c == top and c > 0]
        return random.choice(winners) if winners else None


def vote_embed(vote, final=False):
    labels = dict(vote.options)
    lines = "\n".join(f"**{labels.get(val, val)}** \u2014 {vote.tally.get(val, 0)} vote(s)" for val, _ in vote.options)
    embed = discord.Embed(title=vote.question, description=lines or "_No options_", color=0x3BA55C if final else 0x5865F2)
    embed.set_footer(text="1 vote from each team decides \u2014 closes as soon as both teams have voted")
    if final and vote.result is not None:
        embed.add_field(name="\U0001f4ca Result", value=labels.get(vote.result, str(vote.result)), inline=False)
    return embed


class VoteView(View):
    def __init__(self, vote):
        super().__init__(timeout=None)
        self.vote = vote
        self.on_complete = None
        for value, label in vote.options:
            b = Button(label=label, style=discord.ButtonStyle.primary)
            b.callback = self._make_cb(value)
            self.add_item(b)

    def _make_cb(self, value):
        async def cb(i: discord.Interaction):
            try:
                v = self.vote
                if v.closed:
                    return await i.response.send_message("Voting is closed.", ephemeral=True)
                if i.user.id not in v.voters:
                    return await i.response.send_message("You're not part of this match.", ephemeral=True)
                if i.user.id in v.votes:
                    return await i.response.send_message("You already voted!", ephemeral=True)
                v.votes[i.user.id] = value
                try:
                    await i.response.send_message("\u2705 Vote counted!", ephemeral=True)
                except:
                    pass
                try:
                    if i.message:
                        await i.message.edit(embed=vote_embed(v), view=self)
                except:
                    pass
                if v.is_complete():
                    v.closed = True
                    v.result = v.most_votes()
                    for child in self.children:
                        child.disabled = True
                    try:
                        if i.message:
                            await i.message.edit(embed=vote_embed(v, final=True), view=self)
                    except:
                        pass
                    if self.on_complete:
                        try:
                            await self.on_complete(v)
                        except Exception as e:
                            log.error("vote on_complete error: %s", e)
            except Exception as e:
                log.error("VoteView cb error: %s", e)
        return cb


async def _auto_close_vote(view, close_after):
    await asyncio.sleep(close_after)
    v = view.vote
    if v.closed:
        return
    v.closed = True
    v.result = v.most_votes()
    for child in view.children:
        child.disabled = True
    if v.message_id and v.channel:
        msg = await safe_fetch_message(v.channel, v.message_id)
        if msg:
            try:
                await msg.edit(embed=vote_embed(v, final=True), view=view)
            except:
                pass
    if view.on_complete:
        try:
            await view.on_complete(v)
        except Exception as e:
            log.error("auto-close vote error: %s", e)


async def run_vote(channel, lobby, question, options, voters, close_after=180):
    vote = MatchVote(lobby, question, options, voters)
    view = VoteView(vote)
    msg = await safe_send(channel, embed=vote_embed(vote), view=view)
    if not msg:
        vote.closed = True
        vote.result = vote.most_votes()
        return vote
    vote.message_id = msg.id
    vote.channel = channel
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    view.on_complete = lambda v: (future.set_result(v) if not future.done() else None)
    _spawn(_auto_close_vote(view, close_after))
    try:
        return await asyncio.wait_for(future, timeout=close_after + 60)
    except asyncio.TimeoutError:
        return vote


async def _apply_match_results(guild, lobby, win_team, lose_team, win_mvp, lose_mvp):
    tracked = [m for m in lobby.team1 + lobby.team2 if m.roles and any(ROLE_NAME in r.name for r in m.roles)]
    tracked_ids = {m.id for m in tracked}
    wp = [m.id for m in win_team]
    gid = str(guild.id)

    def _apply(g):
        for m in tracked:
            u = g.setdefault(str(m.id), {"name": m.name, "points": 0, "wins": 0, "losses": 0, "mvp_wins": 0, "mvp_losses": 0})
            u["name"] = m.name
            if m.id in wp:
                u["points"] += 5
                u["wins"] += 1
            else:
                u["losses"] += 1
        for mid, pts, key, name in [(win_mvp.id, 5, "mvp_wins", win_mvp.name), (lose_mvp.id, 2, "mvp_losses", lose_mvp.name)]:
            if mid not in tracked_ids:
                continue
            u = g.setdefault(str(mid), {"name": name, "points": 0, "wins": 0, "losses": 0, "mvp_wins": 0, "mvp_losses": 0})
            u["points"] += pts
            u[key] += 1

    update_scores(gid, _apply)
    schedule_recalc(guild)
    return tracked_ids


async def run_post_game_votes(guild, lobby, vote_ch):
    all_players = lobby.team1 + lobby.team2
    voters = {m.id for m in all_players}
    if not voters:
        return False

    if lobby.cancel_vote and lobby.cancel_vote.message_id:
        lobby.cancel_vote.closed = True
        msg = await safe_fetch_message(vote_ch, lobby.cancel_vote.message_id)
        if msg:
            try:
                await msg.edit(embed=vote_embed(lobby.cancel_vote, final=True), view=None)
            except:
                pass

    v = await run_vote(vote_ch, lobby, "Which team won?", [("t1", "\U0001f535 Team 1"), ("t2", "\U0001f534 Team 2")], voters, close_after=180)
    if not v or v.result is None:
        return False
    win_side = v.result
    win_team = lobby.team1 if win_side == "t1" else lobby.team2
    lose_team = lobby.team2 if win_side == "t1" else lobby.team1
    win_label = "Team 1" if win_side == "t1" else "Team 2"
    lose_label = "Team 2" if win_side == "t1" else "Team 1"
    we = "\U0001f535" if win_side == "t1" else "\U0001f534"
    le = "\U0001f534" if win_side == "t1" else "\U0001f535"

    v2 = await run_vote(vote_ch, lobby, f"Vote the MVP of {win_label} (winners)", [(str(m.id), m.display_name) for m in win_team], voters, close_after=180)
    v3 = await run_vote(vote_ch, lobby, f"Vote the MVP of {lose_label} (losers)", [(str(m.id), m.display_name) for m in lose_team], voters, close_after=180)
    win_mvp = guild.get_member(int(v2.result)) if v2 and v2.result else None
    lose_mvp = guild.get_member(int(v3.result)) if v3 and v3.result else None

    tracked_ids = await _apply_match_results(guild, lobby, win_team, lose_team, win_mvp, lose_mvp)

    check = "\u2705"
    cross = "\u274c"
    def team_line(team):
        return "\n".join(f"{m.mention} {check if m.id in tracked_ids else cross}" for m in team)
    win_mvp_txt = f"{win_mvp.mention} (no rank role)" if win_mvp and win_mvp.id not in tracked_ids else (win_mvp.mention if win_mvp else "None")
    lose_mvp_txt = f"{lose_mvp.mention} (no rank role)" if lose_mvp and lose_mvp.id not in tracked_ids else (lose_mvp.mention if lose_mvp else "None")

    embed = discord.Embed(title=f"\U0001f3c6 {win_label} WINS!", color=0xFFD700)
    embed.add_field(name=f"{we} {win_label} (+5 each)", value=team_line(win_team), inline=True)
    embed.add_field(name=f"{le} {lose_label}", value=team_line(lose_team), inline=True)
    embed.add_field(name="\u200b", value="\u200b", inline=True)
    embed.add_field(name=f"\u2b50 {win_label} MVP (+5 bonus)", value=win_mvp_txt, inline=True)
    embed.add_field(name=f"\U0001f4aa {lose_label} MVP (+2)", value=lose_mvp_txt, inline=True)
    embed.add_field(name="\u200b", value="\u200b", inline=True)
    embed.set_footer(text="\u2705 Points updated \u2022 decided by player votes")
    await safe_send(vote_ch, embed=embed)
    await _move_back_players(lobby, guild)
    await safe_send(vote_ch, "\U0001f3c6 Points updated! Will you keep playing or finish?", view=PostGameEndView(lobby.id, guild))
    return True


async def _cancel_vote_flow(lobby, guild, vote_ch):
    all_players = lobby.team1 + lobby.team2
    voters = {m.id for m in all_players}
    if not voters:
        return
    v = await run_vote(vote_ch, lobby, "Cancel the game?", [("cancel", "\u270b Cancel"), ("continue", "\u25b6\ufe0f Continue")], voters, close_after=600)
    lobby.cancel_vote = v
    lobby.paused = False
    if v.closed and v.result is None:
        return
    cancel_count = v.tally.get("cancel", 0)
    cont_count = v.tally.get("continue", 0)
    if cancel_count > cont_count:
        lobby.active = False
        await safe_send(vote_ch, embed=discord.Embed(title="\u274c Game cancelled by vote", color=0xed4245))
        await cleanup_game(lobby, guild)
    else:
        await safe_send(vote_ch, embed=discord.Embed(title="\u25b6\ufe0f Game continues", description="Vote decided: keep playing. Use **Finish** in this channel when it's over.", color=0x3BA55C))


async def _monitor_team_voice(lobby, guild, vote_ch):
    try:
        while lobbies.get(lobby.id) is lobby and lobby.started and not lobby.finished and not lobby.paused:
            await asyncio.sleep(30)
            if lobbies.get(lobby.id) is not lobby or lobby.finished or lobby.paused:
                break
            t1 = guild.get_channel(lobby.t1_vc_id) if lobby.t1_vc_id else None
            t2 = guild.get_channel(lobby.t2_vc_id) if lobby.t2_vc_id else None
            for team, vc, label in [(lobby.team1, t1, "Team 1"), (lobby.team2, t2, "Team 2")]:
                for m in list(team):
                    try:
                        in_vc = bool(vc) and bool(m.voice) and bool(m.voice.channel) and m.voice.channel.id == vc.id
                        if in_vc:
                            lobby.voice_warnings.pop(m.id, None)
                            continue
                        count = lobby.voice_warnings.get(m.id, 0) + 1
                        lobby.voice_warnings[m.id] = count
                        if count == 1:
                            await safe_send(vote_ch, f"\u26a0\ufe0f {m.mention} you're not in the **{label}** voice channel. Return now or you'll be timed out!")
                        else:
                            lobby.voice_warnings[m.id] = 0
                            try:
                                await m.timeout(datetime.timedelta(minutes=5), reason=f"Left {label} voice during match")
                                await safe_send(vote_ch, f"\U0001f6ab {m.mention} timed out for leaving {label} voice. Don't do it again.")
                            except Exception as e:
                                log.error("voice monitor timeout error: %s", e)
                    except Exception as e:
                        log.error("voice monitor player error: %s", e)
    except asyncio.CancelledError:
        pass
    except Exception as e:
        log.error("voice monitor error: %s", e)


class MatchControlView(View):
    def __init__(self, lobby_id, guild, vote_ch):
        super().__init__(timeout=None)
        self.lobby_id = lobby_id
        self.guild = guild
        self.vote_ch = vote_ch
        self._busy = False

    async def _can_act(self, i):
        lobby = lobbies.get(self.lobby_id)
        if not lobby:
            return None
        member_ids = {m.id for m in lobby.all_members()}
        if i.user.id not in member_ids and not is_admin_user(i):
            return None
        return lobby

    async def _ephemeral(self, i, msg):
        try:
            await i.response.send_message(msg, ephemeral=True)
        except (discord.NotFound, discord.InteractionResponded):
            pass
        except Exception:
            pass

    async def _disable(self, i):
        try:
            for child in self.children:
                child.disabled = True
            await i.message.edit(view=self)
        except Exception:
            pass

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.red, emoji="\u26d4")
    async def cancel(self, i: discord.Interaction, b: Button):
        lobby = await self._can_act(i)
        if not lobby:
            return await self._ephemeral(i, "You're not part of this match.")
        if self._busy:
            return await self._ephemeral(i, "An action is already running.")
        self._busy = True
        lobby.paused = True
        await self._disable(i)
        try:
            await i.response.defer()
        except:
            pass
        _spawn(_cancel_vote_flow(lobby, self.guild, self.vote_ch))

    @discord.ui.button(label="MVP", style=discord.ButtonStyle.green, emoji="\U0001f3c6")
    async def mvp(self, i: discord.Interaction, b: Button):
        lobby = await self._can_act(i)
        if not lobby:
            return await self._ephemeral(i, "You're not part of this match.")
        if self._busy:
            return await self._ephemeral(i, "An action is already running.")
        self._busy = True
        await self._disable(i)
        try:
            await i.response.defer()
        except:
            pass
        _spawn(self._mvp_flow(lobby))

    async def _mvp_flow(self, lobby):
        try:
            ok = await run_post_game_votes(self.guild, lobby, self.vote_ch)
            if not ok:
                await cleanup_game(lobby, self.guild)
        except Exception as e:
            log.error("mvp flow error: %s", e)
            await cleanup_game(lobby, self.guild)


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
            role = interaction.guild.get_role(apostado_role_id) if interaction.guild else None
            if not role or role not in interaction.user.roles:
                return await interaction.response.send_message("You need to react with 🏆 in the APOSTADO channel first to play!", ephemeral=True)
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
        role = i.guild.get_role(apostado_role_id) if i.guild else None
        if not role or role not in i.user.roles:
            return await self._ephemeral(i, "You need to react with 🏆 in the APOSTADO channel first to play!")
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
            category = await guild.create_category(f"{l.creator.display_name}'s lobby", overwrites=overwrites)
            l.category_id = category.id
            t1_overwrites = {guild.default_role: discord.PermissionOverwrite(connect=False, view_channel=True)}
            for m in l.team1:
                t1_overwrites[m] = discord.PermissionOverwrite(connect=True, view_channel=True)
            t2_overwrites = {guild.default_role: discord.PermissionOverwrite(connect=False, view_channel=True)}
            for m in l.team2:
                t2_overwrites[m] = discord.PermissionOverwrite(connect=True, view_channel=True)
            vc1 = await guild.create_voice_channel("Team 1", category=category, overwrites=t1_overwrites)
            vc2 = await guild.create_voice_channel("Team 2", category=category, overwrites=t2_overwrites)
            l.t1_vc_id = vc1.id
            l.t2_vc_id = vc2.id
            def _text_overwrites():
                ow = {guild.default_role: discord.PermissionOverwrite(view_channel=False, send_messages=False)}
                for m in l.team1 + l.team2:
                    ow[m] = discord.PermissionOverwrite(
                        view_channel=True, send_messages=True, read_message_history=True, embed_links=True
                    )
                return ow
            text = await guild.create_text_channel("lobby", category=category, overwrites=_text_overwrites())
            l.text_id = text.id
            vote_ch = await guild.create_text_channel("vote", category=category, overwrites=_text_overwrites())
            l.vote_id = vote_ch.id
            move_tasks = []
            for m in l.team1:
                move_tasks.append(safe_move_member(m, vc1))
            for m in l.team2:
                move_tasks.append(safe_move_member(m, vc2))
            await asyncio.gather(*move_tasks, return_exceptions=True)
            msg = f"## \U0001f3ae Match Live!\n\U0001f194 **Match ID:** `{l.match_id}` \U0001f511 **Password:** `{l.password}`"
            if l.key:
                msg += f" \U0001f510 **Key:** `{l.key}`"
            msg += "\n\nUse the **\u26d4 Cancel** or **\U0001f3c6 MVP** buttons in the **#vote** channel. Everyone must stay in their team voice channel."
            await safe_send(text, msg)
            control_embed = discord.Embed(title="\U0001f3ae Match Controls", color=0x5865F2)
            control_embed.add_field(name="\u26d4 Cancel", value="Vote to cancel the game", inline=True)
            control_embed.add_field(name="\U0001f3c6 MVP", value="Vote winner team & MVPs \u2014 then keep playing or finish", inline=True)
            await safe_send(vote_ch, embed=control_embed, view=MatchControlView(l.id, guild, vote_ch))
            _spawn(_monitor_team_voice(l, guild, vote_ch))
        except discord.Forbidden as e:
            log.warning("Missing permissions for game room creation: %s", e)
            for cid in [l.t1_vc_id, l.t2_vc_id, l.text_id, l.vote_id, l.category_id]:
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
            for cid in [l.t1_vc_id, l.t2_vc_id, l.text_id, l.vote_id, l.category_id]:
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
        embed.set_footer(text="\u23f0 Game in progress \u2022 Use the buttons in the #vote channel")
        try:
            await i.edit_original_response(embed=embed, view=None)
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


class PostGameEndView(View):
    def __init__(self, lobby_id, guild):
        super().__init__(timeout=None)
        self.lobby_id = lobby_id
        self.guild = guild

    @discord.ui.button(label="\u25b6\ufe0f Keep Playing", style=discord.ButtonStyle.green)
    async def keep_playing(self, i: discord.Interaction, b: Button):
        try:
            lobby = lobbies.get(self.lobby_id)
            if not lobby:
                return await self._ephemeral(i, "Lobby gone.")
            member_ids = {m.id for m in lobby.team1 + lobby.team2}
            if i.user.id not in member_ids and not is_admin_user(i):
                return await self._ephemeral(i, "Only match players.")
            try:
                await i.response.defer(ephemeral=True)
            except:
                return
            try:
                for child in self.children:
                    child.disabled = True
                await i.message.edit(view=self)
            except:
                pass
            vote_ch = self.guild.get_channel(lobby.vote_id) if self.guild else None
            if vote_ch:
                await safe_send(
                    vote_ch,
                    "\u25b6\ufe0f Game continues \u2014 same teams, same channels! Use \U0001f3c6 **MVP** here when the next game ends.",
                    view=MatchControlView(lobby.id, self.guild, vote_ch),
                )
        except Exception as e:
            log.error("keep playing error: %s", e)

    @discord.ui.button(label="\u26d4 End Game", style=discord.ButtonStyle.red)
    async def end(self, i: discord.Interaction, b: Button):
        try:
            lobby = lobbies.get(self.lobby_id)
            if not lobby:
                return await self._ephemeral(i, "Lobby gone.")
            member_ids = {m.id for m in lobby.team1 + lobby.team2}
            if i.user.id not in member_ids and not is_admin_user(i):
                return await self._ephemeral(i, "Only match players.")
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
            role = interaction.guild.get_role(apostado_role_id) if interaction.guild else None
            if not role or role not in interaction.user.roles:
                return await interaction.response.send_message("You need to react with 🏆 in the APOSTADO channel first to play!", ephemeral=True)
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


def is_admin_user(interaction: discord.Interaction) -> bool:
    if interaction.user.id == ADMIN_ID:
        return True
    if interaction.user.id in admin_ids:
        return True
    member = interaction.user if isinstance(interaction.user, discord.Member) else None
    if member is None and interaction.guild:
        member = interaction.guild.get_member(interaction.user.id)
    if member and any(r.id in (ADMIN_ROLE_ID, ADMIN_ROLE_ID_2, ADMIN_ROLE_ID_3) for r in member.roles):
        return True
    return False


def admin_check(interaction: discord.Interaction) -> bool:
    return is_admin_user(interaction)


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
        players = compute_rankings(interaction.guild, data)
        rank_pos = next((i + 1 for i, (m, pts) in enumerate(players) if m.id == target.id), "?")
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
        guild = interaction.guild
        data = load_scores()
        players = compute_rankings(guild, data)
        if not players:
            return await interaction.response.send_message("No scores yet!")
        desc = ""
        for i, (m, pts) in enumerate(players[:10], 1):
            u = data.get(str(guild.id), {}).get(str(m.id), {})
            desc += f"{i}. {m.mention} - {pts} pts ({u.get('wins', 0)}W/{u.get('losses', 0)}L)\n"
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


@bot.tree.command(name="setrankchannel", description="Set the channel where 🏆 reactions grant the APOSTADO PLAYER role (per server)")
async def cmd_setrankchannel(interaction: discord.Interaction, channel: discord.TextChannel):
    try:
        global rank_message_id, rank_channel_id, apostado_channel_id
        if not admin_check(interaction):
            return await interaction.response.send_message("Only admin.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        if not guild:
            return await interaction.followup.send("Must run inside a server.", ephemeral=True)
        bot_member = guild.get_member(bot.user.id) if bot.user else None
        if not bot_member:
            return await interaction.followup.send("I can't find myself in this server. Re-invite me.", ephemeral=True)
        if not bot_member.guild_permissions.manage_roles:
            return await interaction.followup.send("❌ I need the **Manage Roles** permission in this server to give/remove roles. Grant it (Server Settings → Roles → the bot's role) and try again.", ephemeral=True)
        await ensure_apostado_role(guild)
        await ensure_rank_role(guild)
        for rname, role in (("APOSTADO PLAYER", guild.get_role(_guild_setting(guild.id, "apostado_role_id") or apostado_role_id)), ("Rank", guild.get_role(_guild_setting(guild.id, "rank_role_id") or rank_role_id))):
            if role and role.position >= bot_member.top_role.position:
                await interaction.followup.send(f"❌ The **{rname}** role is above my role in Server Settings → Roles. Move my role (top one) **above** it, then rerun /setrankchannel.", ephemeral=True)
                return
        _set_guild_setting(guild.id, rank_channel_id=channel.id, apostado_channel_id=channel.id)
        rank_channel_id = channel.id
        apostado_channel_id = channel.id
        cfg = load_config()
        cfg["rank_channel_id"] = channel.id
        cfg["apostado_channel_id"] = channel.id
        msg = None
        configured_mid = _guild_setting(guild.id, "rank_message_id") or rank_message_id
        if configured_mid:
            msg = await safe_fetch_message(channel, configured_mid)
            if not msg:
                log.info("Previous rank message %s no longer exists in #%s", configured_mid, channel.name)
        _last_auto_rank_msg[guild.id] = time.monotonic()
        if msg:
            _set_guild_setting(guild.id, rank_message_id=msg.id)
            rank_message_id = msg.id
            cfg["rank_message_id"] = msg.id
            save_config(cfg)
            try:
                await msg.add_reaction("🏆")
            except:
                pass
            n = 0
            react = discord.utils.get(msg.reactions, emoji="🏆")
            if react:
                try:
                    async for u in react.users():
                        if not u.bot:
                            n += 1
                except:
                    pass
            await interaction.followup.send(f"✅ Rank channel set to {channel.mention}. Found a 🏆 message with {n} reaction(s). Reacting grants the **APOSTADO PLAYER** role, unreacting removes it.", ephemeral=True)
        else:
            try:
                msg = await channel.send("React with 🏆 to get the **APOSTADO PLAYER** role and play with the bot!\n\nUnreacting removes the role.")
                await msg.add_reaction("🏆")
                _set_guild_setting(guild.id, rank_message_id=msg.id)
                rank_message_id = msg.id
                cfg["rank_message_id"] = msg.id
                save_config(cfg)
                await interaction.followup.send(f"✅ Rank channel set to {channel.mention}. Created a new 🏆 reaction message. Reacting grants the **APOSTADO PLAYER** role, unreacting removes it.", ephemeral=True)
            except discord.Forbidden:
                save_config(cfg)
                await interaction.followup.send(f"✅ Channel set to {channel.mention}, but I couldn't send the message there (missing permission).", ephemeral=True)
    except Exception as e:
        log.error("setrankchannel error: %s", e)


@bot.tree.command(name="diagnose", description="[Admin] Check why reaction roles may not be working in this server")
async def cmd_diagnose(interaction: discord.Interaction):
    try:
        if not admin_check(interaction):
            return await interaction.response.send_message("Only admin.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        if not guild:
            return await interaction.followup.send("Must run inside a server.", ephemeral=True)
        bot_member = guild.get_member(bot.user.id) if bot.user else None
        gs = _guild_settings(guild.id)
        lines = []
        lines.append(f"**Guild:** {guild.name} ({guild.id})")
        lines.append(f"**Bot role:** {'@' + bot_member.top_role.name if bot_member else 'N/A'} (pos {bot_member.top_role.position if bot_member else '?'})")
        lines.append(f"**Manage Roles:** {bool(bot_member and bot_member.guild_permissions.manage_roles)}")
        arole = guild.get_role(gs.get("apostado_role_id") or apostado_role_id)
        rrole = guild.get_role(gs.get("rank_role_id") or rank_role_id)
        if not arole:
            arole = discord.utils.get(guild.roles, name=APOSTADO_ROLE_NAME)
        if not rrole:
            rrole = discord.utils.get(guild.roles, name=ROLE_NAME)
        lines.append(f"**APOSTADO role:** {'@' + arole.name + ' (pos ' + str(arole.position) + ')' if arole else 'MISSING'}" + (f" — ⚠️ ABOVE BOT, move bot's role up" if (arole and bot_member and arole.position >= bot_member.top_role.position) else ""))
        lines.append(f"**Rank role:** {'@' + rrole.name + ' (pos ' + str(rrole.position) + ')' if rrole else 'MISSING'}" + (f" — ⚠️ ABOVE BOT, move bot's role up" if (rrole and bot_member and rrole.position >= bot_member.top_role.position) else ""))
        cid = gs.get("rank_channel_id") or rank_channel_id
        achid = gs.get("apostado_channel_id") or apostado_channel_id
        lines.append(f"**Saved channel (this server):** {guild.get_channel(cid).mention if cid and guild.get_channel(cid) else str(cid)}")
        lines.append(f"**Saved 🏆 message:** {gs.get('rank_message_id') or rank_message_id}")
        lines.append(f"**Channel checks:** rank_match={'YES' if cid else 'no'}, apostado_match={'YES' if achid else 'no'}")
        embed = discord.Embed(title="🔍 Diagnose", color=0x5865F2, description="\n".join(lines))
        await interaction.followup.send(embed=embed, ephemeral=True)
    except Exception as e:
        log.error("diagnose command error: %s", e)


@bot.tree.command(name="mergeroles", description="[Admin] Merge two roles into one APOSTADO PLAYER role (blue)")
async def cmd_mergeroles(interaction: discord.Interaction, role1: discord.Role, role2: discord.Role):
    if not admin_check(interaction):
        return await interaction.response.send_message("Only admin.", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    guild = interaction.guild
    if not guild:
        return await interaction.followup.send("Guild not found.", ephemeral=True)
    global rank_role_id, apostado_role_id
    target = guild.get_role(apostado_role_id)
    if not target:
        target = discord.utils.get(guild.roles, name=APOSTADO_ROLE_NAME)
    if not target:
        bot_member = guild.get_member(bot.user.id) if bot.user else None
        if not bot_member or not bot_member.guild_permissions.manage_roles:
            return await interaction.followup.send("I need the Manage Roles permission to create the role.", ephemeral=True)
        try:
            target = await guild.create_role(name=APOSTADO_ROLE_NAME, color=discord.Color.blue(), reason="Merged APOSTADO PLAYER role")
        except discord.Forbidden:
            return await interaction.followup.send("I can't create roles here.", ephemeral=True)
    bot_member = guild.get_member(bot.user.id) if bot.user else None
    members = {}
    for src in (role1, role2):
        if src.id == target.id:
            continue
        for m in src.members:
            members[m.id] = m
    assigned = 0
    for m in members.values():
        if bot_member and m.top_role >= bot_member.top_role:
            continue
        if await safe_add_role(m, target):
            assigned += 1
    deleted = 0
    for src in (role1, role2):
        if src.id == target.id:
            continue
        try:
            await src.delete(reason="Merged into APOSTADO PLAYER")
            deleted += 1
        except discord.Forbidden:
            continue
    rank_role_id = target.id
    apostado_role_id = target.id
    cfg = load_config()
    cfg["rank_role_id"] = target.id
    cfg["apostado_role_id"] = target.id
    save_config(cfg)
    await recalculate_all_ranks(guild)
    await interaction.followup.send(
        f"✅ Merged {role1.mention} and {role2.mention} into {target.mention} (blue).\n"
        f"Assigned to {assigned} member(s), deleted {deleted} old role(s).",
        ephemeral=True,
    )


@bot.tree.command(name="addpoints", description="Add or remove points from a player")
async def cmd_addpoints(interaction: discord.Interaction, member: discord.Member, amount: int):
    try:
        if not admin_check(interaction):
            return await interaction.response.send_message("Only admins can use this.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        def _apply(g):
            u = g.setdefault(str(member.id), {"name": member.name, "points": 0, "wins": 0, "losses": 0, "mvp_wins": 0, "mvp_losses": 0})
            u["name"] = member.name
            u["points"] += amount
        g = update_scores(interaction.guild_id, _apply)
        total = g.get(str(member.id), {}).get("points", 0)
        guild = interaction.guild
        if guild:
            await recalculate_all_ranks(guild)
        await interaction.followup.send(f"{'+' if amount >= 0 else ''}{amount} points for {member.mention}. Total: {total}", ephemeral=True)
    except Exception as e:
        log.error("addpoints error: %s", e)


@bot.tree.command(name="1v1", description="Create a 1v1 lobby")
async def cmd_1v1(interaction: discord.Interaction):
    try:
        await interaction.response.send_modal(GameModal("1v1"))
    except Exception as e:
        log.error("1v1 error: %s", e)


@bot.tree.command(name="2v2", description="Create a 2v2 lobby")
async def cmd_2v2(interaction: discord.Interaction):
    try:
        await interaction.response.send_modal(GameModal("2v2"))
    except Exception as e:
        log.error("2v2 error: %s", e)


@bot.tree.command(name="3v3", description="Create a 3v3 lobby")
async def cmd_3v3(interaction: discord.Interaction):
    try:
        await interaction.response.send_modal(GameModal("3v3"))
    except Exception as e:
        log.error("3v3 error: %s", e)


@bot.tree.command(name="4v4", description="Create a 4v4 lobby")
async def cmd_4v4(interaction: discord.Interaction):
    try:
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


class ModeSelect(discord.ui.Select):
    def __init__(self, default=None):
        options = [
            discord.SelectOption(label="1v1", description="Create a 1v1 lobby", emoji="⚔️"),
            discord.SelectOption(label="2v2", description="Create a 2v2 lobby", emoji="⚔️"),
            discord.SelectOption(label="3v3", description="Create a 3v3 lobby", emoji="⚔️"),
            discord.SelectOption(label="4v4", description="Create a 4v4 lobby", emoji="⚔️"),
        ]
        for o in options:
            if o.value == default:
                o.default = True
        super().__init__(placeholder="Choose a game mode...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        try:
            await interaction.response.send_modal(GameModal(self.values[0]))
        except Exception as e:
            log.error("ModeSelect error: %s", e)

class ModeView(View):
    def __init__(self, default=None):
        super().__init__(timeout=60)
        self.add_item(ModeSelect(default))


@bot.tree.command(name="play", description="Create a lobby (1v1, 2v2, 3v3, 4v4)")
async def cmd_play(interaction: discord.Interaction):
    try:
        await interaction.response.send_message("Select a game mode:", view=ModeView(), ephemeral=True)
    except Exception as e:
        log.error("play error: %s", e)


@bot.command(name="play")
async def prefix_play(ctx):
    try:
        await ctx.send("Select a game mode:", view=ModeView())
    except Exception as e:
        log.error("!play error: %s", e)


async def _send_prefix_mode(ctx, mode):
    try:
        await ctx.send(f"Select a game mode ({mode} preselected):", view=ModeView(mode))
    except Exception as e:
        log.error("!%s error: %s", mode, e)


@bot.command(name="1v1")
async def prefix_1v1(ctx):
    await _send_prefix_mode(ctx, "1v1")


@bot.command(name="2v2")
async def prefix_2v2(ctx):
    await _send_prefix_mode(ctx, "2v2")


@bot.command(name="3v3")
async def prefix_3v3(ctx):
    await _send_prefix_mode(ctx, "3v3")


@bot.command(name="4v4")
async def prefix_4v4(ctx):
    await _send_prefix_mode(ctx, "4v4")


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
            schedule_recalc(guild)
        player_count = sum(len(g) for g in data.values()) if data else 0
        await interaction.response.send_message(f"✅ Restored scores for {player_count} players! Nicknames being refreshed.", ephemeral=True)
    except Exception as e:
        log.error("restore error: %s", e)


async def ensure_rank_role(guild):
    global rank_role_id
    try:
        role = None
        rid = _guild_setting(guild.id, "rank_role_id") or rank_role_id
        if rid:
            role = guild.get_role(rid)
        if not role:
            role = discord.utils.get(guild.roles, name=ROLE_NAME)
        if not role:
            bot_member = guild.get_member(bot.user.id) if bot.user else None
            if bot_member and bot_member.guild_permissions.manage_roles:
                role = await guild.create_role(name=ROLE_NAME, color=discord.Color.blue(), reason="Auto-created rank role")
            else:
                log.warning("Missing manage_roles permission in %s", guild.name)
                return None
        _set_guild_setting(guild.id, rank_role_id=role.id)
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


_last_auto_rank_msg: dict[int, float] = {}
RANK_MSG_MIN_RESEND_INTERVAL = 6 * 3600

async def ensure_get_rank_channel(guild):
    global rank_message_id, rank_channel_id
    try:
        gs = _guild_settings(guild.id)
        target = None
        cid = gs.get("rank_channel_id") or rank_channel_id
        if cid:
            target = guild.get_channel(cid)
        if not target:
            target = discord.utils.get(guild.text_channels, name="get-rank")
        if not target:
            log.warning("No get-rank channel found in %s; set one with /setrankchannel. Not auto-creating.", guild.name)
            return None
        mid = gs.get("rank_message_id") or rank_message_id
        if mid:
            try:
                await target.fetch_message(mid)
                return target
            except Exception as e:
                log.info("Saved rank message %s unavailable in #%s (%s): %r", mid, getattr(target, "name", "?"), guild.name, e)
        last = _last_auto_rank_msg.get(guild.id, 0.0)
        if time.monotonic() - last < RANK_MSG_MIN_RESEND_INTERVAL:
            log.info("Skipping rank-message recreation in %s (auto-resend cooldown)", guild.name)
            return target
        try:
            msg = await target.send("React with 🏆 to get the **APOSTADO PLAYER** role and access to the bot!\n\nYour nickname will also be tracked with a rank based on points.")
            await msg.add_reaction("🏆")
            _last_auto_rank_msg[guild.id] = time.monotonic()
            _set_guild_setting(guild.id, rank_message_id=msg.id, rank_channel_id=target.id)
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


async def ensure_apostado_role(guild):
    global apostado_role_id
    try:
        role = None
        rid = _guild_setting(guild.id, "apostado_role_id") or apostado_role_id
        if rid:
            role = guild.get_role(rid)
        if not role:
            role = discord.utils.get(guild.roles, name=APOSTADO_ROLE_NAME)
        if not role:
            bot_member = guild.get_member(bot.user.id) if bot.user else None
            if bot_member and bot_member.guild_permissions.manage_roles:
                role = await guild.create_role(name=APOSTADO_ROLE_NAME, color=discord.Color.blue(), reason="Auto-created APOSTADO PLAYER role")
            else:
                log.warning("Missing manage_roles permission in %s", guild.name)
                return None
        _set_guild_setting(guild.id, apostado_role_id=role.id)
        if role.id != apostado_role_id:
            apostado_role_id = role.id
            c = load_config()
            c["apostado_role_id"] = role.id
            save_config(c)
        return role
    except discord.Forbidden:
        log.warning("Forbidden to create/manage APOSTADO role in %s", guild.name)
        return None
    except Exception as e:
        log.error("ensure_apostado_role error in %s: %s", guild.name, e)
        return None


async def _handle_apostado_reaction(guild, user_id, add):
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
        role = guild.get_role(_guild_setting(guild.id, "apostado_role_id") or apostado_role_id)
        if not role:
            role = discord.utils.get(guild.roles, name=APOSTADO_ROLE_NAME)
        if not role:
            role = await ensure_apostado_role(guild)
        if not role:
            return
        bot_member = guild.get_member(bot.user.id) if bot.user else None
        if bot_member and role.position >= bot_member.top_role.position:
            log.warning("APOSTADO role is above bot's top role in %s", guild.name)
            return
        if add:
            ok = await safe_add_role(member, role)
            if not ok:
                log.warning("Failed to add APOSTADO role to %s in %s", member.id, guild.name)
        else:
            ok = await safe_remove_role(member, role)
            if not ok:
                log.warning("Failed to remove APOSTADO role from %s in %s", member.id, guild.name)
    except Exception as e:
        log.error("_handle_apostado_reaction error: %s", e)


def _is_apostado_channel(guild, channel_id):
    ch = guild.get_channel(channel_id)
    if not ch:
        return False
    cid = _guild_setting(guild.id, "apostado_channel_id") or apostado_channel_id
    if cid and channel_id == cid:
        return True
    return "apostado" in ch.name.lower()


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
        role = guild.get_role(_guild_setting(guild.id, "rank_role_id") or rank_role_id)
        if not role:
            role = discord.utils.get(guild.roles, name=ROLE_NAME)
        if not role:
            role = await ensure_rank_role(guild)
        access_role = guild.get_role(_guild_setting(guild.id, "apostado_role_id") or apostado_role_id)
        if not access_role:
            access_role = discord.utils.get(guild.roles, name=APOSTADO_ROLE_NAME)
        if not access_role:
            access_role = await ensure_apostado_role(guild)
        bot_member = guild.get_member(bot.user.id) if bot.user else None
        if role and bot_member and role.position >= bot_member.top_role.position:
            log.warning("Rank role is above bot's top role in %s", guild.name)
            return
        if add:
            if role:
                ok = await safe_add_role(member, role)
                if not ok:
                    log.warning("Failed to add rank role to %s in %s", member.id, guild.name)
            if access_role:
                ok = await safe_add_role(member, access_role)
                if not ok:
                    log.warning("Failed to add access role to %s in %s", member.id, guild.name)
        else:
            if role:
                ok = await safe_remove_role(member, role)
                if not ok:
                    log.warning("Failed to remove rank role from %s in %s", member.id, guild.name)
                if ok:
                    base = strip_rank_prefix(member.display_name)
                    if member.display_name != base and bot_member and member.top_role < bot_member.top_role:
                        await safe_nick_edit(member, base)
            if access_role:
                ok = await safe_remove_role(member, access_role)
                if not ok:
                    log.warning("Failed to remove access role from %s in %s", member.id, guild.name)
        schedule_recalc(guild)
    except Exception as e:
        log.error("_handle_rank_reaction error: %s", e)


def _is_rank_channel(guild, channel_id):
    global rank_channel_id
    ch = guild.get_channel(channel_id)
    if not ch:
        return False
    if ch.name == "get-rank":
        return True
    cid = _guild_setting(guild.id, "rank_channel_id") or rank_channel_id
    if cid:
        return channel_id == cid
    return False


MAINTENANCE_INTERVAL = 900

_music_lock = asyncio.Lock()

def get_audio_source(url):
    if yt_dlp is None:
        raise RuntimeError("yt-dlp is not installed")
    ydl_opts = {"format": "bestaudio/best", "quiet": True, "noplaylist": True}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        stream_url = info.get("url") or info.get("webpage_url")
    return discord.FFmpegPCMAudio(
        stream_url,
        before_options="-stream_loop -1 -reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    )

async def start_stay_music(client):
    if not stay_music_url:
        return
    async with _music_lock:
        if client.is_playing():
            return
        loop = asyncio.get_running_loop()
        def _after(err):
            if err:
                log.error("Playback ended with error: %s", err)
            def _restart():
                asyncio.create_task(start_stay_music(client))
            loop.call_soon_threadsafe(_restart)
        try:
            source = await loop.run_in_executor(None, get_audio_source, stay_music_url)
        except Exception as e:
            log.error("Failed to fetch music source: %s", e)
            return
        try:
            client.play(source, after=_after)
            log.info("Now playing %s in %s", stay_music_url, client.channel)
        except Exception as e:
            log.error("Music play error: %s", e)

async def ensure_stay_voice():
    if not stay_vc_id:
        return
    try:
        for guild in bot.guilds:
            vc = guild.get_channel(stay_vc_id)
            if not vc:
                continue
            client = guild.voice_client
            if not (client and client.channel and client.channel.id == stay_vc_id):
                if client:
                    await client.move_to(vc)
                else:
                    client = await vc.connect()
            if client:
                await start_stay_music(client)
            return
    except Exception as e:
        log.warning("ensure_stay_voice error: %s", e)


@bot.event
async def on_voice_state_update(member, before, after):
    try:
        if member.id != (bot.user.id if bot.user else None):
            return
        if after.channel and after.channel.id == stay_vc_id:
            return
        await ensure_stay_voice()
    except Exception as e:
        log.error("on_voice_state_update error: %s", e)


async def maintenance_loop():
    try:
        while not bot.is_closed():
            await asyncio.sleep(MAINTENANCE_INTERVAL)
            await ensure_stay_voice()
            for guild in list(bot.guilds):
                try:
                    await ensure_rank_role(guild)
                    await ensure_get_rank_channel(guild)
                    await ensure_apostado_role(guild)
                except Exception as e:
                    log.error("maintenance ensure error in %s: %s", guild.name, e)
                schedule_recalc(guild)
    except asyncio.CancelledError:
        pass
    except Exception as e:
        log.error("maintenance loop error: %s", e)


@bot.event
async def on_ready():
    global _maintenance_started
    log.info("%s is online! (%d guilds)", bot.user, len(bot.guilds))
    for g in bot.guilds:
        log.info("  - %s (%s)", g.name, g.id)
    try:
        await bot.tree.sync()
        log.info("Global commands synced successfully")
    except Exception as e:
        log.error("Global sync failed: %s", e)
    for guild in bot.guilds:
        try:
            await ensure_rank_role(guild)
            await ensure_get_rank_channel(guild)
            await ensure_apostado_role(guild)
        except Exception as e:
            log.error("on_ready ensure error in %s: %s", guild.name, e)
        try:
            bot.tree.copy_global_to(guild=guild)
            await bot.tree.sync(guild=guild)
        except Exception as e:
            log.error("sync failed for %s: %s", guild.name, e)
        schedule_recalc(guild)
    try:
        await ensure_stay_voice()
    except Exception as e:
        log.error("on_ready ensure_stay_voice error: %s", e)
    if not _maintenance_started:
        _maintenance_started = True
        _spawn(maintenance_loop())
    log.info("Commands synced to all guilds")


@bot.event
async def on_guild_join(guild):
    try:
        await ensure_rank_role(guild)
        await ensure_get_rank_channel(guild)
        await ensure_apostado_role(guild)
        try:
            bot.tree.copy_global_to(guild=guild)
            await bot.tree.sync(guild=guild)
        except Exception as e:
            log.error("on_guild_join sync error in %s: %s", guild.name, e)
        schedule_recalc(guild)
    except Exception as e:
        log.error("on_guild_join error in %s: %s", guild.name, e)


@bot.event
async def on_member_update(before, after):
    try:
        if after.bot or not after.guild:
            return
        had_rank = bool(before.roles and any(ROLE_NAME in r.name for r in before.roles))
        has_rank = bool(after.roles and any(ROLE_NAME in r.name for r in after.roles))
        if not had_rank and not has_rank:
            return
        guild = after.guild
        bot_member = guild.get_member(bot.user.id) if bot.user else None
        if not bot_member or after.top_role >= bot_member.top_role:
            return
        if had_rank and not has_rank:
            base = strip_rank_prefix(after.display_name)
            if after.display_name != base:
                await safe_nick_edit(after, base)
            return
        if has_rank and not had_rank:
            schedule_recalc(guild)
    except Exception as e:
        log.error("on_member_update error: %s", e)


@bot.event
async def on_member_remove(member):
    try:
        if member.guild:
            schedule_recalc(member.guild)
    except Exception as e:
        log.error("on_member_remove error: %s", e)


@bot.tree.command(name="sync", description="[Admin] Force resync all slash commands")
async def cmd_sync(interaction: discord.Interaction):
    if not admin_check(interaction):
        return await interaction.response.send_message("Only admin.", ephemeral=True)
    try:
        await interaction.response.defer(ephemeral=True)
        await bot.tree.sync()
        await interaction.followup.send("✅ Global commands synced!", ephemeral=True)
    except Exception as e:
        log.error("sync command error: %s", e)
        await interaction.followup.send(f"Sync failed: {e}", ephemeral=True)


@bot.event
async def on_raw_reaction_add(payload):
    try:
        if str(payload.emoji) != "🏆":
            return
        guild = bot.get_guild(payload.guild_id)
        if not guild:
            return
        if _is_rank_channel(guild, payload.channel_id):
            await _handle_rank_reaction(guild, payload.user_id, True)
        elif _is_apostado_channel(guild, payload.channel_id):
            await _handle_apostado_reaction(guild, payload.user_id, True)
    except Exception as e:
        log.error("on_raw_reaction_add error: %s", e)


@bot.event
async def on_raw_reaction_remove(payload):
    try:
        if str(payload.emoji) != "🏆":
            return
        guild = bot.get_guild(payload.guild_id)
        if not guild:
            return
        if _is_rank_channel(guild, payload.channel_id):
            await _handle_rank_reaction(guild, payload.user_id, False)
        elif _is_apostado_channel(guild, payload.channel_id):
            await _handle_apostado_reaction(guild, payload.user_id, False)
    except Exception as e:
        log.error("on_raw_reaction_remove error: %s", e)


@bot.event
async def on_command_error(ctx, error):
    log.warning("Prefix command error: %s", error)


@bot.tree.error
async def on_tree_error(interaction: discord.Interaction, error: Exception):
    log.error("Slash command '%s' error: %s", interaction.command.name if interaction.command else "?", error)
    try:
        msg = "Something went wrong. This has been logged."
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
    except Exception:
        pass


DISCORD_LINK_RE = re.compile(
    r"(?:https?://)?(?:www\.)?"
    r"(?:discord\.(?:gg|com|me|li|io)/|discordapp\.com/(?:invite/)?|invite\.gg/|dsc\.gg/|discord\.link/)"
    r"[a-zA-Z0-9_-]+",
    re.IGNORECASE,
)

@bot.event
async def on_message(message):
    try:
        if not message.guild or message.author.bot:
            return
        if not message.content:
            return
        if not DISCORD_LINK_RE.search(message.content):
            return
        guild = message.guild
        bot_member = guild.get_member(bot.user.id) if bot.user else None
        member = guild.get_member(message.author.id)
        if member and bot_member and member.top_role >= bot_member.top_role:
            log.warning("Cannot ban %s (role too high) for invite link in %s", message.author, guild.name)
            return
        try:
            await message.delete()
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            pass
        try:
            await message.author.ban(reason=f"Sent a Discord server link: {message.content[:200]}")
            log.info("Banned %s (%s) for sending a Discord invite in %s", message.author, message.author.id, guild.name)
        except discord.Forbidden:
            log.warning("Missing ban permission in %s", guild.name)
        except discord.HTTPException as e:
            log.error("Ban failed for %s: %s", message.author, e)
    except Exception as e:
        log.error("on_message invite-ban error: %s", e)
    finally:
        await bot.process_commands(message)


# ── License HTTP API ──────────────────────────────────────────────────
LICENSE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
LICENSE_SECRET  = "M0nSt3rL1c3ns3K3yG3n2024!@#$%"

def generate_license_key():
    raw_id = "".join(random.choice(LICENSE_ALPHABET) for _ in range(8))
    h = hmac.new(LICENSE_SECRET.encode(), (LICENSE_SECRET + raw_id).encode(), hashlib.sha256).digest()
    check = "".join(LICENSE_ALPHABET[b % len(LICENSE_ALPHABET)] for b in h[:8])
    return f"MONSTER-{raw_id}-{check}"

class LicenseHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(body)
        except Exception:
            data = {}
        key = (data.get("key") or "").strip().upper()
        machine_id = (data.get("machine_id") or "").strip().upper()
        resp = self.handle_verify(key, machine_id)
        self.send_json(resp)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/ping":
            self.send_json({"ok": True, "bot": "TitansBot"})
        else:
            self.send_json({"ok": False, "error": "not_found"})

    def handle_verify(self, key, machine_id):
        if not key or not machine_id:
            return {"ok": False, "error": "missing_key_or_machine_id"}
        try:
            db = get_db()
            row = db.execute("SELECT * FROM licenses WHERE key_id = ?", (key,)).fetchone()
            if not row:
                db.close()
                return {"ok": False, "error": "key_not_found"}
            existing_hid = row["machine_id"]
            if existing_hid is None:
                db.execute("UPDATE licenses SET machine_id = ?, activated_at = datetime('now') WHERE key_id = ?", (machine_id, key))
                db.commit()
                db.close()
                return {"ok": True, "status": "activated"}
            if existing_hid == machine_id:
                db.close()
                return {"ok": True, "status": "already_activated_same_pc"}
            db.close()
            return {"ok": False, "error": "key_already_activated", "message": "This key is already activated on another PC"}
        except Exception as e:
            log.error("License verify error: %s", e)
            return {"ok": False, "error": "server_error"}

    def send_json(self, data):
        msg = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(msg)))
        self.end_headers()
        self.wfile.write(msg)

    def log_message(self, fmt, *args):
        log.info("HTTP %s", fmt % args)

def run_http_server():
    port = int(os.getenv("PORT", 8080))
    for i in range(5):
        try:
            server = HTTPServer(("0.0.0.0", port), LicenseHandler)
            log.info("License API server listening on port %d", port)
            server.serve_forever()
            return
        except OSError as e:
            log.warning("HTTP server failed on port %d: %s (attempt %d/5)", port, e, i+1)
            if i < 4:
                time.sleep(3)
    log.error("HTTP server could not start after 5 attempts")

# ── License Slash Commands ────────────────────────────────────────────
@bot.tree.command(name="addadmin", description="[Admin] Grant bot admin access to a user")
async def cmd_addadmin(interaction: discord.Interaction, member: discord.Member):
    if not is_admin_user(interaction):
        return await interaction.response.send_message("You don't have permission.", ephemeral=True)
    global admin_ids
    admin_ids.add(member.id)
    c = load_config()
    c["admin_ids"] = sorted(admin_ids)
    save_config(c)
    await interaction.response.send_message(f"✅ {member.mention} is now a bot admin.", ephemeral=True)


@bot.tree.command(name="removeadmin", description="[Admin] Remove bot admin access from a user")
async def cmd_removeadmin(interaction: discord.Interaction, member: discord.Member):
    if not is_admin_user(interaction):
        return await interaction.response.send_message("You don't have permission.", ephemeral=True)
    global admin_ids
    if member.id == ADMIN_ID:
        return await interaction.response.send_message("The owner cannot be removed.", ephemeral=True)
    admin_ids.discard(member.id)
    c = load_config()
    c["admin_ids"] = sorted(admin_ids)
    save_config(c)
    await interaction.response.send_message(f"Removed {member.mention} from bot admins.", ephemeral=True)


@bot.tree.command(name="genkeys", description="[Admin] Generate N license keys")
async def genkeys(interaction: discord.Interaction, count: int = 1):
    if not is_admin_user(interaction):
        return await interaction.response.send_message("You don't have permission.", ephemeral=True)
    if count < 1 or count > 50:
        return await interaction.response.send_message("Count must be 1-50.", ephemeral=True)
    keys = []
    db = get_db()
    for _ in range(count):
        k = generate_license_key()
        db.execute("INSERT OR IGNORE INTO licenses (key_id, generated_by) VALUES (?, ?)", (k, str(interaction.user.id)))
        keys.append(k)
    db.commit()
    db.close()
    msg = f"**{len(keys)} key(s) generated:**\n```\n" + "\n".join(keys) + "\n```"
    await interaction.response.send_message(msg, ephemeral=True)

@bot.tree.command(name="addkey", description="[Admin] Register an existing license key")
async def addkey(interaction: discord.Interaction, key: str):
    if not is_admin_user(interaction):
        return await interaction.response.send_message("You don't have permission.", ephemeral=True)
    key = key.strip().upper()
    if not (key.startswith("MONSTER-") and len(key) == 25):
        return await interaction.response.send_message("Invalid key format. Expected: MONSTER-XXXXXXXX-XXXXXXXX", ephemeral=True)
    db = get_db()
    try:
        db.execute("INSERT INTO licenses (key_id, generated_by) VALUES (?, ?)", (key, str(interaction.user.id)))
        db.commit()
        await interaction.response.send_message(f"Key `{key}` registered.", ephemeral=True)
    except sqlite3.IntegrityError:
        await interaction.response.send_message("That key already exists in the database.", ephemeral=True)
    finally:
        db.close()

@bot.tree.command(name="listkeys", description="[Admin] List all license keys")
async def listkeys(interaction: discord.Interaction):
    if not is_admin_user(interaction):
        return await interaction.response.send_message("You don't have permission.", ephemeral=True)
    db = get_db()
    rows = db.execute("SELECT * FROM licenses ORDER BY created_at DESC LIMIT 50").fetchall()
    db.close()
    if not rows:
        return await interaction.response.send_message("No keys in database.", ephemeral=True)
    lines = []
    for r in rows:
        status = "ACTIVATED" if r["machine_id"] else "UNUSED"
        lines.append(f"{r['key_id']} [{status}]")
    msg = "**License Keys (last 50):**\n```\n" + "\n".join(lines) + "\n```"
    await interaction.response.send_message(msg, ephemeral=True)


if __name__ == "__main__":
    init_db()
    threading.Thread(target=run_http_server, daemon=True).start()
    async def start():
        _setup_loop_handler()
        attempt = 0
        while True:
            attempt += 1
            try:
                await bot.start(TOKEN)
                return
            except asyncio.CancelledError:
                raise
            except (discord.HTTPException, discord.GatewayNotFound, discord.ConnectionClosed,
                    discord.PrivilegedIntentsRequired, ConnectionError, OSError) as e:
                wait = min(30 * (2 ** attempt), 600)
                jitter = random.uniform(0, 5)
                log.warning("Discord connection error (%s), retrying in %.0fs (attempt %d)", e, wait + jitter, attempt)
            except Exception as e:
                wait = min(30 * (2 ** attempt), 600)
                jitter = random.uniform(0, 5)
                log.critical("Unexpected startup error: %s — retrying in %.0fs (attempt %d)", e, wait + jitter, attempt)
            await asyncio.sleep(wait + jitter)
    asyncio.run(start())
