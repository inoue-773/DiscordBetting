import discord
from discord.ext import commands
import os
import math
import random
from pymongo import MongoClient
import datetime
from dotenv import load_dotenv
import logging
import asyncio

load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logging.basicConfig(level=logging.WARNING, format='%(asctime)s - %(message)s')

intents = discord.Intents.all()
bot = commands.Bot(command_prefix='/', intents=intents, case_insensitive=True)

TOKEN = os.getenv("DISCORD_BOT_TOKEN")
CLUSTER_LINK = os.getenv("MONGODB_CLUSTER_LINK")
ADMIN_ROLE_ID = int(os.getenv("ADMIN_ROLE_ID"))
cluster = MongoClient(CLUSTER_LINK)

globalDict = {}
contenderPools = {}
payOutPool = {}

def is_admin():
    def predicate(ctx):
        return any(role.id == ADMIN_ROLE_ID for role in ctx.author.roles)
    return commands.check(predicate)

def removeSpace(string):
    return string.replace(" ", "")

def findTheirGuild(guildName):
    newGuildNameStr = removeSpace(guildName)
    if newGuildNameStr in bot.dbList:
        db = cluster[newGuildNameStr]
        collection = db[f"{newGuildNameStr} Points"]
        return db, collection
    else:
        return None, None

def listGuild():
    guilds = bot.guilds
    dbList = []
    for guild in guilds:
        guildCutSpace = removeSpace(str(guild.name))
        dbList.append(guildCutSpace)
    return dbList

def addGuild():
    global posts
    guilds = bot.guilds
    dbList = cluster.list_database_names()
    for guild in guilds:
        thisGuild = removeSpace(guild.name)
        posts = []
        if thisGuild not in dbList:
            collectionName = f"{thisGuild} Points"
            var = cluster[thisGuild]
            var.create_collection(collectionName)
            this = var[collectionName]
            get_members(guild, this)

def get_members(guild, guildCollection):
    for person in guild.members:
        existingMember = guildCollection.find_one({"_id": person.id})
        if existingMember is None:
            posts.append({"_id": person.id, "name": person.name, "points": 0})
    if posts:
        guildCollection.insert_many(posts)

def resetAllDicts():
    globalDict.clear()
    contenderPools.clear()
    payOutPool.clear()

def refund_dicts():
    for pool in contenderPools.values():
        for user, amount in pool.items():
            userPoints = bot.betCollection.find_one({"name": user})["points"]
            bot.betCollection.update_one({"name": user}, {"$set": {"points": userPoints + amount}})

def giveAmountWon(winnerPool):
    totalPool = sum(sum(pool.values()) for pool in contenderPools.values())
    winnerSum = sum(winnerPool.values())
    loserSum = totalPool - winnerSum
    distributedPercentage = float(os.getenv("DISTRIBUTED_PERCENTAGE"))
    distributedPool = distributedPercentage * loserSum
    deductedAmount = loserSum - distributedPool

    logging.warning(f"Deducted {deductedAmount} points from the loser's pool.")

    for user, amount in winnerPool.items():
        userPoints = bot.betCollection.find_one({"name": user})["points"]
        share = amount / winnerSum
        payout = share * distributedPool + amount
        bot.betCollection.update_one({"name": user}, {"$set": {"points": userPoints + math.trunc(payout)}})
        payOutPool[user] = math.trunc(payout)

def startText(title, contenders, timer):
    text = f"## **{title}**の賭けが開始しました\n 残り時間: **{timer}**\n"

    for i, contender in enumerate(contenders, 1):
        text += f"> /bet {i} (賭けたい額) で \"{contender}\"に賭ける\n"
    text += "> /ptsで現在の所持ポイントを確認"
    return text

def userInputText(user, amount, contender, percentages):
    text = f"{user} が **{amount} ポイントを \"{contender}\" に賭けました！** "

    return text

def endText(title, percentages):
    if not percentages:
        return discord.Embed(title="No Bets Placed", description="There were no bets placed for this prediction event.", color=discord.Color.red())

    embed = discord.Embed(title=f"Submissions Closed: {title}?", color=discord.Color.blue())
    embed.add_field(name="Total Pool", value=f"{globalDict['Total']} points", inline=False)

    for contender, percentage in percentages.items():
        pool = contenderPools[contender]
        embed.add_field(name=contender, value=f"{percentage}% | {len(pool)} bets | {sum(pool.values())} points", inline=False)

    return embed

