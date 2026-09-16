import asyncio
import logging
import os
import sys

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from config import settings
from database import init_db
from handlers.common import router as common_router
from handlers.admin import router as admin_router
from scheduler import scheduler_loop
from notion_service import notion_service

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("bot.log", encoding="utf-8")
    ]
)
logger = logging.getLogger("NotionBot")

async def main():
    logger.info("Starting Notion Tasks Telegram Bot...")

    if not settings.BOT_TOKEN:
        logger.error(
            "❌ BOT_TOKEN не указан в файле .env!\n"
            "Пожалуйста, откройте файл .env и укажите ваш TELEGRAM BOT TOKEN (от @BotFather)."
        )
        print("\n" + "="*60)
        print("ОШИБКА: BOT_TOKEN не заполнен в файле .env!")
        print("1. Откройте файл notion_tasks_bot\\.env")
        print("2. Вставьте ваш токен бота: BOT_TOKEN=123456789:ABC...")
        print("3. Вставьте токен Notion: NOTION_TOKEN=secret_...")
        print("="*60 + "\n")
        return

    # Initialize SQLite database
    await init_db()

    from database import get_setting
    saved_token = await get_setting("notion_token")
    if saved_token and not settings.NOTION_TOKEN:
        settings.NOTION_TOKEN = saved_token
        notion_service.update_token(saved_token)
        logger.info("Восстановлен токен Notion из локальной базы данных.")

    saved_db = await get_setting("notion_database_id")
    if saved_db:
        settings.NOTION_DATABASE_ID = saved_db
        logger.info(f"Восстановлен ID базы Notion из базы данных: {saved_db}")

    # Check Notion connectivity
    if settings.NOTION_TOKEN:
        logger.info(f"Checking Notion access with database ID: {settings.clean_notion_database_id}...")
        ok, msg, details = await notion_service.verify_connection()
        if ok:
            logger.info(f"✅ Notion подключен: {msg}")
        else:
            logger.warning(f"⚠️ Предупреждение Notion: {msg}")
    else:
        logger.warning("⚠️ NOTION_TOKEN не указан в .env (можно установить позже через команду /set_token).")

    # Initialize Bot and Dispatcher
    bot = Bot(
        token=settings.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    dp = Dispatcher()

    # Register handlers
    dp.include_router(common_router)
    dp.include_router(admin_router)

    # Start background polling scheduler
    scheduler_task = asyncio.create_task(scheduler_loop(bot))

    # Optional lightweight HTTP server for Render Web Service health checks
    port_str = os.getenv("PORT")
    web_runner = None
    if port_str:
        try:
            from aiohttp import web
            app = web.Application()
            async def handle_health(request):
                return web.Response(text="Notion Telegram Bot is alive and running!")
            app.router.add_get("/", handle_health)
            app.router.add_get("/health", handle_health)
            web_runner = web.AppRunner(app)
            await web_runner.setup()
            site = web.TCPSite(web_runner, "0.0.0.0", int(port_str))
            await site.start()
            logger.info(f"Render health check server started on port {port_str}")
        except Exception as ex:
            logger.error(f"Could not start Render health server on port {port_str}: {ex}")

    logger.info("Bot is ready and polling for updates...")
    try:
        await dp.start_polling(bot)
    finally:
        scheduler_task.cancel()
        if web_runner:
            await web_runner.cleanup()
        await bot.session.close()
        logger.info("Bot stopped.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped manually.")
