# api.py — автономный API-сервер мини-приложения (второй бот)
import os
import json
import hashlib
import hmac
import urllib.parse
import asyncio
import random
from aiohttp import web
import aiohttp_cors
from datetime import datetime, timedelta
import time

import psycopg2
from psycopg2 import pool

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (
    Message, LabeledPrice, PreCheckoutQuery,
)

from database import (
    get_user, get_balance, get_user_level, claim_daily, get_last_daily,
    add_balance, is_vip_active, init_db, treasury_ensure, get_user_rank,
    get_spouse, get_reputation, get_daily_streak, get_top, give_vip_days,
    buy_vip, log_transaction, is_lucky_active, increment_stat, BOT_TREASURY_ID,
    get_case_cooldown, set_case_cooldown, get_couple_info, wedding_case_get_cooldown,
    wedding_case_set_cooldown, bank_get, is_hide_balance_active, get_nick,
    ensure_user, log_payment, has_charge_id, get_next_payment_number,
    save_payment_code,
)

# =====================================
#  ПОДКЛЮЧЕНИЕ К POSTGRESQL (общая база с первым ботом)
# =====================================

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://USER:PASSWORD@HOST:5432/DBNAME"   # ← вставь ту же ссылку от bothost
)

db_pool = pool.ThreadedConnectionPool(minconn=1, maxconn=10, dsn=DATABASE_URL)

def pg():
    """Соединение из пула с autocommit."""
    class _Ctx:
        def __enter__(self):
            self.conn = db_pool.getconn()
            self.conn.autocommit = True
            return self.conn
        def __exit__(self, *exc):
            db_pool.putconn(self.conn)
            return False
    return _Ctx()

# =====================================
#  СВОЙ БОТ (второй токен)
# =====================================

BOT_TOKEN = os.getenv("API_BOT_TOKEN", "ВСТАВЬ_СЮДА_ТОКЕН_ВТОРОГО_БОТА")
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

MINI_APP_URL = "https://bot-1793434826-8925-ayzen.bothost.tech"

# =====================================
#  ИГРОВАЯ ЛОГИКА (точная копия из air.py)
# =====================================

CASES = {
    "case": {
        "name": "🎁 Обычный кейс",
        "price": 950,
        "cooldown_normal": 10 * 60,
        "cooldown_vip": 5 * 60,
        "frame_delay": 0.55,
        "msg_range": (800, 1600),
        "tiers": [
            (700, 900, 57.0),
            (900, 1150, 28.0),
            (1150, 1600, 10.0),
        ],
        "vip_chance": 5.0,
        "vip_days": 3,
    },
    "rarecase": {
        "name": "💎 Редкий кейс",
        "price": 4500,
        "cooldown_normal": 30 * 60,
        "cooldown_vip": 15 * 60,
        "frame_delay": 0.55,
        "msg_range": (4300, 8000),
        "tiers": [
            (4300, 5000, 54.0),
            (5000, 6000, 27.0),
            (6000, 8000, 9.0),
        ],
        "vip_chance": 10.0,
        "vip_days": 7,
    },
    "lcase": {
        "name": "👑 Легендарный кейс",
        "price": 24500,
        "cooldown_normal": 60 * 60,
        "cooldown_vip": 30 * 60,
        "frame_delay": 0.6,
        "msg_range": (24000, 31000),
        "tiers": [
            (24000, 25000, 51.0),
            (25000, 27500, 25.5),
            (27500, 31000, 8.5),
        ],
        "vip_chance": 15.0,
        "vip_days": 30,
    },
    "petcase": {
        "name": "🐾 Обычный кейс питомца",
        "price": 4000,
        "cooldown_normal": 0,
        "cooldown_vip": 0,
        "frame_delay": 0.6,
        "msg_range": (0, 0),
        "tiers": [],
        "vip_chance": 0,
        "vip_days": 0,
        "is_pet_case": True,
        "pet_pool": "common",
    },
    "petrarecase": {
        "name": "🐾 Редкий кейс питомца",
        "price": 7000,
        "cooldown_normal": 0,
        "cooldown_vip": 0,
        "frame_delay": 0.6,
        "msg_range": (0, 0),
        "tiers": [],
        "vip_chance": 0,
        "vip_days": 0,
        "is_pet_case": True,
        "pet_pool": "rare",
        "vip_only": True,
    },
    "weddingcase": {
        "name": "💍 Свадебный кейс",
        "price": 0,
        "cooldown_normal": 0,
        "cooldown_vip": 0,
        "frame_delay": 0.6,
        "msg_range": (0, 0),
        "tiers": [],
        "vip_chance": 0,
        "vip_days": 0,
        "is_wedding_case": True,
    },
}

