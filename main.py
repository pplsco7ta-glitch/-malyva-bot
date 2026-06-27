import asyncio
import logging
import json
import os
from datetime import datetime, timedelta
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
from telegram.constants import ParseMode
from googleapiclient.discovery import build

# ===== НАСТРОЙКИ — БЕРУТСЯ ИЗ ПЕРЕМЕННЫХ ОКРУЖЕНИЯ =====
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
OWNER_ID = os.environ.get("OWNER_ID", "136034133")
CHANNEL_ID = "UCAfEHDd7n_oUnsNETWbRdAQ"

# Цели монетизации
GOAL_SUBSCRIBERS = 1000
VIRAL_GROWTH_PERCENT = 200

# Файлы данных
APPROVED_FILE = "approved_users.json"
HISTORY_FILE = "stats_history.json"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ===== УПРАВЛЕНИЕ ДОСТУПОМ =====

def load_approved():
    if os.path.exists(APPROVED_FILE):
        with open(APPROVED_FILE, "r") as f:
            return json.load(f)
    return [OWNER_ID]

def save_approved(approved):
    with open(APPROVED_FILE, "w") as f:
        json.dump(approved, f)

def is_approved(user_id: str) -> bool:
    approved = load_approved()
    return str(user_id) == OWNER_ID or str(user_id) in approved

# ===== ИСТОРИЯ ДАННЫХ =====

def load_history():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "daily_stats": [],
        "video_snapshots": {},
        "known_videos": [],
        "known_comments": [],
        "reached_milestones": [],
    }

def save_history(data):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# ===== YOUTUBE API =====

def get_youtube():
    return build("youtube", "v3", developerKey=YOUTUBE_API_KEY)

def get_channel_stats():
    try:
        youtube = get_youtube()
        response = youtube.channels().list(
            part="statistics,snippet",
            id=CHANNEL_ID
        ).execute()
        if not response["items"]:
            return None
        stats = response["items"][0]["statistics"]
        return {
            "subscribers": int(stats.get("subscriberCount", 0)),
            "total_views": int(stats.get("viewCount", 0)),
            "video_count": int(stats.get("videoCount", 0)),
        }
    except Exception as e:
        logger.error(f"Ошибка YouTube API: {e}")
        return None

