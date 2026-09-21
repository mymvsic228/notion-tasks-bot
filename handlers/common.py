import logging
from typing import List, Dict, Any, Optional
from aiogram import Router, F, Bot
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from database import (
    register_user,
    get_user_by_telegram_id,
    link_notion_to_telegram,
    get_active_tasks_for_user,
    update_task_status_cache,
    delete_task_from_cache,
    mark_task_pending_approval,
    clear_task_pending_approval,
    get_task_cache
)
from notion_service import notion_service
from keyboards import (
    get_main_menu_keyboard,
    get_link_users_keyboard,
    get_paginated_task_keyboard,
    get_task_keyboard,
    get_approval_keyboard
)

logger = logging.getLogger(__name__)
router = Router()

# Tracks last menu/card message per user to keep chat 100% clean without message spam
user_last_message: Dict[int, int] = {}

async def ensure_user_linked_smartly(user_id: int, username: Optional[str], full_name: Optional[str], bot: Bot) -> Optional[Dict[str, Any]]:
    """Automatically links user by Telegram username matching Notion name if not linked yet."""
    db_user = await get_user_by_telegram_id(user_id)
    if db_user and db_user.get("notion_user_id"):
        return db_user

    # Try auto-linking by username match
    clean_username = (username or "").lower().strip().lstrip("@")
    clean_fullname = (full_name or "").lower().strip()

    try:
        users = await notion_service.get_workspace_users()
        matched = None
        for u in users:
            n_name = (u.get("name") or "").lower().strip()
            # If username or fullname contains Notion name (e.g. 'hextechk' contains 'hextech')
            if n_name and (n_name in clean_username or clean_username in n_name or n_name in clean_fullname):
                matched = u
                break

        if matched:
            logger.info(f"Auto-linking Telegram user {user_id} (@{username}) to Notion user {matched.get('name')}")
            await link_notion_to_telegram(
                telegram_id=user_id,
                notion_name=matched.get("name"),
                notion_email=matched.get("email"),
                notion_user_id=matched.get("id"),
                username=username,
                full_name=full_name
            )
            from scheduler import sync_tasks_now
            await sync_tasks_now(bot)
            return await get_user_by_telegram_id(user_id)
    except Exception as e:
        logger.error(f"Error in smart auto-link: {e}")

    return db_user

async def clean_send_or_edit(bot: Bot, chat_id: int, text: str, reply_markup=None, callback: Optional[CallbackQuery] = None):
    """Edits current message if possible, or deletes previous message to avoid spam in chat."""
    if callback and callback.message:
        try:
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=reply_markup)
            user_last_message[chat_id] = callback.message.message_id
            return
        except Exception:
            pass

    # Delete previous bot message if present
    prev_id = user_last_message.get(chat_id)
    if prev_id:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=prev_id)
        except Exception:
            pass

    new_msg = await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML", reply_markup=reply_markup)
    user_last_message[chat_id] = new_msg.message_id

def format_main_menu_text(full_name: str, username: Optional[str], linked_notion: Optional[str]) -> str:
    text = (
        f"👋 Здравствуйте, <b>{full_name}</b>!\n\n"
        f"Я бот для получения уведомлений и управления задачами из <b>Notion</b>.\n\n"
    )
    if linked_notion:
        text += (
            f"✅ Профиль Notion: <b>{linked_notion}</b>\n\n"
            f"Все ваши задачи синхронизированы. Используйте кнопку ниже для просмотра."
        )
    else:
        text += (
            f"ℹ️ Ваш аккаунт пока не привязан к Notion.\n"
            f"Нажмите кнопку <b>«🔗 Привязать Notion»</b> ниже и выберите свое имя."
        )
    return text

def format_task_card_text(task: Dict[str, Any], current_idx: int, total_count: int) -> str:
    title = task.get("title") or "Без названия"
    status = task.get("status") or "Not started"
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
async def cmd_start(message: Message, bot: Bot):
    user = message.from_user
    if not user:
        return

    # Delete incoming user /start command to keep chat pristine
    try:
        await message.delete()
    except Exception:
        pass

    await register_user(
        telegram_id=user.id,
        username=user.username,
        full_name=user.full_name
    )

    db_user = await ensure_user_linked_smartly(user.id, user.username, user.full_name, bot)
    linked_notion = db_user.get("notion_name") if db_user else None

    text = format_main_menu_text(user.full_name, user.username, linked_notion)
    await clean_send_or_edit(bot, user.id, text, reply_markup=get_main_menu_keyboard())

