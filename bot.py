import os
import re
import json
import time
import asyncio
import tempfile
import threading
from pathlib import Path
from urllib.parse import quote

import requests
from flask import Flask

from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import FloodWait


# ============================================================
# CONFIG
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
API_ID_RAW = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")

ADMIN_ID_RAW = os.getenv("ADMIN_ID")

CHANNEL_ID_RAW = os.getenv("CHANNEL_ID")
CHANNEL_URL = os.getenv("CHANNEL_URL")

# Downloader API
DOWNLOADER_API = os.getenv("DOWNLOADER_API")


# ============================================================
# CONFIG VALIDATION
# ============================================================

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable missing")

if not API_ID_RAW:
    raise RuntimeError("API_ID environment variable missing")

if not API_HASH:
    raise RuntimeError("API_HASH environment variable missing")

if not ADMIN_ID_RAW:
    raise RuntimeError("ADMIN_ID environment variable missing")

if not CHANNEL_ID_RAW:
    raise RuntimeError("CHANNEL_ID environment variable missing")

if not CHANNEL_URL:
    raise RuntimeError("CHANNEL_URL environment variable missing")

if not DOWNLOADER_API:
    raise RuntimeError("DOWNLOADER_API environment variable missing")


try:
    API_ID = int(API_ID_RAW)
    ADMIN_ID = int(ADMIN_ID_RAW)
    CHANNEL_ID = int(CHANNEL_ID_RAW)
except ValueError:
    raise RuntimeError(
        "API_ID, ADMIN_ID and CHANNEL_ID must be valid numbers"
    )


# ============================================================
# APP / PATHS
# ============================================================

app = Flask(__name__)

DATA_DIR = Path(tempfile.gettempdir()) / "misstu_video_bot"
DATA_DIR.mkdir(parents=True, exist_ok=True)

USERS_FILE = DATA_DIR / "users.json"


# ============================================================
# USER DATABASE
# ============================================================

def load_users():
    if not USERS_FILE.exists():
        return {}

    try:
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Old format support:
        # [123456789, 987654321]
        if isinstance(data, list):
            result = {}

            for user_id in data:
                result[str(user_id)] = {
                    "user_id": int(user_id),
                    "name": "Unknown",
                    "username": ""
                }

            return result

        # New format
        if isinstance(data, dict):
            return data

    except Exception as e:
        print("User database read error:", e)

    return {}


def save_users(users):
    try:
        with open(USERS_FILE, "w", encoding="utf-8") as f:
            json.dump(
                users,
                f,
                ensure_ascii=False,
                indent=2
            )
    except Exception as e:
        print("User database save error:", e)


USERS = load_users()


def add_user(user):
    try:
        user_id = int(user.id)

        first_name = user.first_name or ""
        last_name = user.last_name or ""

        name = f"{first_name} {last_name}".strip()

        if not name:
            name = "Unknown"

        username = user.username or ""

        USERS[str(user_id)] = {
            "user_id": user_id,
            "name": name,
            "username": username
        }

        save_users(USERS)

    except Exception as e:
        print("Add user error:", e)


def remove_user(user_id):
    try:
        USERS.pop(str(user_id), None)
        save_users(USERS)
    except Exception as e:
        print("Remove user error:", e)


# ============================================================
# FLASK HEALTH SERVER
# ============================================================

@app.route("/")
def home():
    return "MISSTU Video Downloader Bot is running"


@app.route("/health")
def health():
    return "OK"


def run_flask():
    port = int(os.getenv("PORT", "10000"))

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False,
        use_reloader=False
    )


# ============================================================
# PYROGRAM CLIENT
# ============================================================

client = Client(
    "misstu_video_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    in_memory=True,
    ipv6=False,
    max_concurrent_transmissions=1,
)


# ============================================================
# CHANNEL JOIN KEYBOARD
# ============================================================

def join_keyboard():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🔗 Join Channel",
                    url=CHANNEL_URL
                )
            ],
            [
                InlineKeyboardButton(
                    "✅ Joined",
                    callback_data="check_join"
                )
            ]
        ]
    )


# ============================================================
# CHANNEL JOIN CHECK
# ============================================================

