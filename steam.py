import json
import re
import ssl
from json import JSONDecodeError
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


STEAM_OPENID = "https://steamcommunity.com/openid/login"
APP_RE = re.compile(r"store\.steampowered\.com/app/(\d+)|(?:^|\s)(\d{3,10})(?:\s|$)")
STEAM_ID_RE = re.compile(r"https://steamcommunity\.com/openid/id/(\d+)")
USER_AGENT = "TelegramSteamPriceBot/1.0"
_SSL_CONTEXT = ssl._create_unverified_context()


class SteamJsonError(RuntimeError):
    def __init__(self, url: str, body: str):
        preview = body[:300].replace("\n", " ").replace("\r", " ")
        super().__init__(f"Steam returned non-JSON response for {url}: {preview}")
        self.url = url
        self.preview = preview


def set_ssl_verify(enabled: bool) -> None:
    global _SSL_CONTEXT
    _SSL_CONTEXT = ssl.create_default_context() if enabled else ssl._create_unverified_context()


def extract_app_id(text: str) -> int | None:
    match = APP_RE.search(text.strip())
    if not match:
        return None
    return int(match.group(1) or match.group(2))


def format_price(price: dict | Any | None) -> str:
    if price is None:
        return "цена неизвестна"

    def get(name: str, default=None):
        if isinstance(price, dict):
            return price.get(name, default)
        return price[name] if name in price.keys() else default

    if not get("is_available", True):
        return "недоступно"
    if get("is_free"):
        return "бесплатно"
    final_price = get("final_price")
    currency = get("currency") or ""
    if final_price is None:
        return "цена скрыта"
    amount = final_price / 100
    discount = get("discount_percent") or 0
    label = f"{amount:.2f} {currency}".strip()
    if discount:
        label += f" (-{discount}%)"
    return label


def build_openid_url(public_base_url: str, realm: str, token: str) -> str:
    return_to = f"{public_base_url}/steam/callback?token={token}"
    query = {
        "openid.ns": "http://specs.openid.net/auth/2.0",
        "openid.mode": "checkid_setup",
        "openid.return_to": return_to,
        "openid.realm": realm,
        "openid.identity": "http://specs.openid.net/auth/2.0/identifier_select",
        "openid.claimed_id": "http://specs.openid.net/auth/2.0/identifier_select",
    }
    return f"{STEAM_OPENID}?{urlencode(query)}"


def http_json(url: str, params: dict | None = None, timeout: int = 25):
    if params:
        url = f"{url}?{urlencode(params)}"
    request = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json,text/plain,*/*",
            "Accept-Language": "ru,en;q=0.9",
            "Referer": "https://store.steampowered.com/",
        },
    )
    with urlopen(request, timeout=timeout, context=_SSL_CONTEXT) as response:
        body = response.read().decode("utf-8", errors="replace")
    try:
        return json.loads(body)
    except JSONDecodeError as exc:
        raise SteamJsonError(url, body) from exc


def http_post_text(url: str, data: dict, timeout: int = 20) -> str:
    body = urlencode(data).encode("utf-8")
    request = Request(
        url,
        data=body,
        headers={
            "User-Agent": USER_AGENT,
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    with urlopen(request, timeout=timeout, context=_SSL_CONTEXT) as response:
        return response.read().decode("utf-8", errors="replace")


def verify_openid(params: dict[str, str]) -> str | None:
    payload = dict(params)
    payload["openid.mode"] = "check_authentication"
    text = http_post_text(STEAM_OPENID, payload)
    if "is_valid:true" not in text:
        return None
    claimed_id = params.get("openid.claimed_id", "")
    match = STEAM_ID_RE.fullmatch(claimed_id)
    return match.group(1) if match else None


def fetch_wishlist(steam_id: str) -> list[dict[str, Any]]:
    url = "https://api.steampowered.com/IWishlistService/GetWishlist/v1"
    try:
        data = http_json(url, {"steamid": steam_id})
    except HTTPError as exc:
        if exc.code == 403:
            raise PermissionError("wishlist is private") from exc
        raise

    items = data.get("response", {}).get("items", [])
    if not items and isinstance(data.get("items"), list):
        items = data["items"]

    games = []
    for item in items:
        app_id = item.get("appid") or item.get("app_id")
        if str(app_id).isdigit():
            games.append({"app_id": int(app_id), "title": item.get("name")})
    return games


def fetch_app_details(app_id: int, country: str, language: str) -> tuple[str | None, dict]:
    data = http_json(
        "https://store.steampowered.com/api/appdetails",
        {
            "appids": str(app_id),
            "filters": "basic,price_overview",
            "cc": country,
            "l": language,
        },
    )

    wrapper = data.get(str(app_id), {})
    if not wrapper.get("success"):
        return None, {"is_available": False}

    app = wrapper.get("data", {})
    title = app.get("name")
    if app.get("is_free"):
        return title, {
            "currency": None,
            "final_price": 0,
            "initial_price": 0,
            "discount_percent": 100,
            "is_free": True,
            "is_available": True,
        }

    overview = app.get("price_overview")
    if not overview:
        return title, {
            "currency": None,
            "final_price": None,
            "initial_price": None,
            "discount_percent": 0,
            "is_free": False,
            "is_available": True,
        }

    return title, {
        "currency": overview.get("currency"),
        "final_price": overview.get("final"),
        "initial_price": overview.get("initial"),
        "discount_percent": overview.get("discount_percent", 0),
        "is_free": False,
        "is_available": True,
    }


def price_changed(old: Any, new_price: dict) -> bool:
    return (
        old["currency"] != new_price.get("currency")
        or old["final_price"] != new_price.get("final_price")
        or old["initial_price"] != new_price.get("initial_price")
        or old["discount_percent"] != new_price.get("discount_percent")
        or bool(old["is_free"]) != bool(new_price.get("is_free", False))
        or bool(old["is_available"]) != bool(new_price.get("is_available", True))
    )
