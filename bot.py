import discord
from discord.ext import commands, tasks
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
    guilds = bot.guilds
    dbList = cluster.list_database_names()
    for guild in guilds:
        thisGuild = removeSpace(guild.name)
        if thisGuild not in dbList:
            collectionName = f"{thisGuild} Points"
            var = cluster[thisGuild]
            var.create_collection(collectionName)
            get_members(guild, var[collectionName])

def get_members(guild, guildCollection):
    posts = []
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
    text = f"## **{title}**の賭けが開始しました\n"
    for i, contender in enumerate(contenders, 1):
        text += f"> /bet {i} (賭けたい額) で \"{contender}\"に賭ける\n"
    text += "> /ptsで現在の所持ポイントを確認\n"
    text += "賭けのやり方は [こちら](https://discord.com/channels/1147925136419336295/1220396387724820492/1220694356063944774)"
    return text

def userInputText(user, amount, contender, percentages):
    return f"{user} が **{amount} ポイントを \"{contender}\" に賭けました！** "

def endText(title, percentages):
    if not percentages:
        return discord.Embed(title="だれも賭けませんでした", description="There were no bets placed for this prediction event.", color=discord.Color.red())

    embed = discord.Embed(title=f"{title} の賭けが終了しました", color=discord.Color.blue())
    embed.add_field(name="合計賭けポイント数", value=f"{globalDict['Total']} points", inline=False)

    for contender, percentage in percentages.items():
        pool = contenderPools[contender]
        embed.add_field(name=contender, value=f"{percentage}% | {len(pool)} bets | {sum(pool.values())} points", inline=False)
    embed.set_image(url="https://i.imgur.com/NhyxuwT.png")
    embed.set_footer(text="Betting Bot by NickyBoy", icon_url="https://i.imgur.com/QfmDKS6.png")

    return embed

def returnWinText(title, result, percentages):
    embed = discord.Embed(title=f"試合の結果: {result} が勝ちました!", color=discord.Color.green())
    embed.add_field(name="タイトル", value=f"{title}", inline=False)
    embed.add_field(name="試合結果", value=result, inline=False)

    if payOutPool:
        maxVal = max(payOutPool.values())
        biggestWinner = max(payOutPool, key=payOutPool.get)
        embed.add_field(name="最大払い戻しポイント数", value=f"{biggestWinner} さん +{maxVal} points", inline=False)
    else:
        embed.add_field(name="最大払い戻しポイント数", value="No bets were placed", inline=False)

    for contender, percentage in percentages.items():
        pool = contenderPools[contender]
        embed.add_field(name=f"{contender} の情報", value=f"割合: {percentage}%\n賭けた人数: {len(pool)}\n賭けポイント合計: {sum(pool.values())} points", inline=True)
    embed.set_image(url="https://i.imgur.com/sFEdFf4.png")
    embed.set_footer(text="Betting Bot by NickyBoy", icon_url="https://i.imgur.com/QfmDKS6.png")

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
    logging.info(f'Bot has logged in as {bot.user}')
    bot.dbList = listGuild()
    addGuild()

@bot.event
async def on_guild_join(guild):
    addGuild()

