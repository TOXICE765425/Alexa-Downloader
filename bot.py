import os
import json
import asyncio
import tempfile
import shutil
import threading
import time
from pathlib import Path

import requests
from flask import Flask
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import FloodWait, UserIsBlocked, PeerIdInvalid


# =========================================================
# CONFIG
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
API_ID = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")
ADMIN_ID = os.getenv("ADMIN_ID")
CHANNEL_ID_RAW = os.getenv("CHANNEL_ID")
CHANNEL_URL = os.getenv("CHANNEL_URL")
DOWNLOADER_API = os.getenv("DOWNLOADER_API")


# =========================================================
# CHECK CONFIG
# =========================================================

if not BOT_TOKEN:
    raise RuntimeError("❌ BOT_TOKEN environment variable missing")

if not API_ID:
    raise RuntimeError("❌ API_ID environment variable missing")

if not API_HASH:
    raise RuntimeError("❌ API_HASH environment variable missing")

if not ADMIN_ID:
    raise RuntimeError("❌ ADMIN_ID environment variable missing")

if not CHANNEL_ID_RAW:
    raise RuntimeError("❌ CHANNEL_ID environment variable missing")

if not CHANNEL_URL:
    raise RuntimeError("❌ CHANNEL_URL environment variable missing")

if not DOWNLOADER_API:
    raise RuntimeError("❌ DOWNLOADER_API environment variable missing")


try:
    API_ID = int(API_ID)
except ValueError:
    raise RuntimeError("❌ API_ID must be a number")

try:
    ADMIN_ID = int(ADMIN_ID)
except ValueError:
    raise RuntimeError("❌ ADMIN_ID must be a number")


# =========================================================
# APP
# =========================================================

app = Client(
    "misstu_video_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    in_memory=True,
    ipv6=False,
    max_concurrent_transmissions=1,
)


# =========================================================
# FLASK HEALTH SERVER
# =========================================================

web = Flask(__name__)


@web.route("/")
def home():
    return "MISSTU Video Downloader Bot is running", 200


@web.route("/health")
def health():
    return "OK", 200


def run_web_server():
    port = int(os.getenv("PORT", "10000"))

    web.run(
        host="0.0.0.0",
        port=port,
        debug=False,
        use_reloader=False,
    )


# =========================================================
# DATA FILE
# =========================================================

DATA_DIR = Path(tempfile.gettempdir()) / "misstu_video_bot"
DATA_DIR.mkdir(parents=True, exist_ok=True)

USERS_FILE = DATA_DIR / "users.json"


def load_users():
    if not USERS_FILE.exists():
        return {}

    try:
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, dict):
            return data

    except Exception as e:
        print("[USERS LOAD ERROR]", repr(e))

    return {}


def save_users(users):
    try:
        temp_file = USERS_FILE.with_suffix(".tmp")

        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(users, f, ensure_ascii=False, indent=2)

        temp_file.replace(USERS_FILE)

    except Exception as e:
        print("[USERS SAVE ERROR]", repr(e))


USERS = load_users()


# =========================================================
# USER SAVE
# =========================================================

def add_user(user):
    if not user:
        return

    uid = str(user.id)

    USERS[uid] = {
        "id": user.id,
        "first_name": user.first_name or "",
        "last_name": user.last_name or "",
        "username": user.username or "",
        "updated_at": int(time.time()),
    }

    save_users(USERS)


# =========================================================
# CHANNEL
# =========================================================

CHANNEL_TARGET = None


def prepare_channel_target():
    """
    Converts numeric channel ID to int.
    Keeps @username as string.
    """

    value = CHANNEL_ID_RAW.strip()

    if not value:
        raise RuntimeError("CHANNEL_ID is empty")

    if value.lstrip("-").isdigit():
        return int(value)

    if value.startswith("https://t.me/"):
        value = value.replace("https://t.me/", "", 1)

    if value.startswith("t.me/"):
        value = value.replace("t.me/", "", 1)

    if not value.startswith("@"):
        value = "@" + value

    return value


CHANNEL_TARGET = prepare_channel_target()


# =========================================================
# CHANNEL RESOLVE
# =========================================================

