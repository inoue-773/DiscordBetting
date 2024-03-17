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
async def startbet(ctx, title, minutes: int, player1: int, player2: int, player1_name: str, player2_name: str):
    if player1 < 1 or player1 > NUM_PARTICIPANTS or player2 < 1 or player2 > NUM_PARTICIPANTS:
        await ctx.send("Invalid player IDs. Players should be between 1 and 30.", ephemeral=True)
        return

    time_obj = datetime.datetime.utcnow() + datetime.timedelta(minutes=minutes)

    bet_data = {
        "title": title,
        "time": time_obj,
        "player1": player1,
        "player2": player2,
        "player1_name": player1_name,
        "player2_name": player2_name,
        "bets": [],
        "result": None
    }

    bets_collection.insert_one(bet_data)
    embed = create_bet_embed(title, minutes, player1, player2, player1_name, player2_name)
    countdown_task = asyncio.create_task(countdown(ctx, time_obj, title, player1, player2, player1_name, player2_name))
    await ctx.send(embed=embed)

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

@bot.command()
@commands.check(is_admin)
@commands.check(is_allowed_channel)
async def betlist(ctx, player: int):
    if player != 1 and player != 2:
        await ctx.send("Player should be either 1 or 2.", ephemeral=True)
        return

    latest_bet = bets_collection.find_one({"result": None}, sort=[("time", -1)])
    if not latest_bet:
        await ctx.send("No ongoing bet found.", ephemeral=True)
        return

    player_key = f"player{player}"
    bettors = [bet["user_id"] for bet in latest_bet["bets"] if bet["player"] == latest_bet[player_key]]

    if not bettors:
        await ctx.send(f"No one has placed a bet on player {player} yet.", ephemeral=True)
    else:
        bettor_list = "\n".join(map(str, bettors))
        await ctx.send(f"Users who placed bets on player {player}:\n{bettor_list}")

@bot.command()
@commands.check(is_admin)
@commands.check(is_allowed_channel)
async def winner(ctx, player: int):
    if player != 1 and player != 2:
        await ctx.send("Player should be either 1 or 2.", ephemeral=True)
        return

    latest_bet = bets_collection.find_one({"result": None}, sort=[("time", -1)])
    if not latest_bet:
        await ctx.send("No ongoing bet found.", ephemeral=True)
        return

    winner_id = latest_bet[f"player{player}"]
    bets_collection.update_one({"_id": latest_bet["_id"]}, {"$set": {"result": player}})

    for bet in latest_bet["bets"]:
        user_id = bet["user_id"]
        amount = bet["amount"]
        if bet["player"] == winner_id:
            users_collection.update_one({"user_id": user_id}, {"$inc": {"points": amount}})  # Winner gets their bet amount
        else:
            pass  # Losers don't lose any points

    await ctx.send(f"Player {winner_id} has won the bet '{latest_bet['title']}'.")

@bot.command()
async def result(ctx):
    latest_bet = bets_collection.find_one({"result": {"$ne": None}}, sort=[("time", -1)])
    if not latest_bet:
        await ctx.send("No completed bet found.", ephemeral=True)
        return

    winner_id = latest_bet["result"]
    title = latest_bet["title"]
    player1 = latest_bet["player1"]
    player2 = latest_bet["player2"]
    time = latest_bet["time"].strftime("%Y-%m-%d %H:%M")

    result_message = f"Bet '{title}' between player {player1} and player {player2} at {time}\n\n"
    result_message += f"Winner: Player {winner_id}\n\n" if winner_id != -1 else "Bet expired\n\n"
    result_message += "Bets placed:\n"

    for bet in latest_bet["bets"]:
        user_id = bet["user_id"]
        amount = bet["amount"]
        player = bet["player"]
        result_message += f"User {user_id} bet {amount} points on player {player}\n"

    await ctx.send(result_message)

@bot.command()
async def bet(ctx, amount: int, player: int):
    if player != 1 and player != 2:
        await ctx.send("Player should be either 1 or 2.", ephemeral=True)
        return

    user_id = ctx.author.id
    user_data = users_collection.find_one({"user_id": user_id})
    if user_data["points"] < amount:
        await ctx.send(f"You don't have enough points to bet {amount}.", ephemeral=True)
        return

    latest_bet = bets_collection.find_one({"result": None}, sort=[("time", -1)])
    if not latest_bet:
        await ctx.send("No ongoing bet found.", ephemeral=True)
        return

    if latest_bet[f"player{player}"] != player:
        await ctx.send(f"Player {player} is not participating in the current bet.", ephemeral=True)
        return

    bet_data = {
        "user_id": user_id,
        "amount": amount,
        "player": latest_bet[f"player{player}"]
    }
    bets_collection.update_one({"_id": latest_bet["_id"]}, {"$push": {"bets": bet_data}})
    users_collection.update_one({"user_id": user_id}, {"$inc": {"points": -amount}})

    await ctx.send(f"You have placed a bet of {amount} points on player {player}.", ephemeral=True)

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

async def countdown(ctx, end_time, title, player1, player2, player1_name, player2_name):
    countdown_channel = bot.get_channel(ALLOWED_CHANNEL_ID)
    time_remaining = end_time - datetime.datetime.utcnow()
    while time_remaining.total_seconds() > 0:
        embed = create_bet_embed(title, time_remaining.total_seconds() // 60, player1, player2, player1_name, player2_name)
        await countdown_channel.send(embed=embed, delete_after=time_remaining.total_seconds())
        await asyncio.sleep(time_remaining.total_seconds())
        time_remaining = end_time - datetime.datetime.utcnow()

def create_bet_embed(title, minutes, player1, player2, player1_name, player2_name):
    embed = discord.Embed(title=title, url="https://github.com/", description="/bet [1 or 2] to win the bet", color=0xfbff00)
    embed.add_field(name="Time remaining", value=f"{minutes} minutes", inline=False)
    embed.add_field(name="Challenger 1", value=player1_name, inline=True)
    embed.add_field(name="Challenger 2", value=player2_name, inline=True)
    embed.set_image(url="https://imgur.com/9lBVS8F")
    embed.set_footer(text="Betting Bot Powered by NickyBoy", icon_url="https://imgur.com/l67iXkZ")
    return embed

# Run the bot
bot.run(DISCORD_TOKEN)
