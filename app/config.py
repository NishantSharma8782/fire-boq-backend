from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    gemini_api_key: str = ""
    mongodb_url: str = "mongodb://localhost:27017"
    db_name: str = "fire_boq_db"
    upload_dir: str = "./uploads"
    frontend_url: str = "http://localhost:3000"

    class Config:
        env_file = ".env"


@lru_cache()
def get_settings():
    return Settings()