@router.callback_query(F.data == "btn_menu")
async def cb_menu(callback: CallbackQuery, bot: Bot):
    await callback.answer()
    user = callback.from_user
    db_user = await get_user_by_telegram_id(user.id)
    linked_notion = db_user.get("notion_name") if db_user else None

    text = format_main_menu_text(user.full_name, user.username, linked_notion)
    await clean_send_or_edit(bot, user.id, text, reply_markup=get_main_menu_keyboard(), callback=callback)

@router.message(Command("my_tasks"))
async def cmd_my_tasks(message: Message, bot: Bot):
    user = message.from_user
    user_id = user.id

    # Delete command text to keep chat clean
    try:
        await message.delete()
    except Exception:
        pass

    await ensure_user_linked_smartly(user_id, user.username, user.full_name, bot)

    tasks = await get_active_tasks_for_user(user_id)
    if not tasks:
        from scheduler import sync_tasks_now
        await sync_tasks_now(bot)
        tasks = await get_active_tasks_for_user(user_id)

    if not tasks:
        empty_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Проверить снова", callback_data="btn_my_tasks")],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="btn_menu")]
        ])
        text = (
            "🎉 <b>У вас нет активных незавершенных задач в Notion!</b>\n\n"
            "Все назначенные на вас задачи выполнены или еще не созданы.\n"
            "Как только в Notion появится новая задача, бот сразу пришлет уведомление."
        )
        await clean_send_or_edit(bot, user_id, text, reply_markup=empty_kb)
        return

    task = tasks[0]
    task_id = task["task_id"]
    url = task.get("url") or f"https://www.notion.so/{task_id.replace('-', '')}"
    text = format_task_card_text(task, 0, len(tasks))
    kb = get_paginated_task_keyboard(task_id, url, 0, len(tasks))
    await clean_send_or_edit(bot, user_id, text, reply_markup=kb)

@router.callback_query(F.data == "btn_my_tasks")
async def cb_my_tasks(callback: CallbackQuery, bot: Bot):
    await callback.answer()
    user = callback.from_user
    user_id = user.id

    await ensure_user_linked_smartly(user_id, user.username, user.full_name, bot)

    tasks = await get_active_tasks_for_user(user_id)
    if not tasks:
        from scheduler import sync_tasks_now
        await sync_tasks_now(bot)
        tasks = await get_active_tasks_for_user(user_id)

    if not tasks:
        empty_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Проверить снова", callback_data="btn_my_tasks")],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="btn_menu")]
        ])
        text = (
            "🎉 <b>У вас нет активных незавершенных задач в Notion!</b>\n\n"
            "Все назначенные на вас задачи выполнены или еще не созданы.\n"
            "Как только в Notion появится новая задача, бот сразу пришлет уведомление."
        )
        await clean_send_or_edit(bot, user_id, text, reply_markup=empty_kb, callback=callback)
        return

    task = tasks[0]
    task_id = task["task_id"]
    url = task.get("url") or f"https://www.notion.so/{task_id.replace('-', '')}"
    text = format_task_card_text(task, 0, len(tasks))
    kb = get_paginated_task_keyboard(task_id, url, 0, len(tasks))
    await clean_send_or_edit(bot, user_id, text, reply_markup=kb, callback=callback)

@router.callback_query(F.data.startswith("task_page:"))
async def cb_task_page(callback: CallbackQuery, bot: Bot):
    await callback.answer()
    try:
        page_idx = int(callback.data.split(":")[1])
    except Exception:
        page_idx = 0

    tasks = await get_active_tasks_for_user(callback.from_user.id)
    if not tasks:
        empty_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="btn_menu")]
        ])
        await clean_send_or_edit(bot, callback.from_user.id, "🎉 Все активные задачи выполнены!", reply_markup=empty_kb, callback=callback)
        return

    idx = max(0, min(page_idx, len(tasks) - 1))
    task = tasks[idx]
    task_id = task["task_id"]
    url = task.get("url") or f"https://www.notion.so/{task_id.replace('-', '')}"
    text = format_task_card_text(task, idx, len(tasks))
    kb = get_paginated_task_keyboard(task_id, url, idx, len(tasks))
    await clean_send_or_edit(bot, callback.from_user.id, text, reply_markup=kb, callback=callback)

