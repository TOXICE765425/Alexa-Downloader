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
from pyrogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from pyrogram.errors import (
    FloodWait,
    UserIsBlocked,
    PeerIdInvalid,
)


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
# CONFIG CHECK
# =========================================================

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN missing")

if not API_ID:
    raise RuntimeError("API_ID missing")

if not API_HASH:
    raise RuntimeError("API_HASH missing")

if not ADMIN_ID:
    raise RuntimeError("ADMIN_ID missing")

if not CHANNEL_ID_RAW:
    raise RuntimeError("CHANNEL_ID missing")

if not CHANNEL_URL:
    raise RuntimeError("CHANNEL_URL missing")

if not DOWNLOADER_API:
    raise RuntimeError("DOWNLOADER_API missing")


try:
    API_ID = int(API_ID)
except Exception:
    raise RuntimeError("API_ID must be numeric")


try:
    ADMIN_ID = int(ADMIN_ID)
except Exception:
    raise RuntimeError("ADMIN_ID must be numeric")


# =========================================================
# CHANNEL TARGET
# =========================================================

CHANNEL_ID_RAW = CHANNEL_ID_RAW.strip()

if CHANNEL_ID_RAW.lstrip("-").isdigit():
    CHANNEL_TARGET = int(CHANNEL_ID_RAW)
else:
    if CHANNEL_ID_RAW.startswith("https://t.me/"):
        CHANNEL_ID_RAW = CHANNEL_ID_RAW.replace(
            "https://t.me/",
            "",
            1,
        )

    if CHANNEL_ID_RAW.startswith("t.me/"):
        CHANNEL_ID_RAW = CHANNEL_ID_RAW.replace(
            "t.me/",
            "",
            1,
        )

    if not CHANNEL_ID_RAW.startswith("@"):
        CHANNEL_ID_RAW = "@" + CHANNEL_ID_RAW

    CHANNEL_TARGET = CHANNEL_ID_RAW


# =========================================================
# PYROGRAM CLIENT
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
# FLASK
# =========================================================

web = Flask(__name__)


@web.route("/")
def home():
    return "MISSTU Video Downloader Bot is running", 200


@web.route("/health")
def health():
    return "OK", 200


def run_web_server():

    port = int(
        os.getenv("PORT", "10000")
    )

    web.run(
        host="0.0.0.0",
        port=port,
        debug=False,
        use_reloader=False,
    )


# =========================================================
# USERS
# =========================================================

DATA_DIR = Path(
    tempfile.gettempdir()
) / "misstu_video_bot"

DATA_DIR.mkdir(
    parents=True,
    exist_ok=True,
)

USERS_FILE = DATA_DIR / "users.json"


def load_users():

    if not USERS_FILE.exists():
        return {}

    try:

        with open(
            USERS_FILE,
            "r",
            encoding="utf-8",
        ) as f:

            data = json.load(f)

        if isinstance(data, dict):
            return data

    except Exception as e:

        print(
            "[USERS LOAD ERROR]",
            repr(e),
        )

    return {}


def save_users():

    try:

        temp_file = USERS_FILE.with_suffix(
            ".tmp"
        )

        with open(
            temp_file,
            "w",
            encoding="utf-8",
        ) as f:

            json.dump(
                USERS,
                f,
                ensure_ascii=False,
                indent=2,
            )

        temp_file.replace(
            USERS_FILE
        )

    except Exception as e:

        print(
            "[USERS SAVE ERROR]",
            repr(e),
        )


USERS = load_users()


def add_user(user):

    if not user:
        return

    user_id = str(user.id)

    USERS[user_id] = {
        "id": user.id,
        "first_name": user.first_name or "",
        "last_name": user.last_name or "",
        "username": user.username or "",
        "updated_at": int(time.time()),
    }

    save_users()


# =========================================================
# CHANNEL RESOLVE
# =========================================================

async def resolve_channel():

    print("=" * 60)
    print("CHANNEL CHECK")
    print("CHANNEL_ID:", CHANNEL_TARGET)
    print("CHANNEL_URL:", CHANNEL_URL)
    print("=" * 60)

    try:

        chat = await app.get_chat(
            CHANNEL_TARGET
        )

        print("✅ CHANNEL FOUND")
        print("Title:", chat.title)
        print("ID:", chat.id)
        print("Username:", chat.username)

        return True

    except PeerIdInvalid:

        print(
            "❌ PeerIdInvalid"
        )

        print(
            "Bot ko exact channel me ADMIN banao."
        )

        return False

    except Exception as e:

        print(
            "❌ CHANNEL ERROR:",
            repr(e),
        )

        return False


# =========================================================
# JOIN CHECK
# =========================================================