async def is_user_joined(user_id):
    try:
        member = await client.get_chat_member(
            CHANNEL_ID,
            user_id
        )

        status = str(member.status).lower()

        if status in (
            "member",
            "administrator",
            "owner"
        ):
            return True

        # Restricted users can still be members
        if status == "restricted":
            try:
                return bool(member.is_member)
            except Exception:
                return False

        return False

    except Exception as e:
        print(
            f"Join check error for {user_id}:",
            e
        )

        return False


# ============================================================
# JOIN REQUIRED
# ============================================================

async def require_join(message):
    user_id = message.from_user.id

    # Admin ko join requirement nahi
    if user_id == ADMIN_ID:
        return True

    joined = await is_user_joined(user_id)

    if joined:
        return True

    text = (
        "🚫 <b>Access Denied</b>\n\n"
        "Bot use karne ke liye pehle hamara "
        "channel join karo.\n\n"
        "Channel join karne ke baad "
        "<b>✅ Joined</b> button dabao."
    )

    await message.reply_text(
        text,
        reply_markup=join_keyboard()
    )

    return False


# ============================================================
# START COMMAND
# ============================================================

@client.on_message(
    filters.private &
    filters.command("start")
)
async def start_command(client, message):

    user = message.from_user

    add_user(user)

    user_name = user.first_name or "User"

    # Admin direct welcome
    if user.id == ADMIN_ID:
        text = (
            f"👋 <b>Welcome {user_name}!</b>\n\n"
            "🤖 <b>Alexa Video Downloader Bot</b>\n\n"
            "You are the bot admin.\n\n"
            "Send any supported video URL "
            "to download it."
        )

        try:
            photos = []
            async for photo in client.get_chat_photos(
                user.id,
                limit=1
            ):
                photos.append(photo)

            if photos:
                await message.reply_photo(
                    photos[0].file_id,
                    caption=text
                )
            else:
                await message.reply_text(text)

        except Exception:
            await message.reply_text(text)

        return

    joined = await is_user_joined(user.id)

    if not joined:

        text = (
            f"👋 <b>Welcome {user_name}!</b>\n\n"
            "🤖 <b>Alexa Video Downloader Bot</b>\n\n"
            "Video download karne ke liye "
            "pehle hamara channel join karo.\n\n"
            "👇 Pehle channel join karo."
        )

        try:
            photos = []

            async for photo in client.get_chat_photos(
                user.id,
                limit=1
            ):
                photos.append(photo)

            if photos:
                await message.reply_photo(
                    photos[0].file_id,
                    caption=text,
                    reply_markup=join_keyboard()
                )
            else:
                await message.reply_text(
                    text,
                    reply_markup=join_keyboard()
                )

        except Exception:
            await message.reply_text(
                text,
                reply_markup=join_keyboard()
            )

        return

    # Already joined
    text = (
        f"👋 <b>Welcome {user_name}!</b>\n\n"
        "🤖 <b>Alexa Video Downloader Bot</b>\n\n"
        "🔗 Bas video ka link bhejo.\n"
        "⚡ Main video download karke Telegram par bhej dunga."
    )

    try:
        photos = []

        async for photo in client.get_chat_photos(
            user.id,
            limit=1
        ):
            photos.append(photo)

        if photos:
            await message.reply_photo(
                photos[0].file_id,
                caption=text
            )
        else:
            await message.reply_text(text)

    except Exception:
        await message.reply_text(text)


# ============================================================
# JOIN CHECK BUTTON
# ============================================================

@client.on_callback_query(
    filters.regex("^check_join$")
)
async def check_join_callback(client, callback):

    user = callback.from_user

    add_user(user)

    joined = await is_user_joined(user.id)

    if not joined:

        await callback.answer(
            "🚫 Access denied! Pehle channel join karo.",
            show_alert=True
        )

        return

    await callback.answer(
        "🎉 Congratulations! You are Joined.",
        show_alert=True
    )

    text = (
        f"🎉 <b>Welcome {user.first_name or 'User'}!</b>\n\n"
        "✅ Channel membership verified.\n\n"
        "🤖 <b>Alexa Video Downloader Bot</b>\n\n"
        "🔗 Ab video ka link bhejo."
    )

    try:
        await callback.message.edit_caption(
            caption=text,
            reply_markup=None
        )
    except Exception:
        try:
            await callback.message.edit_text(
                text,
                reply_markup=None
            )
        except Exception:
            pass


# ============================================================
# /USER COMMAND
# ============================================================

