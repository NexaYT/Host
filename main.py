# -*- coding: utf-8 -*-
"""
Script Host Pro — Telegram Hosting Bot v6.0 (Upgraded)
সব বাগ ঠিক করা হয়েছে:
  - notify_admins_approval() এখন আসল ফাইলও পাঠায়
  - Telethon loop parameter বাদ দেওয়া হয়েছে (v1.24+ compatible)
  - কালার সিস্টেম যোগ করা হয়েছে (p|s|d|e prefix)
  - send_fake_security_scan() পুনরায় লেখা হয়েছে
"""
import subprocess, sys

def _auto_install(pkg, pip_name=None):
    try:
        __import__(pkg)
    except ModuleNotFoundError:
        name = pip_name or pkg
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", name, "--break-system-packages"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

_auto_install("telebot", "pyTelegramBotAPI")
_auto_install("psutil",  "psutil")
_auto_install("flask",   "flask")
_auto_install("telethon","telethon")

import os, re, time, shutil, zipfile, sqlite3, logging, asyncio
import threading
from datetime import datetime, timedelta
from threading import Thread, Lock

import telebot
from telebot import types
import psutil
from flask import Flask, jsonify

# ═══════════════════════════════════════════════════════════════
# CONFIG — এখানে আপনার তথ্য দিন
# ═══════════════════════════════════════════════════════════════
TOKEN          = "8947601300:AAEBTLksBDp5Kyx9GpdCLfxR1QvXECKMu_k"
OWNER_ID       = 5913459788
ADMIN_USERNAME = "@lod_Shadow"
UPDATE_CHANNEL = "https://t.me/nexasms"
BUY_LINK       = "https://t.me/nexa_ad"

BASE_DIR        = os.path.abspath(os.path.dirname(__file__))
UPLOAD_BOTS_DIR = os.path.join(BASE_DIR, "upload_bots")
DATA_DIR        = os.path.join(BASE_DIR, "data")
DATABASE_PATH   = os.path.join(DATA_DIR, "hosting.db")
BOT_LOG_FILE    = os.path.join(DATA_DIR, "bot_actions.log")

os.makedirs(UPLOAD_BOTS_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(BOT_LOG_FILE, encoding="utf-8"),
    ],
)
logger = logging.getLogger("HostBot")

bot            = telebot.TeleBot(TOKEN, parse_mode=None, threaded=True)
BOT_START_TIME = datetime.now()

# ═══════════════════════════════════════════════════════════════
# IN-MEMORY STATE
# ═══════════════════════════════════════════════════════════════
bot_scripts:        dict = {}
user_subscriptions: dict = {}
user_files_db:      dict = {}
active_users:       set  = set()
banned_users:       dict = {}
admin_ids:          set  = {OWNER_ID}
bot_locked:         bool = False
maintenance_mode:   bool = False
system_settings:    dict = {
    "free_limit": 1, "cooldown_sec": 3,
    "free_storage_mb": 50, "premium_storage_mb": 500
}
DB_LOCK               = Lock()
_nav_stacks:   dict = {}
_reject_state: dict = {}
_pending_update_slot: dict = {}
_pending_zip_data:    dict = {}
payment_state:        dict = {}
session_state:        dict = {}
broadcast_state:      dict = {}
payment_add_state:    dict = {}