async def is_user_joined(
    user_id,
    retries=3,
):

    # Admin always allowed
    if user_id == ADMIN_ID:
        return True, None

    for attempt in range(retries):

        try:

            member = await app.get_chat_member(
                chat_id=CHANNEL_TARGET,
                user_id=user_id,
            )

            status = member.status

            if hasattr(
                status,
                "value",
            ):
                status = status.value

            status = str(
                status
            ).lower().strip()

            print(
                f"[JOIN CHECK] "
                f"user={user_id} "
                f"status={status}"
            )

            if status in (
                "member",
                "administrator",
                "admin",
                "owner",
                "creator",
            ):
                return True, None

            if status == "restricted":

                return (
                    bool(
                        getattr(
                            member,
                            "is_member",
                            False,
                        )
                    ),
                    None,
                )

            if status in (
                "left",
                "kicked",
                "banned",
            ):
                return False, None

            return False, None

        except FloodWait as e:

            wait = int(e.value)

            print(
                f"[JOIN FLOODWAIT] {wait}s"
            )

            await asyncio.sleep(
                wait
            )

        except Exception as e:

            print(
                "[JOIN CHECK ERROR]",
                repr(e),
            )

            if attempt < retries - 1:

                await asyncio.sleep(
                    1.5
                )

    return (
        False,
        "Unable to verify channel membership",
    )


# =========================================================
# JOIN KEYBOARD
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
# WELCOME
# =========================================================

def welcome_text(
    user,
    verified=False,
):

    name = (
        user.first_name
        or "User"
    )

    if verified:

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
        "⚠️ Bot use karne ke liye pehle "
        "hamara channel join karo.\n\n"
        "👇 Join karne ke baad "
        "<b>Joined</b> button dabao."
    )


# =========================================================
# START
# =========================================================

@app.on_message(
    filters.command("start")
    & filters.private
)
async def start_handler(
    client,
    message,
):

    user = message.from_user

    if not user:
        return

    add_user(user)

    joined, error = await is_user_joined(
        user.id
    )

    if error:

        await message.reply_text(
            "⚠️ Channel verification error.\n\n"
            "Please try again later."
        )

        return

    text = welcome_text(
        user,
        joined,
    )

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
                reply_markup=(
                    None
                    if joined
                    else join_keyboard()
                ),
            )

        else:

            await message.reply_text(
                text,
                reply_markup=(
                    None
                    if joined
                    else join_keyboard()
                ),
            )

    except Exception as e:

        print(
            "[START PHOTO ERROR]",
            repr(e),
        )

        await message.reply_text(
            text,
            reply_markup=(
                None
                if joined
                else join_keyboard()
            ),
        )


# =========================================================
# JOINED BUTTON
# =========================================================

@app.on_callback_query(
    filters.regex("^check_join$")
)
async def joined_callback(
    client,
    callback_query,
):

    user = callback_query.from_user

    if not user:
        return

    add_user(user)

    joined, error = await is_user_joined(
        user.id,
        retries=3,
    )

    if error:

        await callback_query.answer(
            "⚠️ Verification error. "
            "Please try again.",
            show_alert=True,
        )

        return

    if not joined:

        await callback_query.answer(
            "🚫 Pehle channel join karo!",
            show_alert=True,
        )

        return

    await callback_query.answer(
        "🎉 Congratulations! You are Joined.",
        show_alert=True,
    )

    text = welcome_text(
        user,
        True,
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
            "[JOIN EDIT ERROR]",
            repr(e),
        )


# =========================================================
# URL
# =========================================================

def valid_url(text):

    if not text:
        return False

    return text.lower().startswith(
        (
            "http://",
            "https://",
        )
    )


# =========================================================
# FIND VIDEO
# =========================================================

