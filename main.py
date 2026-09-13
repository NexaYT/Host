# master_bot.py
# ULTIMATE BOT HOSTING SYSTEM - RENDER READY

import telebot
from telebot import types
import threading
import subprocess
import os
import sys
import json
import time
import re
import signal
import atexit
import platform
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
import requests
import io
import zipfile

# CONFIGURATION
API_TOKEN = "8605874531:AAF7DXId7CGd3LOKrhhSdXGWTzd0K7uurGs"  # Use Render environment variable in production
ADMIN_ID = 5913459788  # Your Telegram User ID
PORT = int(os.environ.get('PORT', 8080))
RENDER_EXTERNAL_URL = os.environ.get('RENDER_EXTERNAL_URL', 'http://localhost:8080')

# Telegram Channel for persistent cloud backup storage.
# Bot must be admin of this channel with post permission.
# Set BACKUP_CHANNEL env var to your channel ID (e.g. -1001234567890)
BACKUP_CHANNEL = -1003710777540

# Override token from environment variable if set (must be configured on Render)
if os.environ.get('BOT_TOKEN'):
    API_TOKEN = os.environ.get('BOT_TOKEN')

BOTS_DIR = "hosted_bots"
CONFIG_FILE = "bot_config.json"
LOG_FILE = "bot_logs.txt"
ERROR_LOG = "error_logs.txt"

# Create directories and files
os.makedirs(BOTS_DIR, exist_ok=True)
for f in [CONFIG_FILE, LOG_FILE, ERROR_LOG]:
    if not os.path.exists(f):
        with open(f, 'w') as file:
            file.write('')

# Initialize bot
bot = telebot.TeleBot(API_TOKEN)

# ==================== AUTO PACKAGE INSTALLER ====================
def auto_install_packages(code):
    """Auto detect and install required packages from Python code"""
    imports = re.findall(r'^import\s+(\S+)|^from\s+(\S+)', code, re.MULTILINE)
    packages = set()
    
    standard_libs = [
        'os', 'sys', 'time', 'json', 're', 'threading', 'subprocess', 
        'datetime', 'math', 'random', 'string', 'collections', 'itertools',
        'functools', 'io', 'pathlib', 'hashlib', 'base64', 'urllib',
        'http', 'socket', 'ssl', 'email', 'xml', 'csv', 'sqlite3',
        'logging', 'unittest', 'typing', 'enum', 'abc', 'copy', 'pprint',
        'inspect', 'traceback', 'warnings', 'weakref', 'struct', 'pickle',
        'tempfile', 'shutil', 'glob', 'fnmatch', 'linecache', 'marshal',
        'operator', 'stat', 'textwrap', 'decimal', 'fractions', 'statistics',
        'signal', 'atexit', 'platform'
    ]
    
    for imp in imports:
        pkg = imp[0] or imp[1]
        pkg = pkg.split('.')[0]
        if pkg not in standard_libs:
            packages.add(pkg)
    
    installed = []
    failed = []
    
    for package in packages:
        try:
            __import__(package)
        except ImportError:
            try:
                subprocess.check_call(
                    [sys.executable, '-m', 'pip', 'install', package],
                    stdout=subprocess.DEVNULL, 
                    stderr=subprocess.DEVNULL
                )
                installed.append(package)
            except:
                failed.append(package)
    
    return installed, failed

# ==================== KEEP ALIVE SYSTEM ====================
class KeepAlive:
    """Keeps the bot alive by periodically pinging itself"""
    def __init__(self):
        self.keep_running = True
        self.ping_thread = None
        
    def start(self):
        """Start the keep-alive ping thread"""
        self.ping_thread = threading.Thread(target=self._ping_self, daemon=True)
        self.ping_thread.start()
    
    def _ping_self(self):
        """Pings itself every 5 minutes to prevent sleep"""
        while self.keep_running:
            try:
                if RENDER_EXTERNAL_URL:
                    requests.get(f"{RENDER_EXTERNAL_URL}/ping", timeout=10)
            except:
                pass
            time.sleep(300)  # 5 minutes

keep_alive = KeepAlive()

