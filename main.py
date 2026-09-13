# -*- coding: utf-8 -*-
"""
Script Host Pro — Telegram Hosting Bot v6.1
Changes:
  - All text translated to English
  - Most inline keyboards converted to custom reply keyboards
  - Module Installation button added on file-upload result page
  - Token/subscription system unchanged
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
# CONFIG — Fill in your details here
# ═══════════════════════════════════════════════════════════════
TOKEN          = "8947601300:AAFkxgtiJOAq7HwAskNW3qJnWbQDQMq79fc"
OWNER_ID       = 5913459788
ADMIN_USERNAME = "@lod_Shadow"
UPDATE_CHANNEL = "https://t.me/nexasms"
BUY_LINK       = "https://t.me/nexa_ad"

# ★ DATA BACKUP CHANNEL
# Set this to a private Telegram channel ID (e.g. -1001234567890)
# The bot must be admin in that channel with "Post Messages" permission.
# All DB + uploaded scripts will be backed up here and can be restored.
BACKUP_CHANNEL_ID = -1003710777540   # ← replace with your channel ID (integer)

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
_pkg_install_state:   dict = {}   # uid -> slot (waiting for pkg name)

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
        c.execute("INSERT OR IGNORE INTO plan_prices VALUES (1,5.00,'1 Month',1)")
        c.execute("INSERT OR IGNORE INTO plan_prices VALUES (3,12.00,'3 Months',1)")
        c.execute("INSERT OR IGNORE INTO plan_prices VALUES (6,20.00,'6 Months',1)")
        c.execute("INSERT OR IGNORE INTO plan_prices VALUES (12,35.00,'1 Year',1)")
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
        return True, 999, "Unlimited", 9999
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
        if mins < 1:    return "just now"
        if mins < 60:   return f"{mins} min ago"
        if mins < 1440: return f"{mins//60} hr ago"
        return f"{mins//1440} days ago"
    except Exception:
        return iso_str[:10] if iso_str else "-"

def h(text):
    """Escape HTML special characters."""
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
    """Activate premium — adds to existing expiry."""
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
# TEXT BUILDERS (HTML parse mode) — All English
# ═══════════════════════════════════════════════════════════════
def build_welcome(uid, name):
    prem, lim, exp, days = get_sub_info(uid)
    plan = "💎 Premium" if prem else "🆓 Free"
    cnt  = len(user_files_db.get(uid, []))
    return (
        f"✦ Welcome, <b>{h(name)}</b>! ✦\n\n"
        "<b>𝗦𝗰𝗿𝗶𝗽𝘁 𝗛𝗼𝘀𝘁 𝗣𝗿𝗼</b>\n"
        "<i>Your trusted hosting partner</i>\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        "◆ 24/7 Uptime Guarantee\n"
        "◆ Maximum Security\n"
        "◆ Instant Deployment\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 Plan: {plan} | Scripts: {cnt}/{lim}"
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
        f"\n📅 Expires: <i>{days} days left ({h(exp)})</i>"
        if prem and uid not in admin_ids else ""
    )
    return (
        "⟡ <b>My Panel</b> ⟡\n\n"
        f"👤 @{h(name)}\n"
        f"🆔 ID: <code>{uid}</code>\n"
        f"⭐ Plan: {plan}{exp_l}\n"
        f"📄 Scripts: <code>{len(files)}/{lim}</code>\n"
        f"🏃 Running: <code>{running_count_user(uid)}</code>\n"
        f"💾 Storage: <code>{used:.1f}MB/{stor}MB</code> {bar}\n"
        "━━━━━━━━━━━━━━━━━━━━━━━"
    )

def build_my_scripts(uid):
    files = user_files_db.get(uid, [])
    prem, lim, _, _ = get_sub_info(uid)
    used  = get_user_storage_mb(uid)
    stor  = get_storage_limit(uid)
    if not files:
        return (
            "⟡ <b>My Scripts</b> ⟡\n\n"
            "📭 No scripts yet.\n\n"
            "🚀 Upload a file to get started."
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
        "⟡ <b>My Scripts</b> ⟡\n\n"
        + "\n".join(lines)
        + f"\n\n━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💾 Total: <code>{used:.1f}MB / {stor}MB</code>\n"
        f"📄 Files: <code>{len(files)}/{lim}</code>"
    )

def build_file_control(uid, slot):
    fi = get_file_by_slot(uid, slot)
    if not fi:
        return "❌ File not found."
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
    types_map = {"py": "Python (.py)", "js": "Node.js (.js)", "zip": "ZIP Project", "php": "PHP (.php)"}
    return (
        "⟡ <b>File Control</b> ⟡\n\n"
        f"📄 <b>{h(fi['file_name'])}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🗂 Type: <code>{types_map.get(fi['file_type'], fi['file_type'])}</code>\n"
        f"📡 Status: {status}\n"
        f"🔢 PID: <code>{pid_txt}</code>\n"
        f"🖥 CPU: <code>{cpu_txt}</code> | RAM: <code>{ram_txt}</code>\n"
        f"💾 Size: <code>{fi['file_size_kb']:.0f} KB</code>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━"
    )

def build_admin_panel():
    pf  = len(db_get_pending_approvals())
    pp  = len(db_get_pending_payments())
    run = sum(1 for v in bot_scripts.values() if v.get("proc") and v["proc"].poll() is None)
    prc = sum(1 for u in active_users if is_premium(u) and u not in admin_ids)
    return (
        "✦ <b>Admin Panel</b> ✦\n\n"
        "<b>System Overview</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"◆ Total Users: <code>{len(active_users)}</code>\n"
        f"◆ Premium: <code>{prc}</code> | Free: <code>{len(active_users)-prc}</code>\n"
        f"◆ Pending Files: <code>{pf}</code>\n"
        f"◆ Pending Payments: <code>{pp}</code>\n"
        f"◆ Running Scripts: <code>{run}</code>\n"
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
        "🖥️ <b>Server Status</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚙️ CPU: <code>{cpu:.0f}%</code> {cb}\n"
        f"🧠 RAM: <code>{ram.used/1024**3:.1f}/{ram.total/1024**3:.1f} GB</code> {rb}\n"
        f"💿 Disk: <code>{disk.used/1024**3:.1f}/{disk.total/1024**3:.1f} GB</code> ({disk.percent:.1f}%)\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🤖 Running Scripts: <code>{run}</code>\n"
        f"👥 Total Users: <code>{len(active_users)}</code>\n"
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
        "⟡ <b>Premium Plans</b> ⟡\n\n"
        "<i>Premium Benefits:</i>\n"
        "◆ Admin Custom File Limit\n"
        "◆ 500MB Storage\n"
        "◆ Telegram Session Creation\n"
        "◆ Auto Restart (on crash)\n"
        "◆ Priority Approval\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Choose a plan:\n\n"
        + lines
    )

def build_status(uid, name):
    prem, lim, exp, days = get_sub_info(uid)
    files = user_files_db.get(uid, [])
    used  = get_user_storage_mb(uid)
    stor  = get_storage_limit(uid)
    plan  = "💎 Premium" if prem else "🆓 Free"
    return (
        "📊 <b>Your Status</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 @{h(name)}\n"
        f"🆔 ID: <code>{uid}</code>\n"
        f"⭐ Plan: {plan}\n"
        f"📅 Expires: <code>{h(exp)}</code> ({days} days)\n"
        f"📄 Scripts: <code>{len(files)}/{lim}</code>\n"
        f"🏃 Running: <code>{running_count_user(uid)}</code>\n"
        f"💾 Storage: <code>{used:.1f}MB/{stor}MB</code>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━"
    )

def build_help():
    return (
        "ℹ️ <b>Help</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        "📄 <b>Supported Files:</b>\n"
        "◆ <code>.py</code> — Python script\n"
        "◆ <code>.js</code> — Node.js script\n"
        "◆ <code>.php</code> — PHP script\n"
        "◆ <code>.zip</code> — Full project (auto-detects entry point)\n\n"
        "💎 <b>Premium Benefits:</b>\n"
        "◆ More files hosted\n"
        "◆ 500MB storage\n"
        "◆ Telethon/Pyrogram sessions\n"
        "◆ Auto-restart\n\n"
        "📌 <b>Commands:</b>\n"
        "◆ /start — Main menu\n"
        "◆ /status — My status\n"
        "◆ /ping — Bot alive check\n"
        "◆ /cancel — Cancel current action\n"
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
        "⚙️ <b>Bot Settings</b>\n"
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
        f"🚫 Banned ({h(banned_users[uid]['reason'])})"
        if uid in banned_users else "✅ Active"
    )
    uname  = info["username"] if info and info["username"] else str(uid)
    fname  = info["first_name"] if info and info["first_name"] else "-"
    joined = (info["joined_at"] or "")[:10] if info else "-"
    return (
        "⟡ <b>User Profile</b> ⟡\n\n"
        f"👤 @{h(uname)}\n"
        f"📛 Name: {h(fname)}\n"
        f"🆔 ID: <code>{uid}</code>\n"
        f"⭐ Plan: {plan}\n"
        f"📅 Expires: <code>{h(exp)}</code> ({days} days)\n"
        f"📄 Scripts: <code>{len(files)}/{lim}</code>\n"
        f"🏃 Running: <code>{running_count_user(uid)}</code>\n"
        f"💾 Storage: <code>{used:.1f}MB/{stor}MB</code>\n"
        f"📅 Joined: <code>{joined}</code>\n"
        f"🔒 Status: {ban}\n"
        "━━━━━━━━━━━━━━━━━━━━━━━"
    )

def build_session_menu_text(uid):
    sessions = db_get_sessions(uid)
    return (
        "🔑 <b>Create Session</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💾 Saved: <code>{len(sessions)}</code> sessions\n\n"
        "➕ Create a new session\n"
        "↳ Login with Phone + OTP to generate a session\n\n"
        "📋 View my sessions\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        "💡 Use the <code>.session</code> filename in your bot code."
    )

# ═══════════════════════════════════════════════════════════════
# ★ CUSTOM REPLY KEYBOARDS (main navigation)
# ═══════════════════════════════════════════════════════════════

def rkb_main(uid):
    """Main menu — custom keyboard."""
    m = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    m.add(
        types.KeyboardButton("🚀 Host Script"),
        types.KeyboardButton("👤 My Panel")
    )
    row2 = [
        types.KeyboardButton("📊 Status"),
        types.KeyboardButton("ℹ️ Help")
    ]
    if is_premium(uid):
        row2.append(types.KeyboardButton("🔑 Sessions"))
    m.add(*row2)
    if uid in admin_ids:
        m.add(types.KeyboardButton("👑 Admin Panel"))
    return m

def rkb_user_panel(uid):
    m = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    m.add(
        types.KeyboardButton("📁 My Scripts"),
        types.KeyboardButton("📊 Status")
    )
    if is_premium(uid):
        m.add(types.KeyboardButton("🔑 Sessions"))
    else:
        m.add(types.KeyboardButton("💎 Get Premium"))
    m.add(types.KeyboardButton("◀️ Back"))
    return m

def rkb_my_scripts():
    m = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    m.add(types.KeyboardButton("◀️ Back"))
    return m

def rkb_file_control(uid, slot):
    fi      = get_file_by_slot(uid, slot)
    running = is_running(uid, slot)
    m       = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    if fi and fi["status"] == "pending":
        m.add(types.KeyboardButton("⏳ Pending Approval..."))
    elif running:
        m.add(
            types.KeyboardButton("⏹️ Stop"),
            types.KeyboardButton("🔄 Restart")
        )
    else:
        m.add(types.KeyboardButton("▶️ Start"))
    m.add(
        types.KeyboardButton("📜 View Logs"),
        types.KeyboardButton("⚡ Live Stats")
    )
    m.add(
        types.KeyboardButton("🔄 Update File"),
        types.KeyboardButton("🗑️ Delete")
    )
    # ★ Module Installation button
    m.add(types.KeyboardButton("📦 Module Installation"))
    m.add(types.KeyboardButton("◀️ Back"))
    return m

def rkb_admin():
    m = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    m.add(
        types.KeyboardButton("📋 File Approvals"),
        types.KeyboardButton("💰 Payment Approvals")
    )
    m.add(
        types.KeyboardButton("👥 User Management"),
        types.KeyboardButton("🖥️ Server Stats")
    )
    m.add(
        types.KeyboardButton("📢 Broadcast"),
        types.KeyboardButton("⚙️ Bot Settings")
    )
    m.add(types.KeyboardButton("🗄️ Store"))
    m.add(types.KeyboardButton("◀️ Back"))
    return m

def rkb_store():
    """Store (Backup/Restore) panel keyboard."""
    ch_set = "✅" if (BACKUP_CHANNEL_ID and BACKUP_CHANNEL_ID != 0) else "❌"
    m = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    m.add(
        types.KeyboardButton("💾 Backup Now"),
        types.KeyboardButton("♻️ Restore from Channel")
    )
    m.add(
        types.KeyboardButton("📊 Backup Status"),
        types.KeyboardButton(f"📡 Channel {ch_set}")
    )
    m.add(types.KeyboardButton("◀️ Back"))
    return m

def rkb_admin_settings():
    m = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    m.add(
        types.KeyboardButton("💰 Price Management"),
        types.KeyboardButton("💳 Payment Methods")
    )
    m.add(
        types.KeyboardButton("🔒 Toggle Bot Lock"),
        types.KeyboardButton("🔧 Toggle Maintenance")
    )
    m.add(
        types.KeyboardButton("🤖 Set Free Limit"),
        types.KeyboardButton("⏱ Set Cooldown")
    )
    m.add(
        types.KeyboardButton("🗑️ Clear All Logs"),
        types.KeyboardButton("◀️ Back")
    )
    return m

def rkb_session_menu():
    m = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    m.add(
        types.KeyboardButton("➕ New Session"),
        types.KeyboardButton("📋 My Sessions")
    )
    m.add(
        types.KeyboardButton("🗑️ Delete Session"),
        types.KeyboardButton("◀️ Back")
    )
    return m

def rkb_back():
    m = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
    m.add(types.KeyboardButton("◀️ Back"))
    return m

def rkb_premium_plans():
    m = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    for p in db_get_plan_prices():
        m.add(types.KeyboardButton(f"📅 {p['label']} — ${p['price_usd']:.0f}"))
    m.add(types.KeyboardButton("◀️ Back"))
    return m

def rkb_pay_methods_user(months):
    rows  = db_get_payment_settings()
    icons = {"bkash": "💛", "nagad": "🟠", "rocket": "🟣", "usdt": "🟡"}
    m     = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
    for r in rows:
        if r["is_active"] and r["number_or_address"]:
            m.add(types.KeyboardButton(
                f"{icons.get(r['method_name'],'💳')} {r['display_name']}"
            ))
    m.add(types.KeyboardButton("◀️ Back"))
    return m

def rkb_broadcast():
    pm = sum(1 for u in active_users if is_premium(u) and u not in admin_ids)
    fr = len(active_users) - pm
    m  = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
    m.add(types.KeyboardButton(f"👥 All [{len(active_users)}]"))
    m.add(types.KeyboardButton(f"⭐ Premium Only [{pm}]"))
    m.add(types.KeyboardButton(f"🆓 Free Only [{fr}]"))
    m.add(types.KeyboardButton("👤 Specific User"))
    m.add(types.KeyboardButton("◀️ Back"))
    return m

def rkb_session_type():
    m = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    m.add(
        types.KeyboardButton("🐍 Telethon"),
        types.KeyboardButton("🔥 Pyrogram")
    )
    m.add(types.KeyboardButton("❌ Cancel"))
    return m

def rkb_prices():
    m = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
    for p in db_get_plan_prices():
        m.add(types.KeyboardButton(f"✏️ {p['label']}: ${p['price_usd']:.0f}"))
    m.add(types.KeyboardButton("◀️ Back"))
    return m

def rkb_payment_methods_admin():
    rows  = db_get_payment_settings()
    icons = {"bkash": "💛", "nagad": "🟠", "rocket": "🟣", "usdt": "🟡"}
    m     = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
    for r in rows:
        num = (r["number_or_address"] or "")[:12] or "Not set"
        act = "✅" if r["is_active"] else "❌"
        m.add(types.KeyboardButton(
            f"{icons.get(r['method_name'],'💳')} {r['display_name']} {act}"
        ))
    m.add(
        types.KeyboardButton("➕ Add New Method"),
    )
    m.add(types.KeyboardButton("◀️ Back"))
    return m

def remove_keyboard():
    return types.ReplyKeyboardRemove()

# ═══════════════════════════════════════════════════════════════
# INLINE KEYBOARDS — kept only where essential
# ═══════════════════════════════════════════════════════════════
def _b(text, cd, style="e"):
    styled_cd = f"{style}|{cd}"
    if len(styled_cd) > 64:
        styled_cd = cd
    return types.InlineKeyboardButton(text, callback_data=styled_cd)

def _bu(text, url):
    return types.InlineKeyboardButton(text, url=url)

def _strip_style(data):
    if len(data) > 2 and data[1] == "|" and data[0] in ("p", "s", "d", "e"):
        return data[2:]
    return data

def kb_approval(aid):
    """Admin file approval — stays inline."""
    m = types.InlineKeyboardMarkup(row_width=2)
    m.row(
        _b("✅ Approve", f"approve_{aid}", "s"),
        _b("❌ Reject",  f"reject_{aid}", "d")
    )
    return m

def kb_pay_approval(pid):
    """Admin payment approval — stays inline."""
    m = types.InlineKeyboardMarkup(row_width=2)
    m.row(
        _b("✅ Approve", f"pay_approve_{pid}", "s"),
        _b("❌ Reject",  f"pay_reject_{pid}", "d")
    )
    return m

def kb_confirm_delete(slot):
    """Confirm delete dialog — stays inline."""
    m = types.InlineKeyboardMarkup(row_width=2)
    m.row(
        _b("✅ Yes, Delete", f"fdelete_confirm_{slot}", "d"),
        _b("❌ Cancel", "cancel_delete", "e")
    )
    return m

def kb_my_scripts_inline(uid):
    """Script selection list — inline for compact script picking."""
    m = types.InlineKeyboardMarkup(row_width=1)
    for f in user_files_db.get(uid, []):
        if is_running(uid, f["slot"]):       icon = "🟢"
        elif f["status"] == "pending":       icon = "🟡"
        elif f["status"] == "rejected":      icon = "❌"
        else:                                icon = "🔴"
        m.add(_b(f"{icon} {f['file_name']}", f"fctl_{f['slot']}", "p"))
    return m

def kb_users_inline(page=0):
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
        nav_row.append(_b("⬅️ Prev", f"adm_users_page_{page-1}", "e"))
    if start + per < len(users):
        nav_row.append(_b("Next ➡️", f"adm_users_page_{page+1}", "e"))
    if nav_row:
        m.row(*nav_row)
    m.add(_b("🔍 Search by ID", "adm_search_user", "e"))
    return m, len(users)

def kb_user_profile_admin(target_uid):
    m = types.InlineKeyboardMarkup(row_width=2)
    m.row(
        _b("▶️ Start All", f"adm_startall_{target_uid}", "s"),
        _b("⏹️ Stop All",  f"adm_stopall_{target_uid}", "d")
    )
    m.row(
        _b("✉️ Send Message", f"adm_msg_{target_uid}", "p"),
        _b("⭐ Give Premium", f"adm_giveprem_{target_uid}", "s")
    )
    m.add(_b("💾 Set Storage", f"adm_setstorage_{target_uid}", "p"))
    if target_uid in banned_users:
        m.add(_b("✅ Unban", f"adm_unban_{target_uid}", "s"))
    else:
        m.add(_b("🚫 Ban", f"adm_ban_{target_uid}", "d"))
    return m

def kb_go_scripts():
    m = types.InlineKeyboardMarkup()
    m.add(_b("📁 My Scripts", "go_scripts", "p"))
    return m

def kb_retry_inline():
    m = types.InlineKeyboardMarkup()
    m.add(_b("🔄 Try Again", "upload_file_inline", "p"))
    return m

# ═══════════════════════════════════════════════════════════════
# STATE TRACKER — which screen a user is on (for custom kb routing)
# ═══════════════════════════════════════════════════════════════
_user_screen: dict = {}   # uid -> ("screen_name", optional slot)

def set_screen(uid, screen, slot=None):
    _user_screen[uid] = (screen, slot)

def get_screen(uid):
    return _user_screen.get(uid, ("main", None))

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
        return True, 999, "Unlimited", 9999
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

# ═══════════════════════════════════════════════════════════════
# ★ FAKE SECURITY SCAN
# ═══════════════════════════════════════════════════════════════
def send_fake_security_scan(cid):
    try:
        m = bot.send_message(
            cid,
            "✦ <b>File Processing</b> ✦\n\n"
            "🔵 Upload complete ✓\n"
            "⬜ Virus scan running...\n"
            "⬜ Code analysis...\n"
            "⬜ Security check...",
            parse_mode="HTML"
        )
        time.sleep(2)
        bot.edit_message_text(
            "✦ <b>File Processing</b> ✦\n\n"
            "🔵 Upload complete ✓\n"
            "🔵 Virus scan complete ✓\n"
            "⬜ Code analysis running...\n"
            "⬜ Security check...",
            cid, m.message_id, parse_mode="HTML"
        )
        time.sleep(2)
        bot.edit_message_text(
            "✦ <b>File Processing</b> ✦\n\n"
            "🔵 Upload complete ✓\n"
            "🔵 Virus scan complete ✓\n"
            "🔵 Code analysis complete ✓\n"
            "⬜ Security check running...",
            cid, m.message_id, parse_mode="HTML"
        )
        time.sleep(2)
        bot.edit_message_text(
            "✦ <b>File Processing</b> ✦\n\n"
            "🔵 Upload complete ✓\n"
            "🔵 Virus scan complete ✓\n"
            "🔵 Code analysis complete ✓\n"
            "🔵 Security check complete ✓\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━\n"
            "⚙️ <i>Preparing system deployment...</i>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "⏳ <b>Your script will start shortly.</b>",
            cid, m.message_id, parse_mode="HTML"
        )
    except Exception:
        pass

# ═══════════════════════════════════════════════════════════════
# ★ ADMIN NOTIFICATIONS
# ═══════════════════════════════════════════════════════════════
def notify_admins_approval(uid, fname, ftype, slot, aid, size_kb, username, is_prem):
    priority = "⭐ Priority (Premium)" if is_prem else "🆓 Free"
    caption = (
        "⟡ <b>New File Request</b> ⟡\n\n"
        f"👤 User: @{h(username)}\n"
        f"🆔 ID: <code>{uid}</code>\n"
        f"📄 File: <code>{h(fname)}</code>\n"
        f"📏 Size: <code>{size_kb:.1f} KB</code>\n"
        f"🔧 Type: <code>{ftype.upper()}</code>\n"
        f"🎯 Plan: {priority}\n"
        "━━━━━━━━━━━━━━━━━━━━━━━"
    )
    file_path = os.path.join(get_user_folder(uid), f"script_{slot}", fname)
    for adm in list(admin_ids):
        try:
            if os.path.exists(file_path):
                with open(file_path, "rb") as f:
                    bot.send_document(
                        adm, f, caption=caption,
                        reply_markup=kb_approval(aid), parse_mode="HTML"
                    )
            else:
                bot.send_message(
                    adm, caption, reply_markup=kb_approval(aid), parse_mode="HTML"
                )
        except Exception as e:
            logger.warning(f"notify_admins_approval admin={adm}: {e}")

def notify_admins_payment(uid, pay_id, months, amount, method, screenshot_id, username, msg):
    labels = {1: "1 Month", 3: "3 Months", 6: "6 Months", 12: "1 Year"}
    caption = (
        "⟡ <b>New Payment Request</b> ⟡\n\n"
        f"👤 User: @{h(username)}\n"
        f"🆔 ID: <code>{uid}</code>\n"
        f"📅 Plan: <b>{labels.get(months, str(months)+'m')}</b>\n"
        f"💰 Amount: <b>${amount:.0f} USDT</b>\n"
        f"💳 Method: <code>{h(method)}</code>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━"
    )
    for adm in list(admin_ids):
        try:
            sent = False
            try:
                bot.send_photo(
                    adm, screenshot_id, caption=caption,
                    reply_markup=kb_pay_approval(pay_id), parse_mode="HTML"
                )
                sent = True
            except Exception:
                pass
            if not sent:
                try:
                    bot.send_document(
                        adm, screenshot_id, caption=caption,
                        reply_markup=kb_pay_approval(pay_id), parse_mode="HTML"
                    )
                    sent = True
                except Exception:
                    pass
            if not sent:
                bot.send_message(
                    adm, caption, reply_markup=kb_pay_approval(pay_id), parse_mode="HTML"
                )
        except Exception as e:
            logger.warning(f"notify_admins_payment admin={adm}: {e}")

# ═══════════════════════════════════════════════════════════════
# SCRIPT RUNNER
# ═══════════════════════════════════════════════════════════════
TELEGRAM_MODULES = {
    "telebot": "pyTelegramBotAPI", "telegram": "python-telegram-bot",
    "aiogram": "aiogram", "pyrogram": "pyrogram", "telethon": "telethon",
    "tgcrypto": "tgcrypto", "telethon_crypto": "tgcrypto",
    "requests": "requests",
    "httpx": "httpx[http2]",
    "h2": "h2",
    "aiohttp": "aiohttp",
    "urllib3": "urllib3",
    "websockets": "websockets",
    "socks": "PySocks",
    "flask": "Flask", "flask_cors": "Flask-Cors",
    "fastapi": "fastapi", "uvicorn": "uvicorn",
    "django": "django",
    "bs4": "beautifulsoup4",
    "lxml": "lxml",
    "yaml": "PyYAML",
    "dotenv": "python-dotenv",
    "pandas": "pandas",
    "numpy": "numpy",
    "openpyxl": "openpyxl",
    "csv": None,
    "cv2": "opencv-python",
    "PIL": "Pillow",
    "pytesseract": "pytesseract",
    "psycopg2": "psycopg2-binary",
    "pymongo": "pymongo",
    "redis": "redis",
    "mysql": "mysql-connector-python",
    "sqlalchemy": "SQLAlchemy",
    "psutil": "psutil",
    "cryptography": "cryptography",
    "jwt": "PyJWT",
    "qrcode": "qrcode",
    "dateutil": "python-dateutil",
    "asyncio": None, "json": None, "os": None, "sys": None, "re": None,
    "time": None, "math": None, "random": None, "logging": None,
    "threading": None, "subprocess": None, "zipfile": None, "sqlite3": None,
    "datetime": None, "typing": None, "collections": None, "io": None,
    "base64": None, "hashlib": None, "uuid": None, "pathlib": None,
    "shutil": None, "glob": None, "traceback": None, "functools": None,
    "itertools": None, "contextlib": None, "socket": None, "ssl": None,
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
        safe_send(reply_msg.chat.id, "❌ Script not found.")
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
    elif ftype == "php":
        path = os.path.join(folder, fname)
        # Check php-cli available
        php_bin = shutil.which("php") or shutil.which("php8") or shutil.which("php7")
        if not php_bin:
            safe_send(reply_msg.chat.id,
                "❌ PHP CLI not found on this server.\n"
                "Ask admin to install: <code>sudo apt install php-cli</code>",
                parse_mode="HTML"
            )
            return
        cmd  = [php_bin, path]
    else:
        # ZIP — use smart_find_main_file
        main_rel, detected_type = smart_find_main_file(folder)
        if not main_rel:
            safe_send(reply_msg.chat.id, "❌ No entry-point found in project folder.")
            return
        path = os.path.join(folder, main_rel)
        if detected_type == "py":
            cmd = [sys.executable, path]
        elif detected_type == "js":
            cmd = ["node", path]
        elif detected_type == "php":
            php_bin = shutil.which("php") or shutil.which("php8") or shutil.which("php7")
            if not php_bin:
                safe_send(reply_msg.chat.id,
                    "❌ PHP CLI not found on this server.",
                    parse_mode="HTML"
                )
                return
            cmd = [php_bin, path]
        else:
            safe_send(reply_msg.chat.id, "❌ Unsupported file type in ZIP.")
            return

    if not os.path.exists(path):
        safe_send(reply_msg.chat.id, f"❌ File <code>{h(fname)}</code> not found.")
        return
    if attempt > 2:
        safe_send(reply_msg.chat.id, f"❌ Could not start <code>{h(fname)}</code>.")
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
                pkg_to_install = None
                mod_name       = None

                mo = re.search(r"ModuleNotFoundError: No module named '(.+?)'", err)
                if mo:
                    mod_name = mo.group(1).strip().strip("'\"").split(".")[0]
                    pkg_to_install = TELEGRAM_MODULES.get(mod_name.lower(), mod_name)

                if not mo:
                    ie = re.search(
                        r"ImportError:.*?'([a-zA-Z0-9_\-]+)'\s*"
                        r"(?:package\s*)?is not installed", err
                    )
                    if ie:
                        mod_name = ie.group(1).strip().strip("'\"")
                        pkg_to_install = TELEGRAM_MODULES.get(mod_name.lower(), mod_name)

                if not mo and not pkg_to_install:
                    pip_hint = re.search(r"pip install ([a-zA-Z0-9_\-\[\]]+)", err)
                    if pip_hint:
                        pkg_to_install = pip_hint.group(1)
                        mod_name       = pkg_to_install

                if pkg_to_install:
                    logger.info(f"Auto-install attempt: {pkg_to_install} (module={mod_name})")
                    if _pip_silent(pkg_to_install):
                        logger.info(f"Installed OK: {pkg_to_install}")
                        time.sleep(1)
                        threading.Thread(
                            target=run_script,
                            args=(uid, slot, reply_msg, 2),
                            daemon=True
                        ).start()
                    else:
                        safe_send(
                            reply_msg.chat.id,
                            f"❌ Failed to install package <code>{h(pkg_to_install)}</code>.\n\n"
                            f"💡 Use <b>📦 Module Installation</b> to install it manually."
                        )
                    return

                safe_send(
                    reply_msg.chat.id,
                    f"❌ Script error:\n<pre>{h(err[:600])}</pre>"
                )
                return

        except subprocess.TimeoutExpired:
            if chk:
                chk.kill()
                chk.communicate()
        except FileNotFoundError:
            safe_send(reply_msg.chat.id, "❌ Python/Node not found.")
            return
        finally:
            if chk and chk.poll() is None:
                chk.kill()
                chk.communicate()

    log_path = os.path.join(folder, "logs.txt")
    try:
        lf = open(log_path, "w", encoding="utf-8", errors="ignore")
    except Exception as e:
        safe_send(reply_msg.chat.id, f"❌ Could not create log file: {e}")
        return
    try:
        proc = subprocess.Popen(
            cmd, cwd=folder, stdout=lf, stderr=lf,
            text=True, encoding="utf-8", errors="ignore"
        )
    except FileNotFoundError:
        lf.close()
        safe_send(reply_msg.chat.id, "❌ Python/Node not found.")
        return

    bot_scripts[key] = {
        "proc": proc, "log_file": lf, "fname": fname,
        "folder": folder, "started": datetime.now()
    }
    db_update_file_status(uid, slot, "running")
    safe_send(
        reply_msg.chat.id,
        f"✅ <b>Deployment complete!</b>\n\n"
        f"🟢 <b>{h(fname)}</b> is now running!\n"
        f"🔢 PID: <code>{proc.pid}</code>"
    )
    logger.info(f"Script started: uid={uid} slot={slot} pid={proc.pid}")
    if is_premium(uid):
        threading.Thread(
            target=_auto_restart_monitor,
            args=(uid, slot, reply_msg),
            daemon=True
        ).start()

def _auto_restart_monitor(uid, slot, reply_msg, crash_count=0):
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
                        f"⚠️ <code>{h(info.get('fname',''))}</code> crashed 5 times. Please check manually.",
                        parse_mode="HTML"
                    )
                except Exception:
                    pass
                break
            logger.warning(f"Script crashed #{crash_count}: uid={uid} slot={slot}")
            try:
                bot.send_message(
                    uid,
                    f"⚠️ <code>{h(info.get('fname',''))}</code> crashed. Restarting...",
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
    _stop_script_key(uid, slot)
    folder = os.path.join(get_user_folder(uid), f"script_{slot}")
    if os.path.exists(folder):
        shutil.rmtree(folder, ignore_errors=True)
    db_del_file(uid, slot)
    logger.info(f"Hard delete: uid={uid} slot={slot}")

def smart_find_main_file(folder):
    """
    Intelligently find the main entry-point file in an extracted ZIP.
    Priority order:
      1. Priority names: main.py, app.py, bot.py, index.py, run.py,
                         start.py, server.py, main.js, index.js, app.js
      2. Single .py/.js/.php file in root
      3. Scan for 'if __name__' or bot.polling/app.run patterns
      4. Any .py / .js / .php in root (first found)
      5. Recurse into subdirs (nested project)
    Returns (file_path_relative_to_folder, file_type) or (None, None)
    """
    PRIORITY_NAMES = [
        "main.py","app.py","bot.py","index.py","run.py","start.py",
        "server.py","core.py","launcher.py",
        "main.js","index.js","app.js","server.js","bot.js",
        "main.php","index.php","app.php","server.php",
    ]

    def ext_to_type(f):
        e = os.path.splitext(f)[1].lower()
        return {"py":"py","js":"js","php":"php"}.get(e.lstrip("."))

    # ── 1. Priority names at root ──────────────────────────────
    root_files = [f for f in os.listdir(folder) if os.path.isfile(os.path.join(folder, f))]
    for name in PRIORITY_NAMES:
        if name in root_files:
            return name, ext_to_type(name)

    # ── 2. Only one code file at root ──────────────────────────
    code_files = [f for f in root_files if ext_to_type(f) is not None]
    if len(code_files) == 1:
        return code_files[0], ext_to_type(code_files[0])

    # ── 3. Heuristic scan — look for entry-point patterns ──────
    py_candidates = [f for f in code_files if f.endswith(".py")]
    js_candidates = [f for f in code_files if f.endswith(".js")]
    php_candidates = [f for f in code_files if f.endswith(".php")]

    ENTRY_PATTERNS_PY = [
        r"if\s+__name__\s*==\s*['\"]__main__['\"]",
        r"bot\.infinity_polling\b", r"bot\.polling\b",
        r"app\.run\b", r"updater\.start_polling\b",
        r"client\.run\b", r"asyncio\.run\b",
        r"uvicorn\.run\b", r"execute\b",
    ]
    ENTRY_PATTERNS_JS = [
        r"bot\.launch\b", r"app\.listen\b",
        r"client\.login\b", r"\.connect\(",
    ]
    ENTRY_PATTERNS_PHP = [
        r"\$bot\s*=\s*new\b", r"Longman\\Telegram",
        r"Telegram\\Bot", r"\$telegram\b",
    ]

    def _score_file(path, patterns):
        try:
            content = open(path, "r", encoding="utf-8", errors="ignore").read()
            return sum(1 for p in patterns if re.search(p, content))
        except Exception:
            return 0

    # Score Python files
    if py_candidates:
        scored = [(f, _score_file(os.path.join(folder, f), ENTRY_PATTERNS_PY))
                  for f in py_candidates]
        best = max(scored, key=lambda x: x[1])
        if best[1] > 0:
            return best[0], "py"

    # Score JS files
    if js_candidates:
        scored = [(f, _score_file(os.path.join(folder, f), ENTRY_PATTERNS_JS))
                  for f in js_candidates]
        best = max(scored, key=lambda x: x[1])
        if best[1] > 0:
            return best[0], "js"

    # Score PHP files
    if php_candidates:
        scored = [(f, _score_file(os.path.join(folder, f), ENTRY_PATTERNS_PHP))
                  for f in php_candidates]
        best = max(scored, key=lambda x: x[1])
        if best[1] > 0:
            return best[0], "php"

    # ── 4. Any code file at root ───────────────────────────────
    for ext_list, ftype in [(py_candidates,"py"),(js_candidates,"js"),(php_candidates,"php")]:
        if ext_list:
            return ext_list[0], ftype

    # ── 5. Recurse one level into subdirs ──────────────────────
    subdirs = [d for d in os.listdir(folder)
               if os.path.isdir(os.path.join(folder, d)) and not d.startswith(".")]
    for sub in subdirs:
        sub_path = os.path.join(folder, sub)
        result, ftype = smart_find_main_file(sub_path)
        if result:
            return os.path.join(sub, result), ftype

    return None, None


def handle_zip_extract(content, fname, uid, slot, folder, reply_msg):
    try:
        zp = os.path.join(folder, fname)
        with open(zp, "wb") as f:
            f.write(content)
        with zipfile.ZipFile(zp, "r") as zf:
            zf.extractall(folder)
        os.remove(zp)

        # ★ Smart main file detection
        main_rel, detected_type = smart_find_main_file(folder)
        if not main_rel:
            safe_send(
                reply_msg.chat.id,
                "❌ Could not find a main entry-point file in the ZIP.\n\n"
                "Please make sure your ZIP contains one of:\n"
                "<code>main.py / app.py / bot.py / index.py / main.js</code>\n"
                "or any <code>.py .js .php</code> file with an entry-point.",
                parse_mode="HTML"
            )
            db_update_file_status(uid, slot, "rejected")
            return

        # Save detected main file into DB slot info for run_script to use
        with DB_LOCK:
            c = _db()
            c.execute(
                "UPDATE user_files SET file_name=?, file_type=? WHERE user_id=? AND slot=?",
                (os.path.basename(main_rel), detected_type, uid, slot)
            )
            c.commit()
            c.close()
        for f in user_files_db.get(uid, []):
            if f["slot"] == slot:
                f["file_name"] = os.path.basename(main_rel)
                f["file_type"] = detected_type

        safe_send(
            reply_msg.chat.id,
            f"📦 <b>ZIP extracted.</b>\n"
            f"🎯 Detected entry point: <code>{h(main_rel)}</code> ({detected_type.upper()})",
            parse_mode="HTML"
        )
        db_update_file_status(uid, slot, "approved")
        threading.Thread(target=run_script, args=(uid, slot, reply_msg), daemon=True).start()

    except zipfile.BadZipFile:
        safe_send(reply_msg.chat.id, "❌ ZIP file is corrupted.")
        db_update_file_status(uid, slot, "rejected")
    except Exception as e:
        safe_send(reply_msg.chat.id, f"❌ ZIP extract failed: {h(str(e))}")

# ═══════════════════════════════════════════════════════════════
# BACKGROUND THREADS
# ═══════════════════════════════════════════════════════════════
def _expiry_checker():
    while True:
        try:
            now = datetime.now()
            for uid, sub in list(user_subscriptions.items()):
                if uid in admin_ids:
                    continue
                expiry = sub["expiry"]
                days_left = (expiry - now).days

                if days_left == 3 and not db_expiry_notif_sent(uid, "3day"):
                    try:
                        bot.send_message(
                            uid,
                            "⚠️ <b>Premium Expiry Warning!</b>\n\n"
                            "Your Premium expires in <b>3 days</b>.\n"
                            "Renew your plan to keep your scripts running.",
                            parse_mode="HTML"
                        )
                        db_mark_expiry_notif(uid, "3day")
                    except Exception:
                        pass

                elif days_left == 1 and not db_expiry_notif_sent(uid, "1day"):
                    try:
                        bot.send_message(
                            uid,
                            "🔴 <b>Final Warning!</b>\n\n"
                            "Your Premium expires <b>tomorrow</b>.\n"
                            "All scripts will stop if you don't renew!",
                            parse_mode="HTML"
                        )
                        db_mark_expiry_notif(uid, "1day")
                    except Exception:
                        pass

                elif expiry <= now and not db_expiry_notif_sent(uid, "expired"):
                    files = user_files_db.get(uid, [])
                    for f in files:
                        _stop_script_key(uid, f["slot"])
                    try:
                        bot.send_message(
                            uid,
                            "🔴 <b>Premium Expired!</b>\n\n"
                            "All your scripts have been stopped.\n"
                            "Purchase a new plan to continue.",
                            parse_mode="HTML"
                        )
                        db_mark_expiry_notif(uid, "expired")
                    except Exception:
                        pass
        except Exception as e:
            logger.error(f"expiry_checker error: {e}")
        time.sleep(3600)

def _cleanup_thread():
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
        bot.reply_to(msg, "🚫 You have been banned from this bot.")
        return
    if maintenance_mode and uid not in admin_ids:
        bot.reply_to(msg, "🔧 Bot is under maintenance. Please try later.")
        return

    db_add_user(uid, username, fname)
    get_user_folder(uid)
    set_screen(uid, "main")
    bot.send_message(uid, build_welcome(uid, name), reply_markup=rkb_main(uid), parse_mode="HTML")

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
    _pkg_install_state.pop(uid, None)
    set_screen(uid, "main")
    bot.reply_to(msg, "✅ Cancelled.", reply_markup=rkb_main(uid))

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
        bot.reply_to(msg, "❌ Usage: <code>/setpremium USER_ID LIMIT DAYS</code>", parse_mode="HTML")
        return
    try:
        target, lim, days = int(parts[1]), int(parts[2]), int(parts[3])
    except ValueError:
        bot.reply_to(msg, "❌ Use numbers.")
        return
    exp = datetime.now() + timedelta(days=days)
    db_save_sub(target, lim, exp, msg.from_user.id)
    db_clear_expiry_notifs(target)
    bot.reply_to(
        msg,
        f"✅ Premium granted to <code>{target}</code>.",
        parse_mode="HTML"
    )
    try:
        bot.send_message(
            target,
            f"🎉 <b>Your Premium is now active!</b>\n"
            f"🔢 Limit: {lim}\n"
            f"📅 Expires: {exp.strftime('%Y-%m-%d')}",
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
        bot.reply_to(msg, "❌ Use a number.")
        return
    db_del_sub(target)
    bot.reply_to(msg, f"✅ Premium removed from <code>{target}</code>.", parse_mode="HTML")

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
        bot.reply_to(msg, "❌ Use a number.")
        return
    if target == OWNER_ID:
        bot.reply_to(msg, "❌ Cannot ban the owner.")
        return
    reason = parts[2] if len(parts) > 2 else "Policy violation"
    db_ban_user(target, reason, msg.from_user.id)
    bot.reply_to(msg, f"✅ User <code>{target}</code> banned.", parse_mode="HTML")

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
        bot.reply_to(msg, "❌ Use a number.")
        return
    db_unban_user(target)
    bot.reply_to(msg, f"✅ User <code>{target}</code> unbanned.", parse_mode="HTML")

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
        bot.reply_to(msg, "❌ Use a number.")
        return
    bot.send_message(
        msg.chat.id, build_user_profile(target),
        reply_markup=kb_user_profile_admin(target), parse_mode="HTML"
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
        bot.reply_to(msg, "❌ Use a number.")
        return
    db_add_admin(target, msg.from_user.id)
    bot.reply_to(msg, f"✅ User <code>{target}</code> is now admin.", parse_mode="HTML")

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
        bot.reply_to(msg, "❌ Use a number.")
        return
    if target == OWNER_ID:
        bot.reply_to(msg, "❌ Cannot remove owner from admin.")
        return
    db_del_admin(target)
    bot.reply_to(msg, f"✅ User <code>{target}</code> removed from admin.", parse_mode="HTML")

@bot.message_handler(commands=["broadcast"])
def cmd_broadcast_cmd(msg):
    if msg.from_user.id not in admin_ids:
        return
    parts = msg.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(msg, "❌ /broadcast message")
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
    bot.reply_to(msg, f"✅ Success: {ok} | ❌ Failed: {fail}")

# ═══════════════════════════════════════════════════════════════
# ★ CUSTOM KEYBOARD TEXT ROUTER
# ═══════════════════════════════════════════════════════════════
@bot.message_handler(func=lambda m: m.text and not m.text.startswith("/"))
def handle_text(msg):
    uid  = msg.from_user.id
    text = msg.text.strip()

    if uid in banned_users:
        return
    if maintenance_mode and uid not in admin_ids:
        bot.reply_to(msg, "🔧 Bot under maintenance.")
        return

    name = msg.from_user.username or msg.from_user.first_name or "User"

    # ── pkg install waiting state ──────────────────────────────
    if uid in _pkg_install_state:
        slot = _pkg_install_state.pop(uid)
        _do_manual_pkg_install(msg, uid, slot, text)
        return

    # ── session/payment/broadcast multi-step states ─────────────
    # These are handled via register_next_step_handler — pass through
    # only if no active state
    screen, slot = get_screen(uid)

    # ── MAIN MENU buttons ──────────────────────────────────────
    if text == "🚀 Host Script":
        set_screen(uid, "upload")
        bot.send_message(
            msg.chat.id,
            "📁 <b>Upload a File</b>\n\n"
            "◆ <code>.py</code> — Python script\n"
            "◆ <code>.js</code> — Node.js script\n"
            "◆ <code>.php</code> — PHP script\n"
            "◆ <code>.zip</code> — Full project (max 20MB, auto entry-point detect)\n\n"
            "<i>Send your file now...</i>",
            parse_mode="HTML",
            reply_markup=rkb_back()
        )

    elif text == "👤 My Panel":
        set_screen(uid, "user_panel")
        bot.send_message(
            msg.chat.id, build_user_panel(uid, name),
            reply_markup=rkb_user_panel(uid), parse_mode="HTML"
        )

    elif text == "📊 Status":
        bot.send_message(
            msg.chat.id, build_status(uid, name), parse_mode="HTML"
        )

    elif text == "ℹ️ Help":
        bot.send_message(msg.chat.id, build_help(), parse_mode="HTML")

    elif text == "🔑 Sessions":
        if not is_premium(uid):
            bot.send_message(msg.chat.id, "💎 Premium required for sessions.")
            return
        set_screen(uid, "session_menu")
        bot.send_message(
            msg.chat.id, build_session_menu_text(uid),
            reply_markup=rkb_session_menu(), parse_mode="HTML"
        )

    elif text == "👑 Admin Panel":
        if uid not in admin_ids:
            return
        set_screen(uid, "admin_panel")
        bot.send_message(
            msg.chat.id, build_admin_panel(),
            reply_markup=rkb_admin(), parse_mode="HTML"
        )

    # ── USER PANEL buttons ─────────────────────────────────────
    elif text == "📁 My Scripts":
        set_screen(uid, "my_scripts")
        files = user_files_db.get(uid, [])
        if not files:
            bot.send_message(
                msg.chat.id, build_my_scripts(uid),
                reply_markup=rkb_back(), parse_mode="HTML"
            )
        else:
            bot.send_message(
                msg.chat.id, build_my_scripts(uid),
                reply_markup=rkb_my_scripts(), parse_mode="HTML"
            )
            bot.send_message(
                msg.chat.id,
                "📌 Select a script to control:",
                reply_markup=kb_my_scripts_inline(uid),
                parse_mode="HTML"
            )

    elif text == "💎 Get Premium":
        set_screen(uid, "buy_premium")
        bot.send_message(
            msg.chat.id, build_premium_plans(),
            reply_markup=rkb_premium_plans(), parse_mode="HTML"
        )

    # ── FILE CONTROL buttons ────────────────────────────────────
    elif text == "▶️ Start" and screen == "file_control" and slot:
        fi = get_file_by_slot(uid, slot)
        if fi and fi["status"] == "pending":
            bot.send_message(msg.chat.id, "⏳ Still under review.")
            return
        if fi and fi["status"] == "rejected":
            bot.send_message(msg.chat.id, "❌ File was rejected.")
            return
        threading.Thread(target=run_script, args=(uid, slot, msg), daemon=True).start()

    elif text == "⏹️ Stop" and screen == "file_control" and slot:
        if _stop_script_key(uid, slot):
            fi = get_file_by_slot(uid, slot)
            bot.send_message(
                msg.chat.id,
                f"⏹️ <b>{h(fi['file_name'] if fi else str(slot))}</b> stopped.",
                parse_mode="HTML"
            )
            bot.send_message(
                msg.chat.id, build_file_control(uid, slot),
                reply_markup=rkb_file_control(uid, slot), parse_mode="HTML"
            )
        else:
            bot.send_message(msg.chat.id, "⚠️ Process not found.")

    elif text == "🔄 Restart" and screen == "file_control" and slot:
        _stop_script_key(uid, slot)
        threading.Thread(target=run_script, args=(uid, slot, msg), daemon=True).start()

    elif text == "📜 View Logs" and screen == "file_control" and slot:
        fi       = get_file_by_slot(uid, slot)
        log_path = os.path.join(get_user_folder(uid), f"script_{slot}", "logs.txt")
        if not fi or not os.path.exists(log_path):
            bot.send_message(msg.chat.id, "📭 No logs yet.")
            return
        try:
            content = open(log_path, "r", encoding="utf-8", errors="ignore").read()
            if not content.strip():
                bot.send_message(msg.chat.id, "📭 Log file is empty.")
                return
            bot.send_message(
                msg.chat.id,
                f"📜 <b>{h(fi['file_name'])} — Logs</b>\n\n<pre>{h(content[-3000:])}</pre>",
                parse_mode="HTML"
            )
        except Exception as e:
            bot.send_message(msg.chat.id, f"❌ Failed to read log: {e}")

    elif text == "⚡ Live Stats" and screen == "file_control" and slot:
        key  = f"{uid}_{slot}"
        info = bot_scripts.get(key)
        if not info or not info.get("proc") or info["proc"].poll() is not None:
            bot.send_message(msg.chat.id, "⚠️ Script is not running.")
            return
        try:
            p   = psutil.Process(info["proc"].pid)
            cpu = p.cpu_percent(interval=0.5)
            ram = p.memory_info().rss / 1024 / 1024
            el  = datetime.now() - info.get("started", datetime.now())
            d, r = divmod(int(el.total_seconds()), 86400)
            hh, r = divmod(r, 3600)
            mm, ss = divmod(r, 60)
            bot.send_message(
                msg.chat.id,
                f"⚡ <b>Live Stats — {h(info.get('fname',''))}</b>\n\n"
                f"🖥 CPU: <code>{cpu:.1f}%</code>\n"
                f"🧠 RAM: <code>{ram:.1f} MB</code>\n"
                f"🔢 PID: <code>{info['proc'].pid}</code>\n"
                f"⏱ Running: <code>{d}d {hh}h {mm}m {ss}s</code>",
                parse_mode="HTML"
            )
        except Exception as e:
            bot.send_message(msg.chat.id, f"❌ Could not fetch stats: {e}")

    elif text == "🔄 Update File" and screen == "file_control" and slot:
        fi = get_file_by_slot(uid, slot)
        _stop_script_key(uid, slot)
        _pending_update_slot[uid] = slot
        set_screen(uid, "upload")
        bot.send_message(
            msg.chat.id,
            f"🔄 <b>Update — {h(fi['file_name'] if fi else str(slot))}</b>\n\n"
            "Send the new file (old file will be replaced):",
            reply_markup=rkb_back(), parse_mode="HTML"
        )

    elif text == "🗑️ Delete" and screen == "file_control" and slot:
        fi = get_file_by_slot(uid, slot)
        bot.send_message(
            msg.chat.id,
            f"⚠️ <b>Confirm Delete</b>\n\n"
            f"<code>{h(fi['file_name'] if fi else str(slot))}</code> will be permanently deleted.\n"
            "All files and data will be removed.",
            reply_markup=kb_confirm_delete(slot),
            parse_mode="HTML"
        )

    # ★ MODULE INSTALLATION button
    elif text == "📦 Module Installation" and screen == "file_control" and slot:
        _pkg_install_state[uid] = slot
        bot.send_message(
            msg.chat.id,
            "📦 <b>Module Installation</b>\n\n"
            "Enter the package name to install:\n"
            "<i>(e.g. requests, aiohttp, Pillow)</i>\n\n"
            "Send /cancel to abort.",
            parse_mode="HTML",
            reply_markup=rkb_back()
        )

    # ── ADMIN PANEL buttons ────────────────────────────────────
    elif text == "📋 File Approvals" and uid in admin_ids:
        _show_adm_approvals(msg)

    elif text == "💰 Payment Approvals" and uid in admin_ids:
        _show_adm_payments(msg)

    elif text == "👥 User Management" and uid in admin_ids:
        set_screen(uid, "admin_users")
        m, total = kb_users_inline(0)
        bot.send_message(
            msg.chat.id,
            f"👥 <b>User Management</b>\n\nTotal users: <code>{total}</code>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━\nPage 1:",
            reply_markup=m, parse_mode="HTML"
        )

    elif text == "🖥️ Server Stats" and uid in admin_ids:
        bot.send_message(msg.chat.id, build_server_stats(), parse_mode="HTML")

    elif text == "📢 Broadcast" and uid in admin_ids:
        set_screen(uid, "broadcast")
        bot.send_message(
            msg.chat.id,
            "📢 <b>Broadcast</b>\n\nWho do you want to send to?",
            reply_markup=rkb_broadcast(), parse_mode="HTML"
        )

    elif text == "⚙️ Bot Settings" and uid in admin_ids:
        set_screen(uid, "admin_settings")
        bot.send_message(
            msg.chat.id, build_admin_settings(),
            reply_markup=rkb_admin_settings(), parse_mode="HTML"
        )

    # ── STORE (Backup/Restore) panel ───────────────────────────
    elif text == "🗄️ Store" and uid in admin_ids:
        set_screen(uid, "store")
        bot.send_message(
            msg.chat.id,
            _build_store_panel(),
            reply_markup=rkb_store(), parse_mode="HTML"
        )

    elif text == "💾 Backup Now" and uid in admin_ids:
        if not _backup_enabled():
            bot.send_message(
                msg.chat.id,
                "❌ <b>Backup channel not set.</b>\n\n"
                "Set <code>BACKUP_CHANNEL_ID</code> in the script config.\n"
                "Bot must be admin in that channel.",
                parse_mode="HTML", reply_markup=rkb_store()
            )
            return
        bot.send_message(msg.chat.id, "⏳ Starting backup...", reply_markup=rkb_store())
        threading.Thread(target=tg_full_backup, args=(uid,), daemon=True).start()

    elif text == "♻️ Restore from Channel" and uid in admin_ids:
        if not _backup_enabled():
            bot.send_message(
                msg.chat.id,
                "❌ Backup channel not configured.",
                reply_markup=rkb_store()
            )
            return
        # Confirmation step
        set_screen(uid, "store_confirm_restore")
        confirm_kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
        confirm_kb.add(
            types.KeyboardButton("✅ Yes, Restore"),
            types.KeyboardButton("❌ Cancel")
        )
        bot.send_message(
            msg.chat.id,
            "⚠️ <b>Restore Confirmation</b>\n\n"
            "This will <b>overwrite</b> the current database and all script files "
            "with the backup from the channel.\n\n"
            "Running scripts will be stopped.\n\n"
            "Are you sure?",
            reply_markup=confirm_kb, parse_mode="HTML"
        )

    elif text == "✅ Yes, Restore" and screen == "store_confirm_restore" and uid in admin_ids:
        if not _backup_enabled():
            bot.send_message(msg.chat.id, "❌ Backup channel not configured.", reply_markup=rkb_store())
            set_screen(uid, "store")
            return
        set_screen(uid, "store")
        bot.send_message(msg.chat.id, "⏳ Restoring from backup channel...", reply_markup=rkb_store())
        threading.Thread(target=tg_restore_all, args=(uid,), daemon=True).start()

    elif text == "❌ Cancel" and screen == "store_confirm_restore" and uid in admin_ids:
        set_screen(uid, "store")
        bot.send_message(
            msg.chat.id, "✅ Restore cancelled.",
            reply_markup=rkb_store()
        )

    elif text == "📊 Backup Status" and uid in admin_ids:
        total_scripts = sum(
            1 for files in user_files_db.values()
            for f in files if f["status"] not in ("pending", "rejected")
        )
        try:
            db_size = f"{os.path.getsize(DATABASE_PATH)//1024} KB"
        except Exception:
            db_size = "N/A"
        ch = str(BACKUP_CHANNEL_ID) if _backup_enabled() else "❌ Not configured"
        bot.send_message(
            msg.chat.id,
            f"📊 <b>Backup Status</b>\n\n"
            f"📡 Channel ID: <code>{ch}</code>\n"
            f"💾 DB size: <code>{db_size}</code>\n"
            f"📁 Script slots: <code>{total_scripts}</code>\n"
            f"🕐 Auto-backup: <b>every 1 hour</b>\n\n"
            f"<b>Commands:</b>\n"
            f"◆ /backup — Backup now\n"
            f"◆ /restore — Restore warning\n"
            f"◆ /restore_confirm — Confirm restore",
            parse_mode="HTML", reply_markup=rkb_store()
        )

    elif text.startswith("📡 Channel") and uid in admin_ids:
        # Show channel setup guide
        bot.send_message(
            msg.chat.id,
            "📡 <b>Backup Channel Setup</b>\n\n"
            "1️⃣ Create a <b>private Telegram channel</b>\n"
            "2️⃣ Add this bot as <b>admin</b> (Post Messages permission)\n"
            "3️⃣ Get the channel ID:\n"
            "   ◆ Forward any message from channel to @userinfobot\n"
            "   ◆ Or use @JsonDumpBot\n"
            "   ◆ ID looks like: <code>-1001234567890</code>\n"
            "4️⃣ Set in config:\n"
            "   <code>BACKUP_CHANNEL_ID = -1001234567890</code>\n\n"
            f"Current: <code>{'Set ✅' if _backup_enabled() else 'Not set ❌'}</code>",
            parse_mode="HTML", reply_markup=rkb_store()
        )

    # ── ADMIN SETTINGS buttons ──────────────────────────────────
    elif text == "💰 Price Management" and uid in admin_ids:
        bot.send_message(
            msg.chat.id,
            "💰 <b>Price Management</b>\n\nSelect a plan to edit:",
            reply_markup=rkb_prices(), parse_mode="HTML"
        )

    elif text == "💳 Payment Methods" and uid in admin_ids:
        bot.send_message(
            msg.chat.id,
            "💳 <b>Payment Methods</b>\n\nSelect to configure:",
            reply_markup=rkb_payment_methods_admin(), parse_mode="HTML"
        )

    elif text.startswith("✏️") and uid in admin_ids and screen == "admin_settings":
        # e.g. "✏️ 1 Month: $5"
        for p in db_get_plan_prices():
            if text == f"✏️ {p['label']}: ${p['price_usd']:.0f}":
                bot.send_message(
                    msg.chat.id,
                    f"✏️ <b>Enter new price for {h(p['label'])}</b>\n"
                    "<i>(numbers only, e.g. 7.5)</i>:",
                    reply_markup=rkb_back(), parse_mode="HTML"
                )
                bot.register_next_step_handler_by_chat_id(
                    msg.chat.id, lambda m, mo=p["months"]: _save_price(m, uid, mo)
                )
                return

    elif text.startswith("💛") or text.startswith("🟠") or text.startswith("🟣") or text.startswith("🟡"):
        # Payment method selection (admin config or user payment)
        if uid in admin_ids and screen == "admin_settings":
            rows = db_get_payment_settings()
            for r in rows:
                icons = {"bkash": "💛", "nagad": "🟠", "rocket": "🟣", "usdt": "🟡"}
                if text.startswith(f"{icons.get(r['method_name'],'💳')} {r['display_name']}"):
                    bot.send_message(
                        msg.chat.id,
                        f"📱 <b>Enter new number/address for {h(r['display_name'])}:</b>",
                        reply_markup=rkb_back(), parse_mode="HTML"
                    )
                    bot.register_next_step_handler_by_chat_id(
                        msg.chat.id,
                        lambda m, method=r["method_name"]: _save_pay_method(m, uid, method)
                    )
                    return
        # User payment flow
        elif uid in payment_state:
            state = payment_state.get(uid)
            rows = db_get_payment_settings()
            icons = {"bkash": "💛", "nagad": "🟠", "rocket": "🟣", "usdt": "🟡"}
            for r in rows:
                btn_text = f"{icons.get(r['method_name'],'💳')} {r['display_name']}"
                if text == btn_text and r["is_active"] and r["number_or_address"]:
                    months = state.get("months")
                    prices  = {p["months"]: p["price_usd"] for p in db_get_plan_prices()}
                    price   = prices.get(months, 0)
                    payment_state[uid]["method"] = r["display_name"]
                    payment_state[uid]["amount"] = price
                    number = r["number_or_address"]
                    bot.send_message(
                        msg.chat.id,
                        f"⟡ <b>Payment Instructions</b> ⟡\n\n"
                        f"{btn_text}\n\n"
                        "━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"📱 Number/Address: <code>{h(number)}</code>\n"
                        f"💰 Amount: <b>${price:.0f} USDT</b>\n"
                        f"📝 Reference: <code>UID_{uid}</code>\n"
                        "━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                        "📌 <b>Instructions:</b>\n"
                        "1. Send payment to the number above\n"
                        "2. Include your ID in the reference\n"
                        "3. Take a screenshot\n"
                        "4. Send the screenshot here\n\n"
                        "<i>Send payment screenshot now...</i>",
                        reply_markup=rkb_back(), parse_mode="HTML"
                    )
                    return

    elif text.startswith("📅") and screen == "buy_premium":
        # Plan selection e.g. "📅 1 Month — $5"
        for p in db_get_plan_prices():
            if text == f"📅 {p['label']} — ${p['price_usd']:.0f}":
                months  = p["months"]
                price   = p["price_usd"]
                methods = [
                    r for r in db_get_payment_settings()
                    if r["is_active"] and r["number_or_address"]
                ]
                if not methods:
                    bot.send_message(
                        msg.chat.id,
                        f"⚠️ No payment methods available.\nContact admin: {ADMIN_USERNAME}",
                        reply_markup=rkb_back()
                    )
                    return
                payment_state[uid] = {"months": months, "amount": price, "method": None}
                bot.send_message(
                    msg.chat.id,
                    f"⟡ <b>Payment Method</b> ⟡\n\n"
                    f"📅 Selected: <b>{h(p['label'])} (${price:.0f} USDT)</b>\n\n"
                    "Choose payment method:",
                    reply_markup=rkb_pay_methods_user(months), parse_mode="HTML"
                )
                return

    elif text == "🔒 Toggle Bot Lock" and uid in admin_ids:
        global bot_locked
        bot_locked = not bot_locked
        db_set_setting("bot_locked", "1" if bot_locked else "0")
        bot.send_message(
            msg.chat.id,
            f"🔒 Bot {'locked' if bot_locked else 'unlocked'}.",
        )
        bot.send_message(
            msg.chat.id, build_admin_settings(),
            reply_markup=rkb_admin_settings(), parse_mode="HTML"
        )

    elif text == "🔧 Toggle Maintenance" and uid in admin_ids:
        global maintenance_mode
        maintenance_mode = not maintenance_mode
        db_set_setting("maintenance", "1" if maintenance_mode else "0")
        bot.send_message(
            msg.chat.id,
            f"🔧 Maintenance {'ON' if maintenance_mode else 'OFF'}.",
        )
        bot.send_message(
            msg.chat.id, build_admin_settings(),
            reply_markup=rkb_admin_settings(), parse_mode="HTML"
        )

    elif text == "🤖 Set Free Limit" and uid in admin_ids:
        fl = system_settings.get("free_limit", 1)
        fs = system_settings.get("free_storage_mb", 50)
        bot.send_message(
            msg.chat.id,
            "🤖 <b>Set Free User Limit</b>\n\n"
            f"<i>Current: {fl} files, {fs}MB storage</i>\n\n"
            "Format: <code>files storage_MB</code>\n"
            "Example: <code>3 100</code> = 3 files + 100MB\n"
            "Files only: <code>5</code> keeps storage unchanged",
            reply_markup=rkb_back(), parse_mode="HTML"
        )
        bot.register_next_step_handler_by_chat_id(msg.chat.id, _save_free_limit)

    elif text == "⏱ Set Cooldown" and uid in admin_ids:
        bot.send_message(
            msg.chat.id,
            "⏱ Enter cooldown in seconds (number):",
            reply_markup=rkb_back()
        )
        bot.register_next_step_handler_by_chat_id(msg.chat.id, _save_cooldown)

    elif text == "🗑️ Clear All Logs" and uid in admin_ids:
        try:
            open(BOT_LOG_FILE, "w").close()
            bot.send_message(msg.chat.id, "✅ All logs cleared.")
        except Exception as e:
            bot.send_message(msg.chat.id, f"❌ Failed: {e}")

    elif text == "➕ Add New Method" and uid in admin_ids:
        bot.send_message(
            msg.chat.id,
            "➕ <b>Add New Payment Method</b>\n\n"
            "<b>Step 1/3:</b> Enter method ID\n"
            "<i>(lowercase, letters/numbers only, e.g. binance, paypal)</i>",
            reply_markup=rkb_back(), parse_mode="HTML"
        )
        payment_add_state[uid] = {"step": "method_id"}
        bot.register_next_step_handler_by_chat_id(msg.chat.id, lambda m: _new_pay_step(m, uid))

    # ── BROADCAST buttons ───────────────────────────────────────
    elif text.startswith("👥 All") and screen == "broadcast" and uid in admin_ids:
        _ask_broadcast_msg(msg, uid, "bc_all")

    elif text.startswith("⭐ Premium Only") and screen == "broadcast" and uid in admin_ids:
        _ask_broadcast_msg(msg, uid, "bc_premium")

    elif text.startswith("🆓 Free Only") and screen == "broadcast" and uid in admin_ids:
        _ask_broadcast_msg(msg, uid, "bc_free")

    elif text == "👤 Specific User" and screen == "broadcast" and uid in admin_ids:
        bot.send_message(msg.chat.id, "👤 Enter user ID:", reply_markup=rkb_back())
        bot.register_next_step_handler_by_chat_id(
            msg.chat.id, lambda m: _do_bc_specific_id(m, uid)
        )

    # ── SESSION MENU buttons ────────────────────────────────────
    elif text == "➕ New Session" and screen == "session_menu":
        if not is_premium(uid):
            bot.send_message(msg.chat.id, "💎 Premium required.")
            return
        bot.send_message(
            msg.chat.id,
            "🔑 <b>Select Session Type:</b>",
            reply_markup=rkb_session_type(), parse_mode="HTML"
        )

    elif text == "📋 My Sessions" and screen == "session_menu":
        _show_session_list(msg, uid)

    elif text == "🗑️ Delete Session" and screen == "session_menu":
        _show_session_delete_list(msg, uid)

    elif text in ("🐍 Telethon", "🔥 Pyrogram"):
        stype = "telethon" if text == "🐍 Telethon" else "pyrogram"
        _start_session_flow_msg(msg, uid, stype)

    elif text == "❌ Cancel":
        session_state.pop(uid, None)
        set_screen(uid, "main")
        bot.send_message(msg.chat.id, "✅ Cancelled.", reply_markup=rkb_main(uid))

    # ── BACK button ─────────────────────────────────────────────
    elif text == "◀️ Back":
        _go_back(msg, uid, screen)

    # ── unrecognised text (during multi-step states or unknown) ─
    else:
        # If user is in a state expecting text input, ignore routing
        pass

def _go_back(msg, uid, screen):
    name = msg.from_user.username or msg.from_user.first_name or "User"
    if screen in ("user_panel", "my_scripts", "file_control", "buy_premium",
                  "upload", "session_menu", "broadcast"):
        set_screen(uid, "main")
        bot.send_message(
            msg.chat.id, build_welcome(uid, name),
            reply_markup=rkb_main(uid), parse_mode="HTML"
        )
    elif screen == "store":
        # Store → Admin Panel
        set_screen(uid, "admin_panel")
        bot.send_message(
            msg.chat.id, build_admin_panel(),
            reply_markup=rkb_admin(), parse_mode="HTML"
        )
    elif screen == "store_confirm_restore":
        # Cancel restore → back to store
        set_screen(uid, "store")
        bot.send_message(
            msg.chat.id, _build_store_panel(),
            reply_markup=rkb_store(), parse_mode="HTML"
        )
    elif screen in ("admin_panel", "admin_settings", "admin_users"):
        set_screen(uid, "main")
        bot.send_message(
            msg.chat.id, build_welcome(uid, name),
            reply_markup=rkb_main(uid), parse_mode="HTML"
        )
    else:
        set_screen(uid, "main")
        bot.send_message(
            msg.chat.id, build_welcome(uid, name),
            reply_markup=rkb_main(uid), parse_mode="HTML"
        )

def _ask_broadcast_msg(msg, uid, target_type):
    labels = {"bc_all": "Everyone", "bc_premium": "Premium Only", "bc_free": "Free Only"}
    bot.send_message(
        msg.chat.id,
        f"📢 Write message for <b>{labels[target_type]}</b>:",
        reply_markup=rkb_back(), parse_mode="HTML"
    )
    broadcast_state[uid] = target_type
    bot.register_next_step_handler_by_chat_id(
        msg.chat.id, lambda m: _do_broadcast(m, uid, broadcast_state.get(uid, "bc_all"))
    )

# ═══════════════════════════════════════════════════════════════
# ★ MODULE INSTALLATION HANDLER
# ═══════════════════════════════════════════════════════════════
def _do_manual_pkg_install(msg, uid, slot, pkg_name):
    pkg_name = pkg_name.strip()
    if not pkg_name or pkg_name.startswith("/"):
        bot.reply_to(msg, "❌ Invalid package name.")
        return

    prog = bot.send_message(
        msg.chat.id,
        f"📦 Installing <code>{h(pkg_name)}</code>...",
        parse_mode="HTML"
    )
    success = _pip_silent(pkg_name)
    try:
        bot.delete_message(msg.chat.id, prog.message_id)
    except Exception:
        pass

    if success:
        bot.send_message(
            msg.chat.id,
            f"✅ <b>{h(pkg_name)}</b> installed successfully!\n\n"
            f"You can now restart your script using <b>🔄 Restart</b>.",
            parse_mode="HTML",
            reply_markup=rkb_file_control(uid, slot)
        )
        # Log in DB
        with DB_LOCK:
            c = _db()
            c.execute(
                "INSERT OR REPLACE INTO installed_packages(user_id,package_name,installed_at) VALUES(?,?,?)",
                (uid, pkg_name, datetime.now().isoformat())
            )
            c.commit()
            c.close()
    else:
        bot.send_message(
            msg.chat.id,
            f"❌ Failed to install <code>{h(pkg_name)}</code>.\n\n"
            "Possible reasons:\n"
            "◆ Package name is incorrect\n"
            "◆ Network issue\n"
            "◆ Package not available on this platform\n\n"
            "Try again with the exact PyPI package name.",
            parse_mode="HTML",
            reply_markup=rkb_file_control(uid, slot)
        )

# ── File upload handler ──────────────────────────────────────
@bot.message_handler(content_types=["document"])
def handle_document(msg):
    uid = msg.from_user.id
    if uid in banned_users:
        return
    if maintenance_mode and uid not in admin_ids:
        bot.reply_to(msg, "🔧 Bot under maintenance.")
        return

    update_slot = _pending_update_slot.pop(uid, None)
    doc   = msg.document
    fname = doc.file_name or "script.py"
    ext   = os.path.splitext(fname)[1].lower()

    if ext not in (".py", ".js", ".zip", ".php"):
        bot.reply_to(
            msg,
            "❌ Only <code>.py</code>, <code>.js</code>, <code>.php</code>, "
            "<code>.zip</code> files supported.",
            parse_mode="HTML"
        )
        return

    ftype      = ext[1:]
    size_bytes = doc.file_size or 0
    size_kb    = size_bytes / 1024

    if size_bytes > 20 * 1024 * 1024:
        bot.reply_to(msg, "❌ Max file size is 20MB.")
        return

    if update_slot is None:
        files        = user_files_db.get(uid, [])
        active_files = [f for f in files if f["status"] != "rejected"]
        lim          = get_bot_limit(uid)
        if len(active_files) >= lim and uid not in admin_ids:
            bot.reply_to(
                msg,
                f"❌ Script limit reached! ({lim})\n💎 Get Premium for more scripts."
            )
            return
        for f in files:
            if f["file_name"] == fname and f["status"] != "rejected":
                bot.reply_to(
                    msg,
                    f"⚠️ A file named <code>{h(fname)}</code> already exists. "
                    "Use the 🔄 Update button on the script to update it.",
                    parse_mode="HTML"
                )
                return

    used_mb  = get_user_storage_mb(uid)
    stor_lim = get_storage_limit(uid)
    if used_mb + size_kb / 1024 > stor_lim and uid not in admin_ids:
        bot.reply_to(msg, f"❌ Storage full! {used_mb:.1f}MB/{stor_lim}MB")
        return

    prog = bot.reply_to(msg, "⏳ Downloading file...")
    try:
        fi      = bot.get_file(doc.file_id)
        content = bot.download_file(fi.file_path)
    except Exception as e:
        safe_send(msg.chat.id, f"❌ Download failed: {h(str(e))}")
        return
    finally:
        try:
            bot.delete_message(msg.chat.id, prog.message_id)
        except Exception:
            pass

    slot   = update_slot if update_slot else get_next_slot(uid)
    folder = create_script_folder(uid, slot)

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

    # Set screen to file_control so module install button works
    set_screen(uid, "file_control", slot)

    threading.Thread(
        target=send_fake_security_scan, args=(msg.chat.id,), daemon=True
    ).start()
    threading.Thread(
        target=notify_admins_approval,
        args=(uid, fname, ftype, slot, aid, size_kb, username, is_premium(uid)),
        daemon=True
    ).start()
    logger.info(f"Upload pending: uid={uid} slot={slot} file={fname} aid={aid}")

    # Show file control keyboard after scan
    def _send_control_later():
        time.sleep(8)
        try:
            fi = get_file_by_slot(uid, slot)
            if fi:
                bot.send_message(
                    msg.chat.id,
                    build_file_control(uid, slot),
                    reply_markup=rkb_file_control(uid, slot),
                    parse_mode="HTML"
                )
        except Exception:
            pass
    threading.Thread(target=_send_control_later, daemon=True).start()

# ── Photo handler (payment screenshot) ──────────────────────────
@bot.message_handler(content_types=["photo"])
def handle_photo(msg):
    uid = msg.from_user.id
    if uid in payment_state:
        _receive_payment_screenshot(msg, uid)

# ═══════════════════════════════════════════════════════════════
# INLINE CALLBACK ROUTER — for remaining inline keyboards
# ═══════════════════════════════════════════════════════════════
@bot.callback_query_handler(func=lambda c: True)
def cb_router(call):
    uid  = call.from_user.id
    data = _strip_style(call.data)

    if data == "noop":
        bot.answer_callback_query(call.id)
        return
    if uid in banned_users:
        bot.answer_callback_query(call.id, "🚫 You are banned.", show_alert=True)
        return
    if bot_locked and uid not in admin_ids:
        bot.answer_callback_query(call.id, "🔒 Bot is locked.", show_alert=True)
        return
    try:
        _cb(call, uid, data)
    except Exception as e:
        logger.error(f"CB error: uid={uid} data={data} err={e}")
        bot.answer_callback_query(call.id, "⚠️ An error occurred.", show_alert=True)

def _cb(call, uid, data):
    cid  = call.message.chat.id
    mid  = call.message.message_id

    # ── File control from script list ──
    if data.startswith("fctl_"):
        slot = int(data[5:])
        bot.answer_callback_query(call.id)
        set_screen(uid, "file_control", slot)
        bot.send_message(
            cid, build_file_control(uid, slot),
            reply_markup=rkb_file_control(uid, slot), parse_mode="HTML"
        )

    # ── Confirm delete (inline) ──
    elif data.startswith("fdelete_confirm_"):
        slot = int(data.split("_confirm_")[1])
        bot.answer_callback_query(call.id, "🗑️ Deleting...")
        _delete_slot_hard(uid, slot)
        name = call.from_user.username or call.from_user.first_name or "User"
        set_screen(uid, "main")
        try:
            bot.edit_message_reply_markup(cid, mid, reply_markup=None)
        except Exception:
            pass
        bot.send_message(
            cid, "✅ Script deleted.",
            reply_markup=rkb_main(uid)
        )

    elif data == "cancel_delete":
        bot.answer_callback_query(call.id)
        try:
            bot.edit_message_reply_markup(cid, mid, reply_markup=None)
        except Exception:
            pass

    # ── Go to scripts (from approval notification) ──
    elif data == "go_scripts":
        bot.answer_callback_query(call.id)
        set_screen(uid, "my_scripts")
        files = user_files_db.get(uid, [])
        bot.send_message(
            cid, build_my_scripts(uid),
            reply_markup=rkb_my_scripts() if files else rkb_back(),
            parse_mode="HTML"
        )
        if files:
            bot.send_message(
                cid, "📌 Select a script to control:",
                reply_markup=kb_my_scripts_inline(uid)
            )

    elif data == "upload_file_inline":
        bot.answer_callback_query(call.id)
        set_screen(uid, "upload")
        bot.send_message(
            cid,
            "📁 <b>Upload a File</b>\n\n"
            "◆ <code>.py</code> — Python script\n"
            "◆ <code>.js</code> — Node.js script\n"
            "◆ <code>.php</code> — PHP script\n"
            "◆ <code>.zip</code> — Full project (max 20MB, auto entry-point detect)\n\n"
            "<i>Send your file now...</i>",
            parse_mode="HTML",
            reply_markup=rkb_back()
        )

    # ── Admin: File approval ──
    elif data.startswith("approve_"):
        aid = int(data[8:])
        bot.answer_callback_query(call.id, "✅ Approving...")
        _cb_approve_file(call, aid)

    elif data.startswith("reject_"):
        aid = int(data[7:])
        bot.answer_callback_query(call.id)
        _cb_reject_file_ask(call, aid)

    # ── Admin: Payment approval ──
    elif data.startswith("pay_approve_"):
        pid = int(data[12:])
        bot.answer_callback_query(call.id, "✅ Approving...")
        _cb_approve_payment(call, pid)

    elif data.startswith("pay_reject_"):
        pid = int(data[11:])
        bot.answer_callback_query(call.id, "❌ Rejecting...")
        _cb_reject_payment(call, pid)

    # ── Admin: User list pagination ──
    elif data.startswith("adm_users_page_"):
        if uid not in admin_ids:
            return
        bot.answer_callback_query(call.id)
        page = int(data[15:])
        m, total = kb_users_inline(page)
        try:
            bot.edit_message_text(
                f"👥 <b>User Management</b>\n\nTotal users: <code>{total}</code>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━\nPage {page+1}:",
                cid, mid, reply_markup=m, parse_mode="HTML"
            )
        except Exception:
            pass

    elif data.startswith("adm_view_user_"):
        if uid not in admin_ids:
            return
        target = int(data[14:])
        bot.answer_callback_query(call.id)
        bot.send_message(
            cid, build_user_profile(target),
            reply_markup=kb_user_profile_admin(target), parse_mode="HTML"
        )

    elif data == "adm_search_user":
        if uid not in admin_ids:
            return
        bot.answer_callback_query(call.id)
        bot.send_message(cid, "🔍 Enter user ID:", reply_markup=rkb_back())
        bot.register_next_step_handler_by_chat_id(
            cid, lambda m: _do_search_user(m, uid)
        )

    # ── Admin: User actions ──
    elif data.startswith("adm_setstorage_"):
        target = int(data[15:])
        if uid not in admin_ids:
            return
        bot.answer_callback_query(call.id)
        cur_override = db_get_user_storage_override(target)
        cur_txt = f"Current custom: {cur_override}MB" if cur_override else "No custom storage set"
        bot.send_message(
            cid,
            f"💾 <b>Set Storage for <code>{target}</code></b>\n\n"
            f"<i>{cur_txt}</i>\n\n"
            "Enter new storage in MB:\n"
            "<i>(e.g. 100 for 100MB, 0 to remove custom)</i>",
            reply_markup=rkb_back(), parse_mode="HTML"
        )
        bot.register_next_step_handler_by_chat_id(
            cid, lambda m: _save_user_storage(m, uid, target)
        )

    elif data.startswith("adm_giveprem_"):
        target = int(data[13:])
        if uid not in admin_ids:
            return
        bot.answer_callback_query(call.id)
        bot.send_message(
            cid,
            f"⭐ <b>Give Premium to <code>{target}</code></b>\n\n"
            "Format: <code>LIMIT DAYS</code>\n"
            "Example: <code>10 30</code> (10 files, 30 days)",
            reply_markup=rkb_back(), parse_mode="HTML"
        )
        bot.register_next_step_handler_by_chat_id(
            cid, lambda m: _save_give_premium(m, uid, target)
        )

    elif data.startswith("adm_ban_"):
        target = int(data[8:])
        if uid not in admin_ids:
            return
        if target == OWNER_ID:
            bot.answer_callback_query(call.id, "❌ Cannot ban the owner.", show_alert=True)
            return
        bot.answer_callback_query(call.id)
        bot.send_message(
            cid, f"🚫 Enter ban reason for <code>{target}</code>:",
            reply_markup=rkb_back(), parse_mode="HTML"
        )
        bot.register_next_step_handler_by_chat_id(
            cid, lambda m: _save_ban(m, uid, target)
        )

    elif data.startswith("adm_unban_"):
        target = int(data[10:])
        if uid not in admin_ids:
            return
        bot.answer_callback_query(call.id, "✅ Unbanning...")
        db_unban_user(target)
        bot.send_message(cid, f"✅ User <code>{target}</code> unbanned.", parse_mode="HTML")
        bot.send_message(
            cid, build_user_profile(target),
            reply_markup=kb_user_profile_admin(target), parse_mode="HTML"
        )

    elif data.startswith("adm_stopall_"):
        target = int(data[12:])
        if uid not in admin_ids:
            return
        bot.answer_callback_query(call.id, "⏹️ Stopping...")
        count = 0
        for key in [k for k in bot_scripts if k.startswith(f"{target}_")]:
            slot_str = key.split("_")[1]
            _stop_script_key(target, int(slot_str))
            count += 1
        bot.send_message(cid, f"⏹️ Stopped {count} scripts for <code>{target}</code>.", parse_mode="HTML")

    elif data.startswith("adm_startall_"):
        target = int(data[13:])
        if uid not in admin_ids:
            return
        bot.answer_callback_query(call.id, "▶️ Starting...")
        files = [
            f for f in user_files_db.get(target, [])
            if f["status"] in ("approved", "running", "stopped")
        ]
        for f in files:
            if not is_running(target, f["slot"]):
                threading.Thread(
                    target=run_script, args=(target, f["slot"], call.message), daemon=True
                ).start()
        bot.send_message(cid, f"▶️ Starting scripts for <code>{target}</code>.", parse_mode="HTML")

    elif data.startswith("adm_msg_"):
        target = int(data[8:])
        if uid not in admin_ids:
            return
        bot.answer_callback_query(call.id)
        bot.send_message(
            cid, f"✉️ Write a message to <code>{target}</code>:",
            reply_markup=rkb_back(), parse_mode="HTML"
        )
        bot.register_next_step_handler_by_chat_id(
            cid, lambda m: _send_adm_msg(m, target)
        )

    # ── Session: from inline (kb_go_scripts etc.) ──
    elif data.startswith("session_info_"):
        sname = data[13:]
        bot.answer_callback_query(call.id)
        _cb_session_info_inline(call, uid, sname)

    elif data.startswith("sesdel_"):
        sname = data[7:]
        bot.answer_callback_query(call.id, "🗑️ Deleting...")
        _cb_session_do_delete_inline(call, uid, sname)

    else:
        bot.answer_callback_query(call.id)

# ─── File approval callbacks ────────────────────────────────────
def _cb_approve_file(call, aid):
    row = db_get_approval(aid)
    if not row or row["status"] != "pending":
        safe_send(call.message.chat.id, "⚠️ Already processed.")
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
        safe_send(call.message.chat.id, f"✅ <code>{h(fname)}</code> approved.")
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
            safe_send(call.message.chat.id, "⚠️ ZIP data not found.")
    else:
        db_update_file_status(target, slot, "approved")
        threading.Thread(
            target=run_script, args=(target, slot, call.message), daemon=True
        ).start()

    try:
        bot.send_message(
            target,
            f"✅ <b>Deployment complete!</b>\n\n"
            f"🟢 Your script <code>{h(fname)}</code> is now running.",
            reply_markup=kb_go_scripts(), parse_mode="HTML"
        )
    except Exception:
        pass

def _cb_reject_file_ask(call, aid):
    row = db_get_approval(aid)
    if not row or row["status"] != "pending":
        safe_send(call.message.chat.id, "⚠️ Already processed.")
        return
    _reject_state[call.from_user.id] = {"type": "file", "aid": aid}
    safe_edit(
        call.message.chat.id, call.message.message_id,
        "❌ <b>Enter rejection reason:</b>", None
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
    reason = msg.text or "No specific reason"
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
    bot.reply_to(msg, f"✅ <code>{h(fname)}</code> rejected.", parse_mode="HTML")

    try:
        bot.send_message(
            target,
            "🔴 <b>Scan Complete</b>\n\n"
            "Suspicious content was detected in your file "
            "that violates our security policy.\n\n"
            "Please review your file and try again.",
            reply_markup=kb_retry_inline(), parse_mode="HTML"
        )
    except Exception:
        pass

def _show_adm_approvals(msg):
    rows = db_get_pending_approvals()
    if not rows:
        bot.send_message(msg.chat.id, "📋 <b>File Approvals</b>\n\n✅ No pending files.", parse_mode="HTML")
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
    bot.send_message(
        msg.chat.id,
        f"📋 <b>File Approvals</b> ({len(rows)} pending)\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n" + "\n".join(lines),
        reply_markup=m, parse_mode="HTML"
    )

def _show_adm_payments(msg):
    rows   = db_get_pending_payments()
    labels = {1: "1 Month", 3: "3 Months", 6: "6 Months", 12: "1 Year"}
    if not rows:
        bot.send_message(msg.chat.id, "💰 <b>Payment Approvals</b>\n\n✅ No pending payments.", parse_mode="HTML")
        return
    lines = []
    m     = types.InlineKeyboardMarkup()
    for r in rows[:10]:
        lines.append(
            f"▸ #{r['id']} | <code>{r['user_id']}</code> | "
            f"{labels.get(r['plan_months'], str(r['plan_months'])+'m')} | ${r['amount_usd']}"
        )
        m.row(
            _b(f"✅ #{r['id']} Approve", f"pay_approve_{r['id']}", "s"),
            _b("❌ Reject", f"pay_reject_{r['id']}", "d")
        )
    bot.send_message(
        msg.chat.id,
        f"💰 <b>Payment Approvals</b> ({len(rows)} pending)\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n" + "\n".join(lines),
        reply_markup=m, parse_mode="HTML"
    )

def _cb_approve_payment(call, pid):
    row = db_get_payment(pid)
    if not row or row["status"] != "pending":
        safe_send(call.message.chat.id, "⚠️ Already processed.")
        return
    target = row["user_id"]
    months = row["plan_months"]
    db_update_payment(pid, "approved", call.from_user.id)
    expiry = activate_premium(target, months, bot_limit=10, granted_by=call.from_user.id)
    try:
        bot.edit_message_reply_markup(
            call.message.chat.id, call.message.message_id, reply_markup=None
        )
        safe_send(call.message.chat.id, f"✅ Payment #{pid} approved.")
    except Exception:
        pass
    try:
        bot.send_message(
            target,
            f"🎉 <b>Your Premium is now active!</b>\n"
            f"📅 Expires: {expiry.strftime('%Y-%m-%d')}",
            parse_mode="HTML"
        )
    except Exception:
        pass

def _cb_reject_payment(call, pid):
    row = db_get_payment(pid)
    if not row or row["status"] != "pending":
        safe_send(call.message.chat.id, "⚠️ Already processed.")
        return
    target = row["user_id"]
    db_update_payment(pid, "rejected", call.from_user.id)
    try:
        bot.edit_message_reply_markup(
            call.message.chat.id, call.message.message_id, reply_markup=None
        )
        safe_send(call.message.chat.id, f"❌ Payment #{pid} rejected.")
    except Exception:
        pass
    try:
        bot.send_message(
            target,
            f"❌ <b>Payment could not be verified.</b>\n"
            f"Contact admin: {ADMIN_USERNAME}",
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
        bot.reply_to(msg, "⚠️ Please send a screenshot (image).")
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
        "✅ <b>Payment request submitted!</b>\n\n"
        "<i>Usually verified within 1–24 hours.</i>",
        parse_mode="HTML"
    )

# ── Admin helper functions ────────────────────────────────────────
def _do_search_user(msg, admin_uid):
    if not msg.text:
        return
    try:
        target = int(msg.text.strip())
    except ValueError:
        bot.reply_to(msg, "❌ Enter a valid ID.")
        return
    bot.send_message(
        msg.chat.id, build_user_profile(target),
        reply_markup=kb_user_profile_admin(target), parse_mode="HTML"
    )

def _save_pay_method(msg, uid, method):
    number = (msg.text or "").strip()
    if not number:
        bot.reply_to(msg, "❌ Cannot be empty.")
        return
    db_set_payment_method(method, number)
    bot.reply_to(
        msg,
        f"✅ {method} number set: <code>{h(number)}</code>",
        parse_mode="HTML"
    )

def _save_price(msg, uid, months):
    try:
        price = float((msg.text or "").strip())
    except ValueError:
        bot.reply_to(msg, "❌ Enter a number.")
        return
    db_set_plan_price(months, price)
    bot.reply_to(msg, f"✅ Price updated: ${price:.2f}")

def _save_welcome(msg):
    if msg.text:
        db_set_setting("welcome_text", msg.text)
    bot.reply_to(msg, "✅ Welcome message updated.")

def _save_free_limit(msg):
    parts = (msg.text or "").strip().split()
    try:
        lim = int(parts[0])
    except (ValueError, IndexError):
        bot.reply_to(msg, "❌ Use correct format, e.g. <code>3 100</code>", parse_mode="HTML")
        return
    system_settings["free_limit"] = lim
    db_set_setting("free_limit", str(lim))
    reply = f"✅ Free limit: <code>{lim} files</code>"
    if len(parts) >= 2:
        try:
            storage_mb = int(parts[1])
            system_settings["free_storage_mb"] = storage_mb
            db_set_setting("free_storage_mb", str(storage_mb))
            reply += f"\n✅ Free storage: <code>{storage_mb}MB</code>"
        except ValueError:
            reply += "\n⚠️ Storage value invalid, kept unchanged."
    bot.reply_to(msg, reply, parse_mode="HTML")

def _save_cooldown(msg):
    try:
        cdw = int((msg.text or "").strip())
    except ValueError:
        bot.reply_to(msg, "❌ Enter a number.")
        return
    system_settings["cooldown_sec"] = cdw
    db_set_setting("cooldown_sec", str(cdw))
    bot.reply_to(msg, f"✅ Cooldown: {cdw}s")

def _save_give_premium(msg, admin_uid, target):
    parts = (msg.text or "").split()
    if len(parts) < 2:
        bot.reply_to(msg, "❌ Use LIMIT DAYS format.")
        return
    try:
        lim, days = int(parts[0]), int(parts[1])
    except ValueError:
        bot.reply_to(msg, "❌ Use numbers.")
        return
    exp = datetime.now() + timedelta(days=days)
    db_save_sub(target, lim, exp, admin_uid)
    db_clear_expiry_notifs(target)
    bot.reply_to(
        msg,
        f"✅ Granted {days}-day Premium to <code>{target}</code>.",
        parse_mode="HTML"
    )
    try:
        bot.send_message(
            target,
            f"🎉 <b>Your Premium is now active!</b>\n"
            f"🔢 Limit: {lim}\n"
            f"📅 Expires: {exp.strftime('%Y-%m-%d')}",
            parse_mode="HTML"
        )
    except Exception:
        pass

def _save_user_storage(msg, admin_uid, target):
    try:
        mb = int((msg.text or "").strip())
    except ValueError:
        bot.reply_to(msg, "❌ Enter a number (MB).")
        return
    if mb <= 0:
        db_del_user_storage_override(target)
        bot.reply_to(
            msg,
            f"✅ Custom storage removed for <code>{target}</code>.\nDefault storage now applies.",
            parse_mode="HTML"
        )
        try:
            bot.send_message(
                target, "💾 <b>Storage updated.</b>\nDefault storage limit now applies.",
                parse_mode="HTML"
            )
        except Exception:
            pass
    else:
        db_set_user_storage(target, mb, admin_uid)
        bot.reply_to(
            msg,
            f"✅ Storage set to <code>{mb}MB</code> for <code>{target}</code>.",
            parse_mode="HTML"
        )
        try:
            bot.send_message(
                target,
                f"💾 <b>Your storage has been updated!</b>\nNew limit: <code>{mb}MB</code>",
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
            bot.reply_to(msg, "❌ Use lowercase letters, numbers and _ only.")
            bot.register_next_step_handler(msg, lambda m: _new_pay_step(m, uid))
            return
        state["method_id"] = mid_val
        state["step"]      = "display_name"
        bot.reply_to(
            msg,
            "<b>Step 2/3:</b> Enter display name\n"
            "<i>(e.g. Binance USDT, PayPal)</i>",
            parse_mode="HTML"
        )
        bot.register_next_step_handler(msg, lambda m: _new_pay_step(m, uid))
    elif step == "display_name":
        state["display_name"] = (msg.text or "").strip()
        state["step"]         = "number"
        bot.reply_to(
            msg,
            "<b>Step 3/3:</b> Enter number or address\n"
            "<i>(e.g. 01XXXXXXXXX, 0xABC...)</i>",
            parse_mode="HTML"
        )
        bot.register_next_step_handler(msg, lambda m: _new_pay_step(m, uid))
    elif step == "number":
        number = (msg.text or "").strip()
        if not number:
            bot.reply_to(msg, "❌ Cannot be empty.")
            bot.register_next_step_handler(msg, lambda m: _new_pay_step(m, uid))
            return
        db_add_new_payment_method(state["method_id"], state["display_name"], number)
        payment_add_state.pop(uid, None)
        bot.reply_to(
            msg,
            f"✅ New payment method added!\n"
            f"🆔 ID: <code>{h(state['method_id'])}</code>\n"
            f"📛 Name: {h(state['display_name'])}\n"
            f"📱 Number: <code>{h(number)}</code>",
            parse_mode="HTML"
        )

def _save_ban(msg, admin_uid, target):
    reason = msg.text or "Policy violation"
    db_ban_user(target, reason, admin_uid)
    bot.reply_to(msg, f"✅ User <code>{target}</code> banned.", parse_mode="HTML")

def _send_adm_msg(msg, target):
    if not msg.text:
        return
    try:
        bot.send_message(
            target,
            f"📨 <b>System Message:</b>\n\n{h(msg.text)}",
            parse_mode="HTML"
        )
        bot.reply_to(msg, "✅ Message sent.")
    except Exception as e:
        bot.reply_to(msg, f"❌ Could not send: {e}")

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
    bot.reply_to(msg, f"✅ Broadcast done!\n✅ Success: {ok} | ❌ Failed: {fail}")

def _do_bc_specific_id(msg, admin_uid):
    try:
        target = int((msg.text or "").strip())
    except ValueError:
        bot.reply_to(msg, "❌ Enter a valid ID.")
        return
    bot.send_message(
        msg.chat.id,
        f"✉️ Write a message to <code>{target}</code>:",
        reply_markup=rkb_back(), parse_mode="HTML"
    )
    bot.register_next_step_handler_by_chat_id(
        msg.chat.id, lambda m: _send_adm_msg(m, target)
    )

# ═══════════════════════════════════════════════════════════════
# SESSION SYSTEM
# ═══════════════════════════════════════════════════════════════
def _show_session_list(msg, uid):
    sessions = db_get_sessions(uid)
    if not sessions:
        bot.send_message(
            msg.chat.id,
            "📋 <b>My Sessions</b>\n\n<i>No sessions yet.</i>",
            reply_markup=rkb_back(), parse_mode="HTML"
        )
        return
    m = types.InlineKeyboardMarkup(row_width=1)
    for s in sessions:
        m.add(_b(
            f"📄 {s['session_name']} [{s['session_type']}]",
            f"session_info_{s['session_name']}", "p"
        ))
    lines = [
        f"▸ <code>{h(s['session_name'])}</code> — {s['session_type']}"
        for s in sessions
    ]
    bot.send_message(
        msg.chat.id,
        "📋 <b>My Sessions</b>\n\n" + "\n".join(lines),
        reply_markup=m, parse_mode="HTML"
    )

def _show_session_delete_list(msg, uid):
    sessions = db_get_sessions(uid)
    if not sessions:
        bot.send_message(
            msg.chat.id,
            "🗑️ <b>Delete Session</b>\n\n<i>No sessions.</i>",
            reply_markup=rkb_back(), parse_mode="HTML"
        )
        return
    m = types.InlineKeyboardMarkup(row_width=1)
    for s in sessions:
        m.add(_b(f"🗑️ {s['session_name']}", f"sesdel_{s['session_name']}", "d"))
    bot.send_message(
        msg.chat.id,
        "🗑️ <b>Delete Session</b>\n\nWhich one do you want to delete?",
        reply_markup=m, parse_mode="HTML"
    )

def _cb_session_info_inline(call, uid, session_name):
    sessions = db_get_sessions(uid)
    entry    = next((s for s in sessions if s["session_name"] == session_name), None)
    if not entry:
        bot.answer_callback_query(call.id, "⚠️ Session not found.", show_alert=True)
        return
    uf = get_user_folder(uid)
    ex = "✅ Exists" if os.path.exists(os.path.join(uf, f"{session_name}.session")) else "❌ Missing"
    m  = types.InlineKeyboardMarkup()
    m.add(_b(f"🗑️ Delete {session_name}", f"sesdel_{session_name}", "d"))
    safe_edit(
        call.message.chat.id, call.message.message_id,
        f"📄 <b>Session: <code>{h(session_name)}</code></b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔧 Type: <code>{entry['session_type']}</code>\n"
        f"🔑 API ID: <code>{h(entry['api_id'] or '-')}</code>\n"
        f"💾 File: {ex}\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💡 Usage: <code>TelegramClient('{h(session_name)}', api_id, api_hash)</code>",
        m
    )

def _cb_session_do_delete_inline(call, uid, session_name):
    uf = get_user_folder(uid)
    sf = os.path.join(uf, f"{session_name}.session")
    if os.path.exists(sf):
        os.remove(sf)
    db_del_session(uid, session_name)
    safe_send(call.message.chat.id, f"✅ Session <code>{h(session_name)}</code> deleted.")

def _start_session_flow_msg(msg, uid, stype):
    session_state[uid] = {"type": stype, "step": "name"}
    bot.send_message(
        msg.chat.id,
        f"🔑 <b>{stype.capitalize()} Session</b>\n\n"
        "<b>Step 1/6:</b> Enter a session name:\n"
        "<i>(letters and numbers only, e.g. mysession)</i>",
        reply_markup=rkb_back(), parse_mode="HTML"
    )
    bot.register_next_step_handler_by_chat_id(
        msg.chat.id, lambda m: _session_step(m, uid)
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
            bot.reply_to(msg, "❌ Use letters, numbers and _ only.")
            bot.register_next_step_handler(msg, lambda m: _session_step(m, uid))
            return
        state["name"] = name
        state["step"] = "api_id"
        bot.reply_to(
            msg,
            "<b>Step 2/6:</b> Enter API ID:\n<i>(from my.telegram.org)</i>",
            parse_mode="HTML"
        )
        bot.register_next_step_handler(msg, lambda m: _session_step(m, uid))

    elif step == "api_id":
        try:
            state["api_id"] = int((msg.text or "").strip())
        except ValueError:
            bot.reply_to(msg, "❌ API ID must be a number.")
            bot.register_next_step_handler(msg, lambda m: _session_step(m, uid))
            return
        state["step"] = "api_hash"
        bot.reply_to(msg, "<b>Step 3/6:</b> Enter API Hash:", parse_mode="HTML")
        bot.register_next_step_handler(msg, lambda m: _session_step(m, uid))

    elif step == "api_hash":
        state["api_hash"] = (msg.text or "").strip()
        state["step"]     = "phone"
        bot.reply_to(
            msg,
            "<b>Step 4/6:</b> Enter phone number:\n<i>(+880...)</i>",
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

def _send_otp(msg, uid, state):
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
        state["client"] = client
        state["loop"]   = loop
        bot.reply_to(
            msg,
            "<b>Step 5/6:</b> Enter OTP code:\n"
            "<i>(the code Telegram sent you)</i>",
            parse_mode="HTML"
        )
        bot.register_next_step_handler(msg, lambda m: _session_step(m, uid))
    except Exception as e:
        session_state.pop(uid, None)
        try:
            loop.close()
        except Exception:
            pass
        bot.reply_to(msg, f"❌ Failed to send OTP: {h(str(e))}", parse_mode="HTML")

def _complete_session(msg, uid, state):
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
            f"✅ <b>Session created!</b>\n"
            f"📄 Name: <code>{h(state['name'])}</code>",
            parse_mode="HTML"
        )
    except Exception as e:
        try:
            from telethon.errors import SessionPasswordNeededError
            needs_2fa = isinstance(e, SessionPasswordNeededError)
        except ImportError:
            err_lower = str(e).lower()
            needs_2fa = "password" in err_lower or "2fa" in err_lower or "two" in err_lower

        if needs_2fa:
            state["step"] = "2fa"
            bot.reply_to(msg, "<b>Step 6/6:</b> Enter 2FA Password:", parse_mode="HTML")
            bot.register_next_step_handler(msg, lambda m: _session_step(m, uid))
        else:
            session_state.pop(uid, None)
            try:
                async def _disc(): await client.disconnect()
                loop.run_until_complete(_disc())
                loop.close()
            except Exception:
                pass
            bot.reply_to(msg, f"❌ Login failed: {h(str(e))}", parse_mode="HTML")

def _complete_session_2fa(msg, uid, state):
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
            f"✅ <b>Session created!</b>\n"
            f"📄 Name: <code>{h(state['name'])}</code>",
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
        bot.reply_to(msg, f"❌ 2FA failed: {h(str(e))}", parse_mode="HTML")

# ═══════════════════════════════════════════════════════════════
# ★ TELEGRAM CHANNEL BACKUP / RESTORE SYSTEM
# ═══════════════════════════════════════════════════════════════
"""
How it works:
  BACKUP:
    - DB (hosting.db) → zipped → sent as document to BACKUP_CHANNEL_ID
      with caption "#DB_BACKUP"
    - Each user script folder → zipped → sent with caption
      "#SCRIPT_BACKUP uid=<uid> slot=<slot> fname=<fname> ftype=<ftype>"
  RESTORE:
    - Bot fetches last #DB_BACKUP message from channel → downloads → restores DB
    - Bot fetches all #SCRIPT_BACKUP messages → downloads → restores folders
    - After restore → load_data() reloads everything
  AUTO-BACKUP:
    - Every 6 hours, a background thread runs full backup.
  COMMANDS (owner only):
    /backup  → manual backup now
    /restore → restore from channel
