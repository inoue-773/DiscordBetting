import discord
from discord.ext import commands
import datetime
import pymongo
import os
from dotenv import load_dotenv
import asyncio

# Load environment variables
load_dotenv()
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
MONGODB_URI = os.getenv('MONGODB_URI')

# Set up MongoDB connection
mongo_client = pymongo.MongoClient(MONGODB_URI)
db = mongo_client["betting_bot"]
users_collection = db["users"]
bets_collection = db["bets"]

# Set up Discord client
intents = discord.Intents.all()
intents.typing = False
intents.presences = False
bot = commands.Bot(command_prefix='/', intents=intents)

# Define constants
ADMIN_ROLE_ID = 1182399086503145545  # Replace with your admin role ID
NUM_PARTICIPANTS = 30
ALLOWED_CHANNEL_ID = 1218223013938331708  # Replace with your desired channel ID

# Helper functions
def is_admin(ctx):
    return any(role.id == ADMIN_ROLE_ID for role in ctx.author.roles)

def is_allowed_channel(ctx):
    return ctx.channel.id == ALLOWED_CHANNEL_ID

# Initial setup for users
@bot.event
async def on_ready():
    print(f'{bot.user.name} has connected to Discord!')
    for member in bot.get_all_members():
        user_data = {
            "user_id": member.id,
            "points": 0  # Initial points for each user
        }
        users_collection.update_one({"user_id": member.id}, {"$set": user_data}, upsert=True)

    # Start background task to close expired bets
    bot.loop.create_task(close_expired_bets())

# Define bot commands
@bot.command()
@commands.check(is_admin)
@commands.check(is_allowed_channel)
async def startbet(ctx, title, minutes: int, *player_info):
    if len(player_info) % 2 != 0 or len(player_info) < 4 or len(player_info) > 12:
        await ctx.send("Invalid format. Please provide player IDs and names in the format: <player1_id> <player1_name> <player2_id> <player2_name> ... <player6_id> <player6_name>", ephemeral=True)
        return

    player_ids = []
    player_names = []
    for i in range(0, len(player_info), 2):
        try:
            player_id = int(player_info[i])
        except ValueError:
            await ctx.send(f"Invalid player ID: {player_info[i]}", ephemeral=True)
            return

        if player_id < 1 or player_id > NUM_PARTICIPANTS:
            await ctx.send(f"Invalid player ID {player_id}. Players should be between 1 and {NUM_PARTICIPANTS}.", ephemeral=True)
            return

        player_ids.append(player_id)
        player_names.append(player_info[i + 1])

    time_obj = datetime.datetime.utcnow() + datetime.timedelta(minutes=minutes)

    bet_data = {
        "title": title,
        "time": time_obj,
        "players": player_ids,
        "player_names": player_names,
        "bets": [],
        "result": None
    }

    bets_collection.insert_one(bet_data)
    embed = create_bet_embed(title, minutes, player_ids, player_names)
    await ctx.send(embed=embed)
    timer_task = asyncio.create_task(timer(ctx, time_obj, title))

@bot.command()
@commands.check(is_admin)
@commands.check(is_allowed_channel)
async def forcestop(ctx):
    bets_collection.drop()
    await ctx.send("All ongoing bets have been forcefully stopped.")

@bot.command()
@commands.check(is_admin)
@commands.check(is_allowed_channel)
async def givept(ctx, user: discord.User, amount: int):
    users_collection.update_one({"user_id": user.id}, {"$inc": {"points": amount}})
    await ctx.send(f"Given {amount} points to {user.name}.", ephemeral=True)
    print(f"Error:{ctx.author.name} has given {amount} points to {user.name}")

@bot.command()
@commands.check(is_admin)
@commands.check(is_allowed_channel)
async def takept(ctx, user: discord.User, amount: int):
    user_data = users_collection.find_one({"user_id": user.id})
    if user_data["points"] < amount:
        await ctx.send(f"{user.name} doesn't have enough points to take {amount}.", ephemeral=True)
        return

    users_collection.update_one({"user_id": user.id}, {"$inc": {"points": -amount}})
    await ctx.send(f"Taken {amount} points from {user.name}.", ephemeral=True)
    print(f"Error:{ctx.author.name} has taken {amount} points from {user.name}")