# ==================== TELEGRAM CLOUD STORAGE ====================
class TelegramStorage:
    """
    Uses a private Telegram channel as persistent cloud storage.
    Saves all bot .py files + config as a zip archive to the channel.
    On startup, restores everything from the latest backup message.
    This means data survives host changes, restarts, or redeployments.
    """

    BACKUP_TAG = "#HOSTBOT_BACKUP"   # Used to identify backup messages
    BACKUP_PIN = True                 # Pin the latest backup for fast lookup

    def __init__(self, bot_instance, channel_id):
        self.bot = bot_instance
        self.channel_id = channel_id
        self._enabled = bool(channel_id)
        self._lock = threading.Lock()

    @property
    def enabled(self):
        return self._enabled

    # ── Internal helpers ─────────────────────────────────────────────

    def _build_zip(self, bots_data: dict) -> bytes:
        """
        Pack everything into a zip:
          manifest.json  – config metadata for all bots
          bots/<name>.py – source code for each bot
        """
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            # Metadata (no process / thread objects)
            manifest = {}
            for name, data in bots_data.items():
                manifest[name] = {
                    'name': data['name'],
                    'file': data['file'],
                    'status': data['status'],
                    'created_at': data['created_at'],
                    'restart_count': data['restart_count'],
                    'has_error': data.get('has_error', False),
                }
            zf.writestr('manifest.json', json.dumps(manifest, indent=2))

            # Bot source files
            for name, data in bots_data.items():
                bot_file = data.get('file', '')
                if bot_file and os.path.exists(bot_file):
                    with open(bot_file, 'r', encoding='utf-8') as f:
                        code = f.read()
                    zf.writestr(f'bots/{name}.py', code)

        return buf.getvalue()

    def _find_latest_backup_message(self):
        """
        Search recent channel messages for the latest backup.
        Returns message object or None.
        We look for a document message whose caption contains BACKUP_TAG.
        """
        try:
            # Telegram Bot API: getUpdates won't help for channel history.
            # We use the pinned message first (fastest), then fall back to
            # the forwardMessages trick via getChat.
            chat = self.bot.get_chat(self.channel_id)
            pinned = getattr(chat, 'pinned_message', None)
            if pinned and pinned.document:
                cap = pinned.caption or ''
                if self.BACKUP_TAG in cap:
                    return pinned

            # Fallback: nothing found via pin
            return None
        except Exception as e:
            print(f"[TelegramStorage] Could not find backup message: {e}")
            return None

    # ── Public API ────────────────────────────────────────────────────

    def save(self, bots_data: dict, label: str = "auto"):
        """Upload a new backup zip to the channel and pin it."""
        if not self.enabled:
            return False
        with self._lock:
            try:
                zip_bytes = self._build_zip(bots_data)
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                bot_count = len(bots_data)
                caption = (
                    f"{self.BACKUP_TAG}\n"
                    f"🕐 {timestamp}\n"
                    f"📦 Bots: {bot_count}\n"
                    f"🏷️ Trigger: {label}"
                )
                buf = io.BytesIO(zip_bytes)
                buf.name = f'hostbot_backup_{datetime.now().strftime("%Y%m%d_%H%M%S")}.zip'
                msg = self.bot.send_document(
                    self.channel_id,
                    buf,
                    caption=caption,
                    visible_file_name=buf.name
                )
                # Pin the latest backup for quick retrieval
                if self.BACKUP_PIN:
                    try:
                        self.bot.pin_chat_message(
                            self.channel_id, msg.message_id,
                            disable_notification=True
                        )
                    except Exception:
                        pass  # Pin is optional
                print(f"[TelegramStorage] Backup saved: {bot_count} bot(s) [{label}]")
                return True
            except Exception as e:
                print(f"[TelegramStorage] Save failed: {e}")
                return False

    def restore(self) -> dict:
        """
        Download the latest backup zip from the channel.
        Returns dict: { bot_name: { manifest_data + 'code': str } }
        Returns empty dict if no backup found or channel not set.
        """
        if not self.enabled:
            return {}
        try:
            msg = self._find_latest_backup_message()
            if not msg or not msg.document:
                print("[TelegramStorage] No backup found in channel.")
                return {}

            # Download the zip
            file_info = self.bot.get_file(msg.document.file_id)
            zip_bytes = self.bot.download_file(file_info.file_path)

            result = {}
            with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
                # Read manifest
                manifest = json.loads(zf.read('manifest.json').decode('utf-8'))

                # Read each bot's code
                for name, meta in manifest.items():
                    code = None
                    zip_path = f'bots/{name}.py'
                    if zip_path in zf.namelist():
                        code = zf.read(zip_path).decode('utf-8')
                    result[name] = {**meta, 'code': code}

            print(f"[TelegramStorage] Restored {len(result)} bot(s) from channel backup.")
            return result

        except Exception as e:
            print(f"[TelegramStorage] Restore failed: {e}")
            return {}

    def send_notification(self, text: str):
        """Send a plain text notification to the backup channel."""
        if not self.enabled:
            return
        try:
            self.bot.send_message(self.channel_id, text, parse_mode='HTML')
        except Exception:
            pass


# ==================== SYSTEM INFO ====================
def get_system_info():
    """Get system information"""
    info = {
        'platform': platform.platform(),
        'processor': platform.processor(),
        'python_version': sys.version.split()[0],
    }
    
    # Memory info (Linux)
    try:
        if platform.system() == 'Linux':
            with open('/proc/meminfo', 'r') as f:
                mem = f.read()
            total = re.search(r'MemTotal:\s+(\d+)', mem)
            available = re.search(r'MemAvailable:\s+(\d+)', mem)
            if total and available:
                total_kb = int(total.group(1))
                avail_kb = int(available.group(1))
                used_kb = total_kb - avail_kb
                info['ram_total_gb'] = round(total_kb / 1024 / 1024, 2)
                info['ram_used_gb'] = round(used_kb / 1024 / 1024, 2)
                info['ram_percent'] = round((used_kb / total_kb) * 100, 1)
    except:
        info['ram_percent'] = 'N/A'
    
    # CPU load (Linux)
    try:
        if platform.system() == 'Linux':
            with open('/proc/loadavg', 'r') as f:
                load = f.read().split()
            info['cpu_load'] = load[0]
    except:
        info['cpu_load'] = 'N/A'
    
    # Disk info
    try:
        stat = os.statvfs('/')
        total = stat.f_frsize * stat.f_blocks
        free = stat.f_frsize * stat.f_bfree
        used = total - free
        info['disk_total_gb'] = round(total / (1024**3), 2)
        info['disk_free_gb'] = round(free / (1024**3), 2)
        info['disk_percent'] = round((used / total) * 100, 1)
    except:
        info['disk_percent'] = 'N/A'
    
    return info