@bot.slash_command(name='start', description='賭けを開始 管理者専用')
@is_admin()
async def start(ctx, title: discord.Option(str, "試合のタイトル"), timer: discord.Option(int, "賭けの制限時間"), contenders: discord.Option(str, "対戦者の名前をコンマで区切って入力 例: Ritsu, Nicky")):
    if timer <= 0:
        await ctx.respond("0秒以上を指定してください", ephemeral=True)
        return

    contenderList = [c.strip() for c in contenders.split(',')]

    if len(contenderList) < 2 or len(contenderList) > 10:
        await ctx.respond("対戦者は2人から10人の間で指定してください", ephemeral=True)
        return

    if globalDict:
        await ctx.respond("すでに賭けが始まっています。/refundで賭けを終了するか、自動的に終了するまで待ってください。", ephemeral=True)
        return

    bot.predictionDB, bot.betCollection = findTheirGuild(ctx.guild.name)
    if bot.predictionDB is None or bot.betCollection is None:
        await ctx.respond("Guild database not found.", ephemeral=True)
        return

    globalDict['title'] = title
    globalDict['Total'] = 0

    for contender in contenderList:
        contenderPools[contender] = {}

    bot.endTime = datetime.datetime.now() + datetime.timedelta(seconds=timer)

    minutes, secs = divmod(timer, 60)
    timerStr = '{:02d}:{:02d}'.format(minutes, secs)

    text = startText(title, contenderList, timerStr)
    await ctx.respond(text)

    # Send initial countdown timer message
    timerMessage = await ctx.send(f"# 残り時間: {timerStr}")

    # Send initial betting statistics message
    bot.statsMessage = await ctx.send(embed=getBettingStatsEmbed(contenderList))

    # Start the background task to update betting statistics
    bot.update_stats.start(contenderList)

    # Update countdown timer every second
    while datetime.datetime.now() < bot.endTime:
        remaining = (bot.endTime - datetime.datetime.now()).seconds
        minutes, secs = divmod(remaining, 60)
        timerStr = '{:02d}:{:02d}'.format(minutes, secs)
        await timerMessage.edit(content=f"# 残り時間: {timerStr}")
        await asyncio.sleep(1)

    # Stop the background task when the timer ends
    bot.update_stats.stop()

    await ctx.send("~~--------------------------------------------~~")
    await close(ctx)

@tasks.loop(seconds=5)
async def update_stats(contenderList):
    embed = getBettingStatsEmbed(contenderList)
    await bot.statsMessage.edit(embed=embed)

bot.update_stats = update_stats

@bot.slash_command(name='bet', description='誰かに賭ける  例: /bet 1 1000')
async def bet(ctx, contender: discord.Option(int, "賭けたい対戦者の番号を選択", choices=['1', '2', '3', '4', '5', '6', '7', '8', '9', '10'], required = True), amount: discord.Option(int, "賭けたいポイント数を入力", required = True)):
    user = ctx.author.name
    userMention = ctx.author.mention
    if datetime.datetime.now() >= bot.endTime:
        await ctx.respond(f"{userMention} 賭けはすでに終了しています", ephemeral=True)
        return

    contenders = list(contenderPools.keys())
    if contender < 1 or contender > len(contenders):
        await ctx.respond(f"{userMention} 対戦者の番号が違います。対戦者の番号は、統計情報の上に表示されます。", ephemeral=True)
        return

    selectedContender = contenders[contender - 1]
    userDB = bot.betCollection.find_one({"name": user})

    if userDB is None:
        defaultPoints = 0
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
    embed = discord.Embed(title="リアルタイム賭け統計情報", color=discord.Color.blue())
    totalPool = sum(sum(pool.values()) for pool in contenderPools.values())

    for contender in contenders:
        pool = contenderPools[contender]
        totalContenderBets = sum(pool.values())
        percentage = (totalContenderBets / totalPool) * 100 if totalPool > 0 else 0

        if totalContenderBets > 0:
            odds = (totalPool - totalContenderBets) / totalContenderBets
            estimatedPayoutPer100pts = (odds * 100) + 100
            estimatedPayout = estimatedPayoutPer100pts / 100
        else:
            estimatedPayout = 0

        topBettor = max(pool, key=pool.get) if pool else "N/A"
        topBet = max(pool.values()) if pool else 0

        fieldValue = f"**{percentage:.2f}%** | {len(pool)} bets | {totalContenderBets} points\n" \
                     f"オッズ: {estimatedPayout:.2f} 倍\n" \
                     f"Top Bettor: {topBettor} ({topBet} points)"

        embed.add_field(name=f"{contender} 🏆", value=fieldValue, inline=False)

    embed.description = f"合計ポイント: {totalPool} points"
    embed.set_image(url="https://i.imgur.com/tfAhqTW.png")
    embed.set_footer(text="Betting Bot by NickyBoy", icon_url="https://i.imgur.com/QfmDKS6.png")
    return embed

