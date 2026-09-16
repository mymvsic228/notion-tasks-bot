import asyncio
import sys

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from config import settings
from notion_service import notion_service

async def run_diagnostics():
    print("=" * 65)
    print("  Диагностика подключения к Notion API")
    print("=" * 65)

    print(f"Target Database/Page ID: {settings.clean_notion_database_id}")
    print(f"Notion Token: {'Указан (' + settings.NOTION_TOKEN[:10] + '...)' if settings.NOTION_TOKEN else 'НЕ УКАЗАН ❌'}")
    print()

    if not settings.NOTION_TOKEN:
        print("❌ ОШИБКА: NOTION_TOKEN не найден в .env файле.")
        print("Пожалуйста, создайте внутреннюю интеграцию на https://www.notion.so/my-integrations")
        print("и добавьте NOTION_TOKEN=secret_... в файл .env.")
        print("=" * 65)
        return

    print("⏳ Проверка подключения к Notion...")
    ok, msg, details = await notion_service.verify_connection()

    if not ok:
        print(f"❌ ОШИБКА ПОДКЛЮЧЕНИЯ:\n{msg}\n")
        print("Инструкция по решению:")
        print("1. Откройте нужную страницу/базу в Notion в браузере.")
        print("2. Нажмите кнопку '...' (в правом верхнем углу).")
        print("3. Выберите 'Connections' ('Подключения') -> 'Connect to' / 'Добавить подключение'.")
        print("4. Выберите созданную интеграцию, чей токен указан в .env.")
        print("=" * 65)
        return

    print(f"✅ {msg}\n")
    if details:
        print(f"📌 Название базы: {details.get('title')}")
        print(f"📌 Найденные колонки (properties):")
        for prop in details.get("properties", []):
            print(f"   • {prop}")
    print()

    # Query tasks
    print("⏳ Получение задач из базы...")
    tasks = await notion_service.query_tasks()
    print(f"📊 Всего найдено задач: {len(tasks)}")
    for i, t in enumerate(tasks[:5], 1):
        assignees = ", ".join([a.get("name") or "Не указано" for a in t.get("assignees", [])]) or "Не назначен"
        print(f"\n[{i}] {t['title']}")
        print(f"    • Статус: {t['status']}")
        print(f"    • Исполнитель: {assignees}")
        if t.get("telegram_prop"):
            print(f"    • Telegram поле: {t['telegram_prop']}")
        if t.get("due_date"):
            print(f"    • Дедлайн: {t['due_date']}")
        print(f"    • URL: {t['url']}")

    # Workspace users
    print("\n⏳ Получение списка пользователей Notion Workspace...")
    users = await notion_service.get_workspace_users()
    print(f"👥 Найдено пользователей: {len(users)}")
    for u in users:
        print(f"   • {u['name']} (Email: {u.get('email') or 'не указан'}, ID: {u['id']})")

    print("\n" + "=" * 65)
    print("✅ Тест подключения завершен успешно!")
    print("=" * 65)

if __name__ == "__main__":
    asyncio.run(run_diagnostics())
