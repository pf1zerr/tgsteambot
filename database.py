import sqlite3
import time
from pathlib import Path
from typing import Iterable


SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS users (
    telegram_id INTEGER PRIMARY KEY,
    steam_id TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS tracked_games (
    telegram_id INTEGER NOT NULL,
    app_id INTEGER NOT NULL,
    title TEXT,
    source TEXT NOT NULL,
    currency TEXT,
    final_price INTEGER,
    initial_price INTEGER,
    discount_percent INTEGER,
    is_free INTEGER NOT NULL DEFAULT 0,
    is_available INTEGER NOT NULL DEFAULT 1,
    last_checked_at INTEGER,
    created_at INTEGER NOT NULL,
    PRIMARY KEY (telegram_id, app_id),
    FOREIGN KEY (telegram_id) REFERENCES users(telegram_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS link_tokens (
    token TEXT PRIMARY KEY,
    telegram_id INTEGER NOT NULL,
    created_at INTEGER NOT NULL
);
"""


class Database:
    def __init__(self, path: str):
        self.path = Path(path)
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    def upsert_user(self, telegram_id: int, steam_id: str | None = None) -> None:
        now = int(time.time())
        self.db.execute(
            """
            INSERT INTO users (telegram_id, steam_id, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET
                steam_id = COALESCE(excluded.steam_id, users.steam_id),
                updated_at = excluded.updated_at
            """,
            (telegram_id, steam_id, now, now),
        )
        self.db.commit()

    def get_user(self, telegram_id: int):
        return self.db.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()

    def unlink_steam(self, telegram_id: int) -> None:
        now = int(time.time())
        self.db.execute(
            "UPDATE users SET steam_id = NULL, updated_at = ? WHERE telegram_id = ?",
            (now, telegram_id),
        )
        self.db.commit()

    def create_link_token(self, token: str, telegram_id: int) -> None:
        now = int(time.time())
        self.db.execute(
            "INSERT OR REPLACE INTO link_tokens (token, telegram_id, created_at) VALUES (?, ?, ?)",
            (token, telegram_id, now),
        )
        self.db.commit()

    def get_link_token(self, token: str):
        return self.db.execute("SELECT * FROM link_tokens WHERE token = ?", (token,)).fetchone()

    def delete_link_token(self, token: str) -> None:
        self.db.execute("DELETE FROM link_tokens WHERE token = ?", (token,))
        self.db.commit()

    def add_or_update_game(
        self,
        telegram_id: int,
        app_id: int,
        title: str | None,
        source: str,
        price: dict | None = None,
    ) -> None:
        now = int(time.time())
        price = price or {}
        self.upsert_user(telegram_id)
        self.db.execute(
            """
            INSERT INTO tracked_games (
                telegram_id, app_id, title, source, currency, final_price, initial_price,
                discount_percent, is_free, is_available, last_checked_at, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(telegram_id, app_id) DO UPDATE SET
                title = COALESCE(excluded.title, tracked_games.title),
                source = CASE
                    WHEN instr(tracked_games.source, excluded.source) > 0 THEN tracked_games.source
                    ELSE tracked_games.source || ',' || excluded.source
                END,
                currency = COALESCE(excluded.currency, tracked_games.currency),
                final_price = COALESCE(excluded.final_price, tracked_games.final_price),
                initial_price = COALESCE(excluded.initial_price, tracked_games.initial_price),
                discount_percent = COALESCE(excluded.discount_percent, tracked_games.discount_percent),
                is_free = excluded.is_free,
                is_available = excluded.is_available,
                last_checked_at = COALESCE(excluded.last_checked_at, tracked_games.last_checked_at)
            """,
            (
                telegram_id,
                app_id,
                title,
                source,
                price.get("currency"),
                price.get("final_price"),
                price.get("initial_price"),
                price.get("discount_percent"),
                int(price.get("is_free", False)),
                int(price.get("is_available", True)),
                now if price else None,
                now,
            ),
        )
        self.db.commit()

    def remove_game(self, telegram_id: int, app_id: int) -> bool:
        cursor = self.db.execute(
            "DELETE FROM tracked_games WHERE telegram_id = ? AND app_id = ?",
            (telegram_id, app_id),
        )
        self.db.commit()
        return cursor.rowcount > 0

    def list_games(self, telegram_id: int):
        return self.db.execute(
            "SELECT * FROM tracked_games WHERE telegram_id = ? ORDER BY title COLLATE NOCASE, app_id",
            (telegram_id,),
        ).fetchall()

    def all_games(self):
        return self.db.execute("SELECT * FROM tracked_games ORDER BY telegram_id, app_id").fetchall()

    def update_price(self, telegram_id: int, app_id: int, title: str | None, price: dict) -> None:
        now = int(time.time())
        self.db.execute(
            """
            UPDATE tracked_games
            SET title = COALESCE(?, title),
                currency = ?,
                final_price = ?,
                initial_price = ?,
                discount_percent = ?,
                is_free = ?,
                is_available = ?,
                last_checked_at = ?
            WHERE telegram_id = ? AND app_id = ?
            """,
            (
                title,
                price.get("currency"),
                price.get("final_price"),
                price.get("initial_price"),
                price.get("discount_percent"),
                int(price.get("is_free", False)),
                int(price.get("is_available", True)),
                now,
                telegram_id,
                app_id,
            ),
        )
        self.db.commit()

    def add_many_wishlist_games(self, telegram_id: int, games: Iterable[dict]) -> int:
        count = 0
        for game in games:
            self.add_or_update_game(
                telegram_id=telegram_id,
                app_id=game["app_id"],
                title=game.get("title"),
                source="wishlist",
            )
            count += 1
        return count