def get_recent_videos(max_results=15):
    try:
        import urllib.request
        import json as json_lib
        # Используем RSS фид канала — работает без OAuth
        rss_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={CHANNEL_ID}"
        req = urllib.request.Request(rss_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as response:
            content = response.read().decode("utf-8")
        
        import re
        video_ids = re.findall(r'<yt:videoId>([^<]+)</yt:videoId>', content)
        titles = re.findall(r'<title>([^<]+)</title>', content)[1:]  # skip channel title
        published = re.findall(r'<published>([^<]+)</published>', content)
        
        if not video_ids:
            return []
        
        # Получаем статистику через videos.list
        youtube = get_youtube()
        videos_resp = youtube.videos().list(
            part="statistics,snippet,contentDetails",
            id=",".join(video_ids[:max_results])
        ).execute()
        
        videos = []
        for item in videos_resp["items"]:
            stats = item["statistics"]
            duration = item["contentDetails"]["duration"]
            is_short = "M" not in duration and "H" not in duration
            videos.append({
                "id": item["id"],
                "title": item["snippet"]["title"],
                "published_at": item["snippet"]["publishedAt"],
                "views": int(stats.get("viewCount", 0)),
                "likes": int(stats.get("likeCount", 0)),
                "comments": int(stats.get("commentCount", 0)),
                "is_short": is_short,
                "url": f"https://youtu.be/{item['id']}"
            })
        return videos
    except Exception as e:
        logger.error(f"Ошибка получения видео: {e}")
        return []

def get_top_videos():
    try:
        # Получаем видео через RSS и сортируем по просмотрам
        videos = get_recent_videos(15)
        return sorted(videos, key=lambda x: x["views"], reverse=True)[:5]
    except Exception as e:
        logger.error(f"Ошибка топ видео: {e}")
        return []

def get_new_comments(video_id, max_results=20):
    try:
        youtube = get_youtube()
        response = youtube.commentThreads().list(
            part="snippet",
            videoId=video_id,
            order="time",
            maxResults=max_results
        ).execute()
        comments = []
        for item in response.get("items", []):
            comment = item["snippet"]["topLevelComment"]["snippet"]
            comments.append({
                "id": item["id"],
                "text": comment["textDisplay"],
                "author": comment["authorDisplayName"],
                "published_at": comment["publishedAt"],
                "video_id": video_id
            })
        return comments
    except Exception as e:
        logger.error(f"Ошибка комментариев: {e}")
        return []

# ===== ФОНОВЫЕ ЗАДАЧИ =====

async def background_monitor(app):
    logger.info("Фоновый мониторинг запущен")
    check_count = 0
    last_daily = None
    last_weekly = None

    while True:
        try:
            now = datetime.now()
            history = load_history()
            videos = get_recent_videos(10)

            # Новые видео
            known_ids = set(history.get("known_videos", []))
            new_videos = [v for v in videos if v["id"] not in known_ids]
            for video in new_videos:
                type_icon = "📱 НОВЫЙ SHORTS" if video["is_short"] else "🎬 НОВОЕ ВИДЕО"
                text = (
                    f"🔔 <b>{type_icon} НА КАНАЛЕ!</b>\n\n"
                    f"📌 {video['title']}\n"
                    f"🔗 {video['url']}\n\n"
                    f"⚡ <b>Первые 24 часа решают!</b>\n"
                    f"✅ Проверь описание и теги\n"
                    f"✅ Ответь на первые комментарии\n"
                    f"✅ Поделись в соцсетях"
                )
                await app.bot.send_message(chat_id=OWNER_ID, text=text, parse_mode=ParseMode.HTML)
                known_ids.add(video["id"])
            history["known_videos"] = list(known_ids)

            # Вирал алерт
            snapshots = history.get("video_snapshots", {})
            for video in videos:
                vid_id = video["id"]
                current_views = video["views"]
                if vid_id in snapshots:
                    prev_views = snapshots[vid_id].get("views", 0)
                    if prev_views > 0 and current_views > 500:
                        growth = (current_views - prev_views) / prev_views * 100
                        if growth > VIRAL_GROWTH_PERCENT:
                            text = (
                                f"🚀 <b>ВИРАЛ АЛЕРТ!</b>\n\n"
                                f"📹 {video['title'][:50]}\n"
                                f"🔗 {video['url']}\n\n"
                                f"📈 Рост: +{growth:.0f}%\n"
                                f"👁 Сейчас: {current_views:,} просмотров\n\n"
                                f"✅ Срочно проверь комментарии!\n"
                                f"✅ Сними похожий контент пока горячо!"
                            )
                            await app.bot.send_message(chat_id=OWNER_ID, text=text, parse_mode=ParseMode.HTML)
                snapshots[vid_id] = {"views": current_views, "timestamp": now.isoformat()}
            history["video_snapshots"] = snapshots

            # Комментарии
            known_comments = set(history.get("known_comments", []))
            for video in videos[:3]:
                comments = get_new_comments(video["id"])
                for comment in comments:
                    if comment["id"] not in known_comments:
                        text_lower = comment["text"].lower()
                        has_question = "?" in comment["text"]
                        has_spam = any(x in text_lower for x in ["http", "www.", "t.me", "подпишись на меня"])
                        if has_spam:
                            alert = (
                                f"🚨 <b>СПАМ В КОММЕНТАРИЯХ!</b>\n\n"
                                f"📹 {video['title'][:40]}\n"
                                f"👤 {comment['author']}\n"
                                f"💬 {comment['text'][:100]}\n"
                                f"🔗 https://youtu.be/{video['id']}\n\n"
                                f"⚠️ Рекомендуется удалить!"
                            )
                            await app.bot.send_message(chat_id=OWNER_ID, text=alert, parse_mode=ParseMode.HTML)
                        elif has_question:
                            alert = (
                                f"❓ <b>ВОПРОС В КОММЕНТАРИЯХ!</b>\n\n"
                                f"📹 {video['title'][:40]}\n"
                                f"👤 {comment['author']}:\n"
                                f"💬 {comment['text'][:150]}\n"
                                f"🔗 https://youtu.be/{video['id']}\n\n"
                                f"💡 Быстрый ответ повышает удержание!"
                            )
                            await app.bot.send_message(chat_id=OWNER_ID, text=alert, parse_mode=ParseMode.HTML)
                        known_comments.add(comment["id"])
            history["known_comments"] = list(known_comments)[-200:]

            # Milestone алерты
            channel = get_channel_stats()
            if channel:
                subs = channel["subscribers"]
                milestones = [100, 150, 200, 250, 300, 350, 400, 450, 500]
                reached = history.get("reached_milestones", [])
                for milestone in milestones:
                    if subs >= milestone and milestone not in reached:
                        emoji = "🏆" if milestone == 500 else "🎉"
                        text = (
                            f"{emoji} <b>MILESTONE!</b>\n\n"
                            f"👥 Канал МАЛЮВА набрал <b>{milestone} подписчиков!</b>\n\n"
                            f"{'🎊 ЭТО МОНЕТИЗАЦИЯ!' if milestone == 500 else f'Осталось {GOAL_SUBSCRIBERS - subs} до монетизации!'}\n\n"
                            f"{'🟩' * int(subs/GOAL_SUBSCRIBERS*10)}{'⬜' * (10-int(subs/GOAL_SUBSCRIBERS*10))} {round(subs/GOAL_SUBSCRIBERS*100, 1)}%"
                        )
                        await app.bot.send_message(chat_id=OWNER_ID, text=text, parse_mode=ParseMode.HTML)
                        reached.append(milestone)
                history["reached_milestones"] = reached

                # Ежедневный отчёт в 20:00
                today = now.date()
                if now.hour == 20 and now.minute < 31 and last_daily != today:
                    await send_daily_report(app, channel, videos, history)
                    last_daily = today

                # Еженедельный отчёт в воскресенье 19:00
                this_week = now.isocalendar()[1]
                if now.weekday() == 6 and now.hour == 19 and now.minute < 31 and last_weekly != this_week:
                    await send_weekly_report(app, channel, videos, history)
                    last_weekly = this_week

                # Напоминание о контенте
                check_count += 1
                if check_count % 48 == 0:
                    shorts = [v for v in videos if v["is_short"]]
                    if shorts:
                        last_short = max(shorts, key=lambda x: x["published_at"])
                        published = datetime.fromisoformat(last_short["published_at"].replace("Z", "+00:00"))
                        days_ago = (datetime.now().astimezone() - published).days
                        if days_ago >= 3:
                            text = (
                                f"⏰ <b>НАПОМИНАНИЕ!</b>\n\n"
                                f"Последний Shorts был {days_ago} дней назад!\n\n"
                                f"📱 \"{last_short['title'][:40]}...\"\n\n"
                                f"Алгоритм любит регулярность!\n"
                                f"💡 Цель: минимум 1 Shorts каждые 1-2 дня"
                            )
                            await app.bot.send_message(chat_id=OWNER_ID, text=text, parse_mode=ParseMode.HTML)

            save_history(history)

        except Exception as e:
            logger.error(f"Ошибка мониторинга: {e}")

        await asyncio.sleep(1800)

async def send_daily_report(app, channel, videos, history):
    try:
        now = datetime.now()
        today_str = now.strftime("%d.%m.%Y")
        subs_yesterday = history["daily_stats"][-1].get("subscribers", 0) if history["daily_stats"] else 0
        views_yesterday = history["daily_stats"][-1].get("total_views", 0) if history["daily_stats"] else 0
        subs_growth = channel["subscribers"] - subs_yesterday
        views_growth = channel["total_views"] - views_yesterday
        sorted_videos = sorted(videos, key=lambda x: x["views"], reverse=True)[:3]
        shorts = [v for v in videos if v["is_short"]]
        longs = [v for v in videos if not v["is_short"]]
        subs = channel["subscribers"]
        subs_percent = round(subs / GOAL_SUBSCRIBERS * 100, 1)
        days_to_500 = max(0, (GOAL_SUBSCRIBERS - subs) // max(subs_growth, 1)) if subs_growth > 0 else 0
        forecast = f"~{days_to_500} дней до 500" if subs_growth > 0 else "нет роста сегодня"
        text = (
            f"📊 <b>МАЛЮВА — Дайджест {today_str}</b>\n\n"
            f"👥 Подписчики: {subs} (+{subs_growth})\n"
            f"👁 Просмотры: +{views_growth:,}\n"
            f"🎬 Видео: {channel['video_count']}\n\n"
            f"🏆 <b>ТОП-3:</b>\n"
        )
        medals = ["1️⃣", "2️⃣", "3️⃣"]
        for i, v in enumerate(sorted_videos):
            icon = "📱" if v["is_short"] else "🎥"
            text += f"{medals[i]} {icon} {v['title'][:35]}...\n   👁 {v['views']:,}\n"
        text += (
            f"\n📱 Shorts: {len(shorts)} шт\n"
            f"🎥 Видео: {len(longs)} шт\n\n"
            f"🎯 <b>Монетизация:</b>\n"
            f"{'🟩' * int(subs_percent/10)}{'⬜' * (10-int(subs_percent/10))} {subs_percent}%\n"
            f"⏱ {forecast}"
        )
        await app.bot.send_message(chat_id=OWNER_ID, text=text, parse_mode=ParseMode.HTML)
        history["daily_stats"].append({
            "date": today_str,
            "subscribers": channel["subscribers"],
            "total_views": channel["total_views"],
            "timestamp": now.isoformat()
        })
        history["daily_stats"] = history["daily_stats"][-30:]
    except Exception as e:
        logger.error(f"Ошибка дайджеста: {e}")

async def send_weekly_report(app, channel, videos, history):
    try:
        now = datetime.now()
        subs_week_ago = history["daily_stats"][-7].get("subscribers", 0) if len(history["daily_stats"]) >= 7 else 0
        subs_week_growth = channel["subscribers"] - subs_week_ago
        shorts = [v for v in videos if v["is_short"]]
        longs = [v for v in videos if not v["is_short"]]
        shorts_avg = sum(v["views"] for v in shorts) // max(len(shorts), 1)
        longs_avg = sum(v["views"] for v in longs) // max(len(longs), 1)
        best_short = max(shorts, key=lambda x: x["views"]) if shorts else None
        best_long = max(longs, key=lambda x: x["views"]) if longs else None
        text = (
            f"📅 <b>МАЛЮВА — Недельный отчёт</b>\n\n"
            f"📈 За неделю:\n"
            f"👥 Подписчики: +{subs_week_growth}\n\n"
            f"📱 <b>Shorts ({len(shorts)} шт):</b>\n"
            f"• Среднее: {shorts_avg:,} просмотров\n"
            f"• Лучший: {best_short['title'][:35] + '...' if best_short else 'нет'}\n\n"
            f"🎥 <b>Видео ({len(longs)} шт):</b>\n"
            f"• Среднее: {longs_avg:,} просмотров\n"
            f"• Лучшее: {best_long['title'][:35] + '...' if best_long else 'нет'}\n\n"
            f"🏆 Победитель: {'📱 Shorts' if shorts_avg > longs_avg else '🎥 Видео'}\n\n"
            f"🎯 Цель на неделю: 5 Shorts + 1 длинное видео"
        )
        await app.bot.send_message(chat_id=OWNER_ID, text=text, parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"Ошибка еженедельного отчёта: {e}")

# ===== КЛАВИАТУРА =====

def get_main_keyboard(user_id):
    keyboard = [
        ["📊 Статистика", "🏆 Топ видео"],
        ["🎯 Монетизация", "📱 Shorts vs Видео"],
        ["💬 Комментарии", "TikTok"],
        ["ℹ️ О боте"]
    ]
    if str(user_id) == OWNER_ID:
        keyboard.append(["👥 Управление доступом"])
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# ===== КОМАНДЫ =====

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.message.from_user.id)
    user_name = update.message.from_user.full_name
    username = update.message.from_user.username or "нет"

    if is_approved(user_id):
        await update.message.reply_text(
            f"👋 Привет, {user_name}!\n\nДобро пожаловать в бот МАЛЮВА 🎮\nВыбери что хочешь узнать 👇",
            reply_markup=get_main_keyboard(user_id)
        )
    else:
        await update.message.reply_text("⏳ Запрос отправлен владельцу. Ожидай одобрения...")
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Одобрить", callback_data=f"approve_{user_id}"),
            InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_{user_id}")
        ]])
        await context.bot.send_message(
            chat_id=OWNER_ID,
            text=(
                f"🔔 <b>Запрос на доступ!</b>\n\n"
                f"👤 {user_name}\n"
                f"🆔 {user_id}\n"
                f"📱 @{username}\n\n"
                f"Разрешить доступ?"
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard
        )

async def handle_approval(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if str(query.from_user.id) != OWNER_ID:
        await query.edit_message_text("⛔ Только владелец может управлять доступом.")
        return
    action, user_id = query.data.split("_", 1)
    approved = load_approved()
    if action == "approve":
        if user_id not in approved:
            approved.append(user_id)
            save_approved(approved)
        await query.edit_message_text(f"✅ Пользователь {user_id} одобрен!")
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text="✅ Доступ одобрен! Добро пожаловать в бот МАЛЮВА 🎮",
                reply_markup=get_main_keyboard(user_id)
            )
        except Exception as e:
            logger.error(f"Ошибка уведомления: {e}")
    elif action == "reject":
        if user_id in approved:
            approved.remove(user_id)
            save_approved(approved)
        await query.edit_message_text(f"❌ Пользователь {user_id} отклонён.")
        try:
            await context.bot.send_message(chat_id=user_id, text="❌ Запрос на доступ отклонён.")
        except:
            pass

async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_approved(str(update.message.from_user.id)):
        await update.message.reply_text("⛔ Нет доступа.")
        return
    await update.message.reply_text("⏳ Получаю статистику...")
    channel = get_channel_stats()
    if not channel:
        await update.message.reply_text("❌ Ошибка получения данных.")
        return
    subs = channel["subscribers"]
    subs_percent = round(subs / GOAL_SUBSCRIBERS * 100, 1)
    text = (
        f"📊 <b>Статистика МАЛЮВА</b>\n\n"
        f"👥 Подписчики: <b>{subs}</b>\n"
        f"👁 Просмотры: <b>{channel['total_views']:,}</b>\n"
        f"🎬 Видео: <b>{channel['video_count']}</b>\n\n"
        f"🎯 До монетизации:\n"
        f"{'🟩' * int(subs_percent/10)}{'⬜' * (10-int(subs_percent/10))} {subs_percent}%\n"
        f"Осталось: <b>{max(0, GOAL_SUBSCRIBERS - subs)} подписчиков</b>"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)

async def cmd_top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_approved(str(update.message.from_user.id)):
        return
    await update.message.reply_text("⏳ Загружаю топ видео...")
    videos = get_top_videos()
    if not videos:
        await update.message.reply_text("❌ Ошибка.")
        return
    text = "🏆 <b>Топ видео МАЛЮВА:</b>\n\n"
    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
    for i, v in enumerate(videos[:5]):
        text += (
            f"{medals[i]} {v['title'][:45]}...\n"
            f"   👁 {v['views']:,} | ❤️ {v['likes']:,}\n"
            f"   🔗 https://youtu.be/{v['id']}\n\n"
        )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)