# ==================== BOT MANAGER CLASS ====================
class BotManager:
    def __init__(self):
        self.bots = {}
        self.start_time = datetime.now()
        self.pending_updates = {}  # user_id -> bot_name awaiting file upload
        self.storage = None        # TelegramStorage – set after bot is ready
        self.load_config()
        
        # Cleanup handler
        atexit.register(self.cleanup_all)
        signal.signal(signal.SIGTERM, self.signal_handler)
        signal.signal(signal.SIGINT, self.signal_handler)
    
    def signal_handler(self, signum, frame):
        """Handle shutdown signals"""
        self.cleanup_all()
        sys.exit(0)
    
    def cleanup_all(self):
        """Cleanup all bots on exit"""
        for name in list(self.bots.keys()):
            try:
                self.stop_bot(name)
            except:
                pass
    
    def load_config(self):
        try:
            with open(CONFIG_FILE, 'r') as f:
                content = f.read().strip()
                if content:
                    self.bots = json.loads(content)
        except:
            self.bots = {}
    
    def restore_from_channel(self) -> str:
        """
        Restore all bots from the Telegram channel backup.
        Called at startup when local files are missing or stale.
        Returns a human-readable status string.
        """
        if not self.storage or not self.storage.enabled:
            return "❌ Backup channel not configured."

        restored_data = self.storage.restore()
        if not restored_data:
            return "⚠️ No backup found in the channel."

        os.makedirs(BOTS_DIR, exist_ok=True)
        results = []

        for name, meta in restored_data.items():
            code = meta.get('code')
            if not code:
                results.append(f"⚠️ {name}: no code in backup, skipped.")
                continue

            # Write the .py file
            bot_file = os.path.join(BOTS_DIR, f"{name}.py")
            try:
                with open(bot_file, 'w', encoding='utf-8') as f:
                    f.write(code)
            except Exception as e:
                results.append(f"❌ {name}: failed to write file — {e}")
                continue

            # Install packages silently
            auto_install_packages(code)

            if name not in self.bots:
                self.bots[name] = {
                    'name': name,
                    'file': bot_file,
                    'process': None,
                    'pid': 0,
                    'status': 'stopped',
                    'created_at': meta.get('created_at', str(datetime.now())),
                    'restart_count': 0,
                    'has_error': False,
                    'error_msg': '',
                    'monitor_thread': None,
                }
            else:
                self.bots[name]['file'] = bot_file

            # Start the bot
            success, msg = self._restart_bot_internal(name)
            if success:
                results.append(f"✅ {name}: restored and running.")
            else:
                results.append(f"⚠️ {name}: restored but failed to start — {msg[:80]}")

        # Save local config (without triggering another backup)
        self.save_config(backup_label="no_backup")
        summary = f"🔄 <b>Restore complete</b> — {len(restored_data)} bot(s)\n\n" + "\n".join(results)
        return summary

    def save_config(self, backup_label: str = "auto"):
        save_data = {}
        for name, data in self.bots.items():
            save_data[name] = {
                'name': data['name'],
                'file': data['file'],
                'pid': data.get('pid', 0),
                'status': data['status'],
                'created_at': data['created_at'],
                'restart_count': data['restart_count'],
                'has_error': data.get('has_error', False)
            }
        with open(CONFIG_FILE, 'w') as f:
            json.dump(save_data, f, indent=2)

        # Cloud backup to Telegram channel
        if self.storage and self.storage.enabled and backup_label != "no_backup":
            threading.Thread(
                target=self.storage.save,
                args=(self.bots, backup_label),
                daemon=True
            ).start()
    
    def log(self, message, level="INFO"):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_entry = f"[{timestamp}] [{level}] {message}\n"
        with open(LOG_FILE, 'a') as f:
            f.write(log_entry)
        print(log_entry.strip())
    
    def log_error(self, bot_name, error_msg):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        error_entry = f"[{timestamp}] BOT: {bot_name}\nERROR: {error_msg}\n{'='*50}\n"
        with open(ERROR_LOG, 'a') as f:
            f.write(error_entry)
        print(f"ERROR [{bot_name}]: {error_msg[:200]}")
    
    def create_bot(self, name, code):
        """Create and start a new bot"""
        if name in self.bots:
            return False, "Bot already exists with this name"
        
        # Auto install packages
        installed, failed = auto_install_packages(code)
        
        install_msg = ""
        if installed:
            install_msg = f"\nPackages Installed: {', '.join(installed)}"
        if failed:
            install_msg += f"\nFailed: {', '.join(failed)}"
        
        # Save bot file
        bot_file = os.path.join(BOTS_DIR, f"{name}.py")
        try:
            with open(bot_file, 'w', encoding='utf-8') as f:
                f.write(code)
        except Exception as e:
            return False, f"Failed to save file: {str(e)}"
        
        # Start bot
        return self._start_bot_process(name, bot_file, install_msg)
    
    def _start_bot_process(self, name, bot_file, extra_msg=""):
        """Start bot process with proper environment"""
        try:
            # Create subprocess with new session (prevents parent death killing child)
            if platform.system() != 'Windows':
                process = subprocess.Popen(
                    [sys.executable, bot_file],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    cwd=os.getcwd(),
                    preexec_fn=os.setsid,  # New process group
                    env=os.environ.copy()  # Pass environment
                )
            else:
                process = subprocess.Popen(
                    [sys.executable, bot_file],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    cwd=os.getcwd(),
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
                )
            
            # Wait for startup
            time.sleep(3)
            
            # Check if process is alive
            if process.poll() is not None:
                _, stderr = process.communicate()
                error_msg = stderr if stderr else "Unknown error - process exited immediately"
                self.log_error(name, error_msg)
                return False, f"Bot Failed to Start!\n\nError:\n{error_msg[-500:]}{extra_msg}"
            
            # Initialize bot data
            self.bots[name] = {
                'name': name,
                'file': bot_file,
                'process': process,
                'pid': process.pid,
                'status': 'running',
                'created_at': str(datetime.now()),
                'restart_count': 0,
                'has_error': False,
                'error_msg': '',
                'monitor_thread': None
            }
            
            # Start robust monitoring
            monitor = threading.Thread(target=self._robust_monitor, args=(name,), daemon=True)
            monitor.start()
            self.bots[name]['monitor_thread'] = monitor
            
            self.save_config(backup_label=f"create:{name}")
            self.log(f"Bot {name} started - PID: {process.pid}")
            
            return True, f"Bot Started Successfully!\nPID: {process.pid}{extra_msg}"
            
        except Exception as e:
            self.log_error(name, str(e))
            return False, f"Failed to start bot: {str(e)}"
    
    def _robust_monitor(self, name):
        """Enhanced monitoring with better restart logic"""
        restart_delay = 5
        max_restart_delay = 300  # Max 5 minutes delay
        
        while name in self.bots:
            try:
                if name not in self.bots or 'process' not in self.bots[name]:
                    break
                
                process = self.bots[name]['process']
                
                # Check if process is alive
                if process.poll() is not None:
                    returncode = process.returncode
                    stdout, stderr = process.communicate()
                    
                    error_msg = stderr if stderr else f"Process exited with code {returncode}"
                    
                    self.bots[name]['status'] = 'stopped'
                    self.bots[name]['has_error'] = True
                    self.bots[name]['error_msg'] = error_msg[-500:]
                    
                    self.log_error(name, f"Exit code: {returncode}\n{error_msg}")
                    self.log(f"Bot {name} crashed - attempting restart in {restart_delay}s")
                    
                    # Exponential backoff for restarts
                    time.sleep(restart_delay)
                    
                    # Try restart
                    success, _ = self._restart_bot_internal(name)
                    
                    if success:
                        self.log(f"Bot {name} restarted successfully")
                        restart_delay = 5  # Reset delay
                    else:
                        self.log(f"Bot {name} restart failed")
                        restart_delay = min(restart_delay * 2, max_restart_delay)
                    
                    self.save_config()
                
                time.sleep(5)
                
            except Exception as e:
                self.log(f"Monitor error for {name}: {str(e)}")
                time.sleep(10)
    
    def _restart_bot_internal(self, name):
        """Internal restart without returning message"""
        if name not in self.bots:
            return False, "Bot not found"
        
        try:
            # Kill old process if exists
            old_process = self.bots[name].get('process')
            if old_process and old_process.poll() is None:
                try:
                    if platform.system() != 'Windows':
                        os.killpg(os.getpgid(old_process.pid), signal.SIGTERM)
                    else:
                        old_process.terminate()
                    time.sleep(2)
                    if old_process.poll() is None:
                        old_process.kill()
                except:
                    pass
            
            # Check if file exists
            if not os.path.exists(self.bots[name]['file']):
                return False, "Bot file not found"
            
            # Read code and install packages
            with open(self.bots[name]['file'], 'r') as f:
                code = f.read()
            auto_install_packages(code)
            
            # Start new process
            if platform.system() != 'Windows':
                process = subprocess.Popen(
                    [sys.executable, self.bots[name]['file']],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    preexec_fn=os.setsid,
                    env=os.environ.copy()
                )
            else:
                process = subprocess.Popen(
                    [sys.executable, self.bots[name]['file']],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
                )
            
            time.sleep(3)
            
            if process.poll() is not None:
                _, stderr = process.communicate()
                self.bots[name]['has_error'] = True
                self.bots[name]['error_msg'] = stderr[-500:] if stderr else "Unknown error"
                self.bots[name]['status'] = 'stopped'
                self.log_error(name, str(stderr))
                return False, f"Restart failed: {stderr[-200:]}"
            
            self.bots[name]['process'] = process
            self.bots[name]['pid'] = process.pid
            self.bots[name]['status'] = 'running'
            self.bots[name]['restart_count'] += 1
            self.bots[name]['has_error'] = False
            self.bots[name]['error_msg'] = ''
            
            return True, "Success"
            
        except Exception as e:
            return False, str(e)
    
    def stop_bot(self, name):
        if name not in self.bots:
            return False, "Bot not found"
        
        try:
            process = self.bots[name].get('process')
            if process and process.poll() is None:
                try:
                    if platform.system() != 'Windows':
                        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                    else:
                        process.terminate()
                    time.sleep(2)
                    if process.poll() is None:
                        process.kill()
                except:
                    pass
            
            self.bots[name]['status'] = 'stopped'
            self.bots[name]['process'] = None
            self.save_config(backup_label=f"stop:{name}")
            self.log(f"Bot {name} stopped")
            return True, "Bot stopped successfully"
        except Exception as e:
            return False, f"Error stopping bot: {str(e)}"
    
    def restart_bot(self, name):
        """Public restart method"""
        success, msg = self._restart_bot_internal(name)
        if success:
            msg = f"Bot restarted successfully!\nPID: {self.bots[name]['pid']}"
        return success, msg
    
    def delete_bot(self, name):
        if name not in self.bots:
            return False, "Bot not found"
        
        try:
            self.stop_bot(name)
            if os.path.exists(self.bots[name]['file']):
                os.remove(self.bots[name]['file'])
            del self.bots[name]
            self.save_config(backup_label=f"delete:{name}")
            self.log(f"Bot {name} deleted")
            return True, "Bot deleted successfully"
        except Exception as e:
            return False, f"Delete error: {str(e)}"
    
    def update_bot(self, name, new_code):
        """Stop existing bot, replace its code, and restart it"""
        if name not in self.bots:
            return False, "Bot not found"
        
        # Install any new packages
        installed, failed = auto_install_packages(new_code)
        install_msg = ""
        if installed:
            install_msg = f"\nNew packages installed: {', '.join(installed)}"
        if failed:
            install_msg += f"\nFailed to install: {', '.join(failed)}"
        
        # Stop old process
        self.stop_bot(name)
        
        # Overwrite bot file
        bot_file = self.bots[name]['file']
        try:
            with open(bot_file, 'w', encoding='utf-8') as f:
                f.write(new_code)
        except Exception as e:
            return False, f"Failed to write file: {str(e)}"
        
        # Restart with new code
        success, msg = self._restart_bot_internal(name)
        if success:
            self.save_config(backup_label=f"update:{name}")
            return True, f"Bot updated and restarted successfully!\nPID: {self.bots[name]['pid']}{install_msg}"
        return False, f"Code saved but restart failed:\n{msg}"

    def get_bot_error(self, name):
        if name in self.bots and self.bots[name].get('has_error'):
            return self.bots[name].get('error_msg', 'Unknown error')
        return None
    
    def get_stats(self):
        running = sum(1 for b in self.bots.values() if b['status'] == 'running')
        stopped = sum(1 for b in self.bots.values() if b['status'] == 'stopped')
        error_bots = sum(1 for b in self.bots.values() if b.get('has_error'))
        
        sys_info = get_system_info()
        uptime = str(datetime.now() - self.start_time).split('.')[0]
        
        return {
            'total': len(self.bots),
            'running': running,
            'stopped': stopped,
            'errors': error_bots,
            'cpu_load': sys_info.get('cpu_load', 'N/A'),
            'ram_percent': sys_info.get('ram_percent', 'N/A'),
            'disk_percent': sys_info.get('disk_percent', 'N/A'),
            'uptime': uptime,
            'platform': sys_info.get('platform', 'Unknown')
        }
    
    def get_all_bots_info(self):
        info = []
        for name, data in self.bots.items():
            info.append({
                'name': name,
                'status': data['status'],
                'pid': data.get('pid', 0),
                'error': data.get('has_error', False),
                'restarts': data['restart_count'],
                'created': data['created_at'][:19] if 'created_at' in data else 'Unknown'
            })
        return info