async def resolve_channel():
    global CHANNEL_TARGET

    print("=" * 60)
    print("CHANNEL CHECK")
    print("CHANNEL_ID:", CHANNEL_TARGET)
    print("CHANNEL_URL:", CHANNEL_URL)
    print("=" * 60)

    try:
        chat = await app.get_chat(CHANNEL_TARGET)

        print("✅ Channel resolved successfully")
        print("Title:", chat.title)
        print("ID:", chat.id)
        print("Username:", chat.username)

        CHANNEL_TARGET = chat.id

        return True

    except PeerIdInvalid:
        print("❌ Peer id invalid")
        print("Channel ID/username cannot be resolved.")
        print("Make sure BOT is added as ADMIN in the SAME channel.")
        return False

    except Exception as e:
        print("❌ CHANNEL RESOLVE ERROR:")
        print(repr(e))
        print()
        print("CHECK THESE:")
        print("1. Bot channel me added hai")
        print("2. Bot ADMIN hai")
        print("3. CHANNEL_ID correct hai")
        print("4. CHANNEL_URL same channel ka hai")
        return False


# =========================================================
# MEMBERSHIP CHECK
# =========================================================

async def is_user_joined(user_id, retries=3):

    if user_id == ADMIN_ID:
        return True, None

    if CHANNEL_TARGET is None:
        return False, "CHANNEL_TARGET is not configured"

    last_error = None

    for attempt in range(retries):

        try:
            member = await app.get_chat_member(
                chat_id=CHANNEL_TARGET,
                user_id=user_id,
            )

            status = member.status

            if hasattr(status, "value"):
                status = status.value

            status = str(status).lower().strip()

            print(
                f"[JOIN CHECK] "
                f"user={user_id} "
                f"channel={CHANNEL_TARGET} "
                f"attempt={attempt + 1} "
                f"status={status}"
            )

            # Normal member
            if status == "member":
                return True, None

            # Admin
            if status in ("administrator", "admin"):
                return True, None

            # Owner
            if status in ("owner", "creator"):
                return True, None

            # Restricted member
            if status == "restricted":
                return bool(
                    getattr(member, "is_member", False)
                ), None

            # Not joined
            if status in ("left", "kicked", "banned"):
                return False, None

            return False, None

        except FloodWait as e:

            wait_time = int(e.value)

            print(
                f"[JOIN CHECK FLOODWAIT] "
                f"waiting {wait_time}s"
            )

            await asyncio.sleep(wait_time)

            last_error = repr(e)

        except Exception as e:

            last_error = repr(e)

            print(
                f"[JOIN CHECK ERROR] "
                f"attempt={attempt + 1}: {repr(e)}"
            )

            if attempt < retries - 1:
                await asyncio.sleep(1.5)

    return False, last_error


# =========================================================
# KEYBOARDS
# =========================================================

def join_keyboard():

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "📢 Join Channel",
                    url=CHANNEL_URL,
                )
            ],
            [
                InlineKeyboardButton(
                    "✅ Joined",
                    callback_data="check_join",
                )
            ],
        ]
    )


# =========================================================
# START MESSAGE
# =========================================================

def welcome_text(user, joined=False):

    name = user.first_name or "User"

    if joined:

        return (
            f"🎉 <b>Welcome {name}!</b>\n\n"
            "✅ <b>Channel membership verified.</b>\n\n"
            "🤖 <b>Alexa Video Downloader Bot</b>\n\n"
            "🔗 <b>Ab video ka link bhejo.</b>\n\n"
            "📥 Instagram • YouTube • TikTok • Facebook"
        )

    return (
        f"👋 <b>Welcome {name}!</b>\n\n"
        "🤖 <b>Alexa Video Downloader Bot</b>\n\n"
        "⚠️ Bot use karne ke liye pehle hamara channel join karo.\n\n"
        "👇 Join karne ke baad <b>Joined</b> button dabao."
    )


# =========================================================
# SEND START
# =========================================================