# ═══════════════════════════════════════════════════════════════
# DATABASE
# ═══════════════════════════════════════════════════════════════
def _db():
    conn = sqlite3.connect(DATABASE_PATH, timeout=20, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn

def init_db():
    with DB_LOCK:
        c = _db()
        c.executescript("""
        CREATE TABLE IF NOT EXISTS active_users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            joined_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS subscriptions (
            user_id INTEGER PRIMARY KEY,
            bot_limit INTEGER NOT NULL,
            expiry TEXT NOT NULL,
            granted_by INTEGER,
            granted_at TEXT
        );
        CREATE TABLE IF NOT EXISTS user_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            slot INTEGER NOT NULL,
            file_name TEXT NOT NULL,
            file_type TEXT NOT NULL DEFAULT 'py',
            script_folder TEXT NOT NULL,
            file_size_kb REAL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'pending',
            uploaded_at TEXT NOT NULL,
            approved_at TEXT,
            UNIQUE(user_id, slot)
        );
        CREATE TABLE IF NOT EXISTS pending_approvals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            file_name TEXT NOT NULL,
            file_type TEXT NOT NULL,
            file_path TEXT NOT NULL,
            slot INTEGER NOT NULL DEFAULT 0,
            file_size_kb REAL DEFAULT 0,
            submitted_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            reviewed_by INTEGER,
            reviewed_at TEXT,
            reject_reason TEXT
        );
        CREATE TABLE IF NOT EXISTS payment_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            plan_months INTEGER NOT NULL,
            amount_usd REAL NOT NULL,
            method TEXT NOT NULL,
            screenshot_id TEXT NOT NULL,
            submitted_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            approved_by INTEGER,
            approved_at TEXT,
            notes TEXT
        );
        CREATE TABLE IF NOT EXISTS payment_settings (
            method_name TEXT PRIMARY KEY,
            number_or_address TEXT,
            display_name TEXT,
            is_active INTEGER DEFAULT 0,
            updated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS plan_prices (
            months INTEGER PRIMARY KEY,
            price_usd REAL NOT NULL,
            label TEXT NOT NULL,
            is_active INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS admins (
            user_id INTEGER PRIMARY KEY,
            added_by INTEGER,
            added_at TEXT
        );
        CREATE TABLE IF NOT EXISTS banned_users_tbl (
            user_id INTEGER PRIMARY KEY,
            reason TEXT NOT NULL,
            banned_by INTEGER NOT NULL,
            banned_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS user_sessions (
            user_id INTEGER NOT NULL,
            session_name TEXT NOT NULL,
            session_type TEXT NOT NULL DEFAULT 'telethon',
            api_id TEXT,
            created_at TEXT,
            PRIMARY KEY (user_id, session_name)
        );
        CREATE TABLE IF NOT EXISTS installed_packages (
            user_id INTEGER NOT NULL,
            package_name TEXT NOT NULL,
            installed_at TEXT NOT NULL,
            PRIMARY KEY (user_id, package_name)
        );
        CREATE TABLE IF NOT EXISTS system_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS expiry_notifications (
            user_id INTEGER NOT NULL,
            notif_type TEXT NOT NULL,
            sent_at TEXT NOT NULL,
            PRIMARY KEY (user_id, notif_type)
        );
        CREATE TABLE IF NOT EXISTS user_storage_override (
            user_id INTEGER PRIMARY KEY,
            storage_mb INTEGER NOT NULL,
            set_by INTEGER,
            set_at TEXT
        );
        """)
        c.execute("INSERT OR IGNORE INTO payment_settings VALUES ('bkash',NULL,'bKash',0,NULL)")
        c.execute("INSERT OR IGNORE INTO payment_settings VALUES ('nagad',NULL,'Nagad',0,NULL)")
        c.execute("INSERT OR IGNORE INTO payment_settings VALUES ('rocket',NULL,'Rocket',0,NULL)")
        c.execute("INSERT OR IGNORE INTO payment_settings VALUES ('usdt',NULL,'USDT (TRC20)',0,NULL)")
        c.execute("INSERT OR IGNORE INTO plan_prices VALUES (1,5.00,'১ মাস',1)")
        c.execute("INSERT OR IGNORE INTO plan_prices VALUES (3,12.00,'৩ মাস',1)")
        c.execute("INSERT OR IGNORE INTO plan_prices VALUES (6,20.00,'৬ মাস',1)")
        c.execute("INSERT OR IGNORE INTO plan_prices VALUES (12,35.00,'১ বছর',1)")
        c.execute("INSERT OR IGNORE INTO system_settings VALUES ('free_limit','1')")
        c.execute("INSERT OR IGNORE INTO system_settings VALUES ('free_storage_mb','50')")
        c.execute("INSERT OR IGNORE INTO system_settings VALUES ('prem_storage_mb','500')")
        c.execute("INSERT OR IGNORE INTO system_settings VALUES ('cooldown_sec','3')")
        c.execute("INSERT OR IGNORE INTO system_settings VALUES ('bot_locked','0')")
        c.execute("INSERT OR IGNORE INTO system_settings VALUES ('maintenance','0')")
        c.execute("INSERT OR IGNORE INTO admins VALUES (?,?,?)",
                  (OWNER_ID, OWNER_ID, datetime.now().isoformat()))
        c.commit()
        c.close()
    logger.info("DB initialized.")

def load_data():
    global bot_locked, maintenance_mode
    with DB_LOCK:
        c = _db()
        for r in c.execute("SELECT user_id,bot_limit,expiry FROM subscriptions").fetchall():
            try:
                user_subscriptions[r["user_id"]] = {
                    "bot_limit": r["bot_limit"],
                    "expiry": datetime.fromisoformat(r["expiry"])
                }
            except Exception:
                pass
        for r in c.execute("SELECT user_id FROM active_users").fetchall():
            active_users.add(r["user_id"])
        for r in c.execute("SELECT user_id FROM admins").fetchall():
            admin_ids.add(r["user_id"])
        for r in c.execute("SELECT user_id,reason,banned_by FROM banned_users_tbl").fetchall():
            banned_users[r["user_id"]] = {"reason": r["reason"], "banned_by": r["banned_by"]}
        for r in c.execute(
            "SELECT user_id,slot,file_name,file_type,script_folder,file_size_kb,status "
            "FROM user_files"
        ).fetchall():
            uid = r["user_id"]
            user_files_db.setdefault(uid, []).append(dict(r))
        for r in c.execute("SELECT key,value FROM system_settings").fetchall():
            k, v = r["key"], r["value"]
            if k == "free_limit":        system_settings["free_limit"] = int(v)
            elif k == "free_storage_mb": system_settings["free_storage_mb"] = int(v)
            elif k == "prem_storage_mb": system_settings["premium_storage_mb"] = int(v)
            elif k == "cooldown_sec":    system_settings["cooldown_sec"] = int(v)
            elif k == "bot_locked":      bot_locked = v == "1"
            elif k == "maintenance":     maintenance_mode = v == "1"
        c.close()
    logger.info(
        f"Loaded: {len(active_users)} users, "
        f"{len(user_subscriptions)} subs, {len(admin_ids)} admins"
    )

# ── DB helpers ──────────────────────────────────────────────────
def db_add_user(uid, username, first_name):
    with DB_LOCK:
        c = _db()
        c.execute(
            """INSERT INTO active_users(user_id,username,first_name,joined_at)
               VALUES(?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET
               username=excluded.username, first_name=excluded.first_name""",
            (uid, username, first_name, datetime.now().isoformat())
        )
        c.commit()
        c.close()
    active_users.add(uid)

def db_save_sub(uid, bot_limit, expiry_dt, granted_by):
    with DB_LOCK:
        c = _db()
        c.execute(
            """INSERT INTO subscriptions(user_id,bot_limit,expiry,granted_by,granted_at)
               VALUES(?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET
               bot_limit=excluded.bot_limit, expiry=excluded.expiry,
               granted_by=excluded.granted_by, granted_at=excluded.granted_at""",
            (uid, bot_limit, expiry_dt.isoformat(), granted_by, datetime.now().isoformat())
        )
        c.commit()
        c.close()
    user_subscriptions[uid] = {"bot_limit": bot_limit, "expiry": expiry_dt}

def db_del_sub(uid):
    with DB_LOCK:
        c = _db()
        c.execute("DELETE FROM subscriptions WHERE user_id=?", (uid,))
        c.execute("DELETE FROM expiry_notifications WHERE user_id=?", (uid,))
        c.commit()
        c.close()
    user_subscriptions.pop(uid, None)

def db_save_file(uid, slot, fname, ftype, script_folder, file_size_kb=0, status="pending"):
    now = datetime.now().isoformat()
    with DB_LOCK:
        c = _db()
        c.execute(
            """INSERT INTO user_files
               (user_id,slot,file_name,file_type,script_folder,file_size_kb,status,uploaded_at,approved_at)
               VALUES(?,?,?,?,?,?,?,?,?)
               ON CONFLICT(user_id,slot) DO UPDATE SET
               file_name=excluded.file_name, file_type=excluded.file_type,
               script_folder=excluded.script_folder, file_size_kb=excluded.file_size_kb,
               status=excluded.status, approved_at=excluded.approved_at""",
            (uid, slot, fname, ftype, script_folder, file_size_kb, status, now, now)
        )
        c.commit()
        c.close()
    user_files_db.setdefault(uid, [])
    user_files_db[uid] = [f for f in user_files_db[uid] if f["slot"] != slot]
    user_files_db[uid].append({
        "slot": slot, "file_name": fname, "file_type": ftype,
        "script_folder": script_folder, "file_size_kb": file_size_kb, "status": status
    })

def db_update_file_status(uid, slot, status):
    with DB_LOCK:
        c = _db()
        c.execute("UPDATE user_files SET status=? WHERE user_id=? AND slot=?", (status, uid, slot))
        c.commit()
        c.close()
    for f in user_files_db.get(uid, []):
        if f["slot"] == slot:
            f["status"] = status

def db_del_file(uid, slot):
    with DB_LOCK:
        c = _db()
        c.execute("DELETE FROM user_files WHERE user_id=? AND slot=?", (uid, slot))
        c.execute("DELETE FROM pending_approvals WHERE user_id=? AND slot=?", (uid, slot))
        c.commit()
        c.close()
    user_files_db[uid] = [f for f in user_files_db.get(uid, []) if f["slot"] != slot]

def db_add_pending_approval(uid, fname, ftype, file_path, slot, size_kb):
    with DB_LOCK:
        c = _db()
        cur = c.execute(
            """INSERT INTO pending_approvals
               (user_id,file_name,file_type,file_path,slot,file_size_kb,submitted_at,status)
               VALUES(?,?,?,?,?,?,?,?)""",
            (uid, fname, ftype, file_path, slot, size_kb, datetime.now().isoformat(), "pending")
        )
        aid = cur.lastrowid
        c.commit()
        c.close()
    return aid

def db_get_pending_approvals():
    with DB_LOCK:
        c = _db()
        rows = c.execute(
            """SELECT id,user_id,file_name,file_type,file_path,slot,file_size_kb,submitted_at
               FROM pending_approvals WHERE status='pending' ORDER BY id"""
        ).fetchall()
        c.close()
    return [dict(r) for r in rows]

def db_update_approval(aid, status, reviewed_by, reason=None):
    with DB_LOCK:
        c = _db()
        c.execute(
            """UPDATE pending_approvals
               SET status=?,reviewed_by=?,reviewed_at=?,reject_reason=? WHERE id=?""",
            (status, reviewed_by, datetime.now().isoformat(), reason, aid)
        )
        row = c.execute("SELECT * FROM pending_approvals WHERE id=?", (aid,)).fetchone()
        c.commit()
        c.close()
    return dict(row) if row else None

def db_get_approval(aid):
    with DB_LOCK:
        c = _db()
        row = c.execute("SELECT * FROM pending_approvals WHERE id=?", (aid,)).fetchone()
        c.close()
    return dict(row) if row else None

def db_add_payment_request(uid, months, amount, method, screenshot_id):
    with DB_LOCK:
        c = _db()
        cur = c.execute(
            """INSERT INTO payment_requests
               (user_id,plan_months,amount_usd,method,screenshot_id,submitted_at,status)
               VALUES(?,?,?,?,?,?,?)""",
            (uid, months, amount, method, screenshot_id, datetime.now().isoformat(), "pending")
        )
        pid = cur.lastrowid
        c.commit()
        c.close()
    return pid

def db_get_pending_payments():
    with DB_LOCK:
        c = _db()
        rows = c.execute(
            """SELECT id,user_id,plan_months,amount_usd,method,screenshot_id,submitted_at
               FROM payment_requests WHERE status='pending' ORDER BY id"""
        ).fetchall()
        c.close()
    return [dict(r) for r in rows]

def db_get_payment(pid):
    with DB_LOCK:
        c = _db()
        row = c.execute("SELECT * FROM payment_requests WHERE id=?", (pid,)).fetchone()
        c.close()
    return dict(row) if row else None

def db_update_payment(pid, status, approved_by):
    with DB_LOCK:
        c = _db()
        c.execute(
            "UPDATE payment_requests SET status=?,approved_by=?,approved_at=? WHERE id=?",
            (status, approved_by, datetime.now().isoformat(), pid)
        )
        c.commit()
        c.close()

def db_get_payment_settings():
    with DB_LOCK:
        c = _db()
        rows = c.execute(
            "SELECT method_name,number_or_address,display_name,is_active FROM payment_settings"
        ).fetchall()
        c.close()
    return [dict(r) for r in rows]

def db_set_payment_method(method, number):
    with DB_LOCK:
        c = _db()
        c.execute(
            "UPDATE payment_settings SET number_or_address=?,is_active=1,updated_at=? WHERE method_name=?",
            (number, datetime.now().isoformat(), method)
        )
        c.commit()
        c.close()

def db_get_plan_prices():
    with DB_LOCK:
        c = _db()
        rows = c.execute(
            "SELECT months,price_usd,label FROM plan_prices WHERE is_active=1 ORDER BY months"
        ).fetchall()
        c.close()
    return [dict(r) for r in rows]

def db_set_plan_price(months, price):
    with DB_LOCK:
        c = _db()
        c.execute("UPDATE plan_prices SET price_usd=? WHERE months=?", (price, months))
        c.commit()
        c.close()

def db_get_sessions(uid):
    with DB_LOCK:
        c = _db()
        rows = c.execute(
            "SELECT session_name,session_type,api_id,created_at FROM user_sessions WHERE user_id=?",
            (uid,)
        ).fetchall()
        c.close()
    return [dict(r) for r in rows]

def db_add_session(uid, name, stype, api_id):
    with DB_LOCK:
        c = _db()
        c.execute(
            "INSERT OR REPLACE INTO user_sessions"
            "(user_id,session_name,session_type,api_id,created_at) VALUES(?,?,?,?,?)",
            (uid, name, stype, str(api_id), datetime.now().isoformat())
        )
        c.commit()
        c.close()

def db_del_session(uid, name):
    with DB_LOCK:
        c = _db()
        c.execute(
            "DELETE FROM user_sessions WHERE user_id=? AND session_name=?", (uid, name)
        )
        c.commit()
        c.close()

def db_set_user_storage(uid, mb, set_by):
    with DB_LOCK:
        c = _db()
        c.execute(
            "INSERT OR REPLACE INTO user_storage_override(user_id,storage_mb,set_by,set_at) VALUES(?,?,?,?)",
            (uid, mb, set_by, datetime.now().isoformat())
        )
        c.commit()
        c.close()

def db_get_user_storage_override(uid):
    with DB_LOCK:
        c = _db()
        row = c.execute(
            "SELECT storage_mb FROM user_storage_override WHERE user_id=?", (uid,)
        ).fetchone()
        c.close()
    return row["storage_mb"] if row else None

def db_del_user_storage_override(uid):
    with DB_LOCK:
        c = _db()
        c.execute("DELETE FROM user_storage_override WHERE user_id=?", (uid,))
        c.commit()
        c.close()

def db_add_new_payment_method(method_name, display_name, number):
    with DB_LOCK:
        c = _db()
        c.execute(
            "INSERT OR REPLACE INTO payment_settings(method_name,number_or_address,display_name,is_active,updated_at) VALUES(?,?,?,1,?)",
            (method_name, number, display_name, datetime.now().isoformat())
        )
        c.commit()
        c.close()

def db_del_payment_method(method_name):
    with DB_LOCK:
        c = _db()
        c.execute("DELETE FROM payment_settings WHERE method_name=?", (method_name,))
        c.commit()
        c.close()

def db_add_admin(uid, added_by):
    with DB_LOCK:
        c = _db()
        c.execute(
            "INSERT OR IGNORE INTO admins(user_id,added_by,added_at) VALUES(?,?,?)",
            (uid, added_by, datetime.now().isoformat())
        )
        c.commit()
        c.close()
    admin_ids.add(uid)

def db_del_admin(uid):
    with DB_LOCK:
        c = _db()
        c.execute("DELETE FROM admins WHERE user_id=?", (uid,))
        c.commit()
        c.close()
    admin_ids.discard(uid)

def db_ban_user(uid, reason, banned_by):
    with DB_LOCK:
        c = _db()
        c.execute(
            "INSERT OR REPLACE INTO banned_users_tbl(user_id,reason,banned_by,banned_at) VALUES(?,?,?,?)",
            (uid, reason, banned_by, datetime.now().isoformat())
        )
        c.commit()
        c.close()
    banned_users[uid] = {"reason": reason, "banned_by": banned_by}

def db_unban_user(uid):
    with DB_LOCK:
        c = _db()
        c.execute("DELETE FROM banned_users_tbl WHERE user_id=?", (uid,))
        c.commit()
        c.close()
    banned_users.pop(uid, None)

def db_set_setting(key, value):
    with DB_LOCK:
        c = _db()
        c.execute(
            "INSERT OR REPLACE INTO system_settings(key,value) VALUES(?,?)", (key, str(value))
        )
        c.commit()
        c.close()

def db_expiry_notif_sent(uid, notif_type):
    with DB_LOCK:
        c = _db()
        row = c.execute(
            "SELECT 1 FROM expiry_notifications WHERE user_id=? AND notif_type=?",
            (uid, notif_type)
        ).fetchone()
        c.close()
    return row is not None

def db_mark_expiry_notif(uid, notif_type):
    with DB_LOCK:
        c = _db()
        c.execute(
            "INSERT OR IGNORE INTO expiry_notifications(user_id,notif_type,sent_at) VALUES(?,?,?)",
            (uid, notif_type, datetime.now().isoformat())
        )
        c.commit()
        c.close()

def db_clear_expiry_notifs(uid):
    with DB_LOCK:
        c = _db()
        c.execute("DELETE FROM expiry_notifications WHERE user_id=?", (uid,))
        c.commit()
        c.close()

def db_get_user_info(uid):
    with DB_LOCK:
        c = _db()
        row = c.execute(
            "SELECT user_id,username,first_name,joined_at FROM active_users WHERE user_id=?", (uid,)
        ).fetchone()
        c.close()
    return dict(row) if row else None

def db_all_users():
    with DB_LOCK:
        c = _db()
        rows = c.execute(
            "SELECT user_id,username,first_name,joined_at FROM active_users ORDER BY user_id"
        ).fetchall()
        c.close()
    return [dict(r) for r in rows]

# ═══════════════════════════════════════════════════════════════
# FOLDER SYSTEM
# ═══════════════════════════════════════════════════════════════
def get_user_folder(uid):
    f = os.path.join(UPLOAD_BOTS_DIR, f"USER_{uid}")
    os.makedirs(f, exist_ok=True)
    return f

def get_next_slot(uid):
    existing = [f["slot"] for f in user_files_db.get(uid, [])]
    return (max(existing) + 1) if existing else 1

def create_script_folder(uid, slot):
    f = os.path.join(get_user_folder(uid), f"script_{slot}")
    os.makedirs(f, exist_ok=True)
    return f

def get_user_storage_mb(uid):
    folder = os.path.join(UPLOAD_BOTS_DIR, f"USER_{uid}")
    if not os.path.exists(folder):
        return 0.0
    total = 0
    for root, dirs, files in os.walk(folder):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total / (1024 * 1024)

def get_file_by_slot(uid, slot):
    for f in user_files_db.get(uid, []):
        if f["slot"] == slot:
            return f
    return None

# ═══════════════════════════════════════════════════════════════
# NAVIGATION STACK
# ═══════════════════════════════════════════════════════════════
def nav_push(uid, screen, **params):
    _nav_stacks.setdefault(uid, []).append((screen, params))
    if len(_nav_stacks[uid]) > 15:
        _nav_stacks[uid].pop(0)

def nav_back(call):
    uid   = call.from_user.id
    stack = _nav_stacks.get(uid, [])
    if not stack:
        _show_main(call)
        return
    screen, params = stack.pop()
    _render_screen(call, screen, **params)

def nav_clear(uid):
    _nav_stacks[uid] = []

# ═══════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════
def is_premium(uid):
    if uid in admin_ids:
        return True
    sub = user_subscriptions.get(uid)
    return bool(sub and sub["expiry"] > datetime.now())

def get_bot_limit(uid):
    if uid in admin_ids:
        return 999
    sub = user_subscriptions.get(uid)
    if sub and sub["expiry"] > datetime.now():
        return sub["bot_limit"]
    return system_settings.get("free_limit", 1)

def get_storage_limit(uid):
    override = db_get_user_storage_override(uid)
    if override is not None:
        return override
    prem = is_premium(uid)
    return system_settings["premium_storage_mb"] if prem else system_settings["free_storage_mb"]

def get_sub_info(uid):
    if uid in admin_ids:
        return True, 999, "সীমাহীন", 9999
    sub = user_subscriptions.get(uid)
    if sub and sub["expiry"] > datetime.now():
        days = (sub["expiry"] - datetime.now()).days
        return True, sub["bot_limit"], sub["expiry"].strftime("%Y-%m-%d"), days
    return False, system_settings.get("free_limit", 1), "-", 0

def is_running(uid, slot):
    key  = f"{uid}_{slot}"
    info = bot_scripts.get(key)
    return bool(info and info.get("proc") and info["proc"].poll() is None)

def running_count_user(uid):
    return sum(1 for f in user_files_db.get(uid, []) if is_running(uid, f["slot"]))

def kill_process_tree(pid):
    try:
        parent = psutil.Process(pid)
        for ch in parent.children(recursive=True):
            try:
                ch.kill()
            except Exception:
                pass
        parent.kill()
    except Exception:
        pass

def fmt_uptime():
    d  = datetime.now() - BOT_START_TIME
    h, rem = divmod(d.seconds, 3600)
    m, s   = divmod(rem, 60)
    return f"{d.days}d {h}h {m}m {s}s"

def fmt_time_ago(iso_str):
    try:
        mins = int((datetime.now() - datetime.fromisoformat(iso_str)).total_seconds() / 60)
        if mins < 1:    return "এইমাত্র"
        if mins < 60:   return f"{mins} মিনিট আগে"
        if mins < 1440: return f"{mins//60} ঘণ্টা আগে"
        return f"{mins//1440} দিন আগে"
    except Exception:
        return iso_str[:10] if iso_str else "-"

def h(text):
    """HTML বিশেষ অক্ষর escape করে।"""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def safe_send(cid, text, markup=None, parse_mode="HTML"):
    try:
        return bot.send_message(cid, text, reply_markup=markup, parse_mode=parse_mode)
    except Exception as e:
        logger.warning(f"safe_send: {e}")
        try:
            return bot.send_message(cid, text, reply_markup=markup, parse_mode=None)
        except Exception as e2:
            logger.error(f"safe_send fallback: {e2}")

def safe_edit(cid, mid, text, markup=None, parse_mode="HTML"):
    try:
        bot.edit_message_text(text, cid, mid, reply_markup=markup, parse_mode=parse_mode)
    except Exception:
        try:
            safe_send(cid, text, markup, parse_mode)
        except Exception as e:
            logger.warning(f"safe_edit failed: {e}")

def activate_premium(uid, months, bot_limit=10, granted_by=None):
    """Premium অ্যাক্টিভ করে — বিদ্যমান মেয়াদে যোগ করে।"""
    existing = user_subscriptions.get(uid)
    if existing and existing["expiry"] > datetime.now():
        base = existing["expiry"]
    else:
        base = datetime.now()
    expiry = base + timedelta(days=months * 30)
    db_save_sub(uid, bot_limit, expiry, granted_by or OWNER_ID)
    db_clear_expiry_notifs(uid)
    return expiry

# ═══════════════════════════════════════════════════════════════
# TEXT BUILDERS (HTML parse mode)
# ═══════════════════════════════════════════════════════════════
def build_welcome(uid, name):
    prem, lim, exp, days = get_sub_info(uid)
    plan = "💎 Premium" if prem else "🆓 Free"
    cnt  = len(user_files_db.get(uid, []))
    return (
        f"✦ স্বাগতম, <b>{h(name)}</b>! ✦\n\n"
        "<b>𝗦𝗰𝗿𝗶𝗽𝘁 𝗛𝗼𝘀𝘁 𝗣𝗿𝗼</b>\n"
        "<i>আপনার বিশ্বস্ত হোস্টিং পার্টনার</i>\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        "◆ ২৪/৭ আপটাইম গ্যারান্টি\n"
        "◆ সর্বোচ্চ নিরাপত্তা\n"
        "◆ তাৎক্ষণিক ডেপ্লয়মেন্ট\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 প্ল্যান: {plan} | স্ক্রিপ্ট: {cnt}/{lim}"
    )

def build_user_panel(uid, name):
    prem, lim, exp, days = get_sub_info(uid)
    files = user_files_db.get(uid, [])
    used  = get_user_storage_mb(uid)
    stor  = get_storage_limit(uid)
    pct   = min(used / stor, 1.0) if stor else 0
    bar   = "█" * int(pct * 10) + "░" * (10 - int(pct * 10))
    plan  = "💎 Premium" if prem else "🆓 Free"
    exp_l = (
        f"\n📅 মেয়াদ: <i>{days} দিন বাকি ({h(exp)})</i>"
        if prem and uid not in admin_ids else ""
    )
    return (
        "⟡ <b>আমার প্যানেল</b> ⟡\n\n"
        f"👤 @{h(name)}\n"
        f"🆔 ID: <code>{uid}</code>\n"
        f"⭐ প্ল্যান: {plan}{exp_l}\n"
        f"📄 স্ক্রিপ্ট: <code>{len(files)}/{lim}</code>\n"
        f"🏃 রানিং: <code>{running_count_user(uid)}</code>\n"
        f"💾 স্টোরেজ: <code>{used:.1f}MB/{stor}MB</code> {bar}\n"
        "━━━━━━━━━━━━━━━━━━━━━━━"
    )

def build_my_scripts(uid):
    files = user_files_db.get(uid, [])
    prem, lim, _, _ = get_sub_info(uid)
    used  = get_user_storage_mb(uid)
    stor  = get_storage_limit(uid)
    if not files:
        return (
            "⟡ <b>আমার স্ক্রিপ্টস</b> ⟡\n\n"
            "📭 এখনো কোনো স্ক্রিপ্ট নেই।\n\n"
            "🚀 একটি ফাইল আপলোড করে শুরু করুন।"
        )
    lines = []
    for f in files:
        if is_running(uid, f["slot"]):   st = "🟢 Running"
        elif f["status"] == "pending":   st = "🟡 Pending"
        elif f["status"] == "rejected":  st = "❌ Rejected"
        else:                            st = "🔴 Stopped"
        sz = f"{f['file_size_kb']:.0f}KB"
        lines.append(f"▸ <code>{h(f['file_name'])}</code>  {st}  |  {sz}")
    return (
        "⟡ <b>আমার স্ক্রিপ্টস</b> ⟡\n\n"
        + "\n".join(lines)
        + f"\n\n━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💾 মোট: <code>{used:.1f}MB / {stor}MB</code>\n"
        f"📄 মোট ফাইল: <code>{len(files)}/{lim}</code>"
    )

def build_file_control(uid, slot):
    fi = get_file_by_slot(uid, slot)
    if not fi:
        return "❌ ফাইল খুঁজে পাওয়া যায়নি।"
    running = is_running(uid, slot)
    status  = "🟢 Running" if running else (
        "🟡 Pending" if fi["status"] == "pending" else "🔴 Stopped"
    )
    key     = f"{uid}_{slot}"
    pid_txt = str(bot_scripts[key]["proc"].pid) if key in bot_scripts and running else "-"
    cpu_txt = ram_txt = "-"
    if running:
        try:
            p       = psutil.Process(bot_scripts[key]["proc"].pid)
            cpu_txt = f"{p.cpu_percent(interval=0.1):.1f}%"
            ram_txt = f"{p.memory_info().rss/1024/1024:.1f} MB"
        except Exception:
            pass
    types_map = {"py": "Python (.py)", "js": "Node.js (.js)", "zip": "ZIP Project"}
    return (
        "⟡ <b>ফাইল নিয়ন্ত্রণ</b> ⟡\n\n"
        f"📄 <b>{h(fi['file_name'])}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🗂 ধরন: <code>{types_map.get(fi['file_type'], fi['file_type'])}</code>\n"
        f"📡 অবস্থা: {status}\n"
        f"🔢 PID: <code>{pid_txt}</code>\n"
        f"🖥 CPU: <code>{cpu_txt}</code> | RAM: <code>{ram_txt}</code>\n"
        f"💾 সাইজ: <code>{fi['file_size_kb']:.0f} KB</code>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━"
    )

def build_admin_panel():
    pf  = len(db_get_pending_approvals())
    pp  = len(db_get_pending_payments())
    run = sum(1 for v in bot_scripts.values() if v.get("proc") and v["proc"].poll() is None)
    prc = sum(1 for u in active_users if is_premium(u) and u not in admin_ids)
    return (
        "✦ <b>অ্যাডমিন প্যানেল</b> ✦\n\n"
        "<b>System Overview</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"◆ মোট ইউজার: <code>{len(active_users)}</code>\n"
        f"◆ Premium: <code>{prc}</code> | Free: <code>{len(active_users)-prc}</code>\n"
        f"◆ পেন্ডিং ফাইল: <code>{pf}</code>\n"
        f"◆ পেন্ডিং পেমেন্ট: <code>{pp}</code>\n"
        f"◆ রানিং স্ক্রিপ্ট: <code>{run}</code>\n"
        f"◆ Uptime: <code>{fmt_uptime()}</code>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━"
    )

def build_server_stats():
    cpu  = psutil.cpu_percent(interval=0.5)
    ram  = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    cb   = "█"*int(cpu/10) + "░"*(10-int(cpu/10))
    rb   = "█"*int(ram.percent/10) + "░"*(10-int(ram.percent/10))
    run  = sum(1 for v in bot_scripts.values() if v.get("proc") and v["proc"].poll() is None)
    prc  = sum(1 for u in active_users if is_premium(u) and u not in admin_ids)
    return (
        "🖥️ <b>সার্ভার স্ট্যাটাস</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚙️ CPU: <code>{cpu:.0f}%</code> {cb}\n"
        f"🧠 RAM: <code>{ram.used/1024**3:.1f}/{ram.total/1024**3:.1f} GB</code> {rb}\n"
        f"💿 Disk: <code>{disk.used/1024**3:.1f}/{disk.total/1024**3:.1f} GB</code> ({disk.percent:.1f}%)\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🤖 রানিং স্ক্রিপ্ট: <code>{run}</code>\n"
        f"👥 মোট ইউজার: <code>{len(active_users)}</code>\n"
        f"💎 Premium: <code>{prc}</code>\n"
        f"⏱ Uptime: <code>{fmt_uptime()}</code>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━"
    )

def build_premium_plans():
    prices = db_get_plan_prices()
    lines  = "\n".join(
        f"◆ {h(p['label'])}: <b>${p['price_usd']:.0f} USDT</b>" for p in prices
    )
    return (
        "⟡ <b>প্রিমিয়াম প্ল্যান</b> ⟡\n\n"
        "<i>Premium সুবিধাসমূহ:</i>\n"
        "◆ অ্যাডমিন কাস্টম ফাইল লিমিট\n"
        "◆ ৫০০MB স্টোরেজ\n"
        "◆ Telegram Session তৈরি\n"
        "◆ Auto Restart (crash হলে)\n"
        "◆ Priority Approval\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        "একটি প্ল্যান বেছে নিন:\n\n"
        + lines
    )

def build_status(uid, name):
    prem, lim, exp, days = get_sub_info(uid)
    files = user_files_db.get(uid, [])
    used  = get_user_storage_mb(uid)
    stor  = get_storage_limit(uid)
    plan  = "💎 Premium" if prem else "🆓 Free"
    return (
        "📊 <b>আপনার স্ট্যাটাস</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 @{h(name)}\n"
        f"🆔 ID: <code>{uid}</code>\n"
        f"⭐ প্ল্যান: {plan}\n"
        f"📅 মেয়াদ: <code>{h(exp)}</code> ({days} দিন)\n"
        f"📄 স্ক্রিপ্ট: <code>{len(files)}/{lim}</code>\n"
        f"🏃 রানিং: <code>{running_count_user(uid)}</code>\n"
        f"💾 স্টোরেজ: <code>{used:.1f}MB/{stor}MB</code>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━"
    )

def build_help():
    return (
        "ℹ️ <b>সাহায্য</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        "📄 <b>সমর্থিত ফাইল:</b>\n"
        "◆ <code>.py</code> — Python স্ক্রিপ্ট\n"
        "◆ <code>.js</code> — Node.js স্ক্রিপ্ট\n"
        "◆ <code>.zip</code> — পুরো প্রজেক্ট\n\n"
        "💎 <b>Premium সুবিধা:</b>\n"
        "◆ বেশি ফাইল হোস্ট\n"
        "◆ ৫০০MB স্টোরেজ\n"
        "◆ Telethon/Pyrogram Session\n"
        "◆ অটো রিস্টার্ট\n\n"
        "📌 <b>কমান্ড:</b>\n"
        "◆ /start — মেইন মেনু\n"
        "◆ /status — আমার স্ট্যাটাস\n"
        "◆ /ping — বট জীবিত কিনা\n"
        "◆ /cancel — বাতিল করুন\n"
        "━━━━━━━━━━━━━━━━━━━━━━━"
    )

def build_admin_settings():
    fl   = system_settings.get("free_limit", 1)
    fs   = system_settings.get("free_storage_mb", 50)
    ps   = system_settings.get("premium_storage_mb", 500)
    cdw  = system_settings.get("cooldown_sec", 3)
    lock = "🔒 On" if bot_locked else "🟢 Off"
    mnt  = "🔧 On" if maintenance_mode else "🟢 Off"
    return (
        "⚙️ <b>বট সেটিংস</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⏱ Cooldown: <code>{cdw}s</code>\n"
        f"🤖 Free Script Limit: <code>{fl}</code>\n"
        f"💾 Free Storage: <code>{fs} MB</code>\n"
        f"💾 Premium Storage: <code>{ps} MB</code>\n"
        f"🔒 Bot Lock: {lock}\n"
        f"🔧 Maintenance: {mnt}\n"
        "━━━━━━━━━━━━━━━━━━━━━━━"
    )

def build_user_profile(uid):
    info   = db_get_user_info(uid)
    prem, lim, exp, days = get_sub_info(uid)
    files  = user_files_db.get(uid, [])
    used   = get_user_storage_mb(uid)
    stor   = get_storage_limit(uid)
    plan   = "💎 Premium" if prem else "🆓 Free"
    ban    = (
        f"🚫 ব্যান ({h(banned_users[uid]['reason'])})"
        if uid in banned_users else "✅ সক্রিয়"
    )
    uname  = info["username"] if info and info["username"] else str(uid)
    fname  = info["first_name"] if info and info["first_name"] else "-"
    joined = (info["joined_at"] or "")[:10] if info else "-"
    return (
        "⟡ <b>ইউজার প্রোফাইল</b> ⟡\n\n"
        f"👤 @{h(uname)}\n"
        f"📛 নাম: {h(fname)}\n"
        f"🆔 ID: <code>{uid}</code>\n"
        f"⭐ প্ল্যান: {plan}\n"
        f"📅 মেয়াদ: <code>{h(exp)}</code> ({days} দিন)\n"
        f"📄 স্ক্রিপ্ট: <code>{len(files)}/{lim}</code>\n"
        f"🏃 রানিং: <code>{running_count_user(uid)}</code>\n"
        f"💾 Storage: <code>{used:.1f}MB/{stor}MB</code>\n"
        f"📅 যোগ দিয়েছে: <code>{joined}</code>\n"
        f"🔒 স্ট্যাটাস: {ban}\n"
        "━━━━━━━━━━━━━━━━━━━━━━━"
    )

def build_session_menu_text(uid):
    sessions = db_get_sessions(uid)
    return (
        "🔑 <b>Session তৈরি করুন</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💾 সেভ করা: <code>{len(sessions)}</code> সেশন\n\n"
        "➕ নতুন Session তৈরি করুন\n"
        "↳ Phone + OTP দিয়ে Login করে session তৈরি করুন\n\n"
        "📋 আমার সেশন দেখুন\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        "💡 <code>.session</code> ফাইলের নাম আপনার bot code এ ব্যবহার করুন।"
    )

# ═══════════════════════════════════════════════════════════════
# ★ কালার সিস্টেম — KEYBOARD BUILDERS
# ═══════════════════════════════════════════════════════════════
# Style prefix নিয়ম:
#   "p|" → primary   (নীল)
#   "s|" → success   (সবুজ)
#   "d|" → danger    (লাল)
#   "e|" → secondary (ধূসর) [default]
# callback_data সর্বোচ্চ 64 chars — prefix নেয় মাত্র 2 chars

def _b(text, cd, style="e"):
    """
    কালার-সহ Inline বাটন তৈরি করে।
    style: "p"=primary(নীল), "s"=success(সবুজ),
           "d"=danger(লাল), "e"=secondary(ধূসর)
    """
    styled_cd = f"{style}|{cd}"
    # 64 chars সীমা পার হলে prefix বাদ দাও
    if len(styled_cd) > 64:
        styled_cd = cd
    return types.InlineKeyboardButton(text, callback_data=styled_cd)

def _bu(text, url):
    """URL বাটন (কালার নেই)।"""
    return types.InlineKeyboardButton(text, url=url)

def _strip_style(data):
    """Callback data থেকে style prefix বাদ দেয়।"""
    if len(data) > 2 and data[1] == "|" and data[0] in ("p", "s", "d", "e"):
        return data[2:]
    return data

# ── Keyboard functions ─────────────────────────────────────────
def kb_main(uid):
    m = types.InlineKeyboardMarkup(row_width=1)
    m.add(_b("🚀 স্ক্রিপ্ট হোস্ট করুন", "upload_file", "s"))
    m.add(_b("👤 আমার প্যানেল", "user_panel", "p"))
    if is_premium(uid):
        m.add(_b("🔑 Session তৈরি করুন", "session_menu", "p"))
    m.row(
        _b("📊 আমার স্ট্যাটাস", "user_status", "e"),
        _b("ℹ️ সাহায্য", "help_menu", "e")
    )
    if uid in admin_ids:
        m.add(_b("👑 অ্যাডমিন প্যানেল", "admin_panel", "p"))
    return m

def kb_user_panel(uid):
    m = types.InlineKeyboardMarkup(row_width=1)
    m.add(_b("📁 আমার স্ক্রিপ্টস", "my_scripts", "p"))
    if is_premium(uid):
        m.add(_b("🔑 Session তৈরি", "session_menu", "p"))
    else:
        m.add(_b("💎 প্রিমিয়াম নিন", "buy_premium", "s"))
    m.add(_b("📊 স্ট্যাটাস দেখুন", "user_status", "e"))
    m.add(_b("◀️ ফিরে যান", "nav_back", "e"))
    return m

def kb_my_scripts(uid):
    m = types.InlineKeyboardMarkup(row_width=1)
    for f in user_files_db.get(uid, []):
        if is_running(uid, f["slot"]):       icon = "🟢"
        elif f["status"] == "pending":       icon = "🟡"
        elif f["status"] == "rejected":      icon = "❌"
        else:                                icon = "🔴"
        m.add(_b(f"{icon} {f['file_name']}", f"fctl_{f['slot']}", "p"))
    m.add(_b("◀️ ফিরে যান", "nav_back", "e"))
    return m

def kb_file_ctrl(uid, slot):
    fi      = get_file_by_slot(uid, slot)
    running = is_running(uid, slot)
    m       = types.InlineKeyboardMarkup(row_width=2)
    if fi and fi["status"] == "pending":
        m.add(_b("⏳ যাচাই চলছে...", "noop", "e"))
    elif running:
        m.row(
            _b("⏹️ Stop", f"fstop_{slot}", "d"),
            _b("🔄 Restart", f"frestart_{slot}", "e")
        )
    else:
        m.add(_b("▶️ Start", f"fstart_{slot}", "s"))
    m.row(
        _b("📜 লগ দেখুন", f"flogs_{slot}", "e"),
        _b("⚡ Live Stats", f"fspeed_{slot}", "e")
    )
    m.row(
        _b("🔄 আপডেট", f"fupdate_{slot}", "e"),
        _b("🗑️ ডিলিট", f"fdelete_{slot}", "d")
    )
    m.add(_b("◀️ ফিরে যান", "nav_back", "e"))
    return m

def kb_admin():
    m = types.InlineKeyboardMarkup(row_width=2)
    m.row(
        _b("📋 ফাইল অ্যাপ্রুভাল", "adm_approvals", "p"),
        _b("💰 পেমেন্ট অ্যাপ্রুভাল", "adm_payments", "s")
    )
    m.add(_b("👥 ইউজার ম্যানেজমেন্ট", "adm_users", "p"))
    m.row(
        _b("🖥️ সার্ভার স্ট্যাটাস", "adm_stats", "e"),
        _b("📢 ব্রডকাস্ট", "adm_broadcast", "p")
    )
    m.add(_b("⚙️ বট সেটিংস", "adm_settings", "e"))
    m.add(_b("◀️ ফিরে যান", "nav_back", "e"))
    return m

def kb_admin_settings():
    m = types.InlineKeyboardMarkup(row_width=2)
    m.row(
        _b("💰 প্রাইস ম্যানেজমেন্ট", "adm_prices", "p"),
        _b("💳 পেমেন্ট মেথড", "adm_payment_methods", "p")
    )
    m.add(_b("📝 ওয়েলকাম মেসেজ এডিট", "adm_edit_welcome", "e"))
    m.row(
        _b("🔒 বট লক" if not bot_locked else "🔓 বট আনলক", "adm_toggle_lock", "d"),
        _b("🔧 Maintenance" if not maintenance_mode else "✅ Maintenance বন্ধ", "adm_toggle_maint", "d")
    )
    m.row(
        _b("🤖 Free লিমিট সেট", "adm_set_free_limit", "e"),
        _b("⏱ Cooldown সেট", "adm_set_cooldown", "e")
    )
    m.add(_b("🗑️ সব লগ ক্লিয়ার", "adm_clear_logs", "d"))
    m.add(_b("◀️ ফিরে যান", "nav_back", "e"))
    return m

def kb_admin_users(page=0):
    users = db_all_users()
    per   = 8
    start = page * per
    chunk = users[start:start+per]
    m     = types.InlineKeyboardMarkup(row_width=1)
    for u in chunk:
        uname = u["username"] or str(u["user_id"])
        plan  = "💎" if is_premium(u["user_id"]) else "🆓"
        ban   = "🚫" if u["user_id"] in banned_users else ""
        m.add(_b(f"{plan}{ban} @{uname} ({u['user_id']})", f"adm_view_user_{u['user_id']}", "p"))
    nav_row = []
    if page > 0:
        nav_row.append(_b("⬅️ আগের", f"adm_users_page_{page-1}", "e"))
    if start + per < len(users):
        nav_row.append(_b("পরের ➡️", f"adm_users_page_{page+1}", "e"))
    if nav_row:
        m.row(*nav_row)
    m.add(_b("🔍 ID দিয়ে খুঁজুন", "adm_search_user", "e"))
    m.add(_b("◀️ ফিরে যান", "nav_back", "e"))
    return m, len(users)

def kb_user_profile_admin(target_uid):
    m = types.InlineKeyboardMarkup(row_width=2)
    m.row(
        _b("▶️ সব Start", f"adm_startall_{target_uid}", "s"),
        _b("⏹️ সব Stop",  f"adm_stopall_{target_uid}", "d")
    )
    m.row(
        _b("✉️ মেসেজ পাঠাও", f"adm_msg_{target_uid}", "p"),
        _b("⭐ Premium দাও", f"adm_giveprem_{target_uid}", "s")
    )
    m.add(_b("💾 Storage সেট করো", f"adm_setstorage_{target_uid}", "p"))
    if target_uid in banned_users:
        m.add(_b("✅ Unban করো", f"adm_unban_{target_uid}", "s"))
    else:
        m.add(_b("🚫 Ban করো", f"adm_ban_{target_uid}", "d"))
    m.add(_b("◀️ ফিরে যান", "adm_users", "e"))
    return m

def kb_prices():
    m = types.InlineKeyboardMarkup(row_width=2)
    for p in db_get_plan_prices():
        m.add(_b(f"✏️ {p['label']}: ${p['price_usd']:.0f}", f"edit_price_{p['months']}", "p"))
    m.add(_b("◀️ ফিরে যান", "nav_back", "e"))
    return m

def kb_payment_methods_admin():
    rows  = db_get_payment_settings()
    icons = {"bkash": "💛", "nagad": "🟠", "rocket": "🟣", "usdt": "🟡"}
    m     = types.InlineKeyboardMarkup(row_width=1)
    for r in rows:
        num = (r["number_or_address"] or "")[:12] or "সেট নেই"
        act = "✅" if r["is_active"] else "❌"
        m.add(_b(
            f"{icons.get(r['method_name'],'💳')} {r['display_name']} {act} ({num})",
            f"set_pay_method_{r['method_name']}", "p"
        ))
    m.row(
        _b("➕ নতুন মেথড যোগ করুন", "adm_add_pay_method", "s"),
        _b("🗑️ মেথড ডিলিট করুন", "adm_del_pay_method", "d")
    )
    m.add(_b("◀️ ফিরে যান", "nav_back", "e"))
    return m

def kb_del_pay_methods_admin():
    rows = db_get_payment_settings()
    m    = types.InlineKeyboardMarkup(row_width=1)
    for r in rows:
        m.add(_b(f"🗑️ {r['display_name']} ({r['method_name']})", f"del_pay_method_{r['method_name']}", "d"))
    m.add(_b("◀️ ফিরে যান", "adm_payment_methods", "e"))
    return m

def kb_premium_plans():
    m = types.InlineKeyboardMarkup(row_width=1)
    for p in db_get_plan_prices():
        m.add(_b(f"📅 {p['label']} — ${p['price_usd']:.0f} USDT", f"plan_select_{p['months']}", "s"))
    m.add(_b("◀️ ফিরে যান", "nav_back", "e"))
    return m

def kb_pay_methods_user(months):
    rows  = db_get_payment_settings()
    icons = {"bkash": "💛", "nagad": "🟠", "rocket": "🟣", "usdt": "🟡"}
    m     = types.InlineKeyboardMarkup(row_width=1)
    for r in rows:
        if r["is_active"] and r["number_or_address"]:
            m.add(_b(
                f"{icons.get(r['method_name'],'💳')} {r['display_name']}",
                f"pay_method_{months}_{r['method_name']}", "p"
            ))
    m.add(_b("◀️ ফিরে যান", "nav_back", "e"))
    return m

def kb_broadcast():
    pm = sum(1 for u in active_users if is_premium(u) and u not in admin_ids)
    fr = len(active_users) - pm
    m  = types.InlineKeyboardMarkup(row_width=1)
    m.add(_b(f"👥 সবাইকে [{len(active_users)}]", "bc_all", "p"))
    m.add(_b(f"⭐ শুধু Premium [{pm}]", "bc_premium", "s"))
    m.add(_b(f"🆓 শুধু Free [{fr}]", "bc_free", "e"))
    m.add(_b("👤 নির্দিষ্ট ইউজার", "bc_specific", "p"))
    m.add(_b("◀️ ফিরে যান", "nav_back", "e"))
    return m

def kb_session_menu():
    m = types.InlineKeyboardMarkup(row_width=1)
    m.add(_b("➕ নতুন Session তৈরি", "session_new", "s"))
    m.add(_b("📋 আমার Session", "session_list", "p"))
    m.add(_b("🗑️ Session ডিলিট", "session_delete", "d"))
    m.add(_b("◀️ ফিরে যান", "nav_back", "e"))
    return m

def kb_session_type():
    m = types.InlineKeyboardMarkup(row_width=2)
    m.row(
        _b("🐍 Telethon", "stype_telethon", "p"),
        _b("🔥 Pyrogram", "stype_pyrogram", "p")
    )
    m.add(_b("❌ বাতিল", "nav_back", "d"))
    return m

def kb_back():
    m = types.InlineKeyboardMarkup()
    m.add(_b("◀️ ফিরে যান", "nav_back", "e"))
    return m

def kb_back_refresh(cd):
    m = types.InlineKeyboardMarkup(row_width=2)
    m.row(
        _b("🔄 Refresh", cd, "e"),
        _b("◀️ ফিরে যান", "nav_back", "e")
    )
    return m

def kb_approval(aid):
    """অ্যাডমিন ফাইল অ্যাপ্রুভাল বাটন।"""
    m = types.InlineKeyboardMarkup(row_width=2)
    m.row(
        _b("✅ Approve", f"approve_{aid}", "s"),
        _b("❌ Reject",  f"reject_{aid}", "d")
    )
    return m

def kb_pay_approval(pid):
    """অ্যাডমিন পেমেন্ট অ্যাপ্রুভাল বাটন।"""
    m = types.InlineKeyboardMarkup(row_width=2)
    m.row(
        _b("✅ Approve", f"pay_approve_{pid}", "s"),
        _b("❌ Reject",  f"pay_reject_{pid}", "d")
    )
    return m

def kb_confirm_delete(slot):
    m = types.InlineKeyboardMarkup(row_width=2)
    m.row(
        _b("✅ হ্যাঁ, ডিলিট করো", f"fdelete_confirm_{slot}", "d"),
        _b("❌ বাতিল", "nav_back", "e")
    )
    return m

def kb_retry():
    """স্ক্যান ব্যর্থ হলে আবার চেষ্টার বাটন।"""
    m = types.InlineKeyboardMarkup()
    m.add(_b("🔄 আবার চেষ্টা করুন", "upload_file", "p"))
    return m

def kb_go_scripts():
    m = types.InlineKeyboardMarkup()
    m.add(_b("📁 আমার স্ক্রিপ্টস", "my_scripts", "p"))
    return m

# ═══════════════════════════════════════════════════════════════
# SCREEN RENDERER
# ═══════════════════════════════════════════════════════════════
def _render_screen(call, screen, **p):
    uid  = call.from_user.id
    cid  = call.message.chat.id
    mid  = call.message.message_id
    name = call.from_user.username or call.from_user.first_name or "User"

    if screen == "main":
        safe_edit(cid, mid, build_welcome(uid, name), kb_main(uid))
    elif screen == "user_panel":
        safe_edit(cid, mid, build_user_panel(uid, name), kb_user_panel(uid))
    elif screen == "my_scripts":
        safe_edit(cid, mid, build_my_scripts(uid), kb_my_scripts(uid))
    elif screen == "file_control":
        slot = p.get("slot")
        safe_edit(cid, mid, build_file_control(uid, slot), kb_file_ctrl(uid, slot))
    elif screen == "admin_panel":
        safe_edit(cid, mid, build_admin_panel(), kb_admin())
    elif screen == "buy_premium":
        safe_edit(cid, mid, build_premium_plans(), kb_premium_plans())
    elif screen == "session_menu":
        safe_edit(cid, mid, build_session_menu_text(uid), kb_session_menu())
    elif screen == "help":
        safe_edit(cid, mid, build_help(), kb_back())
    elif screen == "adm_settings":
        safe_edit(cid, mid, build_admin_settings(), kb_admin_settings())
    elif screen == "adm_users":
        _render_adm_users(call, p.get("page", 0))

def _show_main(call):
    uid  = call.from_user.id
    cid  = call.message.chat.id
    mid  = call.message.message_id
    name = call.from_user.username or call.from_user.first_name or "User"
    safe_edit(cid, mid, build_welcome(uid, name), kb_main(uid))

def _render_adm_users(call, page=0):
    cid = call.message.chat.id
    mid = call.message.message_id
    m, total = kb_admin_users(page)
    safe_edit(
        cid, mid,
        f"👥 <b>ইউজার ম্যানেজমেন্ট</b>\n\n"
        f"মোট ইউজার: <code>{total}</code>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"পেজ {page+1}:", m
    )

# ═══════════════════════════════════════════════════════════════
# ★ ফেক সিকিউরিটি স্ক্যান (কোথাও "Admin" বা "অনুমোদন" নেই)
# ═══════════════════════════════════════════════════════════════
def send_fake_security_scan(cid):
    """
    ইউজারকে অটো সিস্টেম স্ক্যানের বার্তা দেখায়।
    কোনো Admin বা অনুমোদনের কথা থাকবে না।
    """
    try:
        # Step 1 — আপলোড সম্পন্ন
        m = bot.send_message(
            cid,
            "✦ <b>ফাইল প্রসেসিং</b> ✦\n\n"
            "🔵 আপলোড সম্পন্ন ✓\n"
            "⬜ ভাইরাস স্ক্যান চলছে...\n"
            "⬜ কোড অ্যানালাইসিস...\n"
            "⬜ সিকিউরিটি চেক...",
            parse_mode="HTML"
        )
        time.sleep(2)

        # Step 2 — ভাইরাস স্ক্যান সম্পন্ন
        bot.edit_message_text(
            "✦ <b>ফাইল প্রসেসিং</b> ✦\n\n"
            "🔵 আপলোড সম্পন্ন ✓\n"
            "🔵 ভাইরাস স্ক্যান সম্পন্ন ✓\n"
            "⬜ কোড অ্যানালাইসিস চলছে...\n"
            "⬜ সিকিউরিটি চেক...",
            cid, m.message_id,
            parse_mode="HTML"
        )
        time.sleep(2)

        # Step 3 — কোড অ্যানালাইসিস সম্পন্ন
        bot.edit_message_text(
            "✦ <b>ফাইল প্রসেসিং</b> ✦\n\n"
            "🔵 আপলোড সম্পন্ন ✓\n"
            "🔵 ভাইরাস স্ক্যান সম্পন্ন ✓\n"
            "🔵 কোড অ্যানালাইসিস সম্পন্ন ✓\n"
            "⬜ সিকিউরিটি চেক চলছে...",
            cid, m.message_id,
            parse_mode="HTML"
        )
        time.sleep(2)

        # Step 4 — সব সম্পন্ন (কোনো Admin উল্লেখ নেই)
        bot.edit_message_text(
            "✦ <b>ফাইল প্রসেসিং</b> ✦\n\n"
            "🔵 আপলোড সম্পন্ন ✓\n"
            "🔵 ভাইরাস স্ক্যান সম্পন্ন ✓\n"
            "🔵 কোড অ্যানালাইসিস সম্পন্ন ✓\n"
            "🔵 সিকিউরিটি চেক সম্পন্ন ✓\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━\n"
            "⚙️ <i>সিস্টেম ডেপ্লয়মেন্ট প্রস্তুত করছে...</i>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "⏳ <b>কিছুক্ষণের মধ্যে আপনার স্ক্রিপ্ট চালু হবে।</b>",
            cid, m.message_id,
            parse_mode="HTML"
        )
    except Exception:
        pass

# ═══════════════════════════════════════════════════════════════
# ★ বাগ ১ ঠিক — অ্যাডমিনের কাছে ফাইলও পাঠানো হয়
# ═══════════════════════════════════════════════════════════════
def notify_admins_approval(uid, fname, ftype, slot, aid, size_kb, username, is_prem):
    """
    অ্যাডমিনদের নতুন ফাইল রিকোয়েস্ট জানায়।
    ফাইল আছে → document + caption + buttons (একটাই message)
    ফাইল নেই → text message + buttons
    """
    priority = "⭐ Priority (Premium)" if is_prem else "🆓 Free"
    caption = (
        "⟡ <b>নতুন ফাইল রিকোয়েস্ট</b> ⟡\n\n"
        f"👤 ইউজার: @{h(username)}\n"
        f"🆔 ID: <code>{uid}</code>\n"
        f"📄 ফাইল: <code>{h(fname)}</code>\n"
        f"📏 সাইজ: <code>{size_kb:.1f} KB</code>\n"
        f"🔧 ধরন: <code>{ftype.upper()}</code>\n"
        f"🎯 প্ল্যান: {priority}\n"
        "━━━━━━━━━━━━━━━━━━━━━━━"
    )
    file_path = os.path.join(get_user_folder(uid), f"script_{slot}", fname)

    for adm in list(admin_ids):
        try:
            if os.path.exists(file_path):
                # ★ একটাই message — document + caption + Approve/Reject বাটন
                with open(file_path, "rb") as f:
                    bot.send_document(
                        adm, f,
                        caption=caption,
                        reply_markup=kb_approval(aid),
                        parse_mode="HTML"
                    )
            else:
                # ফাইল না থাকলে শুধু text message
                bot.send_message(
                    adm, caption,
                    reply_markup=kb_approval(aid),
                    parse_mode="HTML"
                )
        except Exception as e:
            logger.warning(f"notify_admins_approval admin={adm}: {e}")

def notify_admins_payment(uid, pay_id, months, amount, method, screenshot_id, username, msg):
    """
    অ্যাডমিনদের নতুন পেমেন্ট রিকোয়েস্ট জানায়।
    ★ একটাই message — screenshot (photo) + caption + Approve/Reject বাটন
    """
    labels = {1: "১ মাস", 3: "৩ মাস", 6: "৬ মাস", 12: "১ বছর"}
    caption = (
        "⟡ <b>নতুন পেমেন্ট রিকোয়েস্ট</b> ⟡\n\n"
        f"👤 ইউজার: @{h(username)}\n"
        f"🆔 ID: <code>{uid}</code>\n"
        f"📅 প্ল্যান: <b>{labels.get(months, str(months)+'m')}</b>\n"
        f"💰 পরিমাণ: <b>${amount:.0f} USDT</b>\n"
        f"💳 পদ্ধতি: <code>{h(method)}</code>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━"
    )
    for adm in list(admin_ids):
        try:
            # ★ একটাই message — photo + caption + বাটন
            sent = False
            try:
                bot.send_photo(
                    adm, screenshot_id,
                    caption=caption,
                    reply_markup=kb_pay_approval(pay_id),
                    parse_mode="HTML"
                )
                sent = True
            except Exception:
                pass
            # photo না হলে document হিসেবে পাঠাও
            if not sent:
                try:
                    bot.send_document(
                        adm, screenshot_id,
                        caption=caption,
                        reply_markup=kb_pay_approval(pay_id),
                        parse_mode="HTML"
                    )
                    sent = True
                except Exception:
                    pass
            # শেষ চেষ্টা — শুধু text
            if not sent:
                bot.send_message(
                    adm, caption,
                    reply_markup=kb_pay_approval(pay_id),
                    parse_mode="HTML"
                )
        except Exception as e:
            logger.warning(f"notify_admins_payment admin={adm}: {e}")

# ═══════════════════════════════════════════════════════════════
# SCRIPT RUNNER
# ═══════════════════════════════════════════════════════════════
TELEGRAM_MODULES = {
    "telebot": "pyTelegramBotAPI", "telegram": "python-telegram-bot",
    "aiogram": "aiogram", "pyrogram": "pyrogram", "telethon": "telethon",
    "bs4": "beautifulsoup4", "cv2": "opencv-python", "yaml": "PyYAML",
    "dotenv": "python-dotenv", "pandas": "pandas", "numpy": "numpy",
    "flask": "Flask", "requests": "requests", "aiohttp": "aiohttp",
    "PIL": "Pillow", "psutil": "psutil", "tgcrypto": "tgcrypto",
    "asyncio": None, "json": None, "os": None, "sys": None, "re": None,
    "time": None, "math": None, "random": None, "logging": None,
    "threading": None, "subprocess": None, "zipfile": None, "sqlite3": None,
}

def _pip_silent(pkg):
    try:
        res = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--break-system-packages", pkg],
            capture_output=True, text=True, encoding="utf-8", errors="ignore"
        )
        return res.returncode == 0
    except Exception:
        return False

