import os
from typing import List
from pydantic_settings import BaseSettings
from dotenv import load_dotenv

load_dotenv()

class Settings(BaseSettings):
    BOT_TOKEN: str = ""
    NOTION_TOKEN: str = ""
    NOTION_DATABASE_ID: str = "0fffed8ebc6348b699f24122c59fffc9"
    POLL_INTERVAL_SECONDS: int = 10
    ADMIN_IDS: str = "486058343,1530089636"
    NOTION_VERSION: str = "2022-06-28"

    @property
    def admin_id_list(self) -> List[int]:
        if not self.ADMIN_IDS:
            return []
        ids = []
        for part in self.ADMIN_IDS.split(","):
            part = part.strip()
            if part.isdigit():
                ids.append(int(part))
        return ids

    @property
    def clean_notion_database_id(self) -> str:
        # Returns 32-char hex string without hyphens or URL junk
        raw = self.NOTION_DATABASE_ID.strip()
        # If full URL is provided (e.g. notion.so/workspace/title-2d1bc62a0c7081589955000398a16908?v=...)
        if "notion.so" in raw:
            path_part = raw.split("notion.so/")[-1].split("?")[0]
            # Take the last segment after / or -
            candidate = path_part.split("/")[-1].split("-")[-1]
            if len(candidate) == 32:
                return candidate
        return raw.replace("-", "")

    class Config:
        env_file = ".env"
        extra = "ignore"

settings = Settings()
