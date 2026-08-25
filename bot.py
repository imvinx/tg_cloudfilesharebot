import os
import sys
import time
import math
import re
import secrets
import logging
import sqlite3
import asyncio
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple, Any
from contextlib import contextmanager

from dotenv import load_dotenv

# Telegram Imports
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ChatMember,
    InputFile
)
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters
)
from telegram.error import TelegramError, Forbidden, BadRequest

# ==============================================================================
# 1. CONFIGURATION & ENVIRONMENT
# ==============================================================================

load_dotenv()

LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
logger = logging.getLogger("VINX_CLOUD")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
BACKEND_CHANNEL_ID_RAW = os.getenv("BACKEND_CHANNEL_ID", "").strip()
ADMIN_IDS_RAW = os.getenv("ADMIN_IDS", "").strip()
SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "admin").strip().lstrip("@")
DATABASE_PATH = os.getenv("DATABASE_PATH", "vinx_cloud.db").strip()
DEFAULT_STORAGE = int(os.getenv("DEFAULT_STORAGE", str(1024 * 1024 * 1024 * 1024))) # 1 TB in bytes
FORCE_JOIN_ENABLED_DEFAULT = os.getenv("FORCE_JOIN_ENABLED", "false").lower() in ("true", "1", "yes")
MAX_FILE_SIZE = int(os.getenv("MAX_FILE_SIZE", str(2 * 1024 * 1024 * 1024))) # 2 GB limit

try:
    BACKEND_CHANNEL_ID = int(BACKEND_CHANNEL_ID_RAW) if BACKEND_CHANNEL_ID_RAW else 0
except ValueError:
    BACKEND_CHANNEL_ID = 0

ADMIN_IDS: List[int] = []
if ADMIN_IDS_RAW:
    for aid in ADMIN_IDS_RAW.split(","):
        aid_clean = aid.strip()
        if aid_clean.isdigit() or (aid_clean.startswith("-") and aid_clean[1:].isdigit()):
            ADMIN_IDS.append(int(aid_clean))

# ==============================================================================
# 2. UNICODE & FORMATTING UTILITIES
# ==============================================================================

UNICODE_BOLD_MAP = {
    'A': '𝗔', 'B': '𝗕', 'C': '𝗖', 'D': '𝗗', 'E': '𝗘', 'F': '𝗙', 'G': '𝗚', 'H': '𝗛', 'I': '𝗜',
    'J': '𝗝', 'K': '𝗞', 'L': '𝗟', 'M': '𝗠', 'N': '𝗡', 'O': '𝗢', 'P': '𝗣', 'Q': '𝗤', 'R': '𝗥',
    'S': '𝗦', 'T': '𝗧', 'U': '𝗨', 'V': '𝗩', 'W': '𝗪', 'X': '𝗫', 'Y': '𝗬', 'Z': '𝗭',
    'a': '𝗮', 'b': '𝗯', 'c': '𝗰', 'd': '𝗱', 'e': '𝗲', 'f': '𝗳', 'g': '𝗴', 'h': '𝗵', 'i': '𝗶',
    'j': '𝗷', 'k': '𝗸', 'l': '𝗹', 'm': '𝗺', 'n': '𝗻', 'o': '𝗼', 'p': '𝗽', 'q': '𝗾', 'r': '𝗿',
    's': '𝘀', 't': '𝘁', 'u': '𝘂', 'v': '𝘃', 'w': '𝘄', 'x': '𝘁', 'y': '𝘆', 'z': '𝘇',
    '0': '𝟬', '1': '𝟭', '2': '𝟮', '3': '𝟯', '4': '𝟰', '5': '𝟱', '6': '𝟲', '7': '𝟳', '8': '𝟴', '9': '𝟵'
}

def format_title(text: str) -> str:
    """Converts standard text to Styled Unicode Sans-Serif Bold text for visual hierarchy."""
    return "".join(UNICODE_BOLD_MAP.get(c, c) for c in text)

def format_bytes(size_bytes: int) -> str:
    """Formats raw byte count into clean human-readable units (B, KB, MB, GB, TB)."""
    if size_bytes <= 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    i = int(math.floor(math.log(size_bytes, 1024)))
    p = math.pow(1024, i)
    s = round(size_bytes / p, 1)
    if s == int(s):
        s = int(s)
    return f"{s} {units[i]}"

def calculate_percentage(used: int, total: int) -> float:
    """Calculates used percentage clamped between 0 and 100."""
    if total <= 0:
        return 0.0
    pct = (used / total) * 100.0
    return min(100.0, max(0.0, round(pct, 1)))

def generate_progress_bar(used: int, total: int, length: int = 16) -> str:
    """Generates a dynamic Unicode progress bar with percentage indicator."""
    pct = calculate_percentage(used, total)
    filled_length = int(round((pct / 100.0) * length))
    filled_length = min(length, max(0, filled_length))
    bar = "█" * filled_length + "░" * (length - filled_length)
    pct_str = f"{int(pct)}%" if pct == int(pct) else f"{pct:.1f}%"
    return f"{bar} {pct_str}"

# ==============================================================================
# 3. RATE LIMITER
# ==============================================================================

class RateLimiter:
    """In-memory rate limiter to protect against request spam."""
    def __init__(self):
        self.user_cooldowns: Dict[Tuple[int, str], float] = {}

    def is_rate_limited(self, user_id: int, action: str, cooldown_seconds: float) -> bool:
        now = time.time()
        key = (user_id, action)
        last_time = self.user_cooldowns.get(key, 0.0)
        if now - last_time < cooldown_seconds:
            return True
        self.user_cooldowns[key] = now
        return False

rate_limiter = RateLimiter()

# ==============================================================================
# 4. DATABASE SYSTEM (SQLITE)
# ==============================================================================