async def cmd_monetization(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_approved(str(update.message.from_user.id)):
        return
    channel = get_channel_stats()
    if not channel:
        await update.message.reply_text("❌ Ошибка.")
        return
    subs = channel["subscribers"]
    subs_percent = round(subs / GOAL_SUBSCRIBERS * 100, 1)
    text = (
        f"🎯 <b>Монетизация МАЛЮВА</b>\n\n"
        f"<b>Подписчики (цель 1000):</b>\n"
        f"{'🟩' * int(subs_percent/10)}{'⬜' * (10-int(subs_percent/10))}\n"
        f"👥 {subs}/{GOAL_SUBSCRIBERS} ({subs_percent}%)\n"
        f"Осталось: {max(0, GOAL_SUBSCRIBERS - subs)}\n\n"
        f"<b>Shorts (цель 10 млн за 90 дней):</b>\n"
        f"📱 Продолжай выкладывать шортсы!\n\n"
        f"<b>Часы просмотра (цель 4000 ч):</b>\n"
        f"⏱ Снимай длинные видео!\n\n"
        f"💡 {'Близко к цели! 🔥' if subs > 800 else 'Продолжай в том же духе!'}"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)

async def cmd_shorts_vs_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_approved(str(update.message.from_user.id)):
        return
    await update.message.reply_text("⏳ Анализирую...")
    videos = get_recent_videos(20)
    shorts = [v for v in videos if v["is_short"]]
    longs = [v for v in videos if not v["is_short"]]
    shorts_avg = sum(v["views"] for v in shorts) // max(len(shorts), 1)
    longs_avg = sum(v["views"] for v in longs) // max(len(longs), 1)
    best_short = max(shorts, key=lambda x: x["views"]) if shorts else None
    best_long = max(longs, key=lambda x: x["views"]) if longs else None
    text = (
        f"📊 <b>Shorts vs Видео</b>\n\n"
        f"📱 <b>SHORTS ({len(shorts)} шт):</b>\n"
        f"• Среднее: {shorts_avg:,} просмотров\n"
        f"• Лучший: {best_short['title'][:35] + '...' if best_short else 'нет'}\n"
        f"  ({best_short['views']:,} просмотров)\n\n"
        f"🎥 <b>ВИДЕО ({len(longs)} шт):</b>\n"
        f"• Среднее: {longs_avg:,} просмотров\n"
        f"• Лучшее: {best_long['title'][:35] + '...' if best_long else 'нет'}\n"
        f"  ({best_long['views']:,} просмотров)\n\n"
        f"🏆 Победитель: {'📱 Shorts' if shorts_avg > longs_avg else '🎥 Видео'}"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)

async def cmd_comments(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_approved(str(update.message.from_user.id)):
        return
    await update.message.reply_text("⏳ Загружаю комментарии...")
    videos = get_recent_videos(3)
    if not videos:
        await update.message.reply_text("❌ Ошибка.")
        return
    latest = videos[0]
    comments = get_new_comments(latest["id"], max_results=5)
    if not comments:
        await update.message.reply_text("💬 Комментариев пока нет.")
        return
    text = f"💬 <b>Последние комментарии:</b>\n📹 {latest['title'][:40]}...\n\n"
    for c in comments[:5]:
        icon = "❓" if "?" in c["text"] else "💬"
        text += f"{icon} <b>{c['author']}:</b>\n{c['text'][:100]}\n\n"
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)

async def cmd_manage_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.message.from_user.id) != OWNER_ID:
        return
    approved = load_approved()
    users = [u for u in approved if u != OWNER_ID]
    if not users:
        await update.message.reply_text("👥 Одобренных пользователей нет.")
        return
    text = "👥 <b>Одобренные пользователи:</b>\n\n"
    keyboard = []
    for user_id in users:
        text += f"🆔 {user_id}\n"
        keyboard.append([InlineKeyboardButton(f"❌ Удалить {user_id}", callback_data=f"reject_{user_id}")])
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))