def returnWinText(title, result, percentages):
    embed = discord.Embed(title=f"Prediction Results: {result} Won!", color=discord.Color.green())

    embed.add_field(name="Title", value=f"{title}?", inline=False)
    embed.add_field(name="Result", value=result, inline=False)

    if payOutPool:
        maxVal = max(payOutPool.values())
        biggestWinner = max(payOutPool, key=payOutPool.get)
        embed.add_field(name="Biggest Payout", value=f"{biggestWinner} with +{maxVal} points", inline=False)
    else:
        embed.add_field(name="Biggest Payout", value="No bets were placed", inline=False)

    for contender, percentage in percentages.items():
        pool = contenderPools[contender]
        embed.add_field(name=f"{contender} Stats", value=f"Percent: {percentage}%\nPeople: {len(pool)}\nAmount: {sum(pool.values())} points", inline=True)

    return embed

def calculatePercentages():
    totalPool = sum(sum(pool.values()) for pool in contenderPools.values())
    percentages = {}
    for contender, pool in contenderPools.items():
        poolSum = sum(pool.values())
        percentage = (poolSum / totalPool) * 100 if totalPool > 0 else 0
        percentages[contender] = round(percentage, 2)
    return percentages

@bot.event
async def on_ready():
    print(f'Bot has logged in as {bot.user}')
    addGuild()
    bot.dbList = listGuild()

@bot.event
async def on_guild_join(guild):
    addGuild()

@bot.slash_command(name='start', description='Start a new prediction event')
@is_admin()
async def start(ctx, title: str, timer: int, contenders: str):
    if timer <= 0:
        await ctx.respond("Timer must be greater than 0 seconds.", ephemeral=True)
        return

    contenderList = [c.strip() for c in contenders.split(',')]

    if len(contenderList) < 2 or len(contenderList) > 10:
        await ctx.respond("Number of contenders must be between 2 and 10.", ephemeral=True)
        return

    if globalDict:
        await ctx.respond("A prediction event is already running. Please wait for it to finish or use /refund to cancel it.", ephemeral=True)
        return

    bot.predictionDB, bot.betCollection = findTheirGuild(ctx.guild.name)

    globalDict['title'] = title
    globalDict['Total'] = 0

    for contender in contenderList:
        contenderPools[contender] = {}

    bot.endTime = datetime.datetime.now() + datetime.timedelta(seconds=timer)

    minutes, secs = divmod(timer, 60)
    timerStr = '{:02d}:{:02d}'.format(minutes, secs)

    text = startText(title, contenderList, timerStr)

    message = await ctx.send(text)

    # Send initial betting statistics message
    statsMessage = await ctx.send(embed=getBettingStatsEmbed(contenderList))

    # Update countdown timer every second
    while datetime.datetime.now() < bot.endTime:
        remaining = (bot.endTime - datetime.datetime.now()).seconds
        minutes, secs = divmod(remaining, 60)
        timerStr = '{:02d}:{:02d}'.format(minutes, secs)
        await message.edit(content=startText(title, contenderList, timerStr))
        await asyncio.sleep(1)

    # Update betting statistics every 5 seconds
    while datetime.datetime.now() < bot.endTime:
        await statsMessage.edit(embed=getBettingStatsEmbed(contenderList))
        await asyncio.sleep(5)

    await ctx.send("Prediction event has ended.")
    await close(ctx)

@bot.slash_command(name='bet', description='Place a bet on a contender')
async def bet(ctx, contender: int, amount: int):
    user = ctx.author.name
    userMention = ctx.author.mention
    if datetime.datetime.now() >= bot.endTime:
        await ctx.respond(f"{userMention} Submissions have closed! <:ohwow:602690781224108052>", ephemeral=True)
        return

    contenders = list(contenderPools.keys())
    if contender < 1 or contender > len(contenders):
        await ctx.respond(f"{userMention} Invalid contender number.", ephemeral=True)
        return

    selectedContender = contenders[contender - 1]
    userDB = bot.betCollection.find_one({"name": user})

    if userDB is None:
        # User is not in the database, create a new entry with default points
        defaultPoints = 1000  # Adjust the default points as needed
        bot.betCollection.insert_one({"name": user, "points": defaultPoints})
        userPoints = defaultPoints
    else:
        userPoints = userDB["points"]

    if userPoints < amount:
        await ctx.respond(f"ポイントがたりません。 {userPoints} ポイント持っています", ephemeral=True)
        return

    userPoints -= amount
    bot.betCollection.update_one({"name": user}, {"$set": {"points": userPoints}})

    if user in contenderPools[selectedContender]:
        contenderPools[selectedContender][user] += amount
    else:
        contenderPools[selectedContender][user] = amount

    globalDict['Total'] += amount

    percentages = calculatePercentages()
    text = userInputText(userMention, amount, selectedContender, percentages)
    await ctx.respond(text)

