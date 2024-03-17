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

def create_bet_embed(title, time, player1, player2):
    embed = discord.Embed(title=title, color=discord.Color.green())
    embed.add_field(name="Players", value=f"Player 1: {player1}\nPlayer 2: {player2}", inline=False)
    embed.add_field(name="Time", value=f"{time.strftime('%Y-%m-%d %H:%M')}", inline=False)
    return embed

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
async def startbet(ctx, title, time, player1: int, player2: int):
    if player1 < 1 or player1 > NUM_PARTICIPANTS or player2 < 1 or player2 > NUM_PARTICIPANTS:
        await ctx.send("Invalid player IDs. Players should be between 1 and 30.", ephemeral=True)
        return

    try:
        time_obj = datetime.datetime.strptime(time, "%Y-%m-%d %H:%M")
    except ValueError:
        await ctx.send("Invalid time format. Use YYYY-MM-DD HH:MM.", ephemeral=True)
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
    embed = create_bet_embed(title, time_obj, player1, player2)
    countdown_task = asyncio.create_task(countdown(ctx, time_obj, title, player1, player2))
    await ctx.send(embed=embed)

# ... (Other commands omitted for brevity)

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

async def countdown(ctx, end_time, title, player1, player2):
    countdown_channel = bot.get_channel(ALLOWED_CHANNEL_ID)
    time_remaining = end_time - datetime.datetime.utcnow()
    while time_remaining.total_seconds() > 0:
        embed = discord.Embed(title=f"{title} Countdown", color=discord.Color.orange())
        embed.add_field(name="Players", value=f"Player 1: {player1}\nPlayer 2: {player2}", inline=False)
        embed.add_field(name="Time Remaining", value=str(time_remaining), inline=False)
        await countdown_channel.send(embed=embed, delete_after=time_remaining.total_seconds())
        await asyncio.sleep(time_remaining.total_seconds())
        time_remaining = end_time - datetime.datetime.utcnow()

# Run the bot
bot.run(DISCORD_TOKEN)
