from typing import List, Dict, Any
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

def get_task_keyboard(task_id: str, url: str) -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(text="🚀 В работу", callback_data=f"status:in_progress:{task_id}"),
            InlineKeyboardButton(text="✅ Выполнено", callback_data=f"status:done:{task_id}")
        ],
        [
            InlineKeyboardButton(text="🔗 Открыть в Notion", url=url)
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_link_users_keyboard(notion_users: List[Dict[str, Any]]) -> InlineKeyboardMarkup:
    buttons = []
    for u in notion_users[:10]:  # Limit to 10 for neat inline keyboard
        name = u.get("name") or "Без имени"
        uid = u.get("id")
        buttons.append([InlineKeyboardButton(text=f"👤 {name}", callback_data=f"link_me:{uid}")])
    buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_link")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_main_menu_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(text="📋 Мои активные задачи", callback_data="btn_my_tasks"),
        ],
        [
            InlineKeyboardButton(text="🔄 Синхронизировать сейчас", callback_data="btn_sync_now"),
            InlineKeyboardButton(text="🔗 Привязать Notion", callback_data="btn_link_account")
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)
