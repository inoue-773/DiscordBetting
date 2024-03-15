import discord
from discord.ext import commands
import datetime
import pymongo
import os
from dotenv import load_dotenv

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
intents = discord.Intents.default()
intents.typing = False
intents.presences = False
bot = commands.Bot(command_prefix='/', intents=intents)

# Define constants
ADMIN_ROLE_ID = 1182399086503145545  # Replace with your admin role ID
NUM_PARTICIPANTS = 30

# Helper functions
def is_admin(ctx):
    return any(role.id == ADMIN_ROLE_ID for role in ctx.author.roles)

# Initial setup for users
for i in range(1, NUM_PARTICIPANTS + 1):
    user_data = {
        "user_id": i,
        "points": 1000  # Initial points for each user
    }
    users_collection.insert_one(user_data)

# Define bot commands
@bot.command()
@commands.check(is_admin)
async def startbet(ctx, title, time, player1: int, player2: int):
    if player1 < 1 or player1 > NUM_PARTICIPANTS or player2 < 1 or player2 > NUM_PARTICIPANTS:
        await ctx.send("Invalid player IDs. Players should be between 1 and 30.")
        return

    try:
        time_obj = datetime.datetime.strptime(time, "%Y-%m-%d %H:%M")
    except ValueError:
        await ctx.send("Invalid time format. Use YYYY-MM-DD HH:MM.")
        return

    bet_data = {
        "title": title,
        "time": time_obj,
        "player1": player1,
        "player2": player2,
        "bets": [],
        "result": None
    }

    bets_collection.insert_one(bet_data)
    await ctx.send(f"New bet '{title}' started between player {player1} and player {player2} at {time_obj}.")

@bot.command()
@commands.check(is_admin)
async def forcestop(ctx):
    bets_collection.drop()
    await ctx.send("All ongoing bets have been forcefully stopped.")

@bot.command()
@commands.check(is_admin)
async def givept(ctx, user: int, amount: int):
    if user < 1 or user > NUM_PARTICIPANTS:
        await ctx.send("Invalid user ID. User should be between 1 and 30.")
        return

    users_collection.update_one({"user_id": user}, {"$inc": {"points": amount}})
    await ctx.send(f"Given {amount} points to user {user}.")

@bot.command()
@commands.check(is_admin)
async def takept(ctx, user: int, amount: int):
    if user < 1 or user > NUM_PARTICIPANTS:
        await ctx.send("Invalid user ID. User should be between 1 and 30.")
        return

    user_data = users_collection.find_one({"user_id": user})
    if user_data["points"] < amount:
        await ctx.send(f"User {user} doesn't have enough points to take {amount}.")
        return

    users_collection.update_one({"user_id": user}, {"$inc": {"points": -amount}})
    await ctx.send(f"Taken {amount} points from user {user}.")

@bot.command()
@commands.check(is_admin)
async def betlist(ctx, player: int):
    if player != 1 and player != 2:
        await ctx.send("Player should be either 1 or 2.")
        return

    latest_bet = bets_collection.find_one({"result": None}, sort=[("time", -1)])
    if not latest_bet:
        await ctx.send("No ongoing bet found.")
        return

    player_key = f"player{player}"
    bettors = [bet["user_id"] for bet in latest_bet["bets"] if bet["player"] == latest_bet[player_key]]

    if not bettors:
        await ctx.send(f"No one has placed a bet on player {player} yet.")
    else:
        bettor_list = "\n".join(map(str, bettors))
        await ctx.send(f"Users who placed bets on player {player}:\n{bettor_list}")

@bot.command()
@commands.check(is_admin)
async def winner(ctx, player: int):
    if player != 1 and player != 2:
        await ctx.send("Player should be either 1 or 2.")
        return

    latest_bet = bets_collection.find_one({"result": None}, sort=[("time", -1)])
    if not latest_bet:
        await ctx.send("No ongoing bet found.")
        return

    winner_id = latest_bet[f"player{player}"]
    bets_collection.update_one({"_id": latest_bet["_id"]}, {"$set": {"result": player}})

    for bet in latest_bet["bets"]:
        user_id = bet["user_id"]
        amount = bet["amount"]
        if bet["player"] == winner_id:
            users_collection.update_one({"user_id": user_id}, {"$inc": {"points": amount}})
        else:
            users_collection.update_one({"user_id": user_id}, {"$inc": {"points": -amount}})

    await ctx.send(f"Player {winner_id} has won the bet '{latest_bet['title']}'.")

@bot.command()
async def result(ctx):
    latest_bet = bets_collection.find_one({"result": {"$ne": None}}, sort=[("time", -1)])
    if not latest_bet:
        await ctx.send("No completed bet found.")
        return

    winner_id = latest_bet["result"]
    title = latest_bet["title"]
    player1 = latest_bet["player1"]
    player2 = latest_bet["player2"]
    time = latest_bet["time"].strftime("%Y-%m-%d %H:%M")

    result_message = f"Bet '{title}' between player {player1} and player {player2} at {time}\n\n"
    result_message += f"Winner: Player {winner_id}\n\n"
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
        await ctx.send("Player should be either 1 or 2.")
        return

    user_id = ctx.author.id % NUM_PARTICIPANTS + 1
    user_data = users_collection.find_one({"user_id": user_id})
    if user_data["points"] < amount:
        await ctx.send(f"You don't have enough points to bet {amount}.")
        return

    latest_bet = bets_collection.find_one({"result": None}, sort=[("time", -1)])
    if not latest_bet:
        await ctx.send("No ongoing bet found.")
        return

    if latest_bet[f"player{player}"] != player:
        await ctx.send(f"Player {player} is not participating in the current bet.")
        return

    bet_data = {
        "user_id": user_id,
        "amount": amount,
        "player": latest_bet[f"player{player}"]
    }
    bets_collection.update_one({"_id": latest_bet["_id"]}, {"$push": {"bets": bet_data}})
    users_collection.update_one({"user_id": user_id}, {"$inc": {"points": -amount}})

    await ctx.send(f"You have placed a bet of {amount} points on player {player}.")

# Run the bot
bot.run(DISCORD_TOKEN)