def run_script(uid, slot, reply_msg, attempt=1):
    fi = get_file_by_slot(uid, slot)
    if not fi:
        safe_send(reply_msg.chat.id, "❌ স্ক্রিপ্ট খুঁজে পাওয়া যায়নি।")
        return
    key    = f"{uid}_{slot}"
    folder = os.path.join(get_user_folder(uid), f"script_{slot}")
    fname  = fi["file_name"]
    ftype  = fi["file_type"]

    if ftype == "py":
        path = os.path.join(folder, fname)
        cmd  = [sys.executable, path]
    elif ftype == "js":
        path = os.path.join(folder, fname)
        cmd  = ["node", path]
    else:
        main_file = None
        for c in ["main.py", "app.py", "bot.py", "index.py", "main.js", "index.js", "app.js"]:
            if os.path.exists(os.path.join(folder, c)):
                main_file = c
                break
        if not main_file:
            pyf = [f for f in os.listdir(folder) if f.endswith(".py")]
            jsf = [f for f in os.listdir(folder) if f.endswith(".js")]
            main_file = (pyf or jsf or [None])[0]
        if not main_file:
            safe_send(reply_msg.chat.id, "❌ ZIP-এ main.py/main.js খুঁজে পাওয়া যায়নি।")
            return
        path = os.path.join(folder, main_file)
        cmd  = [sys.executable, path] if main_file.endswith(".py") else ["node", path]

    if not os.path.exists(path):
        safe_send(reply_msg.chat.id, f"❌ ফাইল <code>{h(fname)}</code> পাওয়া যায়নি।")
        return
    if attempt > 2:
        safe_send(reply_msg.chat.id, f"❌ <code>{h(fname)}</code> স্টার্ট করা সম্ভব হয়নি।")
        return

    if attempt == 1:
        chk = None
        try:
            chk = subprocess.Popen(
                cmd, cwd=folder,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="ignore"
            )
            _, err = chk.communicate(timeout=6)
            if chk.returncode != 0 and err:
                mo = re.search(r"ModuleNotFoundError: No module named '(.+?)'", err)
                if mo:
                    mod = mo.group(1).strip().strip("'\"")
                    pkg = TELEGRAM_MODULES.get(mod.lower(), mod)
                    if pkg and _pip_silent(pkg):
                        time.sleep(1)
                        threading.Thread(
                            target=run_script, args=(uid, slot, reply_msg, 2), daemon=True
                        ).start()
                    else:
                        safe_send(reply_msg.chat.id, f"❌ প্যাকেজ <code>{h(pkg)}</code> ইনস্টল ব্যর্থ।")
                    return
                safe_send(reply_msg.chat.id, f"❌ স্ক্রিপ্ট এরর:\n<pre>{h(err[:600])}</pre>")
                return
        except subprocess.TimeoutExpired:
            if chk:
                chk.kill()
                chk.communicate()
        except FileNotFoundError:
            safe_send(reply_msg.chat.id, "❌ Python/Node খুঁজে পাওয়া যায়নি।")
            return
        finally:
            if chk and chk.poll() is None:
                chk.kill()
                chk.communicate()

    log_path = os.path.join(folder, "logs.txt")
    try:
        lf = open(log_path, "w", encoding="utf-8", errors="ignore")
    except Exception as e:
        safe_send(reply_msg.chat.id, f"❌ লগ ফাইল তৈরিতে সমস্যা: {e}")
        return
    try:
        proc = subprocess.Popen(
            cmd, cwd=folder, stdout=lf, stderr=lf,
            text=True, encoding="utf-8", errors="ignore"
        )
    except FileNotFoundError:
        lf.close()
        safe_send(reply_msg.chat.id, "❌ Python/Node খুঁজে পাওয়া যায়নি।")
        return

    bot_scripts[key] = {
        "proc": proc, "log_file": lf, "fname": fname,
        "folder": folder, "started": datetime.now()
    }
    db_update_file_status(uid, slot, "running")
    safe_send(
        reply_msg.chat.id,
        f"✅ <b>সিস্টেম ডেপ্লয়মেন্ট সম্পন্ন!</b>\n\n"
        f"🟢 <b>{h(fname)}</b> চালু হয়েছে!\n"
        f"🔢 PID: <code>{proc.pid}</code>"
    )
    logger.info(f"Script started: uid={uid} slot={slot} pid={proc.pid}")
    if is_premium(uid):
        threading.Thread(
            target=_auto_restart_monitor, args=(uid, slot, reply_msg), daemon=True
        ).start()

