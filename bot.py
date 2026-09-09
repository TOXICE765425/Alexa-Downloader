import os
import json
import time
import asyncio
import tempfile
import threading
from pathlib import Path

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

DOWNLOADER_API = os.getenv("DOWNLOADER_API")


# ============================================================
# REQUIRED ENVIRONMENT VARIABLES CHECK
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
        "API_ID, ADMIN_ID and CHANNEL_ID must be numeric"
    )


# ============================================================
# DATA DIRECTORY
# ============================================================

DATA_DIR = Path(tempfile.gettempdir()) / "misstu_video_bot"
DATA_DIR.mkdir(parents=True, exist_ok=True)

USERS_FILE = DATA_DIR / "users.json"


# ============================================================
# USER DATABASE
# ============================================================

def load_users():
    try:
        if USERS_FILE.exists():
            with USERS_FILE.open("r", encoding="utf-8") as f:
                data = json.load(f)

            return set(int(x) for x in data)

    except Exception as e:
        print("❌ User database read error:", repr(e), flush=True)

    return set()


users = load_users()


def save_users():
    try:
        with USERS_FILE.open("w", encoding="utf-8") as f:
            json.dump(
                sorted(users),
                f,
                ensure_ascii=False,
                indent=2
            )

    except Exception as e:
        print(
            "❌ User database write error:",
            repr(e),
            flush=True
        )


def add_user(user_id):
    if user_id not in users:
        users.add(user_id)
        save_users()


# ============================================================
# PYROGRAM CLIENT
# ============================================================

app = Client(
    "misstu_video_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    in_memory=True,
    ipv6=False,
    max_concurrent_transmissions=1,
)


# ============================================================
# FLASK HEALTH SERVER
# ============================================================

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


# ============================================================
# CHANNEL KEYBOARD
# ============================================================

def join_keyboard():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "📢 Join Channel",
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
# CHANNEL MEMBERSHIP CHECK
# ============================================================

