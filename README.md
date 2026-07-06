# Steam Price Telegram Bot

Telegram-бот для відстеження цін на ігри в Steam.

Бот вміє:

- прив'язувати Steam через Steam OpenID;
- імпортувати wishlist користувача;
- додавати гру вручну за посиланням Steam;
- показувати список ігор;
- надсилати повідомлення, коли ціна змінилась;
- працювати на EC2 як `systemd`-сервіс.

## AWS-сервіси

У проєкті використовуються:

- **EC2** - сервер, на якому працює бот;
- **SQLite на EC2** - база даних з користувачами та іграми;
- **Security Group** - правила доступу до EC2;
- **GitHub Actions** - автоматичний деплой на EC2 після push у `main`.

## Файли проєкту

```text
bot.py                         # основний код бота
config.py                      # читання .env
database.py                    # робота з SQLite
steam.py                       # робота зі Steam API
.env.example                   # приклад змінних середовища
requirements.txt               # залежності
deploy/steam-price-bot.service # systemd service
deploy/ec2-setup.sh            # перше налаштування EC2
deploy/update.sh               # ручне оновлення на EC2
.github/workflows/deploy.yml   # GitHub Actions деплой
```

## Налаштування `.env`

Створіть файл `.env`:

```bash
cp .env.example .env
```

Приклад:

```env
BOT_TOKEN=telegram-bot-token
PUBLIC_BASE_URL=https://your-domain-or-ec2-url
STEAM_REALM=https://your-domain-or-ec2-url/
STEAM_COUNTRY=UA
STEAM_LANGUAGE=russian
CHECK_INTERVAL_SECONDS=1800
DATABASE_PATH=steam_price_bot.sqlite3
SSL_VERIFY=false
```

`.env` не можна додавати в GitHub. Він уже доданий у `.gitignore`.

## Локальний запуск

```bash
python bot.py
```

Для Steam OpenID потрібен публічний HTTPS callback. Локально можна використати ngrok:

```bash
ngrok http 8080
```

Потім вставити адресу ngrok у `.env`:

```env
PUBLIC_BASE_URL=https://example.ngrok-free.app
STEAM_REALM=https://example.ngrok-free.app/
```

## Деплой на EC2

1. Створіть EC2 instance з Ubuntu.
2. У Security Group відкрийте:
   - `22` для SSH;
   - `8080` для Steam OpenID callback.
3. Завантажте код у GitHub repository.
4. Підключіться до EC2 через SSH.
5. Склонуйте репозиторій:

```bash
sudo git clone https://github.com/YOUR_USERNAME/YOUR_REPOSITORY.git /opt/steam-price-bot
sudo chown -R ubuntu:ubuntu /opt/steam-price-bot
```

6. Створіть `.env`:

```bash
cd /opt/steam-price-bot
cp .env.example .env
nano .env
```

7. Встановіть `systemd`-сервіс:

```bash
sudo cp deploy/steam-price-bot.service /etc/systemd/system/steam-price-bot.service
sudo systemctl daemon-reload
sudo systemctl enable steam-price-bot
sudo systemctl restart steam-price-bot
```

8. Перевірте статус:

```bash
sudo systemctl status steam-price-bot
```

Логи:

```bash
journalctl -u steam-price-bot -f
```