@bot.slash_command(name='close', description='賭けを中断する 管理者のみ')
@is_admin()
async def close(ctx):
    percentages = calculatePercentages()
    embed = endText(globalDict['title'], percentages)
    await ctx.respond(embed=embed)

@bot.slash_command(name='winner', description='試合の勝者を決定 管理者のみ')
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

@bot.slash_command(name='refund', description='全てのポイントを返金 管理者のみ')
@is_admin()
async def refund(ctx):
    refund_dicts()
    resetAllDicts()
    await ctx.respond("賭けが中断されたので返金します")

@bot.slash_command(name='pts', description='今の所持ポイントを確認')
async def askPts(ctx):
    user = ctx.author.name
    userMention = ctx.author.mention
    bot.userDB, bot.userCollection = findTheirGuild(ctx.guild.name)
    userDoc = bot.userCollection.find_one({"name": user})
    
    if userDoc:
        userPoints = userDoc["points"]
    else:
        defaultPoints = 0
        bot.userCollection.insert_one({"name": user, "points": defaultPoints})
        userPoints = defaultPoints
    
    await ctx.respond(f"{userPoints} ポイント賭けられます", ephemeral=True)

@bot.slash_command(name='addpt', description='ポイントを増やす 管理者のみ')
@is_admin()
async def addPts(ctx, member: discord.Member, amount: discord.Option(int, "ここに増やしたいポイント数を入力")):
    bot.userDB, bot.userCollection = findTheirGuild(ctx.guild.name)
    userDoc = bot.userCollection.find_one({"name": member.name})
    
    if userDoc:
        userPoints = userDoc["points"] + amount
    else:
        userPoints = amount
        bot.userCollection.insert_one({"name": member.name, "points": userPoints})
    
    bot.userCollection.update_one({"name": member.name}, {"$set": {"points": userPoints}})

    await ctx.respond(f"{member.name} のポイントを {amount} ポイント増やしました。 この人のアカウントには {userPoints} ポイントあります。")
    admin_name = ctx.author.name
    logging.warning(f"{admin_name} has added {amount} points to {member.name}")

@bot.slash_command(name='reducept', description='ポイントを減らす 管理者のみ')
@is_admin()
async def reducePts(ctx, member: discord.Member, amount: discord.Option(int, "減らしたいポイント数を入力")):
    bot.userDB, bot.userCollection = findTheirGuild(ctx.guild.name)
    userDoc = bot.userCollection.find_one({"name": member.name})
    if userDoc:
        userPoints = userDoc["points"] - amount
        bot.userCollection.update_one({"name": member.name}, {"$set": {"points": userPoints}})
    else:
        await ctx.respond(f"Member {member.name} not found in database.", ephemeral=True)
        return

    await ctx.respond(f"{member.name} のアカウントから {amount} ポイント減らしました。このアカウントには {userPoints} ポイントあります。", ephemeral=True)
    admin_name = ctx.author.name
    logging.warning(f"{admin_name} has reduced {amount} points from {member.name}")

@bot.slash_command(name='balance', description='特定のユーザーのポイントを確認する 管理者のみ')
@is_admin()
async def balance(ctx, member: discord.Member):
    bot.userDB, bot.userCollection = findTheirGuild(ctx.guild.name)
    userDoc = bot.userCollection.find_one({"name": member.name})
    if userDoc:
        userPoints = userDoc["points"]
        message = f"{member.name}'s Balance:\nPoints: {userPoints}"
        await ctx.respond(message, ephemeral=True)
        logging.warning(f"{ctx.author.name} checked {member.name}'s balance. Balance: {userPoints} points.")
    else:
        await ctx.respond(f"Member {member.name} not found in database.", ephemeral=True)

bot.run(TOKEN)