def find_video_url(data):

    if isinstance(data, str):

        if data.startswith(
            (
                "http://",
                "https://",
            )
        ):

            return data

        return None

    if isinstance(data, dict):

        keys = [
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

        for key in keys:

            if key in data:

                result = find_video_url(
                    data[key]
                )

                if result:
                    return result

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


# =========================================================
# API
# =========================================================

def call_api(url):

    endpoint = (
        DOWNLOADER_API
        + url
    )

    print(
        "[API]",
        endpoint,
    )

    response = requests.get(
        endpoint,
        timeout=120,
        headers={
            "User-Agent": "Mozilla/5.0"
        },
    )

    response.raise_for_status()

    try:

        return response.json()

    except Exception:

        text = response.text.strip()

        if text.startswith(
            (
                "http://",
                "https://",
            )
        ):
            return text

        raise RuntimeError(
            "Invalid API response"
        )


# =========================================================
# DOWNLOAD
# =========================================================

def download_file(
    video_url,
    path,
):

    with requests.get(
        video_url,
        stream=True,
        timeout=180,
        headers={
            "User-Agent": "Mozilla/5.0"
        },
    ) as response:

        response.raise_for_status()

        with open(
            path,
            "wb",
        ) as f:

            for chunk in response.iter_content(
                chunk_size=1024 * 1024
            ):

                if chunk:
                    f.write(chunk)

    return path


# =========================================================
# VIDEO DOWNLOADER
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
async def video_handler(
    client,
    message,
):

    user = message.from_user

    if not user:
        return

    add_user(user)

    # Join check
    if user.id != ADMIN_ID:

        joined, error = await is_user_joined(
            user.id
        )

        if error:

            await message.reply_text(
                "⚠️ Channel verification failed."
            )

            return

        if not joined:

            await message.reply_text(
                "🚫 Pehle channel join karo.",
                reply_markup=join_keyboard(),
            )

            return

    url = message.text.strip()

    if not valid_url(url):

        await message.reply_text(
            "❌ Valid video URL bhejo."
        )

        return

    status = await message.reply_text(
        "⏳ <b>Searching Database...</b>\n\n"
        "⚡ Connecting database..."
    )

    temp_dir = Path(
        tempfile.mkdtemp(
            prefix="misstu_"
        )
    )

    video_path = (
        temp_dir / "video.mp4"
    )

    try:

        await status.edit_text(
            "⏳ <b>Searching Database...</b>\n\n"
            "⚡ Connecting database...\n"
            "🔎 Searching records..."
        )

        api_data = await asyncio.to_thread(
            call_api,
            url,
        )

        await status.edit_text(
            "⏳ <b>Searching Database...</b>\n\n"
            "🔎 Searching records...\n"
            "🔍 Checking response...\n"
            "📡 Fetching information..."
        )

        video_url = find_video_url(
            api_data
        )

        if not video_url:

            print(
                json.dumps(
                    api_data,
                    ensure_ascii=False,
                    indent=2,
                )[:10000]
            )

            await status.edit_text(
                "❌ Video URL nahi mila."
            )

            return

        await status.edit_text(
            "⏳ <b>Downloading...</b>\n\n"
            "📥 Video download ho raha hai...\n"
            "🔄 Please wait..."
        )

        await asyncio.to_thread(
            download_file,
            video_url,
            video_path,
        )

        if (
            not video_path.exists()
            or video_path.stat().st_size <= 0
        ):

            raise RuntimeError(
                "Downloaded file empty"
            )

        await status.edit_text(
            "📤 <b>Uploading Video...</b>\n\n"
            "⚡ Please wait..."
        )

        caption = (
            "🎬 <b>Video Downloaded Successfully</b>\n\n"
            "cradit:- Toxice Babu"
        )

        uploaded = False

        for attempt in range(4):

            try:

                await message.reply_video(
                    video=str(video_path),
                    caption=caption,
                    supports_streaming=True,
                )

                uploaded = True
                break

            except FloodWait as e:

                await asyncio.sleep(
                    int(e.value)
                )

            except Exception as e:

                print(
                    "[UPLOAD ERROR]",
                    repr(e),
                )

                await asyncio.sleep(3)

        if uploaded:

            await status.delete()

        else:

            await status.edit_text(
                "❌ Video upload failed."
            )

    except Exception as e:

        print(
            "[VIDEO ERROR]",
            repr(e),
        )

        await status.edit_text(
            "❌ <b>Download failed.</b>\n\n"
            "Please try again."
        )

    finally:

        shutil.rmtree(
            temp_dir,
            ignore_errors=True,
        )


# =========================================================
# /USER
# =========================================================

@app.on_message(
    filters.command("user")
    & filters.private
)
async def user_command(
    client,
    message,
):

    user = message.from_user

    if not user or user.id != ADMIN_ID:

        await message.reply_text(
            "🚫 Admin only."
        )

        return

    if not USERS:

        await message.reply_text(
            "📭 No users found."
        )

        return

    text = (
        f"👥 <b>Total Users: "
        f"{len(USERS)}</b>\n\n"
    )

    for i, data in enumerate(
        USERS.values(),
        1,
    ):

        name = (
            f"{data.get('first_name', '')} "
            f"{data.get('last_name', '')}"
        ).strip()

        username = data.get(
            "username",
            "",
        )

        uid = data.get(
            "id",
            "",
        )

        username_text = (
            "@" + username
            if username
            else "No username"
        )

        item = (
            f"{i}. <b>{name}</b>\n"
            f"Username: {username_text}\n"
            f"ID: <code>{uid}</code>\n\n"
        )

        if len(text) + len(item) > 3800:

            await message.reply_text(
                text
            )

            text = ""

        text += item

    if text:

        await message.reply_text(
            text
        )


# =========================================================
# BROADCAST
# =========================================================

async def send_broadcast(
    user_id,
    text,
):

    try:

        await app.send_message(
            user_id,
            text,
        )

        return "sent"

    except FloodWait as e:

        await asyncio.sleep(
            int(e.value)
        )

        try:

            await app.send_message(
                user_id,
                text,
            )

            return "sent"

        except Exception:

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
            "🚫 Admin only."
        )

        return

    parts = (
        message.text or ""
    ).split(
        maxsplit=2
    )

    if len(parts) < 2:

        await message.reply_text(
            "📢 Usage:\n\n"
            "/broadcast Hello everyone!\n\n"
            "/broadcast 123456789 Hello!"
        )

        return

    # Personal
    if (
        len(parts) == 3
        and parts[1].lstrip("-").isdigit()
    ):

        target = int(parts[1])
        text = parts[2]

        result = await send_broadcast(
            target,
            text,
        )

        await message.reply_text(
            "✅ Message sent."
            if result == "sent"
            else "❌ Message failed."
        )

        return

    # All users
    text = message.text[
        len("/broadcast"):
    ].strip()

    if not text:

        await message.reply_text(
            "❌ Message empty."
        )

        return

    progress = await message.reply_text(
        f"📢 <b>Broadcast Started</b>\n\n"
        f"👥 Users: {len(USERS)}"
    )

    sent = 0
    failed = 0
    blocked = 0

    for uid in list(USERS.keys()):

        try:
            user_id = int(uid)
        except Exception:
            failed += 1
            continue

        result = await send_broadcast(
            user_id,
            text,
        )

        if result == "sent":
            sent += 1

        elif result == "blocked":
            blocked += 1
            USERS.pop(uid, None)

        else:
            failed += 1

        await asyncio.sleep(
            0.08
        )

    save_users()

    await progress.edit_text(
        "📢 <b>Broadcast Completed</b>\n\n"
        f"👥 Total: {sent + failed + blocked}\n"
        f"✅ Sent: {sent}\n"
        f"🚫 Blocked: {blocked}\n"
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
            "🚫 Admin only."
        )

        return

    # Reply to any message
    if message.reply_to_message:

        source = message.reply_to_message

        try:

            await app.copy_message(
                chat_id=CHANNEL_TARGET,
                from_chat_id=source.chat.id,
                message_id=source.id,
            )

            await message.reply_text(
                "✅ Message channel me add ho gaya."
            )

        except Exception as e:

            print(
                "[ADD ERROR]",
                repr(e),
            )

            await message.reply_text(
                "❌ Channel me message add nahi hua.\n\n"
                "Bot ko channel ka ADMIN banao "
                "aur posting permission do."
            )

        return

    # Normal text
    parts = (
        message.text or ""
    ).split(
        maxsplit=1
    )

    if len(parts) < 2:

        await message.reply_text(
            "📌 <b>Usage</b>\n\n"
            "/add Hello Channel\n\n"
            "Ya kisi message ko reply karke:\n"
            "/add"
        )

        return

    text = parts[1].strip()

    try:

        await app.send_message(
            CHANNEL_TARGET,
            text,
        )

        await message.reply_text(
            "✅ Text channel me add ho gaya."
        )

    except Exception as e:

        print(
            "[ADD TEXT ERROR]",
            repr(e),
        )

        await message.reply_text(
            "❌ Text send nahi hua."
        )


# =========================================================
# MAIN
# =========================================================

async def main():

    print("=" * 60)
    print("STARTING TELEGRAM CLIENT")
    print("=" * 60)

    # IMPORTANT:
    # Client is started FIRST.
    await app.start()

    print("✅ Pyrogram client started")

    # NOW channel can be resolved
    channel_ok = await resolve_channel()

    if channel_ok:

        print(
            "✅ Channel verification ready"
        )

    else:

        print(
            "⚠️ Channel resolve failed."
        )

        print(
            "Bot running rahega, "
            "but join verification won't work "
            "until CHANNEL_ID/Bot permissions are fixed."
        )

    print("=" * 60)
    print("🤖 BOT IS RUNNING")
    print("=" * 60)

    # Keep process alive
    try:

        await asyncio.Event().wait()

    finally:

        print(
            "Stopping bot..."
        )

        await app.stop()


# =========================================================
# START
# =========================================================

if __name__ == "__main__":

    # Flask first
    threading.Thread(
        target=run_web_server,
        daemon=True,
    ).start()

    # Correct Pyrogram lifecycle
    asyncio.run(
        main()
    )
