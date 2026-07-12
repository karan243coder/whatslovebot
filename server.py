# ============ MeetLink Advanced Backend (Super Advanced & High Speed) ============
# Receives events + video recordings, converts to MP4+MP3, sends to Telegram
# Also handles direct file sharing with preview, 1-Hour TTL auto-expiration, and non-blocking worker threads.

import os
import re
import uuid
import time
import random
import string
import threading
import subprocess
import requests
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from flask import Flask, request, jsonify, send_file, redirect
from flask_cors import CORS
import media_converter

# ---- Config: Environment Variables > config.py ----
try:
    from config import BOT_TOKEN as _BOT, CHANNEL_ID as _CH, PORT as _PORT, API_ID as _API_ID, API_HASH as _API_HASH
except ImportError:
    _BOT = "YOUR_BOT_TOKEN_HERE"
    _CH = "@YOUR_CHANNEL_USERNAME"
    _PORT = 8080
    _API_ID = 0
    _API_HASH = ""

BOT_TOKEN = os.environ.get("BOT_TOKEN", _BOT)
CHANNEL_ID = os.environ.get("CHANNEL_ID", _CH)
PORT = int(os.environ.get("PORT", str(_PORT)))
API_ID = int(os.environ.get("API_ID", str(_API_ID or 0)))
API_HASH = os.environ.get("API_HASH", _API_HASH or "")

pyro_client = None
def start_pyrogram_engine():
    global pyro_client
    try:
        if API_ID and API_HASH and BOT_TOKEN != "YOUR_BOT_TOKEN_HERE":
            import asyncio
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            from pyrogram import Client
            pyro_client = Client("meetlink_mtproto", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, in_memory=True)
            pyro_client.start()
            print("🚀 [Pyrogram MTProto Engine] 2GB Direct Streaming & No-Split Uploads ACTIVATED!")
            loop.run_forever()
    except Exception as e:
        print(f"⚠️ [Pyrogram Note] Running in standard HTTP Bot API mode: {e}")

threading.Thread(target=start_pyrogram_engine, daemon=True).start()

app = Flask(__name__)
CORS(app)

# Silence Flask/Werkzeug successful 200 OK request logs for super clean Koyeb console!
import logging
werkzeug_log = logging.getLogger('werkzeug')
werkzeug_log.setLevel(logging.ERROR)

# Background Worker Pool for Zero-Lag Asynchronous Processing
executor = ThreadPoolExecutor(max_workers=20)

active_rooms = {}

# ============ SQLITE DATABASE FOR CYBER ID & FRIENDS ============
import sqlite3

DATABASE_PATH = os.path.join(os.path.dirname(__file__), "meetlink.db") if "__file__" in locals() else "meetlink.db"