async def send_start_message(message, user):

    joined, error = await is_user_joined(user.id)

    if error:
        print("[START JOIN ERROR]", error)

        text = (
            f"👋 <b>Welcome {user.first_name or 'User'}!</b>\n\n"
            "⚠️ Channel verification temporarily unavailable.\n\n"
            "Please make sure the bot is an admin in the channel."
        )

        await message.reply_text(text)
        return

    if joined:

        text = welcome_text(user, True)

        try:
            photos = []

            async for photo in app.get_chat_photos(
                user.id,
                limit=1,
            ):
                photos.append(photo)

            if photos:

                await message.reply_photo(
                    photos[0].file_id,
                    caption=text,
                )

            else:

                await message.reply_text(text)

        except Exception as e:

            print("[PROFILE PHOTO ERROR]", repr(e))

            await message.reply_text(text)

        return

    # Not joined
    text = welcome_text(user, False)

    try:

        photos = []

        async for photo in app.get_chat_photos(
            user.id,
            limit=1,
        ):
            photos.append(photo)

        if photos:

            await message.reply_photo(
                photos[0].file_id,
                caption=text,
                reply_markup=join_keyboard(),
            )

        else:

            await message.reply_text(
                text,
                reply_markup=join_keyboard(),
            )

    except Exception as e:

        print("[START PHOTO ERROR]", repr(e))

        await message.reply_text(
            text,
            reply_markup=join_keyboard(),
        )


# =========================================================
# /START
# =========================================================

@app.on_message(filters.command("start") & filters.private)
async def start_handler(client, message):

    user = message.from_user

    if not user:
        return

    add_user(user)

    await send_start_message(message, user)


# =========================================================
# JOINED CALLBACK
# =========================================================

@app.on_callback_query(filters.regex("^check_join$"))
async def joined_callback(client, callback_query):

    user = callback_query.from_user

    if not user:
        return

    add_user(user)

    # Admin bypass
    if user.id == ADMIN_ID:

        joined = True
        error = None

    else:

        joined, error = await is_user_joined(
            user.id,
            retries=3,
        )

    # Verification error
    if error:

        print(
            "[CALLBACK JOIN ERROR]",
            error,
        )

        await callback_query.answer(
            "⚠️ Verification error.\n"
            "Bot/channel settings check karo.",
            show_alert=True,
        )

        return

    # Not joined
    if not joined:

        await callback_query.answer(
            "🚫 Pehle channel join karo!",
            show_alert=True,
        )

        return

    # Successfully joined
    await callback_query.answer(
        "🎉 Congratulations! You are Joined.",
        show_alert=True,
    )

    text = welcome_text(
        user,
        joined=True,
    )

    try:

        if callback_query.message.photo:

            await callback_query.message.edit_caption(
                caption=text,
                reply_markup=None,
            )

        else:

            await callback_query.message.edit_text(
                text,
                reply_markup=None,
            )

    except Exception as e:

        print(
            "[JOIN SUCCESS EDIT ERROR]",
            repr(e),
        )

        try:

            await callback_query.message.reply_text(
                text
            )

        except Exception:
            pass


# =========================================================
# URL CHECK
# =========================================================

def is_valid_url(text):

    if not text:
        return False

    text = text.strip().lower()

    supported = (
        "http://",
        "https://",
    )

    if not text.startswith(supported):
        return False

    return True


# =========================================================
# FIND VIDEO URL
# =========================================================

def find_video_url(data):

    if isinstance(data, str):

        if data.startswith("http://") or data.startswith("https://"):

            lower = data.lower()

            if any(
                x in lower
                for x in (
                    ".mp4",
                    ".mkv",
                    ".webm",
                    ".mov",
                    "video",
                    "download",
                    "media",
                )
            ):
                return data

        return None

    if isinstance(data, dict):

        # Common keys first
        priority_keys = [
            "video",
            "video_url",
            "download_url",
            "download",
            "url",
            "link",
            "media",
            "play",
            "play_url",
            "src",
        ]

        for key in priority_keys:

            if key in data:

                result = find_video_url(
                    data[key]
                )

                if result:
                    return result

        # Search all values
        for value in data.values():

            result = find_video_url(value)

            if result:
                return result

    elif isinstance(data, list):

        for item in data:

            result = find_video_url(item)

            if result:
                return result

    return None


# =========================================================
# DOWNLOAD VIDEO
# =========================================================

def download_video(video_url, output_path):

    print("[DOWNLOAD]", video_url)

    with requests.get(
        video_url,
        stream=True,
        timeout=120,
        headers={
            "User-Agent": "Mozilla/5.0"
        },
    ) as response:

        response.raise_for_status()

        with open(
            output_path,
            "wb",
        ) as file:

            for chunk in response.iter_content(
                chunk_size=1024 * 1024
            ):

                if chunk:
                    file.write(chunk)

    return output_path


