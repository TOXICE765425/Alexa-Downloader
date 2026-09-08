import os
import re
import json
import time
import asyncio
import tempfile
import threading
from pathlib import Path

import requests
from flask import Flask
from pyrogram import Client, filters
from pyrogram.errors import FloodWait


BOT_TOKEN = os.getenv("BOT_TOKEN")
API_ID_RAW = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")
ADMIN_ID_RAW = os.getenv("ADMIN_ID")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable missing")
if not API_ID_RAW:
    raise RuntimeError("API_ID environment variable missing")
if not API_HASH:
    raise RuntimeError("API_HASH environment variable missing")
if not ADMIN_ID_RAW:
    raise RuntimeError("ADMIN_ID environment variable missing")

try:
    API_ID = int(API_ID_RAW)
    ADMIN_ID = int(ADMIN_ID_RAW)
except ValueError:
    raise RuntimeError("API_ID and ADMIN_ID must be numeric")

DOWNLOADER_API = "https://ansh-apis.is-dev.org/api/downloder?key=ansh&url="

DATA_DIR = Path(tempfile.gettempdir()) / "misstu_video_bot"
DATA_DIR.mkdir(parents=True, exist_ok=True)
USERS_FILE = DATA_DIR / "users.json"


def load_users():
    try:
        if USERS_FILE.exists():
            with USERS_FILE.open("r", encoding="utf-8") as f:
                return set(int(x) for x in json.load(f))
    except Exception as e:
        print("User database read error:", e)
    return set()


users = load_users()


def save_users():
    try:
        with USERS_FILE.open("w", encoding="utf-8") as f:
            json.dump(sorted(users), f)
    except Exception as e:
        print("User database write error:", e)


def add_user(user_id):
    if user_id not in users:
        users.add(user_id)
        save_users()


app = Client(
    "misstu_video_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    in_memory=True,
    ipv6=False,
    max_concurrent_transmissions=1,
)


web = Flask(__name__)


@web.get("/")
def home():
    return "MISSTU Video Downloader Bot is running", 200


@web.get("/health")
def health():
    return "OK", 200


def run_health_server():
    port = int(os.getenv("PORT", "10000"))
    web.run(
        host="0.0.0.0",
        port=port,
        threaded=True,
        use_reloader=False,
    )


def find_video_url(data):
    if isinstance(data, str):
        if data.startswith(("http://", "https://")):
            lower = data.lower()
            if any(x in lower for x in (
                ".mp4", ".mkv", ".webm", ".mov", ".m4v", ".avi"
            )):
                return data
            if "video" in lower or "media" in lower:
                return data
        return None

    if isinstance(data, dict):
        preferred = (
            "video", "video_url", "download_url", "url",
            "link", "media", "result"
        )
        for key in preferred:
            if key in data:
                found = find_video_url(data[key])
                if found:
                    return found
        for value in data.values():
            found = find_video_url(value)
            if found:
                return found

    elif isinstance(data, list):
        for item in data:
            found = find_video_url(item)
            if found:
                return found

    return None


