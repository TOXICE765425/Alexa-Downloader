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
from pyrogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton
)
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

DOWNLOADER_API = os.getenv("DOWNLOADER_API")


# ============================================================
# VALIDATE ENVIRONMENT
# ============================================================

required = {
    "BOT_TOKEN": BOT_TOKEN,
    "API_ID": API_ID_RAW,
    "API_HASH": API_HASH,
    "ADMIN_ID": ADMIN_ID_RAW,
    "CHANNEL_ID": CHANNEL_ID_RAW,
    "CHANNEL_URL": CHANNEL_URL,
    "DOWNLOADER_API": DOWNLOADER_API,
}

missing = [
    key for key, value in required.items()
    if not value
]

if missing:
    raise RuntimeError(
        "Missing environment variables: "
        + ", ".join(missing)
    )


try:
    API_ID = int(API_ID_RAW)
except ValueError:
    raise RuntimeError("API_ID must be a number")


try:
    ADMIN_ID = int(ADMIN_ID_RAW)
except ValueError:
    raise RuntimeError("ADMIN_ID must be a number")


# CHANNEL_ID can be:
# -1001234567890
# OR
# @channelusername

CHANNEL_ID = CHANNEL_ID_RAW.strip()

if CHANNEL_ID.lstrip("-").isdigit():
    CHANNEL_ID = int(CHANNEL_ID)


# ============================================================
# FLASK
# ============================================================

web = Flask(__name__)


@web.route("/")
def home():
    return "MISSTU Video Downloader Bot is running"


@web.route("/health")
def health():
    return "OK"


def run_web_server():
    port = int(os.getenv("PORT", "10000"))

    web.run(
        host="0.0.0.0",
        port=port,
        debug=False,
        use_reloader=False
    )


# ============================================================
# USER STORAGE
# ============================================================

DATA_DIR = (
    Path(tempfile.gettempdir())
    / "misstu_video_bot"
)

DATA_DIR.mkdir(
    parents=True,
    exist_ok=True
)

USERS_FILE = DATA_DIR / "users.json"


def load_users():

    if not USERS_FILE.exists():
        return {}

    try:
        with open(
            USERS_FILE,
            "r",
            encoding="utf-8"
        ) as f:
            data = json.load(f)

        # Old list format support
        if isinstance(data, list):

            result = {}

            for user_id in data:

                result[str(user_id)] = {
                    "user_id": int(user_id),
                    "name": "Unknown",
                    "username": ""
                }

            return result

        if isinstance(data, dict):
            return data

    except Exception as e:
        print(
            "Users file read error:",
            e
        )

    return {}


def save_users(users):

    try:

        with open(
            USERS_FILE,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                users,
                f,
                ensure_ascii=False,
                indent=2
            )

    except Exception as e:

        print(
            "Users file save error:",
            e
        )


USERS = load_users()


def add_user(user):

    try:

        user_id = int(user.id)

        first_name = (
            user.first_name or ""
        )

        last_name = (
            user.last_name or ""
        )

        name = (
            f"{first_name} {last_name}"
        ).strip()

        if not name:
            name = "Unknown"

        username = (
            user.username or ""
        )

        USERS[str(user_id)] = {
            "user_id": user_id,
            "name": name,
            "username": username
        }

        save_users(USERS)

    except Exception as e:

        print(
            "Add user error:",
            e
        )


def remove_user(user_id):

    try:

        USERS.pop(
            str(user_id),
            None
        )

        save_users(USERS)

    except Exception as e:

        print(
            "Remove user error:",
            e
        )


# ============================================================
# PYROGRAM
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
# JOIN BUTTONS
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
# CHECK CHANNEL MEMBERSHIP
# ============================================================

async def check_membership(user_id):

    try:

        member = await client.get_chat_member(
            chat_id=CHANNEL_ID,
            user_id=user_id
        )

        status = str(
            member.status
        ).lower()

        print(
            f"[JOIN CHECK] "
            f"user={user_id} "
            f"channel={CHANNEL_ID} "
            f"status={status}"
        )

        # Normal member
        if status == "member":
            return True, None

        # Admin
        if status == "administrator":
            return True, None

        # Owner
        if status == "owner":
            return True, None

        # Restricted member
        if status == "restricted":

            is_member = getattr(
                member,
                "is_member",
                False
            )

            if is_member:
                return True, None

            return False, None

        # Left / kicked
        if status in (
            "left",
            "banned",
            "kicked"
        ):
            return False, None

        return False, None

    except Exception as e:

        print(
            f"[JOIN CHECK ERROR] "
            f"user={user_id}: {e}"
        )

        return False, str(e)