@router.callback_query(F.data.startswith("status:"))
async def cb_status_change(callback: CallbackQuery, bot: Bot):
    # format: status:<action>:<task_id>:<idx_or_single>
    parts = callback.data.split(":")
    if len(parts) < 3:
        await callback.answer()
        return

    action = parts[1] # "in_progress", "done", "submit_done"
    task_id = parts[2]
    pos = parts[3] if len(parts) > 3 else "0"

    if action == "submit_done":
        cached = await get_task_cache(task_id)
        title = cached.get('title', 'Без названия') if cached else 'Без названия'
        etap = cached.get('etap', 'Не указан') if cached else 'Не указан'
        assignee_name = cached.get('assignee_name', callback.from_user.full_name) if cached else callback.from_user.full_name
        
        await mark_task_pending_approval(task_id, callback.from_user.id, callback.from_user.full_name)
        
        admin_text = (
            f"📬 Задача ожидает проверки!\n\n"
            f"📌 <b>{title}</b>\n"
            f"👤 Исполнитель: {assignee_name}\n"
            f"📊 Статус: In progress → ожидает апрува\n"
            f"🎬 Этап: {etap}\n\n"
            f"Нажмите «Принять» чтобы закрыть задачу в Notion, или «Отклонить» чтобы вернуть исполнителю."
        )
        
        for admin_id in [486058343, 1530089636]:
            try:
                await bot.send_message(
                    chat_id=admin_id,
                    text=admin_text,
                    parse_mode="HTML",
                    reply_markup=get_approval_keyboard(task_id, callback.from_user.id)
                )
            except Exception as e:
                logger.error(f"Failed to notify admin {admin_id}: {e}")
                
        await callback.answer("⏳ Задача отправлена на проверку администратору!", show_alert=False)
        
        if pos == "single":
            try:
                await callback.message.edit_reply_markup(reply_markup=None)
                await callback.message.edit_text(
                    callback.message.html_text + f"\n\n<b>[⏳ На проверке у администратора]</b>",
                    parse_mode="HTML"
                )
            except Exception:
                pass
            return
            
        try:
            current_idx = int(pos)
        except Exception:
            current_idx = 0
            
        tasks = await get_active_tasks_for_user(callback.from_user.id)
        task = next((t for t in tasks if t["task_id"] == task_id), None)
        if not task and tasks:
            task = tasks[max(0, min(current_idx, len(tasks) - 1))]
            
        if task:
            idx = tasks.index(task) if task in tasks else current_idx
            url = task.get("url") or f"https://www.notion.so/{task_id.replace('-', '')}"
            text = format_task_card_text(task, idx, len(tasks)) + "\n\n<b>[⏳ На проверке у администратора]</b>"
            kb = get_paginated_task_keyboard(task_id, url, idx, len(tasks))
            await clean_send_or_edit(bot, callback.from_user.id, text, reply_markup=kb, callback=callback)
            
        return

    await callback.answer("⏳ Обновляю в Notion...")

    success = await notion_service.update_task_status(task_id, action)
    if not success:
        await callback.answer("⚠️ Не удалось обновить статус в Notion", show_alert=True)
        return

    # Update local cache status
    new_cached_status = "In progress" if action == "in_progress" else "Done"
    await update_task_status_cache(task_id, new_cached_status)

    if pos == "single":
        # Notification message in-place update
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
            empty_kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Проверить новые", callback_data="btn_my_tasks")],
                [InlineKeyboardButton(text="🏠 Главное меню", callback_data="btn_menu")]
            ])
            text = (
                "🎉 <b>Все задачи выполнены! Отличная работа.</b>\n\n"
                "Новые задачи появятся здесь, как только будут созданы в Notion."
            )
            await clean_send_or_edit(bot, callback.from_user.id, text, reply_markup=empty_kb, callback=callback)
            return

        next_idx = max(0, min(current_idx, len(tasks) - 1))
        task = tasks[next_idx]
        task_id = task["task_id"]
        url = task.get("url") or f"https://www.notion.so/{task_id.replace('-', '')}"
        text = format_task_card_text(task, next_idx, len(tasks))
        kb = get_paginated_task_keyboard(task_id, url, next_idx, len(tasks))
        await clean_send_or_edit(bot, callback.from_user.id, text, reply_markup=kb, callback=callback)

    else:
        # in_progress
        await callback.answer("🚀 Статус переведен 'In progress' в Notion!", show_alert=False)
        task = next((t for t in tasks if t["task_id"] == task_id), None)
        if not task and tasks:
            task = tasks[max(0, min(current_idx, len(tasks) - 1))]

        if task:
            idx = tasks.index(task) if task in tasks else current_idx
            task_id = task["task_id"]
            url = task.get("url") or f"https://www.notion.so/{task_id.replace('-', '')}"
            text = format_task_card_text(task, idx, len(tasks))
            kb = get_paginated_task_keyboard(task_id, url, idx, len(tasks))
            await clean_send_or_edit(bot, callback.from_user.id, text, reply_markup=kb, callback=callback)

