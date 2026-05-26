import discord
from discord.ext import commands
from discord.ui import View, Button
import os

TOKEN = os.getenv("ONBOARD_TOKEN", "YOUR_BOT_TOKEN_HERE")

# ─── ONBOARDING STEPS ────────────────────────────────────

STEPS = [
    ("\U0001f4f1 Are you **PC** or **Mobile**?", ["PC", "Mobile"]),
    ("\U0001f9cc Are you **Boy** or **Girl**?", ["Male", "Female"]),
    ("\U0001f9be Are you **+18**?", ["+18", "-18"]),
]

class OnboardView(View):
    def __init__(self, member, guild, step=0):
        super().__init__(timeout=300)
        self.member = member
        self.guild = guild
        self.step = step
        if step < len(STEPS):
            txt, btns = STEPS[step]
            for label in btns:
                b = Button(label=label, style=discord.ButtonStyle.primary)
                b.callback = self._cb(label)
                self.add_item(b)

    def _cb(self, answer):
        async def cb(i: discord.Interaction):
            if i.user.id != self.member.id:
                return await i.response.send_message("Not for you.", ephemeral=True)
            role = discord.utils.get(self.guild.roles, name=answer)
            if role:
                try: await self.member.add_roles(role, reason="Onboarding")
                except: pass
            n = self.step + 1
            if n >= len(STEPS):
                await i.response.edit_message(content="\u2705 You're all set! Enjoy the server.", view=None)
            else:
                txt, btns = STEPS[n]
                await i.response.edit_message(content=txt, view=OnboardView(self.member, self.guild, n))
        return cb

    async def on_timeout(self):
        try: await self.member.send("Timed out. Run `/onboard` to restart.")
        except: pass


# ─── BOT SETUP ───────────────────────────────────────────

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.voice_states = True
bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    print(f"[ONLINE] {bot.user}")
    for guild in bot.guilds:
        bot.tree.copy_global_to(guild=guild)
        await bot.tree.sync(guild=guild)
    print("[SYNCED]")


@bot.event
async def on_member_join(member):
    try:
        await member.send("Welcome! Let's get you set up.", view=OnboardView(member, member.guild))
    except:
        pass


@bot.tree.command(name="onboard", description="Start the onboarding questions")
async def cmd_onboard(interaction: discord.Interaction):
    await interaction.response.send_message("Welcome! Let's get you set up.", view=OnboardView(interaction.user, interaction.guild), ephemeral=True)


@bot.tree.command(name="joinvc", description="Make the bot sit in your voice channel")
async def cmd_joinvc(interaction: discord.Interaction):
    if not interaction.user.voice or not interaction.user.voice.channel:
        return await interaction.response.send_message("You're not in a voice channel!", ephemeral=True)
    vc = interaction.user.voice.channel
    try:
        if interaction.guild.voice_client:
            await interaction.guild.voice_client.move_to(vc)
        else:
            await vc.connect()
        await interaction.response.send_message(f"Joined {vc.name}!", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"Failed: {e}", ephemeral=True)


@bot.tree.command(name="leavevc", description="Make the bot leave voice channel")
async def cmd_leavevc(interaction: discord.Interaction):
    if interaction.guild.voice_client:
        await interaction.guild.voice_client.disconnect()
        await interaction.response.send_message("Left voice channel.", ephemeral=True)
    else:
        await interaction.response.send_message("Not in a voice channel.", ephemeral=True)


if __name__ == "__main__":
    import asyncio, random
    async def start():
        for i in range(10):
            try:
                await bot.start(TOKEN)
                return
            except discord.HTTPException as e:
                if e.status == 429:
                    wait = min(30 * (2 ** i), 600)
                    jitter = random.uniform(0, 5)
                    print(f"Rate limited, retrying in {wait + jitter:.0f}s")
                    await asyncio.sleep(wait + jitter)
                else:
                    raise
    asyncio.run(start())