# =========================================================
# CALL DOWNLOADER API
# =========================================================

def call_downloader_api(url):

    endpoint = DOWNLOADER_API + url

    print("[API REQUEST]", endpoint)

    response = requests.get(
        endpoint,
        timeout=120,
        headers={
            "User-Agent": "Mozilla/5.0"
        },
    )

    print(
        "[API STATUS]",
        response.status_code,
    )

    response.raise_for_status()

    try:
        return response.json()

    except Exception:

        text = response.text.strip()

        if text.startswith("http://") or text.startswith("https://"):
            return text

        raise RuntimeError(
            "Downloader API did not return valid JSON"
        )


# =========================================================
# VIDEO HANDLER
# =========================================================

@app.on_message(
    filters.private
    & filters.text
    & ~filters.command(
        [
            "start",
            "broadcast",
            "user",
            "add",
        ]
    )
)
async def video_handler(client, message):

    user = message.from_user

    if not user:
        return

    add_user(user)

    # Admin bypass
    if user.id != ADMIN_ID:

        joined, error = await is_user_joined(
            user.id,
            retries=3,
        )

        if error:

            await message.reply_text(
                "⚠️ Channel verification error.\n"
                "Thodi der baad try karo."
            )

            return

        if not joined:

            await message.reply_text(
                "🚫 Pehle channel join karo.",
                reply_markup=join_keyboard(),
            )

            return

    url = message.text.strip()

    if not is_valid_url(url):

        await message.reply_text(
            "❌ Please send a valid video URL."
        )

        return

    status_message = await message.reply_text(
        "⏳ <b>Searching Database...</b>\n\n"
        "⚡ Connecting database..."
    )

    temp_dir = Path(
        tempfile.mkdtemp(
            prefix="misstu_"
        )
    )

    video_file = temp_dir / "video.mp4"

    try:

        await status_message.edit_text(
            "⏳ <b>Searching Database...</b>\n\n"
            "⚡ Connecting database...\n"
            "🔎 Searching records..."
        )

        # API call
        api_data = await asyncio.to_thread(
            call_downloader_api,
            url,
        )

        await status_message.edit_text(
            "⏳ <b>Searching Database...</b>\n\n"
            "⚡ Searching records...\n"
            "🔍 Checking response...\n"
            "📡 Fetching information..."
        )

        video_url = find_video_url(
            api_data
        )

        if not video_url:

            print(
                "[API RESPONSE]",
                json.dumps(
                    api_data,
                    ensure_ascii=False,
                    indent=2,
                )[:10000],
            )

            await status_message.edit_text(
                "❌ <b>Video URL nahi mila.</b>\n\n"
                "API ne downloadable video return nahi kiya."
            )

            return

        await status_message.edit_text(
            "⏳ <b>Searching Database...</b>\n\n"
            "⚡ Video link found.\n"
            "📥 Downloading video...\n"
            "🔄 Please wait..."
        )

        # Download
        await asyncio.to_thread(
            download_video,
            video_url,
            video_file,
        )

        if not video_file.exists():

            raise RuntimeError(
                "Video file was not created"
            )

        file_size = video_file.stat().st_size

        print(
            "[VIDEO SIZE]",
            file_size,
        )

        if file_size <= 0:

            raise RuntimeError(
                "Downloaded file is empty"
            )

        await status_message.edit_text(
            "⏳ <b>Finalizing result...</b>\n\n"
            "📤 Uploading video to Telegram..."
        )

        caption = (
            "🎬 <b>Video Downloaded Successfully</b>\n\n"
            "cradit:- Toxice Babu"
        )

        # Telegram upload retry
        uploaded = False

        for attempt in range(4):

            try:

                await message.reply_video(
                    video=str(video_file),
                    caption=caption,
                    supports_streaming=True,
                )

                uploaded = True
                break

            except FloodWait as e:

                wait_time = int(e.value)

                print(
                    f"[UPLOAD FLOODWAIT] "
                    f"{wait_time}s"
                )

                await asyncio.sleep(
                    wait_time
                )

            except Exception as e:

                print(
                    f"[UPLOAD ERROR] "
                    f"attempt={attempt + 1}: "
                    f"{repr(e)}"
                )

                if attempt < 3:
                    await asyncio.sleep(3)

        if uploaded:

            await status_message.delete()

        else:

            await status_message.edit_text(
                "❌ Video upload failed.\n"
                "Please try again."
            )

    except requests.HTTPError as e:

        print(
            "[HTTP ERROR]",
            repr(e),
        )

        await status_message.edit_text(
            "❌ Downloader API error.\n\n"
            "API response nahi aa raha."
        )

    except Exception as e:

        print(
            "[VIDEO ERROR]",
            repr(e),
        )

        await status_message.edit_text(
            "❌ <b>Download failed.</b>\n\n"
            "Please try again with another link."
        )

    finally:

        try:
            shutil.rmtree(
                temp_dir,
                ignore_errors=True,
            )

        except Exception:
            pass


