"""
╔══════════════════════════════════════════════════╗
║         TELEGRAM БОТ-ПИНГЕР v1.0                 ║
║   Пинг сам себя или другого бота каждые 30 сек   ║
╚══════════════════════════════════════════════════╝
"""
import logging
import asyncio
import aiohttp
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
import os

BOT_TOKEN = os.environ.get("PINGER_BOT_TOKEN")
TARGET_URL = os.environ.get("TARGET_URL")
PING_INTERVAL = int(os.environ.get("PING_INTERVAL", 30))

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

ping_task = None

async def ping_target():
    """Отправляет GET-запрос к целевому URL"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(TARGET_URL, timeout=10) as response:
                logger.info(f"✅ Пинг {TARGET_URL} - статус: {response.status}")
                return response.status
    except Exception as e:
        logger.error(f"❌ Ошибка пинга: {e}")
        return None

async def scheduled_pinger():
    """Фоновый процесс, который пингует каждые N секунд"""
    while True:
        await ping_target()
        await asyncio.sleep(PING_INTERVAL)

async def start_pinger(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Запускает пингер вручную"""
    global ping_task
    if ping_task and not ping_task.done():
        await update.message.reply_text("🔄 Пингер уже запущен!")
        return
    
    ping_task = asyncio.create_task(scheduled_pinger())
    await update.message.reply_text(
        f"✅ Пингер запущен!\n"
        f"🎯 Цель: {TARGET_URL}\n"
        f"⏱️ Интервал: {PING_INTERVAL} сек."
    )

async def stop_pinger(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Останавливает пингер"""
    global ping_task
    if ping_task and not ping_task.done():
        ping_task.cancel()
        ping_task = None
        await update.message.reply_text("🛑 Пингер остановлен!")
    else:
        await update.message.reply_text("❌ Пингер не был запущен")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает статус пингера"""
    if ping_task and not ping_task.done():
        await update.message.reply_text(
            f"🟢 Пингер активен\n"
            f"🎯 Цель: {TARGET_URL}\n"
            f"⏱️ Интервал: {PING_INTERVAL} сек."
        )
    else:
        await update.message.reply_text("🔴 Пингер не активен")

async def ping_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Выполняет один пинг вручную"""
    await update.message.reply_text("🔄 Выполняю пинг...")
    status_code = await ping_target()
    if status_code:
        await update.message.reply_text(f"✅ Пинг выполнен! HTTP статус: {status_code}")
    else:
        await update.message.reply_text("❌ Ошибка при пинге! Проверь URL.")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Приветственное сообщение"""
    await update.message.reply_text(
        f"🤖 *Бот-пингер v1.0*\n\n"
        f"🎯 Цель: `{TARGET_URL}`\n"
        f"⏱️ Интервал: {PING_INTERVAL} сек.\n\n"
        f"*Команды:*\n"
        f"`/start_pinger` — запустить пингер\n"
        f"`/stop_pinger` — остановить пингер\n"
        f"`/ping_now` — пинг сейчас\n"
        f"`/status` — статус пингера",
        parse_mode="Markdown"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)

def main():
    """Главная функция"""
    if not BOT_TOKEN:
        logger.error("❌ Ошибка: PINGER_BOT_TOKEN не настроен!")
        return
    
    if not TARGET_URL:
        logger.error("❌ Ошибка: TARGET_URL не настроен!")
        return
    
    # Создаём приложение
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Регистрируем команды
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("start_pinger", start_pinger))
    app.add_handler(CommandHandler("stop_pinger", stop_pinger))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("ping_now", ping_now))
    
    # Запускаем бота
    logger.info("✅ Бот-пингер запускается...")
    logger.info(f"🎯 Цель: {TARGET_URL}")
    logger.info(f"⏱️ Интервал: {PING_INTERVAL} сек.")
    
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