@client.on_message(
    filters.private &
    filters.command("user")
)
async def user_command(client, message):

    if message.from_user.id != ADMIN_ID:
        return

    users = load_users()

    if not users:
        await message.reply_text(
            "📂 Abhi koi registered user nahi hai."
        )
        return

    lines = [
        "👥 <b>REGISTERED USERS</b>",
        "",
        f"Total Users: <b>{len(users)}</b>",
        ""
    ]

    for index, data in enumerate(
        users.values(),
        start=1
    ):
        user_id = data.get("user_id", "")
        name = data.get("name", "Unknown")
        username = data.get("username", "")

        username_text = (
            f"@{username}"
            if username
            else "No Username"
        )

        lines.append(
            f"{index}. <b>{name}</b>\n"
            f"   Username: {username_text}\n"
            f"   ID: <code>{user_id}</code>\n"
        )

    result = "\n".join(lines)

    # Telegram message limit protection
    chunks = []

    while len(result) > 3900:
        split_at = result.rfind(
            "\n",
            0,
            3900
        )

        if split_at == -1:
            split_at = 3900

        chunks.append(result[:split_at])
        result = result[split_at:]

    chunks.append(result)

    for chunk in chunks:
        await message.reply_text(chunk)


# ============================================================
# /BROADCAST
# ============================================================

@client.on_message(
    filters.private &
    filters.command("broadcast")
)
async def broadcast_command(client, message):

    if message.from_user.id != ADMIN_ID:
        return

    text = message.text or ""

    parts = text.split(maxsplit=2)

    if len(parts) < 2:
        await message.reply_text(
            "❌ Usage:\n\n"
            "<code>/broadcast Hello everyone</code>\n\n"
            "Specific user:\n"
            "<code>/broadcast USER_ID Hello</code>"
        )
        return

    # --------------------------------------------------------
    # Personal message
    # /broadcast 123456789 Hello
    # --------------------------------------------------------

    if (
        len(parts) >= 3
        and parts[1].isdigit()
    ):
        try:
            target_id = int(parts[1])
            broadcast_text = parts[2]

            await client.send_message(
                target_id,
                broadcast_text
            )

            await message.reply_text(
                f"✅ Message sent to "
                f"<code>{target_id}</code>"
            )

        except FloodWait as e:
            await message.reply_text(
                f"⏳ FloodWait: {e.value} seconds"
            )

        except Exception as e:
            await message.reply_text(
                f"❌ Failed:\n<code>{e}</code>"
            )

        return

    # --------------------------------------------------------
    # Broadcast to all
    # --------------------------------------------------------

    broadcast_text = text.split(
        " ",
        1
    )[1].strip()

    users = load_users()

    if not users:
        await message.reply_text(
            "❌ User list empty."
        )
        return

    sent = 0
    failed = 0

    await message.reply_text(
        f"📢 Broadcast started...\n"
        f"👥 Users: {len(users)}"
    )

    for user_id in list(users.keys()):

        try:
            await client.send_message(
                int(user_id),
                broadcast_text
            )

            sent += 1

            await asyncio.sleep(0.05)

        except FloodWait as e:

            print(
                f"FloodWait {e.value}s"
            )

            await asyncio.sleep(
                e.value
            )

            try:
                await client.send_message(
                    int(user_id),
                    broadcast_text
                )

                sent += 1

            except Exception:
                failed += 1

        except Exception as e:

            print(
                f"Broadcast failed "
                f"for {user_id}: {e}"
            )

            failed += 1

            # User blocked/deactivated bot
            remove_user(user_id)

    await message.reply_text(
        "📢 <b>Broadcast Completed</b>\n\n"
        f"✅ Sent: <b>{sent}</b>\n"
        f"❌ Failed: <b>{failed}</b>"
    )


# ============================================================
# /ADD COMMAND
#
# /add Hello
# OR
#
# Reply to any Telegram message and send:
# /add
#
# It copies the replied message to configured channel.
# ============================================================