def _roll_case_item(case_data: dict) -> tuple:
    """Возвращает (kind, msg_amount, vip_days)."""
    roll = random.random() * 100

    if roll < case_data["vip_chance"]:
        return ("vip", 0, case_data["vip_days"])

    tier_roll = random.random() * 100
    acc = 0.0
    chosen = case_data["tiers"][0]

    for tier in case_data["tiers"]:
        acc += tier[2]
        if tier_roll < acc:
            chosen = tier
            break

    min_msg, max_msg, _chance = chosen
    msg_amount = random.randint(min_msg, max_msg)

    return ("msg", msg_amount, 0)

def _roll_wedding_prize() -> dict:
    """
    Возвращает приз: {"kind": "msg"|"vip", "amount": int, "vip_days": int, "phrase": str}
    Шансы: 40% msg 1000-5000, 20% msg 10000-20000, 10% msg 20000-50000,
           20% VIP 14 дней, 10% VIP 28 дней.
    """
    roll = random.random() * 100

    if roll < 40:
        amount = random.randint(1000, 5000)
        return {
            "kind": "msg",
            "amount": amount,
            "vip_days": 0,
            "phrase": random.choice([
                "💕 Нежный подарок для пары!",
                "💐 Немного романтики!",
                "🌹 Приятный сюрприз!",
            ]),
        }
    elif roll < 60:
        amount = random.randint(10_000, 20_000)
        return {
            "kind": "msg",
            "amount": amount,
            "vip_days": 0,
            "phrase": random.choice([
                "💰 Щедрый подарок!",
                "✨ Отличный улов!",
                "💎 Блестяще!",
            ]),
        }
    elif roll < 70:
        amount = random.randint(20_000, 50_000)
        return {
            "kind": "msg",
            "amount": amount,
            "vip_days": 0,
            "phrase": random.choice([
                "🎉 ДЖЕКПОТ! Огромный подарок!",
                "🔥 Вот это удача!",
                "💎 Бриллиантовая свадьба!",
            ]),
        }
    elif roll < 90:
        return {
            "kind": "vip",
            "amount": 0,
            "vip_days": 14,
            "phrase": "👑 VIP на 14 дней каждому!",
        }
    else:
        return {
            "kind": "vip",
            "amount": 0,
            "vip_days": 28,
            "phrase": "👑 VIP на 28 дней каждому!",
        }

BUY_PACKAGES = [
    (1_000,      1,    0),
    (5_000,      5,    0),
    (10_000,     10,   0),
    (52_500,     50,   5),
    (110_000,    100,  10),
    (230_000,    200,  15),
    (600_000,    500,  20),
    (1_000_000,  1000, 0),
]

def get_package_by_stars(stars: int):
    """Возвращает (msg_amount, bonus) для пакета или None."""
    for msg, s, bonus in BUY_PACKAGES:
        if s == stars:
            return msg, bonus
    return None

# Для эндпоинтов магазина (совместимость со старым именем)
LOCAL_CASES = {
    k: {"name": v["name"], "price": v["price"]} for k, v in CASES.items()
}

def validate_init_data(init_data: str) -> dict:
    try:
        parsed_data = dict(urllib.parse.parse_qsl(init_data))
        hash_str = parsed_data.pop('hash', None)
        if not hash_str:
            return None
        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed_data.items()))
        secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        if calculated_hash == hash_str:
            return json.loads(parsed_data.get('user', '{}'))
        return None
    except Exception as e:
        print(f"[API] Ошибка валидации: {e}")
        return None

_avatar_cache = {}
FALLBACK_PHOTO_ID = "AgACAgIAAxkBAAILu2rNjFR6CE9NdRrgp27N9BwmlO-9AALkHWsbjhRxSq6BR2Q4O82xAQADAgADeAADPQQ"

async def get_avatar_url(user_id):
    now = time.time()
    if user_id in _avatar_cache:
        url, timestamp = _avatar_cache[user_id]
        if now - timestamp < 3600:
            return url
    try:
        photos = await bot.get_user_profile_photos(user_id, limit=1)
        if photos.total_count > 0:
            file_id = photos.photos[0][-1].file_id
            file_info = await bot.get_file(file_id)
            url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_info.file_path}"
            _avatar_cache[user_id] = (url, now)
            return url
    except Exception as e:
        print(f"[AVATAR] Ошибка загрузки для {user_id}: {e}")
    return f"{MINI_APP_URL}/api/fallback_avatar"