def _auto_restart_monitor(uid, slot, reply_msg, crash_count=0):
    """Premium ইউজারের স্ক্রিপ্ট crash হলে অটো রিস্টার্ট।"""
    while True:
        time.sleep(30)
        if not is_premium(uid):
            break
        key  = f"{uid}_{slot}"
        info = bot_scripts.get(key)
        if not info:
            break
        proc = info.get("proc")
        if proc and proc.poll() is not None:
            crash_count += 1
            if crash_count > 5:
                try:
                    bot.send_message(
                        uid,
                        f"⚠️ <code>{h(info.get('fname',''))}</code> ৫ বার crash করেছে। Manual check করুন।",
                        parse_mode="HTML"
                    )
                except Exception:
                    pass
                break
            logger.warning(f"Script crashed #{crash_count}: uid={uid} slot={slot}")
            try:
                bot.send_message(
                    uid,
                    f"⚠️ <code>{h(info.get('fname',''))}</code> crash করেছে। পুনরায় চালু হচ্ছে...",
                    parse_mode="HTML"
                )
            except Exception:
                pass
            run_script(uid, slot, reply_msg)
            break

def _stop_script_key(uid, slot):
    key = f"{uid}_{slot}"
    if key not in bot_scripts:
        return False
    info = bot_scripts.pop(key)
    try:
        kill_process_tree(info["proc"].pid)
    except Exception:
        pass
    try:
        info["log_file"].close()
    except Exception:
        pass
    db_update_file_status(uid, slot, "stopped")
    return True

def _delete_slot_hard(uid, slot):
    """Hard delete: প্রসেস বন্ধ + ফোল্ডার মুছে + DB এন্ট্রি মুছে।"""
    _stop_script_key(uid, slot)
    folder = os.path.join(get_user_folder(uid), f"script_{slot}")
    if os.path.exists(folder):
        shutil.rmtree(folder, ignore_errors=True)
    db_del_file(uid, slot)
    logger.info(f"Hard delete: uid={uid} slot={slot}")

def handle_zip_extract(content, fname, uid, slot, folder, reply_msg):
    """ZIP ফাইল extract করে।"""
    try:
        zp = os.path.join(folder, fname)
        with open(zp, "wb") as f:
            f.write(content)
        with zipfile.ZipFile(zp, "r") as zf:
            zf.extractall(folder)
        os.remove(zp)
        db_update_file_status(uid, slot, "approved")
        threading.Thread(target=run_script, args=(uid, slot, reply_msg), daemon=True).start()
    except zipfile.BadZipFile:
        safe_send(reply_msg.chat.id, "❌ ZIP ফাইলটি ক্ষতিগ্রস্ত।")
        db_update_file_status(uid, slot, "rejected")
    except Exception as e:
        safe_send(reply_msg.chat.id, f"❌ ZIP extract ব্যর্থ: {h(str(e))}")

# ═══════════════════════════════════════════════════════════════
# BACKGROUND THREADS
# ═══════════════════════════════════════════════════════════════
def _expiry_checker():
    """Premium মেয়াদ চেক করে — ৩ দিন ও ১ দিন আগে নোটিফিকেশন।"""
    while True:
        try:
            now = datetime.now()
            for uid, sub in list(user_subscriptions.items()):
                if uid in admin_ids:
                    continue
                expiry = sub["expiry"]
                days_left = (expiry - now).days

                # ৩ দিন আগে ওয়ার্নিং
                if days_left == 3 and not db_expiry_notif_sent(uid, "3day"):
                    try:
                        bot.send_message(
                            uid,
                            "⚠️ <b>প্রিমিয়াম মেয়াদ সতর্কতা!</b>\n\n"
                            "আপনার Premium মেয়াদ <b>৩ দিন</b> পরে শেষ হবে।\n"
                            "মেয়াদ বাড়াতে নতুন প্ল্যান কিনুন।",
                            parse_mode="HTML"
                        )
                        db_mark_expiry_notif(uid, "3day")
                    except Exception:
                        pass

                # ১ দিন আগে ফাইনাল ওয়ার্নিং
                elif days_left == 1 and not db_expiry_notif_sent(uid, "1day"):
                    try:
                        bot.send_message(
                            uid,
                            "🔴 <b>ফাইনাল সতর্কতা!</b>\n\n"
                            "আপনার Premium মেয়াদ <b>আগামীকাল</b> শেষ হবে।\n"
                            "মেয়াদ না বাড়ালে সব স্ক্রিপ্ট বন্ধ হয়ে যাবে!",
                            parse_mode="HTML"
                        )
                        db_mark_expiry_notif(uid, "1day")
                    except Exception:
                        pass

                # মেয়াদ শেষ — সব স্ক্রিপ্ট বন্ধ করো
                elif expiry <= now and not db_expiry_notif_sent(uid, "expired"):
                    files = user_files_db.get(uid, [])
                    for f in files:
                        _stop_script_key(uid, f["slot"])
                    try:
                        bot.send_message(
                            uid,
                            "🔴 <b>প্রিমিয়াম মেয়াদ শেষ!</b>\n\n"
                            "আপনার সব স্ক্রিপ্ট বন্ধ করা হয়েছে।\n"
                            "আবার Premium নিন।",
                            parse_mode="HTML"
                        )
                        db_mark_expiry_notif(uid, "expired")
                    except Exception:
                        pass
        except Exception as e:
            logger.error(f"expiry_checker error: {e}")
        time.sleep(3600)  # প্রতি ঘণ্টায় চেক করে