@router.callback_query(F.data.startswith("approve_task:"))
async def cb_approve_task(callback: CallbackQuery, bot: Bot):
    # Only admins can approve
    # Parse: approve_task:<task_id>:<assignee_tg_id>
    parts = callback.data.split(":")
    task_id = parts[1]
    assignee_tg_id = int(parts[2])
    
    # Mark done in Notion
    success = await notion_service.update_task_status(task_id, "done")
    if not success:
        await callback.answer("⚠️ Ошибка обновления в Notion", show_alert=True)
        return
    
    # Remove from cache
    await delete_task_from_cache(task_id)
    
    # Edit admin message
    await callback.message.edit_text(
        callback.message.html_text + "\n\n✅ <b>Принято!</b> Задача закрыта в Notion.",
        parse_mode="HTML"
    )
    await callback.answer("✅ Задача принята!")
    
    # Notify executor
    try:
        cached = await get_task_cache(task_id)
        title = cached.get('title', 'Задача') if cached else 'Задача'
        await bot.send_message(
            chat_id=assignee_tg_id,
            text=f"✅ <b>Ваша задача принята!</b>\n\n📌 <b>{title}</b>\n\nОтличная работа! Задача закрыта в Notion.",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Failed to notify executor {assignee_tg_id}: {e}")

@router.callback_query(F.data.startswith("reject_task:"))
async def cb_reject_task(callback: CallbackQuery, bot: Bot):
    parts = callback.data.split(":")
    task_id = parts[1]
    assignee_tg_id = int(parts[2])
    
    # Clear pending approval
    await clear_task_pending_approval(task_id)
    
    # Edit admin message  
    await callback.message.edit_text(
        callback.message.html_text + "\n\n❌ <b>Отклонено.</b> Задача возвращена исполнителю.",
        parse_mode="HTML"
    )
    await callback.answer("❌ Задача отклонена")
    
    # Notify executor
    try:
        cached = await get_task_cache(task_id)
        title = cached.get('title', 'Задача') if cached else 'Задача'
        await bot.send_message(
            chat_id=assignee_tg_id,
            text=f"❌ <b>Задача возвращена на доработку.</b>\n\n📌 <b>{title}</b>\n\nАдминистратор отклонил выполнение. Задача остается в работе.",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Failed to notify executor {assignee_tg_id}: {e}")

@router.callback_query(F.data == "btn_sync_now")
async def cb_sync_now(callback: CallbackQuery, bot: Bot):
    await callback.answer("⏳ Синхронизация с Notion...", show_alert=False)
    try:
        from scheduler import sync_tasks_now
        total, notified = await sync_tasks_now(bot)
        await callback.answer(
            f"✅ Синхронизировано!\n• Всего задач в Notion: {total}\n• Новых уведомлений: {notified}",
            show_alert=True
        )
    except Exception as e:
        await callback.answer(f"❌ Ошибка синхронизации: {e}", show_alert=True)

@router.callback_query(F.data == "btn_link_account")
async def cb_link_account(callback: CallbackQuery, bot: Bot):
    await callback.answer()
    users = await notion_service.get_workspace_users()
    if not users:
        await callback.answer(
            "⚠️ Не удалось получить список пользователей Notion. Проверьте права интеграции.",
            show_alert=True
        )
        return

    text = "Выберите ваш профиль в Notion из списка ниже:"
    await clean_send_or_edit(bot, callback.from_user.id, text, reply_markup=get_link_users_keyboard(users), callback=callback)

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
        from scheduler import sync_tasks_now
        await sync_tasks_now(bot)

        done_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📋 Мои активные задачи", callback_data="btn_my_tasks")],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="btn_menu")]
        ])

        await callback.answer("✅ Профиль успешно привязан!", show_alert=False)
        text = (
            f"✅ <b>Профиль успешно привязан!</b>\n\n"
            f"👤 Имя в Notion: <b>{matched.get('name')}</b>\n"
            f"📧 Email: <code>{matched.get('email') or 'не указан'}</code>\n\n"
            f"Теперь задачи, назначенные на этот профиль в Notion, доступны в боте!"
        )
        await clean_send_or_edit(bot, callback.from_user.id, text, reply_markup=done_kb, callback=callback)
    else:
        await callback.answer("Пользователь не найден в списке Notion", show_alert=True)

@router.callback_query(F.data == "noop")
async def cb_noop(callback: CallbackQuery):
    await callback.answer()