# Initialize manager
manager = BotManager()

# Attach cloud storage to manager (TelegramStorage uses the bot instance)
if BACKUP_CHANNEL:
    manager.storage = TelegramStorage(bot, BACKUP_CHANNEL)
    print(f"[TelegramStorage] Backup channel configured: {BACKUP_CHANNEL}")
else:
    print("[TelegramStorage] No backup channel set. Data will not persist across hosts.")

# ==================== ADMIN CHECK ====================
def is_admin(user_id):
    return user_id == ADMIN_ID

# ==================== KEYBOARDS ====================
def main_menu_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=2)
    buttons = [
        types.InlineKeyboardButton("➕ Add Bot", callback_data='add_bot'),
        types.InlineKeyboardButton("📋 My Bots", callback_data='list_bots'),
        types.InlineKeyboardButton("🎛️ Manage Bot", callback_data='control_bot'),
        types.InlineKeyboardButton("🔄 Update Bot", callback_data='update_bot'),
        types.InlineKeyboardButton("🗑️ Remove Bot", callback_data='delete_bot'),
        types.InlineKeyboardButton("📊 System Info", callback_data='stats'),
        types.InlineKeyboardButton("🚨 View Errors", callback_data='view_errors'),
        types.InlineKeyboardButton("▶️ Start All", callback_data='start_all'),
        types.InlineKeyboardButton("⏹️ Stop All", callback_data='stop_all'),
        types.InlineKeyboardButton("🔁 Restart All", callback_data='restart_all'),
        types.InlineKeyboardButton("💾 Local Backup", callback_data='export'),
        types.InlineKeyboardButton("☁️ Cloud Backup", callback_data='cloud_backup'),
        types.InlineKeyboardButton("♻️ Cloud Restore", callback_data='cloud_restore'),
        types.InlineKeyboardButton("💓 Keep Alive", callback_data='keep_alive'),
    ]
    markup.add(*buttons)
    return markup