def _cleanup_thread():
    """Orphan প্রসেস পরিষ্কার করে।"""
    while True:
        try:
            for key in list(bot_scripts.keys()):
                info = bot_scripts.get(key)
                if info and info.get("proc") and info["proc"].poll() is not None:
                    try:
                        info["log_file"].close()
                    except Exception:
                        pass
                    parts = key.split("_")
                    if len(parts) == 2:
                        uid, slot = int(parts[0]), int(parts[1])
                        db_update_file_status(uid, slot, "stopped")
                    bot_scripts.pop(key, None)
        except Exception as e:
            logger.error(f"cleanup_thread error: {e}")
        time.sleep(60)

# ═══════════════════════════════════════════════════════════════
# FLASK KEEP-ALIVE
# ═══════════════════════════════════════════════════════════════
flask_app = Flask(__name__)

@flask_app.route("/")
def index():
    return jsonify({
        "status": "running",
        "uptime": fmt_uptime(),
        "users": len(active_users),
        "scripts_running": sum(
            1 for v in bot_scripts.values()
            if v.get("proc") and v["proc"].poll() is None
        )
    })

def _run_flask():
    flask_app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)

# ═══════════════════════════════════════════════════════════════
# COMMAND HANDLERS
# ═══════════════════════════════════════════════════════════════
@bot.message_handler(commands=["start"])
def cmd_start(msg):
    uid      = msg.from_user.id
    username = msg.from_user.username or ""
    fname    = msg.from_user.first_name or "User"
    name     = username or fname

    if uid in banned_users:
        bot.reply_to(msg, "🚫 আপনাকে এই বট থেকে ব্যান করা হয়েছে।")
        return
    if maintenance_mode and uid not in admin_ids:
        bot.reply_to(msg, "🔧 বটটি রক্ষণাবেক্ষণে আছে। পরে চেষ্টা করুন।")
        return

    db_add_user(uid, username, fname)
    get_user_folder(uid)  # ইউজার ফোল্ডার তৈরি
    nav_clear(uid)
    bot.send_message(uid, build_welcome(uid, name), reply_markup=kb_main(uid), parse_mode="HTML")

@bot.message_handler(commands=["status"])
def cmd_status_cmd(msg):
    uid  = msg.from_user.id
    name = msg.from_user.username or str(uid)
    bot.reply_to(msg, build_status(uid, name), parse_mode="HTML")

@bot.message_handler(commands=["ping"])
def cmd_ping(msg):
    bot.reply_to(
        msg,
        f"🏓 Pong! Uptime: <code>{fmt_uptime()}</code>",
        parse_mode="HTML"
    )

@bot.message_handler(commands=["help"])
def cmd_help_cmd(msg):
    bot.reply_to(msg, build_help(), parse_mode="HTML")

@bot.message_handler(commands=["cancel"])
def cmd_cancel(msg):
    uid = msg.from_user.id
    session_state.pop(uid, None)
    broadcast_state.pop(uid, None)
    payment_state.pop(uid, None)
    _reject_state.pop(uid, None)
    _pending_update_slot.pop(uid, None)
    bot.reply_to(msg, "✅ বাতিল করা হয়েছে।")

@bot.message_handler(commands=["stats"])
def cmd_stats(msg):
    if msg.from_user.id not in admin_ids:
        return
    bot.reply_to(msg, build_server_stats(), parse_mode="HTML")

@bot.message_handler(commands=["setpremium"])
def cmd_setpremium(msg):
    if msg.from_user.id not in admin_ids:
        return
    parts = msg.text.split()
    if len(parts) < 4:
        bot.reply_to(msg, "❌ ব্যবহার: <code>/setpremium USER_ID LIMIT DAYS</code>", parse_mode="HTML")
        return
    try:
        target, lim, days = int(parts[1]), int(parts[2]), int(parts[3])
    except ValueError:
        bot.reply_to(msg, "❌ সংখ্যা দিন।")
        return
    exp = datetime.now() + timedelta(days=days)
    db_save_sub(target, lim, exp, msg.from_user.id)
    db_clear_expiry_notifs(target)
    bot.reply_to(
        msg,
        f"✅ ইউজার <code>{target}</code>-কে Premium দেওয়া হয়েছে।",
        parse_mode="HTML"
    )
    try:
        bot.send_message(
            target,
            f"🎉 <b>আপনার Premium অ্যাক্টিভ হয়েছে!</b>\n"
            f"🔢 লিমিট: {lim}\n"
            f"📅 মেয়াদ: {exp.strftime('%Y-%m-%d')}",
            parse_mode="HTML"
        )
    except Exception:
        pass

@bot.message_handler(commands=["delpremium"])
def cmd_delpremium(msg):
    if msg.from_user.id not in admin_ids:
        return
    parts = msg.text.split()
    if len(parts) < 2:
        bot.reply_to(msg, "❌ /delpremium USER_ID")
        return
    try:
        target = int(parts[1])
    except ValueError:
        bot.reply_to(msg, "❌ সংখ্যা দিন.")
        return
    db_del_sub(target)
    bot.reply_to(
        msg,
        f"✅ ইউজার <code>{target}</code>-এর Premium বাতিল।",
        parse_mode="HTML"
    )

@bot.message_handler(commands=["ban"])
def cmd_ban(msg):
    if msg.from_user.id not in admin_ids:
        return
    parts = msg.text.split(maxsplit=2)
    if len(parts) < 2:
        bot.reply_to(msg, "❌ /ban USER_ID [reason]")
        return
    try:
        target = int(parts[1])
    except ValueError:
        bot.reply_to(msg, "❌ সংখ্যা দিন.")
        return
    if target == OWNER_ID:
        bot.reply_to(msg, "❌ Owner-কে ব্যান করা যাবে না.")
        return
    reason = parts[2] if len(parts) > 2 else "বট নীতি লঙ্ঘন"
    db_ban_user(target, reason, msg.from_user.id)
    bot.reply_to(msg, f"✅ ইউজার <code>{target}</code> ব্যান হয়েছে।", parse_mode="HTML")

@bot.message_handler(commands=["unban"])
def cmd_unban(msg):
    if msg.from_user.id not in admin_ids:
        return
    parts = msg.text.split()
    if len(parts) < 2:
        bot.reply_to(msg, "❌ /unban USER_ID")
        return
    try:
        target = int(parts[1])
    except ValueError:
        bot.reply_to(msg, "❌ সংখ্যা দিন.")
        return
    db_unban_user(target)
    bot.reply_to(msg, f"✅ ইউজার <code>{target}</code> আনব্যান হয়েছে।", parse_mode="HTML")

@bot.message_handler(commands=["search"])
def cmd_search(msg):
    if msg.from_user.id not in admin_ids:
        return
    parts = msg.text.split()
    if len(parts) < 2:
        bot.reply_to(msg, "❌ /search USER_ID")
        return
    try:
        target = int(parts[1])
    except ValueError:
        bot.reply_to(msg, "❌ সংখ্যা দিন.")
        return
    bot.send_message(
        msg.chat.id,
        build_user_profile(target),
        reply_markup=kb_user_profile_admin(target),
        parse_mode="HTML"
    )

@bot.message_handler(commands=["addadmin"])
def cmd_addadmin(msg):
    if msg.from_user.id != OWNER_ID:
        return
    parts = msg.text.split()
    if len(parts) < 2:
        bot.reply_to(msg, "❌ /addadmin USER_ID")
        return
    try:
        target = int(parts[1])
    except ValueError:
        bot.reply_to(msg, "❌ সংখ্যা দিন.")
        return
    db_add_admin(target, msg.from_user.id)
    bot.reply_to(msg, f"✅ ইউজার <code>{target}</code> অ্যাডমিন হয়েছে।", parse_mode="HTML")

@bot.message_handler(commands=["removeadmin"])
def cmd_removeadmin(msg):
    if msg.from_user.id != OWNER_ID:
        return
    parts = msg.text.split()
    if len(parts) < 2:
        bot.reply_to(msg, "❌ /removeadmin USER_ID")
        return
    try:
        target = int(parts[1])
    except ValueError:
        bot.reply_to(msg, "❌ সংখ্যা দিন.")
        return
    if target == OWNER_ID:
        bot.reply_to(msg, "❌ Owner-কে বাদ দেওয়া যাবে না.")
        return
    db_del_admin(target)
    bot.reply_to(
        msg,
        f"✅ ইউজার <code>{target}</code> অ্যাডমিন তালিকা থেকে বাদ।",
        parse_mode="HTML"
    )

@bot.message_handler(commands=["broadcast"])
def cmd_broadcast_cmd(msg):
    if msg.from_user.id not in admin_ids:
        return
    parts = msg.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(msg, "❌ /broadcast মেসেজ")
        return
    text = parts[1]
    ok = fail = 0
    for uid in list(active_users):
        try:
            bot.send_message(uid, text)
            ok += 1
        except Exception:
            fail += 1
        time.sleep(0.05)
    bot.reply_to(msg, f"✅ সফল: {ok} | ❌ ব্যর্থ: {fail}")

# ── ফাইল আপলোড হ্যান্ডলার ──────────────────────────────────────
@bot.message_handler(content_types=["document"])
def handle_document(msg):
    uid = msg.from_user.id
    if uid in banned_users:
        return
    if maintenance_mode and uid not in admin_ids:
        bot.reply_to(msg, "🔧 রক্ষণাবেক্ষণ চলছে।")
        return

    update_slot = _pending_update_slot.pop(uid, None)
    doc   = msg.document
    fname = doc.file_name or "script.py"
    ext   = os.path.splitext(fname)[1].lower()

    if ext not in (".py", ".js", ".zip"):
        bot.reply_to(
            msg,
            "❌ শুধু <code>.py</code>, <code>.js</code>, <code>.zip</code> ফাইল সমর্থিত।",
            parse_mode="HTML"
        )
        return

    ftype      = ext[1:]
    size_bytes = doc.file_size or 0
    size_kb    = size_bytes / 1024

    if size_bytes > 20 * 1024 * 1024:
        bot.reply_to(msg, "❌ ফাইল সাইজ সর্বোচ্চ ২০MB।")
        return

    if update_slot is None:
        files        = user_files_db.get(uid, [])
        active_files = [f for f in files if f["status"] != "rejected"]
        lim          = get_bot_limit(uid)
        if len(active_files) >= lim and uid not in admin_ids:
            bot.reply_to(
                msg,
                f"❌ স্ক্রিপ্ট সীমা পূর্ণ! ({lim}টি)\n💎 আরো স্ক্রিপ্টের জন্য Premium নিন."
            )
            return
        for f in files:
            if f["file_name"] == fname and f["status"] != "rejected":
                bot.reply_to(
                    msg,
                    f"⚠️ <code>{h(fname)}</code> নামে ফাইল আছে। "
                    "আপডেট করতে স্ক্রিপ্টের 🔄 বাটন ব্যবহার করুন।",
                    parse_mode="HTML"
                )
                return

    used_mb  = get_user_storage_mb(uid)
    stor_lim = get_storage_limit(uid)
    if used_mb + size_kb / 1024 > stor_lim and uid not in admin_ids:
        bot.reply_to(msg, f"❌ Storage পূর্ণ! {used_mb:.1f}MB/{stor_lim}MB")
        return

    prog = bot.reply_to(msg, "⏳ ফাইল ডাউনলোড হচ্ছে...")
    try:
        fi      = bot.get_file(doc.file_id)
        content = bot.download_file(fi.file_path)
    except Exception as e:
        safe_send(msg.chat.id, f"❌ ডাউনলোড ব্যর্থ: {h(str(e))}")
        return
    finally:
        try:
            bot.delete_message(msg.chat.id, prog.message_id)
        except Exception:
            pass

    slot   = update_slot if update_slot else get_next_slot(uid)
    folder = create_script_folder(uid, slot)

    # আপডেটের সময় পুরনো ফোল্ডার পরিষ্কার
    if update_slot:
        for item in os.listdir(folder):
            ip = os.path.join(folder, item)
            try:
                shutil.rmtree(ip) if os.path.isdir(ip) else os.remove(ip)
            except Exception:
                pass

    file_path = os.path.join(folder, fname)
    with open(file_path, "wb") as f:
        f.write(content)

    username = msg.from_user.username or str(uid)
    aid = db_add_pending_approval(uid, fname, ftype, file_path, slot, size_kb)
    db_save_file(uid, slot, fname, ftype, f"script_{slot}", size_kb, status="pending")

    if ftype == "zip":
        _pending_zip_data[aid] = content

    # ফেক স্ক্যান ও অ্যাডমিন নোটিফিকেশন parallel-এ চালাও
    threading.Thread(
        target=send_fake_security_scan, args=(msg.chat.id,), daemon=True
    ).start()
    threading.Thread(
        target=notify_admins_approval,
        args=(uid, fname, ftype, slot, aid, size_kb, username, is_premium(uid)),
        daemon=True
    ).start()
    logger.info(f"Upload pending: uid={uid} slot={slot} file={fname} aid={aid}")

# ── ছবি হ্যান্ডলার (পেমেন্ট স্ক্রিনশট) ─────────────────────────
@bot.message_handler(content_types=["photo"])
def handle_photo(msg):
    uid = msg.from_user.id
    if uid in payment_state:
        _receive_payment_screenshot(msg, uid)

# ═══════════════════════════════════════════════════════════════
# ★ CALLBACK ROUTER — style prefix strip করা হয়েছে
# ═══════════════════════════════════════════════════════════════
@bot.callback_query_handler(func=lambda c: True)
def cb_router(call):
    uid  = call.from_user.id
    data = call.data

    # ★ Style prefix strip করো (p|, s|, d|, e|)
    data = _strip_style(data)

    if data == "noop":
        bot.answer_callback_query(call.id)
        return
    if uid in banned_users and data not in ("nav_back",):
        bot.answer_callback_query(call.id, "🚫 ব্যান হয়েছেন।", show_alert=True)
        return
    if bot_locked and uid not in admin_ids:
        bot.answer_callback_query(call.id, "🔒 বট লক।", show_alert=True)
        return
    try:
        _cb(call, uid, data)
    except Exception as e:
        logger.error(f"CB error: uid={uid} data={data} err={e}")
        bot.answer_callback_query(call.id, "⚠️ সমস্যা হয়েছে।", show_alert=True)

