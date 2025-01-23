import discord
from discord.errors import Forbidden
import irc.bot
import asyncio
from discord.ext import commands
import aiohttp

# Discord bot token
DISCORD_TOKEN = os.environ["TOKEN"]

# IRC server details
IRC_SERVER = "irc.lewdchat.com"
IRC_NICKNAME = "Zerotwo02"

# Set up the Discord bot with commands
intents = discord.Intents.all()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# IRC-to-Discord channel mapping
channel_mapping = {
    "#desis": 1329481910996439061,
    "#lewd": 1331591263056564254,
    "#porn": 1331734838503411743,
    "#nudes": 1331734910364553288,
}

# Discord DM thread and connection mapping
active_dm_threads = {}
messages_from_irc = set()  # Track messages originating from IRC
send_username_to_irc = True  # Toggle for sending Discord usernames to IRC

# Logging webhook URL
LOGGING_WEBHOOK = os.environ["WEB"]

# Main channel ID for threads
THREAD_CHANNEL_ID = 1331834596723265617


class IRCBridgeBot(irc.bot.SingleServerIRCBot):
    def __init__(self):
        server = (IRC_SERVER, 6667)
        irc.bot.SingleServerIRCBot.__init__(self, [server], IRC_NICKNAME, IRC_NICKNAME)

    def on_nicknameinuse(self, connection, event):
        connection.nick(connection.get_nickname() + "_")

    def on_welcome(self, connection, event):
        for irc_channel in channel_mapping.keys():
            connection.join(irc_channel)
        asyncio.run_coroutine_threadsafe(
            send_log("Connected to IRC server and joined channel(s)."), bot.loop
        )

    def on_pubmsg(self, connection, event):
        irc_channel = event.target
        sender = event.source.nick
        message = event.arguments[0]

        discord_channel_id = channel_mapping.get(irc_channel)
        if discord_channel_id:
            content = f"<{sender}> {message}"
            asyncio.run_coroutine_threadsafe(
                self.send_message_to_discord(discord_channel_id, content), bot.loop
            )

    def on_privmsg(self, connection, event):
        sender = event.source.nick
        message = event.arguments[0]

        # Check if a thread for the user already exists
        if sender in active_dm_threads:
            thread = active_dm_threads[sender]
            asyncio.run_coroutine_threadsafe(thread.send(content=f"{sender}: {message}"), bot.loop)
            messages_from_irc.add((thread.id, message))
        else:
            thread_channel = bot.get_channel(THREAD_CHANNEL_ID)
            if isinstance(thread_channel, discord.TextChannel):
                try:
                    thread = asyncio.run_coroutine_threadsafe(
                        thread_channel.create_thread(
                            name=f"DM with {sender}",
                            message=None,
                            type=discord.ChannelType.public_thread,
                        ),
                        bot.loop,
                    ).result()
                    active_dm_threads[sender] = thread
                    asyncio.run_coroutine_threadsafe(
                        thread.send(content=f"New DM from {sender}: {message}"), bot.loop
                    )
                    asyncio.run_coroutine_threadsafe(
                        send_log(f"Thread created for user {sender}. DM: {message}"), bot.loop
                    )
                    messages_from_irc.add((thread.id, message))
                except Forbidden as e:
                    asyncio.run_coroutine_threadsafe(
                        send_log(f"Error: Missing permissions to create a thread. Details: {e}"),
                        bot.loop,
                    )

    async def send_message_to_discord(self, discord_channel_id, content):
        channel = bot.get_channel(discord_channel_id)
        if channel:
            await asyncio.sleep(4)  # Add a 4-second delay before sending the message
            await channel.send(content)

    def send_message_to_irc(self, irc_channel, content):
        self.connection.privmsg(irc_channel, content)

    def delete_dm_thread(self, user_nick):
        if user_nick in active_dm_threads:
            asyncio.run_coroutine_threadsafe(
                active_dm_threads[user_nick].delete(), bot.loop
            ).result()
            del active_dm_threads[user_nick]


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    loop = asyncio.get_running_loop()
    loop.run_in_executor(None, irc_bridge.start)
    await send_log(f"Discord bot logged in as {bot.user}")


@bot.event
async def on_message(message):
    # Ignore bot's own messages
    if message.author == bot.user:
        return

    # Check if the message is from a mapped Discord channel
    for irc_channel, discord_channel_id in channel_mapping.items():
        if message.channel.id == discord_channel_id:
            # Determine the content format based on toggle status
            content = f"<{message.author}> {message.content}" if send_username_to_irc else message.content
            irc_bridge.send_message_to_irc(irc_channel, content)
            await send_log(
                f"Message from Discord sent to IRC ({irc_channel}): {content}"
            )
            break

    # Process messages in active threads
    if message.channel.id in [thread.id for thread in active_dm_threads.values()]:
        if (message.channel.id, message.content) in messages_from_irc:
            messages_from_irc.discard((message.channel.id, message.content))
            return

        user_nick = next(
            (nick for nick, thread in active_dm_threads.items() if thread.id == message.channel.id),
            None,
        )
        if user_nick:
            irc_bridge.connection.privmsg(user_nick, message.content)
            await send_log(f"Message sent to IRC user {user_nick}: {message.content}")
    await bot.process_commands(message)


@bot.command(name="db")
async def delete_bridge(ctx, user_nick: str):
    if user_nick in active_dm_threads:
        thread = active_dm_threads.pop(user_nick)
        await thread.delete()
        await send_log(f"DM bridge for {user_nick} deleted by {ctx.author}.")
        await ctx.send(f"DM bridge for {user_nick} has been deleted.")
    else:
        await ctx.send(f"No DM bridge found for {user_nick}.")


@bot.command(name="cb")
async def create_bridge(ctx, user_nick: str):
    if user_nick not in active_dm_threads:
        thread_channel = bot.get_channel(THREAD_CHANNEL_ID)
        if isinstance(thread_channel, discord.TextChannel):
            try:
                thread = await thread_channel.create_thread(
                    name=f"DM with {user_nick}",
                    message=None,
                    type=discord.ChannelType.public_thread,
                )
                active_dm_threads[user_nick] = thread
                await send_log(f"DM bridge created for {user_nick} by {ctx.author}.")
                await ctx.send(f"DM bridge created for {user_nick}.")
            except Forbidden as e:
                await send_log(f"Error: Missing permissions to create a thread. Details: {e}")
        else:
            await ctx.send("The thread channel is not found or is invalid.")
    else:
        await ctx.send(f"DM bridge already exists for {user_nick}.")


@bot.command(name="tu")
async def toggle_username(ctx):
    global send_username_to_irc
    send_username_to_irc = not send_username_to_irc
    status = "on" if send_username_to_irc else "off"
    await send_log(f"Username sending toggled to {status} by {ctx.author}.")
    await ctx.send(f"Sending Discord usernames to IRC is now {status}.")


async def send_log(message):
    async with aiohttp.ClientSession() as session:
        webhook = discord.Webhook.from_url(LOGGING_WEBHOOK, session=session)
        await webhook.send(message, username="Logger")


irc_bridge = IRCBridgeBot()

async def run_bot():
    await bot.start(DISCORD_TOKEN)


loop = asyncio.get_event_loop()
loop.create_task(run_bot())
loop.run_in_executor(None, irc_bridge.start)
loop.run_forever()