# ============================================================
# REQUIRE JOIN
# ============================================================

async def require_join(message):

    user_id = message.from_user.id

    # Admin bypass
    if user_id == ADMIN_ID:
        return True

    joined, error = await check_membership(
        user_id
    )

    if joined:
        return True

    # Membership API error
    if error:

        print(
            "Membership verification failed:",
            error
        )

        await message.reply_text(
            "⚠️ <b>Join verification problem</b>\n\n"
            "Bot channel membership verify nahi "
            "kar paa raha hai.\n\n"
            "Channel me bot ko Administrator banaye "
            "aur CHANNEL_ID check kare."
        )

        return False

    await message.reply_text(
        "🚫 <b>Access Denied</b>\n\n"
        "Video download karne ke liye "
        "pehle hamara channel join karo.\n\n"
        "Join karne ke baad "
        "<b>✅ Joined</b> dabao.",
        reply_markup=join_keyboard()
    )

    return False


# ============================================================
# /START
# ============================================================

@client.on_message(
    filters.private &
    filters.command("start")
)
async def start_command(client, message):

    user = message.from_user

    add_user(user)

    name = (
        user.first_name
        or "User"
    )

    # --------------------------------------------------------
    # ADMIN
    # --------------------------------------------------------

    if user.id == ADMIN_ID:

        text = (
            f"👋 <b>Welcome {name}!</b>\n\n"
            "🤖 <b>Alexa Video Downloader Bot</b>\n\n"
            "👑 <b>Admin Panel</b>\n\n"
            "🔗 Video link bhejo aur download karo.\n\n"
            "Commands:\n"
            "• /user\n"
            "• /broadcast message\n"
            "• /broadcast USER_ID message\n"
            "• /add message\n"
            "• Reply + /add"
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

                await message.reply_text(
                    text
                )

        except Exception:

            await message.reply_text(
                text
            )

        return

    # --------------------------------------------------------
    # USER JOIN CHECK
    # --------------------------------------------------------

    joined, error = await check_membership(
        user.id
    )

    if not joined:

        text = (
            f"👋 <b>Welcome {name}!</b>\n\n"
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

    # --------------------------------------------------------
    # ALREADY JOINED
    # --------------------------------------------------------

    text = (
        f"👋 <b>Welcome {name}!</b>\n\n"
        "🤖 <b>Alexa Video Downloader Bot</b>\n\n"
        "✅ Channel verified.\n\n"
        "🔗 Bas video ka link bhejo.\n"
        "⚡ Main video download karke "
        "Telegram par bhej dunga."
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

            await message.reply_text(
                text
            )

    except Exception:

        await message.reply_text(
            text
        )


# ============================================================
# JOINED BUTTON
# ============================================================

@client.on_callback_query(
    filters.regex("^check_join$")
)
async def check_join_callback(
    client,
    callback
):

    user = callback.from_user

    add_user(user)

    # Admin bypass
    if user.id == ADMIN_ID:

        joined = True
        error = None

    else:

        joined, error = await check_membership(
            user.id
        )

    # --------------------------------------------------------
    # ERROR
    # --------------------------------------------------------

    if error:

        await callback.answer(
            "⚠️ Verification error. "
            "Bot/channel settings check karo.",
            show_alert=True
        )

        print(
            "Callback join error:",
            error
        )

        return

    # --------------------------------------------------------
    # NOT JOINED
    # --------------------------------------------------------

    if not joined:

        await callback.answer(
            "🚫 Pehle channel join karo!",
            show_alert=True
        )

        return

    # --------------------------------------------------------
    # JOINED
    # --------------------------------------------------------

    await callback.answer(
        "🎉 Congratulations! You are Joined.",
        show_alert=True
    )

    text = (
        f"🎉 <b>Welcome "
        f"{user.first_name or 'User'}!</b>\n\n"
        "✅ <b>Channel membership verified.</b>\n\n"
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

        except Exception as e:

            print(
                "Join success edit error:",
                e
            )


# ============================================================
# /USER
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

        user_id = data.get(
            "user_id",
            ""
        )

        name = data.get(
            "name",
            "Unknown"
        )

        username = data.get(
            "username",
            ""
        )

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

    # Telegram 4096 limit
    while len(result) > 3900:

        split_at = result.rfind(
            "\n",
            0,
            3900
        )

        if split_at <= 0:
            split_at = 3900

        chunk = result[:split_at]

        await message.reply_text(
            chunk
        )

        result = result[split_at:].lstrip()

    if result:
        await message.reply_text(
            result
        )


# ============================================================
# /BROADCAST
#
# /broadcast Hello
#
# /broadcast 123456789 Hello
# ============================================================

@client.on_message(
    filters.private &
    filters.command("broadcast")
)
async def broadcast_command(
    client,
    message
):

    if message.from_user.id != ADMIN_ID:
        return

    text = (
        message.text or ""
    ).strip()

    parts = text.split(
        maxsplit=2
    )

    if len(parts) < 2:

        await message.reply_text(
            "❌ <b>Usage</b>\n\n"
            "All users:\n"
            "<code>/broadcast Hello everyone</code>\n\n"
            "Specific user:\n"
            "<code>/broadcast 123456789 Hello</code>"
        )

        return

    # --------------------------------------------------------
    # SPECIFIC USER
    # --------------------------------------------------------

    if (
        len(parts) == 3
        and parts[1].isdigit()
    ):

        target_id = int(parts[1])
        broadcast_text = parts[2]

        try:

            await client.send_message(
                target_id,
                broadcast_text
            )

            await message.reply_text(
                f"✅ Message sent to "
                f"<code>{target_id}</code>"
            )

        except FloodWait as e:

            await asyncio.sleep(
                e.value
            )

            try:

                await client.send_message(
                    target_id,
                    broadcast_text
                )

                await message.reply_text(
                    f"✅ Message sent to "
                    f"<code>{target_id}</code>"
                )

            except Exception as e2:

                await message.reply_text(
                    f"❌ Failed:\n"
                    f"<code>{e2}</code>"
                )

        except Exception as e:

            await message.reply_text(
                f"❌ Failed:\n"
                f"<code>{e}</code>"
            )

        return

    # --------------------------------------------------------
    # ALL USERS
    # --------------------------------------------------------

    broadcast_text = parts[1]

    users = load_users()

    if not users:

        await message.reply_text(
            "❌ User list empty."
        )

        return

    await message.reply_text(
        f"📢 <b>Broadcast Started</b>\n\n"
        f"👥 Total users: "
        f"<b>{len(users)}</b>"
    )

    sent = 0
    failed = 0

    for user_id in list(users.keys()):

        try:

            await client.send_message(
                int(user_id),
                broadcast_text
            )

            sent += 1

            await asyncio.sleep(
                0.08
            )

        except FloodWait as e:

            print(
                f"FloodWait: {e.value}s"
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

            except Exception as e2:

                print(
                    "Broadcast retry failed:",
                    e2
                )

                failed += 1

        except Exception as e:

            print(
                f"Broadcast failed "
                f"for {user_id}: {e}"
            )

            failed += 1

            # Remove inaccessible users
            remove_user(
                user_id
            )

    await message.reply_text(
        "📢 <b>Broadcast Completed</b>\n\n"
        f"✅ Sent: <b>{sent}</b>\n"
        f"❌ Failed: <b>{failed}</b>"
    )


# ============================================================
# /ADD
#
# /add Hello
#
# OR
#
# Reply to any message + /add
#
# Supports:
# photo
# video
# document
# sticker
# animation
# audio
# voice
# contact
# location
# text
# etc.
# ============================================================

@client.on_message(
    filters.private &
    filters.command("add")
)
async def add_command(
    client,
    message
):

    if message.from_user.id != ADMIN_ID:
        return

    # --------------------------------------------------------
    # REPLY MODE
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
                "✅ Message successfully "
                "added to the channel."
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
                    "✅ Message successfully "
                    "added to the channel."
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
    # TEXT MODE
    # --------------------------------------------------------

    text = (
        message.text or ""
    )

    parts = text.split(
        " ",
        1
    )

    if len(parts) < 2:

        await message.reply_text(
            "❌ <b>Usage</b>\n\n"
            "Text:\n"
            "<code>/add Hello ❤️</code>\n\n"
            "Media:\n"
            "Photo/video/document/sticker etc. "
            "ko reply karo aur <code>/add</code> bhejo."
        )

        return

    content = parts[1].strip()

    if not content:

        await message.reply_text(
            "❌ Message empty hai."
        )

        return

    try:

        await client.send_message(
            chat_id=CHANNEL_ID,
            text=content
        )

        await message.reply_text(
            "✅ Text successfully "
            "added to the channel."
        )

    except FloodWait as e:

        await asyncio.sleep(
            e.value
        )

        try:

            await client.send_message(
                chat_id=CHANNEL_ID,
                text=content
            )

            await message.reply_text(
                "✅ Text successfully "
                "added to the channel."
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
# FIND URL IN API RESPONSE
# ============================================================

def find_video_url(data):

    if isinstance(data, str):

        value = data.strip()

        if (
            value.startswith("https://")
            or value.startswith("http://")
        ):
            return value

        return None

    if isinstance(data, dict):

        preferred_keys = [
            "url",
            "video_url",
            "video",
            "download_url",
            "download",
            "media_url",
            "play_url",
            "playback_url",
            "link",
            "src"
        ]

        # First check common keys
        for key in preferred_keys:

            if key in data:

                result = find_video_url(
                    data[key]
                )

                if result:
                    return result

        # Then recursively search
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
        + quote(
            source_url,
            safe=""
        )
    )

    print(
        "Downloader API request started"
    )

    response = requests.get(
        api_url,
        timeout=90,
        headers={
            "User-Agent":
                "Mozilla/5.0"
        }
    )

    response.raise_for_status()

    content_type = (
        response.headers
        .get(
            "content-type",
            ""
        )
        .lower()
    )

    if "json" in content_type:

        data = response.json()

    else:

        try:
            data = response.json()
        except Exception:
            data = response.text

    video_url = find_video_url(
        data
    )

    if not video_url:

        print(
            "API response:",
            str(data)[:2000]
        )

        raise RuntimeError(
            "API response me video URL nahi mila."
        )

    return video_url


# ============================================================
# DOWNLOAD VIDEO
# ============================================================

def download_video(
    video_url,
    output_path
):

    response = requests.get(
        video_url,
        stream=True,
        timeout=180,
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

    if not os.path.exists(
        output_path
    ):
        raise RuntimeError(
            "Video file create nahi hui."
        )

    size = os.path.getsize(
        output_path
    )

    if size <= 0:
        raise RuntimeError(
            "Downloaded video empty hai."
        )

    return output_path


# ============================================================
# URL DETECTION
# ============================================================

URL_PATTERN = re.compile(
    r"https?://[^\s]+",
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
async def video_downloader(
    client,
    message
):

    # --------------------------------------------------------
    # CHANNEL JOIN
    # --------------------------------------------------------

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
            "❌ <b>Valid video URL bhejo.</b>\n\n"
            "Example:\n"
            "<code>https://example.com/video</code>"
        )

        return

    source_url = match.group(
        0
    )

    status = await message.reply_text(
        "⏳ <b>Processing...</b>\n\n"
        "🔎 Link check ho raha hai..."
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
            "🔎 Downloader API se "
            "video information aa rahi hai..."
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
        # UPLOAD
        # ----------------------------------------------------

        await status.edit_text(
            "📤 <b>Uploading...</b>\n\n"
            "Ruko Jara Sabar Karo..."
        )

        caption = (
            "🎬 <b>Video Downloaded Successfully</b>\n\n"
            "🥰 Alexa Video Downloader"
        )

        uploaded = False

        for attempt in range(4):

            try:

                await client.send_video(
                    chat_id=message.chat.id,
                    video=output_file,
                    caption=caption,
                    supports_streaming=True
                )

                uploaded = True
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

        if not uploaded:

            raise RuntimeError(
                "Telegram upload failed."
            )

        # Delete processing message
        try:

            await status.delete()

        except Exception:
            pass

    except requests.exceptions.Timeout:

        try:

            await status.edit_text(
                "❌ <b>Request Timeout</b>\n\n"
                "API/download server ne time out diya."
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
                "❌ <b>Downloader API Error</b>\n\n"
                "API request complete nahi ho saka."
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
        # CLEAN TEMP FILE
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
# START BOT
# ============================================================

if __name__ == "__main__":

    print("=" * 60)
    print("ALEXA VIDEO DOWNLOADER BOT")
    print("=" * 60)

    print(
        "CHANNEL_ID:",
        CHANNEL_ID
    )

    print(
        "Starting health server..."
    )

    flask_thread = threading.Thread(
        target=run_web_server,
        daemon=True
    )

    flask_thread.start()

    print(
        "Starting Telegram bot..."
    )

    client.run()