async def handle_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_approved(str(update.message.from_user.id)):
        await update.message.reply_text("⛔ Нет доступа. Напиши /start")
        return
    text = update.message.text
    if text == "📊 Статистика":
        await cmd_stats(update, context)
    elif text == "🏆 Топ видео":
        await cmd_top(update, context)
    elif text == "🎯 Монетизация":
        await cmd_monetization(update, context)
    elif text == "📱 Shorts vs Видео":
        await cmd_shorts_vs_video(update, context)
    elif text == "💬 Комментарии":
        await cmd_comments(update, context)
    elif text == "👥 Управление доступом":
        await cmd_manage_users(update, context)
    elif text == "TikTok":
        from telegram import InlineKeyboardMarkup, InlineKeyboardButton
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📊 Открыть TikTok Аналитику", url="https://www.tiktok.com/tiktok-studio/analytics")],
            [InlineKeyboardButton("📱 Перейти на канал", url="https://www.tiktok.com/@malyva21")]
        ])
        await update.message.reply_text(
            "<b>TikTok канал МАЛЮВА</b>\n\n"
            "Аккаунт: @malyva21\n\n"
            "Нажми кнопку ниже чтобы открыть аналитику TikTok Studio - "
            "там подписчики, просмотры, вовлеченность и топ видео.",
            parse_mode="HTML",
            reply_markup=keyboard
        )
    elif text == "ℹ️ О боте":
        await update.message.reply_text(
            "ℹ️ <b>Бот канала МАЛЮВА</b>\n\n"
            "📊 Статистика YouTube канала\n"
            "🏆 Топ видео\n"
            "🎯 Прогресс монетизации\n"
            "📱 Анализ Shorts vs Видео\n"
            "💬 Последние комментарии\n"
            "TikTok аналитика\n"
            "🔔 Авто-алерты каждые 30 минут\n\n"
            "YouTube: youtube.com/@malyva21\n"
            "TikTok: tiktok.com/@malyva21",
            parse_mode=ParseMode.HTML
        )

async def post_init(app):
    asyncio.create_task(background_monitor(app))

def main():
    print("🤖 Бот МАЛЮВА запускается...")
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("top", cmd_top))
    app.add_handler(CommandHandler("monetization", cmd_monetization))
    app.add_handler(CallbackQueryHandler(handle_approval, pattern="^(approve|reject)_"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_buttons))
    print("✅ Бот запущен!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