@client.on_message(
    filters.private &
    filters.command("add")
)
async def add_command(client, message):

    if message.from_user.id != ADMIN_ID:
        return

    # --------------------------------------------------------
    # Reply mode
    # --------------------------------------------------------

    if message.reply_to_message:

        source = message.reply_to_message

        try:
            await client.copy_message(
                chat_id=CHANNEL_ID,
                from_chat_id=source.chat.id,
                message_id=source.id
            )

            await message.reply_text(
                "✅ Message successfully added "
                "to the channel."
            )

        except FloodWait as e:

            await asyncio.sleep(
                e.value
            )

            try:
                await client.copy_message(
                    chat_id=CHANNEL_ID,
                    from_chat_id=source.chat.id,
                    message_id=source.id
                )

                await message.reply_text(
                    "✅ Message successfully added "
                    "to the channel."
                )

            except Exception as e2:

                await message.reply_text(
                    f"❌ Add failed:\n"
                    f"<code>{e2}</code>"
                )

        except Exception as e:

            await message.reply_text(
                f"❌ Add failed:\n"
                f"<code>{e}</code>"
            )

        return

    # --------------------------------------------------------
    # Text mode
    # /add Hello
    # --------------------------------------------------------

    text = message.text or ""

    parts = text.split(
        " ",
        1
    )

    if len(parts) < 2 or not parts[1].strip():

        await message.reply_text(
            "❌ Usage:\n\n"
            "Text:\n"
            "<code>/add Hello</code>\n\n"
            "Photo/Video/Document/Sticker/etc:\n"
            "Message ko reply karo aur "
            "<code>/add</code> bhejo."
        )

        return

    content = parts[1].strip()

    try:

        await client.send_message(
            CHANNEL_ID,
            content
        )

        await message.reply_text(
            "✅ Text successfully added "
            "to the channel."
        )

    except FloodWait as e:

        await asyncio.sleep(
            e.value
        )

        try:
            await client.send_message(
                CHANNEL_ID,
                content
            )

            await message.reply_text(
                "✅ Text successfully added "
                "to the channel."
            )

        except Exception as e2:

            await message.reply_text(
                f"❌ Add failed:\n"
                f"<code>{e2}</code>"
            )

    except Exception as e:

        await message.reply_text(
            f"❌ Add failed:\n"
            f"<code>{e}</code>"
        )


# ============================================================
# FIND VIDEO URL FROM API RESPONSE
# ============================================================

def find_video_url(data):

    if isinstance(data, str):

        value = data.strip()

        if (
            value.startswith("http://")
            or value.startswith("https://")
        ):
            return value

        return None

    if isinstance(data, dict):

        # Common direct keys
        preferred_keys = [
            "url",
            "video_url",
            "video",
            "download_url",
            "download",
            "media_url",
            "play_url",
            "link"
        ]

        for key in preferred_keys:

            if key in data:

                result = find_video_url(
                    data[key]
                )

                if result:
                    return result

        # Search everything recursively
        for value in data.values():

            result = find_video_url(
                value
            )

            if result:
                return result

    elif isinstance(data, list):

        for item in data:

            result = find_video_url(
                item
            )

            if result:
                return result

    return None


# ============================================================
# CALL DOWNLOADER API
# ============================================================

def get_video_url(source_url):

    api_url = (
        DOWNLOADER_API
        + quote(source_url, safe="")
    )

    print("Downloader API:", api_url)

    response = requests.get(
        api_url,
        timeout=60,
        headers={
            "User-Agent":
                "Mozilla/5.0"
        }
    )

    response.raise_for_status()

    content_type = (
        response.headers
        .get("content-type", "")
        .lower()
    )

    if "application/json" in content_type:

        data = response.json()

    else:

        try:
            data = response.json()
        except Exception:
            data = response.text

    video_url = find_video_url(data)

    if not video_url:
        raise RuntimeError(
            "API response me video URL nahi mila."
        )

    return video_url


# ============================================================
# DOWNLOAD VIDEO
# ============================================================

def download_video(video_url, output_path):

    response = requests.get(
        video_url,
        stream=True,
        timeout=120,
        headers={
            "User-Agent":
                "Mozilla/5.0"
        }
    )

    response.raise_for_status()

    with open(
        output_path,
        "wb"
    ) as file:

        for chunk in response.iter_content(
            chunk_size=1024 * 1024
        ):

            if chunk:
                file.write(chunk)

    if not os.path.exists(output_path):
        raise RuntimeError(
            "Video file create nahi hui."
        )

    file_size = os.path.getsize(
        output_path
    )

    if file_size == 0:
        raise RuntimeError(
            "Downloaded video empty hai."
        )

    return output_path