async def handle_fallback_avatar(request):
    try:
        file_info = await bot.get_file(FALLBACK_PHOTO_ID)
        file_bytes = await bot.download_file(file_info.file_path)
        return web.Response(body=file_bytes.read(), content_type="image/jpeg")
    except Exception as e:
        return web.Response(text="Error", status=500)

BG_FILE_IDS = {
    "dark": "AgACAgIAAxkBAAIUwWrutMARochEk1v3LVefICAcn3JWAAK8HGsbRSh5S2CNt_PnNevyAQADAgADeAADPQQ",
    "light": "AgACAgIAAxkBAAIUwGrutL08nNtSXJmtqjquXrqLfh2VAAK7HGsbRSh5S1mpQT5uWs01AQADAgADeAADPQQ"
}
NEWYEAR_BG_ID = "AgACAgIAAxkBAAIV-Gr0tenb4xiZnyKsC8T6xoR6c83iAALSGmsbAAF5qEuJ8z488RisfgEAAwIAA3kAAz0E"

async def handle_get_bg(request):
    theme = request.query.get("theme", "dark")
    file_id = BG_FILE_IDS.get(theme, BG_FILE_IDS["dark"])
    try:
        file_info = await bot.get_file(file_id)
        file_bytes = await bot.download_file(file_info.file_path)
        return web.Response(body=file_bytes.read(), content_type="image/jpeg")
    except Exception as e:
        return web.Response(text="Error loading bg", status=500)

async def handle_get_newyear_bg(request):
    try:
        file_info = await bot.get_file(NEWYEAR_BG_ID)
        file_bytes = await bot.download_file(file_info.file_path)
        return web.Response(body=file_bytes.read(), content_type="image/jpeg")
    except Exception as e:
        return web.Response(text="Error loading bg", status=500)

async def handle_get_shop_items(request):
    try:
        vip_packages = [
            {"id": 1, "weeks": 1, "price": 4000, "title": "1 неделя", "desc": "4 000 msg"},
            {"id": 2, "weeks": 2, "price": 8000, "title": "2 недели", "desc": "8 000 msg"},
            {"id": 3, "weeks": 3, "price": 12000, "title": "3 недели", "desc": "12 000 msg"},
        ]

        cases_list = []
        for key, data in LOCAL_CASES.items():
            if key == "weddingcase" or key == "petcase" or key == "petrarecase":
                continue
            cases_list.append({
                "id": key,
                "name": data["name"],
                "price": data["price"],
            })

        return web.json_response({"vip": vip_packages, "cases": cases_list})
    except Exception as e:
        print(f"[API] КРИТИЧЕСКАЯ ОШИБКА shop_items: {e}")
        return web.json_response({"error": "Internal error"}, status=500)