def getBettingStatsEmbed(contenders):
    embed = discord.Embed(title="Betting Statistics", color=discord.Color.blue())
    totalPool = sum(sum(pool.values()) for pool in contenderPools.values())

    for contender in contenders:
        pool = contenderPools[contender]
        totalContenderBets = sum(pool.values())
        percentage = (totalContenderBets / totalPool) * 100 if totalPool > 0 else 0

        if totalContenderBets > 0:
            odds = (totalPool - totalContenderBets) / totalContenderBets
            estimatedPayout = (odds * 100) + 100
        else:
            estimatedPayout = 0

        topBettor = max(pool, key=pool.get) if pool else "N/A"
        topBet = max(pool.values()) if pool else 0

        fieldValue = f"**{percentage:.2f}%** | {len(pool)} bets | {totalContenderBets} points\n" \
                     f"Estimated Payout per 100 points: {estimatedPayout:.2f} points\n" \
                     f"Top Bettor: {topBettor} ({topBet} points)"

        embed.add_field(name=f"{contender} 🏆", value=fieldValue, inline=False)

    embed.description = f"Total Pool: {totalPool} points"
    return embed

@bot.slash_command(name='close', description='Close the betting submissions')
@is_admin()
async def close(ctx):
    percentages = calculatePercentages()
    embed = endText(globalDict['title'], percentages)
    await ctx.respond(embed=embed)

@bot.slash_command(name='winner', description='Announce the winner')
@is_admin()
async def winner(ctx, contender: int):
    contenders = list(contenderPools.keys())
    if contender < 1 or contender > len(contenders):
        await ctx.respond("Invalid contender number.")
        return

    winnerContender = contenders[contender - 1]
    giveAmountWon(contenderPools[winnerContender])

    percentages = calculatePercentages()
    embed = returnWinText(globalDict['title'], winnerContender, percentages)
    await ctx.respond(embed=embed)
    resetAllDicts()

@bot.slash_command(name='refund', description='Refund bets')
@is_admin()
async def refund(ctx):
    refund_dicts()
    resetAllDicts()
    await ctx.respond("賭けが中断されたので返金します")

@bot.slash_command(name='pts', description='Check your points')
async def askPts(ctx):
    user = ctx.author.name
    userMention = ctx.author.mention
    bot.userDB, bot.userCollection = findTheirGuild(ctx.guild.name)
    userPoints = bot.userCollection.find_one({"name": user})["points"]
    await ctx.respond(f"{userPoints} ポイント賭けられます", ephemeral=True)

@bot.slash_command(name='addpt', description='Add points to a member')
@is_admin()
async def addPts(ctx, member: discord.Member, amount: int):
    bot.userDB, bot.userCollection = findTheirGuild(ctx.guild.name)
    userPoints = bot.userCollection.find_one({"name": member.name})["points"] + amount
    bot.userCollection.update_one({"name": member.name}, {"$set": {"points": userPoints}})

    # Send ephemeral message to the admin
    await ctx.respond(f"You have added {amount} points to {member.name}. Their new balance is {userPoints} points.", ephemeral=True)

    # Log the activity
    admin_name = ctx.author.name
    logging.info(f"{admin_name} has added {amount} points to {member.name}")

@bot.slash_command(name='reducept', description='Reduce points from a member')
@is_admin()
async def reducePts(ctx, member: discord.Member, amount: int):
    bot.userDB, bot.userCollection = findTheirGuild(ctx.guild.name)
    userPoints = bot.userCollection.find_one({"name": member.name})["points"] - amount
    bot.userCollection.update_one({"name": member.name}, {"$set": {"points": userPoints}})

    # Send ephemeral message to the admin
    await ctx.respond(f"You have reduced {amount} points from {member.name}. Their new balance is {userPoints} points.", ephemeral=True)

    # Log the activity
    admin_name = ctx.author.name
    logging.info(f"{admin_name} has reduced {amount} points from {member.name}")

@bot.slash_command(name='balance', description='Check a member\'s balance')
@is_admin()
async def balance(ctx, member: discord.Member):
    bot.userDB, bot.userCollection = findTheirGuild(ctx.guild.name)
    userPoints = bot.userCollection.find_one({"name": member.name})["points"]

    message = f"{member.name}'s Balance:\nPoints: {userPoints}"

    await ctx.respond(message, ephemeral=True)

    logging.warning(f"{ctx.author.name} checked {member.name}'s balance. Balance: {userPoints} points.")

bot.run(TOKEN)