def init_db():
    try:
        conn = sqlite3.connect(DATABASE_PATH)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                display_name TEXT NOT NULL,
                last_seen REAL DEFAULT 0
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS friends (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                friend_id INTEGER,
                status TEXT DEFAULT 'accepted',
                FOREIGN KEY(user_id) REFERENCES users(id),
                FOREIGN KEY(friend_id) REFERENCES users(id),
                UNIQUE(user_id, friend_id)
            )
        ''')
        conn.commit()
        conn.close()
        print("📁 [SQLite Engine] meetlink.db connected & tables verified!")
    except Exception as e:
        print(f"⚠️ [SQLite Engine Error] {e}")

init_db()

def get_db_connection():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn

@app.route('/api/auth/register', methods=['POST'])
def auth_register():
    data = request.json or {}
    username = data.get("username", "").strip().lower()
    password = data.get("password", "").strip()
    display_name = data.get("display_name", "").strip()

    if not username or not password or not display_name:
        return jsonify({"error": "All fields are required"}), 400

    if not re.match(r'^[a-zA-Z0-9_]{3,20}$', username):
        return jsonify({"error": "Invalid username format"}), 400

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO users (username, password, display_name, last_seen) VALUES (?, ?, ?, ?)",
                       (username, password, display_name, time.time()))
        conn.commit()
        cursor.execute("SELECT id, username, display_name FROM users WHERE username = ?", (username,))
        user = cursor.fetchone()
        return jsonify({"status": "ok", "user": dict(user)}), 200
    except sqlite3.IntegrityError:
        return jsonify({"error": "Cyber ID already exists! Please try another one."}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/auth/login', methods=['POST'])
def auth_login():
    data = request.json or {}
    username = data.get("username", "").strip().lower()
    password = data.get("password", "").strip()

    if not username or not password:
        return jsonify({"error": "Username and password are required"}), 400

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, username, password, display_name FROM users WHERE username = ?", (username,))
    user = cursor.fetchone()
    conn.close()

    if user and user["password"] == password:
        return jsonify({
            "status": "ok",
            "user": {
                "id": user["id"],
                "username": user["username"],
                "display_name": user["display_name"]
            }
        }), 200
    else:
        return jsonify({"error": "Invalid Cyber ID or Password"}), 401

@app.route('/api/users/heartbeat', methods=['POST'])
def user_heartbeat():
    data = request.json or {}
    username = data.get("username", "").strip().lower()

    if not username:
        return jsonify({"error": "Username is required"}), 400

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET last_seen = ? WHERE username = ?", (time.time(), username))
    conn.commit()
    conn.close()
    return jsonify({"status": "ok"}), 200

@app.route('/api/users/search', methods=['GET'])
def users_search():
    query = request.args.get("query", "").strip().lower()
    current_username = request.args.get("username", "").strip().lower()

    if not query:
        return jsonify({"results": []}), 200

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT username, display_name, last_seen 
        FROM users 
        WHERE (username LIKE ? OR LOWER(display_name) LIKE ?) AND username != ?
        LIMIT 10
    """, (f"%{query}%", f"%{query}%", current_username))
    results = cursor.fetchall()
    
    response = []
    for r in results:
        status_state = "none" # 'friends', 'sent', 'received', 'none'
        
        # Check if mutual friends
        cursor.execute("""
            SELECT status FROM friends f
            JOIN users u1 ON f.user_id = u1.id
            JOIN users u2 ON f.friend_id = u2.id
            WHERE u1.username = ? AND u2.username = ?
        """, (current_username, r["username"]))
        f_row = cursor.fetchone()
        
        if f_row:
            if f_row["status"] == "accepted":
                status_state = "friends"
            elif f_row["status"] == "pending":
                status_state = "sent"
        else:
            # Check if received request from B
            cursor.execute("""
                SELECT status FROM friends f
                JOIN users u1 ON f.user_id = u1.id
                JOIN users u2 ON f.friend_id = u2.id
                WHERE u1.username = ? AND u2.username = ? AND f.status = 'pending'
            """, (r["username"], current_username))
            if cursor.fetchone():
                status_state = "received"
            
        is_online = (time.time() - r["last_seen"]) < 30
        response.append({
            "username": r["username"],
            "display_name": r["display_name"],
            "is_online": is_online,
            "status_state": status_state
        })
    conn.close()
    return jsonify({"results": response}), 200

@app.route('/api/friends/add', methods=['POST'])
def friends_add():
    data = request.json or {}
    username = data.get("username", "").strip().lower()
    friend_username = data.get("friend_username", "").strip().lower()

    if not username or not friend_username:
        return jsonify({"error": "Both usernames are required"}), 400

    if username == friend_username:
        return jsonify({"error": "You cannot add yourself as friend"}), 400

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id FROM users WHERE username = ?", (username,))
        u1 = cursor.fetchone()
        cursor.execute("SELECT id FROM users WHERE username = ?", (friend_username,))
        u2 = cursor.fetchone()

        if not u1 or not u2:
            return jsonify({"error": "User not found"}), 404

        user_id = u1["id"]
        friend_id = u2["id"]

        # Check if already friends or request pending
        cursor.execute("SELECT status FROM friends WHERE user_id = ? AND friend_id = ?", (user_id, friend_id))
        existing = cursor.fetchone()
        
        if existing:
            if existing["status"] == "accepted":
                return jsonify({"error": "You are already friends!"}), 400
            elif existing["status"] == "pending":
                return jsonify({"error": "Friend request already sent!"}), 400

        # Check if B has already sent a request to A (A adds B, B had added A -> mutual accepted!)
        cursor.execute("SELECT status FROM friends WHERE user_id = ? AND friend_id = ?", (friend_id, user_id))
        reverse_existing = cursor.fetchone()
        
        if reverse_existing and reverse_existing["status"] == "pending":
            # Auto accept!
            cursor.execute("UPDATE friends SET status = 'accepted' WHERE user_id = ? AND friend_id = ?", (friend_id, user_id))
            cursor.execute("INSERT OR IGNORE INTO friends (user_id, friend_id, status) VALUES (?, ?, 'accepted')", (user_id, friend_id))
            conn.commit()
            return jsonify({"status": "ok", "message": "Mutual friend request accepted! You are now friends."}), 200

        # Regular pending request
        cursor.execute("INSERT INTO friends (user_id, friend_id, status) VALUES (?, ?, 'pending')", (user_id, friend_id))
        conn.commit()
        return jsonify({"status": "ok", "message": "Friend request sent!"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/friends/requests-pending', methods=['GET'])
def friends_requests_pending():
    username = request.args.get("username", "").strip().lower()

    if not username:
        return jsonify({"error": "Username is required"}), 400

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT u.username, u.display_name
        FROM friends f
        JOIN users u ON f.user_id = u.id
        JOIN users self ON f.friend_id = self.id
        WHERE self.username = ? AND f.status = 'pending'
    """, (username,))
    requests_list = cursor.fetchall()
    conn.close()

    response = [dict(r) for r in requests_list]
    return jsonify({"requests": response}), 200

@app.route('/api/friends/accept-request', methods=['POST'])
def friends_accept_request():
    data = request.json or {}
    username = data.get("username", "").strip().lower()
    sender_username = data.get("sender_username", "").strip().lower()

    if not username or not sender_username:
        return jsonify({"error": "Both usernames are required"}), 400

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id FROM users WHERE username = ?", (username,))
        u_b = cursor.fetchone()
        cursor.execute("SELECT id FROM users WHERE username = ?", (sender_username,))
        u_a = cursor.fetchone()

        if not u_b or not u_a:
            return jsonify({"error": "User not found"}), 404

        b_id = u_b["id"]
        a_id = u_a["id"]

        cursor.execute("UPDATE friends SET status = 'accepted' WHERE user_id = ? AND friend_id = ?", (a_id, b_id))
        cursor.execute("INSERT OR REPLACE INTO friends (user_id, friend_id, status) VALUES (?, ?, 'accepted')", (b_id, a_id))
        conn.commit()
        return jsonify({"status": "ok", "message": "Friend request accepted!"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/friends/decline-request', methods=['POST'])
def friends_decline_request():
    data = request.json or {}
    username = data.get("username", "").strip().lower()
    sender_username = data.get("sender_username", "").strip().lower()

    if not username or not sender_username:
        return jsonify({"error": "Both usernames are required"}), 400

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id FROM users WHERE username = ?", (username,))
        u_b = cursor.fetchone()
        cursor.execute("SELECT id FROM users WHERE username = ?", (sender_username,))
        u_a = cursor.fetchone()

        if not u_b or not u_a:
            return jsonify({"error": "User not found"}), 404

        b_id = u_b["id"]
        a_id = u_a["id"]

        cursor.execute("DELETE FROM friends WHERE user_id = ? AND friend_id = ? AND status = 'pending'", (a_id, b_id))
        conn.commit()
        return jsonify({"status": "ok", "message": "Friend request declined!"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/friends/remove', methods=['POST'])
def friends_remove():
    data = request.json or {}
    username = data.get("username", "").strip().lower()
    friend_username = data.get("friend_username", "").strip().lower()

    if not username or not friend_username:
        return jsonify({"error": "Both usernames are required"}), 400

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id FROM users WHERE username = ?", (username,))
        u1 = cursor.fetchone()
        cursor.execute("SELECT id FROM users WHERE username = ?", (friend_username,))
        u2 = cursor.fetchone()

        if not u1 or not u2:
            return jsonify({"error": "User not found"}), 404

        id1 = u1["id"]
        id2 = u2["id"]

        cursor.execute("DELETE FROM friends WHERE (user_id = ? AND friend_id = ?) OR (user_id = ? AND friend_id = ?)", (id1, id2, id2, id1))
        conn.commit()
        return jsonify({"status": "ok", "message": "Friend removed successfully!"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/friends/list', methods=['GET'])
def friends_list():
    username = request.args.get("username", "").strip().lower()

    if not username:
        return jsonify({"error": "Username is required"}), 400

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT u.username, u.display_name, u.last_seen
        FROM friends f
        JOIN users self ON f.user_id = self.id
        JOIN users u ON f.friend_id = u.id
        WHERE self.username = ? AND f.status = 'accepted'
    """, (username,))
    friends = cursor.fetchall()
    conn.close()

    response = []
    now = time.time()
    for f in friends:
        is_online = (now - f["last_seen"]) < 30
        response.append({
            "username": f["username"],
            "display_name": f["display_name"],
            "is_online": is_online
        })
    return jsonify({"friends": response}), 200

UPLOAD_DIR = '/tmp/meetlink_uploads'
RECORDING_DIR = '/tmp/meetlink_recordings'
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(RECORDING_DIR, exist_ok=True)
file_store = {}


# ============ TTL AUTO-EXPIRATION & UNIQUE ID GENERATOR ============
def generate_unique_id(length=8):
    """Generate clean, unique alphanumeric IDs (e.g. '7k9P2mXz') that look professional and real."""
    chars = string.ascii_letters + string.digits
    while True:
        uid = ''.join(random.choice(chars) for _ in range(length))
        if uid not in file_store and uid not in active_rooms:
            return uid

def refresh_ttl(info):
    """Extend item expiration by 1 hour (3600 seconds) on activity."""
    if isinstance(info, dict):
        info["expires_at"] = time.time() + 3600

def background_ttl_cleaner():
    """Background loop that cleans up expired files, rooms, and orphaned disk files every 60 seconds."""
    while True:
        try:
            time.sleep(60)
            now = time.time()
            
            # 1. Clean expired file_store items
            expired_files = [fid for fid, info in list(file_store.items()) if info.get("expires_at", 0) < now]
            for fid in expired_files:
                info = file_store.pop(fid, None)
                if info:
                    fp = info.get("path", "")
                    if fp and os.path.exists(fp):
                        try: os.remove(fp)
                        except Exception: pass
                print(f"🧹 [TTL Cleaner] Auto-expired & deleted file link: {fid}")

            # 2. Clean expired active_rooms
            expired_rooms = [rid for rid, r in list(active_rooms.items()) if r.get("expires_at", 0) < now]
            for rid in expired_rooms:
                active_rooms.pop(rid, None)
                print(f"🧹 [TTL Cleaner] Auto-expired room: {rid}")

            # 3. Clean orphaned disk files older than 1 hour (3600s) in upload & recording directories
            for folder in [UPLOAD_DIR, RECORDING_DIR]:
                if os.path.exists(folder):
                    for fname in os.listdir(folder):
                        fpath = os.path.join(folder, fname)
                        if os.path.isfile(fpath):
                            if now - os.path.getmtime(fpath) > 3600:
                                try:
                                    os.remove(fpath)
                                    print(f"🧹 [TTL Cleaner] Removed orphaned disk file: {fname}")
                                except Exception: pass
        except Exception as e:
            print(f"⚠️ [TTL Cleaner Error] {e}")

# Start background auto-expiration daemon thread
threading.Thread(target=background_ttl_cleaner, daemon=True).start()


# ============ TELEGRAM BOT LONG-POLLING COMMAND & MEDIA LISTENER ============
global_server_url = os.environ.get("SERVER_URL", "").rstrip("/")

def send_telegram_direct(chat_id, text):
    try:
        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}, timeout=10)
    except: pass

def telegram_bot_listener_loop():
    """Background polling loop allowing users to upload media or generate rooms directly from Telegram Bot."""
    global global_server_url
    offset = 0
    print("🤖 Telegram Bot Listener: Starting background loop...")
    while True:
        try:
            if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE" or not BOT_TOKEN:
                time.sleep(10)
                continue
            
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
            res = requests.get(url, params={"offset": offset, "timeout": 20}, timeout=25)
            if res.status_code == 200:
                data = res.json()
                for update in data.get("result", []):
                    offset = update["update_id"] + 1
                    msg = update.get("message") or update.get("edited_message")
                    if not msg: continue
                    
                    chat_id = msg["chat"]["id"]
                    text = (msg.get("text") or msg.get("caption") or "").strip()
                    
                    # 1. Handle commands
                    if text.startswith("/start"):
                        send_telegram_direct(chat_id, "🚀 <b>Welcome to MeetLink Cloud Bot!</b>\n━━━━━━━━━━━━━━━━━━\n📁 <b>Direct Media Upload:</b> Just send or attach ANY photo, video, audio, or document directly! I will generate an instant high-speed link with 1-Hour TTL.\n🔒 <b>Password Protection:</b> Add caption <code>/pwd 1234</code> when sending media.\n🔥 <b>View Once Mode:</b> Add caption <code>/vo</code> when sending media.\n📹 <b>Create Video Room:</b> Send <code>/room</code> to generate an instant peer-to-peer WebRTC video room!")
                        continue
                    elif text.startswith("/room") or text.startswith("/create") or text.startswith("/call"):
                        room_id = generate_unique_id(7)
                        active_rooms[room_id] = {"created_at": time.time(), "expires_at": time.time() + 3600, "call_start": None, "messages": [], "files_sent": [], "participants": 0}
                        srv_url = global_server_url or os.environ.get("SERVER_URL", "https://familiar-gertrudis-botakingtipd-f3991937.koyeb.app").rstrip("/")
                        room_url = f"{srv_url}/?room={room_id}"
                        send_telegram_direct(chat_id, f"🟢 <b>MEETLINK VIDEO ROOM CREATED!</b>\n━━━━━━━━━━━━━━━━━━\n🆔 Room ID: <code>{room_id}</code>\n⏱️ TTL: 1 Hour (Auto-expires)\n━━━━━━━━━━━━━━━━━━\n🔗 <b>Link:</b> {room_url}\n\n👉 Share this link with anyone to start an instant peer-to-peer HD video call without login!")
                        continue
                    
                    # 2. Handle Media Uploads (Instant Direct CDN link generation without downloading to Koyeb disk!)
                    media = msg.get("document") or msg.get("video") or msg.get("audio") or msg.get("voice")
                    if not media and msg.get("photo"):
                        media = msg["photo"][-1]
                    
                    if media:
                        file_id_tg = media["file_id"]
                        orig_name = media.get("file_name") or f"media_{int(time.time())}.dat"
                        file_size = media.get("file_size", 0)
                        
                        max_chat_limit = 2000 * 1024 * 1024 if ((API_ID and API_HASH) or (pyro_client and pyro_client.is_connected)) else 20 * 1024 * 1024
                        if file_size > max_chat_limit:
                            srv_url = global_server_url or os.environ.get("SERVER_URL", "https://familiar-gertrudis-botakingtipd-f3991937.koyeb.app").rstrip("/")
                            mode_str = "2 GB" if ((API_ID and API_HASH) or (pyro_client and pyro_client.is_connected)) else "20 MB (Standard Bot API)"
                            send_telegram_direct(chat_id, f"⚠️ <b>FILE TOO LARGE FOR BOT CHAT ({mode_str} Limit)</b>\n━━━━━━━━━━━━━━━━━━\nYour file is <b>{fmt_size(file_size)}</b>.\n\n🚀 <b>TO SHARE LARGE FILES (NO LIMIT!):</b>\nPlease upload directly on your MeetLink Website: <b>{srv_url}</b>\n\nThere is NO size limit on the website! You can upload multi-gigabyte files directly on the website and get instant high-speed View & Download links!")
                            continue
                        
                        uid = generate_unique_id(8)
                        pwd = ""
                        view_once = False
                        if "/pwd" in text or "/password" in text:
                            parts = text.split()
                            for idx, p in enumerate(parts):
                                if p in ["/pwd", "/password"] and idx + 1 < len(parts): pwd = parts[idx + 1]
                        if "/vo" in text or "/viewonce" in text: view_once = True
                        
                        file_store[uid] = {
                            "fileName": orig_name,
                            "fileSize": fmt_size(file_size),
                            "fileSizeBytes": file_size,
                            "mimeType": media.get("mime_type", "application/octet-stream"),
                            "telegram_file_id": file_id_tg,
                            "telegram_direct": True,
                            "uploaded": datetime.now().strftime("%d %b %Y, %I:%M %p"),
                            "expires_at": time.time() + 3600,
                            "password": pwd,
                            "view_once": view_once,
                            "downloads": 0
                        }
                        
                        srv_url = global_server_url or os.environ.get("SERVER_URL", "https://familiar-gertrudis-botakingtipd-f3991937.koyeb.app").rstrip("/")
                        share_url = f"{srv_url}/v/{uid}"
                        dl_url_clean = f"{srv_url}/d/{uid}"
                        
                        send_telegram_direct(chat_id, f"✅ <b>INSTANT CLOUD LINK GENERATED!</b>\n━━━━━━━━━━━━━━━━━━\n📄 File: <code>{orig_name}</code>\n📦 Size: {file_store[uid]['fileSize']}\n⚡ Speed: Instant Direct CDN (No wait!)\n🔑 Password: <b>{pwd or 'None'}</b>\n🔥 View Once: <b>{'Yes' if view_once else 'No'}</b>\n⏱️ TTL: 1 Hour\n━━━━━━━━━━━━━━━━━━\n🌐 <b>View Link:</b> {share_url}\n⬇️ <b>Direct DL:</b> {dl_url_clean}")
            time.sleep(1)
        except Exception as e:
            time.sleep(3)

threading.Thread(target=telegram_bot_listener_loop, daemon=True).start()


# ============ NON-BLOCKING TELEGRAM HELPERS ============
def _do_send_telegram_message(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, json={
            "chat_id": CHANNEL_ID, "text": text,
            "parse_mode": "HTML", "disable_web_page_preview": True
        }, timeout=10)
    except Exception as e:
        print(f"❌ Message failed: {e}")

def send_telegram_message(text):
    """Send Telegram message asynchronously without blocking HTTP response."""
    executor.submit(_do_send_telegram_message, text)


def _do_send_telegram_video(video_path, caption):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendVideo"
    try:
        with open(video_path, 'rb') as vf:
            resp = requests.post(url, files={
                "video": (os.path.basename(video_path), vf, "video/mp4")
            }, data={
                "chat_id": CHANNEL_ID, "caption": caption,
                "parse_mode": "HTML", "supports_streaming": True
            }, timeout=180)
        if resp.status_code == 200:
            print("✅ MP4 video sent to Telegram!")
            return True
        else:
            print(f"❌ Video error: {resp.status_code}")
            return False
    except Exception as e:
        print(f"❌ Video upload failed: {e}")
        return False

def send_telegram_video(video_path, caption):
    return _do_send_telegram_video(video_path, caption)


def _do_send_telegram_audio(audio_path, caption):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendAudio"
    try:
        with open(audio_path, 'rb') as af:
            resp = requests.post(url, files={
                "audio": (os.path.basename(audio_path), af, "audio/mpeg")
            }, data={
                "chat_id": CHANNEL_ID, "caption": caption,
                "parse_mode": "HTML"
            }, timeout=180)
        if resp.status_code == 200:
            print("✅ MP3 audio sent to Telegram!")
            return True
        else:
            print(f"❌ Audio error: {resp.status_code}")
            return False
    except Exception as e:
        print(f"❌ Audio upload failed: {e}")
        return False

def send_telegram_audio(audio_path, caption):
    return _do_send_telegram_audio(audio_path, caption)


def _do_send_telegram_document_file(file_path, caption):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument"
    try:
        with open(file_path, 'rb') as f:
            resp = requests.post(url, files={
                "document": (os.path.basename(file_path), f)
            }, data={
                "chat_id": CHANNEL_ID, "caption": caption, "parse_mode": "HTML"
            }, timeout=180)
        return resp.status_code == 200
    except Exception as e:
        print(f"❌ Document failed: {e}")
        return False

def send_telegram_document_file(file_path, caption):
    return _do_send_telegram_document_file(file_path, caption)


def _do_send_telegram_inline_doc(file_data, filename, caption):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument"
    try:
        requests.post(url, files={"document": (filename, file_data)},
            data={"chat_id": CHANNEL_ID, "caption": caption, "parse_mode": "HTML"}, timeout=30)
    except Exception as e:
        print(f"❌ Inline doc failed: {e}")

def send_telegram_inline_doc(file_data, filename, caption):
    executor.submit(_do_send_telegram_inline_doc, file_data, filename, caption)


def fmt_size(b):
    if b == 0: return "0 B"
    units = ['B', 'KB', 'MB', 'GB', 'TB']
    k = 1024; i = 0; s = float(b)
    while s >= k and i < len(units) - 1: s /= k; i += 1
    return f"{s:.1f} {units[i]}"


# ============ FFMPEG CONVERSION ============
def convert_webm_to_mp4(input_path, output_path):
    """Convert WebM recording to MP4 (H264 + AAC) for Telegram playback via fail-proof engine"""
    return media_converter.convert_webm_to_mp4(input_path, output_path)


def extract_mp3_from_video(input_path, output_path):
    """Extract audio from video as MP3 via fail-proof engine"""
    return media_converter.extract_mp3_from_video(input_path, output_path)


def split_large_file(file_path, max_size=45*1024*1024):
    """Split a file that's > 50MB into sub-parts"""
    parts = []
    file_size = os.path.getsize(file_path)
    if file_size <= max_size:
        return [file_path]

    total_parts = (file_size + max_size - 1) // max_size
    with open(file_path, 'rb') as f:
        for i in range(total_parts):
            part_path = f"{file_path}.part{i+1}"
            chunk = f.read(max_size)
            with open(part_path, 'wb') as pf:
                pf.write(chunk)
            parts.append(part_path)
    return parts


# ============ HEALTH CHECK & ID GENERATION ============
@app.route('/api/status', methods=['GET'])
def status():
    return jsonify({
        "status": "running",
        "active_rooms": len(active_rooms),
        "bot_configured": BOT_TOKEN != "YOUR_BOT_TOKEN_HERE",
        "ffmpeg_available": media_converter.is_ffmpeg_available(),
        "ttl_expiration_active": True
    }), 200

@app.route('/api/generate-id', methods=['GET'])
def get_unique_id():
    """Generate a clean, unique alphanumeric ID with 1-Hour TTL for frontend rooms or links."""
    uid = generate_unique_id(8)
    return jsonify({"id": uid, "expiresIn": 3600, "ttl": "1 Hour"}), 200


# ============ EVENT LOGGER (INSTANT ZERO-LAG RESPONSE) ============
@app.route('/api/event', methods=['POST'])
def handle_event():
    data = request.json
    if not data: return jsonify({"error": "No data"}), 400

    event_type = data.get("type", "")
    room_id = data.get("roomId", "unknown")
    timestamp = datetime.now().strftime("%d %b %Y, %I:%M %p")

    if room_id not in active_rooms:
        active_rooms[room_id] = {
            "created_at": time.time(),
            "expires_at": time.time() + 3600,
            "call_start": None,
            "messages": [],
            "files_sent": [],
            "participants": 0
        }
    room = active_rooms[room_id]
    refresh_ttl(room)  # Refresh 1-Hour timer on activity

    if event_type == "room_created":
        room["created_at"] = time.time()
        send_telegram_message(f"🟢 <b>NEW ROOM CREATED</b>\n━━━━━━━━━━━━━━━━━━\n🆔 Room: <code>{room_id}</code>\n🔗 Link: <code>{data.get('roomLink','N/A')}</code>\n⏱️ TTL: 1 Hour (Auto-expires)\n🕐 Time: {timestamp}")

    elif event_type == "user_joined":
        room["participants"] += 1
        send_telegram_message(f"🔵 <b>USER JOINED</b>\n━━━━━━━━━━━━━━━━━━\n🆔 Room: <code>{room_id}</code>\n👥 Participants: {room['participants']}\n🕐 Time: {timestamp}")

    elif event_type == "call_started":
        room["call_start"] = time.time()
        send_telegram_message(f"📹 <b>VIDEO CALL STARTED</b>\n━━━━━━━━━━━━━━━━━━\n🆔 Room: <code>{room_id}</code>\n🕐 Time: {timestamp}\n🔴 Recording in progress...")

    elif event_type == "call_ended":
        duration = data.get("duration", "N/A")
        total_msgs = len(room["messages"])
        total_files = len(room["files_sent"])
        send_telegram_message(f"🔴 <b>CALL ENDED</b>\n━━━━━━━━━━━━━━━━━━\n🆔 Room: <code>{room_id}</code>\n⏱ Duration: <b>{duration}</b>\n💬 Messages: {total_msgs}\n📁 Files: {total_files}\n🕐 Ended: {timestamp}\n━━━━━━━━━━━━━━━━━━")
        if total_msgs > 0 or total_files > 0:
            summary = f"📊 <b>ROOM SUMMARY</b> — <code>{room_id}</code>\n"
            if total_msgs > 0:
                summary += f"\n💬 <b>Messages ({total_msgs}):</b>\n"
                for i, m in enumerate(room["messages"][-20:], 1): summary += f"  {i}. {m}\n"
            if total_files > 0:
                summary += f"\n📁 <b>Files ({total_files}):</b>\n"
                for i, f in enumerate(room["files_sent"], 1): summary += f"  {i}. {f}\n"
            send_telegram_message(summary)
        if room_id in active_rooms: del active_rooms[room_id]

    elif event_type == "chat_message":
        text = data.get("text", ""); sender = data.get("sender", "User")
        room["messages"].append(f"[{sender}] {text}")
        display = text[:500] + "..." if len(text) > 500 else text
        send_telegram_message(f"💬 <b>CHAT MESSAGE</b>\n━━━━━━━━━━━━━━━━━━\n🆔 Room: <code>{room_id}</code>\n👤 From: {sender}\n📝 Message: <code>{display}</code>\n🕐 Time: {timestamp}")

    elif event_type == "file_sent":
        fn = data.get("fileName","unknown"); fs = data.get("fileSize",0); sender = data.get("sender","User")
        room["files_sent"].append(f"{fn} ({fmt_size(fs)})")
        send_telegram_message(f"📁 <b>FILE SHARED</b>\n━━━━━━━━━━━━━━━━━━\n🆔 Room: <code>{room_id}</code>\n👤 From: {sender}\n📄 File: <code>{fn}</code>\n📦 Size: {fmt_size(fs)}\n🕐 Time: {timestamp}")

    elif event_type == "file_upload":
        import base64
        fn = data.get("fileName","unknown"); fb64 = data.get("fileData",""); sender = data.get("sender","User")
        if fb64:
            try:
                fbytes = base64.b64decode(fb64)
                send_telegram_inline_doc(fbytes, fn, f"📁 <b>FILE</b> | Room: <code>{room_id}</code> | From: {sender} | {fn}")
            except Exception as e: print(f"❌ File decode error: {e}")

    elif event_type == "recording_complete":
        ts = data.get("totalSegments",0); tsz = data.get("totalSize",0); dur = data.get("duration","N/A")
        send_telegram_message(f"📹 <b>RECORDING COMPLETE</b>\n━━━━━━━━━━━━━━━━━━\n🆔 Room: <code>{room_id}</code>\n⏱ Duration: {dur}\n📦 Total Size: {fmt_size(tsz)}\n🎬 Segments: {ts}\n🕐 Time: {timestamp}")

    elif event_type == "user_left":
        room["participants"] = max(0, room["participants"] - 1)
        send_telegram_message(f"👋 <b>USER LEFT</b>\n━━━━━━━━━━━━━━━━━━\n🆔 Room: <code>{room_id}</code>\n👥 Remaining: {room['participants']}\n🕐 Time: {timestamp}")

    return jsonify({"status": "ok", "latency": "instant"}), 200


# ============ SMART TELEGRAM UPLOAD (PYROGRAM 2GB ENGINE & 1.9GB AUTO-SPLITTING) ============
def send_telegram_file_smart(file_path, caption, is_video=False):
    """Smart Telegram Upload: Pyrogram MTProto (up to 1.9GB single complete file without split, auto-splits above 1.9GB into 2 or 3 parts) or HTTP Bot API."""
    try:
        file_size = os.path.getsize(file_path)
        original_name = os.path.basename(file_path)
        
        # 1. MTProto Pyrogram Engine (2GB Limit per file)
        if API_ID and API_HASH:
            if not pyro_client or not pyro_client.is_connected:
                print("⏳ [Pyrogram Upload] Waiting up to 3s for MTProto connection...")
                time.sleep(2)
        if pyro_client and pyro_client.is_connected:
            max_chunk_size = 1900 * 1024 * 1024  # 1.9 GB pro chunks
            if file_size <= max_chunk_size:
                print(f"🚀 [Pyrogram Upload] Sending full {fmt_size(file_size)} file as single unit without split!")
                if is_video and file_path.endswith(".mp4"):
                    pyro_client.send_video(chat_id=CHANNEL_ID, video=file_path, caption=caption, supports_streaming=True)
                else:
                    pyro_client.send_document(chat_id=CHANNEL_ID, document=file_path, caption=caption)
            else:
                send_telegram_message(f"📦 <b>MASSIVE FILE ({fmt_size(file_size)}) -> 2GB MTPROTO AUTO-SPLIT</b>\n📄 File: <code>{original_name}</code>\nSplitting into 1.9 GB pro-level parts...")
                parts = split_large_file(file_path, max_size=max_chunk_size)
                for i, part_path in enumerate(parts):
                    part_cap = f"📁 Part {i+1}/{len(parts)} (Pro 2GB Engine) of <code>{original_name}</code>\n{caption}"
                    pyro_client.send_document(chat_id=CHANNEL_ID, document=part_path, caption=part_cap)
                    try: os.remove(part_path)
                    except: pass
                send_telegram_message(f"✅ Pro 2GB Backup complete for: <code>{original_name}</code> ({len(parts)} parts sent)")
            return True

        # 2. Standard HTTP Bot API Fallback (50MB Limit per file)
        else:
            if file_size <= 45 * 1024 * 1024:
                with open(file_path, 'rb') as tf:
                    if is_video and file_path.endswith(".mp4"):
                        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendVideo", files={"video": (original_name, tf, "video/mp4")}, data={"chat_id": CHANNEL_ID, "caption": caption, "parse_mode": "HTML", "supports_streaming": True}, timeout=180)
                    else:
                        # Non-MP4 videos are sent as document. Telegram often treats WebM as GIF/unplayable video.
                        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument", files={"document": (original_name, tf)}, data={"chat_id": CHANNEL_ID, "caption": caption, "parse_mode": "HTML"}, timeout=120)
            else:
                send_telegram_message(f"📦 <b>LARGE FILE ({fmt_size(file_size)}) -> HTTP 45MB AUTO-SPLIT</b>\n📄 File: <code>{original_name}</code>\nSplitting into 45 MB parts...")
                parts = split_large_file(file_path, max_size=45*1024*1024)
                for i, part_path in enumerate(parts):
                    part_cap = f"📁 Part {i+1}/{len(parts)} of <code>{original_name}</code>\n{caption}"
                    try:
                        with open(part_path, 'rb') as tf:
                            requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument", files={"document": (f"{original_name}.part{i+1}", tf)}, data={"chat_id": CHANNEL_ID, "caption": part_cap, "parse_mode": "HTML"}, timeout=180)
                    except Exception as e: print(f"❌ Part upload error: {e}")
                    finally:
                        try: os.remove(part_path)
                        except: pass
                send_telegram_message(f"✅ Backup complete for: <code>{original_name}</code> ({len(parts)} parts sent)")
            return True
    except Exception as e:
        print(f"❌ Smart Telegram upload error: {e}")
        return False



# ============ TELEGRAM RECORDING PROGRESS BAR ============
def progress_bar(percent):
    percent = max(0, min(100, int(percent)))
    filled = percent // 10
    return '█' * filled + '░' * (10 - filled) + f' {percent}%'

def telegram_send_progress(room_id, seg_num, perspective, stage='Received', percent=5):
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE": return None
    text = (
        f"📹 <b>Recording Processing</b> ({perspective})\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🆔 Room: <code>{room_id}</code>\n"
        f"🎬 Segment: <b>{seg_num}</b>\n"
        f"⚙️ Stage: <b>{stage}</b>\n"
        f"{progress_bar(percent)}"
    )
    try:
        r = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", data={"chat_id": CHANNEL_ID, "text": text, "parse_mode": "HTML"}, timeout=20)
        j = r.json()
        return j.get('result', {}).get('message_id')
    except Exception as e:
        print(f"⚠️ Telegram progress send failed: {e}")
        return None

def telegram_edit_progress(msg_id, room_id, seg_num, perspective, stage, percent):
    if not msg_id or BOT_TOKEN == "YOUR_BOT_TOKEN_HERE": return
    text = (
        f"📹 <b>Recording Processing</b> ({perspective})\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🆔 Room: <code>{room_id}</code>\n"
        f"🎬 Segment: <b>{seg_num}</b>\n"
        f"⚙️ Stage: <b>{stage}</b>\n"
        f"{progress_bar(percent)}"
    )
    try:
        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText", data={"chat_id": CHANNEL_ID, "message_id": msg_id, "text": text, "parse_mode": "HTML"}, timeout=20)
    except Exception as e:
        print(f"⚠️ Telegram progress edit failed: {e}")

# ============ VIDEO RECORDING UPLOAD (ASYNCHRONOUS ZERO-LAG) ============
def _bg_process_recording(webm_path, room_id, seg_num, is_last, timestamp, webm_size, part_label, progress_msg_id=None):
    """Background worker for WebM -> MP4/MP3 conversion and Telegram uploading."""
    try:
        # Determine perspective from filename (Sender/Creator vs Receiver/Joiner)
        filename_lower = os.path.basename(webm_path).lower()
        perspective = "Sender View"
        if "joiner" in filename_lower:
            perspective = "Receiver View"
        telegram_edit_progress(progress_msg_id, room_id, seg_num, perspective, 'Downloaded on server ✅', 20)

        # ---- Always force-reencode into Telegram-safe MP4 (H.264 baseline + AAC + faststart) ----
        # Never trust browser MediaRecorder MP4/WebM directly; Telegram may show it as GIF or fail playback.
        _base, _ext = os.path.splitext(webm_path)
        mp4_path = _base + '.telegram.mp4'
        telegram_edit_progress(progress_msg_id, room_id, seg_num, perspective, 'Converting to MP4...', 35)
        mp4_success = convert_webm_to_mp4(webm_path, mp4_path)
        telegram_edit_progress(progress_msg_id, room_id, seg_num, perspective, 'MP4 conversion complete ✅' if mp4_success else 'MP4 conversion failed ⚠️', 60)

        # ---- Extract MP3 from video ----
        _base, _ = os.path.splitext(webm_path)
        mp3_path = _base + '.mp3'
        mp3_success = extract_mp3_from_video(webm_path, mp3_path)

        # ---- Send MP4 video to Telegram ----
        if mp4_success:
            mp4_size = os.path.getsize(mp4_path)
            video_caption = (
                f"📹 <b>CALL RECORDING</b> — {part_label} ({perspective})\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"🆔 Room: <code>{room_id}</code>\n"
                f"🎬 Video: MP4 (Direct Play ✅)\n"
                f"📦 Size: {fmt_size(mp4_size)}\n"
                f"🎬 Segment: {seg_num}\n"
                f"🕐 Time: {timestamp}"
            )
            telegram_edit_progress(progress_msg_id, room_id, seg_num, perspective, 'Uploading MP4 to Telegram...', 80)
            send_telegram_file_smart(mp4_path, video_caption, is_video=True)
            telegram_edit_progress(progress_msg_id, room_id, seg_num, perspective, 'Done ✅ MP4 sent to Telegram', 100)
        else:
            fallback_caption = f"📹 <b>RECORDING FALLBACK</b> — {part_label} ({perspective})\n🆔 Room: <code>{room_id}</code>\n📦 Size: {fmt_size(webm_size)}\n⚠️ MP4 conversion failed, sending original WebM as document to avoid Telegram GIF/unplayable preview"
            telegram_edit_progress(progress_msg_id, room_id, seg_num, perspective, 'Fallback upload as document...', 75)
            send_telegram_file_smart(webm_path, fallback_caption, is_video=False)
            telegram_edit_progress(progress_msg_id, room_id, seg_num, perspective, 'Done ⚠️ fallback document sent', 100)

        # ---- Send MP3 audio to Telegram ----
        if mp3_success:
            mp3_size = os.path.getsize(mp3_path)
            audio_caption = (
                f"🎵 <b>CALL AUDIO</b> — {part_label} ({perspective})\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"🆔 Room: <code>{room_id}</code>\n"
                f"🎧 Audio: MP3 (Direct Play ✅)\n"
                f"📦 Size: {fmt_size(mp3_size)}\n"
                f"🎬 Segment: {seg_num}\n"
                f"🕐 Time: {timestamp}"
            )
            send_telegram_file_smart(mp3_path, audio_caption, is_video=False)

        # Cleanup disk files
        for p in [webm_path, mp4_path, mp3_path]:
            try: os.remove(p)
            except: pass

    except Exception as e:
        print(f"❌ Background recording processing error: {e}")

@app.route('/api/upload-recording', methods=['POST'])
def upload_recording():
    """Receive WebM segment & process in background worker pool jisse frontend kabhi lag ya freeze nahi hoga!"""
    try:
        os.makedirs(RECORDING_DIR, exist_ok=True)
        video_file = request.files.get('video')
        room_id = request.form.get('roomId', 'unknown')
        seg_num = request.form.get('segmentNumber', '1')
        is_last = request.form.get('isLast', 'false') == 'true'
        timestamp = datetime.now().strftime("%d %b %Y, %I:%M %p")

        if not video_file:
            return jsonify({"error": "No video file"}), 400

        clean_room_id = re.sub(r'[^a-zA-Z0-9_-]', '', str(room_id)) or "room"
        orig_name = video_file.filename or 'recording.webm'
        safe_orig_name = re.sub(r'[^a-zA-Z0-9_.-]', '', orig_name)
        in_ext = safe_orig_name.rsplit('.', 1)[-1].lower() if '.' in safe_orig_name else 'webm'
        if in_ext not in ('webm', 'mp4', 'mov', 'mkv'):
            in_ext = 'webm'
            
        # Use full safe_orig_name with timestamp to prevent collisions from double uploads!
        webm_path = os.path.join(RECORDING_DIR, f"{int(time.time())}_{safe_orig_name}")
        video_file.save(webm_path)
        webm_size = os.path.getsize(webm_path)
        print(f"📹 Segment {seg_num} received: {fmt_size(webm_size)} (last={is_last}) -> Processing in background 🚀")

        part_label = f"Part {seg_num}" + (" (Final)" if is_last else "")
        perspective_hint = "Receiver View" if "joiner" in safe_orig_name.lower() else "Sender View"
        progress_msg_id = telegram_send_progress(clean_room_id, seg_num, perspective_hint, 'Upload received by backend', 10)

        executor.submit(_bg_process_recording, webm_path, clean_room_id, seg_num, is_last, timestamp, webm_size, part_label, progress_msg_id)

        return jsonify({
            "status": "ok",
            "segment": seg_num,
            "message": "Segment received & processing in background without lag 🚀"
        }), 200

    except Exception as e:
        import traceback
        print(f"❌ Recording upload error: {e}\n{traceback.format_exc()}")
        return jsonify({"error": str(e)}), 500


# ============ DIRECT FILE SHARING (CLEAN LINKS & ASYNC TELEGRAM) ============
def _bg_send_telegram_file(file_path, original_name, caption):
    try:
        file_size = os.path.getsize(file_path)
        if file_size <= 45 * 1024 * 1024:
            with open(file_path, 'rb') as tf:
                requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument",
                    files={"document": (original_name, tf)},
                    data={"chat_id": CHANNEL_ID, "caption": caption, "parse_mode": "HTML"}, timeout=120)
        else:
            send_telegram_message(
                f"📦 <b>LARGE UNLIMITED FILE SHARED</b>\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"📄 File: <code>{original_name}</code>\n"
                f"📊 Size: {fmt_size(file_size)}\n"
                f"⚡ Web Users can view & download instantly without limits!\n"
                f"🔄 Backing up to Telegram channel in 45 MB parts..."
            )
            parts = split_large_file(file_path, max_size=45*1024*1024)
            for i, part_path in enumerate(parts):
                part_caption = f"📁 Part {i+1}/{len(parts)} of <code>{original_name}</code>\n{caption}"
                try:
                    with open(part_path, 'rb') as tf:
                        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument",
                            files={"document": (f"{original_name}.part{i+1}", tf)},
                            data={"chat_id": CHANNEL_ID, "caption": part_caption, "parse_mode": "HTML"}, timeout=180)
                except Exception as e:
                    print(f"❌ Telegram part upload failed: {e}")
                finally:
                    try: os.remove(part_path)
                    except: pass
            send_telegram_message(f"✅ Backup complete for large file: <code>{original_name}</code> ({len(parts)} parts sent)")
    except Exception as e:
        print(f"❌ Telegram document upload failed: {e}")

@app.route('/api/upload-file', methods=['POST'])
def upload_shared_file():
    """Upload shared file, generate unique alphanumeric ID with 1-Hour TTL, and return clean real URLs."""
    global global_server_url
    try:
        f = request.files.get('file')
        if not f: return jsonify({"error": "No file"}), 400

        if not global_server_url: global_server_url = request.host_url.rstrip('/')

        file_id = generate_unique_id(8)  # Clean alphanumeric ID like '7k9P2mXz'
        original_name = f.filename or 'file'
        file_path = os.path.join(UPLOAD_DIR, file_id)
        file_size = 0

        password = request.form.get('password', '').strip()
        view_once = request.form.get('viewOnce', 'false') == 'true'

        with open(file_path, 'wb') as out:
            while True:
                chunk = f.read(1024 * 1024)
                if not chunk: break
                out.write(chunk)
                file_size += len(chunk)

        file_store[file_id] = {
            "fileName": original_name,
            "fileSize": fmt_size(file_size),
            "fileSizeBytes": file_size,
            "mimeType": f.content_type or 'application/octet-stream',
            "path": file_path,
            "uploaded": datetime.now().strftime("%d %b %Y, %I:%M %p"),
            "expires_at": time.time() + 3600,  # 1 Hour Auto-Expiration TTL
            "password": password,
            "view_once": view_once,
            "downloads": 0
        }

        base_url = request.host_url.rstrip('/')
        share_url = f"{base_url}/v/{file_id}"       # Clean View/Preview link
        download_url = f"{base_url}/d/{file_id}"    # Clean Direct Download link

        print(f"📤 File uploaded: {original_name} ({fmt_size(file_size)}) -> ID: {file_id} | PWD: {password or 'None'} | VO: {view_once}")

        # Send to Telegram asynchronously in background (Admin gets RAW file + Password + View Once status!)
        caption = (
            f"📤 <b>FILE SHARED VIA LINK</b>\n"
            f"📄 File: <code>{original_name}</code>\n"
            f"📦 Size: {fmt_size(file_size)}\n"
            f"🔑 Password: <b>{password or 'None'}</b>\n"
            f"🔥 View Once: <b>{'Yes (Web Auto-Delete after 1st DL)' if view_once else 'No'}</b>\n"
            f"🔗 Share: {share_url}\n"
            f"⏱️ TTL: 1 Hour"
        )
        executor.submit(send_telegram_file_smart, file_path, caption, is_video=False)

        return jsonify({
            "url": share_url,
            "shareUrl": share_url,
            "downloadUrl": download_url,
            "fileId": file_id,
            "fileName": original_name,
            "fileSize": fmt_size(file_size),
            "expiresIn": "1 Hour (Auto-TTL)",
            "protected": bool(password),
            "viewOnce": view_once
        }), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/file-info/<file_id>', methods=['GET'])
def file_info(file_id):
    info = file_store.get(file_id)
    if not info:
        fp = os.path.join(UPLOAD_DIR, file_id)
        if os.path.exists(fp):
            return jsonify({"fileName": "file", "fileSize": fmt_size(os.path.getsize(fp)), "expiresIn": "1 Hour"})
        return jsonify({"error": "not found or expired"}), 404
    refresh_ttl(info)
    return jsonify({
        "fileName": info.get("fileName", "file"),
        "fileSize": info.get("fileSize", ""),
        "uploaded": info.get("uploaded", ""),
        "protected": bool(info.get("password")),
        "viewOnce": info.get("view_once", False)
    })


def _bg_delete_view_once(fid):
    time.sleep(3)
    info = file_store.pop(fid, None)
    if info:
        fp = info.get("path", "")
        if fp and os.path.exists(fp):
            try: os.remove(fp)
            except: pass
    print(f"🔥 [View Once] Deleted file from web disk after download: {fid} (Remains untouched in Telegram!)")


# ---- Clean Download Routes (/d/<file_id>, /dl/<file_id>, /api/file/<file_id>) ----
@app.route('/d/<file_id>', methods=['GET'])
@app.route('/dl/<file_id>', methods=['GET'])
@app.route('/api/file/<file_id>', methods=['GET'])
def get_file(file_id):
    info = file_store.get(file_id)
    if not info: return jsonify({"error": "not found or expired"}), 404
    refresh_ttl(info)

    pwd = request.args.get("pwd", "").strip()
    if info.get("password") and pwd != info["password"]:
        return f'''<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Protected File — MeetLink</title><style>body{{background:#04040c;color:#fff;font-family:system-ui,sans-serif;height:100vh;display:flex;align-items:center;justify-content:center;text-align:center;padding:20px}}.card{{background:rgba(255,255,255,0.03);border:1px solid rgba(0,240,255,0.4);padding:40px;border-radius:24px;max-width:420px;width:100%;box-shadow:0 0 40px rgba(0,240,255,0.15)}}input{{width:100%;padding:14px;background:#0a0a1f;border:1px solid rgba(0,240,255,0.3);border-radius:12px;color:#fff;font-size:1.1rem;text-align:center;margin:20px 0;outline:none}}button{{width:100%;padding:14px;background:linear-gradient(135deg,#00f0ff,#0070ff);color:#000;border:none;border-radius:12px;font-weight:700;font-size:1.1rem;cursor:pointer;box-shadow:0 0 20px rgba(0,240,255,0.4)}}</style></head>
<body><div class="card"><div style="font-size:3.5rem;margin-bottom:12px;">🔒</div><h2>Protected File</h2><p style="color:#a0a0cc;font-size:0.9rem;margin-top:4px;">This file is password protected by the sender.</p>
<form method="GET"><input type="password" name="pwd" placeholder="Enter 4-digit PIN / Password" required autofocus><button type="submit">Unlock & Download</button></form></div></body></html>''', 401

    if info.get("telegram_direct"):
        finfo = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getFile", params={"file_id": info["telegram_file_id"]}, timeout=15).json()
        if finfo.get("ok"):
            fresh_path = finfo["result"]["file_path"]
            tg_cdn_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{fresh_path}"
            if info.get("view_once"):
                info["downloads"] = info.get("downloads", 0) + 1
                executor.submit(_bg_delete_view_once, file_id)
            return redirect(tg_cdn_url)
        elif pyro_client and pyro_client.is_connected:
            print(f"🚀 [Pyrogram MTProto Stream] Streaming direct file: {file_id}")
            if info.get("view_once"):
                info["downloads"] = info.get("downloads", 0) + 1
                executor.submit(_bg_delete_view_once, file_id)
            
            file_size = int(info.get("fileSizeBytes") or 0)
            mime_type = info.get("mimeType", "application/octet-stream")
            file_name = info.get("fileName", "file")
            disp = "attachment" if (request.path.startswith('/d/') or request.path.startswith('/dl/')) else "inline"
            
            range_header = request.headers.get('Range', None)
            if range_header and file_size > 0:
                byte1, byte2 = 0, None
                match = re.search(r'bytes=(\d+)-(\d*)', range_header)
                if match:
                    g1, g2 = match.groups()
                    if g1: byte1 = int(g1)
                    if g2: byte2 = int(g2)
                
                byte2 = byte2 if (byte2 is not None and byte2 < file_size) else (file_size - 1)
                length = byte2 - byte1 + 1
                
                # 🛠️ TELEGRAM MTPROTO [400 OFFSET_INVALID] FIX: Pyrogram offset takes CHUNK COUNT (1MB chunks), not byte count!
                chunk_size = 1048576
                chunk_index = byte1 // chunk_size
                skip_bytes = byte1 % chunk_size
                
                def generate_range_stream():
                    bytes_sent = 0
                    skip = skip_bytes
                    try:
                        for chunk in pyro_client.stream_media(info["telegram_file_id"], offset=chunk_index):
                            if skip > 0:
                                if len(chunk) <= skip:
                                    skip -= len(chunk)
                                    continue
                                else:
                                    chunk = chunk[skip:]
                                    skip = 0
                            
                            if bytes_sent + len(chunk) >= length:
                                yield chunk[:length - bytes_sent]
                                break
                            else:
                                yield chunk
                                bytes_sent += len(chunk)
                    except Exception as e:
                        print(f"⚠️ MTProto stream note: {e}")
                
                from flask import Response
                resp = Response(generate_range_stream(), status=206, mimetype=mime_type)
                resp.headers.add('Content-Range', f'bytes {byte1}-{byte2}/{file_size}')
                resp.headers.add('Accept-Ranges', 'bytes')
                resp.headers.add('Content-Length', str(length))
                resp.headers.add('Content-Disposition', f'{disp}; filename="{file_name}"')
                return resp
            else:
                def generate_full_stream():
                    for chunk in pyro_client.stream_media(info["telegram_file_id"], limit=0):
                        yield chunk
                
                from flask import Response
                resp = Response(generate_full_stream(), status=200, mimetype=mime_type)
                resp.headers.add('Accept-Ranges', 'bytes')
                if file_size > 0:
                    resp.headers.add('Content-Length', str(file_size))
                resp.headers.add('Content-Disposition', f'{disp}; filename="{file_name}"')
                return resp
        
        return jsonify({"error": "File > 20MB sent via Bot Chat requires API_ID & API_HASH in server config for Pyrogram MTProto streaming! Please configure them or upload via Website."}), 400

    fp = info.get("path", os.path.join(UPLOAD_DIR, file_id))
    if not os.path.exists(fp): return jsonify({"error": "file missing on disk"}), 404

    if info.get("view_once"):
        info["downloads"] = info.get("downloads", 0) + 1
        executor.submit(_bg_delete_view_once, file_id)

    return send_file(fp, download_name=info.get("fileName", "file"), as_attachment=True, conditional=True)


# ---- Clean View/Preview Routes (/v/<file_id>, /share/<file_id>, /api/file-preview/<file_id>) ----
@app.route('/v/<file_id>', methods=['GET'])
@app.route('/share/<file_id>', methods=['GET'])
@app.route('/api/file-preview/<file_id>', methods=['GET'])
def file_preview_page(file_id):
    info = file_store.get(file_id)
    if not info:
        return '''<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>File Expired or Not Found</title><style>body{background:#04040c;color:#ff2d75;font-family:system-ui,sans-serif;height:100vh;display:flex;align-items:center;justify-content:center;text-align:center;padding:20px}.card{background:rgba(255,255,255,0.03);border:1px solid rgba(255,45,117,0.3);padding:40px;border-radius:24px;max-width:420px}</style></head>
<body><div class="card"><div style="font-size:3.5rem;margin-bottom:16px;">⚠️</div><h2 style="color:#fff;margin-bottom:8px;">Link Expired</h2><p style="color:#a0a0cc;font-size:0.95rem;">This shareable link or room has automatically expired after 1 hour of inactivity to protect server load.</p></div></body></html>''', 404

    refresh_ttl(info)
    pwd = request.args.get("pwd", "").strip()
    if info.get("password") and pwd != info["password"]:
        return f'''<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Protected File — MeetLink</title><style>body{{background:#04040c;color:#fff;font-family:system-ui,sans-serif;height:100vh;display:flex;align-items:center;justify-content:center;text-align:center;padding:20px}}.card{{background:rgba(255,255,255,0.03);border:1px solid rgba(0,240,255,0.4);padding:40px;border-radius:24px;max-width:420px;width:100%;box-shadow:0 0 40px rgba(0,240,255,0.15)}}input{{width:100%;padding:14px;background:#0a0a1f;border:1px solid rgba(0,240,255,0.3);border-radius:12px;color:#fff;font-size:1.1rem;text-align:center;margin:20px 0;outline:none}}button{{width:100%;padding:14px;background:linear-gradient(135deg,#00f0ff,#0070ff);color:#000;border:none;border-radius:12px;font-weight:700;font-size:1.1rem;cursor:pointer;box-shadow:0 0 20px rgba(0,240,255,0.4)}}</style></head>
<body><div class="card"><div style="font-size:3.5rem;margin-bottom:12px;">🔒</div><h2>Protected File View</h2><p style="color:#a0a0cc;font-size:0.9rem;margin-top:4px;">This file is password protected by the sender.</p>
<form method="GET"><input type="password" name="pwd" placeholder="Enter 4-digit PIN / Password" required autofocus><button type="submit">Unlock & Preview</button></form></div></body></html>''', 401

    file_url_dl = f"/d/{file_id}" + (f"?pwd={pwd}" if pwd else "")
    file_url_share = f"/v/{file_id}" + (f"?pwd={pwd}" if pwd else "")
    file_url_raw = f"/api/file/{file_id}" + (f"?pwd={pwd}" if pwd else "")
    fn = info.get("fileName", "file")
    fs = info.get("fileSize", "")
    ext = fn.split('.').pop().lower()
    image_exts = ['jpg','jpeg','png','gif','webp','svg','bmp','ico']
    video_exts = ['mp4','webm','mkv','avi','mov']
    audio_exts = ['mp3','wav','ogg','flac','aac']

    if ext in image_exts:
        preview = f'<img src="{file_url_raw}" style="max-width:100%;max-height:70vh;border-radius:12px;object-fit:contain;" alt="{fn}">'
    elif ext in video_exts:
        preview = f'<video src="{file_url_raw}" controls autoplay style="max-width:100%;max-height:70vh;border-radius:12px;"></video>'
    elif ext in audio_exts:
        preview = f'<div style="text-align:center;padding:60px;"><div style="font-size:4rem;margin-bottom:20px;">🎵</div><audio src="{file_url_raw}" controls autoplay style="width:100%;max-width:400px;"></audio></div>'
    elif ext == 'pdf':
        preview = f'<iframe src="{file_url_raw}" style="width:100%;height:70vh;border:none;border-radius:12px;"></iframe>'
    else:
        preview = f'<div style="text-align:center;padding:60px;"><div style="font-size:4rem;margin-bottom:20px;">📄</div><div style="color:#fff;font-size:1.3rem;font-weight:700;margin-bottom:8px;">{fn}</div><div style="color:#8888bb;margin-bottom:24px;">{fs}</div><a href="{file_url_dl}" download="{fn}" style="padding:12px 28px;background:linear-gradient(135deg,#00f0ff,#0070ff);color:#000;font-weight:700;border-radius:12px;text-decoration:none;display:inline-block;box-shadow:0 0 20px rgba(0,240,255,0.4);">⬇ Instant Download</a></div>'

    return f'''<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{fn} — MeetLink Share</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:#04040c;color:#e8e8ff;font-family:'Inter',system-ui,-apple-system,sans-serif;min-height:100vh;display:flex;flex-direction:column;align-items:center;padding:40px 20px;background-image:radial-gradient(circle at 50% 0%,rgba(177,77,255,0.15) 0%,transparent 70%)}}
.container{{width:100%;max-width:920px;display:flex;flex-direction:column;gap:24px}}
.top-bar{{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:16px;background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.08);padding:20px 24px;border-radius:20px;backdrop-filter:blur(10px)}}
.brand{{display:flex;align-items:center;gap:12px;font-weight:800;font-size:1.25rem;background:linear-gradient(135deg,#fff,#b14dff);-webkit-background-clip:text;-webkit-text-fill-color:transparent}}
.brand-icon{{width:42px;height:42px;border-radius:12px;background:linear-gradient(135deg,#b14dff,#00f0ff);display:flex;align-items:center;justify-content:center;color:#fff;font-size:1.2rem;box-shadow:0 8px 20px rgba(177,77,255,0.3);-webkit-text-fill-color:initial}}
.file-meta{{display:flex;flex-direction:column;gap:4px;flex:1;min-width:200px;margin-left:8px}}
.file-name{{font-weight:700;font-size:1.1rem;color:#fff;word-break:break-all}}
.file-badges{{display:flex;align-items:center;gap:10px;flex-wrap:wrap}}
.badge{{background:rgba(255,255,255,0.06);padding:4px 10px;border-radius:8px;font-size:0.8rem;color:#a0a0cc;border:1px solid rgba(255,255,255,0.05);display:inline-flex;align-items:center;gap:6px}}
.badge-ttl{{color:#00f0ff;border-color:rgba(0,240,255,0.2);background:rgba(0,240,255,0.08)}}
.actions{{display:flex;align-items:center;gap:12px;flex-wrap:wrap}}
.btn{{padding:12px 24px;border-radius:12px;font-weight:600;font-size:0.95rem;text-decoration:none;cursor:pointer;display:inline-flex;align-items:center;gap:8px;transition:all 0.2s ease;border:none}}
.btn-dl{{background:linear-gradient(135deg,#b14dff,#7020ff);color:#fff;box-shadow:0 8px 25px rgba(177,77,255,0.35)}}
.btn-dl:hover{{transform:translateY(-2px);box-shadow:0 12px 30px rgba(177,77,255,0.5)}}
.btn-copy{{background:rgba(255,255,255,0.08);color:#fff;border:1px solid rgba(255,255,255,0.1)}}
.btn-copy:hover{{background:rgba(255,255,255,0.15)}}
.preview-box{{width:100%;background:rgba(10,10,25,0.8);border:1px solid rgba(255,255,255,0.08);border-radius:24px;display:flex;align-items:center;justify-content:center;overflow:hidden;min-height:380px;padding:20px;box-shadow:0 20px 50px rgba(0,0,0,0.5)}}
.links-section{{background:rgba(255,255,255,0.02);border:1px solid rgba(255,255,255,0.06);border-radius:16px;padding:18px 22px;display:flex;flex-direction:column;gap:12px}}
.link-row{{display:flex;align-items:center;justify-content:space-between;gap:12px;background:rgba(0,0,0,0.3);padding:12px 16px;border-radius:12px;border:1px solid rgba(255,255,255,0.05)}}
.link-url{{font-family:monospace;font-size:0.88rem;color:#00f0ff;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1}}
</style></head><body>
<div class="container">
<div class="top-bar">
<div style="display:flex;align-items:center;gap:12px;flex:1;min-width:260px;">
<div class="brand"><div class="brand-icon">⚡</div>MeetLink</div>
<div class="file-meta">
<div class="file-name">{fn}</div>
<div class="file-badges">
<span class="badge">📦 {fs}</span>
<span class="badge badge-ttl">⏱️ Auto-expires in 1 Hour of inactivity</span>
</div>
</div>
</div>
<div class="actions">
<button onclick="copyLink(window.location.origin + '{file_url_share}', this)" class="btn btn-copy">📋 Copy Link</button>
<a href="{file_url_dl}" download="{fn}" class="btn btn-dl">⬇ Download File</a>
</div>
</div>
<div class="preview-box">{preview}</div>
<div class="links-section">
<div style="font-size:0.88rem;font-weight:700;color:#a0a0cc;">🔗 Shareable Links (High Speed & Direct)</div>
<div class="link-row">
<span style="color:#8888bb;font-size:0.85rem;width:100px;font-weight:600;">View Link:</span>
<span class="link-url" id="val-share"></span>
<button onclick="copyLink(document.getElementById('val-share').innerText, this)" style="background:none;border:none;color:#b14dff;cursor:pointer;font-weight:700;font-size:0.85rem;">Copy</button>
</div>
<div class="link-row">
<span style="color:#8888bb;font-size:0.85rem;width:100px;font-weight:600;">Direct DL:</span>
<span class="link-url" id="val-dl"></span>
<button onclick="copyLink(document.getElementById('val-dl').innerText, this)" style="background:none;border:none;color:#00f0ff;cursor:pointer;font-weight:700;font-size:0.85rem;">Copy</button>
</div>
</div>
</div>
<script>
document.getElementById('val-share').innerText = window.location.origin + '{file_url_share}';
document.getElementById('val-dl').innerText = window.location.origin + '{file_url_dl}';
function copyLink(url, btn) {{
    navigator.clipboard.writeText(url).then(() => {{
        const oldText = btn.innerText;
        btn.innerText = "✅ Copied!";
        btn.style.color = "#00f0ff";
        setTimeout(() => {{ btn.innerText = oldText; btn.style.color = ""; }}, 2000);
    }});
}}
</script>
</body></html>'''



# ============ SECURE ENCRYPTED POSTGRESQL LAYER (Telegram logger untouched) ============
# Enabled only when DATABASE_URL and DATA_ENCRYPTION_KEY are set in Koyeb env.
DATABASE_URL = os.environ.get('DATABASE_URL', '').strip()
DATA_ENCRYPTION_KEY = os.environ.get('DATA_ENCRYPTION_KEY', '').strip()
try:
    import psycopg2
    import psycopg2.extras
    import bcrypt
    from cryptography.fernet import Fernet
    _FERNET = Fernet(DATA_ENCRYPTION_KEY.encode()) if DATA_ENCRYPTION_KEY else None
except Exception as _secure_err:
    psycopg2 = None; bcrypt = None; _FERNET = None
    print(f"⚠️ [Secure PostgreSQL] imports unavailable: {_secure_err}")
SECURE_DB_ENABLED = bool(DATABASE_URL and DATA_ENCRYPTION_KEY and psycopg2 and bcrypt and _FERNET)

def sdb_conn():
    return psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor, sslmode='require')

def sdb_enc(v):
    if v is None: return None
    return _FERNET.encrypt(str(v).encode('utf-8')).decode('utf-8')

def sdb_dec(v):
    if v is None: return None
    try: return _FERNET.decrypt(str(v).encode('utf-8')).decode('utf-8')
    except Exception: return '[decrypt-error]'

def sdb_hash(pw):
    return bcrypt.hashpw(pw.encode('utf-8'), bcrypt.gensalt(rounds=12)).decode('utf-8')

def sdb_verify(pw, hashed):
    try: return bcrypt.checkpw(pw.encode('utf-8'), hashed.encode('utf-8'))
    except Exception: return False

def sdb_init():
    if not SECURE_DB_ENABLED:
        print('⚠️ [Secure PostgreSQL] Disabled. Add DATABASE_URL + DATA_ENCRYPTION_KEY env vars to enable encrypted DB.')
        return
    try:
        with sdb_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                CREATE TABLE IF NOT EXISTS users (id BIGSERIAL PRIMARY KEY, username VARCHAR(30) UNIQUE NOT NULL, password_hash TEXT NOT NULL, display_name VARCHAR(80) NOT NULL, created_at TIMESTAMPTZ DEFAULT NOW(), last_seen TIMESTAMPTZ DEFAULT NOW());
                CREATE TABLE IF NOT EXISTS profiles (user_id BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE, about_enc TEXT, photo_enc TEXT, updated_at TIMESTAMPTZ DEFAULT NOW());
                CREATE TABLE IF NOT EXISTS friends (id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE, friend_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE, status VARCHAR(20) DEFAULT 'pending', created_at TIMESTAMPTZ DEFAULT NOW(), updated_at TIMESTAMPTZ DEFAULT NOW(), UNIQUE(user_id, friend_id));
                CREATE TABLE IF NOT EXISTS conversations (id BIGSERIAL PRIMARY KEY, type VARCHAR(20) NOT NULL DEFAULT 'direct', title_enc TEXT, direct_key VARCHAR(90) UNIQUE, created_by BIGINT REFERENCES users(id) ON DELETE SET NULL, created_at TIMESTAMPTZ DEFAULT NOW(), updated_at TIMESTAMPTZ DEFAULT NOW());
                CREATE TABLE IF NOT EXISTS conversation_members (conversation_id BIGINT REFERENCES conversations(id) ON DELETE CASCADE, user_id BIGINT REFERENCES users(id) ON DELETE CASCADE, role VARCHAR(20) DEFAULT 'member', muted BOOLEAN DEFAULT FALSE, pinned BOOLEAN DEFAULT FALSE, archived BOOLEAN DEFAULT FALSE, joined_at TIMESTAMPTZ DEFAULT NOW(), PRIMARY KEY(conversation_id, user_id));
                CREATE TABLE IF NOT EXISTS messages (id BIGSERIAL PRIMARY KEY, client_id VARCHAR(140), conversation_id BIGINT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE, sender_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE, type VARCHAR(30) DEFAULT 'text', body_enc TEXT, reply_payload_enc TEXT, edited BOOLEAN DEFAULT FALSE, deleted_for_everyone BOOLEAN DEFAULT FALSE, reactions_enc TEXT, created_at TIMESTAMPTZ DEFAULT NOW(), updated_at TIMESTAMPTZ DEFAULT NOW());
                CREATE TABLE IF NOT EXISTS message_receipts (message_id BIGINT REFERENCES messages(id) ON DELETE CASCADE, user_id BIGINT REFERENCES users(id) ON DELETE CASCADE, status VARCHAR(20) DEFAULT 'sent', updated_at TIMESTAMPTZ DEFAULT NOW(), PRIMARY KEY(message_id, user_id));
                CREATE TABLE IF NOT EXISTS call_logs (id BIGSERIAL PRIMARY KEY, caller_id BIGINT REFERENCES users(id) ON DELETE SET NULL, receiver_id BIGINT REFERENCES users(id) ON DELETE SET NULL, conversation_id BIGINT REFERENCES conversations(id) ON DELETE SET NULL, call_type VARCHAR(20), status VARCHAR(30), started_at TIMESTAMPTZ DEFAULT NOW(), ended_at TIMESTAMPTZ, duration_seconds INTEGER DEFAULT 0);
                CREATE TABLE IF NOT EXISTS statuses (id BIGSERIAL PRIMARY KEY, user_id BIGINT REFERENCES users(id) ON DELETE CASCADE, type VARCHAR(20) DEFAULT 'text', text_enc TEXT, media_enc TEXT, created_at TIMESTAMPTZ DEFAULT NOW(), expires_at TIMESTAMPTZ DEFAULT (NOW() + INTERVAL '24 hours'));
                CREATE TABLE IF NOT EXISTS push_subscriptions (id BIGSERIAL PRIMARY KEY, user_id BIGINT REFERENCES users(id) ON DELETE CASCADE, endpoint TEXT NOT NULL, p256dh TEXT, auth TEXT, created_at TIMESTAMPTZ DEFAULT NOW(), UNIQUE(user_id, endpoint));
                CREATE INDEX IF NOT EXISTS idx_messages_conversation_created ON messages(conversation_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_friends_user_status ON friends(user_id, status);
                CREATE INDEX IF NOT EXISTS idx_statuses_user_expires ON statuses(user_id, expires_at);
                """)
        print('🔐 [Secure PostgreSQL] Connected. Encrypted schema verified.')
    except Exception as e:
        print(f'❌ [Secure PostgreSQL] init failed: {e}')

def sdb_user(cur, username):
    cur.execute('SELECT * FROM users WHERE username=%s', (username,)); return cur.fetchone()

def sdb_public_user(u): return {'id': u['id'], 'username': u['username'], 'display_name': u['display_name']}
def sdb_online(last_seen):
    try: return (datetime.now(last_seen.tzinfo) - last_seen).total_seconds() < 35
    except Exception: return False

def sdb_direct(cur, a, b):
    users = sorted([a, b]); key = users[0] + ':' + users[1]
    u1 = sdb_user(cur, users[0]); u2 = sdb_user(cur, users[1])
    if not u1 or not u2: return None
    cur.execute('SELECT * FROM conversations WHERE direct_key=%s', (key,)); c = cur.fetchone()
    if not c:
        cur.execute("INSERT INTO conversations(type,direct_key,created_by) VALUES('direct',%s,%s) RETURNING *", (key, u1['id'])); c = cur.fetchone()
        cur.execute('INSERT INTO conversation_members(conversation_id,user_id) VALUES(%s,%s) ON CONFLICT DO NOTHING', (c['id'], u1['id']))
        cur.execute('INSERT INTO conversation_members(conversation_id,user_id) VALUES(%s,%s) ON CONFLICT DO NOTHING', (c['id'], u2['id']))
    return c

def secure_auth_register():
    d=request.json or {}; username=d.get('username','').strip().lower(); password=d.get('password','').strip(); display=d.get('display_name','').strip()
    if not username or not password or not display: return jsonify({'error':'All fields are required'}),400
    if not re.match(r'^[a-zA-Z0-9_]{3,20}$', username): return jsonify({'error':'Invalid username format'}),400
    try:
        with sdb_conn() as conn:
            with conn.cursor() as cur:
                cur.execute('INSERT INTO users(username,password_hash,display_name,last_seen) VALUES(%s,%s,%s,NOW()) RETURNING id,username,display_name', (username, sdb_hash(password), display)); u=cur.fetchone()
                cur.execute('INSERT INTO profiles(user_id,about_enc) VALUES(%s,%s) ON CONFLICT DO NOTHING', (u['id'], sdb_enc('Hey there! I am using WhatsMeet')))
        return jsonify({'status':'ok','user':sdb_public_user(u),'db':'postgres_encrypted'}),200
    except Exception as e:
        if 'duplicate' in str(e).lower() or 'unique' in str(e).lower(): return jsonify({'error':'Cyber ID already exists! Please try another one.'}),400
        return jsonify({'error':str(e)}),500

def secure_auth_login():
    d=request.json or {}; username=d.get('username','').strip().lower(); password=d.get('password','').strip()
    if not username or not password: return jsonify({'error':'Username and password are required'}),400
    with sdb_conn() as conn:
        with conn.cursor() as cur:
            u=sdb_user(cur, username)
            if u and sdb_verify(password, u['password_hash']):
                cur.execute('UPDATE users SET last_seen=NOW() WHERE id=%s', (u['id'],))
                return jsonify({'status':'ok','user':sdb_public_user(u),'db':'postgres_encrypted'}),200
    return jsonify({'error':'Invalid Cyber ID or Password'}),401

def secure_user_heartbeat():
    username=(request.json or {}).get('username','').strip().lower()
    if username:
        with sdb_conn() as conn:
            with conn.cursor() as cur: cur.execute('UPDATE users SET last_seen=NOW() WHERE username=%s', (username,))
    return jsonify({'status':'ok'}),200

def secure_users_search():
    q=request.args.get('query','').strip().lower(); me=request.args.get('username','').strip().lower(); out=[]
    if not q: return jsonify({'results':[]}),200
    with sdb_conn() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT username,display_name,last_seen FROM users WHERE (username ILIKE %s OR display_name ILIKE %s) AND username<>%s LIMIT 10', (f'%{q}%', f'%{q}%', me))
            for r in cur.fetchall():
                state='none'
                cur.execute("SELECT f.status FROM friends f JOIN users u1 ON f.user_id=u1.id JOIN users u2 ON f.friend_id=u2.id WHERE u1.username=%s AND u2.username=%s", (me, r['username'])); fr=cur.fetchone()
                if fr: state='friends' if fr['status']=='accepted' else 'sent'
                else:
                    cur.execute("SELECT f.status FROM friends f JOIN users u1 ON f.user_id=u1.id JOIN users u2 ON f.friend_id=u2.id WHERE u1.username=%s AND u2.username=%s AND f.status='pending'", (r['username'], me))
                    if cur.fetchone(): state='received'
                out.append({'username':r['username'],'display_name':r['display_name'],'is_online':sdb_online(r['last_seen']),'status_state':state})
    return jsonify({'results':out}),200

def secure_friends_add():
    d=request.json or {}; me=d.get('username','').strip().lower(); other=d.get('friend_username','').strip().lower()
    if not me or not other: return jsonify({'error':'Both usernames are required'}),400
    if me==other: return jsonify({'error':'You cannot add yourself as friend'}),400
    with sdb_conn() as conn:
        with conn.cursor() as cur:
            u1=sdb_user(cur, me); u2=sdb_user(cur, other)
            if not u1 or not u2: return jsonify({'error':'User not found'}),404
            cur.execute('SELECT status FROM friends WHERE user_id=%s AND friend_id=%s', (u1['id'],u2['id'])); ex=cur.fetchone()
            if ex: return jsonify({'error':'You are already friends!' if ex['status']=='accepted' else 'Friend request already sent!'}),400
            cur.execute('SELECT status FROM friends WHERE user_id=%s AND friend_id=%s', (u2['id'],u1['id'])); rev=cur.fetchone()
            if rev and rev['status']=='pending':
                cur.execute("UPDATE friends SET status='accepted',updated_at=NOW() WHERE user_id=%s AND friend_id=%s", (u2['id'],u1['id']))
                cur.execute("INSERT INTO friends(user_id,friend_id,status) VALUES(%s,%s,'accepted') ON CONFLICT(user_id,friend_id) DO UPDATE SET status='accepted',updated_at=NOW()", (u1['id'],u2['id']))
                return jsonify({'status':'ok','message':'Mutual friend request accepted! You are now friends.'}),200
            cur.execute("INSERT INTO friends(user_id,friend_id,status) VALUES(%s,%s,'pending')", (u1['id'],u2['id']))
    return jsonify({'status':'ok','message':'Friend request sent!'}),200

def secure_friends_requests_pending():
    me=request.args.get('username','').strip().lower()
    with sdb_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT u.username,u.display_name FROM friends f JOIN users u ON f.user_id=u.id JOIN users me ON f.friend_id=me.id WHERE me.username=%s AND f.status='pending'", (me,))
            out=[dict(x) for x in cur.fetchall()]
    return jsonify({'requests':out}),200

def secure_friends_accept_request():
    d=request.json or {}; me=d.get('username','').strip().lower(); sender=d.get('sender_username','').strip().lower()
    with sdb_conn() as conn:
        with conn.cursor() as cur:
            u1=sdb_user(cur, me); u2=sdb_user(cur, sender)
            if not u1 or not u2: return jsonify({'error':'User not found'}),404
            cur.execute("UPDATE friends SET status='accepted',updated_at=NOW() WHERE user_id=%s AND friend_id=%s", (u2['id'],u1['id']))
            cur.execute("INSERT INTO friends(user_id,friend_id,status) VALUES(%s,%s,'accepted') ON CONFLICT(user_id,friend_id) DO UPDATE SET status='accepted',updated_at=NOW()", (u1['id'],u2['id']))
    return jsonify({'status':'ok','message':'Friend request accepted!'}),200

def secure_friends_decline_request():
    d=request.json or {}; me=d.get('username','').strip().lower(); sender=d.get('sender_username','').strip().lower()
    with sdb_conn() as conn:
        with conn.cursor() as cur:
            u1=sdb_user(cur, me); u2=sdb_user(cur, sender)
            if u1 and u2: cur.execute("DELETE FROM friends WHERE user_id=%s AND friend_id=%s AND status='pending'", (u2['id'],u1['id']))
    return jsonify({'status':'ok','message':'Friend request declined!'}),200

def secure_friends_remove():
    d=request.json or {}; me=d.get('username','').strip().lower(); other=d.get('friend_username','').strip().lower()
    with sdb_conn() as conn:
        with conn.cursor() as cur:
            u1=sdb_user(cur, me); u2=sdb_user(cur, other)
            if u1 and u2: cur.execute('DELETE FROM friends WHERE (user_id=%s AND friend_id=%s) OR (user_id=%s AND friend_id=%s)', (u1['id'],u2['id'],u2['id'],u1['id']))
    return jsonify({'status':'ok','message':'Friend removed successfully!'}),200

def secure_friends_list():
    me=request.args.get('username','').strip().lower(); out=[]
    with sdb_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT u.username,u.display_name,u.last_seen,p.about_enc,p.photo_enc FROM friends f JOIN users self ON f.user_id=self.id JOIN users u ON f.friend_id=u.id LEFT JOIN profiles p ON p.user_id=u.id WHERE self.username=%s AND f.status='accepted' ORDER BY u.display_name", (me,))
            for r in cur.fetchall(): out.append({'username':r['username'],'display_name':r['display_name'],'is_online':sdb_online(r['last_seen']),'about':sdb_dec(r.get('about_enc')) or '', 'photo':sdb_dec(r.get('photo_enc')) or ''})
    return jsonify({'friends':out}),200

@app.route('/api/profile/<username>', methods=['GET'])
def secure_profile_get(username):
    if not SECURE_DB_ENABLED: return jsonify({'error':'Secure DB not enabled'}),503
    with sdb_conn() as conn:
        with conn.cursor() as cur:
            u=sdb_user(cur, username.strip().lower())
            if not u: return jsonify({'error':'not found'}),404
            cur.execute('SELECT about_enc,photo_enc FROM profiles WHERE user_id=%s', (u['id'],)); pr=cur.fetchone() or {}
    return jsonify({'username':u['username'],'display_name':u['display_name'],'about':sdb_dec(pr.get('about_enc')) or 'Hey there! I am using WhatsMeet','photo':sdb_dec(pr.get('photo_enc')) or ''}),200

@app.route('/api/profile/update', methods=['POST'])
def secure_profile_update():
    if not SECURE_DB_ENABLED: return jsonify({'error':'Secure DB not enabled'}),503
    d=request.json or {}; username=d.get('username','').strip().lower()
    with sdb_conn() as conn:
        with conn.cursor() as cur:
            u=sdb_user(cur, username)
            if not u: return jsonify({'error':'user not found'}),404
            cur.execute('INSERT INTO profiles(user_id,about_enc,photo_enc,updated_at) VALUES(%s,%s,%s,NOW()) ON CONFLICT(user_id) DO UPDATE SET about_enc=COALESCE(EXCLUDED.about_enc,profiles.about_enc), photo_enc=COALESCE(EXCLUDED.photo_enc,profiles.photo_enc), updated_at=NOW()', (u['id'], sdb_enc(d.get('about')) if d.get('about') is not None else None, sdb_enc(d.get('photo')) if d.get('photo') is not None else None))
    return jsonify({'status':'ok'}),200

@app.route('/api/chats/direct', methods=['POST'])
def secure_chats_direct():
    if not SECURE_DB_ENABLED: return jsonify({'error':'Secure DB not enabled'}),503
    d=request.json or {}; me=d.get('username','').strip().lower(); other=d.get('friend_username','').strip().lower()
    with sdb_conn() as conn:
        with conn.cursor() as cur:
            c=sdb_direct(cur, me, other)
            if not c: return jsonify({'error':'user not found'}),404
    return jsonify({'status':'ok','conversation_id':c['id']}),200

@app.route('/api/messages/send', methods=['POST'])
def secure_messages_send():
    if not SECURE_DB_ENABLED: return jsonify({'error':'Secure DB not enabled'}),503
    d=request.json or {}; sender=d.get('sender','').strip().lower(); receiver=d.get('receiver','').strip().lower(); text=d.get('message') or d.get('text') or ''
    with sdb_conn() as conn:
        with conn.cursor() as cur:
            su=sdb_user(cur, sender); ru=sdb_user(cur, receiver); c=sdb_direct(cur, sender, receiver)
            if not su or not ru or not c: return jsonify({'error':'user not found'}),404
            cur.execute('INSERT INTO messages(client_id,conversation_id,sender_id,type,body_enc,reply_payload_enc) VALUES(%s,%s,%s,%s,%s,%s) RETURNING id,created_at', (d.get('client_id'), c['id'], su['id'], d.get('type','text'), sdb_enc(text), sdb_enc(d.get('reply')) if d.get('reply') else None)); m=cur.fetchone()
            cur.execute("INSERT INTO message_receipts(message_id,user_id,status) VALUES(%s,%s,'sent') ON CONFLICT DO NOTHING", (m['id'], ru['id']))
            cur.execute('UPDATE conversations SET updated_at=NOW() WHERE id=%s', (c['id'],))
    try: send_telegram_message(f"💾 <b>DB MESSAGE SAVED</b>\n👤 From: <code>{sender}</code>\n➡️ To: <code>{receiver}</code>\n📝 Message: <code>{(text[:500]+'...') if len(text)>500 else text}</code>")
    except Exception: pass
    return jsonify({'status':'ok','message_id':m['id'],'conversation_id':c['id'],'created_at':str(m['created_at'])}),200

@app.route('/api/messages/history', methods=['GET'])
def secure_messages_history():
    if not SECURE_DB_ENABLED: return jsonify({'error':'Secure DB not enabled'}),503
    me=request.args.get('username','').strip().lower(); other=request.args.get('friend_username','').strip().lower(); limit=min(int(request.args.get('limit',50)),200)
    with sdb_conn() as conn:
        with conn.cursor() as cur:
            c=sdb_direct(cur, me, other)
            if not c: return jsonify({'messages':[]}),200
            cur.execute('SELECT m.*,u.username sender,u.display_name FROM messages m JOIN users u ON m.sender_id=u.id WHERE m.conversation_id=%s ORDER BY m.created_at DESC LIMIT %s', (c['id'],limit)); rows=list(reversed(cur.fetchall()))
    out=[]
    for r in rows: out.append({'id':r['id'],'client_id':r['client_id'],'sender':r['sender'],'display_name':r['display_name'],'type':r['type'],'text':'This message was deleted' if r['deleted_for_everyone'] else sdb_dec(r['body_enc']),'edited':r['edited'],'deleted':r['deleted_for_everyone'],'reply':sdb_dec(r.get('reply_payload_enc')),'reactions':sdb_dec(r.get('reactions_enc')),'created_at':str(r['created_at'])})
    return jsonify({'conversation_id':c['id'],'messages':out}),200

@app.route('/api/messages/edit', methods=['POST'])
def secure_messages_edit():
    if not SECURE_DB_ENABLED: return jsonify({'error':'Secure DB not enabled'}),503
    d=request.json or {}
    with sdb_conn() as conn:
        with conn.cursor() as cur: cur.execute('UPDATE messages SET body_enc=%s,edited=TRUE,updated_at=NOW() WHERE id=%s', (sdb_enc(d.get('text','')), d.get('message_id')))
    return jsonify({'status':'ok'}),200

@app.route('/api/messages/delete', methods=['POST'])
def secure_messages_delete():
    if not SECURE_DB_ENABLED: return jsonify({'error':'Secure DB not enabled'}),503
    mid=(request.json or {}).get('message_id')
    with sdb_conn() as conn:
        with conn.cursor() as cur: cur.execute('UPDATE messages SET deleted_for_everyone=TRUE,body_enc=%s,updated_at=NOW() WHERE id=%s', (sdb_enc('This message was deleted'), mid))
    return jsonify({'status':'ok'}),200

@app.route('/api/messages/reaction', methods=['POST'])
def secure_messages_reaction():
    if not SECURE_DB_ENABLED: return jsonify({'error':'Secure DB not enabled'}),503
    d=request.json or {}
    with sdb_conn() as conn:
        with conn.cursor() as cur: cur.execute('UPDATE messages SET reactions_enc=%s,updated_at=NOW() WHERE id=%s', (sdb_enc(d.get('emoji') or d.get('reactions') or ''), d.get('message_id')))
    return jsonify({'status':'ok'}),200

@app.route('/api/calls/log', methods=['POST'])
def secure_calls_log():
    if not SECURE_DB_ENABLED: return jsonify({'error':'Secure DB not enabled'}),503
    d=request.json or {}; caller=d.get('caller','').strip().lower(); receiver=d.get('receiver','').strip().lower()
    with sdb_conn() as conn:
        with conn.cursor() as cur:
            cu=sdb_user(cur, caller); ru=sdb_user(cur, receiver); c=sdb_direct(cur, caller, receiver) if cu and ru else None
            cur.execute('INSERT INTO call_logs(caller_id,receiver_id,conversation_id,call_type,status,duration_seconds) VALUES(%s,%s,%s,%s,%s,%s)', (cu['id'] if cu else None, ru['id'] if ru else None, c['id'] if c else None, d.get('call_type','video'), d.get('status','started'), int(d.get('duration_seconds') or 0)))
    return jsonify({'status':'ok'}),200

@app.route('/api/status/create', methods=['POST'])
def secure_status_create():
    if not SECURE_DB_ENABLED: return jsonify({'error':'Secure DB not enabled'}),503
    d=request.json or {}; username=d.get('username','').strip().lower()
    with sdb_conn() as conn:
        with conn.cursor() as cur:
            u=sdb_user(cur, username)
            if not u: return jsonify({'error':'user not found'}),404
            cur.execute('INSERT INTO statuses(user_id,type,text_enc,media_enc) VALUES(%s,%s,%s,%s)', (u['id'], d.get('type','text'), sdb_enc(d.get('text','')), sdb_enc(d.get('media',''))))
    return jsonify({'status':'ok'}),200

@app.route('/api/status/list', methods=['GET'])
def secure_status_list():
    if not SECURE_DB_ENABLED: return jsonify({'error':'Secure DB not enabled'}),503
    with sdb_conn() as conn:
        with conn.cursor() as cur: cur.execute('SELECT s.*,u.username,u.display_name FROM statuses s JOIN users u ON s.user_id=u.id WHERE s.expires_at>NOW() ORDER BY s.created_at DESC LIMIT 100'); rows=cur.fetchall()
    return jsonify({'statuses':[{'id':r['id'],'username':r['username'],'display_name':r['display_name'],'type':r['type'],'text':sdb_dec(r['text_enc']),'media':sdb_dec(r['media_enc']),'created_at':str(r['created_at']),'expires_at':str(r['expires_at'])} for r in rows]}),200

sdb_init()
if SECURE_DB_ENABLED:
    app.view_functions['auth_register'] = secure_auth_register
    app.view_functions['auth_login'] = secure_auth_login
    app.view_functions['user_heartbeat'] = secure_user_heartbeat
    app.view_functions['users_search'] = secure_users_search
    app.view_functions['friends_add'] = secure_friends_add
    app.view_functions['friends_requests_pending'] = secure_friends_requests_pending
    app.view_functions['friends_accept_request'] = secure_friends_accept_request
    app.view_functions['friends_decline_request'] = secure_friends_decline_request
    app.view_functions['friends_remove'] = secure_friends_remove
    app.view_functions['friends_list'] = secure_friends_list
    print('✅ [Secure PostgreSQL] Auth/friends routes overridden. Telegram logger unchanged.')

# ============ RUN ============
if __name__ == '__main__':
    print("=" * 50)
    print("🚀 MeetLink Advanced Backend (Super Advanced Engine)")
    print("=" * 50)
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("⚠️  Bot token not configured!")
    else:
        print("✅ Bot token configured")
    print(f"📡 Channel: {CHANNEL_ID}")
    print(f"🌐 Port: {PORT}")
    _ff = media_converter.is_ffmpeg_available()
    print(f"🎬 FFmpeg: {'✅ Available' if _ff else '❌ NOT FOUND — recordings will be sent as WebM!'}")
    if not _ff:
        print("⚠️  INSTALL FFMPEG! WebM→MP4 conversion WILL FAIL without it. The Dockerfile installs it — rebuild/redeploy if missing.")
    print("⏱️ TTL Engine: ✅ Active (1 Hour Auto-Expiration & Zero-Load Mode)")
    print("⚡ Thread Pool: ✅ Active (Zero-Lag Asynchronous Mode)")
    print("=" * 50)
    app.run(host='0.0.0.0', port=PORT)