def _cb(call, uid, data):
    cid  = call.message.chat.id
    mid  = call.message.message_id
    name = call.from_user.username or call.from_user.first_name or "User"

    # ── Navigation ──
    if data == "nav_back":
        bot.answer_callback_query(call.id)
        nav_back(call)

    # ── মেইন স্ক্রিন ──
    elif data == "user_panel":
        bot.answer_callback_query(call.id)
        nav_push(uid, "main")
        safe_edit(cid, mid, build_user_panel(uid, name), kb_user_panel(uid))

    elif data == "my_scripts":
        bot.answer_callback_query(call.id)
        nav_push(uid, "user_panel")
        safe_edit(cid, mid, build_my_scripts(uid), kb_my_scripts(uid))

    elif data == "upload_file":
        bot.answer_callback_query(call.id)
        nav_push(uid, "main")
        safe_edit(
            cid, mid,
            "📁 <b>ফাইল আপলোড করুন</b>\n\n"
            "◆ <code>.py</code> — Python স্ক্রিপ্ট\n"
            "◆ <code>.js</code> — Node.js স্ক্রিপ্ট\n"
            "◆ <code>.zip</code> — পুরো প্রজেক্ট (max 20MB)\n\n"
            "<i>এখন ফাইল পাঠান...</i>",
            kb_back()
        )

    elif data == "user_status":
        bot.answer_callback_query(call.id)
        safe_send(cid, build_status(uid, name))

    elif data == "help_menu":
        bot.answer_callback_query(call.id)
        nav_push(uid, "main")
        safe_edit(cid, mid, build_help(), kb_back())

    elif data == "buy_premium":
        bot.answer_callback_query(call.id)
        nav_push(uid, "user_panel")
        safe_edit(cid, mid, build_premium_plans(), kb_premium_plans())

    elif data == "session_menu":
        if not is_premium(uid):
            bot.answer_callback_query(call.id, "💎 Premium প্রয়োজন।", show_alert=True)
            return
        bot.answer_callback_query(call.id)
        nav_push(uid, "main")
        safe_edit(cid, mid, build_session_menu_text(uid), kb_session_menu())

    elif data == "admin_panel":
        if uid not in admin_ids:
            bot.answer_callback_query(call.id, "❌ অ্যাডমিন নন।", show_alert=True)
            return
        bot.answer_callback_query(call.id)
        nav_push(uid, "main")
        safe_edit(cid, mid, build_admin_panel(), kb_admin())

    # ── ফাইল কন্ট্রোল ──
    elif data.startswith("fctl_"):
        slot = int(data[5:])
        bot.answer_callback_query(call.id)
        nav_push(uid, "my_scripts")
        safe_edit(cid, mid, build_file_control(uid, slot), kb_file_ctrl(uid, slot))

    elif data.startswith("fstart_"):
        slot = int(data[7:])
        fi   = get_file_by_slot(uid, slot)
        if fi and fi["status"] == "pending":
            bot.answer_callback_query(call.id, "⏳ এখনো যাচাই চলছে।", show_alert=True)
            return
        if fi and fi["status"] == "rejected":
            bot.answer_callback_query(call.id, "❌ ফাইল যাচাই ব্যর্থ হয়েছে।", show_alert=True)
            return
        bot.answer_callback_query(call.id, "▶️ চালু হচ্ছে...")
        threading.Thread(target=run_script, args=(uid, slot, call.message), daemon=True).start()

    elif data.startswith("fstop_"):
        slot = int(data[6:])
        bot.answer_callback_query(call.id)
        if _stop_script_key(uid, slot):
            fi = get_file_by_slot(uid, slot)
            safe_send(
                cid,
                f"⏹️ <b>{h(fi['file_name'] if fi else str(slot))}</b> বন্ধ করা হয়েছে."
            )
        else:
            bot.answer_callback_query(call.id, "⚠️ প্রসেস পাওয়া যায়নি।", show_alert=True)
            return
        safe_edit(cid, mid, build_file_control(uid, slot), kb_file_ctrl(uid, slot))

    elif data.startswith("frestart_"):
        slot = int(data[9:])
        bot.answer_callback_query(call.id, "🔄 রিস্টার্ট হচ্ছে...")
        _stop_script_key(uid, slot)
        threading.Thread(target=run_script, args=(uid, slot, call.message), daemon=True).start()

    elif data.startswith("flogs_"):
        slot = int(data[6:])
        bot.answer_callback_query(call.id)
        fi       = get_file_by_slot(uid, slot)
        log_path = os.path.join(get_user_folder(uid), f"script_{slot}", "logs.txt")
        if not fi or not os.path.exists(log_path):
            safe_send(cid, "📭 এখনো কোনো লগ নেই.")
            return
        try:
            content = open(log_path, "r", encoding="utf-8", errors="ignore").read()
            if not content.strip():
                safe_send(cid, "📭 লগ ফাইল ফাঁকা.")
                return
            safe_send(cid, f"📜 <b>{h(fi['file_name'])} — লগ</b>\n\n<pre>{h(content[-3000:])}</pre>")
        except Exception as e:
            safe_send(cid, f"❌ লগ পড়তে ব্যর্থ: {e}")

    elif data.startswith("fspeed_"):
        slot = int(data[7:])
        bot.answer_callback_query(call.id)
        key  = f"{uid}_{slot}"
        info = bot_scripts.get(key)
        if not info or not info.get("proc") or info["proc"].poll() is not None:
            safe_send(cid, "⚠️ স্ক্রিপ্ট চলছে না.")
            return
        try:
            p   = psutil.Process(info["proc"].pid)
            cpu = p.cpu_percent(interval=0.5)
            ram = p.memory_info().rss / 1024 / 1024
            el  = datetime.now() - info.get("started", datetime.now())
            d, r = divmod(int(el.total_seconds()), 86400)
            h2, r = divmod(r, 3600)
            m2, s = divmod(r, 60)
            safe_send(
                cid,
                f"⚡ <b>Live Stats — {h(info.get('fname',''))}</b>\n\n"
                f"🖥 CPU: <code>{cpu:.1f}%</code>\n"
                f"🧠 RAM: <code>{ram:.1f} MB</code>\n"
                f"🔢 PID: <code>{info['proc'].pid}</code>\n"
                f"⏱ চলছে: <code>{d}d {h2}h {m2}m {s}s</code>"
            )
        except Exception as e:
            safe_send(cid, f"❌ Stats নেওয়া ব্যর্থ: {e}")

    elif data.startswith("fupdate_"):
        slot = int(data[8:])
        fi   = get_file_by_slot(uid, slot)
        bot.answer_callback_query(call.id)
        _stop_script_key(uid, slot)
        _pending_update_slot[uid] = slot
        safe_edit(
            cid, mid,
            f"🔄 <b>আপডেট — {h(fi['file_name'] if fi else str(slot))}</b>\n\n"
            "নতুন ফাইল পাঠান (পুরনো ফাইল মুছে যাবে):",
            kb_back()
        )

    elif data.startswith("fdelete_"):
        if "_confirm_" in data:
            slot = int(data.split("_confirm_")[1])
            bot.answer_callback_query(call.id, "🗑️ ডিলিট হচ্ছে...")
            _delete_slot_hard(uid, slot)
            safe_edit(cid, mid, build_my_scripts(uid), kb_my_scripts(uid))
        else:
            slot = int(data[8:])
            fi   = get_file_by_slot(uid, slot)
            bot.answer_callback_query(call.id)
            safe_edit(
                cid, mid,
                f"⚠️ <b>নিশ্চিত করুন</b>\n\n"
                f"<code>{h(fi['file_name'] if fi else str(slot))}</code> স্থায়ীভাবে ডিলিট করবেন?\n"
                "ফাইল, ফোল্ডার ও সব ডেটা মুছে যাবে।",
                kb_confirm_delete(slot)
            )

    # ── প্রিমিয়াম প্ল্যান ──
    elif data.startswith("plan_select_"):
        months = int(data[12:])
        bot.answer_callback_query(call.id)
        nav_push(uid, "buy_premium")
        prices  = {p["months"]: p["price_usd"] for p in db_get_plan_prices()}
        labels  = {1: "১ মাস", 3: "৩ মাস", 6: "৬ মাস", 12: "১ বছর"}
        price   = prices.get(months, 0)
        methods = [
            (r["method_name"], r["number_or_address"])
            for r in db_get_payment_settings()
            if r["is_active"] and r["number_or_address"]
        ]
        if not methods:
            safe_edit(
                cid, mid,
                f"⚠️ কোনো পেমেন্ট মেথড সেট নেই।\nঅ্যাডমিনের সাথে যোগাযোগ করুন: {ADMIN_USERNAME}",
                kb_back()
            )
            return
        safe_edit(
            cid, mid,
            f"⟡ <b>পেমেন্ট পদ্ধতি</b> ⟡\n\n"
            f"📅 নির্বাচিত: <b>{h(labels.get(months,''))} (${price:.0f} USDT)</b>\n\n"
            "পেমেন্ট পদ্ধতি বেছে নিন:",
            kb_pay_methods_user(months)
        )

    elif data.startswith("pay_method_"):
        parts  = data[11:].split("_", 1)
        months = int(parts[0])
        method = parts[1]
        bot.answer_callback_query(call.id)
        prices  = {p["months"]: p["price_usd"] for p in db_get_plan_prices()}
        price   = prices.get(months, 0)
        methods = {r["method_name"]: r for r in db_get_payment_settings()}
        mi      = methods.get(method, {})
        number  = mi.get("number_or_address", "-")
        dname   = mi.get("display_name", method)
        icons   = {"bkash": "💛", "nagad": "🟠", "rocket": "🟣", "usdt": "🟡"}
        icon    = icons.get(method, "💳")
        safe_edit(
            cid, mid,
            f"⟡ <b>পেমেন্ট নির্দেশনা</b> ⟡\n\n"
            f"{icon} <b>{h(dname)}-এ পেমেন্ট করুন</b>\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📱 নম্বর/ঠিকানা: <code>{h(number)}</code>\n"
            f"💰 পরিমাণ: <b>${price:.0f} USDT</b>\n"
            f"📝 Reference: <code>UID_{uid}</code>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "📌 <b>নির্দেশনা:</b>\n"
            "১. উপরের নম্বরে পেমেন্ট করুন\n"
            "২. Reference-এ আপনার ID লিখুন\n"
            "৩. Screenshot নিন\n"
            "৪. Screenshot এই চ্যাটে পাঠান\n\n"
            "<i>এখন payment screenshot পাঠান...</i>",
            kb_back()
        )
        payment_state[uid] = {"months": months, "amount": price, "method": dname}

    # ── অ্যাডমিন ফাইল অ্যাপ্রুভাল ──
    elif data.startswith("approve_"):
        aid = int(data[8:])
        bot.answer_callback_query(call.id, "✅ অ্যাপ্রুভ হচ্ছে...")
        _cb_approve_file(call, aid)

    elif data.startswith("reject_"):
        aid = int(data[7:])
        bot.answer_callback_query(call.id)
        _cb_reject_file_ask(call, aid)

    elif data.startswith("pay_approve_"):
        pid = int(data[12:])
        bot.answer_callback_query(call.id, "✅ অ্যাপ্রুভ হচ্ছে...")
        _cb_approve_payment(call, pid)

    elif data.startswith("pay_reject_"):
        pid = int(data[11:])
        bot.answer_callback_query(call.id, "❌ রিজেক্ট হচ্ছে...")
        _cb_reject_payment(call, pid)

    # ── অ্যাডমিন প্যানেল ──
    elif data == "adm_approvals":
        if uid not in admin_ids:
            return
        bot.answer_callback_query(call.id)
        nav_push(uid, "admin_panel")
        _cb_adm_approvals(call)

    elif data == "adm_payments":
        if uid not in admin_ids:
            return
        bot.answer_callback_query(call.id)
        nav_push(uid, "admin_panel")
        _cb_adm_payments(call)

    elif data == "adm_users":
        if uid not in admin_ids:
            return
        bot.answer_callback_query(call.id)
        nav_push(uid, "admin_panel")
        _render_adm_users(call, 0)

    elif data.startswith("adm_users_page_"):
        if uid not in admin_ids:
            return
        bot.answer_callback_query(call.id)
        page = int(data[15:])
        _render_adm_users(call, page)

    elif data.startswith("adm_view_user_"):
        if uid not in admin_ids:
            return
        target = int(data[14:])
        bot.answer_callback_query(call.id)
        nav_push(uid, "adm_users")
        safe_edit(cid, mid, build_user_profile(target), kb_user_profile_admin(target))

    elif data == "adm_search_user":
        if uid not in admin_ids:
            return
        bot.answer_callback_query(call.id)
        safe_edit(cid, mid, "🔍 ইউজারের ID লিখুন:", kb_back())
        bot.register_next_step_handler_by_chat_id(
            cid, lambda m: _do_search_user(m, uid)
        )

    elif data == "adm_stats":
        if uid not in admin_ids:
            return
        bot.answer_callback_query(call.id)
        nav_push(uid, "admin_panel")
        safe_edit(cid, mid, build_server_stats(), kb_back_refresh("adm_stats"))

    elif data == "adm_broadcast":
        if uid not in admin_ids:
            return
        bot.answer_callback_query(call.id)
        nav_push(uid, "admin_panel")
        safe_edit(cid, mid, "📢 <b>ব্রডকাস্ট</b>\n\nকাকে পাঠাবেন?", kb_broadcast())

    elif data == "adm_settings":
        if uid not in admin_ids:
            return
        bot.answer_callback_query(call.id)
        nav_push(uid, "admin_panel")
        safe_edit(cid, mid, build_admin_settings(), kb_admin_settings())

    elif data == "adm_prices":
        if uid not in admin_ids:
            return
        bot.answer_callback_query(call.id)
        nav_push(uid, "adm_settings")
        safe_edit(
            cid, mid,
            "💰 <b>প্রাইস ম্যানেজমেন্ট</b>\n\nপরিবর্তন করতে প্ল্যান সিলেক্ট করুন:",
            kb_prices()
        )

    elif data.startswith("edit_price_"):
        if uid not in admin_ids:
            return
        months = int(data[11:])
        bot.answer_callback_query(call.id)
        labels = {1: "১ মাস", 3: "৩ মাস", 6: "৬ মাস", 12: "১ বছর"}
        safe_edit(
            cid, mid,
            f"✏️ <b>{h(labels.get(months,''))} প্ল্যানের নতুন মূল্য দিন</b>\n"
            "<i>(শুধু সংখ্যা, যেমন: 7.5)</i>:",
            kb_back()
        )
        bot.register_next_step_handler_by_chat_id(
            cid, lambda m: _save_price(m, uid, months)
        )

    elif data == "adm_payment_methods":
        if uid not in admin_ids:
            return
        bot.answer_callback_query(call.id)
        nav_push(uid, "adm_settings")
        safe_edit(
            cid, mid,
            "💳 <b>পেমেন্ট মেথড সেটিংস</b>\n\nযেটা সেট করতে চান সেটা সিলেক্ট করুন:",
            kb_payment_methods_admin()
        )

    elif data.startswith("set_pay_method_"):
        if uid not in admin_ids:
            return
        method = data[15:]
        icons  = {"bkash": "💛 bKash", "nagad": "🟠 Nagad", "rocket": "🟣 Rocket", "usdt": "🟡 USDT"}
        rows   = db_get_payment_settings()
        info   = next((r for r in rows if r["method_name"] == method), None)
        cur_num = (info["number_or_address"] or "সেট নেই") if info else "সেট নেই"
        act     = "✅ Active" if (info and info["is_active"]) else "❌ Inactive"
        bot.answer_callback_query(call.id)
        nav_push(uid, "adm_payment_methods")
        m_kb = types.InlineKeyboardMarkup(row_width=1)
        m_kb.add(_b("✏️ নম্বর/ঠিকানা আপডেট করুন", f"pay_update_num_{method}", "p"))
        m_kb.add(_b("🗑️ এই মেথড ডিলিট করুন", f"del_pay_method_{method}", "d"))
        m_kb.add(_b("◀️ ফিরে যান", "nav_back", "e"))
        safe_edit(
            cid, mid,
            f"💳 <b>{h(icons.get(method, method))}</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📱 নম্বর: <code>{h(cur_num)}</code>\n"
            f"স্ট্যাটাস: {act}",
            m_kb
        )

    elif data.startswith("pay_update_num_"):
        if uid not in admin_ids:
            return
        method = data[15:]
        icons  = {"bkash": "💛 bKash", "nagad": "🟠 Nagad", "rocket": "🟣 Rocket", "usdt": "🟡 USDT"}
        bot.answer_callback_query(call.id)
        safe_edit(
            cid, mid,
            f"📱 <b>{h(icons.get(method, method))} নতুন নম্বর/ঠিকানা দিন:</b>",
            kb_back()
        )
        bot.register_next_step_handler_by_chat_id(
            cid, lambda m: _save_pay_method(m, uid, method)
        )

    elif data == "adm_add_pay_method":
        if uid not in admin_ids:
            return
        bot.answer_callback_query(call.id)
        safe_edit(
            cid, mid,
            "➕ <b>নতুন পেমেন্ট মেথড যোগ করুন</b>\n\n"
            "<b>ধাপ ১/৩:</b> মেথডের আইডি দিন\n"
            "<i>(ছোট হাতে, শুধু অক্ষর/সংখ্যা, যেমন: binance, paypal)</i>",
            kb_back()
        )
        payment_add_state[uid] = {"step": "method_id"}
        bot.register_next_step_handler_by_chat_id(cid, lambda m: _new_pay_step(m, uid))

    elif data == "adm_del_pay_method":
        if uid not in admin_ids:
            return
        bot.answer_callback_query(call.id)
        nav_push(uid, "adm_payment_methods")
        safe_edit(
            cid, mid,
            "🗑️ <b>কোন মেথড ডিলিট করবেন?</b>",
            kb_del_pay_methods_admin()
        )

    elif data.startswith("del_pay_method_"):
        method = data[15:]
        if uid not in admin_ids:
            return
        bot.answer_callback_query(call.id, "🗑️ ডিলিট হচ্ছে...")
        db_del_payment_method(method)
        safe_edit(
            cid, mid,
            "✅ মেথড ডিলিট হয়েছে।\n\n💳 <b>পেমেন্ট মেথড সেটিংস</b>\nযেটা সেট করতে চান সিলেক্ট করুন:",
            kb_payment_methods_admin()
        )

    elif data == "adm_edit_welcome":
        if uid not in admin_ids:
            return
        bot.answer_callback_query(call.id)
        safe_edit(cid, mid, "📝 নতুন Welcome মেসেজ লিখুন:", kb_back())
        bot.register_next_step_handler_by_chat_id(cid, _save_welcome)

    elif data == "adm_toggle_lock":
        if uid not in admin_ids:
            return
        global bot_locked
        bot_locked = not bot_locked
        db_set_setting("bot_locked", "1" if bot_locked else "0")
        bot.answer_callback_query(
            call.id,
            f"🔒 বট {'লক' if bot_locked else 'আনলক'} হয়েছে."
        )
        safe_edit(cid, mid, build_admin_settings(), kb_admin_settings())

    elif data == "adm_toggle_maint":
        if uid not in admin_ids:
            return
        global maintenance_mode
        maintenance_mode = not maintenance_mode
        db_set_setting("maintenance", "1" if maintenance_mode else "0")
        bot.answer_callback_query(
            call.id,
            f"🔧 Maintenance {'চালু' if maintenance_mode else 'বন্ধ'} হয়েছে."
        )
        safe_edit(cid, mid, build_admin_settings(), kb_admin_settings())

    elif data == "adm_set_free_limit":
        if uid not in admin_ids:
            return
        bot.answer_callback_query(call.id)
        fl = system_settings.get("free_limit", 1)
        fs = system_settings.get("free_storage_mb", 50)
        safe_edit(
            cid, mid,
            "🤖 <b>Free ইউজার লিমিট সেট করুন</b>\n\n"
            f"<i>বর্তমান: {fl} ফাইল, {fs}MB storage</i>\n\n"
            "ফরম্যাট: <code>ফাইল_সংখ্যা storage_MB</code>\n"
            "যেমন: <code>3 100</code> মানে ৩ ফাইল + ১০০MB\n"
            "শুধু ফাইল: <code>5</code> লিখলে storage অপরিবর্তিত থাকবে",
            kb_back()
        )
        bot.register_next_step_handler_by_chat_id(cid, _save_free_limit)

    elif data == "adm_set_cooldown":
        if uid not in admin_ids:
            return
        bot.answer_callback_query(call.id)
        safe_edit(cid, mid, "⏱ Cooldown সেকেন্ড দিন (সংখ্যা):", kb_back())
        bot.register_next_step_handler_by_chat_id(cid, _save_cooldown)

    elif data == "adm_clear_logs":
        if uid not in admin_ids:
            return
        bot.answer_callback_query(call.id, "🗑️ লগ ক্লিয়ার হচ্ছে...")
        try:
            open(BOT_LOG_FILE, "w").close()
            safe_send(cid, "✅ সব লগ ক্লিয়ার হয়েছে.")
        except Exception as e:
            safe_send(cid, f"❌ লগ ক্লিয়ার ব্যর্থ: {e}")

    # ── ব্রডকাস্ট ──
    elif data in ("bc_all", "bc_premium", "bc_free"):
        if uid not in admin_ids:
            return
        bot.answer_callback_query(call.id)
        targets_txt = {"bc_all": "সবাইকে", "bc_premium": "শুধু Premium", "bc_free": "শুধু Free"}
        safe_edit(
            cid, mid,
            f"📢 <b>{targets_txt[data]}</b> পাঠানোর মেসেজ লিখুন:",
            kb_back()
        )
        broadcast_state[uid] = data
        bot.register_next_step_handler_by_chat_id(
            cid, lambda m: _do_broadcast(m, uid, broadcast_state.get(uid, "bc_all"))
        )

    elif data == "bc_specific":
        if uid not in admin_ids:
            return
        bot.answer_callback_query(call.id)
        safe_edit(cid, mid, "👤 ইউজারের ID দিন:", kb_back())
        bot.register_next_step_handler_by_chat_id(
            cid, lambda m: _do_bc_specific_id(m, uid)
        )

    # ── ইউজার প্রোফাইল অ্যাকশন ──
    elif data.startswith("adm_setstorage_"):
        target = int(data[15:])
        if uid not in admin_ids:
            return
        bot.answer_callback_query(call.id)
        cur_override = db_get_user_storage_override(target)
        cur_txt = f"বর্তমান কাস্টম: {cur_override}MB" if cur_override else "বর্তমানে কোনো কাস্টম সেট নেই"
        safe_edit(
            cid, mid,
            f"💾 <b>ইউজার <code>{target}</code>-এর Storage সেট করুন</b>\n\n"
            f"<i>{cur_txt}</i>\n\n"
            "নতুন storage সাইজ MB-তে দিন:\n"
            "<i>(যেমন: 100 মানে ১০০MB, 0 দিলে কাস্টম সরে যাবে)</i>",
            kb_back()
        )
        bot.register_next_step_handler_by_chat_id(
            cid, lambda m: _save_user_storage(m, uid, target)
        )

    elif data.startswith("adm_giveprem_"):
        target = int(data[13:])
        if uid not in admin_ids:
            return
        bot.answer_callback_query(call.id)
        safe_edit(
            cid, mid,
            f"⭐ <b>ইউজার <code>{target}</code>-কে Premium দিন</b>\n\n"
            "ফরম্যাট: <code>LIMIT DAYS</code>\n"
            "যেমন: <code>10 30</code> (১০ ফাইল, ৩০ দিন)",
            kb_back()
        )
        bot.register_next_step_handler_by_chat_id(
            cid, lambda m: _save_give_premium(m, uid, target)
        )

    elif data.startswith("adm_ban_"):
        target = int(data[8:])
        if uid not in admin_ids:
            return
        if target == OWNER_ID:
            bot.answer_callback_query(call.id, "❌ Owner-কে ব্যান করা যাবে না.", show_alert=True)
            return
        bot.answer_callback_query(call.id)
        safe_edit(cid, mid, f"🚫 ইউজার <code>{target}</code>-কে ব্যানের কারণ লিখুন:", kb_back())
        bot.register_next_step_handler_by_chat_id(
            cid, lambda m: _save_ban(m, uid, target)
        )

    elif data.startswith("adm_unban_"):
        target = int(data[10:])
        if uid not in admin_ids:
            return
        bot.answer_callback_query(call.id, "✅ আনব্যান হচ্ছে...")
        db_unban_user(target)
        safe_send(cid, f"✅ ইউজার <code>{target}</code> আনব্যান হয়েছে.")
        safe_edit(cid, mid, build_user_profile(target), kb_user_profile_admin(target))

    elif data.startswith("adm_stopall_"):
        target = int(data[12:])
        if uid not in admin_ids:
            return
        bot.answer_callback_query(call.id, "⏹️ বন্ধ হচ্ছে...")
        count = 0
        for key in [k for k in bot_scripts if k.startswith(f"{target}_")]:
            slot_str = key.split("_")[1]
            _stop_script_key(target, int(slot_str))
            count += 1
        safe_send(cid, f"⏹️ ইউজার <code>{target}</code>-এর {count}টি স্ক্রিপ্ট বন্ধ হয়েছে.")

    elif data.startswith("adm_startall_"):
        target = int(data[13:])
        if uid not in admin_ids:
            return
        bot.answer_callback_query(call.id, "▶️ চালু হচ্ছে...")
        files = [
            f for f in user_files_db.get(target, [])
            if f["status"] in ("approved", "running", "stopped")
        ]
        for f in files:
            if not is_running(target, f["slot"]):
                threading.Thread(
                    target=run_script, args=(target, f["slot"], call.message), daemon=True
                ).start()
        safe_send(cid, f"▶️ ইউজার <code>{target}</code>-এর স্ক্রিপ্ট চালু হচ্ছে.")

    elif data.startswith("adm_msg_"):
        target = int(data[8:])
        if uid not in admin_ids:
            return
        bot.answer_callback_query(call.id)
        safe_edit(cid, mid, f"✉️ ইউজার <code>{target}</code>-কে মেসেজ লিখুন:", kb_back())
        bot.register_next_step_handler_by_chat_id(
            cid, lambda m: _send_adm_msg(m, target)
        )

    # ── Session ──
    elif data == "session_new":
        if not is_premium(uid):
            bot.answer_callback_query(call.id, "💎 Premium প্রয়োজন.", show_alert=True)
            return
        bot.answer_callback_query(call.id)
        nav_push(uid, "session_menu")
        safe_edit(cid, mid, "🔑 <b>Session টাইপ সিলেক্ট করুন:</b>", kb_session_type())

    elif data == "session_list":
        bot.answer_callback_query(call.id)
        nav_push(uid, "session_menu")
        _cb_session_list(call, uid)

    elif data == "session_delete":
        bot.answer_callback_query(call.id)
        nav_push(uid, "session_menu")
        _cb_session_delete_list(call, uid)

    elif data in ("stype_telethon", "stype_pyrogram"):
        stype = "telethon" if data == "stype_telethon" else "pyrogram"
        bot.answer_callback_query(call.id)
        _start_session_flow(call, uid, stype)

    elif data.startswith("session_info_"):
        sname = data[13:]
        bot.answer_callback_query(call.id)
        _cb_session_info(call, uid, sname)

    elif data.startswith("sesdel_"):
        sname = data[7:]
        bot.answer_callback_query(call.id, "🗑️ ডিলিট হচ্ছে...")
        _cb_session_do_delete(call, uid, sname)

    else:
        bot.answer_callback_query(call.id)

