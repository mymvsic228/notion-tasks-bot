import logging
from typing import List, Dict, Any, Optional
from aiogram import Router, F, Bot
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery
from database import (
    register_user,
    get_user_by_telegram_id,
    link_notion_to_telegram,
    get_active_tasks_for_user,
    update_task_status_cache
)
from notion_service import notion_service
from keyboards import (
    get_main_menu_keyboard,
    get_link_users_keyboard,
    get_paginated_task_keyboard,
    get_task_keyboard
)

logger = logging.getLogger(__name__)
router = Router()

def format_main_menu_text(full_name: str, username: Optional[str], linked_notion: Optional[str]) -> str:
    text = (
        f"👋 Здравствуйте, <b>{full_name}</b>!\n\n"
        f"Я бот для получения уведомлений и управления задачами из <b>Notion</b>.\n"
    )
    if linked_notion:
        text += (
            f"✅ Ваш Telegram привязан к профилю Notion: <b>{linked_notion}</b>\n\n"
            f"Используйте кнопки ниже для просмотра своих активных задач или синхронизации с базой."
        )
    else:
        text += (
            f"ℹ️ Ваш аккаунт пока не привязан к конкретному профилю Notion.\n"
            f"Нажмите кнопку <b>«🔗 Привязать Notion»</b> ниже и выберите свое имя."
        )
    return text

def format_task_card_text(task: Dict[str, Any], current_idx: int, total_count: int) -> str:
    title = task.get("title") or "Без названия"
    status = task.get("status") or "Не указан"
    etap = task.get("etap")
    due_date = task.get("due_date")
    assignee = task.get("assignee_name") or "Вы"

    etap_part = f"\n🎬 <b>Этап:</b> <i>{etap}</i>" if etap else ""
    due_part = f"\n📅 <b>Дедлайн:</b> <code>{due_date}</code>" if due_date else ""

    return (
        f"📋 <b>Ваша активная задача ({current_idx + 1} из {total_count})</b>\n\n"
        f"📌 <b>{title}</b>\n"
        f"📊 <b>Статус:</b> <code>{status}</code>"
        f"{etap_part}"
        f"{due_part}\n"
        f"👤 <b>Исполнитель:</b> {assignee}"
    )

@router.message(CommandStart())
async def cmd_start(message: Message):
    user = message.from_user
    if not user:
        return

    await register_user(
        telegram_id=user.id,
        username=user.username,
        full_name=user.full_name
    )

    db_user = await get_user_by_telegram_id(user.id)
    linked_notion = db_user.get("notion_name") if db_user else None

    text = format_main_menu_text(user.full_name, user.username, linked_notion)
    await message.answer(text, parse_mode="HTML", reply_markup=get_main_menu_keyboard())

@router.callback_query(F.data == "btn_menu")
async def cb_menu(callback: CallbackQuery):
    await callback.answer()
    user = callback.from_user
    db_user = await get_user_by_telegram_id(user.id)
    linked_notion = db_user.get("notion_name") if db_user else None

    text = format_main_menu_text(user.full_name, user.username, linked_notion)
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=get_main_menu_keyboard())

@router.message(Command("my_tasks"))
async def cmd_my_tasks(message: Message, bot: Bot):
    user_id = message.from_user.id
    tasks = await get_active_tasks_for_user(user_id)

    if not tasks:
        # Try quick sync in case user just registered or got new tasks
        from scheduler import sync_tasks_now
        await sync_tasks_now(bot)
        tasks = await get_active_tasks_for_user(user_id)

    if not tasks:
        await message.answer(
            "🎉 У вас нет активных незавершенных задач в Notion.\n"
            "Новые задачи появятся здесь автоматически.",
            reply_markup=get_main_menu_keyboard()
        )
        return

    task = tasks[0]
    task_id = task["task_id"]
    url = task.get("url") or f"https://www.notion.so/{task_id.replace('-', '')}"
    text = format_task_card_text(task, 0, len(tasks))
    kb = get_paginated_task_keyboard(task_id, url, 0, len(tasks))
    await message.answer(text, parse_mode="HTML", reply_markup=kb)

