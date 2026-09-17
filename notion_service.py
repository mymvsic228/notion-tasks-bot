import logging
from typing import Optional, List, Dict, Any, Tuple
from notion_client import AsyncClient
from notion_client.errors import APIResponseError
from config import settings

logger = logging.getLogger(__name__)

class NotionService:
    def __init__(self, token: Optional[str] = None):
        self.token = token or settings.NOTION_TOKEN
        self.client = AsyncClient(auth=self.token) if self.token else None
        self._cached_schema: Optional[Dict[str, Any]] = None
        self._target_database_id: Optional[str] = None
        self._target_data_source_id: Optional[str] = None

    def update_token(self, token: str):
        self.token = token
        self.client = AsyncClient(auth=self.token)

    async def verify_connection(self, raw_id: Optional[str] = None) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """
        Tests connection to Notion API and inspects the database/page ID.
        Returns: (success: bool, message: str, details: Optional[dict])
        """
        if not self.token or not self.client:
            return False, "Токен Notion (NOTION_TOKEN) не указан в .env файле.", None

        target_id = (raw_id or settings.clean_notion_database_id).replace("-", "")
        if not target_id:
            return False, "ID базы данных Notion не указан.", None

        # 1. Try to fetch as Database
        try:
            db_info = await self.client.databases.retrieve(database_id=target_id)
            title_list = db_info.get("title", [])
            title = "".join([t.get("plain_text", "") for t in title_list]) or "Без названия"
            self._target_database_id = target_id
            self._cached_schema = db_info.get("properties", {})
            
            if "data_sources" in db_info and len(db_info["data_sources"]) > 0:
                self._target_data_source_id = db_info["data_sources"][0]["id"]
            else:
                self._target_data_source_id = None
                
            return True, f"Успешно подключено к базе данных Notion: «{title}»", {
                "type": "database",
                "id": target_id,
                "title": title,
                "properties": list(self._cached_schema.keys())
            }
        except APIResponseError as e:
            logger.warning(f"Failed to retrieve database directly: {e}")

        # 2. If not found as database, check if it's a Page containing a database
        try:
            page_info = await self.client.pages.retrieve(page_id=target_id)
            # Find child databases in this page
            children = await self.client.blocks.children.list(block_id=target_id)
            child_dbs = []
            for block in children.get("results", []):
                if block.get("type") == "child_database":
                    child_dbs.append({
                        "id": block.get("id"),
                        "title": block.get("child_database", {}).get("title", "Без названия")
                    })

            if child_dbs:
                # Select the first child database
                first_db = child_dbs[0]
                self._target_database_id = first_db["id"].replace("-", "")
                db_info = await self.client.databases.retrieve(database_id=self._target_database_id)
                self._cached_schema = db_info.get("properties", {})
                return True, (
                    f"Указанный ID принадлежит странице, внутри найдена база данных: «{first_db['title']}». "
                    f"Используем её (ID: {self._target_database_id})."
                ), {
                    "type": "child_database",
                    "id": self._target_database_id,
                    "title": first_db["title"],
                    "all_child_dbs": child_dbs,
                    "properties": list(self._cached_schema.keys())
                }
            else:
                return False, (
                    f"Указанный ID ({target_id}) — это страница Notion, но внутри неё не найдено дочерних баз данных (child_database). "
                    "Убедитесь, что база данных добавлена на эту страницу или укажите ID самой базы данных."
                ), None
        except APIResponseError as err:
            if err.code == "object_not_found":
                return False, (
                    f"Объект Notion с ID {target_id} не найден. "
                    "Убедитесь, что вы предоставили доступ интеграции (кнопка '...' на странице -> 'Connections' / 'Подключения' -> добавить интеграцию)."
                ), None
            elif err.code == "unauthorized":
                return False, "Неверный токен NOTION_TOKEN (ошибка 401 Unauthorized). Проверьте ключ интеграции.", None
            return False, f"Ошибка Notion API: {err.message} (код: {err.code})", None
        except Exception as ex:
            return False, f"Ошибка соединения: {str(ex)}", None

    async def get_workspace_users(self) -> List[Dict[str, Any]]:
        """Returns all visible users in the Notion workspace."""
        if not self.client:
            return []
        try:
            resp = await self.client.users.list()
            users = []
            for u in resp.get("results", []):
                if u.get("type") == "person":
                    users.append({
                        "id": u.get("id"),
                        "name": u.get("name") or "Пользователь без имени",
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
        status = "Не указан"
        status_prop_name = None
        status_prop_type = None
        due_date = None
        priority = None

        for prop_name, prop_data in props.items():
            prop_type = prop_data.get("type")
            lower_name = prop_name.lower()

            # 1. Title / Название
            if prop_type == "title":
                title_objs = prop_data.get("title", [])
                if title_objs:
                    title = "".join([t.get("plain_text", "") for t in title_objs]).strip() or "Без названия"

            # 2. Assignees / Исполнители
            elif prop_type == "people":
                people = prop_data.get("people", [])
                for p in people:
                    assignees.append({
                        "id": p.get("id"),
                        "name": p.get("name"),
                        "email": p.get("person", {}).get("email")
                    })
            elif ("assign" in lower_name or "ответствен" in lower_name or "исполнител" in lower_name):
                if prop_type == "select" and prop_data.get("select"):
                    assignees.append({"id": None, "name": prop_data["select"].get("name"), "email": None})
                elif prop_type == "rich_text" and prop_data.get("rich_text"):
                    txt = "".join([t.get("plain_text", "") for t in prop_data["rich_text"]]).strip()
                    if txt:
                        assignees.append({"id": None, "name": txt, "email": None})

            # 3. Telegram property (e.g. column called "Telegram", "TG", "@username")
            if "telegram" in lower_name or lower_name in ("tg", "тг"):
                if prop_type == "rich_text":
                    telegram_prop = "".join([t.get("plain_text", "") for t in prop_data.get("rich_text", [])]).strip()
                elif prop_type == "url":
                    telegram_prop = prop_data.get("url")
                elif prop_type == "phone_number":
                    telegram_prop = prop_data.get("phone_number")

            # 4. Status / Статус
            if prop_type == "status":
                status_obj = prop_data.get("status")
                if status_obj:
                    status = status_obj.get("name")
                status_prop_name = prop_name
                status_prop_type = "status"
            elif ("status" in lower_name or "статус" in lower_name or "состояние" in lower_name or "этап" in lower_name) and prop_type == "select":
                select_obj = prop_data.get("select")
                if select_obj:
                    status = select_obj.get("name")
                status_prop_name = prop_name
                status_prop_type = "select"

            # 5. Due Date / Дедлайн
            if prop_type == "date":
                date_obj = prop_data.get("date")
                if date_obj:
                    due_date = date_obj.get("start")
            elif ("deadline" in lower_name or "дедлайн" in lower_name or "срок" in lower_name) and prop_type == "date":
                date_obj = prop_data.get("date")
                if date_obj:
                    due_date = date_obj.get("start")

            # 6. Priority / Приоритет
            if "prior" in lower_name or "приоритет" in lower_name or "важност" in lower_name:
                if prop_type == "select" and prop_data.get("select"):
                    priority = prop_data["select"].get("name")

        return {
            "id": page_id,
            "title": title,
            "assignees": assignees,
            "telegram_prop": telegram_prop,
            "status": status,
            "status_prop_name": status_prop_name,
            "status_prop_type": status_prop_type,
            "due_date": due_date,
            "priority": priority,
            "url": url,
            "created_time": created_time,
            "last_edited_time": last_edited_time
        }

    async def update_task_status(self, page_id: str, new_status: str, prop_name: Optional[str] = None, prop_type: Optional[str] = None) -> bool:
        """Updates the status property of a page in Notion."""
        if not self.client:
            return False

        # If property name not passed, retrieve page to inspect schema
        if not prop_name or not prop_type:
            try:
                page = await self.client.pages.retrieve(page_id=page_id)
                parsed = self._parse_page(page)
                if parsed and parsed.get("status_prop_name"):
                    prop_name = parsed["status_prop_name"]
                    prop_type = parsed["status_prop_type"]
                else:
                    prop_name = "Status"
                    prop_type = "status"
            except Exception as e:
                logger.error(f"Error fetching page schema for update: {e}")
                prop_name = "Status"
                prop_type = "status"

        try:
            if prop_type == "status":
                payload = {prop_name: {"status": {"name": new_status}}}
            else:
                payload = {prop_name: {"select": {"name": new_status}}}

            await self.client.pages.update(page_id=page_id, properties=payload)
            logger.info(f"Updated page {page_id} status to {new_status}")
            return True
        except Exception as e:
            logger.error(f"Failed to update task status in Notion: {e}")
            return False

notion_service = NotionService()