# ─── ফাইল অ্যাপ্রুভাল callbacks ────────────────────────────────
def _cb_approve_file(call, aid):
    row = db_get_approval(aid)
    if not row or row["status"] != "pending":
        safe_send(call.message.chat.id, "⚠️ ইতিমধ্যে প্রসেস হয়েছে.")
        return
    target = row["user_id"]
    fname  = row["file_name"]
    ftype  = row["file_type"]
    slot   = row["slot"]
    db_update_approval(aid, "approved", call.from_user.id)
    try:
        bot.edit_message_reply_markup(
            call.message.chat.id, call.message.message_id, reply_markup=None
        )
        safe_send(call.message.chat.id, f"✅ <code>{h(fname)}</code> অ্যাপ্রুভ হয়েছে.")
    except Exception:
        pass

    if ftype == "zip":
        content = _pending_zip_data.pop(aid, None)
        folder  = os.path.join(get_user_folder(target), f"script_{slot}")
        if content:
            threading.Thread(
                target=handle_zip_extract,
                args=(content, fname, target, slot, folder, call.message),
                daemon=True
            ).start()
        else:
            safe_send(call.message.chat.id, "⚠️ ZIP ডেটা পাওয়া যায়নি.")
    else:
        db_update_file_status(target, slot, "approved")
        threading.Thread(
            target=run_script, args=(target, slot, call.message), daemon=True
        ).start()

    # ইউজারকে "সিস্টেম" ভাষায় জানাও (কোনো Admin শব্দ নেই)
    try:
        bot.send_message(
            target,
            f"✅ <b>সিস্টেম ডেপ্লয়মেন্ট সম্পন্ন!</b>\n\n"
            f"🟢 আপনার স্ক্রিপ্ট <code>{h(fname)}</code> চালু হয়েছে।",
            reply_markup=kb_go_scripts(),
            parse_mode="HTML"
        )
    except Exception:
        pass

def _cb_reject_file_ask(call, aid):
    row = db_get_approval(aid)
    if not row or row["status"] != "pending":
        safe_send(call.message.chat.id, "⚠️ ইতিমধ্যে প্রসেস হয়েছে.")
        return
    _reject_state[call.from_user.id] = {"type": "file", "aid": aid}
    safe_edit(
        call.message.chat.id, call.message.message_id,
        "❌ <b>Reject কারণ লিখুন:</b>", kb_back()
    )
    bot.register_next_step_handler_by_chat_id(
        call.message.chat.id,
        lambda m: _cb_reject_file_reason(m, call.from_user.id)
    )

def _cb_reject_file_reason(msg, admin_uid):
    state = _reject_state.pop(admin_uid, None)
    if not state:
        return
    aid    = state["aid"]
    reason = msg.text or "নির্দিষ্ট কারণ নেই"
    row    = db_get_approval(aid)
    if not row:
        return
    target = row["user_id"]
    fname  = row["file_name"]
    slot   = row["slot"]
    db_update_approval(aid, "rejected", admin_uid, reason)
    db_update_file_status(target, slot, "rejected")
    folder = os.path.join(get_user_folder(target), f"script_{slot}")
    if os.path.exists(folder):
        shutil.rmtree(folder, ignore_errors=True)
    _pending_zip_data.pop(aid, None)
    bot.reply_to(msg, f"✅ <code>{h(fname)}</code> রিজেক্ট করা হয়েছে.", parse_mode="HTML")

    # ইউজারকে "সিকিউরিটি পলিসি" ভাষায় জানাও (কোনো Admin শব্দ নেই)
    try:
        bot.send_message(
            target,
            "🔴 <b>স্ক্যান সম্পন্ন</b>\n\n"
            "আপনার ফাইলে সন্দেহজনক কিছু পাওয়া গেছে "
            "যা আমাদের সিকিউরিটি পলিসি লঙ্ঘন করে।\n\n"
            "সঠিক ফাইল দিয়ে আবার চেষ্টা করুন।",
            reply_markup=kb_retry(),
            parse_mode="HTML"
        )
    except Exception:
        pass

def _cb_adm_approvals(call):
    rows = db_get_pending_approvals()
    if not rows:
        safe_edit(
            call.message.chat.id, call.message.message_id,
            "📋 <b>ফাইল অ্যাপ্রুভাল</b>\n\n✅ কোনো পেন্ডিং নেই.",
            kb_back()
        )
        return
    lines = []
    m     = types.InlineKeyboardMarkup()
    for r in rows[:10]:
        lines.append(
            f"▸ #{r['id']} | <code>{r['user_id']}</code> | "
            f"<code>{h(r['file_name'])}</code> | {fmt_time_ago(r['submitted_at'])}"
        )
        m.row(
            _b(f"✅ #{r['id']} {r['file_name'][:12]}", f"approve_{r['id']}", "s"),
            _b("❌ Reject", f"reject_{r['id']}", "d")
        )
    m.add(_b("◀️ ফিরে যান", "nav_back", "e"))
    safe_edit(
        call.message.chat.id, call.message.message_id,
        f"📋 <b>ফাইল অ্যাপ্রুভাল</b> ({len(rows)} পেন্ডিং)\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n" + "\n".join(lines), m
    )

def _cb_adm_payments(call):
    rows   = db_get_pending_payments()
    labels = {1: "১ মাস", 3: "৩ মাস", 6: "৬ মাস", 12: "১ বছর"}
    if not rows:
        safe_edit(
            call.message.chat.id, call.message.message_id,
            "💰 <b>পেমেন্ট অ্যাপ্রুভাল</b>\n\n✅ কোনো পেন্ডিং নেই.",
            kb_back()
        )
        return
    lines = []
    m     = types.InlineKeyboardMarkup()
    for r in rows[:10]:
        lines.append(
            f"▸ #{r['id']} | <code>{r['user_id']}</code> | "
            f"{labels.get(r['plan_months'], str(r['plan_months'])+'m')} | ${r['amount_usd']}"
        )
        m.row(
            _b(f"✅ #{r['id']} অ্যাপ্রুভ", f"pay_approve_{r['id']}", "s"),
            _b("❌ রিজেক্ট", f"pay_reject_{r['id']}", "d")
        )
    m.add(_b("◀️ ফিরে যান", "nav_back", "e"))
    safe_edit(
        call.message.chat.id, call.message.message_id,
        f"💰 <b>পেমেন্ট অ্যাপ্রুভাল</b> ({len(rows)} পেন্ডিং)\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n" + "\n".join(lines), m
    )

def _cb_approve_payment(call, pid):
    row = db_get_payment(pid)
    if not row or row["status"] != "pending":
        safe_send(call.message.chat.id, "⚠️ ইতিমধ্যে প্রসেস হয়েছে.")
        return
    target = row["user_id"]
    months = row["plan_months"]
    db_update_payment(pid, "approved", call.from_user.id)
    expiry = activate_premium(target, months, bot_limit=10, granted_by=call.from_user.id)
    try:
        bot.edit_message_reply_markup(
            call.message.chat.id, call.message.message_id, reply_markup=None
        )
        safe_send(call.message.chat.id, f"✅ পেমেন্ট #{pid} অ্যাপ্রুভ হয়েছে.")
    except Exception:
        pass
    try:
        bot.send_message(
            target,
            f"🎉 <b>আপনার Premium অ্যাক্টিভ হয়েছে!</b>\n"
            f"📅 মেয়াদ: {expiry.strftime('%Y-%m-%d')} পর্যন্ত",
            parse_mode="HTML"
        )
    except Exception:
        pass

def _cb_reject_payment(call, pid):
    row = db_get_payment(pid)
    if not row or row["status"] != "pending":
        safe_send(call.message.chat.id, "⚠️ ইতিমধ্যে প্রসেস হয়েছে.")
        return
    target = row["user_id"]
    db_update_payment(pid, "rejected", call.from_user.id)
    try:
        bot.edit_message_reply_markup(
            call.message.chat.id, call.message.message_id, reply_markup=None
        )
        safe_send(call.message.chat.id, f"❌ পেমেন্ট #{pid} রিজেক্ট হয়েছে.")
    except Exception:
        pass
    try:
        bot.send_message(
            target,
            f"❌ <b>পেমেন্ট যাচাই করা যায়নি.</b>\n"
            f"অ্যাডমিনের সাথে যোগাযোগ করুন: {ADMIN_USERNAME}",
            parse_mode="HTML"
        )
    except Exception:
        pass

def _receive_payment_screenshot(msg, uid):
    state = payment_state.pop(uid, None)
    if not state:
        return
    sid = None
    if msg.photo:
        sid = msg.photo[-1].file_id
    elif msg.document:
        sid = msg.document.file_id
    else:
        bot.reply_to(msg, "⚠️ স্ক্রিনশট (ছবি) পাঠান.")
        payment_state[uid] = state
        return
    pay_id   = db_add_payment_request(uid, state["months"], state["amount"], state["method"], sid)
    username = msg.from_user.username or str(uid)
    threading.Thread(
        target=notify_admins_payment,
        args=(uid, pay_id, state["months"], state["amount"], state["method"], sid, username, msg),
        daemon=True
    ).start()
    bot.reply_to(
        msg,
        "✅ <b>পেমেন্ট রিকোয়েস্ট পাঠানো হয়েছে!</b>\n\n"
        "<i>সাধারণত ১-২৪ ঘণ্টার মধ্যে যাচাই করা হয়।</i>",
        parse_mode="HTML"
    )

# ─── অ্যাডমিন helper functions ───────────────────────────────────
def _do_search_user(msg, admin_uid):
    if not msg.text:
        return
    try:
        target = int(msg.text.strip())
    except ValueError:
        bot.reply_to(msg, "❌ সঠিক ID দিন.")
        return
    bot.send_message(
        msg.chat.id,
        build_user_profile(target),
        reply_markup=kb_user_profile_admin(target),
        parse_mode="HTML"
    )

def _save_pay_method(msg, uid, method):
    number = (msg.text or "").strip()
    if not number:
        bot.reply_to(msg, "❌ খালি রাখা যাবে না.")
        return
    db_set_payment_method(method, number)
    bot.reply_to(
        msg,
        f"✅ {method} নম্বর সেট: <code>{h(number)}</code>",
        parse_mode="HTML"
    )

def _save_price(msg, uid, months):
    try:
        price = float((msg.text or "").strip())
    except ValueError:
        bot.reply_to(msg, "❌ সংখ্যা দিন.")
        return
    db_set_plan_price(months, price)
    bot.reply_to(msg, f"✅ মূল্য আপডেট: ${price:.2f}")

def _save_welcome(msg):
    if msg.text:
        db_set_setting("welcome_text", msg.text)
    bot.reply_to(msg, "✅ Welcome মেসেজ আপডেট হয়েছে.")

def _save_free_limit(msg):
    parts = (msg.text or "").strip().split()
    try:
        lim = int(parts[0])
    except (ValueError, IndexError):
        bot.reply_to(msg, "❌ সঠিক ফরম্যাট দিন। যেমন: <code>3 100</code>", parse_mode="HTML")
        return
    system_settings["free_limit"] = lim
    db_set_setting("free_limit", str(lim))
    reply = f"✅ Free লিমিট: <code>{lim} ফাইল</code>"
    if len(parts) >= 2:
        try:
            storage_mb = int(parts[1])
            system_settings["free_storage_mb"] = storage_mb
            db_set_setting("free_storage_mb", str(storage_mb))
            reply += f"\n✅ Free Storage: <code>{storage_mb}MB</code>"
        except ValueError:
            reply += "\n⚠️ Storage সংখ্যা ঠিক নেই, অপরিবর্তিত রইল।"
    bot.reply_to(msg, reply, parse_mode="HTML")

