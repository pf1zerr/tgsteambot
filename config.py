from dataclasses import dataclass
import os
from pathlib import Path


@dataclass(frozen=True)
class Config:
    bot_token: str
    public_base_url: str
    steam_realm: str
    steam_country: str
    steam_language: str
    check_interval_seconds: int
    database_path: str
    host: str
    port: int
    ssl_verify: bool


def load_dotenv(path: str = ".env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def load_config() -> Config:
    load_dotenv()

    bot_token = os.getenv("BOT_TOKEN", "").strip()
    if not bot_token:
        raise RuntimeError("BOT_TOKEN is required")

    public_base_url = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
    if not public_base_url:
        raise RuntimeError("PUBLIC_BASE_URL is required")

    steam_realm = os.getenv("STEAM_REALM", f"{public_base_url}/").strip()
    if not steam_realm.endswith("/"):
        steam_realm += "/"

    ssl_verify = os.getenv("SSL_VERIFY", "false").strip().lower() in {"1", "true", "yes", "on"}

    return Config(
        bot_token=bot_token,
        public_base_url=public_base_url,
        steam_realm=steam_realm,
        steam_country=os.getenv("STEAM_COUNTRY", "UA").strip().upper(),
        steam_language=os.getenv("STEAM_LANGUAGE", "russian").strip(),
        check_interval_seconds=int(os.getenv("CHECK_INTERVAL_SECONDS", "1800")),
        database_path=os.getenv("DATABASE_PATH", "steam_price_bot.sqlite3").strip(),
        host=os.getenv("HOST", "0.0.0.0").strip(),
        port=int(os.getenv("PORT", "8080")),
        ssl_verify=ssl_verify,
    )
