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
    dbList = []  # Initialize a local dbList variable
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
    text = f"## Prediction Started: **{title}** Time Left: **{timer}**\n"
    text += "```bash\n"
    for i, contender in enumerate(contenders, 1):
        text += f"Type /bet {i} (amount) to bet on \"{contender}\"\n"
    text += "Type /points to check how many points you have```"
    return text

def userInputText(user, amount, contender, percentages):
    text = f"{user} has added to the pool with **{amount} points! on \"{contender}\"** <:Pog:602691798498017302> \n"
    text += "```autohotkey\n"
    text += f"Total Pool: {globalDict['Total']} points\n"
    for contender, percentage in percentages.items():
        pool = contenderPools[contender]
        text += f"{contender} Percent/People/Amount: {percentage}%, {len(pool)}, {sum(pool.values())}\n"
    text += "```"
    return text

def endText(title, percentages):
    text = f"> Submissions Closed!: **{title}**\n"
    text += "```autohotkey\n"
    text += f"Total Pool: {globalDict['Total']} points\n"
    for contender, percentage in percentages.items():
        pool = contenderPools[contender]
        text += f"{contender} Percent/People/Amount: {percentage}%, {len(pool)}, {sum(pool.values())}\n"
    text += "```"
    return text

def returnWinText(title, result, percentages):
    text = f"```autohotkey\n"
    text += f"Prediction Results: {result} Won!\n"
    text += f"Title: \"{title}?\"\n"
    text += f"Result: \"{result}\"\n"
    maxVal = max(payOutPool.values())
    biggestWinner = max(payOutPool, key=payOutPool.get)
    text += f"Biggest Pay out: {biggestWinner} with +{maxVal} points\n"
    for contender, percentage in percentages.items():
        pool = contenderPools[contender]
        text += f"{contender} Percent/People/Amount: {percentage}%, {len(pool)}, {sum(pool.values())} points\n"
    text += "```"
    return text

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
    bot.dbList = listGuild()  # Assign the result of listGuild() to bot.dbList

@bot.event
async def on_guild_join(guild):
    addGuild()


@bot.command(name='start')
@is_admin()
async def start(ctx, title: str, timer: int, *contenders):
    if timer <= 0:
        await ctx.send("Timer must be greater than 0 seconds.")
        return

    if len(contenders) < 2 or len(contenders) > 10:
        await ctx.send("Number of contenders must be between 2 and 10.")
        return

    bot.predictionDB, bot.betCollection = findTheirGuild(ctx.guild.name)
    globalDict['title'] = title
    globalDict['Total'] = 0
    for contender in contenders:
        contenderPools[contender] = {}
    bot.endTime = datetime.datetime.now() + datetime.timedelta(seconds=timer)

    minutes, secs = divmod(timer, 60)
    timerStr = '{:02d}:{:02d}'.format(minutes, secs)
    text = startText(title, contenders, timerStr)
    message = await ctx.send(text)

    # Send initial betting statistics message
    statsMessage = await ctx.send(embed=getBettingStatsEmbed(contenders))

    # Update countdown timer every second
    while datetime.datetime.now() < bot.endTime:
        remaining = (bot.endTime - datetime.datetime.now()).seconds
        minutes, secs = divmod(remaining, 60)
        timerStr = '{:02d}:{:02d}'.format(minutes, secs)
        await message.edit(content=startText(title, contenders, timerStr))
        await asyncio.sleep(1)

    # Update betting statistics when there is a change
    prevStats = getBettingStats(contenders)
    while datetime.datetime.now() < bot.endTime:
        await asyncio.sleep(5)  # Check for changes every 5 seconds
        currentStats = getBettingStats(contenders)
        if currentStats != prevStats:
            await statsMessage.edit(embed=getBettingStatsEmbed(contenders))
            prevStats = currentStats

    await ctx.send("Prediction event has ended.")
    await ctx.invoke(close)

# ... (rest of the code remains the same)
def getBettingStats(contenders):
    stats = []
    totalBets = sum(sum(pool.values()) for pool in contenderPools.values())

    for contender in contenders:
        pool = contenderPools[contender]
        totalContenderBets = sum(pool.values())
        percentage = (totalContenderBets / totalBets) * 100 if totalBets > 0 else 0
        stats.append((contender, percentage, len(pool), totalContenderBets))

    return stats