def back_keyboard():
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🏠 Back to Menu", callback_data='main_menu'))
    return markup

# ==================== COMMANDS ====================
@bot.message_handler(commands=['start', 'menu'])
def start_command(message):
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "Access Denied! Admin only.")
        return
    
    stats = manager.get_stats()
    
    welcome = f"""
🖥️ <b>BOT HOSTING CONTROL PANEL</b>

<b>📊 Bot Status:</b>
▶️ Running: <b>{stats['running']}</b>
⏹️ Stopped: <b>{stats['stopped']}</b>
⚠️ With Errors: <b>{stats['errors']}</b>
📦 Total: <b>{stats['total']}</b>

<b>💻 System Resources:</b>
🔧 CPU Load: <b>{stats['cpu_load']}</b>
🧠 RAM Usage: <b>{stats['ram_percent']}%</b>
💾 Disk Usage: <b>{stats['disk_percent']}%</b>
⏱️ Uptime: <b>{stats['uptime']}</b>

🖥️ Platform: <code>{stats['platform']}</code>

Select an option below:
"""
    bot.send_message(message.chat.id, welcome, parse_mode='HTML', reply_markup=main_menu_keyboard())

# ==================== CALLBACK HANDLERS ====================
@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "Access Denied!")
        return
    
    chat_id = call.message.chat.id
    msg_id = call.message.message_id
    
    # ADD BOT
    if call.data == 'add_bot':
        text = """➕ <b>ADD NEW BOT</b>

Send your Python bot code in one of two ways:
1️⃣ Upload a <code>.py</code> file directly
2️⃣ Paste the code as a text message

📌 The first line of your code must include:
<code># bot_name: your_bot_name</code>

Packages will be auto-detected and installed.
"""
        bot.edit_message_text(text, chat_id, msg_id, parse_mode='HTML', reply_markup=back_keyboard())
        bot.answer_callback_query(call.id)
    
    # LIST BOTS
    elif call.data == 'list_bots':
        bots_info = manager.get_all_bots_info()
        
        if not bots_info:
            text = "No bots found! Add bots using 'Add Bot' option."
        else:
            text = "📋 <b>YOUR BOTS</b>\n\n"
            for i, bot_info in enumerate(bots_info, 1):
                status_symbol = "▶️ RUNNING" if bot_info['status'] == 'running' else "⏹️ STOPPED"
                error_symbol = " ⚠️ ERROR" if bot_info['error'] else ""
                
                text += f"{i}. <b>{bot_info['name']}</b> [{status_symbol}]{error_symbol}\n"
                text += f"   PID: <code>{bot_info['pid']}</code> | Restarts: {bot_info['restarts']}\n"
                text += f"   Created: {bot_info['created']}\n\n"
        
        bot.edit_message_text(text, chat_id, msg_id, parse_mode='HTML', reply_markup=back_keyboard())
        bot.answer_callback_query(call.id)
    
    # CONTROL BOT
    elif call.data == 'control_bot':
        bots_info = manager.get_all_bots_info()
        
        if not bots_info:
            bot.edit_message_text("No bots to control!", chat_id, msg_id, reply_markup=back_keyboard())
            bot.answer_callback_query(call.id)
            return
        
        markup = types.InlineKeyboardMarkup(row_width=1)
        for bot_info in bots_info:
            status = "▶️" if bot_info['status'] == 'running' else "⏹️"
            btn_text = f"{status} {bot_info['name']}"
            markup.add(types.InlineKeyboardButton(btn_text, callback_data=f"manage_{bot_info['name']}"))
        markup.add(types.InlineKeyboardButton("🏠 Back", callback_data='main_menu'))
        
        bot.edit_message_text("🎛️ <b>MANAGE BOT</b>\n\nSelect bot to control:", chat_id, msg_id, parse_mode='HTML', reply_markup=markup)
        bot.answer_callback_query(call.id)
    
    # MANAGE SPECIFIC BOT
    elif call.data.startswith('manage_'):
        bot_name = call.data[7:]
        
        if bot_name not in manager.bots:
            bot.answer_callback_query(call.id, "Bot not found!")
            return
        
        bot_data = manager.bots[bot_name]
        error_msg = manager.get_bot_error(bot_name)
        
        status_icon = "▶️" if bot_data['status'] == 'running' else "⏹️"
        text = f"""🎛️ <b>BOT: {bot_name}</b>

{status_icon} Status: <b>{bot_data['status'].upper()}</b>
🆔 PID: <code>{bot_data.get('pid', 'N/A')}</code>
🔁 Restart Count: <b>{bot_data['restart_count']}</b>
📁 File: <code>{bot_data['file']}</code>
"""
        if error_msg:
            text += f"\n🚨 <b>Last Error:</b>\n<code>{error_msg[:200]}...</code>"
        
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            types.InlineKeyboardButton("▶️ Start", callback_data=f"start_{bot_name}"),
            types.InlineKeyboardButton("⏹️ Stop", callback_data=f"stop_{bot_name}"),
            types.InlineKeyboardButton("🔁 Restart", callback_data=f"restart_{bot_name}"),
            types.InlineKeyboardButton("🚨 View Error", callback_data=f"error_{bot_name}"),
            types.InlineKeyboardButton("🏠 Back", callback_data='control_bot')
        )
        
        bot.edit_message_text(text, chat_id, msg_id, parse_mode='HTML', reply_markup=markup)
        bot.answer_callback_query(call.id)
    
    # VIEW ERROR
    elif call.data.startswith('error_'):
        bot_name = call.data[6:]
        error_msg = manager.get_bot_error(bot_name)
        
        if error_msg:
            text = f"🚨 <b>Error for: {bot_name}</b>\n\n<code>{error_msg}</code>"
        else:
            text = f"✅ No errors recorded for <b>{bot_name}</b>"
        
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("🔙 Back", callback_data=f"manage_{bot_name}"))
        
        bot.edit_message_text(text[:4000], chat_id, msg_id, parse_mode='HTML', reply_markup=markup)
        bot.answer_callback_query(call.id)
    
    # START BOT
    elif call.data.startswith('start_'):
        bot_name = call.data[6:]
        success, msg = manager.restart_bot(bot_name)
        bot.answer_callback_query(call.id, msg)
    
    # STOP BOT
    elif call.data.startswith('stop_'):
        bot_name = call.data[5:]
        success, msg = manager.stop_bot(bot_name)
        bot.answer_callback_query(call.id, msg)
    
    # RESTART BOT
    elif call.data.startswith('restart_'):
        bot_name = call.data[8:]
        success, msg = manager.restart_bot(bot_name)
        bot.answer_callback_query(call.id, msg)
    
    # UPDATE BOT
    elif call.data == 'update_bot':
        bots_info = manager.get_all_bots_info()
        
        if not bots_info:
            bot.edit_message_text("No bots available to update!", chat_id, msg_id, reply_markup=back_keyboard())
            bot.answer_callback_query(call.id)
            return
        
        markup = types.InlineKeyboardMarkup(row_width=1)
        for bot_info in bots_info:
            status = "▶️" if bot_info['status'] == 'running' else "⏹️"
            markup.add(types.InlineKeyboardButton(
                f"{status} Update: {bot_info['name']}",
                callback_data=f"select_update_{bot_info['name']}"
            ))
        markup.add(types.InlineKeyboardButton("🏠 Back", callback_data='main_menu'))
        
        bot.edit_message_text(
            "🔄 <b>UPDATE BOT</b>\n\nSelect bot to update:",
            chat_id, msg_id, parse_mode='HTML', reply_markup=markup
        )
        bot.answer_callback_query(call.id)
    
    elif call.data.startswith('select_update_'):
        bot_name = call.data[14:]
        if bot_name not in manager.bots:
            bot.answer_callback_query(call.id, "Bot not found!")
            return
        
        msg = bot.edit_message_text(
            f"🔄 <b>UPDATE: {bot_name}</b>\n\nSend the new .py file for this bot.\nThe bot will be stopped, replaced, and restarted automatically.\n\n<i>Note: File must include <code># bot_name: {bot_name}</code> in the first line or the name will be taken from filename.</i>",
            chat_id, msg_id, parse_mode='HTML', reply_markup=back_keyboard()
        )
        bot.answer_callback_query(call.id)
        
        # Store pending update state
        manager.pending_updates[call.from_user.id] = bot_name

    # DELETE BOT
    elif call.data == 'delete_bot':
        bots_info = manager.get_all_bots_info()
        
        if not bots_info:
            bot.edit_message_text("No bots to delete!", chat_id, msg_id, reply_markup=back_keyboard())
            bot.answer_callback_query(call.id)
            return
        
        markup = types.InlineKeyboardMarkup(row_width=1)
        for bot_info in bots_info:
            markup.add(types.InlineKeyboardButton(
                f"Delete: {bot_info['name']}", 
                callback_data=f"confirm_delete_{bot_info['name']}"
            ))
        markup.add(types.InlineKeyboardButton("🏠 Back", callback_data='main_menu'))
        
        bot.edit_message_text("🗑️ <b>DELETE BOT</b>\n\nSelect bot to delete:", chat_id, msg_id, parse_mode='HTML', reply_markup=markup)
        bot.answer_callback_query(call.id)
    
    # CONFIRM DELETE
    elif call.data.startswith('confirm_delete_'):
        bot_name = call.data[15:]
        success, msg = manager.delete_bot(bot_name)
        
        status_icon = "✅" if success else "❌"
        bot.edit_message_text(
            f"{status_icon} <b>DELETE RESULT</b>\n\nBot: <code>{bot_name}</code>\nStatus: {msg}",
            chat_id, msg_id, parse_mode='HTML', reply_markup=back_keyboard()
        )
        bot.answer_callback_query(call.id, msg)
    
    # SYSTEM STATS
    elif call.data == 'stats':
        stats = manager.get_stats()
        
        text = f"""📊 <b>SYSTEM STATISTICS</b>

<b>🤖 Bot Status:</b>
▶️ Running: <b>{stats['running']}</b>
⏹️ Stopped: <b>{stats['stopped']}</b>
⚠️ With Errors: <b>{stats['errors']}</b>
📦 Total: <b>{stats['total']}</b>

<b>💻 System Resources:</b>
🔧 CPU Load: <b>{stats['cpu_load']}</b>
🧠 RAM Usage: <b>{stats['ram_percent']}%</b>
💾 Disk Usage: <b>{stats['disk_percent']}%</b>

<b>ℹ️ System Info:</b>
⏱️ Uptime: <b>{stats['uptime']}</b>
🖥️ Platform: <code>{stats['platform']}</code>
"""
        bot.edit_message_text(text, chat_id, msg_id, parse_mode='HTML', reply_markup=back_keyboard())
        bot.answer_callback_query(call.id)
    
    # VIEW ALL ERRORS
    elif call.data == 'view_errors':
        try:
            with open(ERROR_LOG, 'r') as f:
                errors = f.read()[-3000:]
            if errors.strip():
                text = f"🚨 <b>ERROR LOGS</b>\n\n<code>{errors}</code>"
            else:
                text = "✅ No errors recorded!"
        except:
            text = "❌ Error reading log file"
        
        bot.edit_message_text(text[:4000], chat_id, msg_id, parse_mode='HTML', reply_markup=back_keyboard())
        bot.answer_callback_query(call.id)
    
    # START ALL
    elif call.data == 'start_all':
        count = len(manager.bots)
        for name in list(manager.bots.keys()):
            manager.restart_bot(name)
        bot.edit_message_text(f"▶️ <b>All {count} bot(s) started!</b>", chat_id, msg_id, parse_mode='HTML', reply_markup=back_keyboard())
        bot.answer_callback_query(call.id, "Starting all bots...")
    
    # STOP ALL
    elif call.data == 'stop_all':
        count = len(manager.bots)
        for name in list(manager.bots.keys()):
            manager.stop_bot(name)
        bot.edit_message_text(f"⏹️ <b>All {count} bot(s) stopped!</b>", chat_id, msg_id, parse_mode='HTML', reply_markup=back_keyboard())
        bot.answer_callback_query(call.id, "Stopping all bots...")
    
    # RESTART ALL
    elif call.data == 'restart_all':
        count = len(manager.bots)
        for name in list(manager.bots.keys()):
            manager.restart_bot(name)
        bot.edit_message_text(f"🔁 <b>All {count} bot(s) restarted!</b>", chat_id, msg_id, parse_mode='HTML', reply_markup=back_keyboard())
        bot.answer_callback_query(call.id, "Restarting all bots...")
    
    # KEEP ALIVE
    elif call.data == 'keep_alive':
        text = f"""💓 <b>KEEP ALIVE STATUS</b>

🟢 Self-ping: <b>ACTIVE</b>
⏱️ Interval: Every 5 minutes
🔗 Ping URL: <code>{RENDER_EXTERNAL_URL}/ping</code>
🌐 Port: <code>{PORT}</code>

🔄 Crashed bots are auto-restarted with exponential backoff.
"""
        bot.edit_message_text(text, chat_id, msg_id, parse_mode='HTML', reply_markup=back_keyboard())
        bot.answer_callback_query(call.id)
    
    # CLOUD BACKUP (manual trigger)
    elif call.data == 'cloud_backup':
        if not manager.storage or not manager.storage.enabled:
            bot.edit_message_text(
                "❌ <b>Cloud backup not configured.</b>\n\nSet the <code>BACKUP_CHANNEL</code> environment variable to your channel ID and make this bot an admin of that channel.",
                chat_id, msg_id, parse_mode='HTML', reply_markup=back_keyboard()
            )
            bot.answer_callback_query(call.id)
            return

        bot.answer_callback_query(call.id, "Uploading backup...")
        status_msg = bot.edit_message_text(
            "☁️ <b>Uploading backup to channel...</b>",
            chat_id, msg_id, parse_mode='HTML'
        )
        ok = manager.storage.save(manager.bots, label="manual")
        if ok:
            bot.edit_message_text(
                f"✅ <b>Cloud backup successful!</b>\n\n📦 {len(manager.bots)} bot(s) saved to channel.\n🔗 Channel: <code>{BACKUP_CHANNEL}</code>",
                chat_id, status_msg.message_id, parse_mode='HTML', reply_markup=back_keyboard()
            )
        else:
            bot.edit_message_text(
                "❌ <b>Backup failed.</b> Check that the bot is admin of the backup channel.",
                chat_id, status_msg.message_id, parse_mode='HTML', reply_markup=back_keyboard()
            )

    # CLOUD RESTORE
    elif call.data == 'cloud_restore':
        if not manager.storage or not manager.storage.enabled:
            bot.edit_message_text(
                "❌ <b>Cloud backup not configured.</b>\n\nSet the <code>BACKUP_CHANNEL</code> environment variable to your channel ID.",
                chat_id, msg_id, parse_mode='HTML', reply_markup=back_keyboard()
            )
            bot.answer_callback_query(call.id)
            return

        # Confirm prompt
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            types.InlineKeyboardButton("✅ Yes, Restore", callback_data='confirm_cloud_restore'),
            types.InlineKeyboardButton("❌ Cancel", callback_data='main_menu')
        )
        bot.edit_message_text(
            "♻️ <b>CLOUD RESTORE</b>\n\n⚠️ This will download all bots from the latest channel backup and restart them.\n\nExisting running bots will be stopped and replaced.\n\nContinue?",
            chat_id, msg_id, parse_mode='HTML', reply_markup=markup
        )
        bot.answer_callback_query(call.id)

    elif call.data == 'confirm_cloud_restore':
        bot.answer_callback_query(call.id, "Restoring from channel...")
        status_msg = bot.edit_message_text(
            "♻️ <b>Restoring from channel backup...</b>\n\nDownloading files and restarting bots, please wait.",
            chat_id, msg_id, parse_mode='HTML'
        )
        result_text = manager.restore_from_channel()
        bot.edit_message_text(
            result_text,
            chat_id, status_msg.message_id, parse_mode='HTML', reply_markup=back_keyboard()
        )

    # EXPORT (local json)
    elif call.data == 'export':
        config_json = json.dumps(
            {k: {k2:v2 for k2,v2 in v.items() if k2 not in ['process', 'monitor_thread']} 
             for k,v in manager.bots.items()},
            indent=2, default=str
        )
        bot.send_document(chat_id, config_json.encode(), visible_file_name='bot_backup.json')
        bot.answer_callback_query(call.id, "Backup sent!")
    
    # MAIN MENU
    elif call.data == 'main_menu':
        start_command(call.message)
        bot.answer_callback_query(call.id)