@router.callback_query(F.data == "btn_my_tasks")
async def cb_my_tasks(callback: CallbackQuery, bot: Bot):
    await callback.answer()
    user_id = callback.from_user.id
    tasks = await get_active_tasks_for_user(user_id)

    if not tasks:
        # Run sync once if empty
        from scheduler import sync_tasks_now
        await sync_tasks_now(bot)
        tasks = await get_active_tasks_for_user(user_id)

    if not tasks:
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        empty_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Проверить снова", callback_data="btn_my_tasks")],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="btn_menu")]
        ])
        await callback.message.edit_text(
            "🎉 <b>У вас нет активных незавершенных задач в Notion!</b>\n\n"
            "Все назначенные на вас задачи выполнены или еще не созданы.\n"
            "Как только в Notion появится новая задача, бот сразу пришлет уведомление.",
            parse_mode="HTML",
            reply_markup=empty_kb
        )
        return

    task = tasks[0]
    task_id = task["task_id"]
    url = task.get("url") or f"https://www.notion.so/{task_id.replace('-', '')}"
    text = format_task_card_text(task, 0, len(tasks))
    kb = get_paginated_task_keyboard(task_id, url, 0, len(tasks))
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)

@router.callback_query(F.data.startswith("task_page:"))
async def cb_task_page(callback: CallbackQuery):
    await callback.answer()
    try:
        page_idx = int(callback.data.split(":")[1])
    except Exception:
        page_idx = 0

    tasks = await get_active_tasks_for_user(callback.from_user.id)
    if not tasks:
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        empty_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="btn_menu")]
        ])
        await callback.message.edit_text(
            "🎉 Все активные задачи выполнены!",
            reply_markup=empty_kb
        )
        return

    idx = max(0, min(page_idx, len(tasks) - 1))
    task = tasks[idx]
    task_id = task["task_id"]
    url = task.get("url") or f"https://www.notion.so/{task_id.replace('-', '')}"
    text = format_task_card_text(task, idx, len(tasks))
    kb = get_paginated_task_keyboard(task_id, url, idx, len(tasks))
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)

@router.callback_query(F.data.startswith("status:"))
async def cb_status_change(callback: CallbackQuery):
    # format: status:<action>:<task_id>:<idx_or_single>
    parts = callback.data.split(":")
    if len(parts) < 3:
        await callback.answer()
        return

    action = parts[1] # "in_progress" or "done"
    task_id = parts[2]
    pos = parts[3] if len(parts) > 3 else "0"

    await callback.answer("⏳ Сохраняю в Notion...")

    success = await notion_service.update_task_status(task_id, action)
    if not success:
        await callback.answer("⚠️ Не удалось обновить статус в Notion", show_alert=True)
        return

    # Update local cache status
    new_cached_status = "In progress" if action == "in_progress" else "Done"
    await update_task_status_cache(task_id, new_cached_status)

    if pos == "single":
        # Notification card update
        display = "🚀 В процессе" if action == "in_progress" else "✅ Выполнено"
        await callback.answer(f"✅ Статус обновлен на «{display}»!", show_alert=False)
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
            await callback.message.edit_text(
                callback.message.html_text + f"\n\n<b>[Статус обновлен в Notion: {display}]</b>",
                parse_mode="HTML"
            )
        except Exception:
            pass
        return

    # Carousel card update
    try:
        current_idx = int(pos)
    except Exception:
        current_idx = 0

    tasks = await get_active_tasks_for_user(callback.from_user.id)

    if action == "done":
        await callback.answer("🎉 Задача отмечена выполненной в Notion!", show_alert=False)
        if not tasks:
            from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
            empty_kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Проверить новые", callback_data="btn_my_tasks")],
                [InlineKeyboardButton(text="🏠 Главное меню", callback_data="btn_menu")]
            ])
            await callback.message.edit_text(
                "🎉 <b>Все задачи выполнены! Отличная работа.</b>\n\n"
                "Новые задачи появятся здесь, как только будут созданы в Notion.",
                parse_mode="HTML",
                reply_markup=empty_kb
            )
            return

        next_idx = max(0, min(current_idx, len(tasks) - 1))
        task = tasks[next_idx]
        task_id = task["task_id"]
        url = task.get("url") or f"https://www.notion.so/{task_id.replace('-', '')}"
        text = format_task_card_text(task, next_idx, len(tasks))
        kb = get_paginated_task_keyboard(task_id, url, next_idx, len(tasks))
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)

    else:
        # in_progress
        await callback.answer("🚀 Задача переведена 'В процесс' в Notion!", show_alert=False)
        task = next((t for t in tasks if t["task_id"] == task_id), None)
        if not task and tasks:
            task = tasks[max(0, min(current_idx, len(tasks) - 1))]

        if task:
            idx = tasks.index(task) if task in tasks else current_idx
            task_id = task["task_id"]
            url = task.get("url") or f"https://www.notion.so/{task_id.replace('-', '')}"
            text = format_task_card_text(task, idx, len(tasks))
            kb = get_paginated_task_keyboard(task_id, url, idx, len(tasks))
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)

