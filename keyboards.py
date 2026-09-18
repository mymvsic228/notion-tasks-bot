from typing import List, Dict, Any
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

def get_task_keyboard(task_id: str, url: str) -> InlineKeyboardMarkup:
    """Keyboard for notification messages (standalone task card)."""
    buttons = [
        [
            InlineKeyboardButton(text="🚀 В работу", callback_data=f"status:in_progress:{task_id}:single"),
            InlineKeyboardButton(text="✅ Выполнено", callback_data=f"status:done:{task_id}:single")
        ],
        [
            InlineKeyboardButton(text="🔗 Открыть в Notion", url=url)
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_paginated_task_keyboard(task_id: str, url: str, current_idx: int, total_count: int) -> InlineKeyboardMarkup:
    """Carousel keyboard for browsing active tasks without chat spam."""
    buttons = [
        [
            InlineKeyboardButton(text="🚀 В работу", callback_data=f"status:in_progress:{task_id}:{current_idx}"),
            InlineKeyboardButton(text="✅ Выполнено", callback_data=f"status:done:{task_id}:{current_idx}")
        ]
    ]

    # Navigation row (if more than 1 task)
    nav_row = []
    if current_idx > 0:
        nav_row.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"task_page:{current_idx - 1}"))
    else:
        nav_row.append(InlineKeyboardButton(text="▪️", callback_data="noop"))

    nav_row.append(InlineKeyboardButton(text=f"{current_idx + 1} / {total_count}", callback_data="noop"))

    if current_idx < total_count - 1:
        nav_row.append(InlineKeyboardButton(text="Вперед ➡️", callback_data=f"task_page:{current_idx + 1}"))
    else:
        nav_row.append(InlineKeyboardButton(text="▪️", callback_data="noop"))

    buttons.append(nav_row)

    # Action row
    buttons.append([
        InlineKeyboardButton(text="🔗 Открыть в Notion", url=url),
        InlineKeyboardButton(text="🔄 Обновить", callback_data=f"task_page:{current_idx}")
    ])
    
    # Back to menu
    buttons.append([
        InlineKeyboardButton(text="🏠 Главное меню", callback_data="btn_menu")
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_link_users_keyboard(notion_users: List[Dict[str, Any]]) -> InlineKeyboardMarkup:
    buttons = []
    for u in notion_users[:12]:
        name = (u.get("name") or "Без имени").strip()
        uid = u.get("id")
        buttons.append([InlineKeyboardButton(text=f"👤 {name}", callback_data=f"link_me:{uid}")])
    buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data="btn_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_main_menu_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(text="📋 Мои активные задачи", callback_data="btn_my_tasks"),
        ],
        [
            InlineKeyboardButton(text="🔄 Синхронизировать", callback_data="btn_sync_now"),
            InlineKeyboardButton(text="🔗 Привязать Notion", callback_data="btn_link_account")
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)