@bot.command()
@commands.check(is_admin)
@commands.check(is_allowed_channel)
async def showpt(ctx, user: discord.User):
    user_data = users_collection.find_one({"user_id": user.id})
    if user_data:
        points = user_data.get("points", 0)
        await ctx.send(f"{user.name}'s current balance is {points} points.", ephemeral=True)
    else:
        await ctx.send(f"{user.name} doesn't have a balance yet.", ephemeral=True)

@bot.command()
@commands.check(is_admin)
@commands.check(is_allowed_channel)
async def betlist(ctx, player: int):
    latest_bet = bets_collection.find_one({"result": None}, sort=[("time", -1)])
    if not latest_bet:
        await ctx.send("No ongoing bet found.", ephemeral=True)
        return

    if player not in latest_bet["players"]:
        await ctx.send(f"Player {player} is not participating in the current bet.", ephemeral=True)
        return

    bettors = [bet["user_id"] for bet in latest_bet["bets"] if bet["player"] == player]

    if not bettors:
        await ctx.send(f"No one has placed a bet on player {player} yet.", ephemeral=True)
    else:
        bettor_list = "\n".join(map(str, bettors))
        await ctx.send(f"Users who placed bets on player {player}:\n{bettor_list}")

@bot.command()
@commands.check(is_admin)
@commands.check(is_allowed_channel)
async def winner(ctx, player_id: int):
    latest_bet = bets_collection.find_one({"result": None}, sort=[("time", -1)])
    if not latest_bet:
        await ctx.send("No ongoing bet found.", ephemeral=True)
        return

    if player_id not in latest_bet["players"]:
        await ctx.send(f"Player {player_id} is not participating in the current bet.", ephemeral=True)
        return

    bets_collection.update_one({"_id": latest_bet["_id"]}, {"$set": {"result": player_id}})

    payouts = calculate_payouts(latest_bet["bets"], player_id)

    for user_id, payout in payouts.items():
        users_collection.update_one({"user_id": user_id}, {"$inc": {"points": payout}})

    await ctx.send(f"Player {player_id} has won the bet '{latest_bet['title']}'.")

@bot.command()
async def result(ctx):
    latest_bet = bets_collection.find_one({"result": {"$ne": None}}, sort=[("time", -1)])
    if not latest_bet:
        await ctx.send("No completed bet found.", ephemeral=True)
        return

    winner_id = latest_bet["result"]
    title = latest_bet["title"]
    player_ids = latest_bet["players"]
    player_names = latest_bet["player_names"]
    time = latest_bet["time"].strftime("%Y-%m-%d %H:%M")

    result_message = f"Bet '{title}' between players: "
    for i, player_id in enumerate(player_ids, start=1):
        result_message += f"{player_id} ({player_names[i - 1]}), "
    result_message = result_message[:-2]  # Remove the trailing comma and space
    result_message += f" at {time}\n\n"

    result_message += f"Winner: Player {winner_id}\n\n" if winner_id != -1 else "Bet expired\n\n"
    result_message += "Bets placed:\n"

    for bet in latest_bet["bets"]:
        user_id = bet["user_id"]
        amount = bet["amount"]
        player = bet["player"]
        result_message += f"User {user_id} bet {amount} points on player {player}\n"

    await ctx.send(result_message)