def getBettingStatsEmbed(contenders):
    embed = discord.Embed(title="Betting Statistics", color=discord.Color.blue())
    totalBets = sum(sum(pool.values()) for pool in contenderPools.values())

    for contender in contenders:
        pool = contenderPools[contender]
        totalContenderBets = sum(pool.values())
        percentage = (totalContenderBets / totalBets) * 100 if totalBets > 0 else 0

        topBettor = max(pool, key=pool.get) if pool else "N/A"
        topBet = max(pool.values()) if pool else 0

        embed.add_field(name=f"{contender} 🏆", value=f"**{percentage:.2f}%** | {len(pool)} bets | {totalContenderBets} points\nTop Bettor: {topBettor} ({topBet} points)", inline=False)

    embed.description = f"Total Pool: {totalBets} points"
    return embed
    
@bot.command(name='bet')
async def bet(ctx, contender: int, amount: int):
    user = ctx.author.name
    userMention = ctx.author.mention
    if datetime.datetime.now() >= bot.endTime:
        await ctx.send(f"{userMention} Submissions have closed! <:ohwow:602690781224108052>")
        return

    contenders = list(contenderPools.keys())
    if contender < 1 or contender > len(contenders):
        await ctx.send(f"{userMention} Invalid contender number.")
        return

    selectedContender = contenders[contender - 1]
    userDB = bot.betCollection.find_one({"name": user})
    userPoints = userDB["points"]

    if userPoints < amount:
        await ctx.send(f"{userMention} You don't have enough points. You have {userPoints} points.")
        return

    for pool in contenderPools.values():
        if user in pool:
            await ctx.send(f"{userMention} You've already chosen a side. <:PogO:738917913670582323>")
            return

    userPoints -= amount
    bot.betCollection.update_one({"name": user}, {"$set": {"points": userPoints}})
    contenderPools[selectedContender][user] = amount
    globalDict['Total'] += amount

    percentages = calculatePercentages()
    text = userInputText(userMention, amount, selectedContender, percentages)
    await ctx.send(text)

@bot.command(name='close')
@is_admin()
async def close(ctx):
    percentages = calculatePercentages()
    text = endText(globalDict['title'], percentages)
    await ctx.send(text)

@bot.command(name='winner')
@is_admin()
async def winner(ctx, contender: int):
    contenders = list(contenderPools.keys())
    if contender < 1 or contender > len(contenders):
        await ctx.send("Invalid contender number.")
        return

    winnerContender = contenders[contender - 1]
    giveAmountWon(contenderPools[winnerContender])

    percentages = calculatePercentages()
    text = returnWinText(globalDict['title'], winnerContender, percentages)
    await ctx.send(text)

    resetAllDicts()

@bot.command(name='refund')
@is_admin()
async def refund(ctx):
    refund_dicts()
    resetAllDicts()
    await ctx.send("The prediction has ended early, refunding your points. <:FeelsBadMan:692245421170622496>")

@bot.command(aliases=['points', 'pts'])
async def askPts(ctx):
    user = ctx.author.name
    userMention = ctx.author.mention
    bot.userDB, bot.userCollection = findTheirGuild(ctx.guild.name)
    userPoints = bot.userCollection.find_one({"name": user})["points"]
    await ctx.send(f"{userMention} you have {userPoints} points <:money:689308022660399117>")

@bot.command(name='addpt')
@is_admin()
async def addPts(ctx, member: discord.Member, amount: int):
    bot.userDB, bot.userCollection = findTheirGuild(ctx.guild.name)
    userPoints = bot.userCollection.find_one({"name": member.name})["points"] + amount
    bot.userCollection.update_one({"name": member.name}, {"$set": {"points": userPoints}})
    await ctx.send(f"{member.mention} you have {userPoints} points <:money:689308022660399117> <:Pog:602691798498017302>")

    # Log the activity
    admin_name = ctx.author.name
    logging.info(f"{admin_name} has added {amount} points to {member.name}")

@bot.command(name='reducept')
@is_admin()
async def reducePts(ctx, member: discord.Member, amount: int):
    bot.userDB, bot.userCollection = findTheirGuild(ctx.guild.name)
    userPoints = bot.userCollection.find_one({"name": member.name})["points"] - amount
    bot.userCollection.update_one({"name": member.name}, {"$set": {"points": userPoints}})
    await ctx.send(f"{member.mention} you have {userPoints} points <:money:689308022660399117> <:FeelsBadMan:692245421170622496>")

    # Log the activity
    admin_name = ctx.author.name
    logging.info(f"{admin_name} has reduced {amount} points from {member.name}")

@bot.command(name='balance')
@is_admin()
async def balance(ctx, member: discord.Member):
    bot.userDB, bot.userCollection = findTheirGuild(ctx.guild.name)
    userPoints = bot.userCollection.find_one({"name": member.name})["points"]
    
    message = f"{member.name}'s Balance:\nPoints: {userPoints}"
    
    await ctx.send(message, ephemeral=True)
    
    logging.warning(f"{ctx.author.name} checked {member.name}'s balance. Balance: {userPoints} points.")

bot.run(TOKEN)