# =========================================================
# /USER
# =========================================================

@app.on_message(
    filters.command("user")
    & filters.private
)
async def user_command(client, message):

    user = message.from_user

    if not user or user.id != ADMIN_ID:

        await message.reply_text(
            "🚫 Admin only command."
        )

        return

    if not USERS:

        await message.reply_text(
            "📭 No users found."
        )

        return

    lines = []

    lines.append(
        f"👥 <b>Total Users: {len(USERS)}</b>\n"
    )

    count = 1

    for uid, data in USERS.items():

        first_name = data.get(
            "first_name",
            "User",
        )

        last_name = data.get(
            "last_name",
            "",
        )

        username = data.get(
            "username",
            "",
        )

        user_id = data.get(
            "id",
            uid,
        )

        full_name = (
            f"{first_name} {last_name}"
        ).strip()

        if username:

            username_text = (
                f"@{username}"
            )

        else:

            username_text = "No username"

        lines.append(
            f"{count}. "
            f"<b>{full_name}</b>\n"
            f"   Username: {username_text}\n"
            f"   ID: <code>{user_id}</code>\n"
        )

        count += 1

    full_text = "\n".join(lines)

    # Telegram message limit
    chunks = []

    while len(full_text) > 3800:

        cut = full_text.rfind(
            "\n",
            0,
            3800,
        )

        if cut <= 0:
            cut = 3800

        chunks.append(
            full_text[:cut]
        )

        full_text = full_text[cut:]

    if full_text:
        chunks.append(full_text)

    for chunk in chunks:

        await message.reply_text(
            chunk
        )


# =========================================================
# BROADCAST
# =========================================================

async def send_broadcast_to_user(
    user_id,
    text,
):

    try:

        await app.send_message(
            chat_id=user_id,
            text=text,
        )

        return "sent"

    except FloodWait as e:

        wait_time = int(e.value)

        print(
            f"[BROADCAST FLOODWAIT] "
            f"{wait_time}s"
        )

        await asyncio.sleep(
            wait_time
        )

        try:

            await app.send_message(
                chat_id=user_id,
                text=text,
            )

            return "sent"

        except Exception as e:

            print(
                "[BROADCAST RETRY ERROR]",
                user_id,
                repr(e),
            )

            return "failed"

    except (
        UserIsBlocked,
        PeerIdInvalid,
    ):

        return "blocked"

    except Exception as e:

        print(
            "[BROADCAST ERROR]",
            user_id,
            repr(e),
        )

        return "failed"