async def handle_get_profile(request):
    init_data = request.headers.get('X-Telegram-Init-Data')
    if not init_data: return web.json_response({"error": "No init data"}, status=401)
    user_data = validate_init_data(init_data)
    if not user_data: return web.json_response({"error": "Invalid signature"}, status=403)
    user_id = user_data['id']
    user_row = get_user(user_id)
    if not user_row: return web.json_response({"error": "User not found"}, status=404)
    balance = get_balance(user_id)
    lvl = get_user_level(user_id)
    vip = is_vip_active(user_id)
    rank = get_user_rank(user_id)
    rep = get_reputation(user_id)
    streak = get_daily_streak(user_id)
    bank_balance = bank_get(user_id)['balance']
    vip_days = None
    if user_row[4]:
        try:
            until = datetime.fromisoformat(user_row[4])
            if until > datetime.now(): vip_days = (until - datetime.now()).days
        except ValueError: pass
    spouse_id = get_spouse(user_id)
    spouse_name = None
    if spouse_id:
        spouse_data = get_user(spouse_id)
        if spouse_data: spouse_name = spouse_data[2] or f"@{spouse_data[1]}"
    last_daily = get_last_daily(user_id)
    daily_available = True
    hours_left = 0
    if last_daily:
        try:
            last_dt = datetime.fromisoformat(last_daily)
            elapsed = datetime.now() - last_dt
            if elapsed < timedelta(hours=24):
                daily_available = False
                hours_left = 24 - int(elapsed.total_seconds() // 3600)
        except ValueError: pass
    return web.json_response({
        "user_id": user_id, "custom_id": user_row[6] or 0, "registered_at": user_row[7] or "",
        "name": user_data.get('first_name', 'Игрок'), "avatar_url": await get_avatar_url(user_id),
        "balance": balance, "bank_balance": bank_balance, "level": lvl['level'], "level_title": lvl['title'],
        "vip": vip, "vip_days": vip_days, "rank": rank, "rep": rep, "streak": streak,
        "spouse": spouse_name, "daily_available": daily_available, "daily_hours_left": hours_left
    })

async def handle_spin_wheel(request):
    init_data = request.headers.get('X-Telegram-Init-Data')
    user_data = validate_init_data(init_data) if init_data else None
    if not user_data: return web.json_response({"error": "Auth error"}, status=403)
    user_id = user_data['id']
    last_daily = get_last_daily(user_id)
    if last_daily:
        try:
            last_dt = datetime.fromisoformat(last_daily)
            if datetime.now() - last_dt < timedelta(hours=24):
                return web.json_response({"error": "Колесо уже крутили сегодня"}, status=400)
        except ValueError: pass
    prizes = [
        {"type": "msg", "value": 500, "chance": 30, "label": "500 msg", "emoji": "💰"},
        {"type": "msg", "value": 1000, "chance": 25, "label": "1000 msg", "emoji": "💰"},
        {"type": "msg", "value": 2500, "chance": 15, "label": "2500 msg", "emoji": "💰"},
        {"type": "msg", "value": 5000, "chance": 5, "label": "5000 msg", "emoji": "💰"},
        {"type": "vip", "value": 1, "chance": 15, "label": "VIP 1 день", "emoji": "👑"},
        {"type": "vip", "value": 3, "chance": 5, "label": "VIP 3 дня", "emoji": "👑"},
        {"type": "empty", "value": 0, "chance": 5, "label": "Пусто", "emoji": "💨"}
    ]
    roll = random.uniform(0, 100)
    acc = 0
    chosen = prizes[-1]
    for p in prizes:
        acc += p["chance"]
        if roll <= acc:
            chosen = p
            break
    if chosen["type"] == "msg":
        add_balance(user_id, chosen["value"])
        log_transaction(user_id, user_id, chosen["value"], "daily_wheel")
        msg = f"💰 Выигрыш: +{chosen['value']:,} msg"
    elif chosen["type"] == "vip":
        give_vip_days(user_id, chosen["value"])
        msg = f"👑 Выигрыш: VIP на {chosen['value']} дн."
    else:
        msg = "💨 Увы, сегодня пусто."
    claim_daily(user_id, 0)
    return web.json_response({"success": True, "prize": chosen, "msg": msg, "new_balance": get_balance(user_id)})

async def handle_get_top(request):
    try:
        init_data = request.headers.get('X-Telegram-Init-Data')
        if not validate_init_data(init_data): return web.json_response({"error": "Auth error"}, status=403)
        with pg() as conn:
            cur = conn.cursor()
            cur.execute("SELECT user1_id, user2_id, balance FROM marriages")
            marriages = cur.fetchall()
            married_users = set()
            marriage_by_user = {}
            for u1, u2, balance in marriages:
                married_users.add(u1); married_users.add(u2)
                marriage_by_user[u1] = (u2, balance, u1, u2)
                marriage_by_user[u2] = (u1, balance, u1, u2)
            cur.execute("""
                SELECT user_id, msg_count FROM users
                WHERE user_id NOT IN (SELECT user1_id FROM marriages UNION SELECT user2_id FROM marriages) AND user_id != %s
            """, (BOT_TREASURY_ID,))
            singles = cur.fetchall()
        items = []
        seen_pairs = set()
        def fmt_name(d):
            if not d: return "Игрок", 0
            custom_id = d[6] if len(d) > 6 else d[0]
            try: nick, _ = get_nick(d[0])
            except: nick = None
            name = nick or d[2] or (f"@{d[1]}" if d[1] else "Игрок")
            return name, custom_id
        for uid in married_users:
            try:
                spouse_id, balance, u1, u2 = marriage_by_user[uid]
                pair_key = tuple(sorted([u1, u2]))
                if pair_key in seen_pairs: continue
                seen_pairs.add(pair_key)
                if BOT_TREASURY_ID in (u1, u2): continue
                data1 = get_user(u1); data2 = get_user(u2)
                if not data1 or not data2: continue
                name1, cid1 = fmt_name(data1)
                name2, _ = fmt_name(data2)
                display = f"{name1} 💍 {name2} (ID: {cid1})"
                hide1 = is_hide_balance_active(u1); hide2 = is_hide_balance_active(u2)
                balance_text = str(balance) if not (hide1 or hide2) else "▒▒▒▒▒"
                items.append({"balance": balance, "display": display, "display_balance": balance_text, "uid_for_avatar": u1, "custom_id": cid1, "user_id": u1, "vip": is_vip_active(u1) or is_vip_active(u2)})
            except: continue
        for uid, balance in singles:
            try:
                data = get_user(uid)
                if not data: continue
                name, cid = fmt_name(data)
                hide = is_hide_balance_active(uid)
                balance_text = str(balance) if not hide else "▒▒▒▒▒"
                items.append({"balance": balance, "display": f"{name} (ID: {cid})", "display_balance": balance_text, "uid_for_avatar": uid, "custom_id": cid, "user_id": uid, "vip": is_vip_active(uid)})
            except: continue
        items.sort(key=lambda x: x["balance"], reverse=True)
        top = items[:10]
        result = []
        for i, item in enumerate(top, 1):
            try: av_url = await get_avatar_url(item["uid_for_avatar"])
            except: av_url = None
            result.append({"place": i, "name": item["display"], "balance": int(item["balance"]), "avatar": av_url or "", "custom_id": item["custom_id"], "user_id": item["user_id"], "vip": item["vip"]})
        return web.json_response(result)
    except Exception as e:
        import traceback
        print(f"[TOP] КРИТИЧЕСКАЯ ОШИБКА: {e}\n{traceback.format_exc()}")
        return web.json_response([])

async def handle_open_case(request):
    init_data = request.headers.get('X-Telegram-Init-Data')
    user_data = validate_init_data(init_data) if init_data else None
    if not user_data: return web.json_response({"error": "Auth error"}, status=403)
    user_id = user_data['id']
    try:
        data = await request.json()
        case_key = data.get('case_key')
    except: return web.json_response({"error": "Invalid data"}, status=400)

    if case_key not in CASES: return web.json_response({"error": "Кейс не найден"}, status=404)
    case_data = CASES[case_key]
    price = case_data["price"]
    is_wedding_case = case_data.get("is_wedding_case", False)
    is_vip = is_vip_active(user_id)
    if case_data.get("vip_only") and not is_vip: return web.json_response({"error": "👑 Только для VIP!"}, status=403)
    if case_data.get("is_pet_case"): return web.json_response({"error": "Кейсы питомцев доступны только в боте"}, status=400)
    if is_wedding_case:
        couple_info = get_couple_info(user_id)
        if not couple_info.get("ok"): return web.json_response({"error": "❌ Только для пар!"}, status=403)
        last_case = wedding_case_get_cooldown(user_id)
        if last_case:
            try:
                last_dt = datetime.fromisoformat(last_case)
                if datetime.now() - last_dt < timedelta(hours=24): return web.json_response({"error": "⏳ Свадебный кейс на перезарядке"}, status=400)
            except: pass
        prize = _roll_wedding_prize()
        if prize["kind"] == "msg":
            add_balance(couple_info["user1_id"], prize["amount"])
            log_transaction(BOT_TREASURY_ID, couple_info["user1_id"], prize["amount"], "wedding_case_msg")
            res_text = f"💰 Пара получила: +{prize['amount']:,} msg"
        else:
            give_vip_days(couple_info["user1_id"], prize["vip_days"])
            give_vip_days(couple_info["user2_id"], prize["vip_days"])
            res_text = f"👑 VIP на {prize['vip_days']} дней каждому!"
        wedding_case_set_cooldown(user_id)
        return web.json_response({"success": True, "result_text": f"{prize['phrase']}\n{res_text}", "new_balance": get_balance(user_id)})
    last_open = get_case_cooldown(user_id, case_key)
    if last_open:
        try:
            last_dt = datetime.fromisoformat(last_open)
            cd_sec = case_data["cooldown_vip"] if is_vip else case_data["cooldown_normal"]
            if datetime.now() - last_dt < timedelta(seconds=cd_sec): return web.json_response({"error": "⏳ Кейс на перезарядке"}, status=400)
        except: pass
    balance = get_balance(user_id)
    if balance < price: return web.json_response({"error": f"😢 Недостаточно msg! Нужно: {price}"}, status=400)
    add_balance(user_id, -price)
    log_transaction(user_id, user_id, -price, "case")
    set_case_cooldown(user_id, case_key)
    kind, msg_amount, vip_days = _roll_case_item(case_data)
    if kind == "vip":
        give_vip_days(user_id, vip_days)
        result_text = f"👑 Выигрыш: VIP на {vip_days} дней"
    else:
        add_balance(user_id, msg_amount)
        profit = msg_amount - price
        result_text = f"💰 Выигрыш: +{msg_amount:,} msg\n└─ Профит: {profit:+,} msg"
    return web.json_response({"success": True, "result_text": result_text, "new_balance": get_balance(user_id)})

async def handle_casino(request):
    init_data = request.headers.get('X-Telegram-Init-Data')
    user_data = validate_init_data(init_data) if init_data else None
    if not user_data: return web.json_response({"error": "Auth error"}, status=403)
    user_id = user_data['id']
    try:
        data = await request.json()
        bet = int(data.get('bet', 1000))
    except: return web.json_response({"error": "Invalid data"}, status=400)
    if bet < 1000: return web.json_response({"error": "Минимальная ставка 1000 msg"}, status=400)
    balance = get_balance(user_id)
    if balance < bet: return web.json_response({"error": "😢 Недостаточно msg!"}, status=400)
    lucky = is_lucky_active(user_id)
    add_balance(user_id, -bet)
    win_chance = 0.59 if lucky else 0.44
    roll = random.random()
    CASINO_SLOTS = ["🍒", "🍋", "🍊", "⭐", "💎", "7️⃣"]
    if roll < 0.03:
        symbol = random.choice(CASINO_SLOTS); result = [symbol, symbol, symbol]
    elif roll < win_chance:
        symbol = random.choice(CASINO_SLOTS); other = random.choice([s for s in CASINO_SLOTS if s != symbol]); positions = random.sample([0, 1, 2], 2); result = [other, other, other]; result[positions[0]] = symbol; result[positions[1]] = symbol
    else: result = random.sample(CASINO_SLOTS, 3)
    a, b, c = result
    mult = 0; kind = "casino_lose"
    if a == b == c:
        multipliers = {"🍒": 4, "🍋": 5, "🍊": 6, "⭐": 8, "💎": 12, "7️⃣": 25}
        mult = multipliers[a]; kind = "casino_jackpot" if a == "7️⃣" else "casino_win"
    elif a == b or b == c or a == c:
        mult = 1.5; kind = "casino_win"
    win_amount = int(bet * mult) if mult > 0 else 0
    if win_amount > 0: add_balance(user_id, win_amount)
    log_transaction(user_id, user_id, win_amount - bet, kind)
    increment_stat(user_id, "total_casino_games", 1)
    if win_amount == 0: increment_stat(user_id, "total_casino_losses", bet)
    return web.json_response({"success": True, "result": result, "win_amount": win_amount, "mult": mult, "new_balance": get_balance(user_id), "is_jackpot": a == b == c and a == "7️⃣"})

async def handle_buy_vip(request):
    init_data = request.headers.get('X-Telegram-Init-Data')
    user_data = validate_init_data(init_data) if init_data else None
    if not user_data: return web.json_response({"error": "Auth error"}, status=403)
    user_id = user_data['id']
    try:
        data = await request.json()
        weeks = int(data.get('weeks', 0))
    except: return web.json_response({"error": "Invalid data"}, status=400)
    if weeks <= 0: return web.json_response({"error": "Неверное количество недель"}, status=400)
    ok, info = buy_vip(user_id, weeks)
    if not ok: return web.json_response({"error": info}, status=400)
    return web.json_response({"success": True, "new_balance": get_balance(user_id)})

async def handle_buy_vip_stars(request):
    init_data = request.headers.get('X-Telegram-Init-Data')
    user_data = validate_init_data(init_data) if init_data else None
    if not user_data: return web.json_response({"error": "Auth error"}, status=403)
    user_id = user_data['id']
    try:
        invoice_link = await bot.create_invoice_link(
            title="VIP на 7 дней",
            description="Покупка VIP-статуса на 7 дней",
            payload=f"buyvip_{user_id}_7",
            provider_token="",
            currency="XTR",
            prices=[LabeledPrice(label="VIP", amount=10)]
        )
        return web.json_response({"success": True, "link": invoice_link})
    except Exception as e:
        return web.json_response({"error": "Ошибка создания счета"}, status=500)

async def handle_buy_stars_link(request):
    init_data = request.headers.get('X-Telegram-Init-Data')
    user_data = validate_init_data(init_data) if init_data else None
    if not user_data: return web.json_response({"error": "Auth error"}, status=403)
    user_id = user_data['id']
    try:
        data = await request.json()
        stars = int(data.get('stars'))
    except: return web.json_response({"error": "Invalid data"}, status=400)

    package = get_package_by_stars(stars)
    if not package: return web.json_response({"error": "Пакет не найден"}, status=404)
    msg_amount = package[0]
    try:
        invoice_link = await bot.create_invoice_link(
            title=f"{msg_amount:,} msg",
            description=f"Покупка {msg_amount:,} msg за {stars} ⭐",
            payload=f"buymsg_{user_id}_{stars}",
            provider_token="",
            currency="XTR",
            prices=[LabeledPrice(label="msg", amount=stars)]
        )
        return web.json_response({"success": True, "link": invoice_link})
    except Exception as e:
        return web.json_response({"error": "Ошибка создания счета"}, status=500)

# =====================================
#  ПЛАТЕЖИ (TELEGRAM STARS) — свой токен, свои обработчики
# =====================================

@dp.pre_checkout_query()
async def pre_checkout_handler(pre_checkout_query: PreCheckoutQuery):
    """Подтверждаем платёж (10 секунд на ответ)."""
    try:
        await pre_checkout_query.answer(ok=True)
        print(f"[PAYMENT] Pre-checkout OK: user={pre_checkout_query.from_user.id}")
    except Exception as e:
        print(f"[PAYMENT] Ошибка pre-checkout: {e}")
        try:
            await pre_checkout_query.answer(
                ok=False,
                error_message="Ошибка обработки, попробуйте позже"
            )
        except Exception:
            pass

@dp.message(F.successful_payment)
async def successful_payment_handler(message: Message):
    """Обрабатываем успешный платёж."""
    user = message.from_user
    payment = message.successful_payment

    print(f"[PAYMENT] ✅ Успех: user={user.id}, "
          f"total={payment.total_amount}, "
          f"charge={payment.telegram_payment_charge_id}, "
          f"payload={payment.invoice_payload}")

    # 🔒 Идемпотентность — не начисляем второй раз
    if has_charge_id(payment.telegram_payment_charge_id):
        await message.answer(
            "⚠️ Этот платёж уже был обработан",
            parse_mode="HTML"
        )
        return

    # 🔍 Парсим payload
    try:
        parts = payment.invoice_payload.split("_")
        if len(parts) != 3:
            raise ValueError("Неверный формат payload")

        action = parts[0]
        payload_user_id = int(parts[1])
        value = int(parts[2])

        if payload_user_id != user.id:
            raise ValueError("Payload не совпадает с пользователем")

    except (ValueError, IndexError) as e:
        print(f"[PAYMENT] ❌ Ошибка payload: {e}")
        await message.answer(
            "❌ <b>Ошибка валидации платежа</b>\n"
            "Обратитесь в поддержку: /paysupport",
            parse_mode="HTML"
        )
        return

    ensure_user(user)

    # ═══════════════════════════════════════════
    #  ВЕТКА 1: покупка msg
    # ═══════════════════════════════════════════
    if action == "buymsg":
        if value != payment.total_amount:
            print(f"[PAYMENT] ❌ Сумма не совпадает: {value} != {payment.total_amount}")
            await message.answer("❌ Ошибка: сумма не совпадает")
            return

        package = get_package_by_stars(value)
        if package is None:
            await message.answer(f"❌ Пакет {value}⭐ не найден")
            return

        msg_amount, bonus = package

        new_balance = add_balance(user.id, msg_amount)
        log_payment(
            user.id,
            payment.total_amount,
            msg_amount,
            payment.telegram_payment_charge_id,
            payment.provider_payment_charge_id or ""
        )
        log_transaction(user.id, user.id, msg_amount, "stars_buy")

        payment_number = get_next_payment_number()
        payment_code = f"#paymsg{payment_number}"
        save_payment_code(payment.telegram_payment_charge_id, payment_code)

        bonus_line = f"\n└─ 🎁 Бонус: <b>+{bonus}%</b>" if bonus > 0 else ""

        await message.answer(
            f"✅ <b>Платёж получен!</b>\n\n"
            f"└─ ⭐ Оплачено: <b>{payment.total_amount}</b> Stars\n"
            f"└─ 🧾 Код платежа: <code>{payment_code}</code>"
            f"{bonus_line}\n"
            f"└─ 💰 Начислено: <b>+{msg_amount:,}</b> msg\n"
            f"└─ Баланс: {new_balance:,} msg\n\n"
            f"💡 Спасибо за поддержку!",
            parse_mode="HTML"
        )
        return

    # ═══════════════════════════════════════════
    #  ВЕТКА 2: покупка VIP
    # ═══════════════════════════════════════════
    if action == "buyvip":
        days = value

        if days <= 0 or days > 365:
            await message.answer("❌ Неверное количество дней VIP")
            return

        result = give_vip_days(user.id, days)
        if result is None:
            await message.answer("❌ Ошибка выдачи VIP, обратитесь в поддержку")
            return

        until_dt = datetime.fromisoformat(result)

        log_payment(
            user.id,
            payment.total_amount,
            0,   # msg не начисляем
            payment.telegram_payment_charge_id,
            payment.provider_payment_charge_id or ""
        )
        log_transaction(user.id, user.id, 0, "stars_buy_vip")

        await message.answer(
            f"✅ <b>VIP активирован!</b>\n\n"
            f"└─ ⭐ Оплачено: <b>{payment.total_amount}</b> Stars\n"
            f"└─ 👑 VIP на <b>{days} дней</b>\n"
            f"└─ 📅 До: <b>{until_dt.strftime('%d.%m.%Y %H:%M')}</b>\n\n"
            f"💡 Спасибо за поддержку!",
            parse_mode="HTML"
        )
        return

    # ═══════════════════════════════════════════
    #  НЕИЗВЕСТНЫЙ action
    # ═══════════════════════════════════════════
    print(f"[PAYMENT] ⚠️ Неизвестный action: {action}")
    await message.answer(
        f"⚠️ <b>Платёж получен, но тип не распознан</b>\n"
        f"└─ Action: <code>{action}</code>\n"
        f"└─ Обратитесь в поддержку: /paysupport",
        parse_mode="HTML"
    )

@dp.message(Command("paysupport"))
async def cmd_paysupport(message: Message):
    await message.answer(
        "💬 <b>Поддержка по платежам</b>\n\n"
        "Если платёж прошёл, но msg не начислились — напишите:\n"
        "└─ t.me/Kaoruko_off\n\n"
        "При обращении укажите:\n"
        "└─ ID платежа (из чека Telegram)\n"
        "└─ Дату и сумму",
        parse_mode="HTML"
    )

async def index_handler(request):
    file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'index.html')
    if not os.path.exists(file_path): return web.Response(text=f"Файл не найден по пути: {file_path}", status=404)
    return web.FileResponse(file_path)

