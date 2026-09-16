import logging
from aiogram import Router, F, Bot
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery
from database import (
    register_user,
    get_user_by_telegram_id,
    link_notion_to_telegram,
    get_active_tasks_for_user
)
from notion_service import notion_service
from keyboards import get_main_menu_keyboard, get_link_users_keyboard, get_task_keyboard

logger = logging.getLogger(__name__)
router = Router()

@router.message(CommandStart())
async def cmd_start(message: Message):
    user = message.from_user
    if not user:
        return

    # Register or update Telegram user
    await register_user(
        telegram_id=user.id,
        username=user.username,
        full_name=user.full_name
    )

    db_user = await get_user_by_telegram_id(user.id)
    linked_notion = db_user.get("notion_name") if db_user else None

    text = (
        f"👋 Здравствуйте, <b>{user.full_name}</b>!\n\n"
        f"Я бот для получения уведомлений о задачах из <b>Notion</b>.\n"
    )

    if linked_notion:
        text += (
            f"✅ Ваш Telegram привязан к профилю Notion: <b>{linked_notion}</b>\n\n"
            f"Как только в Notion на вас будет назначена задача или изменен ее статус, "
            f"вам придет персональное уведомление здесь."
        )
    else:
        text += (
            f"ℹ️ Ваш аккаунт пока не привязан к конкретному профилю Notion.\n"
            f"Если в базе Notion в колонке исполнителя указан ваш username (@{user.username or 'не задан'}), "
            f"уведомления будут приходить автоматически.\n\n"
            f"Также вы можете выбрать свой профиль вручную, нажав кнопку ниже:"
        )

    await message.answer(text, parse_mode="HTML", reply_markup=get_main_menu_keyboard())

@router.message(Command("my_tasks"))
async def cmd_my_tasks(message: Message):
    user_id = message.from_user.id
    tasks = await get_active_tasks_for_user(user_id)

    if not tasks:
        await message.answer(
            "🎉 У вас нет активных незавершенных задач в кэше Notion.\n"
            "Если вам только что назначили задачу, нажмите «🔄 Синхронизировать сейчас».",
            reply_markup=get_main_menu_keyboard()
        )
        return

    await message.answer(f"📋 <b>Ваши активные задачи ({len(tasks)}):</b>", parse_mode="HTML")
    for t in tasks:
        due = f"📅 Дедлайн: {t['due_date']}\n" if t.get("due_date") else ""
        msg = (
            f"📌 <b>{t['title']}</b>\n"
            f"📊 Статус: <i>{t['status']}</i>\n"
            f"{due}"
        )
        kb = get_task_keyboard(t["task_id"], f"https://www.notion.so/{t['task_id'].replace('-', '')}")
        await message.answer(msg, parse_mode="HTML", reply_markup=kb)

@router.callback_query(F.data == "btn_my_tasks")
async def cb_my_tasks(callback: CallbackQuery):
    await callback.answer()
    tasks = await get_active_tasks_for_user(callback.from_user.id)
    if not tasks:
        await callback.message.answer(
            "🎉 У вас нет активных незавершенных задач.\n"
            "Новые задачи будут приходить автоматически при появлении в Notion."
        )
        return

    await callback.message.answer(f"📋 <b>Ваши активные задачи ({len(tasks)}):</b>", parse_mode="HTML")
    for t in tasks:
        due = f"📅 Дедлайн: {t['due_date']}\n" if t.get("due_date") else ""
        msg = (
            f"📌 <b>{t['title']}</b>\n"
            f"📊 Статус: <i>{t['status']}</i>\n"
            f"{due}"
        )
        kb = get_task_keyboard(t["task_id"], f"https://www.notion.so/{t['task_id'].replace('-', '')}")
        await callback.message.answer(msg, parse_mode="HTML", reply_markup=kb)

@router.callback_query(F.data == "btn_link_account")
async def cb_link_account(callback: CallbackQuery):
    await callback.answer()
    users = await notion_service.get_workspace_users()
    if not users:
        await callback.message.answer(
            "⚠️ Не удалось получить список пользователей из Notion.\n"
            "Убедитесь, что токен Notion указан и бот имеет доступ к рабочей области."
        )
        return

    await callback.message.answer(
        "Выберите ваш профиль в Notion из списка:",
        reply_markup=get_link_users_keyboard(users)
    )

@router.callback_query(F.data.startswith("link_me:"))
async def cb_link_selected(callback: CallbackQuery):
    uid = callback.data.split(":", 1)[1]
    users = await notion_service.get_workspace_users()
    matched = next((u for u in users if u["id"] == uid), None)

    if matched:
        await link_notion_to_telegram(
            telegram_id=callback.from_user.id,
            notion_user_id=uid,
            notion_name=matched.get("name"),
            notion_email=matched.get("email")
        )
        await callback.answer("Привязка успешна!")
        await callback.message.edit_text(
            f"✅ Профиль успешно привязан!\n"
            f"👤 Имя в Notion: <b>{matched.get('name')}</b>\n"
            f"📧 Email: {matched.get('email') or 'не указан'}\n\n"
            f"Теперь задачи, назначенные на этого пользователя, будут приходить вам.",
            parse_mode="HTML"
        )
    else:
        await callback.answer("Пользователь не найден", show_alert=True)

@router.callback_query(F.data == "cancel_link")
async def cb_cancel_link(callback: CallbackQuery):
    await callback.answer("Отменено")
    await callback.message.delete()

@router.callback_query(F.data.startswith("status:"))
async def cb_status_change(callback: CallbackQuery):
    # status:<action>:<task_id>
    parts = callback.data.split(":")
    if len(parts) < 3:
        await callback.answer()
        return

    action = parts[1]
    task_id = parts[2]

    new_status = "In Progress" if action == "in_progress" else "Done"
    display_status = "🚀 В работе" if action == "in_progress" else "✅ Выполнено"

    if task_id.startswith("test_demo_task"):
        await callback.answer("Тест: статус обновлен!")
        await callback.message.reply(
            f"Тестовый статус обновлен на: <b>{display_status}</b>!\n<i>(В боевом режиме здесь сразу обновится статус задачи в Notion)</i>",
            parse_mode="HTML"
        )
        return

    await callback.answer("Обновление в Notion...")
    success = await notion_service.update_task_status(task_id, new_status)
    if not success:
        # Try Russian variants if database has Russian status options
        alt_status = "В работе" if action == "in_progress" else "Выполнено"
        success = await notion_service.update_task_status(task_id, alt_status)

    if success:
        await callback.message.reply(
            f"Статус задачи обновлен на: <b>{display_status}</b> в Notion!",
            parse_mode="HTML"
        )
    else:
        await callback.message.reply(
            f"⚠️ Не удалось обновить статус в Notion. Проверьте права доступа интеграции или наименование статусов."
        )

@router.callback_query(F.data == "btn_sync_now")
async def cb_sync_now(callback: CallbackQuery, bot: Bot):
    await callback.answer("Синхронизация с Notion...")
    try:
        from scheduler import sync_tasks_now
        total, notified = await sync_tasks_now(bot)
        await callback.message.answer(
            f"✅ <b>Синхронизация завершена!</b>\n"
            f"• Задач в базе Notion: <b>{total}</b>\n"
            f"• Отправлено новых уведомлений: <b>{notified}</b>",
            parse_mode="HTML"
        )
    except Exception as e:
        await callback.message.answer(f"❌ Ошибка синхронизации: {e}")