# ==================== FILE & CODE HANDLERS ====================
@bot.message_handler(content_types=['document'])
def handle_file(message):
    if not is_admin(message.from_user.id):
        return
    
    if not message.document.file_name.endswith('.py'):
        bot.reply_to(message, "⚠️ Please send a .py file only!")
        return
    
    try:
        file_info = bot.get_file(message.document.file_id)
        downloaded = bot.download_file(file_info.file_path)
        code = downloaded.decode('utf-8')
        
        # Check if this is a pending update for an existing bot
        pending_bot_name = manager.pending_updates.pop(message.from_user.id, None)
        if pending_bot_name and pending_bot_name in manager.bots:
            processing_msg = bot.reply_to(message, f"⏳ Updating bot: <b>{pending_bot_name}</b>...", parse_mode='HTML')
            success, msg = manager.update_bot(pending_bot_name, code)
            status_header = "✅ BOT UPDATED" if success else "❌ UPDATE FAILED"
            bot.edit_message_text(
                f"<b>{status_header}</b>\n\nBot: <code>{pending_bot_name}</code>\n{msg}",
                message.chat.id, processing_msg.message_id,
                parse_mode='HTML', reply_markup=back_keyboard()
            )
            return
        
        # Otherwise create new bot
        name_match = re.search(r'#\s*bot_name:\s*(\w+)', code)
        if name_match:
            bot_name = name_match.group(1)
        else:
            bot_name = message.document.file_name.replace('.py', '')
        
        processing_msg = bot.reply_to(message, f"⏳ Processing bot: <b>{bot_name}</b>\nInstalling packages...", parse_mode='HTML')
        
        success, msg = manager.create_bot(bot_name, code)
        status_header = "✅ BOT CREATED" if success else "❌ ERROR"
        bot.edit_message_text(
            f"<b>{status_header}</b>\n\nBot: <code>{bot_name}</code>\n{msg}",
            message.chat.id, processing_msg.message_id,
            parse_mode='HTML', reply_markup=back_keyboard()
        )
    
    except Exception as e:
        bot.reply_to(message, f"❌ Error processing file: {str(e)}")

