import logging
from aiogram import Router, F, Bot
from aiogram.filters import Command
from aiogram.types import Message
from config import settings
from database import get_all_users, link_by_username, init_db
from notion_service import notion_service

logger = logging.getLogger(__name__)
router = Router()

def is_admin(user_id: int) -> bool:
    admin_list = settings.admin_id_list
    # If no admins specified, allow all for initial setup
    if not admin_list:
        return True
    return user_id in admin_list

@router.message(Command("admin"))
@router.message(Command("status"))
async def cmd_admin_status(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ У вас нет прав администратора.")
        return

    # Check Notion
    ok, msg, details = await notion_service.verify_connection()
    status_icon = "🟢" if ok else "🔴"

    users = await get_all_users()
    linked_count = sum(1 for u in users if u.get("notion_name") or u.get("notion_user_id"))

    text = (
        f"⚙️ <b>Панель администратора</b>\n\n"
        f"<b>Интеграция Notion:</b> {status_icon}\n"
        f"• Результат: {msg}\n"
    )

    if details:
        text += (
            f"• ID базы: <code>{details.get('id')}</code>\n"
            f"• Доступные свойства: {', '.join(details.get('properties', []))}\n"
        )

    text += (
        f"\n<b>Пользователи Telegram:</b>\n"
        f"• Всего зарегистрировано в боте: <b>{len(users)}</b>\n"
        f"• Связано с профилями Notion: <b>{linked_count}</b>\n\n"
        f"<b>Команды управления:</b>\n"
        f"• /test_task — отправить тестовую карточку задачи\n"
        f"• /set_token <code>секрет</code> — установить токен Notion прямо из чата\n"
        f"• /set_db <code>id_базы</code> — изменить ID базы Notion\n"
        f"• /sync — запустить синхронизацию вручную\n"
        f"• /users — список зарегистрированных пользователей\n"
        f"• /link @username Имя_в_Notion — связать пользователя вручную"
    )

    await message.answer(text, parse_mode="HTML")

@router.message(Command("users"))
async def cmd_list_users(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ У вас нет прав администратора.")
        return

    users = await get_all_users()
    if not users:
        await message.answer("Пользователи еще не запускали бота.")
        return

    lines = ["👥 <b>Список пользователей:</b>\n"]
    for u in users:
        uname = f"@{u['username']}" if u.get("username") else "без @username"
        fname = u.get("full_name") or "Без имени"
        tg_id = u["telegram_id"]
        notion = u.get("notion_name") or "❌ не привязан"
        lines.append(f"• <b>{fname}</b> ({uname}, ID: <code>{tg_id}</code>) ➔ Notion: <i>{notion}</i>")

    await message.answer("\n".join(lines), parse_mode="HTML")

@router.message(Command("sync"))
async def cmd_sync(message: Message, bot: Bot):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ У вас нет прав администратора.")
        return

    await message.answer("⏳ Запуск синхронизации с Notion...")
    try:
        from scheduler import sync_tasks_now
        total, notified = await sync_tasks_now(bot)
        await message.answer(
            f"✅ Синхронизация завершена!\n"
            f"• Найдено задач в Notion: <b>{total}</b>\n"
            f"• Отправлено уведомлений: <b>{notified}</b>",
            parse_mode="HTML"
        )
    except Exception as e:
        await message.answer(f"❌ Ошибка синхронизации: {e}")


@router.message(Command("link"))
async def cmd_manual_link(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ У вас нет прав администратора.")
        return

    parts = message.text.split(maxsplit=2)
    if len(parts) < 3:
        await message.answer(
            "Формат команды:\n"
            "<code>/link @username Имя_в_Notion</code>\n\n"
            "Пример:\n"
            "<code>/link @ivanov Иван Иванов</code>",
            parse_mode="HTML"
        )
        return

    target_username = parts[1]
    notion_name = parts[2]

    success = await link_by_username(target_username, notion_name)
    if success:
        await message.answer(f"✅ Пользователь {target_username} успешно связан с Notion профилем «{notion_name}».")
    else:
        await message.answer(
            f"⚠️ Пользователь {target_username} не найден в базе бота.\n"
            f"Попросите пользователя сначала нажать /start в этом боте, после чего повторите привязку."
        )

@router.message(Command("set_token"))
async def cmd_set_token(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ У вас нет прав администратора.")
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer(
            "Формат команды:\n"
            "<code>/set_token secret_ваш_notion_api_токен</code>",
            parse_mode="HTML"
        )
        return

    new_token = parts[1].strip()
    from database import set_setting
    await set_setting("notion_token", new_token)
    settings.NOTION_TOKEN = new_token
    notion_service.update_token(new_token)

    # Test new token
    await message.answer("⏳ Проверяем новый токен Notion...")
    ok, msg, details = await notion_service.verify_connection()
    if ok:
        await message.answer(
            f"🎉 <b>Токен Notion успешно сохранен и работает!</b>\n\n"
            f"• Статус: {msg}\n"
            f"• База: <b>{details.get('title') if details else ''}</b>",
            parse_mode="HTML"
        )
    else:
        await message.answer(
            f"⚠️ <b>Токен сохранен, но возникла ошибка при обращении к базе:</b>\n\n"
            f"{msg}",
            parse_mode="HTML"
        )

@router.message(Command("set_db"))
async def cmd_set_db(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ У вас нет прав администратора.")
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer(
            "Формат команды:\n"
            "<code>/set_db id_или_ссылка_на_базу</code>",
            parse_mode="HTML"
        )
        return

    new_db = parts[1].strip()
    from database import set_setting
    await set_setting("notion_database_id", new_db)
    settings.NOTION_DATABASE_ID = new_db

    await message.answer(f"✅ База данных Notion изменена на: <code>{new_db}</code>", parse_mode="HTML")
    ok, msg, details = await notion_service.verify_connection()
    await message.answer(f"Результат проверки: {msg}")

@router.message(Command("test_task"))
async def cmd_test_task(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ У вас нет прав администратора.")
        return

    from keyboards import get_task_keyboard
    test_id = "test_demo_task_123"
    msg = (
        f"🧪 <b>Тестовое уведомление о задаче в Notion!</b>\n\n"
        f"📌 <b>Название:</b> Подготовить отчет по проекту\n"
        f"📊 <b>Статус:</b> <code>В процессе</code>\n"
        f"👤 <b>Исполнитель:</b> {message.from_user.full_name}\n"
        f"📅 <b>Дедлайн:</b> <i>2026-09-20</i>\n\n"
        f"<i>Так выглядит карточка задачи, которая автоматически приходит исполнителям при синхронизации.</i>"
    )
    kb = get_task_keyboard(test_id, "https://notion.so")
    await message.answer(msg, parse_mode="HTML", reply_markup=kb)