@app.on_message(
    filters.command("broadcast")
    & filters.private
)
async def broadcast_command(
    client,
    message,
):

    user = message.from_user

    if not user or user.id != ADMIN_ID:

        await message.reply_text(
            "🚫 Admin only command."
        )

        return

    command_text = message.text or ""

    parts = command_text.split(
        maxsplit=2
    )

    if len(parts) < 2:

        await message.reply_text(
            "📢 <b>Broadcast Usage</b>\n\n"
            "<code>/broadcast Hello everyone!</code>\n\n"
            "Personal message:\n"
            "<code>/broadcast 123456789 Hello!</code>"
        )

        return

    target_id = None
    broadcast_text = None

    # Personal broadcast
    if (
        len(parts) >= 3
        and parts[1].lstrip("-").isdigit()
    ):

        try:

            target_id = int(
                parts[1]
            )

        except Exception:

            target_id = None

        broadcast_text = parts[2]

    else:

        broadcast_text = command_text[
            len("/broadcast"):
        ].strip()

    # Personal
    if target_id is not None:

        result = await send_broadcast_to_user(
            target_id,
            broadcast_text,
        )

        if result == "sent":

            await message.reply_text(
                "✅ Personal message sent."
            )

        else:

            await message.reply_text(
                "❌ Personal message failed."
            )

        return

    # All users
    if not broadcast_text:

        await message.reply_text(
            "❌ Broadcast message empty."
        )

        return

    users_snapshot = list(
        USERS.keys()
    )

    total = len(users_snapshot)

    if total == 0:

        await message.reply_text(
            "📭 No registered users."
        )

        return

    progress = await message.reply_text(
        f"📢 <b>Broadcast started</b>\n\n"
        f"👥 Total: {total}\n"
        f"⏳ Sending..."
    )

    sent = 0
    failed = 0
    blocked = 0

    for uid in users_snapshot:

        try:

            user_id = int(uid)

        except Exception:

            failed += 1
            continue

        result = await send_broadcast_to_user(
            user_id,
            broadcast_text,
        )

        if result == "sent":

            sent += 1

        elif result == "blocked":

            blocked += 1

            if uid in USERS:
                del USERS[uid]

        else:

            failed += 1

        # Small delay to reduce flood risk
        await asyncio.sleep(0.08)

    save_users(USERS)

    await progress.edit_text(
        "📢 <b>Broadcast Completed</b>\n\n"
        f"👥 Total: {total}\n"
        f"✅ Sent: {sent}\n"
        f"🚫 Blocked/Removed: {blocked}\n"
        f"❌ Failed: {failed}"
    )


# =========================================================
# /ADD
# =========================================================

@app.on_message(
    filters.command("add")
    & filters.private
)
async def add_command(
    client,
    message,
):

    user = message.from_user

    if not user or user.id != ADMIN_ID:

        await message.reply_text(
            "🚫 Admin only command."
        )

        return

    # Reply mode
    if message.reply_to_message:

        source = message.reply_to_message

        try:

            await app.copy_message(
                chat_id=CHANNEL_TARGET,
                from_chat_id=source.chat.id,
                message_id=source.id,
            )

            await message.reply_text(
                "✅ Message successfully added "
                "to configured channel."
            )

        except Exception as e:

            print(
                "[ADD COPY ERROR]",
                repr(e),
            )

            await message.reply_text(
                "❌ Message channel me add nahi ho paya.\n\n"
                "Check karo:\n"
                "• Bot channel me admin hai\n"
                "• CHANNEL_ID correct hai\n"
                "• Bot ko post permission hai"
            )

        return

    # Text mode
    text = message.text or ""

    parts = text.split(
        maxsplit=1
    )

    if len(parts) < 2:

        await message.reply_text(
            "📌 <b>/add Usage</b>\n\n"
            "Text add karne ke liye:\n"
            "<code>/add Hello Channel!</code>\n\n"
            "Photo/Video/Document/etc add karne ke liye:\n"
            "Message ko reply karke <code>/add</code> bhejo."
        )

        return

    content = parts[1].strip()

    if not content:

        await message.reply_text(
            "❌ Message empty hai."
        )

        return

    try:

        await app.send_message(
            chat_id=CHANNEL_TARGET,
            text=content,
        )

        await message.reply_text(
            "✅ Text successfully added "
            "to configured channel."
        )

    except Exception as e:

        print(
            "[ADD TEXT ERROR]",
            repr(e),
        )

        await message.reply_text(
            "❌ Text channel me send nahi ho paya.\n\n"
            "Bot admin/permission check karo."
        )


# =========================================================
# STARTUP
# =========================================================

async def main():

    print("=" * 60)
    print("Starting Misstu Video Downloader Bot...")
    print("=" * 60)

    # Resolve channel AFTER client starts
    channel_ok = await resolve_channel()

    if not channel_ok:

        print()
        print("⚠️ WARNING:")
        print(
            "Channel resolve nahi hua."
        )
        print(
            "Bot start hoga, lekin join verification "
            "properly work nahi karega."
        )
        print()

    print(
        "🤖 Bot is running..."
    )

    # Keep Pyrogram alive
    await asyncio.Event().wait()


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":

    # Start Flask health server
    threading.Thread(
        target=run_web_server,
        daemon=True,
    ).start()

    # IMPORTANT:
    # client.idle() nahi hai.
    # Pyrogram client.run() use kar rahe hain.
    app.run(main())