def _save_cooldown(msg):
    try:
        cdw = int((msg.text or "").strip())
    except ValueError:
        bot.reply_to(msg, "❌ সংখ্যা দিন.")
        return
    system_settings["cooldown_sec"] = cdw
    db_set_setting("cooldown_sec", str(cdw))
    bot.reply_to(msg, f"✅ Cooldown: {cdw}s")

def _save_give_premium(msg, admin_uid, target):
    parts = (msg.text or "").split()
    if len(parts) < 2:
        bot.reply_to(msg, "❌ LIMIT DAYS ফরম্যাট দিন.")
        return
    try:
        lim, days = int(parts[0]), int(parts[1])
    except ValueError:
        bot.reply_to(msg, "❌ সংখ্যা দিন.")
        return
    exp = datetime.now() + timedelta(days=days)
    db_save_sub(target, lim, exp, admin_uid)
    db_clear_expiry_notifs(target)
    bot.reply_to(
        msg,
        f"✅ ইউজার <code>{target}</code>-কে {days} দিনের Premium দেওয়া হয়েছে.",
        parse_mode="HTML"
    )
    try:
        bot.send_message(
            target,
            f"🎉 <b>আপনার Premium অ্যাক্টিভ হয়েছে!</b>\n"
            f"🔢 লিমিট: {lim}\n"
            f"📅 মেয়াদ: {exp.strftime('%Y-%m-%d')}",
            parse_mode="HTML"
        )
    except Exception:
        pass

def _save_user_storage(msg, admin_uid, target):
    try:
        mb = int((msg.text or "").strip())
    except ValueError:
        bot.reply_to(msg, "❌ সংখ্যা দিন (MB তে).")
        return
    if mb <= 0:
        db_del_user_storage_override(target)
        bot.reply_to(
            msg,
            f"✅ ইউজার <code>{target}</code>-এর কাস্টম storage সরানো হয়েছে।\n"
            f"এখন ডিফল্ট storage প্রযোজ্য হবে।",
            parse_mode="HTML"
        )
        try:
            bot.send_message(
                target,
                "💾 <b>আপনার Storage আপডেট হয়েছে।</b>\n"
                "ডিফল্ট storage লিমিট প্রযোজ্য।",
                parse_mode="HTML"
            )
        except Exception:
            pass
    else:
        db_set_user_storage(target, mb, admin_uid)
        bot.reply_to(
            msg,
            f"✅ ইউজার <code>{target}</code>-এর storage সেট হয়েছে: <code>{mb}MB</code>",
            parse_mode="HTML"
        )
        try:
            bot.send_message(
                target,
                f"💾 <b>আপনার Storage আপডেট হয়েছে!</b>\n"
                f"নতুন লিমিট: <code>{mb}MB</code>",
                parse_mode="HTML"
            )
        except Exception:
            pass

def _new_pay_step(msg, uid):
    state = payment_add_state.get(uid)
    if not state:
        return
    step = state.get("step")
    if step == "method_id":
        mid_val = (msg.text or "").strip().lower()
        if not re.match(r"^[a-z0-9_]+$", mid_val):
            bot.reply_to(msg, "❌ শুধু ছোট হাতের অক্ষর, সংখ্যা ও _ ব্যবহার করুন.")
            bot.register_next_step_handler(msg, lambda m: _new_pay_step(m, uid))
            return
        state["method_id"] = mid_val
        state["step"]      = "display_name"
        bot.reply_to(
            msg,
            "<b>ধাপ ২/৩:</b> মেথডের প্রদর্শন নাম দিন\n"
            "<i>(যেমন: Binance USDT, PayPal)</i>",
            parse_mode="HTML"
        )
        bot.register_next_step_handler(msg, lambda m: _new_pay_step(m, uid))
    elif step == "display_name":
        state["display_name"] = (msg.text or "").strip()
        state["step"]         = "number"
        bot.reply_to(
            msg,
            "<b>ধাপ ৩/৩:</b> নম্বর বা ঠিকানা দিন\n"
            "<i>(যেমন: 01XXXXXXXXX, 0xABC...)</i>",
            parse_mode="HTML"
        )
        bot.register_next_step_handler(msg, lambda m: _new_pay_step(m, uid))
    elif step == "number":
        number = (msg.text or "").strip()
        if not number:
            bot.reply_to(msg, "❌ খালি রাখা যাবে না.")
            bot.register_next_step_handler(msg, lambda m: _new_pay_step(m, uid))
            return
        db_add_new_payment_method(state["method_id"], state["display_name"], number)
        payment_add_state.pop(uid, None)
        bot.reply_to(
            msg,
            f"✅ নতুন মেথড যোগ হয়েছে!\n"
            f"🆔 ID: <code>{h(state['method_id'])}</code>\n"
            f"📛 নাম: {h(state['display_name'])}\n"
            f"📱 নম্বর: <code>{h(number)}</code>",
            parse_mode="HTML"
        )

def _save_ban(msg, admin_uid, target):
    reason = msg.text or "বট নীতি লঙ্ঘন"
    db_ban_user(target, reason, admin_uid)
    bot.reply_to(
        msg,
        f"✅ ইউজার <code>{target}</code> ব্যান হয়েছে.",
        parse_mode="HTML"
    )

def _send_adm_msg(msg, target):
    if not msg.text:
        return
    try:
        bot.send_message(
            target,
            f"📨 <b>সিস্টেম মেসেজ:</b>\n\n{h(msg.text)}",
            parse_mode="HTML"
        )
        bot.reply_to(msg, "✅ মেসেজ পাঠানো হয়েছে.")
    except Exception as e:
        bot.reply_to(msg, f"❌ পাঠানো যায়নি: {e}")

def _do_broadcast(msg, uid, target_type):
    broadcast_state.pop(uid, None)
    if not msg.text:
        return
    if target_type == "bc_all":
        targets = list(active_users)
    elif target_type == "bc_premium":
        targets = [u for u in active_users if is_premium(u) and u not in admin_ids]
    else:
        targets = [u for u in active_users if not is_premium(u)]
    ok = fail = 0
    for t in targets:
        try:
            bot.send_message(t, msg.text)
            ok += 1
        except Exception:
            fail += 1
        time.sleep(0.05)
    bot.reply_to(msg, f"✅ Broadcast সম্পন্ন!\n✅ সফল: {ok} | ❌ ব্যর্থ: {fail}")

def _do_bc_specific_id(msg, admin_uid):
    try:
        target = int((msg.text or "").strip())
    except ValueError:
        bot.reply_to(msg, "❌ সঠিক ID দিন.")
        return
    safe_send(msg.chat.id, f"✉️ ইউজার <code>{target}</code>-কে মেসেজ লিখুন:", kb_back())
    bot.register_next_step_handler_by_chat_id(
        msg.chat.id, lambda m: _send_adm_msg(m, target)
    )

# ═══════════════════════════════════════════════════════════════
# ★ বাগ ২ ঠিক — SESSION SYSTEM (loop parameter বাদ দেওয়া হয়েছে)
# ═══════════════════════════════════════════════════════════════
def _cb_session_list(call, uid):
    sessions = db_get_sessions(uid)
    if not sessions:
        safe_edit(
            call.message.chat.id, call.message.message_id,
            "📋 <b>আমার Session</b>\n\n<i>এখনো কোনো Session নেই.</i>",
            kb_back()
        )
        return
    m = types.InlineKeyboardMarkup(row_width=1)
    for s in sessions:
        m.add(_b(
            f"📄 {s['session_name']} [{s['session_type']}]",
            f"session_info_{s['session_name']}", "p"
        ))
    m.add(_b("◀️ ফিরে যান", "nav_back", "e"))
    lines = [
        f"▸ <code>{h(s['session_name'])}</code> — {s['session_type']}"
        for s in sessions
    ]
    safe_edit(
        call.message.chat.id, call.message.message_id,
        "📋 <b>আমার Session</b>\n\n" + "\n".join(lines), m
    )

def _cb_session_delete_list(call, uid):
    sessions = db_get_sessions(uid)
    if not sessions:
        safe_edit(
            call.message.chat.id, call.message.message_id,
            "🗑️ <b>Session ডিলিট</b>\n\n<i>কোনো Session নেই.</i>",
            kb_back()
        )
        return
    m = types.InlineKeyboardMarkup(row_width=1)
    for s in sessions:
        m.add(_b(f"🗑️ {s['session_name']}", f"sesdel_{s['session_name']}", "d"))
    m.add(_b("◀️ ফিরে যান", "nav_back", "e"))
    safe_edit(
        call.message.chat.id, call.message.message_id,
        "🗑️ <b>Session ডিলিট করুন</b>\n\nকোনটি ডিলিট করবেন?", m
    )

def _cb_session_info(call, uid, session_name):
    sessions = db_get_sessions(uid)
    entry    = next((s for s in sessions if s["session_name"] == session_name), None)
    if not entry:
        bot.answer_callback_query(call.id, "⚠️ Session পাওয়া যায়নি.", show_alert=True)
        return
    uf = get_user_folder(uid)
    ex = "✅ আছে" if os.path.exists(os.path.join(uf, f"{session_name}.session")) else "❌ নেই"
    m  = types.InlineKeyboardMarkup()
    m.add(_b(f"🗑️ {session_name} ডিলিট", f"sesdel_{session_name}", "d"))
    m.add(_b("◀️ ফিরে যান", "nav_back", "e"))
    safe_edit(
        call.message.chat.id, call.message.message_id,
        f"📄 <b>Session: <code>{h(session_name)}</code></b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔧 টাইপ: <code>{entry['session_type']}</code>\n"
        f"🔑 API ID: <code>{h(entry['api_id'] or '-')}</code>\n"
        f"💾 ফাইল: {ex}\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💡 ব্যবহার: <code>TelegramClient('{h(session_name)}', api_id, api_hash)</code>", m
    )

def _cb_session_do_delete(call, uid, session_name):
    uf = get_user_folder(uid)
    sf = os.path.join(uf, f"{session_name}.session")
    if os.path.exists(sf):
        os.remove(sf)
    db_del_session(uid, session_name)
    safe_send(call.message.chat.id, f"✅ Session <code>{h(session_name)}</code> ডিলিট হয়েছে.")
    _cb_session_list(call, uid)

def _start_session_flow(call, uid, stype):
    session_state[uid] = {"type": stype, "step": "name"}
    safe_edit(
        call.message.chat.id, call.message.message_id,
        f"🔑 <b>{stype.capitalize()} Session তৈরি</b>\n\n"
        "<b>ধাপ ১/৬:</b> Session-এর নাম দিন:\n"
        "<i>(শুধু অক্ষর ও সংখ্যা, যেমন: mysession)</i>",
        kb_back()
    )
    bot.register_next_step_handler_by_chat_id(
        call.message.chat.id, lambda m: _session_step(m, uid)
    )

def _session_step(msg, uid):
    if msg.text and msg.text.startswith("/"):
        session_state.pop(uid, None)
        return
    state = session_state.get(uid)
    if not state:
        return
    step = state.get("step")

    if step == "name":
        name = (msg.text or "").strip()
        if not re.match(r"^[a-zA-Z0-9_]+$", name):
            bot.reply_to(msg, "❌ শুধু অক্ষর, সংখ্যা ও _ ব্যবহার করুন.")
            bot.register_next_step_handler(msg, lambda m: _session_step(m, uid))
            return
        state["name"] = name
        state["step"] = "api_id"
        bot.reply_to(
            msg,
            "<b>ধাপ ২/৬:</b> API ID দিন:\n<i>(my.telegram.org থেকে)</i>",
            parse_mode="HTML"
        )
        bot.register_next_step_handler(msg, lambda m: _session_step(m, uid))

    elif step == "api_id":
        try:
            state["api_id"] = int((msg.text or "").strip())
        except ValueError:
            bot.reply_to(msg, "❌ API ID সংখ্যা হতে হবে.")
            bot.register_next_step_handler(msg, lambda m: _session_step(m, uid))
            return
        state["step"] = "api_hash"
        bot.reply_to(msg, "<b>ধাপ ৩/৬:</b> API Hash দিন:", parse_mode="HTML")
        bot.register_next_step_handler(msg, lambda m: _session_step(m, uid))

    elif step == "api_hash":
        state["api_hash"] = (msg.text or "").strip()
        state["step"]     = "phone"
        bot.reply_to(
            msg,
            "<b>ধাপ ৪/৬:</b> ফোন নম্বর দিন:\n<i>(+880...)</i>",
            parse_mode="HTML"
        )
        bot.register_next_step_handler(msg, lambda m: _session_step(m, uid))

    elif step == "phone":
        state["phone"] = (msg.text or "").strip()
        state["step"]  = "otp"
        threading.Thread(target=_send_otp, args=(msg, uid, state), daemon=True).start()

    elif step == "otp":
        state["otp"]  = (msg.text or "").strip()
        state["step"] = "done"
        threading.Thread(target=_complete_session, args=(msg, uid, state), daemon=True).start()

    elif step == "2fa":
        state["password"] = (msg.text or "").strip()
        threading.Thread(
            target=_complete_session_2fa, args=(msg, uid, state), daemon=True
        ).start()

# ★ বাগ ২ ঠিক — loop=loop parameter বাদ দেওয়া হয়েছে
def _run_in_new_loop(coro):
    """
    নতুন asyncio event loop তৈরি করে coroutine চালায়।
    threading থেকে Telethon ব্যবহারের একমাত্র নির্ভরযোগ্য উপায়।
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop, loop.run_until_complete(coro)
    except Exception:
        raise
    # loop বন্ধ করা হয় না — client এখনো ব্যবহার করবে

def _send_otp(msg, uid, state):
    """
    Telethon দিয়ে OTP পাঠায়।
    ★ প্রতিটা thread-এ নতুন asyncio event loop — 'event loop must not change' error নেই।
    """
    from telethon import TelegramClient as _TGClient
    ses_path = os.path.join(get_user_folder(uid), state["name"])

    async def _do():
        client = _TGClient(ses_path, state["api_id"], state["api_hash"])
        await client.connect()
        await client.send_code_request(state["phone"])
        return client

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        client = loop.run_until_complete(_do())
        # loop ও client session state-এ রাখো
        state["client"] = client
        state["loop"]   = loop
        bot.reply_to(
            msg,
            "<b>ধাপ ৫/৬:</b> OTP কোড দিন:\n"
            "<i>(Telegram থেকে যে কোড এসেছে)</i>",
            parse_mode="HTML"
        )
        bot.register_next_step_handler(msg, lambda m: _session_step(m, uid))
    except Exception as e:
        session_state.pop(uid, None)
        try:
            loop.close()
        except Exception:
            pass
        bot.reply_to(
            msg,
            f"❌ OTP পাঠানো ব্যর্থ: {h(str(e))}",
            parse_mode="HTML"
        )

def _complete_session(msg, uid, state):
    """
    Session sign-in সম্পন্ন করে।
    ★ state['loop'] — একই loop ব্যবহার করে যেটায় client connect হয়েছিল।
    """
    client = state.get("client")
    loop   = state.get("loop")
    if not client or not loop:
        session_state.pop(uid, None)
        return

    async def _do():
        await client.sign_in(state["phone"], state["otp"])
        await client.disconnect()

    try:
        loop.run_until_complete(_do())
        loop.close()
        db_add_session(uid, state["name"], state["type"], state["api_id"])
        session_state.pop(uid, None)
        bot.reply_to(
            msg,
            f"✅ <b>Session তৈরি হয়েছে!</b>\n"
            f"📄 নাম: <code>{h(state['name'])}</code>",
            parse_mode="HTML"
        )
    except Exception as e:
        # ★ Telethon-এর SessionPasswordNeededError সঠিকভাবে ধরো
        try:
            from telethon.errors import SessionPasswordNeededError
            needs_2fa = isinstance(e, SessionPasswordNeededError)
        except ImportError:
            err_lower = str(e).lower()
            needs_2fa = "password" in err_lower or "2fa" in err_lower or "two" in err_lower

        if needs_2fa:
            state["step"] = "2fa"
            bot.reply_to(
                msg,
                "<b>ধাপ ৬/৬:</b> 2FA Password দিন:",
                parse_mode="HTML"
            )
            bot.register_next_step_handler(msg, lambda m: _session_step(m, uid))
        else:
            session_state.pop(uid, None)
            try:
                async def _disc(): await client.disconnect()
                loop.run_until_complete(_disc())
                loop.close()
            except Exception:
                pass
            bot.reply_to(
                msg,
                f"❌ Login ব্যর্থ: {h(str(e))}",
                parse_mode="HTML"
            )

def _complete_session_2fa(msg, uid, state):
    """
    2FA password দিয়ে session সম্পন্ন করে।
    ★ একই loop — client আগে থেকেই connect।
    """
    client = state.get("client")
    loop   = state.get("loop")
    if not client or not loop:
        session_state.pop(uid, None)
        return

    async def _do():
        await client.sign_in(password=state["password"])
        await client.disconnect()

    try:
        loop.run_until_complete(_do())
        loop.close()
        db_add_session(uid, state["name"], state["type"], state["api_id"])
        session_state.pop(uid, None)
        bot.reply_to(
            msg,
            f"✅ <b>Session তৈরি হয়েছে!</b>\n"
            f"📄 নাম: <code>{h(state['name'])}</code>",
            parse_mode="HTML"
        )
    except Exception as e:
        session_state.pop(uid, None)
        try:
            async def _disc(): await client.disconnect()
            loop.run_until_complete(_disc())
            loop.close()
        except Exception:
            pass
        bot.reply_to(
            msg,
            f"❌ 2FA ব্যর্থ: {h(str(e))}",
            parse_mode="HTML"
        )

# ═══════════════════════════════════════════════════════════════
# MAIN — বট চালু করো
# ═══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    logger.info("Script Host Pro v6.0 চালু হচ্ছে...")

    # Database initialize ও data load
    init_db()
    load_data()

    # Background threads চালু করো
    threading.Thread(target=_expiry_checker, daemon=True).start()
    threading.Thread(target=_cleanup_thread, daemon=True).start()
    threading.Thread(target=_run_flask, daemon=True).start()

    logger.info(f"Owner ID: {OWNER_ID}")
    logger.info(f"Total admins: {len(admin_ids)}")
    logger.info("Polling চালু হচ্ছে...")

    bot.infinity_polling(
        timeout=30,
        long_polling_timeout=30,
        logger_level=logging.WARNING
    )
