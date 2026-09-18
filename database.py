import logging
from typing import Optional, List, Dict, Any
import aiosqlite

logger = logging.getLogger(__name__)
DB_PATH = "notion_bot.db"

async def init_db(db_path: str = DB_PATH):
    async with aiosqlite.connect(db_path) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                telegram_id INTEGER PRIMARY KEY,
                username TEXT,
                full_name TEXT,
                notion_user_id TEXT,
                notion_name TEXT,
                notion_email TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS tasks_cache (
                task_id TEXT PRIMARY KEY,
                last_edited_time TEXT,
                title TEXT,
                status TEXT,
                etap TEXT,
                assignee_name TEXT,
                assignee_telegram_id INTEGER,
                due_date TEXT,
                url TEXT,
                deadline_reminded INTEGER DEFAULT 0,
                notified_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS system_settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        # Ensure new columns exist if table was previously created
        try:
            await db.execute("ALTER TABLE tasks_cache ADD COLUMN etap TEXT")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE tasks_cache ADD COLUMN url TEXT")
        except Exception:
            pass
        await db.commit()
    logger.info("Database initialized successfully.")

async def get_setting(key: str, default: Optional[str] = None, db_path: str = DB_PATH) -> Optional[str]:
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("SELECT value FROM system_settings WHERE key = ?", (key,)) as cur:
            row = await cur.fetchone()
            return row[0] if row else default

async def set_setting(key: str, value: str, db_path: str = DB_PATH):
    async with aiosqlite.connect(db_path) as db:
        await db.execute("""
            INSERT INTO system_settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """, (key, value))
        await db.commit()

async def register_user(
    telegram_id: int,
    username: Optional[str] = None,
    full_name: Optional[str] = None,
    notion_name: Optional[str] = None,
    notion_email: Optional[str] = None,
    notion_user_id: Optional[str] = None,
    db_path: str = DB_PATH
):
    clean_username = username.lstrip("@").lower() if username else None
    clean_name = notion_name.strip() if notion_name else None
    clean_email = notion_email.strip().lower() if notion_email else None
    clean_uid = notion_user_id.strip() if notion_user_id else None

    async with aiosqlite.connect(db_path) as db:
        await db.execute("""
            INSERT INTO users (telegram_id, username, full_name, notion_name, notion_email, notion_user_id)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET
                username = COALESCE(excluded.username, users.username),
                full_name = COALESCE(excluded.full_name, users.full_name),
                notion_name = COALESCE(excluded.notion_name, users.notion_name),
                notion_email = COALESCE(excluded.notion_email, users.notion_email),
                notion_user_id = COALESCE(excluded.notion_user_id, users.notion_user_id)
        """, (telegram_id, clean_username, full_name, clean_name, clean_email, clean_uid))
        await db.commit()

async def link_notion_to_telegram(
    telegram_id: int,
    notion_name: Optional[str] = None,
    notion_email: Optional[str] = None,
    notion_user_id: Optional[str] = None,
    username: Optional[str] = None,
    full_name: Optional[str] = None,
    db_path: str = DB_PATH
):
    clean_username = username.lstrip("@").lower() if username else None
    clean_name = notion_name.strip() if notion_name else None
    clean_email = notion_email.strip().lower() if notion_email else None
    clean_uid = notion_user_id.strip() if notion_user_id else None

    async with aiosqlite.connect(db_path) as db:
        await db.execute("""
            INSERT INTO users (telegram_id, username, full_name, notion_name, notion_email, notion_user_id)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET
                username = COALESCE(excluded.username, users.username),
                full_name = COALESCE(excluded.full_name, users.full_name),
                notion_name = COALESCE(excluded.notion_name, users.notion_name),
                notion_email = COALESCE(excluded.notion_email, users.notion_email),
                notion_user_id = COALESCE(excluded.notion_user_id, users.notion_user_id)
        """, (telegram_id, clean_username, full_name, clean_name, clean_email, clean_uid))
        await db.commit()

async def link_by_username(
    username: str,
    notion_name: str,
    db_path: str = DB_PATH
) -> bool:
    clean_username = username.lstrip("@").lower().strip()
    clean_notion = notion_name.strip()
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute("SELECT telegram_id FROM users WHERE LOWER(username) = ?", (clean_username,))
        row = await cursor.fetchone()
        if row:
            tg_id = row[0]
            await db.execute("UPDATE users SET notion_name = ? WHERE telegram_id = ?", (clean_notion, tg_id))
            await db.commit()
            return True
        return False

async def get_user_by_telegram_id(telegram_id: int, db_path: str = DB_PATH) -> Optional[Dict[str, Any]]:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

async def find_telegram_id_for_assignee(
    notion_user_id: Optional[str] = None,
    notion_name: Optional[str] = None,
    notion_email: Optional[str] = None,
    telegram_prop: Optional[str] = None,
    db_path: str = DB_PATH
) -> Optional[int]:
    """
    Finds a Telegram user matching the Notion assignee by:
    1. Explicit telegram username property in Notion (e.g. '@durov')
    2. Notion user ID (exact UUID)
    3. Notion email (case-insensitive)
    4. Notion user display name / username matching Telegram
    """
    async with aiosqlite.connect(db_path) as db:
        # 1. Check explicit telegram property from Notion
        if telegram_prop:
            clean_tg = telegram_prop.strip().lstrip("@").lower()
            if clean_tg.isdigit():
                return int(clean_tg)
            async with db.execute("SELECT telegram_id FROM users WHERE LOWER(username) = ?", (clean_tg,)) as cur:
                row = await cur.fetchone()
                if row:
                    return row[0]

        # 2. Check by Notion user ID
        if notion_user_id:
            clean_uid = notion_user_id.strip()
            async with db.execute("SELECT telegram_id FROM users WHERE notion_user_id = ?", (clean_uid,)) as cur:
                row = await cur.fetchone()
                if row:
                    return row[0]

        # 3. Check by email
        if notion_email:
            clean_email = notion_email.strip().lower()
            async with db.execute("SELECT telegram_id FROM users WHERE LOWER(TRIM(notion_email)) = ?", (clean_email,)) as cur:
                row = await cur.fetchone()
                if row:
                    return row[0]

        # 4. Check by Notion display name
        if notion_name:
            clean_name = notion_name.strip().lower()
            async with db.execute(
                "SELECT telegram_id FROM users WHERE LOWER(TRIM(notion_name)) = ? OR LOWER(username) = ? OR LOWER(full_name) = ?",
                (clean_name, clean_name.lstrip("@"), clean_name)
            ) as cur:
                row = await cur.fetchone()
                if row:
                    return row[0]

    return None

async def get_all_users(db_path: str = DB_PATH) -> List[Dict[str, Any]]:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users ORDER BY created_at DESC") as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

async def get_task_cache(task_id: str, db_path: str = DB_PATH) -> Optional[Dict[str, Any]]:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM tasks_cache WHERE task_id = ?", (task_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

async def save_task_cache(
    task_id: str,
    last_edited_time: str,
    title: str,
    status: str,
    assignee_name: str,
    assignee_telegram_id: Optional[int],
    due_date: Optional[str] = None,
    etap: Optional[str] = None,
    url: Optional[str] = None,
    db_path: str = DB_PATH
):
    async with aiosqlite.connect(db_path) as db:
        await db.execute("""
            INSERT INTO tasks_cache (task_id, last_edited_time, title, status, etap, assignee_name, assignee_telegram_id, due_date, url)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(task_id) DO UPDATE SET
                last_edited_time = excluded.last_edited_time,
                title = excluded.title,
                status = excluded.status,
                etap = COALESCE(excluded.etap, tasks_cache.etap),
                assignee_name = excluded.assignee_name,
                assignee_telegram_id = excluded.assignee_telegram_id,
                due_date = excluded.due_date,
                url = COALESCE(excluded.url, tasks_cache.url),
                notified_at = CURRENT_TIMESTAMP
        """, (task_id, last_edited_time, title, status, etap, assignee_name, assignee_telegram_id, due_date, url))
        await db.commit()

async def update_task_status_cache(task_id: str, new_status: str, db_path: str = DB_PATH):
    async with aiosqlite.connect(db_path) as db:
        await db.execute("UPDATE tasks_cache SET status = ? WHERE task_id = ?", (new_status, task_id))
        await db.commit()

async def mark_deadline_reminded(task_id: str, db_path: str = DB_PATH):
    async with aiosqlite.connect(db_path) as db:
        await db.execute("UPDATE tasks_cache SET deadline_reminded = 1 WHERE task_id = ?", (task_id,))
        await db.commit()

async def get_active_tasks_for_user(telegram_id: int, db_path: str = DB_PATH) -> List[Dict[str, Any]]:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT * FROM tasks_cache 
            WHERE assignee_telegram_id = ? 
              AND LOWER(status) NOT IN ('done', 'выполнено', 'closed', 'завершено', 'готово', 'опубликован', 'завершен')
            ORDER BY due_date ASC
        """, (telegram_id,)) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]