@bot.message_handler(func=lambda message: True, content_types=['text'])
def handle_text(message):
    if not is_admin(message.from_user.id):
        return
    
    text = message.text
    
    if any(keyword in text for keyword in ['import ', 'def ', 'class ', 'BOT_TOKEN', 'bot_token', 'telebot', 'TeleBot']):
        name_match = re.search(r'#\s*bot_name:\s*(\w+)', text)
        if not name_match:
            bot.reply_to(message, 
                "⚠️ Please add a bot name comment in the first line:\n<code># bot_name: your_bot_name</code>\n\nOr upload a .py file directly.",
                parse_mode='HTML')
            return
        
        bot_name = name_match.group(1)
        
        processing_msg = bot.reply_to(message, f"⏳ Processing bot: <b>{bot_name}</b>\nInstalling packages...", parse_mode='HTML')
        
        success, msg = manager.create_bot(bot_name, text)
        status_header = "✅ BOT CREATED" if success else "❌ ERROR"
        bot.edit_message_text(
            f"<b>{status_header}</b>\n\nBot: <code>{bot_name}</code>\n{msg}",
            message.chat.id, processing_msg.message_id,
            parse_mode='HTML', reply_markup=back_keyboard()
        )

# ==================== HEALTH SERVER (RENDER COMPATIBLE) ====================
def run_health_server():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == '/ping':
                self.send_response(200)
                self.send_header('Content-type', 'text/plain')
                self.end_headers()
                self.wfile.write(b'pong')
                return
            
            stats = manager.get_stats()
            html = f"""
            <html>
            <head>
                <title>Bot Hosting System</title>
                <meta charset="UTF-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <style>
                    body {{ font-family: Arial, sans-serif; margin: 20px; background: #1a1a2e; color: #eee; }}
                    .container {{ max-width: 800px; margin: 0 auto; }}
                    .card {{ background: #16213e; padding: 20px; margin: 15px 0; border-radius: 10px; box-shadow: 0 4px 6px rgba(0,0,0,0.3); }}
                    h1 {{ color: #e94560; text-align: center; }}
                    h2 {{ color: #0f3460; }}
                    .status {{ display: inline-block; padding: 5px 15px; border-radius: 20px; font-weight: bold; }}
                    .online {{ background: #00c853; color: white; }}
                    .warning {{ background: #ffd600; color: black; }}
                    .error {{ background: #ff1744; color: white; }}
                    .stats-grid {{ display: grid; grid-template-columns: repeat(2, 1fr); gap: 15px; }}
                    .stat-item {{ text-align: center; }}
                    .stat-value {{ font-size: 24px; font-weight: bold; color: #e94560; }}
                    .refresh {{ text-align: center; margin-top: 10px; font-size: 12px; color: #666; }}
                </style>
                <script>
                    setTimeout(function(){{ location.reload(); }}, 30000);
                </script>
            </head>
            <body>
                <div class="container">
                    <h1>Bot Hosting System</h1>
                    
                    <div class="card">
                        <h2>System Status</h2>
                        <p>Status: <span class="status online">ONLINE</span></p>
                        <p>Uptime: {stats['uptime']}</p>
                        <p>Platform: {stats['platform']}</p>
                    </div>
                    
                    <div class="card">
                        <h2>Bot Statistics</h2>
                        <div class="stats-grid">
                            <div class="stat-item">
                                <div class="stat-value">{stats['running']}</div>
                                <div>Running</div>
                            </div>
                            <div class="stat-item">
                                <div class="stat-value">{stats['stopped']}</div>
                                <div>Stopped</div>
                            </div>
                            <div class="stat-item">
                                <div class="stat-value">{stats['errors']}</div>
                                <div>Errors</div>
                            </div>
                            <div class="stat-item">
                                <div class="stat-value">{stats['total']}</div>
                                <div>Total</div>
                            </div>
                        </div>
                    </div>
                    
                    <div class="card">
                        <h2>Resources</h2>
                        <p>CPU Load: {stats['cpu_load']}</p>
                        <p>RAM Usage: {stats['ram_percent']}%</p>
                        <p>Disk Usage: {stats['disk_percent']}%</p>
                    </div>
                    
                    <div class="refresh">Auto-refresh every 30 seconds</div>
                </div>
            </body>
            </html>
            """
            self.send_response(200)
            self.send_header('Content-type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(html.encode())
        
        def log_message(self, format, *args):
            pass  # Silent logging
    
    # Try port binding with retry
    max_retries = 5
    for i in range(max_retries):
        try:
            server = HTTPServer(('0.0.0.0', PORT), Handler)
            print(f"Health server running on port {PORT}")
            server.serve_forever()
        except OSError as e:
            if i < max_retries - 1:
                print(f"Port {PORT} busy, retrying...")
                time.sleep(2)
            else:
                print(f"Failed to start health server: {e}")

# ==================== MAIN ====================
if __name__ == "__main__":
    print("=" * 50)
    print("BOT HOSTING SYSTEM - RENDER READY")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Port: {PORT}")
    print("=" * 50)
    
    # Start keep alive
    keep_alive.start()
    
    # Start health server in background
    threading.Thread(target=run_health_server, daemon=True).start()
    print(f"Health server: http://0.0.0.0:{PORT}")
    
    # Determine which bots have their .py files present locally
    local_bots_ok = [
        name for name, data in manager.bots.items()
        if os.path.exists(data.get('file', ''))
    ]
    local_bots_missing = [
        name for name in manager.bots if name not in local_bots_ok
    ]

    if local_bots_missing and manager.storage and manager.storage.enabled:
        print(f"[Startup] {len(local_bots_missing)} bot file(s) missing locally. Restoring from channel...")
        restore_result = manager.restore_from_channel()
        print(f"[Startup] Restore result:\n{restore_result}")
        # Notify admin
        try:
            bot.send_message(
                ADMIN_ID,
                f"🔔 <b>Auto-restore on startup</b>\n\n{restore_result}",
                parse_mode='HTML'
            )
        except Exception:
            pass
    else:
        # All local files present — just restart them
        for bot_name in local_bots_ok:
            try:
                manager.restart_bot(bot_name)
                print(f"Restarted: {bot_name}")
            except Exception as e:
                print(f"Failed to restart {bot_name}: {e}")

        if not manager.bots and manager.storage and manager.storage.enabled:
            # Fresh host with no local config at all — try restore anyway
            print("[Startup] No local bots found. Attempting channel restore...")
            restore_result = manager.restore_from_channel()
            print(f"[Startup] {restore_result}")
            try:
                bot.send_message(ADMIN_ID, f"🔔 <b>Fresh host — auto-restore</b>\n\n{restore_result}", parse_mode='HTML')
            except Exception:
                pass

    print("System ready! Starting bot...")
    
    # Start Telegram bot with error handling
    while True:
        try:
            bot.infinity_polling(timeout=10, long_polling_timeout=5)
        except Exception as e:
            print(f"Bot polling error: {e}")
            time.sleep(5)
