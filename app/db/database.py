from motor.motor_asyncio import AsyncIOMotorClient
from app.config import get_settings

settings = get_settings()

client: AsyncIOMotorClient = None
db = None


async def connect_db():
    global client, db
    client = AsyncIOMotorClient(settings.mongodb_url)
    db = client[settings.db_name]
    print(f"[OK] Connected to MongoDB: {settings.db_name}")


async def close_db():
    global client
    if client:
        client.close()
        print("[CLOSED] MongoDB connection closed")


def get_db():
    return db


def get_collection(name: str):
    return db[name]