# ============================================================
# VIDEO URL FILTER
# ============================================================

URL_PATTERN = re.compile(
    r"https?://\S+",
    re.IGNORECASE
)


# ============================================================
# VIDEO DOWNLOADER
# ============================================================

@client.on_message(
    filters.private &
    filters.text &
    ~filters.command(
        [
            "start",
            "broadcast",
            "user",
            "add"
        ]
    )
)
async def video_downloader(client, message):

    # Join check
    allowed = await require_join(
        message
    )

    if not allowed:
        return

    add_user(
        message.from_user
    )

    text = (
        message.text or ""
    ).strip()

    match = URL_PATTERN.search(
        text
    )

    if not match:

        await message.reply_text(
            "❌ Valid video URL bhejo."
        )

        return

    source_url = match.group(0)

    status = await message.reply_text(
        "⏳ <b>Processing...</b>\n\n"
        "🔎 Video link check ho raha hai."
    )

    temp_dir = tempfile.mkdtemp(
        prefix="misstu_"
    )

    output_file = os.path.join(
        temp_dir,
        f"video_{int(time.time())}.mp4"
    )

    try:

        # ----------------------------------------------------
        # API
        # ----------------------------------------------------

        await status.edit_text(
            "⏳ <b>Processing...</b>\n\n"
            "🔎 Downloader API se data aa raha hai..."
        )

        video_url = await asyncio.to_thread(
            get_video_url,
            source_url
        )

        # ----------------------------------------------------
        # DOWNLOAD
        # ----------------------------------------------------

        await status.edit_text(
            "📥 <b>Downloading...</b>\n\n"
            "Please wait..."
        )

        await asyncio.to_thread(
            download_video,
            video_url,
            output_file
        )

        # ----------------------------------------------------
        # SEND
        # ----------------------------------------------------

        await status.edit_text(
            "📤 <b>Uploading...</b>\n\n"
            "Ruko Jara Sabar Karo..."
        )

        caption = (
            "🎬 <b>Video Downloaded Successfully</b>\n\n"
            "🥰 Alexa Video Downloader"
        )

        sent = False

        # Retry upload
        for attempt in range(4):

            try:

                await client.send_video(
                    chat_id=message.chat.id,
                    video=output_file,
                    caption=caption,
                    supports_streaming=True
                )

                sent = True
                break

            except FloodWait as e:

                print(
                    f"Upload FloodWait: "
                    f"{e.value}s"
                )

                await asyncio.sleep(
                    e.value
                )

            except Exception as e:

                print(
                    f"Upload attempt "
                    f"{attempt + 1} failed:",
                    e
                )

                if attempt < 3:
                    await asyncio.sleep(2)

        if not sent:

            raise RuntimeError(
                "Telegram upload failed."
            )

        try:
            await status.delete()
        except Exception:
            pass

    except requests.exceptions.Timeout:

        try:
            await status.edit_text(
                "❌ API/Download timeout.\n"
                "Thodi der baad try karo."
            )
        except Exception:
            pass

    except requests.exceptions.RequestException as e:

        print(
            "Request error:",
            e
        )

        try:
            await status.edit_text(
                "❌ Downloader API error.\n"
                "Link ya API response check karo."
            )
        except Exception:
            pass

    except Exception as e:

        print(
            "Downloader error:",
            e
        )

        try:
            await status.edit_text(
                "❌ <b>Download Failed</b>\n\n"
                "Is link ko download nahi kiya ja saka."
            )
        except Exception:
            pass

    finally:

        # ----------------------------------------------------
        # CLEAN TEMP FILES
        # ----------------------------------------------------

        try:

            if os.path.exists(
                output_file
            ):
                os.remove(
                    output_file
                )

            if os.path.isdir(
                temp_dir
            ):
                try:
                    os.rmdir(
                        temp_dir
                    )
                except Exception:
                    pass

        except Exception as e:

            print(
                "Cleanup error:",
                e
            )


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":

    print("=" * 60)
    print("MISSTU VIDEO DOWNLOADER BOT")
    print("=" * 60)
    print("Starting health server...")

    flask_thread = threading.Thread(
        target=run_flask,
        daemon=True
    )

    flask_thread.start()

    print("Starting Telegram bot...")

    client.run()