@router.callback_query(F.data == "btn_sync_now")
async def cb_sync_now(callback: CallbackQuery, bot: Bot):
    await callback.answer("⏳ Синхронизация с Notion...", show_alert=False)
    try:
        from scheduler import sync_tasks_now
        total, notified = await sync_tasks_now(bot)
        # Show alert toast without spamming chat with new messages
        await callback.answer(
            f"✅ Синхронизировано!\n• Всего задач в Notion: {total}\n• Новых уведомлений: {notified}",
            show_alert=True
        )
    except Exception as e:
        await callback.answer(f"❌ Ошибка синхронизации: {e}", show_alert=True)

@router.callback_query(F.data == "btn_link_account")
async def cb_link_account(callback: CallbackQuery):
    await callback.answer()
    users = await notion_service.get_workspace_users()
    if not users:
        await callback.answer(
            "⚠️ Не удалось получить список пользователей Notion. Проверьте права интеграции.",
            show_alert=True
        )
        return

    await callback.message.edit_text(
        "Выберите ваш профиль в Notion из списка ниже:",
        reply_markup=get_link_users_keyboard(users)
    )

@router.callback_query(F.data.startswith("link_me:"))
async def cb_link_selected(callback: CallbackQuery, bot: Bot):
    uid = callback.data.split(":", 1)[1]
    users = await notion_service.get_workspace_users()
    matched = next((u for u in users if u["id"] == uid), None)

    if matched:
        await link_notion_to_telegram(
            telegram_id=callback.from_user.id,
            notion_user_id=uid,
            notion_name=matched.get("name"),
            notion_email=matched.get("email"),
            username=callback.from_user.username,
            full_name=callback.from_user.full_name
        )
        # Immediately run sync to associate any existing tasks in Notion to this user!
        from scheduler import sync_tasks_now
        await sync_tasks_now(bot)

        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        done_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📋 Мои активные задачи", callback_data="btn_my_tasks")],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="btn_menu")]
        ])

        await callback.answer("✅ Профиль успешно привязан!", show_alert=False)
        await callback.message.edit_text(
            f"✅ <b>Профиль успешно привязан!</b>\n\n"
            f"👤 Имя в Notion: <b>{matched.get('name')}</b>\n"
            f"📧 Email: <code>{matched.get('email') or 'не указан'}</code>\n\n"
            f"Теперь задачи, назначенные на этот профиль в Notion, доступны в боте!",
            parse_mode="HTML",
            reply_markup=done_kb
        )
    else:
        await callback.answer("Пользователь не найден в списке Notion", show_alert=True)

@router.callback_query(F.data == "noop")
async def cb_noop(callback: CallbackQuery):
    await callback.answer()