"""

import json as _json

_BACKUP_TAG_DB     = "#DB_BACKUP"
_BACKUP_TAG_SCRIPT = "#SCRIPT_BACKUP"
_BACKUP_LOCK       = Lock()


def _backup_enabled():
    return bool(BACKUP_CHANNEL_ID and BACKUP_CHANNEL_ID != 0)


def _build_store_panel():
    """Build Store panel info text."""
    total_scripts = sum(
        1 for files in user_files_db.values()
        for f in files if f["status"] not in ("pending", "rejected")
    )
    try:
        db_size = f"{os.path.getsize(DATABASE_PATH)//1024} KB"
    except Exception:
        db_size = "N/A"
    ch_status = f"<code>{BACKUP_CHANNEL_ID}</code> ✅" if _backup_enabled() else "❌ Not configured"
    return (
        "🗄️ <b>Store — Backup & Restore</b>\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📡 Channel: {ch_status}\n"
        f"💾 DB size: <code>{db_size}</code>\n"
        f"📁 Script slots: <code>{total_scripts}</code>\n"
        f"🕐 Auto-backup: <b>every 1 hour</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "💾 <b>Backup Now</b> — save DB + all scripts to channel\n"
        "♻️ <b>Restore</b> — restore from last channel backup\n"
        "📊 <b>Status</b> — show detailed backup info\n"
        "📡 <b>Channel</b> — setup guide\n"
    )

def tg_backup_db():
    """Zip and send hosting.db to backup channel."""
    if not _backup_enabled():
        return False
    try:
        zip_path = os.path.join(DATA_DIR, "_db_backup_tmp.zip")
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(DATABASE_PATH, "hosting.db")
        with open(zip_path, "rb") as f:
            bot.send_document(
                BACKUP_CHANNEL_ID, f,
                caption=f"{_BACKUP_TAG_DB}\n"
                        f"time={datetime.now().isoformat()}",
                visible_file_name="db_backup.zip"
            )
        os.remove(zip_path)
        logger.info("DB backup sent to channel.")
        return True
    except Exception as e:
        logger.error(f"tg_backup_db error: {e}")
        return False


def tg_backup_script(uid, slot):
    """Zip one user script folder and send to backup channel."""
    if not _backup_enabled():
        return False
    fi = get_file_by_slot(uid, slot)
    if not fi:
        return False
    folder = os.path.join(get_user_folder(uid), f"script_{slot}")
    if not os.path.exists(folder):
        return False
    try:
        zip_path = os.path.join(DATA_DIR, f"_script_{uid}_{slot}_tmp.zip")
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, dirs, files in os.walk(folder):
                for file in files:
                    fp = os.path.join(root, file)
                    arcname = os.path.relpath(fp, folder)
                    zf.write(fp, arcname)
        with open(zip_path, "rb") as f:
            bot.send_document(
                BACKUP_CHANNEL_ID, f,
                caption=(
                    f"{_BACKUP_TAG_SCRIPT}\n"
                    f"uid={uid} slot={slot} "
                    f"fname={fi['file_name']} ftype={fi['file_type']}\n"
                    f"time={datetime.now().isoformat()}"
                ),
                visible_file_name=f"script_{uid}_{slot}.zip"
            )
        os.remove(zip_path)
        logger.info(f"Script backup sent: uid={uid} slot={slot}")
        return True
    except Exception as e:
        logger.error(f"tg_backup_script error uid={uid} slot={slot}: {e}")
        return False


def tg_full_backup(notify_uid=None):
    """Full backup: DB + all script folders."""
    if not _backup_enabled():
        if notify_uid:
            try:
                bot.send_message(
                    notify_uid,
                    "❌ <b>Backup channel not configured.</b>\n"
                    "Set <code>BACKUP_CHANNEL_ID</code> in config.",
                    parse_mode="HTML"
                )
            except Exception:
                pass
        return

    with _BACKUP_LOCK:
        if notify_uid:
            try:
                prog = bot.send_message(notify_uid, "⏳ Starting full backup...")
            except Exception:
                prog = None

        # 1. DB
        db_ok = tg_backup_db()

        # 2. All script folders
        script_count = 0
        fail_count   = 0
        for uid, files in list(user_files_db.items()):
            for fi in files:
                if fi["status"] not in ("pending", "rejected"):
                    ok = tg_backup_script(uid, fi["slot"])
                    if ok:
                        script_count += 1
                    else:
                        fail_count += 1
                    time.sleep(0.3)   # rate limit

        if notify_uid:
            try:
                if prog:
                    bot.delete_message(notify_uid, prog.message_id)
            except Exception:
                pass
            try:
                bot.send_message(
                    notify_uid,
                    f"✅ <b>Backup Complete!</b>\n\n"
                    f"💾 DB: {'✅' if db_ok else '❌'}\n"
                    f"📁 Scripts: ✅ {script_count} | ❌ {fail_count}\n"
                    f"🕐 Time: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                    parse_mode="HTML"
                )
            except Exception:
                pass


def tg_restore_all(notify_uid=None):
    """
    Restore from backup channel.
    1. Find latest #DB_BACKUP → restore DB
    2. Find all #SCRIPT_BACKUP → restore script folders
    """
    if not _backup_enabled():
        if notify_uid:
            try:
                bot.send_message(
                    notify_uid,
                    "❌ Backup channel not configured.",
                    parse_mode="HTML"
                )
            except Exception:
                pass
        return

    with _BACKUP_LOCK:
        if notify_uid:
            try:
                prog = bot.send_message(notify_uid, "⏳ Restoring from backup channel...")
            except Exception:
                prog = None

        db_restored      = False
        scripts_restored = 0
        scripts_failed   = 0
        tmp_dir = os.path.join(DATA_DIR, "_restore_tmp")
        os.makedirs(tmp_dir, exist_ok=True)

        try:
            # ── Fetch messages from channel ───────────────────────
            # We search up to 500 recent messages
            db_msg     = None
            script_msgs = []

            offset = 0
            while True:
                try:
                    updates = bot.get_updates(offset=offset, limit=100, timeout=10)
                except Exception:
                    break
                if not updates:
                    break
                offset = updates[-1].update_id + 1

            # Use forward_messages approach — fetch channel history via getUpdates
            # (better: use bot.get_channel_history via raw API call)
            # We use bot.forward approach with message IDs scanning
            # Scan last 1000 message IDs in channel
            found_db     = False
            all_captions = []

            for msg_id in range(1, 2000):
                try:
                    # Try to copy message to a temp location to read its caption
                    # Actually use get_chat workaround — send a forwardMessage
                    raw = bot.forward_message(
                        chat_id=notify_uid or OWNER_ID,
                        from_chat_id=BACKUP_CHANNEL_ID,
                        message_id=msg_id,
                        disable_notification=True
                    )
                    cap = raw.caption or raw.text or ""

                    if _BACKUP_TAG_DB in cap and not found_db:
                        db_msg = raw
                        found_db = True

                    if _BACKUP_TAG_SCRIPT in cap:
                        script_msgs.append(raw)

                    # Delete the forwarded message immediately
                    try:
                        bot.delete_message(raw.chat.id, raw.message_id)
                    except Exception:
                        pass

                except Exception:
                    pass   # message doesn't exist or can't be forwarded

            # ── Restore DB ────────────────────────────────────────
            if db_msg and db_msg.document:
                try:
                    fi    = bot.get_file(db_msg.document.file_id)
                    data  = bot.download_file(fi.file_path)
                    zpath = os.path.join(tmp_dir, "db_restore.zip")
                    with open(zpath, "wb") as f:
                        f.write(data)
                    with zipfile.ZipFile(zpath, "r") as zf:
                        zf.extract("hosting.db", DATA_DIR)
                    os.remove(zpath)
                    db_restored = True
                    logger.info("DB restored from backup channel.")
                except Exception as e:
                    logger.error(f"DB restore error: {e}")

            # ── Restore scripts ───────────────────────────────────
            for smsg in script_msgs:
                if not smsg.document:
                    continue
                cap = smsg.caption or ""
                try:
                    # Parse caption: uid=X slot=Y fname=Z ftype=W
                    uid_m   = re.search(r"uid=(\d+)", cap)
                    slot_m  = re.search(r"slot=(\d+)", cap)
                    fname_m = re.search(r"fname=(\S+)", cap)
                    ftype_m = re.search(r"ftype=(\S+)", cap)
                    if not (uid_m and slot_m and fname_m and ftype_m):
                        continue
                    r_uid   = int(uid_m.group(1))
                    r_slot  = int(slot_m.group(1))
                    r_fname = fname_m.group(1)
                    r_ftype = ftype_m.group(1)

                    # Download
                    fi   = bot.get_file(smsg.document.file_id)
                    data = bot.download_file(fi.file_path)

                    # Extract to script folder
                    folder = os.path.join(UPLOAD_BOTS_DIR, f"USER_{r_uid}", f"script_{r_slot}")
                    os.makedirs(folder, exist_ok=True)
                    zpath = os.path.join(tmp_dir, f"s_{r_uid}_{r_slot}.zip")
                    with open(zpath, "wb") as f:
                        f.write(data)
                    with zipfile.ZipFile(zpath, "r") as zf:
                        zf.extractall(folder)
                    os.remove(zpath)

                    scripts_restored += 1
                    logger.info(f"Script restored: uid={r_uid} slot={r_slot}")
                    time.sleep(0.2)
                except Exception as e:
                    scripts_failed += 1
                    logger.error(f"Script restore error: {e}")

        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

        # Reload data
        if db_restored:
            load_data()

        if notify_uid:
            try:
                if prog:
                    bot.delete_message(notify_uid, prog.message_id)
            except Exception:
                pass
            try:
                bot.send_message(
                    notify_uid,
                    f"{'✅' if db_restored else '⚠️'} <b>Restore Complete!</b>\n\n"
                    f"💾 DB: {'✅ Restored' if db_restored else '❌ Not found'}\n"
                    f"📁 Scripts: ✅ {scripts_restored} | ❌ {scripts_failed}\n"
                    f"{'🔄 Data reloaded.' if db_restored else ''}",
                    parse_mode="HTML"
                )
            except Exception:
                pass


def _auto_backup_thread():
    """Auto backup every 1 hour."""
    while True:
        time.sleep(3600)
        if _backup_enabled():
            logger.info("Auto backup starting...")
            tg_full_backup()


# ── /backup and /restore commands ────────────────────────────────
@bot.message_handler(commands=["backup"])
def cmd_backup(msg):
    if msg.from_user.id != OWNER_ID:
        return
    if not _backup_enabled():
        bot.reply_to(
            msg,
            "❌ <b>Backup not configured.</b>\n\n"
            "Set <code>BACKUP_CHANNEL_ID</code> in the script config to your private channel ID.\n"
            "Make sure the bot is admin in that channel.",
            parse_mode="HTML"
        )
        return
    bot.reply_to(msg, "⏳ Starting backup... this may take a while.")
    threading.Thread(
        target=tg_full_backup, args=(msg.from_user.id,), daemon=True
    ).start()


@bot.message_handler(commands=["restore"])
def cmd_restore(msg):
    if msg.from_user.id != OWNER_ID:
        return
    if not _backup_enabled():
        bot.reply_to(
            msg,
            "❌ <b>Backup not configured.</b>\n\n"
            "Set <code>BACKUP_CHANNEL_ID</code> to a channel ID first.",
            parse_mode="HTML"
        )
        return
    bot.reply_to(
        msg,
        "⚠️ <b>Restore will overwrite current DB and script files!</b>\n\n"
        "Send /restore_confirm to proceed.",
        parse_mode="HTML"
    )


@bot.message_handler(commands=["restore_confirm"])
def cmd_restore_confirm(msg):
    if msg.from_user.id != OWNER_ID:
        return
    if not _backup_enabled():
        bot.reply_to(msg, "❌ Backup channel not configured.")
        return
    bot.reply_to(msg, "⏳ Restoring from backup channel...")
    threading.Thread(
        target=tg_restore_all, args=(msg.from_user.id,), daemon=True
    ).start()


@bot.message_handler(commands=["backup_status"])
def cmd_backup_status(msg):
    if msg.from_user.id not in admin_ids:
        return
    total_scripts = sum(
        1 for files in user_files_db.values()
        for f in files if f["status"] not in ("pending", "rejected")
    )
    ch = BACKUP_CHANNEL_ID if _backup_enabled() else "Not configured"
    bot.reply_to(
        msg,
        f"📦 <b>Backup Status</b>\n\n"
        f"📡 Channel: <code>{ch}</code>\n"
        f"💾 DB size: <code>{os.path.getsize(DATABASE_PATH)//1024} KB</code>\n"
        f"📁 Script slots: <code>{total_scripts}</code>\n"
        f"🕐 Auto-backup: every 1 hour\n\n"
        f"Commands:\n"
        f"◆ /backup — Backup now\n"
        f"◆ /restore — Restore from channel\n"
        f"◆ /restore_confirm — Confirm restore",
        parse_mode="HTML"
    )


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    logger.info("Script Host Pro v6.2 starting...")

    init_db()
    load_data()

    threading.Thread(target=_expiry_checker, daemon=True).start()
    threading.Thread(target=_cleanup_thread, daemon=True).start()
    threading.Thread(target=_run_flask, daemon=True).start()

    # Auto backup thread (only if configured)
    if _backup_enabled():
        threading.Thread(target=_auto_backup_thread, daemon=True).start()
        logger.info(f"Auto-backup enabled (every 1h) → channel {BACKUP_CHANNEL_ID}")
    else:
        logger.info("Auto-backup disabled (BACKUP_CHANNEL_ID not set)")

    logger.info(f"Owner ID: {OWNER_ID}")
    logger.info(f"Total admins: {len(admin_ids)}")
    logger.info("Starting polling...")

    bot.infinity_polling(
        timeout=30,
        long_polling_timeout=30,
        logger_level=logging.WARNING
    )
