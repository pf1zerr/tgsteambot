import html
import json
import logging
import secrets
import ssl
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

from config import load_config
from database import Database
from steam import (
    SteamJsonError,
    build_openid_url,
    extract_app_id,
    fetch_app_details,
    fetch_wishlist,
    format_price,
    price_changed,
    set_ssl_verify,
    verify_openid,
)


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("steam-price-bot")

config = load_config()
db = Database(config.database_path)
set_ssl_verify(config.ssl_verify)


BTN_LINK = "Привязать Steam"
BTN_LINKED = "Steam подключен"
BTN_SYNC = "Обновить wishlist"
BTN_LIST = "Мои игры"
BTN_ADD = "Добавить игру"
BTN_HELP = "Помощь"


def main_menu(user_id: int | None = None) -> dict:
    link_button = BTN_LINK
    if user_id is not None:
        user = db.get_user(user_id)
        if user and user["steam_id"]:
            link_button = BTN_LINKED

    return {
        "keyboard": [
            [{"text": link_button}, {"text": BTN_SYNC}],
            [{"text": BTN_LIST}, {"text": BTN_ADD}],
            [{"text": BTN_HELP}],
        ],
        "resize_keyboard": True,
        "is_persistent": True,
    }


def steam_login_button(url: str) -> dict:
    return {
        "inline_keyboard": [
            [{"text": "Войти через Steam", "url": url}],
        ],
    }


def steam_account_buttons() -> dict:
    return {
        "inline_keyboard": [
            [{"text": "Отвязать Steam", "callback_data": "unlink_steam"}],
        ],
    }