@bot.command()
async def bet(ctx, player_id: int):
    latest_bet = bets_collection.find_one({"result": None}, sort=[("time", -1)])
    if not latest_bet:
        await ctx.send("No ongoing bet found.", ephemeral=True)
        return

    if player_id not in latest_bet["players"]:
        await ctx.send(f"Player {player_id} is not participating in the current bet.", ephemeral=True)
        return

    user_id = ctx.author.id
    user_data = users_collection.find_one({"user_id": user_id})
    if not user_data:
        await ctx.send("You don't have a balance yet. Join the server to get started.", ephemeral=True)
        return

    interaction_context = await bot.get_context(ctx)
    bet_amount, _ = await interaction_context.send_modal(
        title="Place Bet",
        custom_id=f"bet_{user_id}",
        components=[
            discord.ui.TextInput(
                label="Bet Amount",
                placeholder="Enter the amount of points to bet",
                style=discord.TextStyle.short,
                min_length=1,
                max_length=5,
            )
        ],
    )

    try:
        amount = int(bet_amount.components[0].value)
    except ValueError:
        await bet_amount.response.send_message("Invalid bet amount. Please enter a valid number.", ephemeral=True)
        return

    if user_data["points"] < amount:
        await bet_amount.response.send_message(f"You don't have enough points to bet {amount}.", ephemeral=True)
        return

    bet_data = {
        "user_id": user_id,
        "amount": amount,
        "player": player_id
    }
    bets_collection.update_one({"_id": latest_bet["_id"]}, {"$push": {"bets": bet_data}})
    users_collection.update_one({"user_id": user_id}, {"$inc": {"points": -amount}})

    await bet_amount.response.send_message(f"You have placed a bet of {amount} points on player {player_id}.", ephemeral=True)

@bot.command()
async def balance(ctx):
    user_id = ctx.author.id
    user_data = users_collection.find_one({"user_id": user_id})
    if user_data:
        points = user_data.get("points", 0)
        await ctx.send(f"Your current balance is {points} points.", ephemeral=True)
    else:
        await ctx.send("You don't have a balance yet. Join the server to get started.", ephemeral=True)

# Background task to close expired bets
async def close_expired_bets():
    while True:
        now = datetime.datetime.utcnow()
        expired_bets = bets_collection.find({"time": {"$lt": now}, "result": None})
        for expired_bet in expired_bets:
            bets_collection.update_one({"_id": expired_bet["_id"]}, {"$set": {"result": -1}})  # -1 means expired
            for bet in expired_bet["bets"]:
                user_id = bet["user_id"]
                amount = bet["amount"]
                users_collection.update_one({"user_id": user_id}, {"$inc": {"points": amount}})  # Return bet amounts
            print(f"Closed expired bet: {expired_bet['title']}")
        await asyncio.sleep(60)  # Check every minute

async def timer(ctx, end_time, title):
    countdown_channel = bot.get_channel(ALLOWED_CHANNEL_ID)
    time_remaining = end_time - datetime.datetime.utcnow()
    timer_message = await countdown_channel.send(f"Bet '{title}' ends in {time_remaining}")
    while time_remaining.total_seconds() > 0:
        time_remaining = end_time - datetime.datetime.utcnow()
        minutes, seconds = divmod(time_remaining.total_seconds(), 60)
        new_timer_message = f"Bet '{title}' ends in {int(minutes)}m {int(seconds)}s"
        await timer_message.edit(content=new_timer_message)
        await asyncio.sleep(1)

    await timer_message.edit(content=f"Bet '{title}' closed.")

def create_bet_embed(title, minutes, player_ids, player_names):
    embed = discord.Embed(title=title, url="https://github.com/", description="/bet [player_id] to win the bet", color=0xfbff00)
    embed.add_field(name="Time remaining", value=f"{minutes} minutes", inline=False)

    for i, player_id in enumerate(player_ids, start=1):
        embed.add_field(name=f"Contender {i}", value=player_names[i - 1], inline=True)

    embed.set_image(url="https://i.imgur.com/9lBVS8F.png")
    embed.set_footer(text="Betting Bot Powered by NickyBoy", icon_url="https://i.imgur.com/l67iXkZ.png")
    return embed

def calculate_payouts(bets, winner_id):
    winners_pool = {}
    losers_pool = 0

    for bet in bets:
        user_id = bet["user_id"]
        amount = bet["amount"]
        if bet["player"] == winner_id:
            winners_pool[user_id] = winners_pool.get(user_id, 0) + amount
        else:
            losers_pool += amount

    if not winners_pool:
        return {}

    total_winners_pool = sum(winners_pool.values())
    payouts = {}

    for user_id, amount in winners_pool.items():
        payout = amount + (amount / total_winners_pool) * losers_pool
        payouts[user_id] = int(payout)

    return payouts

# Run the bot
bot.run(DISCORD_TOKEN)
