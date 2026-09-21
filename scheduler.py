import asyncio
import logging
from datetime import datetime
from typing import Tuple, List, Optional
from aiogram import Bot
from config import settings
from database import (
    get_task_cache,
    save_task_cache,
    find_telegram_id_for_assignee,
    mark_deadline_reminded,
    delete_task_from_cache,
    get_all_cached_task_ids
)
from notion_service import notion_service
from keyboards import get_task_keyboard

logger = logging.getLogger(__name__)

async def sync_tasks_now(bot: Bot) -> Tuple[int, int]:
    """
    Polls Notion database, identifies new or updated tasks,
    and sends Telegram notifications to assigned users.
    Returns (total_tasks_found, notifications_sent_count)
    """
    if not settings.NOTION_TOKEN:
        logger.debug("Notion token not configured, skipping sync.")
        return 0, 0

    tasks = await notion_service.query_tasks()
    if not tasks:
        return 0, 0

    notified_count = 0

    for task in tasks:
        task_id = task["id"]
        title = task["title"]
        status = task["status"]
        etap = task.get("etap")
        last_edited = task["last_edited_time"]
        url = task.get("url") or f"https://www.notion.so/{task_id.replace('-', '')}"
        due_date = task.get("due_date")
        assignees = task.get("assignees", [])
        telegram_prop = task.get("telegram_prop")

        # Find target Telegram users for this task
        target_tg_ids = set()

        # Check people assignees
        for a in assignees:
            tg_id = await find_telegram_id_for_assignee(
                notion_user_id=a.get("id"),
                notion_name=a.get("name"),
                notion_email=a.get("email"),
                telegram_prop=telegram_prop
            )
            if tg_id:
                target_tg_ids.add(tg_id)

        # Also check explicit telegram property if no people assignee found
        if not target_tg_ids and telegram_prop:
            tg_id = await find_telegram_id_for_assignee(telegram_prop=telegram_prop)
            if tg_id:
                target_tg_ids.add(tg_id)

        assignee_names_str = ", ".join([a.get("name") or "Не указано" for a in assignees]) or "Не назначен"
        first_tg_id = list(target_tg_ids)[0] if target_tg_ids else None

        done_statuses = ['done', 'выполнено', 'closed', 'завершено', 'готово', 'опубликован', 'завершен']
        if status.lower() in done_statuses:
            await delete_task_from_cache(task_id)
            continue

        cached = await get_task_cache(task_id)

        if not cached:
            # BRAND NEW TASK
            for tg_id in target_tg_ids:
                etap_str = f"\n🎬 <b>Этап:</b> <i>{etap}</i>" if etap else ""
                due_str = f"\n📅 <b>Дедлайн:</b> <i>{due_date}</i>" if due_date else ""
                msg = (
                    f"📋 <b>Новая задача в Notion!</b>\n\n"
                    f"📌 <b>{title}</b>\n"
                    f"📊 <b>Статус:</b> <code>{status}</code>"
                    f"{etap_str}"
                    f"\n👤 <b>Исполнитель:</b> {assignee_names_str}"
                    f"{due_str}"
                )

                try:
                    await bot.send_message(
                        chat_id=tg_id,
                        text=msg,
                        parse_mode="HTML",
                        reply_markup=get_task_keyboard(task_id, url)
                    )
                    notified_count += 1
                except Exception as ex:
                    logger.error(f"Failed to send new task notification to {tg_id}: {ex}")

            await save_task_cache(
                task_id=task_id,
                last_edited_time=last_edited,
                title=title,
                status=status,
                assignee_name=assignee_names_str,
                assignee_telegram_id=first_tg_id,
                due_date=due_date,
                etap=etap,
                url=url
            )

        else:
            # TASK WAS EDITED OR USER LINKED
            is_edited = cached.get("last_edited_time") != last_edited
            is_assignee_updated = cached.get("assignee_telegram_id") != first_tg_id

            if is_edited or is_assignee_updated:
                if is_edited:
                    old_status = cached.get("status")
                    old_assignee = cached.get("assignee_name")

                    # If status changed
                    if old_status != status:
                        for tg_id in target_tg_ids:
                            msg = (
                                f"🔄 <b>Обновлен статус задачи!</b>\n\n"
                                f"📌 <b>{title}</b>\n"
                                f"📊 Статус: <s>{old_status}</s> ➔ <b>{status}</b>\n"
                            )
                            if due_date:
                                msg += f"📅 <b>Дедлайн:</b> <i>{due_date}</i>\n"

                            try:
                                await bot.send_message(
                                    chat_id=tg_id,
                                    text=msg,
                                    parse_mode="HTML",
                                    reply_markup=get_task_keyboard(task_id, url)
                                )
                                notified_count += 1
                            except Exception as ex:
                                logger.error(f"Failed to send status update to {tg_id}: {ex}")

                    # If assignee changed to someone new
                    if old_assignee != assignee_names_str:
                        for tg_id in target_tg_ids:
                            msg = (
                                f"👤 <b>Вам переназначена задача в Notion!</b>\n\n"
                                f"📌 <b>{title}</b>\n"
                                f"📊 <b>Статус:</b> {status}\n"
                            )
                            if due_date:
                                msg += f"📅 <b>Дедлайн:</b> <i>{due_date}</i>\n"

                            try:
                                await bot.send_message(
                                    chat_id=tg_id,
                                    text=msg,
                                    parse_mode="HTML",
                                    reply_markup=get_task_keyboard(task_id, url)
                                )
                                notified_count += 1
                            except Exception as ex:
                                logger.error(f"Failed to send reassignment to {tg_id}: {ex}")

                # Save updated cache
                await save_task_cache(
                    task_id=task_id,
                    last_edited_time=last_edited,
                    title=title,
                    status=status,
                    assignee_name=assignee_names_str,
                    assignee_telegram_id=first_tg_id,
                    due_date=due_date,
                    etap=etap,
                    url=url
                )

        # Check deadline reminder
        if due_date and not (cached and cached.get("deadline_reminded")):
            try:
                clean_due = due_date.split("T")[0]
                due_dt = datetime.strptime(clean_due, "%Y-%m-%d").date()
                today = datetime.now().date()
                delta_days = (due_dt - today).days

                closed_statuses = ["done", "выполнено", "closed", "завершено", "готово", "опубликован", "завершен"]
                if 0 <= delta_days <= 1 and status.lower() not in closed_statuses:
                    for tg_id in target_tg_ids:
                        days_text = "СЕГОДНЯ" if delta_days == 0 else "ЗАВТРА"
                        msg = (
                            f"⏰ <b>Внимание: дедлайн {days_text}!</b>\n\n"
                            f"📌 <b>{title}</b>\n"
                            f"📅 Срок сдачи: <b>{due_date}</b>\n"
                            f"📊 Текущий статус: <i>{status}</i>\n"
                        )
                        try:
                            await bot.send_message(
                                chat_id=tg_id,
                                text=msg,
                                parse_mode="HTML",
                                reply_markup=get_task_keyboard(task_id, url)
                            )
                            notified_count += 1
                        except Exception as ex:
                            logger.error(f"Failed to send deadline reminder to {tg_id}: {ex}")

                    await mark_deadline_reminded(task_id)
            except Exception as e:
                logger.debug(f"Could not parse due_date '{due_date}': {e}")

    # Remove stale tasks that no longer exist in Notion or are completed
    fetched_ids = {t['id'] for t in tasks}
    cached_ids = await get_all_cached_task_ids()
    for old_id in cached_ids:
        if old_id not in fetched_ids:
            await delete_task_from_cache(old_id)

    return len(tasks), notified_count

async def scheduler_loop(bot: Bot):
    """Background loop that executes sync_tasks_now every POLL_INTERVAL_SECONDS."""
    logger.info(f"Started Notion background sync scheduler (interval: {settings.POLL_INTERVAL_SECONDS}s).")
    while True:
        try:
            await sync_tasks_now(bot)
        except Exception as e:
            logger.error(f"Error in scheduler loop: {e}", exc_info=True)
        await asyncio.sleep(max(10, settings.POLL_INTERVAL_SECONDS))
