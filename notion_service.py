import logging
from typing import Optional, List, Dict, Any, Tuple
from notion_client import AsyncClient
from config import settings

logger = logging.getLogger(__name__)

class NotionService:
    def __init__(self, token: Optional[str] = None):
        self.token = token or settings.NOTION_TOKEN
        self.client = AsyncClient(auth=self.token) if self.token else None
        self._target_database_id: Optional[str] = None
        self._target_data_source_id: Optional[str] = None

    def update_token(self, token: str):
        self.token = token
        self.client = AsyncClient(auth=self.token)

    async def verify_connection(self, database_id: Optional[str] = None) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """
        Validates connection by querying the database or page.
        Returns (is_success, status_message, database_details)
        """
        if not self.token or not self.client:
            return False, "Notion API токен не задан. Укажите NOTION_TOKEN в настройках.", None

        db_id = database_id or settings.clean_notion_database_id
        if not db_id:
            return False, "ID базы данных Notion не указан. Укажите NOTION_DATABASE_ID.", None

        try:
            # 1. Try retrieving database directly
            try:
                db = await self.client.databases.retrieve(database_id=db_id)
                self._target_database_id = db_id
                
                # Check for newer Notion data sources feature
                if "data_sources" in db and isinstance(db["data_sources"], list) and len(db["data_sources"]) > 0:
                    self._target_data_source_id = db["data_sources"][0]["id"]
                    logger.info(f"Detected Notion data_source ID: {self._target_data_source_id}")
                else:
                    self._target_data_source_id = None
                
                title_list = db.get("title", [])
                title = "".join([t.get("plain_text", "") for t in title_list]) or "Без названия"
                properties = list(db.get("properties", {}).keys())
                return True, f"Успешно подключено к базе данных Notion: «{title}»", {
                    "id": db_id,
                    "title": title,
                    "properties": properties
                }
            except Exception as e:
                # 2. Try page retrieve
                try:
                    page = await self.client.pages.retrieve(page_id=db_id)
                    title = "Страница Notion"
                    props = page.get("properties", {})
                    for p_name, p_val in props.items():
                        if p_val.get("type") == "title":
                            title = "".join([t.get("plain_text", "") for t in p_val.get("title", [])]) or title
                            break
                    self._target_database_id = db_id
                    return True, f"Успешно подключено к странице Notion: «{title}»", {
                        "id": db_id,
                        "title": title,
                        "properties": list(props.keys())
                    }
                except Exception:
                    raise e
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Notion connection error: {error_msg}")
            if "Could not find database with ID" in error_msg or "Could not find page with ID" in error_msg:
                return False, (
                    f"Notion не нашел объект с ID '{db_id}'. "
                    "Убедитесь, что ID скопирован корректно и что вашей интеграции предоставлен доступ к этой базе "
                    "(странице) через меню 'Add connections'."
                ), None
            elif "unauthorized" in error_msg.lower():
                return False, "Неверный токен Notion API. Проверьте правильность токена интеграции.", None
            else:
                return False, f"Ошибка подключения к Notion: {error_msg}", None

    async def get_workspace_users(self) -> List[Dict[str, Any]]:
        """Returns all visible users in the Notion workspace."""
        if not self.client:
            return []
        try:
            resp = await self.client.users.list()
            users = []
            for u in resp.get("results", []):
                if u.get("type") == "person":
                    name = (u.get("name") or "Пользователь без имени").strip()
                    users.append({
                        "id": u.get("id", "").strip(),
                        "name": name,
                        "email": u.get("person", {}).get("email"),
                        "avatar_url": u.get("avatar_url")
                    })
            return users
        except Exception as e:
            logger.error(f"Error fetching Notion users: {e}")
            return []

    async def query_tasks(self, database_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Queries tasks from the Notion database."""
        if not self.client:
            return []
        db_id = database_id or self._target_database_id or settings.clean_notion_database_id
        try:
            if self._target_data_source_id:
                resp = await self.client.request(path=f"data_sources/{self._target_data_source_id}/query", method="POST")
            else:
                resp = await self.client.databases.query(database_id=db_id)
                
            tasks = []
            for page in resp.get("results", []):
                parsed = self._parse_page(page)
                if parsed:
                    tasks.append(parsed)
            return tasks
        except Exception as e:
            logger.error(f"Error querying Notion tasks: {e}")
            return []

    def _parse_page(self, page: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        page_id = page.get("id")
        last_edited_time = page.get("last_edited_time")
        created_time = page.get("created_time")
        url = page.get("url")
        props = page.get("properties", {})

        title = "Без названия"
        assignees = []
        telegram_prop = None
        status = "Not started"
        etap = None
        due_date = None
        priority = None

        # Prioritized status tracking
        status_candidate = None
        select_status_candidate = None

        for prop_name, prop_data in props.items():
            prop_type = prop_data.get("type")
            lower_name = prop_name.lower().strip()

            # 1. Title / Название / Креатив
            if prop_type == "title":
                title_objs = prop_data.get("title", [])
                if title_objs:
                    title = "".join([t.get("plain_text", "") for t in title_objs]).strip() or "Без названия"

            # 2. Assignees / Исполнители / Ответственный
            elif prop_type == "people":
                people = prop_data.get("people", [])
                for p in people:
                    assignees.append({
                        "id": p.get("id", "").strip(),
                        "name": (p.get("name") or "").strip(),
                        "email": p.get("person", {}).get("email")
                    })
            elif ("assign" in lower_name or "ответствен" in lower_name or "исполнител" in lower_name):
                if prop_type == "select" and prop_data.get("select"):
                    assignees.append({"id": None, "name": prop_data["select"].get("name", "").strip(), "email": None})
                elif prop_type == "rich_text" and prop_data.get("rich_text"):
                    txt = "".join([t.get("plain_text", "") for t in prop_data["rich_text"]]).strip()
                    if txt:
                        assignees.append({"id": None, "name": txt, "email": None})

            # 3. Telegram property
            if "telegram" in lower_name or lower_name in ("tg", "тг"):
                if prop_type == "rich_text":
                    telegram_prop = "".join([t.get("plain_text", "") for t in prop_data.get("rich_text", [])]).strip()
                elif prop_type == "url":
                    telegram_prop = prop_data.get("url")
                elif prop_type == "phone_number":
                    telegram_prop = prop_data.get("phone_number")

            # 4. Status / Статус / Этап
            if prop_name == "Status" and prop_type == "status":
                s_obj = prop_data.get("status")
                if s_obj:
                    status_candidate = s_obj.get("name")
            elif prop_name == "Статус" and prop_type in ("select", "status"):
                val = (prop_data.get("select") or prop_data.get("status") or {}).get("name")
                if val:
                    select_status_candidate = val
            elif "этап" in lower_name:
                val = (prop_data.get("select") or prop_data.get("status") or {}).get("name")
                if val:
                    etap = val
            elif prop_type == "status" and not status_candidate:
                s_obj = prop_data.get("status")
                if s_obj:
                    status_candidate = s_obj.get("name")
            elif prop_type == "select" and ("статус" in lower_name or "status" in lower_name) and not select_status_candidate:
                s_obj = prop_data.get("select")
                if s_obj:
                    select_status_candidate = s_obj.get("name")

            # 5. Due Date / Дедлайн
            if prop_type == "date" and ("дедлайн" in lower_name or "deadline" in lower_name or "срок" in lower_name):
                date_obj = prop_data.get("date")
                if date_obj:
                    due_date = date_obj.get("start")
            elif prop_type == "date" and not due_date:
                date_obj = prop_data.get("date")
                if date_obj:
                    due_date = date_obj.get("start")

            # 6. Priority / Приоритет
            if "prior" in lower_name or "приоритет" in lower_name:
                if prop_type == "select" and prop_data.get("select"):
                    priority = prop_data["select"].get("name")

        # Determine final display status
        if status_candidate:
            status = status_candidate
        elif select_status_candidate:
            status = select_status_candidate
        elif etap:
            status = etap

        return {
            "id": page_id,
            "title": title,
            "assignees": assignees,
            "telegram_prop": telegram_prop,
            "status": status,
            "etap": etap,
            "due_date": due_date,
            "priority": priority,
            "url": url,
            "created_time": created_time,
            "last_edited_time": last_edited_time
        }

    async def update_task_status(self, page_id: str, action: str) -> bool:
        """
        Updates task status in Notion.
        action: 'in_progress' or 'done'
        """
        if not self.client:
            return False

        try:
            # Retrieve page properties to see which status columns exist
            page = await self.client.pages.retrieve(page_id=page_id)
            props = page.get("properties", {})

            payload = {}
            # 1. Update 'Status' (status type) if present
            if "Status" in props and props["Status"].get("type") == "status":
                new_val = "In progress" if action in ("in_progress", "start") else "Done"
                payload["Status"] = {"status": {"name": new_val}}

            # 2. Update 'Статус' (select or status type) if present
            if "Статус" in props:
                st_type = props["Статус"].get("type")
                new_val = "В процессе" if action in ("in_progress", "start") else "Выполнено"
                if st_type == "status":
                    payload["Статус"] = {"status": {"name": new_val}}
                elif st_type == "select":
                    payload["Статус"] = {"select": {"name": new_val}}

            if not payload:
                # Fallback: look for any status or select named status
                for p_name, p_data in props.items():
                    p_type = p_data.get("type")
                    if p_type == "status":
                        new_val = "In progress" if action in ("in_progress", "start") else "Done"
                        payload[p_name] = {"status": {"name": new_val}}
                        break

            if payload:
                await self.client.pages.update(page_id=page_id, properties=payload)
                logger.info(f"Updated Notion page {page_id} with payload: {payload}")
                return True
            else:
                logger.warning(f"No status property found on page {page_id} to update")
                return False

        except Exception as e:
            logger.error(f"Failed to update task status in Notion for {page_id}: {e}")
            return False

notion_service = NotionService()