async def is_user_joined(user_id, retries=3):

    # Admin ko channel join requirement nahi
    if user_id == ADMIN_ID:
        return True, None

    for attempt in range(1, retries + 1):

        try:
            member = await app.get_chat_member(
                chat_id=CHANNEL_ID,
                user_id=user_id
            )

            status = getattr(member, "status", "")

            if hasattr(status, "value"):
                status = status.value

            status = str(status).lower().strip()

            print(
                f"🔍 Membership check | User: {user_id} | Status: {status}",
                flush=True
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
                is_member = getattr(
                    member,
                    "is_member",
                    False
                )

                return bool(is_member), None

            if status in (
                "left",
                "kicked",
                "banned",
            ):
                return False, None

            return False, None

        except FloodWait as e:

            wait_time = int(
                getattr(e, "value", 1)
            )

            print(
                f"⚠️ Membership FloodWait: {wait_time}s",
                flush=True
            )

            await asyncio.sleep(wait_time + 1)

        except Exception as e:

            error_name = type(e).__name__
            error_text = str(e)

            print(
                f"❌ [JOIN ERROR] "
                f"Attempt {attempt}/{retries} | "
                f"User {user_id} | "
                f"{error_name}: {error_text}",
                flush=True
            )

            lower_error = error_text.lower()

            if (
                "user not participant" in lower_error
                or "user_not_participant" in lower_error
                or "participant_id_invalid" in lower_error
            ):
                return False, None

            if attempt < retries:
                await asyncio.sleep(1)

    return (
        False,
        "Channel membership verification failed. "
        "Check bot admin permission and CHANNEL_ID."
    )


# ============================================================
# GET USER PROFILE PHOTO
# ============================================================

async def get_user_photo(user_id):

    try:

        async for photo in app.get_chat_photos(
            user_id,
            limit=1
        ):
            return photo.file_id

    except Exception as e:

        print(
            "Profile photo error:",
            repr(e),
            flush=True
        )

    return None


# ============================================================
# SAFE DELETE
# ============================================================

async def safe_delete(message):

    try:
        await message.delete()

    except Exception:
        pass


# ============================================================
# FIND VIDEO URL FROM API RESPONSE
# ============================================================

def find_video_url(data):

    if isinstance(data, str):

        if data.startswith(
            ("http://", "https://")
        ):

            lower = data.lower()

            if any(
                x in lower
                for x in (
                    ".mp4",
                    ".mkv",
                    ".webm",
                    ".mov",
                    ".m4v",
                    ".avi",
                )
            ):
                return data

            if (
                "video" in lower
                or "media" in lower
            ):
                return data

        return None


    if isinstance(data, dict):

        preferred = (
            "video",
            "video_url",
            "download_url",
            "url",
            "link",
            "media",
            "result",
        )

        for key in preferred:

            if key in data:

                found = find_video_url(
                    data[key]
                )

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


# ============================================================
# DOWNLOAD VIDEO
# ============================================================

async def download_video(
    url,
    output_file
):

    api_url = (
        DOWNLOADER_API
        + requests.utils.quote(
            url,
            safe=""
        )
    )


    def do_download():

        print(
            "🔗 Downloader API:",
            api_url,
            flush=True
        )

        response = requests.get(
            api_url,
            timeout=60
        )

        response.raise_for_status()


        try:

            data = response.json()

        except Exception as e:

            raise RuntimeError(
                "Downloader API ne valid JSON response diya."
            ) from e


        video_url = find_video_url(data)


        if not video_url:

            raise RuntimeError(
                "API response me video URL nahi mila."
            )


        print(
            "🎬 Video URL found:",
            video_url,
            flush=True
        )


        with requests.get(
            video_url,
            stream=True,
            timeout=(30, 180)
        ) as r:

            r.raise_for_status()


            with output_file.open(
                "wb"
            ) as f:

                for chunk in r.iter_content(
                    chunk_size=1024 * 1024
                ):

                    if chunk:
                        f.write(chunk)


    await asyncio.to_thread(
        do_download
    )


# ============================================================
# START COMMAND
# ============================================================

@app.on_message(
    filters.command("start")
)
async def start_handler(
    client,
    message
):

    user = message.from_user

    if not user:
        return


    add_user(user.id)


    name = user.first_name or "User"


    # --------------------------------------------------------
    # Membership check
    # --------------------------------------------------------

    joined, error = await is_user_joined(
        user.id
    )


    # --------------------------------------------------------
    # Verification error
    # --------------------------------------------------------

    if error:

        print(
            f"⚠️ START JOIN VERIFICATION ERROR "
            f"User={user.id}: {error}",
            flush=True
        )

        text = (
            f"👋 <b>Welcome {name}!</b>\n\n"
            "⚠️ <b>Channel verification temporarily failed.</b>\n\n"
            "Please join the channel and press "
            "<b>Joined</b> again."
        )

        photo = await get_user_photo(
            user.id
        )

        try:

            if photo:

                await message.reply_photo(
                    photo=photo,
                    caption=text,
                    reply_markup=join_keyboard()
                )

            else:

                await message.reply_text(
                    text,
                    reply_markup=join_keyboard()
                )

        except Exception as e:

            print(
                "Start verification reply error:",
                repr(e),
                flush=True
            )

        return


    # --------------------------------------------------------
    # User not joined
    # --------------------------------------------------------

    if not joined:

        text = (
            f"👋 <b>Welcome {name}!</b>\n\n"
            "🔐 <b>Access Required</b>\n\n"
            "Bot use karne ke liye pehle hamare "
            "channel ko join karo.\n\n"
            "📢 <b>Join Channel</b> par click karo.\n"
            "Join karne ke baad neeche "
            "<b>Joined</b> button press karo."
        )

        # User ka Telegram profile DP
        photo = await get_user_photo(
            user.id
        )

        try:

            if photo:

                await message.reply_photo(
                    photo=photo,
                    caption=text,
                    reply_markup=join_keyboard()
                )

            else:

                await message.reply_text(
                    text,
                    reply_markup=join_keyboard()
                )

        except Exception as e:

            print(
                "Join message error:",
                repr(e),
                flush=True
            )

            try:

                await message.reply_text(
                    text,
                    reply_markup=join_keyboard()
                )

            except Exception:
                pass

        return


    # --------------------------------------------------------
    # Already joined
    # --------------------------------------------------------

    welcome = (
        f"👋 <b>Welcome {name}!</b>\n\n"
        "🎬 <b>MISSTU Video Downloader</b>\n\n"
        "📥 Mujhe kisi supported video ka link bhejo.\n"
        "Main uska video download karke yahin bhej dunga.\n\n"
        "⚡ <b>Fast • Simple • Easy</b>"
    )


    photo = await get_user_photo(
        user.id
    )


    try:

        if photo:

            await message.reply_photo(
                photo=photo,
                caption=welcome
            )

        else:

            await message.reply_text(
                welcome
            )

    except Exception as e:

        print(
            "Welcome error:",
            repr(e),
            flush=True
        )

        try:

            await message.reply_text(
                welcome
            )

        except Exception:
            pass


# ============================================================
# JOINED BUTTON
# ============================================================

@app.on_callback_query(
    filters.regex("^check_join$")
)
async def check_join_callback(
    client,
    callback_query
):

    user = callback_query.from_user

    if not user:
        return


    joined, error = await is_user_joined(
        user.id
    )


    # --------------------------------------------------------
    # Verification error
    # --------------------------------------------------------

    if error:

        print(
            f"⚠️ CALLBACK JOIN ERROR "
            f"User={user.id}: {error}",
            flush=True
        )

        await callback_query.answer(
            "⚠️ Verification failed. Try again.",
            show_alert=True
        )

        return


    # --------------------------------------------------------
    # Not joined
    # --------------------------------------------------------

    if not joined:

        await callback_query.answer(
            "❌ Aapne abhi channel join nahi kiya.",
            show_alert=True
        )

        return


    # --------------------------------------------------------
    # Joined successfully
    # --------------------------------------------------------

    await callback_query.answer(
        "✅ Verified!"
    )


    name = user.first_name or "User"


    welcome = (
        f"👋 <b>Welcome {name}!</b>\n\n"
        "🎬 <b>MISSTU Video Downloader</b>\n\n"
        "📥 Mujhe kisi supported video ka link bhejo.\n"
        "Main uska video download karke yahin bhej dunga.\n\n"
        "⚡ <b>Fast • Simple • Easy</b>"
    )


    try:

        await callback_query.message.edit_text(
            welcome
        )

    except Exception as e:

        print(
            "Joined edit error:",
            repr(e),
            flush=True
        )


# ============================================================
# BROADCAST COMMAND
# ============================================================

@app.on_message(
    filters.command("broadcast")
    & filters.private
)
async def broadcast_handler(
    client,
    message
):

    if not message.from_user:
        return


    if message.from_user.id != ADMIN_ID:

        await message.reply_text(
            "❌ You are not authorized to use this command."
        )

        return


    parts = (
        message.text or ""
    ).split(
        maxsplit=1
    )


    if len(parts) < 2:

        await message.reply_text(
            "❌ Broadcast message missing.\n\n"
            "Examples:\n\n"
            "<code>/broadcast Hello everyone!</code>\n\n"
            "<code>/broadcast 123456789 Hello!</code>"
        )

        return


    content = parts[1].strip()


    if not content:

        await message.reply_text(
            "❌ Message empty hai."
        )

        return


    # ========================================================
    # PERSONAL BROADCAST
    # /broadcast USER_ID message
    # ========================================================

    first_space = content.find(" ")


    if first_space > 0:

        possible_id = content[:first_space].strip()

        try:
            target_user_id = int(
                possible_id
            )

            personal_text = content[
                first_space + 1:
            ].strip()

        except ValueError:

            target_user_id = None
            personal_text = None

    else:

        target_user_id = None
        personal_text = None


    if (
        target_user_id is not None
        and personal_text
    ):

        try:

            await client.send_message(
                target_user_id,
                personal_text
            )

            await message.reply_text(
                "✅ <b>Personal message sent.</b>\n\n"
                f"👤 User ID: <code>{target_user_id}</code>"
            )

        except FloodWait as e:

            wait_time = int(
                getattr(e, "value", 1)
            )

            await message.reply_text(
                f"⏳ Telegram FloodWait.\n"
                f"Please wait {wait_time} seconds."
            )

        except Exception as e:

            await message.reply_text(
                "❌ Personal message failed.\n\n"
                f"<code>{str(e)[:500]}</code>"
            )

        return


    # ========================================================
    # NORMAL BROADCAST
    # /broadcast message
    # ========================================================

    text = content

    user_list = list(users)


    if not user_list:

        await message.reply_text(
            "❌ Abhi koi registered user nahi mila."
        )

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

            await client.send_message(
                user_id,
                text
            )

            sent += 1

            await asyncio.sleep(0.1)


        except FloodWait as e:

            wait_time = int(
                getattr(e, "value", 1)
            )

            print(
                f"⚠️ Broadcast FloodWait: {wait_time}s",
                flush=True
            )

            await asyncio.sleep(
                wait_time + 1
            )


            try:

                await client.send_message(
                    user_id,
                    text
                )

                sent += 1

            except Exception as err:

                print(
                    f"Broadcast retry failed "
                    f"for {user_id}: {err}",
                    flush=True
                )

                failed += 1


        except Exception as e:

            print(
                f"Broadcast failed "
                f"for {user_id}: {e}",
                flush=True
            )

            failed += 1


            error_text = str(e).upper()


            if (
                "USER_IS_BLOCKED"
                in error_text
                or
                "USER_DEACTIVATED"
                in error_text
            ):

                users.discard(
                    user_id
                )


        # Update progress every 10 users
        if (
            (sent + failed) % 10 == 0
        ):

            try:

                await status.edit_text(
                    f"📢 <b>Broadcast running...</b>\n\n"
                    f"👥 Total: <code>{len(user_list)}</code>\n"
                    f"📤 Sent: <code>{sent}</code>\n"
                    f"❌ Failed: <code>{failed}</code>"
                )

            except Exception:
                pass


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


# ============================================================
# USER LIST COMMAND
# ============================================================

@app.on_message(
    filters.command("user")
    & filters.private
)
async def user_handler(
    client,
    message
):

    if not message.from_user:
        return


    if message.from_user.id != ADMIN_ID:

        await message.reply_text(
            "❌ You are not authorized."
        )

        return


    user_list = sorted(
        list(users)
    )


    if not user_list:

        await message.reply_text(
            "❌ Koi registered user nahi hai."
        )

        return


    await message.reply_text(
        f"👥 <b>Total Users:</b> "
        f"<code>{len(user_list)}</code>\n\n"
        "⏳ User details collect kar raha hoon..."
    )


    chunks = []
    current = ""


    for index, user_id in enumerate(
        user_list,
        start=1
    ):

        try:

            chat = await client.get_chat(
                user_id
            )


            first_name = (
                chat.first_name
                or "Unknown"
            )

            username = (
                f"@{chat.username}"
                if chat.username
                else "No username"
            )


            entry = (
                f"{index}. "
                f"<b>{first_name}</b>\n"
                f"   👤 {username}\n"
                f"   🆔 <code>{user_id}</code>\n\n"
            )


        except Exception:

            entry = (
                f"{index}. "
                f"<b>Unknown User</b>\n"
                f"   🆔 <code>{user_id}</code>\n\n"
            )


        if len(current) + len(entry) > 3800:

            chunks.append(
                current
            )

            current = entry

        else:

            current += entry


    if current:
        chunks.append(current)


    for i, chunk in enumerate(
        chunks,
        start=1
    ):

        header = (
            f"👥 <b>REGISTERED USERS</b>\n"
            f"📄 Page {i}/{len(chunks)}\n\n"
        )

        try:

            await message.reply_text(
                header + chunk
            )

        except Exception as e:

            print(
                "User list send error:",
                repr(e),
                flush=True
            )


# ============================================================
# ADD TO CONFIGURED CHANNEL
#
# /add Hello
#
# OR
#
# Reply to ANY Telegram message with /add
# ============================================================

@app.on_message(
    filters.command("add")
)
async def add_handler(
    client,
    message
):

    if not message.from_user:
        return


    if message.from_user.id != ADMIN_ID:

        await message.reply_text(
            "❌ You are not authorized."
        )

        return


    # ========================================================
    # REPLY MODE
    # ========================================================

    if message.reply_to_message:

        source = message.reply_to_message


        try:

            await client.copy_message(
                chat_id=CHANNEL_ID,
                from_chat_id=source.chat.id,
                message_id=source.id
            )


            await message.reply_text(
                "✅ <b>Message successfully added "
                "to the configured channel.</b>"
            )


        except FloodWait as e:

            wait_time = int(
                getattr(e, "value", 1)
            )

            await message.reply_text(
                f"⏳ Telegram FloodWait.\n"
                f"Try again after {wait_time} seconds."
            )


        except Exception as e:

            print(
                "ADD COPY ERROR:",
                repr(e),
                flush=True
            )


            await message.reply_text(
                "❌ Channel mein message add nahi ho saka.\n\n"
                f"<code>{str(e)[:500]}</code>"
            )


        return


    # ========================================================
    # TEXT MODE
    # ========================================================

    parts = (
        message.text or ""
    ).split(
        maxsplit=1
    )


    if len(parts) < 2:

        await message.reply_text(
            "❌ Message missing.\n\n"
            "Example:\n"
            "<code>/add Hello Channel!</code>\n\n"
            "Ya kisi message ko reply karke:\n"
            "<code>/add</code>"
        )

        return


    text = parts[1].strip()


    try:

        await client.send_message(
            chat_id=CHANNEL_ID,
            text=text
        )


        await message.reply_text(
            "✅ <b>Text successfully added "
            "to the configured channel.</b>"
        )


    except FloodWait as e:

        wait_time = int(
            getattr(e, "value", 1)
        )

        await message.reply_text(
            f"⏳ Telegram FloodWait.\n"
            f"Try again after {wait_time} seconds."
        )


    except Exception as e:

        print(
            "ADD TEXT ERROR:",
            repr(e),
            flush=True
        )


        await message.reply_text(
            "❌ Channel mein text send nahi hua.\n\n"
            f"<code>{str(e)[:500]}</code>"
        )


# ============================================================
# VIDEO HANDLER
# ============================================================

@app.on_message(
    filters.private
    & filters.text
    & ~filters.command(
        [
            "start",
            "broadcast",
            "user",
            "add"
        ]
    )
)
async def video_handler(
    client,
    message
):

    user = message.from_user

    if not user:
        return


    add_user(user.id)


    # --------------------------------------------------------
    # Mandatory channel join
    # --------------------------------------------------------

    joined, error = await is_user_joined(
        user.id
    )


    if error:

        print(
            f"⚠️ VIDEO JOIN ERROR "
            f"User={user.id}: {error}",
            flush=True
        )


        await message.reply_text(
            "⚠️ <b>Channel verification failed.</b>\n\n"
            "Please join the channel and try again.",
            reply_markup=join_keyboard()
        )

        return


    if not joined:

        await message.reply_text(
            "🔐 <b>Access Required</b>\n\n"
            "Pehle hamare channel ko join karo, "
            "phir <b>Joined</b> button press karo.",
            reply_markup=join_keyboard()
        )

        return


    # --------------------------------------------------------
    # URL validation
    # --------------------------------------------------------

    url = message.text.strip()


    if not url.startswith(
        ("http://", "https://")
    ):

        await message.reply_text(
            "🔗 Please ek valid video link bhejo."
        )

        return


    status = await message.reply_text(
        "⏳ <b>Processing...</b>\n\n"
        "🔎 Video information check kar raha hoon..."
    )


    output_file = (
        DATA_DIR
        / f"video_{user.id}_{int(time.time() * 1000)}.mp4"
    )


    try:

        # ----------------------------------------------------
        # Download
        # ----------------------------------------------------

        await status.edit_text(
            "⬇️ <b>Downloading video...</b>"
        )


        await download_video(
            url,
            output_file
        )


        if (
            not output_file.exists()
            or
            output_file.stat().st_size <= 0
        ):

            raise RuntimeError(
                "Downloaded video empty hai."
            )


        size_mb = (
            output_file.stat().st_size
            / (1024 * 1024)
        )


        print(
            f"📦 Downloaded video size: "
            f"{size_mb:.2f} MB",
            flush=True
        )


        # ----------------------------------------------------
        # Upload
        # ----------------------------------------------------

        await status.edit_text(
            "⬆️ <b>Uploading video to Telegram...</b>\n\n"
            "Please wait..."
        )


        last_error = None
        uploaded = False


        for attempt in range(1, 5):

            try:

                print(
                    f"📤 Upload attempt "
                    f"{attempt}/4",
                    flush=True
                )


                await client.send_video(
                    chat_id=message.chat.id,
                    video=str(output_file),
                    caption=(
                        "🎬 <b>Video Downloaded Successfully!</b>\n\n"
                        "⚡ <b>MISSTU Downloader</b>"
                    ),
                    supports_streaming=True,
                )


                uploaded = True

                break


            except FloodWait as e:

                last_error = e

                wait_time = int(
                    getattr(e, "value", 1)
                )


                print(
                    f"⚠️ Upload FloodWait: "
                    f"{wait_time}s",
                    flush=True
                )


                await asyncio.sleep(
                    wait_time + 1
                )


            except Exception as e:

                last_error = e


                print(
                    f"❌ Upload attempt "
                    f"{attempt} failed: {e}",
                    flush=True
                )


                if attempt < 4:

                    try:

                        await status.edit_text(
                            f"⚠️ Upload connection issue.\n\n"
                            f"🔄 Retrying "
                            f"{attempt + 1}/4..."
                        )

                    except Exception:
                        pass


                    await asyncio.sleep(5)


        if not uploaded:

            raise RuntimeError(
                f"Telegram upload failed: "
                f"{last_error}"
            )


        await safe_delete(
            status
        )


    except Exception as e:

        print(
            "❌ VIDEO ERROR:",
            repr(e),
            flush=True
        )


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

            print(
                "Temporary file delete error:",
                repr(e),
                flush=True
            )


# ============================================================
# STARTUP
# ============================================================

def start_telegram_bot():

    print("=" * 60)
    print("🚀 MISSTU VIDEO DOWNLOADER BOT")
    print("=" * 60)

    print(
        "API_ID:",
        API_ID,
        flush=True
    )

    print(
        "ADMIN_ID:",
        ADMIN_ID,
        flush=True
    )

    print(
        "CHANNEL_ID:",
        CHANNEL_ID,
        flush=True
    )

    print(
        "CHANNEL_URL:",
        CHANNEL_URL,
        flush=True
    )

    print(
        "DOWNLOADER_API:",
        DOWNLOADER_API,
        flush=True
    )

    print(
        "Users loaded:",
        len(users),
        flush=True
    )

    print("=" * 60)


    while True:

        try:

            print(
                "⏳ Connecting to Telegram...",
                flush=True
            )

            app.run()

            print(
                "🛑 Telegram bot stopped.",
                flush=True
            )

            break


        except FloodWait as e:

            wait_time = int(
                getattr(e, "value", 1)
            )

            print(
                f"⚠️ Telegram login FloodWait: "
                f"{wait_time}s",
                flush=True
            )

            print(
                f"⏳ Waiting {wait_time + 2}s "
                f"before reconnect...",
                flush=True
            )

            time.sleep(
                wait_time + 2
            )


        except Exception as e:

            print(
                "❌ FATAL BOT ERROR:",
                repr(e),
                flush=True
            )

            print(
                "⏳ Restarting bot in 10 seconds...",
                flush=True
            )

            time.sleep(10)


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":

    print("=" * 60)
    print("MISSTU VIDEO DOWNLOADER BOT")
    print("=" * 60)

    print(
        "Admin ID:",
        ADMIN_ID
    )

    print(
        "Users loaded:",
        len(users)
    )


    # Flask health server
    threading.Thread(
        target=run_health_server,
        daemon=True
    ).start()


    print(
        "✅ Flask health server started.",
        flush=True
    )


    # Telegram bot
    start_telegram_bot()