class DatabaseManager:
    """Thread-safe SQLite Database Manager for VINX CLOUD."""
    def __init__(self, db_path: str):
        self.db_path = db_path

    @contextmanager
    def get_connection(self):
        conn = sqlite3.connect(self.db_path, timeout=15.0)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init_db(self):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            # Enable WAL mode for optimal concurrent performance
            cursor.execute("PRAGMA journal_mode=WAL;")

            # Users table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    storage_limit INTEGER DEFAULT 1099511627776,
                    storage_used INTEGER DEFAULT 0,
                    file_count INTEGER DEFAULT 0,
                    photo_count INTEGER DEFAULT 0,
                    video_count INTEGER DEFAULT 0,
                    document_count INTEGER DEFAULT 0,
                    audio_count INTEGER DEFAULT 0,
                    code_count INTEGER DEFAULT 0,
                    other_count INTEGER DEFAULT 0,
                    premium INTEGER DEFAULT 0,
                    banned INTEGER DEFAULT 0,
                    created_at TEXT,
                    last_active TEXT
                );
            """)

            # Files table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS files (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_id INTEGER,
                    telegram_file_id TEXT,
                    file_unique_id TEXT,
                    backend_message_id INTEGER,
                    filename TEXT,
                    extension TEXT,
                    mime_type TEXT,
                    file_type TEXT,
                    size_bytes INTEGER,
                    category TEXT,
                    favorite INTEGER DEFAULT 0,
                    trash INTEGER DEFAULT 0,
                    created_at TEXT,
                    updated_at TEXT,
                    caption TEXT,
                    FOREIGN KEY(owner_id) REFERENCES users(user_id)
                );
            """)

            # Storage Plans table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS plans (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT,
                    storage_limit INTEGER,
                    price TEXT,
                    currency TEXT,
                    enabled INTEGER DEFAULT 1
                );
            """)

            # Force Join Channels table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS force_join_channels (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_id TEXT UNIQUE,
                    title TEXT,
                    invite_link TEXT,
                    created_at TEXT
                );
            """)

            # Key-Value Settings table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT
                );
            """)

            # File Share Links table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS share_links (
                    token TEXT PRIMARY KEY,
                    file_id INTEGER,
                    owner_id INTEGER,
                    expires_at TEXT,
                    enabled INTEGER DEFAULT 1,
                    created_at TEXT,
                    FOREIGN KEY(file_id) REFERENCES files(id)
                );
            """)

            # Admin Logs table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS admin_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    admin_id INTEGER,
                    action TEXT,
                    target_user_id INTEGER,
                    details TEXT,
                    created_at TEXT
                );
            """)

            # Insert default plans if table empty
            cursor.execute("SELECT COUNT(*) as cnt FROM plans")
            if cursor.fetchone()["cnt"] == 0:
                default_plans = [
                    ("FREE", 1099511627776, "Free", "USD", 1),
                    ("PRO", 2199023255552, "$4.99/mo", "USD", 1),
                    ("ULTRA", 5497558138880, "$9.99/mo", "USD", 1),
                    ("MAX", 10995116277760, "$19.99/mo", "USD", 1),
                ]
                cursor.executemany(
                    "INSERT INTO plans (name, storage_limit, price, currency, enabled) VALUES (?, ?, ?, ?, ?)",
                    default_plans
                )

            # Insert default settings if missing
            cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('force_join_enabled', ?)",
                           ("true" if FORCE_JOIN_ENABLED_DEFAULT else "false",))
            cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('maintenance_mode', 'false')")
            logger.info("Database schema verified and ready.")

    # ---------------- USER OPERATIONS ----------------
    def get_or_create_user(self, user_id: int, username: Optional[str], first_name: str) -> sqlite3.Row:
        now = datetime.utcnow().isoformat()
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
            user = cursor.fetchone()
            if not user:
                cursor.execute("""
                    INSERT INTO users (user_id, username, first_name, storage_limit, created_at, last_active)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (user_id, username, first_name, DEFAULT_STORAGE, now, now))
                cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
                user = cursor.fetchone()
            else:
                cursor.execute("""
                    UPDATE users SET username = ?, first_name = ?, last_active = ? WHERE user_id = ?
                """, (username, first_name, now, user_id))
            return user

    def get_user(self, user_id: int) -> Optional[sqlite3.Row]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
            return cursor.fetchone()

    def get_user_by_username_or_id(self, identifier: str) -> Optional[sqlite3.Row]:
        clean_id = identifier.strip().lstrip("@")
        with self.get_connection() as conn:
            cursor = conn.cursor()
            if clean_id.isdigit():
                cursor.execute("SELECT * FROM users WHERE user_id = ?", (int(clean_id),))
            else:
                cursor.execute("SELECT * FROM users WHERE LOWER(username) = LOWER(?)", (clean_id,))
            return cursor.fetchone()

    def update_user_storage_and_counts(self, owner_id: int):
        """Recalculates actual user storage used and category counters from active files."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT 
                    COUNT(*) as total_cnt,
                    COALESCE(SUM(size_bytes), 0) as total_size,
                    COALESCE(SUM(CASE WHEN category = 'Photos' THEN 1 ELSE 0 END), 0) as photo_cnt,
                    COALESCE(SUM(CASE WHEN category = 'Videos' THEN 1 ELSE 0 END), 0) as video_cnt,
                    COALESCE(SUM(CASE WHEN category = 'Documents' THEN 1 ELSE 0 END), 0) as doc_cnt,
                    COALESCE(SUM(CASE WHEN category = 'Audio' THEN 1 ELSE 0 END), 0) as audio_cnt,
                    COALESCE(SUM(CASE WHEN category = 'Source Code' THEN 1 ELSE 0 END), 0) as code_cnt,
                    COALESCE(SUM(CASE WHEN category NOT IN ('Photos', 'Videos', 'Documents', 'Audio', 'Source Code') THEN 1 ELSE 0 END), 0) as other_cnt
                FROM files
                WHERE owner_id = ? AND trash = 0
            """, (owner_id,))
            stats = cursor.fetchone()
            cursor.execute("""
                UPDATE users SET
                    storage_used = ?,
                    file_count = ?,
                    photo_count = ?,
                    video_count = ?,
                    document_count = ?,
                    audio_count = ?,
                    code_count = ?,
                    other_count = ?
                WHERE user_id = ?
            """, (
                stats["total_size"], stats["total_cnt"], stats["photo_cnt"],
                stats["video_cnt"], stats["doc_cnt"], stats["audio_cnt"],
                stats["code_cnt"], stats["other_cnt"], owner_id
            ))

    def set_user_ban(self, user_id: int, banned: bool):
        with self.get_connection() as conn:
            conn.execute("UPDATE users SET banned = ? WHERE user_id = ?", (1 if banned else 0, user_id))

    def set_user_storage_limit(self, user_id: int, new_limit: int):
        with self.get_connection() as conn:
            conn.execute("UPDATE users SET storage_limit = ? WHERE user_id = ?", (new_limit, user_id))

    # ---------------- FILE OPERATIONS ----------------
    def save_file(self, owner_id: int, telegram_file_id: str, file_unique_id: str,
                  backend_message_id: int, filename: str, extension: str, mime_type: str,
                  file_type: str, size_bytes: int, category: str, caption: Optional[str] = None) -> int:
        now = datetime.utcnow().isoformat()
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO files (
                    owner_id, telegram_file_id, file_unique_id, backend_message_id,
                    filename, extension, mime_type, file_type, size_bytes, category,
                    favorite, trash, created_at, updated_at, caption
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?, ?)
            """, (owner_id, telegram_file_id, file_unique_id, backend_message_id,
                  filename, extension, mime_type, file_type, size_bytes, category, now, now, caption))
            file_id = cursor.lastrowid
        self.update_user_storage_and_counts(owner_id)
        return file_id

    def get_file(self, file_id: int, owner_id: Optional[int] = None) -> Optional[sqlite3.Row]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            if owner_id is not None:
                cursor.execute("SELECT * FROM files WHERE id = ? AND owner_id = ?", (file_id, owner_id))
            else:
                cursor.execute("SELECT * FROM files WHERE id = ?", (file_id,))
            return cursor.fetchone()

    def get_file_by_unique_id(self, owner_id: int, file_unique_id: str) -> Optional[sqlite3.Row]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM files WHERE owner_id = ? AND file_unique_id = ? AND trash = 0",
                           (owner_id, file_unique_id))
            return cursor.fetchone()

    def list_user_files(self, owner_id: int, category: Optional[str] = None, favorite: bool = False,
                        trash: bool = False, query: Optional[str] = None, limit: int = 5, offset: int = 0) -> List[sqlite3.Row]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            sql = "SELECT * FROM files WHERE owner_id = ? AND trash = ?"
            params: List[Any] = [owner_id, 1 if trash else 0]

            if favorite:
                sql += " AND favorite = 1"
            if category:
                if category == "Recent":
                    pass # Sorted by created_at DESC
                else:
                    sql += " AND category = ?"
                    params.append(category)
            if query:
                sql += " AND (LOWER(filename) LIKE LOWER(?) OR LOWER(extension) LIKE LOWER(?))"
                q_param = f"%{query.strip()}%"
                params.extend([q_param, q_param])

            sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])

            cursor.execute(sql, params)
            return cursor.fetchall()

    def count_user_files(self, owner_id: int, category: Optional[str] = None, favorite: bool = False,
                         trash: bool = False, query: Optional[str] = None) -> int:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            sql = "SELECT COUNT(*) as cnt FROM files WHERE owner_id = ? AND trash = ?"
            params: List[Any] = [owner_id, 1 if trash else 0]

            if favorite:
                sql += " AND favorite = 1"
            if category and category != "Recent":
                sql += " AND category = ?"
                params.append(category)
            if query:
                sql += " AND (LOWER(filename) LIKE LOWER(?) OR LOWER(extension) LIKE LOWER(?))"
                q_param = f"%{query.strip()}%"
                params.extend([q_param, q_param])

            cursor.execute(sql, params)
            return cursor.fetchone()["cnt"]

    def rename_file(self, file_id: int, owner_id: int, new_filename: str) -> bool:
        now = datetime.utcnow().isoformat()
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE files SET filename = ?, updated_at = ? WHERE id = ? AND owner_id = ?",
                           (new_filename, now, file_id, owner_id))
            return cursor.rowcount > 0

    def toggle_favorite(self, file_id: int, owner_id: int) -> bool:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE files SET favorite = CASE WHEN favorite = 1 THEN 0 ELSE 1 END WHERE id = ? AND owner_id = ?",
                           (file_id, owner_id))
            return cursor.rowcount > 0

    def move_to_trash(self, file_id: int, owner_id: int) -> bool:
        now = datetime.utcnow().isoformat()
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE files SET trash = 1, updated_at = ? WHERE id = ? AND owner_id = ?",
                           (now, file_id, owner_id))
            res = cursor.rowcount > 0
        self.update_user_storage_and_counts(owner_id)
        return res

    def restore_from_trash(self, file_id: int, owner_id: int) -> Tuple[bool, str]:
        # Check available storage space first
        file_rec = self.get_file(file_id, owner_id)
        if not file_rec:
            return False, "File not found."
        user = self.get_user(owner_id)
        if not user:
            return False, "User account error."

        if user["storage_used"] + file_rec["size_bytes"] > user["storage_limit"]:
            return False, "Insufficient available storage to restore file."

        now = datetime.utcnow().isoformat()
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE files SET trash = 0, updated_at = ? WHERE id = ? AND owner_id = ?",
                           (now, file_id, owner_id))
        self.update_user_storage_and_counts(owner_id)
        return True, "File restored successfully."

    def permanently_delete_file(self, file_id: int, owner_id: int) -> bool:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM share_links WHERE file_id = ?", (file_id,))
            cursor.execute("DELETE FROM files WHERE id = ? AND owner_id = ?", (file_id, owner_id))
            res = cursor.rowcount > 0
        self.update_user_storage_and_counts(owner_id)
        return res

    def empty_user_trash(self, owner_id: int) -> int:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM files WHERE owner_id = ? AND trash = 1", (owner_id,))
            trash_ids = [r["id"] for r in cursor.fetchall()]
            if trash_ids:
                placeholders = ",".join("?" for _ in trash_ids)
                cursor.execute(f"DELETE FROM share_links WHERE file_id IN ({placeholders})", trash_ids)
                cursor.execute("DELETE FROM files WHERE owner_id = ? AND trash = 1", (owner_id,))
                deleted_cnt = cursor.rowcount
            else:
                deleted_cnt = 0
        self.update_user_storage_and_counts(owner_id)
        return deleted_cnt

    # ---------------- SHARE LINKS ----------------
    def create_share_link(self, file_id: int, owner_id: int, expire_hours: Optional[int] = None) -> str:
        token = secrets.token_urlsafe(12)
        created_at = datetime.utcnow()
        expires_at = (created_at + timedelta(hours=expire_hours)).isoformat() if expire_hours else None

        with self.get_connection() as conn:
            conn.execute("""
                INSERT INTO share_links (token, file_id, owner_id, expires_at, enabled, created_at)
                VALUES (?, ?, ?, ?, 1, ?)
            """, (token, file_id, owner_id, expires_at, created_at.isoformat()))
        return token

    def resolve_share_link(self, token: str) -> Tuple[Optional[sqlite3.Row], str]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM share_links WHERE token = ? AND enabled = 1", (token,))
            share = cursor.fetchone()
            if not share:
                return None, "Invalid or disabled share link."

            if share["expires_at"]:
                exp_dt = datetime.fromisoformat(share["expires_at"])
                if datetime.utcnow() > exp_dt:
                    return None, "This share link has expired."

            cursor.execute("SELECT * FROM files WHERE id = ?", (share["file_id"],))
            file_rec = cursor.fetchone()
            if not file_rec or file_rec["trash"] == 1:
                return None, "The shared file is no longer available."

            return file_rec, "Success"

    # ---------------- SETTINGS & FORCE JOIN ----------------
    def get_setting(self, key: str, default: str = "") -> str:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
            row = cursor.fetchone()
            return row["value"] if row else default

    def set_setting(self, key: str, value: str):
        with self.get_connection() as conn:
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))

    def list_force_join_channels(self) -> List[sqlite3.Row]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM force_join_channels ORDER BY id ASC")
            return cursor.fetchall()

    def add_force_join_channel(self, channel_id: str, title: str, invite_link: str) -> bool:
        now = datetime.utcnow().isoformat()
        try:
            with self.get_connection() as conn:
                conn.execute("""
                    INSERT INTO force_join_channels (channel_id, title, invite_link, created_at)
                    VALUES (?, ?, ?, ?)
                """, (channel_id, title, invite_link, now))
            return True
        except sqlite3.IntegrityError:
            return False

    def remove_force_join_channel(self, channel_id: str) -> bool:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM force_join_channels WHERE channel_id = ?", (channel_id,))
            return cursor.rowcount > 0

    # ---------------- ADMIN & STATS ----------------
    def get_global_stats(self) -> Dict[str, Any]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) as total_users, COALESCE(SUM(storage_limit), 0) as total_quota, COALESCE(SUM(storage_used), 0) as total_used FROM users")
            u_stats = cursor.fetchone()
            cursor.execute("SELECT COUNT(*) as total_files FROM files WHERE trash = 0")
            f_stats = cursor.fetchone()
            cursor.execute("SELECT COUNT(*) as premium_cnt FROM users WHERE premium = 1")
            p_stats = cursor.fetchone()
            cursor.execute("SELECT COUNT(*) as active_cnt FROM users WHERE strftime('%Y-%m-%d', last_active) = strftime('%Y-%m-%d', 'now')")
            a_stats = cursor.fetchone()

            return {
                "total_users": u_stats["total_users"],
                "total_quota": u_stats["total_quota"],
                "total_used": u_stats["total_used"],
                "total_files": f_stats["total_files"],
                "premium_users": p_stats["premium_cnt"],
                "active_users_today": a_stats["active_cnt"]
            }

    def get_all_user_ids(self) -> List[int]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT user_id FROM users WHERE banned = 0")
            return [r["user_id"] for r in cursor.fetchall()]

    def list_banned_users(self) -> List[sqlite3.Row]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM users WHERE banned = 1 ORDER BY user_id DESC")
            return cursor.fetchall()

    def cleanup_orphaned_records(self) -> Dict[str, int]:
        now = datetime.utcnow().isoformat()
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM share_links WHERE expires_at IS NOT NULL AND expires_at < ?", (now,))
            expired_shares = cursor.rowcount
            return {"expired_shares_deleted": expired_shares}

db = DatabaseManager(DATABASE_PATH)

# ==============================================================================
# 5. FILE CLASSIFIER
# ==============================================================================

EXTENSION_CATEGORY_MAP = {
    # Photos
    "jpg": "Photos", "jpeg": "Photos", "png": "Photos", "webp": "Photos", "gif": "Photos", "bmp": "Photos", "svg": "Photos",
    # Videos
    "mp4": "Videos", "mkv": "Videos", "avi": "Videos", "mov": "Videos", "webm": "Videos", "flv": "Videos", "wmv": "Videos",
    # Audio
    "mp3": "Audio", "flac": "Audio", "wav": "Audio", "aac": "Audio", "m4a": "Audio", "ogg": "Audio", "opus": "Audio",
    # Documents
    "pdf": "Documents", "doc": "Documents", "docx": "Documents", "xls": "Documents", "xlsx": "Documents",
    "ppt": "Documents", "pptx": "Documents", "txt": "Documents", "epub": "Documents", "csv": "Documents",
    # Source Code
    "py": "Source Code", "js": "Source Code", "ts": "Source Code", "html": "Source Code", "css": "Source Code",
    "cpp": "Source Code", "c": "Source Code", "h": "Source Code", "java": "Source Code", "go": "Source Code",
    "rs": "Source Code", "kt": "Source Code", "swift": "Source Code", "php": "Source Code", "sql": "Source Code",
    "json": "Source Code", "xml": "Source Code", "yaml": "Source Code", "yml": "Source Code", "sh": "Source Code",
    "bash": "Source Code", "rb": "Source Code", "cs": "Source Code", "dart": "Source Code",
    # Archives
    "zip": "Archives", "rar": "Archives", "7z": "Archives", "tar": "Archives", "gz": "Archives", "bz2": "Archives", "xz": "Archives",
    # APK
    "apk": "APK"
}

def classify_file(filename: str, mime_type: Optional[str] = None, telegram_type: str = "document") -> Tuple[str, str]:
    """Determines logical category and clean extension for a file."""
    ext = "bin"
    if "." in filename:
        ext = filename.rsplit(".", 1)[-1].lower()

    if telegram_type == "photo":
        return "Photos", ext if ext != "bin" else "jpg"
    elif telegram_type == "video":
        return "Videos", ext if ext != "bin" else "mp4"
    elif telegram_type in ("audio", "voice"):
        return "Audio", ext if ext != "bin" else "mp3"

    category = EXTENSION_CATEGORY_MAP.get(ext)
    if not category:
        if mime_type:
            if mime_type.startswith("image/"):
                category = "Photos"
            elif mime_type.startswith("video/"):
                category = "Videos"
            elif mime_type.startswith("audio/"):
                category = "Audio"
            elif mime_type.startswith("text/"):
                category = "Source Code" if ext in EXTENSION_CATEGORY_MAP else "Documents"

    return category or "Other", ext

# ==============================================================================
# 6. UI KEYBOARD BUILDERS
# ==============================================================================

def main_menu_keyboard(is_admin: bool = False) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton("📤 Upload File", callback_data="ui_upload"),
         InlineKeyboardButton("📁 My Files", callback_data="ui_files:all:1")],
        [InlineKeyboardButton("💾 Storage", callback_data="ui_storage"),
         InlineKeyboardButton("🔍 Search Files", callback_data="ui_search_prompt")],
        [InlineKeyboardButton("🗂 Categories", callback_data="ui_categories"),
         InlineKeyboardButton("⭐ Favorites", callback_data="ui_files:fav:1")],
        [InlineKeyboardButton("🗑 Trash", callback_data="ui_trash:1"),
         InlineKeyboardButton("👤 My Account", callback_data="ui_account")],
        [InlineKeyboardButton("💎 Upgrade Storage", callback_data="ui_upgrade"),
         InlineKeyboardButton("⚙️ Settings", callback_data="ui_settings")],
        [InlineKeyboardButton("❓ Help", callback_data="ui_help"),
         InlineKeyboardButton("📊 Cloud Dashboard", callback_data="ui_dashboard")]
    ]
    if is_admin:
        buttons.append([InlineKeyboardButton("👑 Admin Panel", callback_data="admin_dashboard")])
    return InlineKeyboardMarkup(buttons)

def categories_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton("📷 Photos", callback_data="ui_files:Photos:1"),
         InlineKeyboardButton("🎬 Videos", callback_data="ui_files:Videos:1")],
        [InlineKeyboardButton("📄 Documents", callback_data="ui_files:Documents:1"),
         InlineKeyboardButton("💻 Source Code", callback_data="ui_files:Source Code:1")],
        [InlineKeyboardButton("🎵 Audio", callback_data="ui_files:Audio:1"),
         InlineKeyboardButton("📦 Archives", callback_data="ui_files:Archives:1")],
        [InlineKeyboardButton("📱 APK", callback_data="ui_files:APK:1"),
         InlineKeyboardButton("📦 Other", callback_data="ui_files:Other:1")],
        [InlineKeyboardButton("🕐 Recent Files", callback_data="ui_files:Recent:1"),
         InlineKeyboardButton("⭐ Favorites", callback_data="ui_files:fav:1")],
        [InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="ui_home")]
    ]
    return InlineKeyboardMarkup(buttons)

def pagination_keyboard(current_page: int, total_pages: int, category: str, files: List[sqlite3.Row]) -> InlineKeyboardMarkup:
    buttons = []
    # File listing buttons
    for f in files:
        fname = f["filename"]
        if len(fname) > 28:
            fname = fname[:25] + "..."
        icon = "⭐ " if f["favorite"] else "📄 "
        buttons.append([InlineKeyboardButton(f"{icon}{fname} ({format_bytes(f['size_bytes'])})", callback_data=f"file_view:{f['id']}")])

    # Navigation bar
    nav_row = []
    if current_page > 1:
        nav_row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"ui_files:{category}:{current_page - 1}"))
    nav_row.append(InlineKeyboardButton(f"Page {current_page}/{max(1, total_pages)}", callback_data="ui_ignore"))
    if current_page < total_pages:
        nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"ui_files:{category}:{current_page + 1}"))

    if nav_row:
        buttons.append(nav_row)

    buttons.append([InlineKeyboardButton("🗂 Categories", callback_data="ui_categories"),
                    InlineKeyboardButton("⬅️ Main Menu", callback_data="ui_home")])
    return InlineKeyboardMarkup(buttons)

def file_details_keyboard(file_id: int, is_favorite: bool, is_trash: bool) -> InlineKeyboardMarkup:
    if is_trash:
        buttons = [
            [InlineKeyboardButton("♻️ Restore File", callback_data=f"file_restore:{file_id}"),
             InlineKeyboardButton("❌ Permanently Delete", callback_data=f"file_perm_del:{file_id}")],
            [InlineKeyboardButton("⬅️ Back to Trash", callback_data="ui_trash:1")]
        ]
    else:
        fav_label = "⭐ Unfavorite" if is_favorite else "⭐ Favorite"
        buttons = [
            [InlineKeyboardButton("⬇️ Download", callback_data=f"file_dl:{file_id}"),
             InlineKeyboardButton("📤 Share Link", callback_data=f"file_share:{file_id}")],
            [InlineKeyboardButton("✏️ Rename", callback_data=f"file_rename_prompt:{file_id}"),
             InlineKeyboardButton(fav_label, callback_data=f"file_fav_toggle:{file_id}")],
            [InlineKeyboardButton("🗑 Move to Trash", callback_data=f"file_trash_confirm:{file_id}")],
            [InlineKeyboardButton("📁 My Files", callback_data="ui_files:all:1"),
             InlineKeyboardButton("⬅️ Main Menu", callback_data="ui_home")]
        ]
    return InlineKeyboardMarkup(buttons)

def share_expiry_keyboard(file_id: int) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton("⏳ 1 Hour", callback_data=f"gen_share:{file_id}:1"),
         InlineKeyboardButton("⏳ 1 Day", callback_data=f"gen_share:{file_id}:24")],
        [InlineKeyboardButton("⏳ 7 Days", callback_data=f"gen_share:{file_id}:168"),
         InlineKeyboardButton("♾️ Never Expires", callback_data=f"gen_share:{file_id}:0")],
        [InlineKeyboardButton("⬅️ Back to File", callback_data=f"file_view:{file_id}")]
    ]
    return InlineKeyboardMarkup(buttons)

def admin_menu_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton("📊 Dashboard", callback_data="admin_dashboard"),
         InlineKeyboardButton("👥 Manage User", callback_data="admin_user_prompt")],
        [InlineKeyboardButton("💾 Server Storage", callback_data="admin_storage"),
         InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast_prompt")],
        [InlineKeyboardButton("🔐 Force Join", callback_data="admin_force_join"),
         InlineKeyboardButton("💎 Manage Plans", callback_data="admin_plans")],
        [InlineKeyboardButton("📈 Analytics", callback_data="admin_analytics"),
         InlineKeyboardButton("🚫 Banned Users", callback_data="admin_banned")],
        [InlineKeyboardButton("🛠 Maintenance Mode", callback_data="admin_toggle_maint"),
         InlineKeyboardButton("💾 Backup DB", callback_data="admin_db_backup")],
        [InlineKeyboardButton("🧹 DB Cleanup", callback_data="admin_db_cleanup")],
        [InlineKeyboardButton("⬅️ Back to Cloud Menu", callback_data="ui_home")]
    ]
    return InlineKeyboardMarkup(buttons)

# ==============================================================================
# 7. FORCE JOIN HELPER
# ==============================================================================

async def check_user_force_join(user_id: int, bot: Any) -> Tuple[bool, List[sqlite3.Row]]:
    """Checks whether user has joined all active force join channels."""
    if db.get_setting("force_join_enabled", "false") != "true":
        return True, []

    channels = db.list_force_join_channels()
    if not channels:
        return True, []

    missing_channels = []
    for ch in channels:
        try:
            member = await bot.get_chat_member(chat_id=ch["channel_id"], user_id=user_id)
            if member.status not in (ChatMember.MEMBER, ChatMember.ADMINISTRATOR, ChatMember.OWNER):
                missing_channels.append(ch)
        except Exception as e:
            logger.warning(f"Could not check membership for channel {ch['channel_id']}: {e}")
            # If bot lacks permission or channel invalid, do not block user
            pass

    return (len(missing_channels) == 0), missing_channels

def force_join_keyboard(missing_channels: List[sqlite3.Row]) -> InlineKeyboardMarkup:
    buttons = []
    for idx, ch in enumerate(missing_channels, 1):
        url = ch["invite_link"] if ch["invite_link"] else f"https://t.me/{ch['channel_id'].lstrip('@')}"
        buttons.append([InlineKeyboardButton(f"📢 JOIN CHANNEL {idx}: {ch['title']}", url=url)])
    buttons.append([InlineKeyboardButton("✅ VERIFY MEMBERSHIP", callback_data="verify_force_join")])
    return InlineKeyboardMarkup(buttons)

# ==============================================================================
# 8. HANDLERS — START, MENU & NAVIGATION
# ==============================================================================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return

    db_user = db.get_or_create_user(user.id, user.username, user.first_name)

    if db_user["banned"] == 1:
        await update.message.reply_text("🚫 **ACCOUNT SUSPENDED**\n\nYour VINX CLOUD account has been suspended.\nPlease contact support if you believe this is an error.")
        return

    # Check deep-link parameters (e.g. share_token)
    if context.args and len(context.args) > 0:
        arg = context.args[0]
        if arg.startswith("share_"):
            token = arg.replace("share_", "").strip()
            await handle_deep_link_share(update, context, token)
            return

    # Force Join Check
    joined, missing = await check_user_force_join(user.id, context.bot)
    if not joined:
        msg_text = (
            f"📢 **{format_title('JOIN REQUIRED')}**\n\n"
            "To access **VINX CLOUD**, please join our required update channels below.\n"
            "After joining, click **VERIFY MEMBERSHIP** to activate your **1 TB Free Storage**!"
        )
        await update.message.reply_text(msg_text, reply_markup=force_join_keyboard(missing), parse_mode="Markdown")
        return

    # Welcome Screen
    welcome_text = (
        f"☁️ **{format_title('WELCOME TO VINX CLOUD')}**\n\n"
        f"Hello, **{user.first_name}**!\n"
        "Your private Google Drive-style cloud storage inside Telegram.\n\n"
        "🎁 **1 TB FREE CLOUD STORAGE ACTIVATED**\n\n"
        "⚡ **Supported Files:**\n"
        "📷 Photos • 🎬 Videos • 📄 Documents • 💻 Source Code\n"
        "📦 Archives • 🎵 Audio • 📱 APKs & More\n\n"
        "🔒 *Fast. Private. Cryptographically Secure.*"
    )
    is_admin = user.id in ADMIN_IDS
    await update.message.reply_text(welcome_text, reply_markup=main_menu_keyboard(is_admin), parse_mode="Markdown")

async def handle_deep_link_share(update: Update, context: ContextTypes.DEFAULT_TYPE, token: str):
    file_rec, status_msg = db.resolve_share_link(token)
    if not file_rec:
        await update.message.reply_text(f"❌ **SHARE LINK ERROR**\n\n{status_msg}", parse_mode="Markdown")
        return

    await update.message.reply_text(
        f"🔗 **SHARED FILE RECEIVED**\n\n"
        f"📄 **Filename:** `{file_rec['filename']}`\n"
        f"📦 **Size:** `{format_bytes(file_rec['size_bytes'])}`\n"
        f"☁️ **Server:** VINX CLOUD SERVER\n\n"
        f"Retrieving stored file from cloud...",
        parse_mode="Markdown"
    )

    try:
        if BACKEND_CHANNEL_ID:
            await context.bot.copy_message(
                chat_id=update.effective_chat.id,
                from_chat_id=BACKEND_CHANNEL_ID,
                message_id=file_rec["backend_message_id"]
            )
        else:
            await context.bot.send_document(
                chat_id=update.effective_chat.id,
                document=file_rec["telegram_file_id"],
                filename=file_rec["filename"]
            )
    except Exception as e:
        logger.error(f"Error serving shared file {token}: {e}")
        await update.message.reply_text("❌ Failed to retrieve the file from cloud storage backend.")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        f"☁️ **{format_title('VINX CLOUD HELP & GUIDE')}**\n\n"
        "🔹 **How to Upload Files:**\n"
        "Simply send any photo, video, document, code file, or archive directly to this bot!\n\n"
        "🔹 **Storage Quota:**\n"
        "Every user starts with a free **1 TB** logical storage quota. Manage files using the **My Files** menu.\n\n"
        "🔹 **File Sharing:**\n"
        "Select any file, click **Share Link**, and choose an expiration timer to generate a secure deep link.\n\n"
        "🔹 **Trash & Recovery:**\n"
        "Deleted files move to **Trash** first and can be restored anytime provided available quota exists.\n\n"
        "📩 **Need Admin Support?**\n"
        f"Contact Support: @{SUPPORT_USERNAME}"
    )
    await update.message.reply_text(help_text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="ui_home")]]), parse_mode="Markdown")

# ==============================================================================
# 9. FILE UPLOAD & DUPLICATE HANDLING
# ==============================================================================

async def handle_incoming_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return

    # Rate limiting
    if rate_limiter.is_rate_limited(user.id, "upload", 1.5):
        await msg.reply_text("⚠️ Please wait a moment between uploads.")
        return

    db_user = db.get_or_create_user(user.id, user.username, user.first_name)
    if db_user["banned"] == 1:
        await msg.reply_text("🚫 Your account is suspended.")
        return

    if db.get_setting("maintenance_mode", "false") == "true" and user.id not in ADMIN_IDS:
        await msg.reply_text("🛠 **VINX CLOUD MAINTENANCE**\n\nThe cloud is currently undergoing maintenance. Please try again later.", parse_mode="Markdown")
        return

    # Extract Telegram File attributes
    telegram_file_id = ""
    file_unique_id = ""
    size_bytes = 0
    filename = "unnamed_file"
    mime_type = ""
    telegram_type = "document"
    caption = msg.caption

    if msg.document:
        doc = msg.document
        telegram_file_id = doc.file_id
        file_unique_id = doc.file_unique_id
        size_bytes = doc.file_size or 0
        filename = doc.file_name or "document"
        mime_type = doc.mime_type or ""
        telegram_type = "document"
    elif msg.photo:
        photo = msg.photo[-1]
        telegram_file_id = photo.file_id
        file_unique_id = photo.file_unique_id
        size_bytes = photo.file_size or 0
        filename = f"photo_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
        mime_type = "image/jpeg"
        telegram_type = "photo"
    elif msg.video:
        vid = msg.video
        telegram_file_id = vid.file_id
        file_unique_id = vid.file_unique_id
        size_bytes = vid.file_size or 0
        filename = vid.file_name or f"video_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp4"
        mime_type = vid.mime_type or "video/mp4"
        telegram_type = "video"
    elif msg.audio:
        aud = msg.audio
        telegram_file_id = aud.file_id
        file_unique_id = aud.file_unique_id
        size_bytes = aud.file_size or 0
        filename = aud.file_name or f"audio_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp3"
        mime_type = aud.mime_type or "audio/mpeg"
        telegram_type = "audio"
    elif msg.voice:
        v = msg.voice
        telegram_file_id = v.file_id
        file_unique_id = v.file_unique_id
        size_bytes = v.file_size or 0
        filename = f"voice_{datetime.now().strftime('%Y%m%d_%H%M%S')}.ogg"
        mime_type = "audio/ogg"
        telegram_type = "voice"
    else:
        return

    category, ext = classify_file(filename, mime_type, telegram_type)

    # File Size Limit Validation
    if size_bytes > MAX_FILE_SIZE:
        await msg.reply_text(f"❌ **FILE TOO LARGE**\n\nThe maximum allowed file size is `{format_bytes(MAX_FILE_SIZE)}`.", parse_mode="Markdown")
        return

    # Quota Check
    used = db_user["storage_used"]
    limit = db_user["storage_limit"]
    if used + size_bytes > limit:
        free_space = format_bytes(max(0, limit - used))
        await msg.reply_text(
            f"❌ **STORAGE LIMIT REACHED**\n\n"
            f"You do not have enough available cloud quota to store this file.\n\n"
            f"📦 **File Size:** `{format_bytes(size_bytes)}`\n"
            f"💾 **Available Free Space:** `{free_space}`\n\n"
            f"Click **Upgrade Storage** or delete existing files to make room.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💎 Upgrade Storage", callback_data="ui_upgrade")]]),
            parse_mode="Markdown"
        )
        return

    # Duplicate File Check
    existing_file = db.get_file_by_unique_id(user.id, file_unique_id)
    if existing_file:
        await msg.reply_text(
            f"⚠️ **DUPLICATE FILE DETECTED**\n\n"
            f"You have already uploaded this file as `{existing_file['filename']}`.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📂 View Existing File", callback_data=f"file_view:{existing_file['id']}")]
            ]),
            parse_mode="Markdown"
        )

    # Store File in Backend Storage Channel
    status_msg = await msg.reply_text("☁️ **Storing file in VINX CLOUD SERVER...**\n`░░░░░░░░░░░░░░░░░░░░`", parse_mode="Markdown")

    backend_msg_id = 0
    if BACKEND_CHANNEL_ID != 0:
        try:
            copied_msg = await context.bot.copy_message(
                chat_id=BACKEND_CHANNEL_ID,
                from_chat_id=msg.chat_id,
                message_id=msg.message_id
            )
            backend_msg_id = copied_msg.message_id
        except Exception as e:
            logger.error(f"Failed to copy file to backend channel {BACKEND_CHANNEL_ID}: {e}")
            await status_msg.edit_text("❌ **STORAGE ERROR**\n\nCould not transfer file to VINX CLOUD backend channel.")
            return
    else:
        # Fallback if channel ID not set
        backend_msg_id = msg.message_id

    # Save Metadata to Database
    file_id = db.save_file(
        owner_id=user.id,
        telegram_file_id=telegram_file_id,
        file_unique_id=file_unique_id,
        backend_message_id=backend_msg_id,
        filename=filename,
        extension=ext,
        mime_type=mime_type,
        file_type=telegram_type,
        size_bytes=size_bytes,
        category=category,
        caption=caption
    )

    # Refresh user storage stats
    updated_user = db.get_user(user.id)
    u_used = updated_user["storage_used"]
    u_limit = updated_user["storage_limit"]
    u_free = max(0, u_limit - u_used)
    progress_bar = generate_progress_bar(u_used, u_limit)

    upload_done_text = (
        f"✅ **{format_title('FILE UPLOADED SUCCESSFULLY')}**\n\n"
        f"📄 **Filename:** `{filename}`\n"
        f"📦 **Size:** `{format_bytes(size_bytes)}`\n"
        f"🗂 **Category:** `{category}`\n"
        f"☁️ **Server:** VINX CLOUD SERVER\n\n"
        f"💾 **Cloud Storage Usage:**\n"
        f"`{progress_bar}`\n"
        f"Used: **{format_bytes(u_used)}** | Free: **{format_bytes(u_free)}** | Total: **{format_bytes(u_limit)}**"
    )

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📄 View File Details", callback_data=f"file_view:{file_id}")],
        [InlineKeyboardButton("📁 My Files", callback_data="ui_files:all:1"),
         InlineKeyboardButton("⬅️ Main Menu", callback_data="ui_home")]
    ])

    await status_msg.edit_text(upload_done_text, reply_markup=kb, parse_mode="Markdown")

# ==============================================================================
# 10. CALLBACK QUERY ROUTER & INTERACTIVE MENUS
# ==============================================================================

async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    user = update.effective_user
    data = query.data
    await query.answer()

    # Ownership & Rate Limiting Check
    if rate_limiter.is_rate_limited(user.id, "callback", 0.3):
        return

    db_user = db.get_or_create_user(user.id, user.username, user.first_name)
    if db_user["banned"] == 1:
        await query.edit_message_text("🚫 **ACCOUNT SUSPENDED**\n\nYour account has been suspended.")
        return

    is_admin = user.id in ADMIN_IDS

    # --- HOME / MAIN MENU ---
    if data == "ui_home":
        welcome_text = (
            f"☁️ **{format_title('VINX CLOUD DASHBOARD')}**\n\n"
            f"Welcome back, **{user.first_name}**!\n"
            "Your files are securely encrypted and accessible anytime.\n\n"
            f"💾 **Storage Usage:** `{generate_progress_bar(db_user['storage_used'], db_user['storage_limit'])}`\n"
            f"Total Files: **{db_user['file_count']}**"
        )
        await query.edit_message_text(welcome_text, reply_markup=main_menu_keyboard(is_admin), parse_mode="Markdown")

    elif data == "ui_upload":
        await query.edit_message_text(
            f"📤 **{format_title('UPLOAD FILES TO VINX CLOUD')}**\n\n"
            "Simply send or forward any file directly into this chat!\n\n"
            "• Maximum single file size: **2 GB**\n"
            "• Supports All Formats (Documents, Code, Media, Archives)\n"
            "• Stored in **VINX CLOUD SERVER**",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="ui_home")]]),
            parse_mode="Markdown"
        )

    elif data == "ui_categories":
        await query.edit_message_text(
            f"🗂 **{format_title('FILE CATEGORIES')}**\n\nSelect a category to browse stored files:",
            reply_markup=categories_keyboard(),
            parse_mode="Markdown"
        )

    # --- FILE PAGINATION ---
    elif data.startswith("ui_files:"):
        parts = data.split(":")
        category = parts[1]
        page = int(parts[2]) if len(parts) > 2 else 1
        limit = 5
        offset = (page - 1) * limit

        fav_only = (category == "fav")
        cat_filter = None if category in ("all", "fav", "Recent") else category

        total_files = db.count_user_files(user.id, category=cat_filter, favorite=fav_only, trash=False)
        files = db.list_user_files(user.id, category=cat_filter, favorite=fav_only, trash=False, limit=limit, offset=offset)
        total_pages = math.ceil(total_files / limit) if total_files > 0 else 1

        title_label = "ALL FILES"
        if fav_only: title_label = "FAVORITE FILES"
        elif category == "Recent": title_label = "RECENT UPLOADS"
        elif cat_filter: title_label = f"{cat_filter.upper()} FILES"

        text = (
            f"📁 **{format_title(title_label)}**\n\n"
            f"Showing page **{page} / {total_pages}** (Total: {total_files} files)\n"
            f"Click on a file below to manage, download, or share:"
        )
        if total_files == 0:
            text += "\n\n_No files found in this category._"

        await query.edit_message_text(
            text,
            reply_markup=pagination_keyboard(page, total_pages, category, files),
            parse_mode="Markdown"
        )

    # --- FILE VIEW DETAILS ---
    elif data.startswith("file_view:"):
        file_id = int(data.split(":")[1])
        file_rec = db.get_file(file_id, owner_id=user.id)
        if not file_rec:
            await query.edit_message_text("❌ File not found or access denied.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="ui_home")]]))
            return

        fav_str = "Yes ⭐" if file_rec["favorite"] else "No"
        status_str = "🗑 In Trash" if file_rec["trash"] else "☁️ Stored in VINX CLOUD SERVER"
        upload_date = file_rec["created_at"].split("T")[0]

        details_text = (
            f"📄 **{format_title(file_rec['filename'])}**\n\n"
            f"📦 **Size:** `{format_bytes(file_rec['size_bytes'])}`\n"
            f"🗂 **Category:** `{file_rec['category']}`\n"
            f"🏷 **Extension:** `.{file_rec['extension']}`\n"
            f"📅 **Uploaded:** `{upload_date}`\n"
            f"⭐ **Favorite:** `{fav_str}`\n"
            f"🔒 **Status:** `{status_str}`"
        )
        await query.edit_message_text(
            details_text,
            reply_markup=file_details_keyboard(file_id, bool(file_rec["favorite"]), bool(file_rec["trash"])),
            parse_mode="Markdown"
        )

    # --- DOWNLOAD FILE ---
    elif data.startswith("file_dl:"):
        file_id = int(data.split(":")[1])
        file_rec = db.get_file(file_id, owner_id=user.id)
        if not file_rec:
            await query.answer("❌ File not found.")
            return

        await query.answer("⚡ Fetching file from VINX CLOUD SERVER...")
        try:
            if BACKEND_CHANNEL_ID:
                await context.bot.copy_message(
                    chat_id=query.message.chat_id,
                    from_chat_id=BACKEND_CHANNEL_ID,
                    message_id=file_rec["backend_message_id"]
                )
            else:
                await context.bot.send_document(
                    chat_id=query.message.chat_id,
                    document=file_rec["telegram_file_id"],
                    filename=file_rec["filename"]
                )
        except Exception as e:
            logger.error(f"Download error: {e}")
            await query.message.reply_text("❌ Failed to fetch file from server backend.")

    # --- FILE RENAME PROMPT ---
    elif data.startswith("file_rename_prompt:"):
        file_id = int(data.split(":")[1])
        context.user_data["awaiting_rename_file_id"] = file_id
        await query.edit_message_text(
            "✏️ **RENAME FILE**\n\nPlease reply with the new filename (include extension):",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data=f"file_view:{file_id}")]])
        )

    # --- TOGGLE FAVORITE ---
    elif data.startswith("file_fav_toggle:"):
        file_id = int(data.split(":")[1])
        db.toggle_favorite(file_id, owner_id=user.id)
        await query.answer("Updated Favorites status!")
        file_rec = db.get_file(file_id, owner_id=user.id)
        await query.edit_message_reply_markup(reply_markup=file_details_keyboard(file_id, bool(file_rec["favorite"]), bool(file_rec["trash"])))

    # --- TRASH MANAGEMENT ---
    elif data.startswith("file_trash_confirm:"):
        file_id = int(data.split(":")[1])
        db.move_to_trash(file_id, owner_id=user.id)
        await query.answer("Moved to Trash")
        await query.edit_message_text("🗑 **File moved to Trash.**", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🗑 View Trash", callback_data="ui_trash:1")]]))

    elif data.startswith("ui_trash:"):
        page = int(data.split(":")[1])
        limit = 5
        offset = (page - 1) * limit
        total_files = db.count_user_files(user.id, trash=True)
        files = db.list_user_files(user.id, trash=True, limit=limit, offset=offset)
        total_pages = math.ceil(total_files / limit) if total_files > 0 else 1

        text = (
            f"🗑 **{format_title('TRASH CAN')}**\n\n"
            f"Showing page **{page} / {total_pages}** ({total_files} deleted files)\n"
            f"Files in trash do not consume active storage."
        )
        buttons = []
        for f in files:
            buttons.append([InlineKeyboardButton(f"📄 {f['filename']}", callback_data=f"file_view:{f['id']}")])
        if total_files > 0:
            buttons.append([InlineKeyboardButton("🧹 Empty Trash", callback_data="trash_empty_confirm")])

        buttons.append([InlineKeyboardButton("⬅️ Main Menu", callback_data="ui_home")])
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")

    elif data.startswith("file_restore:"):
        file_id = int(data.split(":")[1])
        success, msg_text = db.restore_from_trash(file_id, owner_id=user.id)
        if success:
            await query.answer("Restored successfully!")
            await query.edit_message_text(f"✅ **{msg_text}**", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📄 View File", callback_data=f"file_view:{file_id}")]]) )
        else:
            await query.answer(f"❌ {msg_text}", show_alert=True)

    elif data.startswith("file_perm_del:"):
        file_id = int(data.split(":")[1])
        db.permanently_delete_file(file_id, owner_id=user.id)
        await query.answer("Permanently deleted!")
        await query.edit_message_text("❌ **File permanently deleted.**", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🗑 Back to Trash", callback_data="ui_trash:1")]]))

    elif data == "trash_empty_confirm":
        cnt = db.empty_user_trash(user.id)
        await query.answer(f"Emptied {cnt} files from Trash!")
        await query.edit_message_text("🧹 **Trash emptied successfully.**", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Main Menu", callback_data="ui_home")]]))

    # --- SHARE SYSTEM ---
    elif data.startswith("file_share:"):
        file_id = int(data.split(":")[1])
        await query.edit_message_text(
            f"🔗 **{format_title('GENERATE FILE SHARE LINK')}**\n\n"
            "Select an expiration duration for this deep link:",
            reply_markup=share_expiry_keyboard(file_id),
            parse_mode="Markdown"
        )

    elif data.startswith("gen_share:"):
        parts = data.split(":")
        file_id = int(parts[1])
        hours = int(parts[2])
        token = db.create_share_link(file_id, owner_id=user.id, expire_hours=hours if hours > 0 else None)
        bot_info = await context.bot.get_me()
        share_url = f"https://t.me/{bot_info.username}?start=share_{token}"

        exp_str = f"{hours} Hours" if hours > 0 else "Never (Permanent)"
        await query.edit_message_text(
            f"🔗 **{format_title('SECURE SHARE LINK CREATED')}**\n\n"
            f"Link: `{share_url}`\n\n"
            f"⏳ **Expires:** `{exp_str}`\n"
            f"Anyone with this link can safely download a copy through VINX CLOUD SERVER.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📄 Back to File", callback_data=f"file_view:{file_id}")],
                [InlineKeyboardButton("⬅️ Main Menu", callback_data="ui_home")]
            ]),
            parse_mode="Markdown"
        )

    # --- STORAGE & DASHBOARD ---
    elif data in ("ui_storage", "ui_dashboard"):
        u_used = db_user["storage_used"]
        u_limit = db_user["storage_limit"]
        u_free = max(0, u_limit - u_used)
        prog_bar = generate_progress_bar(u_used, u_limit, length=18)

        text = (
            f"☁️ **{format_title('VINX CLOUD STORAGE DASHBOARD')}**\n\n"
            f"`{prog_bar}`\n\n"
            f"💾 **Used Storage:** `{format_bytes(u_used)}`\n"
            f"🆓 **Free Storage:** `{format_bytes(u_free)}`\n"
            f"📊 **Total Quota:** `{format_bytes(u_limit)}` \n\n"
            f"📁 **Total Files:** `{db_user['file_count']}`\n\n"
            f"**Category Breakdown:**\n"
            f"📷 Photos: `{db_user['photo_count']}`\n"
            f"🎬 Videos: `{db_user['video_count']}`\n"
            f"📄 Documents: `{db_user['document_count']}`\n"
            f"🎵 Audio: `{db_user['audio_count']}`\n"
            f"💻 Source Code: `{db_user['code_count']}`\n"
            f"📦 Other: `{db_user['other_count']}`"
        )
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💎 Upgrade Quota", callback_data="ui_upgrade")],
                [InlineKeyboardButton("⬅️ Back to Cloud Menu", callback_data="ui_home")]
            ]),
            parse_mode="Markdown"
        )

    # --- ACCOUNT PAGE ---
    elif data == "ui_account":
        reg_date = db_user["created_at"].split("T")[0]
        text = (
            f"👤 **{format_title('MY VINX CLOUD ACCOUNT')}**\n\n"
            f"👤 **Name:** `{user.first_name}`\n"
            f"🏷 **Username:** `@{user.username or 'N/A'}`\n"
            f"🆔 **Telegram ID:** `{user.id}`\n"
            f"📅 **Registered:** `{reg_date}`\n"
            f"💎 **Premium:** `{'Yes ⭐' if db_user['premium'] else 'No'}`\n"
            f"🔒 **Account Status:** `Active 🟢`\n\n"
            f"💾 **Logical Quota:** `{format_bytes(db_user['storage_limit'])}`\n"
            f"📦 **Used Storage:** `{format_bytes(db_user['storage_used'])}`"
        )
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="ui_home")]]), parse_mode="Markdown")

    # --- UPGRADE SYSTEM ---
    elif data == "ui_upgrade":
        plans = [
            "FREE — 1 TB — Free",
            "PRO — 2 TB — $4.99/mo",
            "ULTRA — 5 TB — $9.99/mo",
            "MAX — 10 TB — $19.99/mo"
        ]
        plans_str = "\n".join(f"• {p}" for p in plans)
        text = (
            f"💎 **{format_title('UPGRADE CLOUD STORAGE')}**\n\n"
            f"Current Quota: **{format_bytes(db_user['storage_limit'])}**\n\n"
            f"**Available Storage Plans:**\n{plans_str}\n\n"
            f"To upgrade your logical cloud quota, please contact our administrator directly."
        )
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("👨‍💻 CONTACT ADMIN", url=f"https://t.me/{SUPPORT_USERNAME}")],
                [InlineKeyboardButton("⬅️ Back", callback_data="ui_home")]
            ]),
            parse_mode="Markdown"
        )

    # --- SEARCH PROMPT ---
    elif data == "ui_search_prompt":
        context.user_data["awaiting_search"] = True
        await query.edit_message_text(
            "🔍 **SEARCH VINX CLOUD**\n\nPlease type your search query (filename or extension):",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="ui_home")]])
        )

    # --- VERIFY FORCE JOIN ---
    elif data == "verify_force_join":
        joined, missing = await check_user_force_join(user.id, context.bot)
        if joined:
            await query.answer("✅ Verification successful!", show_alert=True)
            welcome_text = (
                f"☁️ **{format_title('WELCOME TO VINX CLOUD')}**\n\n"
                f"Verification successful! Hello, **{user.first_name}**!\n"
                "Your **1 TB Free Storage** is active."
            )
            await query.edit_message_text(welcome_text, reply_markup=main_menu_keyboard(is_admin), parse_mode="Markdown")
        else:
            await query.answer("❌ You haven't joined all channels yet!", show_alert=True)

    # ================= ADMIN CALLBACK HANDLERS =================
    elif data == "admin_dashboard" and is_admin:
        stats = db.get_global_stats()
        serv_bar = generate_progress_bar(stats["total_used"], stats["total_quota"], length=16)
        text = (
            f"👑 **{format_title('VINX CLOUD ADMIN DASHBOARD')}**\n\n"
            f"👥 **Total Users:** `{stats['total_users']}`\n"
            f"🟢 **Active Users Today:** `{stats['active_users_today']}`\n"
            f"📁 **Total Files Stored:** `{stats['total_files']}`\n"
            f"💎 **Premium Users:** `{stats['premium_users']}`\n\n"
            f"☁️ **SERVER LOGICAL STORAGE USAGE:**\n"
            f"`{serv_bar}`\n"
            f"Total Quota Allocated: **{format_bytes(stats['total_quota'])}**\n"
            f"Total Storage Used: **{format_bytes(stats['total_used'])}**"
        )
        await query.edit_message_text(text, reply_markup=admin_menu_keyboard(), parse_mode="Markdown")

    elif data == "admin_user_prompt" and is_admin:
        context.user_data["awaiting_admin_user_id"] = True
        await query.edit_message_text(
            "👤 **ADMIN USER LOOKUP**\n\nPlease reply with Telegram User ID or Username:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="admin_dashboard")]])
        )

    elif data == "admin_broadcast_prompt" and is_admin:
        context.user_data["awaiting_broadcast_msg"] = True
        await query.edit_message_text(
            "📢 **ADMIN BROADCAST ENGINE**\n\nPlease send the broadcast message (Text, Photo, Video, or Document) you wish to send to all users:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="admin_dashboard")]])
        )

    elif data == "admin_force_join" and is_admin:
        status_str = "🟢 Enabled" if db.get_setting("force_join_enabled", "false") == "true" else "🔴 Disabled"
        channels = db.list_force_join_channels()
        ch_text = "\n".join(f"• `{c['channel_id']}` ({c['title']})" for c in channels) if channels else "None"

        text = (
            f"🔐 **{format_title('FORCE JOIN SYSTEM')}**\n\n"
            f"Status: **{status_str}**\n\n"
            f"**Configured Channels:**\n{ch_text}"
        )
        buttons = [
            [InlineKeyboardButton("🟢 Enable Force Join", callback_data="admin_fj_on"),
             InlineKeyboardButton("🔴 Disable Force Join", callback_data="admin_fj_off")],
            [InlineKeyboardButton("➕ Add Channel", callback_data="admin_fj_add_prompt"),
             InlineKeyboardButton("➖ Remove Channel", callback_data="admin_fj_rem_prompt")],
            [InlineKeyboardButton("⬅️ Admin Menu", callback_data="admin_dashboard")]
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")

    elif data in ("admin_fj_on", "admin_fj_off") and is_admin:
        val = "true" if data == "admin_fj_on" else "false"
        db.set_setting("force_join_enabled", val)
        await query.answer("Force Join status updated!")
        await query.edit_message_text("🔐 Force join updated.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="admin_force_join")]]))

    elif data == "admin_toggle_maint" and is_admin:
        curr = db.get_setting("maintenance_mode", "false")
        new_val = "false" if curr == "true" else "true"
        db.set_setting("maintenance_mode", new_val)
        await query.answer(f"Maintenance Mode set to {new_val.upper()}")
        await query.edit_message_text(f"🛠 Maintenance Mode: **{new_val.upper()}**", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Admin Dashboard", callback_data="admin_dashboard")]]), parse_mode="Markdown")

    elif data == "admin_db_backup" and is_admin:
        await query.answer("Generating database backup...")
        try:
            with open(DATABASE_PATH, "rb") as f:
                await context.bot.send_document(
                    chat_id=query.message.chat_id,
                    document=InputFile(f, filename=f"vinx_cloud_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"),
                    caption="💾 **VINX CLOUD SQLITE DATABASE BACKUP**"
                )
        except Exception as e:
            logger.error(f"DB Backup failed: {e}")
            await query.message.reply_text("❌ Failed to create DB backup.")

    elif data == "admin_db_cleanup" and is_admin:
        res = db.cleanup_orphaned_records()
        await query.answer(f"Cleaned {res['expired_shares_deleted']} expired shares.")
        await query.edit_message_text(f"🧹 **Database Cleanup Complete.**\nExpired shares deleted: {res['expired_shares_deleted']}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Admin Menu", callback_data="admin_dashboard")]]))

    # --- ADMIN USER MOD ACTIONS ---
    elif data.startswith("admin_add_quota:") and is_admin:
        parts = data.split(":")
        target_uid = int(parts[1])
        add_bytes = int(parts[2]) # e.g. 100GB or 1TB
        user_rec = db.get_user(target_uid)
        if user_rec:
            new_lim = user_rec["storage_limit"] + add_bytes
            db.set_user_storage_limit(target_uid, new_lim)
            await query.answer(f"Added storage! New limit: {format_bytes(new_lim)}")
            await render_admin_user_profile(query, target_uid)

    elif data.startswith("admin_toggle_ban:") and is_admin:
        target_uid = int(data.split(":")[1])
        user_rec = db.get_user(target_uid)
        if user_rec:
            new_ban = (user_rec["banned"] == 0)
            db.set_user_ban(target_uid, new_ban)
            await query.answer("Updated ban status!")
            await render_admin_user_profile(query, target_uid)

async def render_admin_user_profile(query: Any, target_uid: int):
    u = db.get_user(target_uid)
    if not u:
        await query.edit_message_text("❌ User not found.")
        return

    ban_str = "🚫 Banned" if u["banned"] else "Active 🟢"
    text = (
        f"👤 **{format_title('ADMIN USER PROFILE')}**\n\n"
        f"🆔 **User ID:** `{u['user_id']}`\n"
        f"👤 **Name:** `{u['first_name']}`\n"
        f"🏷 **Username:** `@{u['username'] or 'N/A'}`\n"
        f"🔒 **Status:** `{ban_str}`\n\n"
        f"💾 **Storage Limit:** `{format_bytes(u['storage_limit'])}`\n"
        f"📦 **Storage Used:** `{format_bytes(u['storage_used'])}`\n"
        f"📁 **File Count:** `{u['file_count']}`"
    )
    buttons = [
        [InlineKeyboardButton("➕ Add 100 GB", callback_data=f"admin_add_quota:{target_uid}:{100 * 1024 * 1024 * 1024}"),
         InlineKeyboardButton("➕ Add 1 TB", callback_data=f"admin_add_quota:{target_uid}:{1024 * 1024 * 1024 * 1024}")],
        [InlineKeyboardButton("🚫 Ban/Unban User", callback_data=f"admin_toggle_ban:{target_uid}")],
        [InlineKeyboardButton("⬅️ Back to Admin Dashboard", callback_data="admin_dashboard")]
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")

# ==============================================================================
# 11. TEXT & CONVERSATION HANDLERS
# ==============================================================================

async def handle_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user or not msg.text:
        return

    text = msg.text.strip()

    # 1. Awaiting Rename Filename
    if "awaiting_rename_file_id" in context.user_data:
        file_id = context.user_data.pop("awaiting_rename_file_id")
        clean_name = re.sub(r'[\\/*?:"<>|]', "", text) # sanitize filename
        if not clean_name:
            await msg.reply_text("❌ Invalid filename.")
            return

        db.rename_file(file_id, owner_id=user.id, new_filename=clean_name)
        await msg.reply_text(f"✅ Filename updated to `{clean_name}`.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📄 View File", callback_data=f"file_view:{file_id}")]]) , parse_mode="Markdown")
        return

    # 2. Awaiting Search Query
    if context.user_data.get("awaiting_search"):
        context.user_data["awaiting_search"] = False
        files = db.list_user_files(user.id, query=text, limit=10)
        total_cnt = db.count_user_files(user.id, query=text)

        search_res_text = (
            f"🔍 **{format_title('SEARCH RESULTS')}**\n\n"
            f"Query: `{text}` (Found: {total_cnt} files)"
        )
        buttons = []
        for f in files:
            buttons.append([InlineKeyboardButton(f"📄 {f['filename']} ({format_bytes(f['size_bytes'])})", callback_data=f"file_view:{f['id']}")])
        buttons.append([InlineKeyboardButton("⬅️ Main Menu", callback_data="ui_home")])

        await msg.reply_text(search_res_text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")
        return

    # 3. Awaiting Admin User Lookup
    if context.user_data.get("awaiting_admin_user_id") and user.id in ADMIN_IDS:
        context.user_data["awaiting_admin_user_id"] = False
        u = db.get_user_by_username_or_id(text)
        if not u:
            await msg.reply_text("❌ User not found in database.")
            return

        ban_str = "🚫 Banned" if u["banned"] else "Active 🟢"
        txt = (
            f"👤 **ADMIN USER PROFILE**\n\n"
            f"🆔 ID: `{u['user_id']}`\n"
            f"👤 Name: `{u['first_name']}`\n"
            f"🏷 Username: `@{u['username'] or 'N/A'}`\n"
            f"🔒 Status: `{ban_str}`\n\n"
            f"💾 Limit: `{format_bytes(u['storage_limit'])}` | Used: `{format_bytes(u['storage_used'])}`"
        )
        buttons = [
            [InlineKeyboardButton("➕ Add 100 GB", callback_data=f"admin_add_quota:{u['user_id']}:{100 * 1024 * 1024 * 1024}"),
             InlineKeyboardButton("➕ Add 1 TB", callback_data=f"admin_add_quota:{u['user_id']}:{1024 * 1024 * 1024 * 1024}")],
            [InlineKeyboardButton("🚫 Toggle Ban", callback_data=f"admin_toggle_ban:{u['user_id']}")],
            [InlineKeyboardButton("⬅️ Admin Menu", callback_data="admin_dashboard")]
        ]
        await msg.reply_text(txt, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")
        return

    # 4. Awaiting Admin Broadcast Message
    if context.user_data.get("awaiting_broadcast_msg") and user.id in ADMIN_IDS:
        context.user_data["awaiting_broadcast_msg"] = False
        all_uids = db.get_all_user_ids()
        status_m = await msg.reply_text(f"📢 Starting broadcast to **{len(all_uids)}** users...", parse_mode="Markdown")

        sent, failed, blocked = 0, 0, 0
        for uid in all_uids:
            try:
                await msg.copy(chat_id=uid)
                sent += 1
            except Forbidden:
                blocked += 1
            except Exception:
                failed += 1
            await asyncio.sleep(0.05) # Rate limit batching

        await status_m.edit_text(
            f"📢 **BROADCAST COMPLETE**\n\n"
            f"✅ **Sent:** `{sent}`\n"
            f"🚫 **Blocked:** `{blocked}`\n"
            f"❌ **Failed:** `{failed}`",
            parse_mode="Markdown"
        )
        return

# ==============================================================================
# 12. ERROR HANDLER
# ==============================================================================

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Exception while handling an update:", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "❌ **Something went wrong.**\nPlease try again later or contact support.",
                parse_mode="Markdown"
            )
        except Exception:
            pass

# ==============================================================================
# 13. STARTUP VALIDATION & MAIN APPLICATION
# ==============================================================================

def validate_environment():
    if not BOT_TOKEN:
        logger.critical("BOT_TOKEN environment variable missing! Exiting.")
        sys.exit(1)

    if not ADMIN_IDS:
        logger.warning("No ADMIN_IDS configured. Admin functions will be inaccessible.")

    if not BACKEND_CHANNEL_ID:
        logger.warning("BACKEND_CHANNEL_ID missing or 0. Files will not be backed up to Telegram server channel!")

def main():
    print(format_title("VINX CLOUD SERVER STARTING..."))
    validate_environment()

    # Initialize SQLite Database
    db.init_db()

    # Build Application
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Add Command Handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("cloud", start_command))
    app.add_handler(CommandHandler("files", start_command))
    app.add_handler(CommandHandler("storage", start_command))
    app.add_handler(CommandHandler("account", start_command))
    app.add_handler(CommandHandler("admin", start_command))

    # Add File Upload Handler (Document, Photo, Video, Audio, Voice)
    app.add_handler(MessageHandler(
        filters.Document.ALL | filters.PHOTO | filters.VIDEO | filters.AUDIO | filters.VOICE,
        handle_incoming_file
    ))

    # Add Callback Router Handler
    app.add_handler(CallbackQueryHandler(callback_router))

    # Add Text Handler for interactions
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_input))

    # Add Global Error Handler
    app.add_error_handler(error_handler)

    logger.info("☁️ VINX CLOUD started successfully.")
    print(format_title("VINX CLOUD ONLINE AND LISTENING"))
    app.run_polling()

if __name__ == "__main__":
    main()