async def start_bot():
    print("[API] Запуск бота (polling)...")
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    except Exception as e:
        print(f"[API] ❌ ОШИБКА БОТА: {e}")

async def on_startup(app):
    print("[API] Инициализация БД...")
    init_db()
    treasury_ensure()
    asyncio.create_task(start_bot())

app = web.Application()
app.on_startup.append(on_startup)

app.router.add_get('/', index_handler)
app.router.add_get('/api/bg', handle_get_bg)
app.router.add_get('/api/newyear_bg', handle_get_newyear_bg)
app.router.add_get('/api/profile', handle_get_profile)
app.router.add_post('/api/spin_wheel', handle_spin_wheel)
app.router.add_get('/api/top', handle_get_top)
app.router.add_post('/api/open_case', handle_open_case)
app.router.add_post('/api/casino', handle_casino)
app.router.add_post('/api/buy_vip', handle_buy_vip)
app.router.add_post('/api/buy_vip_stars', handle_buy_vip_stars)
app.router.add_post('/api/buy_stars', handle_buy_stars_link)
app.router.add_get('/api/shop_items', handle_get_shop_items)
app.router.add_get('/api/fallback_avatar', handle_fallback_avatar)

cors = aiohttp_cors.setup(app, defaults={"*": aiohttp_cors.ResourceOptions(allow_credentials=True, expose_headers="*", allow_headers="*")})
for route in list(app.router.routes()): cors.add(route)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8080))
    web.run_app(app, host='0.0.0.0', port=port)