async def download_video(url, output_file):
    api_url = DOWNLOADER_API + requests.utils.quote(url, safe="")

    def do_download():
        response = requests.get(api_url, timeout=60)
        response.raise_for_status()

        try:
            data = response.json()
        except Exception as e:
            raise RuntimeError("Downloader API ne valid JSON response diya.") from e

        video_url = find_video_url(data)
        if not video_url:
            raise RuntimeError("API response me video URL nahi mila.")

        print("Video URL found:", video_url)

        with requests.get(
            video_url,
            stream=True,
            timeout=(30, 180),
        ) as r:
            r.raise_for_status()
            with output_file.open("wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)

    await asyncio.to_thread(do_download)


async def get_user_photo(user_id):
    try:
        async for photo in app.get_chat_photos(user_id, limit=1):
            return photo.file_id
    except Exception as e:
        print("Profile photo error:", e)
    return None


async def safe_delete(message):
    try:
        await message.delete()
    except Exception:
        pass


@app.on_message(filters.command("start"))
async def start_handler(client, message):
    user = message.from_user
    if not user:
        return

    add_user(user.id)

    name = user.first_name or "User"
    welcome = (
        f"👋 <b>Welcome {name}!</b>\n\n"
        "🎬 <b>MISSTU Video Downloader</b>\n\n"
        "📥 Mujhe kisi supported video ka link bhejo.\n"
        "Main uska video download karke yahin bhej dunga.\n\n"
        "⚡ <b>Fast • Simple • Easy</b>"
    )

    photo = await get_user_photo(user.id)

    try:
        if photo:
            await message.reply_photo(photo=photo, caption=welcome)
        else:
            await message.reply_text(welcome)
    except Exception as e:
        print("Welcome error:", e)
        try:
            await message.reply_text(welcome)
        except Exception:
            pass


@app.on_message(filters.command("broadcast") & filters.private)
async def broadcast_handler(client, message):
    if not message.from_user:
        return

    if message.from_user.id != ADMIN_ID:
        await message.reply_text("❌ You are not authorized to use this command.")
        return

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await message.reply_text(
            "❌ Broadcast message missing.\n\n"
            "Example:\n"
            "<code>/broadcast Hello everyone!</code>"
        )
        return

    text = parts[1].strip()
    user_list = list(users)

    if not user_list:
        await message.reply_text("❌ Abhi koi registered user nahi mila.")
        return

    status = await message.reply_text(
        f"📢 <b>Broadcast started...</b>\n\n"
        f"👥 Total: <code>{len(user_list)}</code>\n"
        "📤 Sent: <code>0</code>\n"
        "❌ Failed: <code>0</code>"
    )

    sent = 0
    failed = 0

    for user_id in user_list:
        try:
            await client.send_message(user_id, text)
            sent += 1
            await asyncio.sleep(0.1)

        except FloodWait as e:
            wait_time = int(getattr(e, "value", 1))
            print(f"FloodWait: waiting {wait_time}s")
            await asyncio.sleep(wait_time)
            try:
                await client.send_message(user_id, text)
                sent += 1
            except Exception as err:
                print(f"Broadcast retry failed for {user_id}: {err}")
                failed += 1

        except Exception as e:
            print(f"Broadcast failed for {user_id}: {e}")
            failed += 1
            error_text = str(e).upper()
            if "USER_IS_BLOCKED" in error_text or "USER_DEACTIVATED" in error_text:
                users.discard(user_id)

    save_users()

    try:
        await status.edit_text(
            f"✅ <b>Broadcast completed!</b>\n\n"
            f"👥 Total: <code>{len(user_list)}</code>\n"
            f"📤 Sent: <code>{sent}</code>\n"
            f"❌ Failed: <code>{failed}</code>"
        )
    except Exception:
        pass


@app.on_message(
    filters.private
    & filters.text
    & ~filters.command(["start", "broadcast"])
)
async def video_handler(client, message):
    user = message.from_user
    if not user:
        return

    add_user(user.id)

    url = message.text.strip()

    if not url.startswith(("http://", "https://")):
        await message.reply_text("🔗 Please ek valid video link bhejo.")
        return

    status = await message.reply_text(
        "⏳ <b>Processing...</b>\n\n"
        "🔎 Video information check kar raha hoon..."
    )

    output_file = DATA_DIR / f"video_{user.id}_{int(time.time() * 1000)}.mp4"

    try:
        await status.edit_text("⬇️ <b>Downloading video...</b>")
        await download_video(url, output_file)

        if not output_file.exists() or output_file.stat().st_size <= 0:
            raise RuntimeError("Downloaded video empty hai.")

        size_mb = output_file.stat().st_size / (1024 * 1024)
        print(f"Downloaded video size: {size_mb:.2f} MB")

        await status.edit_text(
            "⬆️ <b>Uploading video to Telegram...</b>\n\n"
            "Please wait..."
        )

        last_error = None
        uploaded = False

        for attempt in range(1, 5):
            try:
                print(f"Upload attempt {attempt}/4")
                await client.send_video(
                    chat_id=message.chat.id,
                    video=str(output_file),
                    caption="🎬 <b>Video Downloaded Successfully!</b>\n\n⚡ <b>MISSTU Downloader</b>",
                    supports_streaming=True,
                )
                uploaded = True
                break

            except Exception as e:
                last_error = e
                print(f"Upload attempt {attempt} failed: {e}")
                if attempt < 4:
                    try:
                        await status.edit_text(
                            f"⚠️ Upload connection issue.\n\n"
                            f"🔄 Retrying {attempt + 1}/4..."
                        )
                    except Exception:
                        pass
                    await asyncio.sleep(5)

        if not uploaded:
            raise RuntimeError(f"Telegram upload failed: {last_error}")

        await safe_delete(status)

    except Exception as e:
        print("VIDEO ERROR:", repr(e))
        try:
            await status.edit_text(
                "❌ <b>Video download/upload failed.</b>\n\n"
                "Please thodi der baad dobara try karo.\n\n"
                f"<code>{str(e)[:500]}</code>"
            )
        except Exception:
            pass

    finally:
        try:
            if output_file.exists():
                output_file.unlink()
        except Exception as e:
            print("Temporary file delete error:", e)


if __name__ == "__main__":
    print("=" * 50)
    print("MISSTU VIDEO DOWNLOADER BOT")
    print("=" * 50)
    print("Admin ID:", ADMIN_ID)
    print("Users loaded:", len(users))

    threading.Thread(
        target=run_health_server,
        daemon=True,
    ).start()

    print("Health server started.")
    print("Starting Telegram bot...")

    app.run()