class TelegramBot:
    def __init__(self, token: str, ssl_verify: bool):
        self.base_url = f"https://api.telegram.org/bot{token}"
        self.ssl_context = ssl.create_default_context() if ssl_verify else ssl._create_unverified_context()

    def api(self, method: str, payload: dict | None = None, timeout: int = 35):
        data = json.dumps(payload or {}).encode("utf-8")
        request = Request(
            f"{self.base_url}/{method}",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        with urlopen(request, timeout=timeout, context=self.ssl_context) as response:
            result = json.loads(response.read().decode("utf-8"))
        if not result.get("ok"):
            raise RuntimeError(result)
        return result["result"]

    def send_message(self, chat_id: int, text: str, reply_markup: dict | None = None) -> None:
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        self.api("sendMessage", payload)

    def get_updates(self, offset: int | None) -> list[dict]:
        payload = {"timeout": 30, "allowed_updates": ["message", "callback_query"]}
        if offset is not None:
            payload["offset"] = offset
        return self.api("getUpdates", payload, timeout=40)

    def answer_callback_query(self, callback_query_id: str, text: str = "") -> None:
        payload = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text
        self.api("answerCallbackQuery", payload)


bot = TelegramBot(config.bot_token, config.ssl_verify)


def help_text() -> str:
    return (
        "<b>Steam Price Bot</b>\n\n"
        "Я слежу за ценами игр Steam и сообщаю, когда цена, скидка или доступность меняется.\n\n"
        "<b>Что можно сделать:</b>\n"
        "- привязать Steam через кнопку входа;\n"
        "- импортировать wishlist;\n"
        "- добавить игру вручную командой <code>/add ссылка_steam</code>;\n"
        "- посмотреть список отслеживания;\n"
        "- удалить игру командой <code>/remove app_id</code>."
    )


def handle_start(chat_id: int, user_id: int) -> None:
    db.upsert_user(user_id)
    bot.send_message(chat_id, help_text(), main_menu(user_id))


def handle_link(chat_id: int, user_id: int) -> None:
    user = db.get_user(user_id)
    if user and user["steam_id"]:
        bot.send_message(
            chat_id,
            "<b>Steam уже подключен</b>\n\n"
            f"SteamID: <code>{html.escape(user['steam_id'])}</code>\n\n"
            "Можно обновить wishlist или отвязать аккаунт.",
            steam_account_buttons(),
        )
        bot.send_message(chat_id, "Меню обновлено.", main_menu(user_id))
        return

    token = secrets.token_urlsafe(24)
    db.create_link_token(token, user_id)
    url = build_openid_url(config.public_base_url, config.steam_realm, token)
    bot.send_message(
        chat_id,
        "<b>Привязка Steam</b>\n\n"
        "Нажмите кнопку ниже и подтвердите вход через Steam. После возврата в Telegram можно нажать "
        f"<b>{BTN_SYNC}</b>.",
        steam_login_button(url),
    )


def handle_sync(chat_id: int, user_id: int) -> None:
    user = db.get_user(user_id)
    if not user or not user["steam_id"]:
        bot.send_message(chat_id, "Сначала привяжите Steam.", main_menu(user_id))
        return

    bot.send_message(chat_id, "Синхронизирую wishlist. Это может занять несколько секунд.", main_menu(user_id))
    try:
        games = fetch_wishlist(user["steam_id"])
    except PermissionError:
        bot.send_message(chat_id, "Steam не отдал wishlist. Проверьте, что профиль и список желаний публичные.", main_menu(user_id))
        return
    except SteamJsonError:
        logger.exception("wishlist sync failed")
        bot.send_message(
            chat_id,
            "Steam привязан, но wishlist сейчас не отдается как данные.\n\n"
            "Проверьте приватность профиля и списка желаний, подождите минуту и попробуйте снова.",
            main_menu(user_id),
        )
        return
    except Exception:
        logger.exception("wishlist sync failed")
        bot.send_message(chat_id, "Не получилось получить wishlist из Steam. Попробуйте позже.", main_menu(user_id))
        return

    count = db.add_many_wishlist_games(user_id, games)
    bot.send_message(
        chat_id,
        f"Готово. В отслеживании из wishlist: <b>{count}</b> игр.\n\n"
        "Первичная проверка цен пройдет тихо, без пачки уведомлений.",
        main_menu(user_id),
    )


def handle_add(chat_id: int, user_id: int, text: str) -> None:
    app_id = extract_app_id(text)
    if not app_id:
        bot.send_message(
            chat_id,
            "Пришлите ссылку так:\n"
            "<code>/add https://store.steampowered.com/app/730/CounterStrike_2/</code>",
            main_menu(user_id),
        )
        return

    try:
        title, price = fetch_app_details(app_id, config.steam_country, config.steam_language)
    except Exception:
        logger.exception("app details failed")
        bot.send_message(chat_id, "Не получилось получить игру из Steam. Проверьте ссылку и попробуйте еще раз.", main_menu(user_id))
        return

    db.add_or_update_game(user_id, app_id, title, "manual", price)
    bot.send_message(
        chat_id,
        f"Добавил: <b>{html.escape(title or str(app_id))}</b>\n"
        f"Текущая цена: <b>{format_price(price)}</b>",
        main_menu(user_id),
    )


def handle_list(chat_id: int, user_id: int) -> None:
    games = db.list_games(user_id)
    if not games:
        bot.send_message(chat_id, "Список пуст. Добавьте игру вручную или синхронизируйте wishlist.", main_menu(user_id))
        return

    lines = ["<b>Отслеживаемые игры</b>"]
    for game in games[:40]:
        title = html.escape(game["title"] or str(game["app_id"]))
        lines.append(f"\n<b>{title}</b>")
        lines.append(f"ID: <code>{game['app_id']}</code>")
        lines.append(f"Цена: {format_price(game)}")
    if len(games) > 40:
        lines.append(f"\nИ еще {len(games) - 40} игр.")
    bot.send_message(chat_id, "\n".join(lines), main_menu(user_id))


def handle_remove(chat_id: int, user_id: int, text: str) -> None:
    app_id = extract_app_id(text)
    if not app_id:
        bot.send_message(chat_id, "Укажите app_id или ссылку: <code>/remove 730</code>", main_menu(user_id))
        return
    removed = db.remove_game(user_id, app_id)
    bot.send_message(chat_id, "Удалил из отслеживания." if removed else "Такой игры нет в вашем списке.", main_menu(user_id))


def normalize_command(text: str) -> str:
    command = text.split(maxsplit=1)[0].split("@", 1)[0].lower()
    button_map = {
        BTN_LINK.lower(): "/link",
        BTN_LINKED.lower(): "/link",
        BTN_SYNC.lower(): "/sync",
        BTN_LIST.lower(): "/list",
        BTN_ADD.lower(): "/add",
        BTN_HELP.lower(): "/help",
    }
    return button_map.get(text.lower(), command)


def handle_message(message: dict) -> None:
    text = (message.get("text") or "").strip()
    chat_id = message["chat"]["id"]
    user_id = message["from"]["id"]
    command = normalize_command(text)

    if command == "/start":
        handle_start(chat_id, user_id)
    elif command == "/help":
        bot.send_message(chat_id, help_text(), main_menu(user_id))
    elif command == "/link":
        handle_link(chat_id, user_id)
    elif command == "/sync":
        handle_sync(chat_id, user_id)
    elif command == "/add":
        handle_add(chat_id, user_id, text)
    elif command == "/list":
        handle_list(chat_id, user_id)
    elif command == "/remove":
        handle_remove(chat_id, user_id, text)
    else:
        bot.send_message(chat_id, "Выберите действие на клавиатуре или отправьте /help.", main_menu(user_id))


def handle_callback_query(callback_query: dict) -> None:
    callback_id = callback_query["id"]
    data = callback_query.get("data")
    user_id = callback_query["from"]["id"]
    message = callback_query.get("message") or {}
    chat_id = message.get("chat", {}).get("id", user_id)

    if data == "unlink_steam":
        db.unlink_steam(user_id)
        bot.answer_callback_query(callback_id, "Steam отвязан")
        bot.send_message(chat_id, "Steam отвязан. Wishlist больше не будет синхронизироваться, пока вы не подключите Steam снова.", main_menu(user_id))
        return

    bot.answer_callback_query(callback_id)


class SteamCallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        url = urlparse(self.path)
        if url.path == "/":
            self.respond(200, "Steam price bot is running. Use /steam/callback for Steam OpenID.")
            return

        if url.path == "/favicon.ico":
            self.respond(204, "")
            return

        if url.path != "/steam/callback":
            self.respond(404, "Not found")
            return

        params = {key: values[-1] for key, values in parse_qs(url.query).items()}
        token = params.get("token")
        if not token:
            self.respond(400, "Missing token")
            return

        token_row = db.get_link_token(token)
        if not token_row:
            self.respond(400, "This link is expired. Please run /link again.")
            return

        steam_id = verify_openid(params)
        if not steam_id:
            self.respond(400, "Steam OpenID verification failed.")
            return

        telegram_id = token_row["telegram_id"]
        db.delete_link_token(token)
        db.upsert_user(telegram_id, steam_id)
        bot.send_message(
            telegram_id,
            f"Steam привязан: <code>{steam_id}</code>\nТеперь можно обновить wishlist.",
            main_menu(telegram_id),
        )
        self.respond(200, "Steam linked. You can return to Telegram.")

    def respond(self, status: int, text: str) -> None:
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:
        logger.info("openid callback: " + format, *args)


def run_callback_server() -> None:
    server = ThreadingHTTPServer((config.host, config.port), SteamCallbackHandler)
    logger.info("OpenID callback server is listening on %s:%s", config.host, config.port)
    server.serve_forever()


def price_checker() -> None:
    time.sleep(5)
    while True:
        try:
            games = db.all_games()
            for game in games:
                try:
                    title, new_price = fetch_app_details(game["app_id"], config.steam_country, config.steam_language)
                    first_price_check = game["last_checked_at"] is None
                    changed = price_changed(game, new_price)
                    db.update_price(game["telegram_id"], game["app_id"], title, new_price)

                    if changed and not first_price_check:
                        old_text = format_price(game)
                        new_text = format_price(new_price)
                        bot.send_message(
                            game["telegram_id"],
                            "<b>Цена изменилась</b>\n\n"
                            f"<b>{html.escape(title or game['title'] or str(game['app_id']))}</b>\n"
                            f"Было: {old_text}\n"
                            f"Стало: <b>{new_text}</b>\n"
                            f"https://store.steampowered.com/app/{game['app_id']}/",
                            main_menu(game["telegram_id"]),
                        )
                    time.sleep(1)
                except Exception:
                    logger.exception("price check failed for app %s", game["app_id"])
        except Exception:
            logger.exception("price checker loop failed")

        time.sleep(config.check_interval_seconds)


def polling_loop() -> None:
    offset = None
    logger.info("Telegram polling started")
    while True:
        try:
            for update in bot.get_updates(offset):
                offset = update["update_id"] + 1
                message = update.get("message")
                if message:
                    handle_message(message)
                callback_query = update.get("callback_query")
                if callback_query:
                    handle_callback_query(callback_query)
        except HTTPError as exc:
            logger.warning("Telegram API HTTP error: %s", exc)
            time.sleep(5)
        except Exception:
            logger.exception("Telegram polling failed")
            time.sleep(5)


def main() -> None:
    threading.Thread(target=run_callback_server, daemon=True).start()
    threading.Thread(target=price_checker, daemon=True).start()
    polling_loop()


if __name__ == "__main__":
    main()
