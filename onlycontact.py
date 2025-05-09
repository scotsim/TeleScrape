import logging
import asyncio
import csv
import re
import time
import json
import os

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Updater,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    Filters,
    ConversationHandler,
    CallbackContext,
)
from telethon import TelegramClient
from telethon.tl.functions.messages import GetDialogsRequest
from telethon.tl.functions.channels import GetParticipantsRequest, InviteToChannelRequest
from telethon.tl.types import InputPeerEmpty, InputPeerChannel, InputPeerUser, ChannelParticipantsSearch
from telethon.errors import FloodWaitError
from telethon.tl.types import (
    ChannelParticipantsSearch,
    UserStatusOnline,
    UserStatusRecently,
    UserStatusLastWeek
)
# Logging configuration
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Session Configurations
SESSIONS = [
    {"name": "session1", "api_id": 29645357, "api_hash": "09fe232230c4dac6eea1a79792201c25", "phone": "+2349159437403", "session_file": "session1"},
    {"name": "session2", "api_id": 24371031, "api_hash": "43e51f989bc42e650e9aea784da76139", "phone": "+2347030038467", "session_file": "session2"},
    {"name": "session3", "api_id":18276533, "api_hash": "289b606d2713c93b8cf90d4727b1f337", "phone": "+2349051872824", "session_file": "session3"},
    {"name": "session4", "api_id":29534326, "api_hash": "80c69f4dbabcdd412b05821eed853e76", "phone": "+2348022488505", "session_file": "session4"},
    {"name": "session5", "api_id":23270598, "api_hash": "975cb26bc980f5e02dc3caf44af5bc36", "phone": "+2349065197306", "session_file": "session5"},

   
    # Add more sessions here with your own API ID, API hash, phone number, and session file
]

# Offset Management
OFFSET_FILE = 'offsets.json'

def get_offset(group_id):
    """Retrieve the current offset for a group from offsets.json."""
    try:
        with open(OFFSET_FILE, 'r') as f:
            offsets = json.load(f)
        return offsets.get(str(group_id), 0)
    except FileNotFoundError:
        return 0

def set_offset(group_id, offset):
    """Update the offset for a group in offsets.json."""
    try:
        with open(OFFSET_FILE, 'r') as f:
            offsets = json.load(f)
    except FileNotFoundError:
        offsets = {}
    offsets[str(group_id)] = offset
    with open(OFFSET_FILE, 'w') as f:
        json.dump(offsets, f)

# Conversation States
CHOOSE_SESSION, CHOOSE_SCRAPE_GROUP, CHOOSE_ACTION, CHOOSE_TARGET_GROUP = range(4)

# Data store for conversation (in-memory)
user_data = {}

# /start command: show session buttons
def start(update: Update, context: CallbackContext) -> int:
    keyboard = [[InlineKeyboardButton(session['name'], callback_data=session['name'])] for session in SESSIONS]
    reply_markup = InlineKeyboardMarkup(keyboard)
    update.message.reply_text("Please choose a session:", reply_markup=reply_markup)
    return CHOOSE_SESSION

# Handle session selection
def select_session(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    query.answer()
    session_name = query.data
    selected_session = next((s for s in SESSIONS if s['name'] == session_name), None)
    if selected_session:
        user_id = query.from_user.id
        user_data[user_id] = {'selected_session': selected_session}
        query.edit_message_text(f"Selected session: {session_name}")
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        groups = loop.run_until_complete(get_groups(selected_session))
        if not groups:
            context.bot.send_message(chat_id=query.message.chat_id, text="❌ No supergroups found in your account.")
            return ConversationHandler.END
        user_data[user_id]['scrape_groups'] = groups
        msg = "Here are your supergroups:\n\n" + "\n".join(f"{i}: {g.title}" for i, g in enumerate(groups)) + "\n\nReply with the number of the group you want to scrape members from."
        context.bot.send_message(chat_id=query.message.chat_id, text=msg)
        return CHOOSE_SCRAPE_GROUP
    else:
        query.edit_message_text("Invalid session selected.")
        return ConversationHandler.END

# Fetch groups for scraping
async def get_groups(selected_session):
    client = TelegramClient(selected_session['session_file'], selected_session['api_id'], selected_session['api_hash'])
    await client.start(phone=selected_session['phone'])
    result = []
    async for dialog in client.iter_dialogs():
        if dialog.is_group or dialog.is_channel:
            result.append(dialog.entity)
    await client.disconnect()
    return result

# Handle scrape group selection
def receive_scrape_group(update: Update, context: CallbackContext) -> int:
    user_id = update.message.from_user.id
    choice_text = update.message.text.strip()
    if not choice_text.isdigit():
        update.message.reply_text("❌ Please enter a valid number.")
        return CHOOSE_SCRAPE_GROUP
    index = int(choice_text)
    groups = user_data[user_id].get('scrape_groups', [])
    if index < 0 or index >= len(groups):
        update.message.reply_text("❌ Invalid selection. Try again.")
        return CHOOSE_SCRAPE_GROUP
    selected_group = groups[index]
    user_data[user_id]['selected_scrape_group'] = selected_group
    update.message.reply_text(f"⏳ Scraping members from *{selected_group.title}*... Please wait...", parse_mode="Markdown")
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(scrape_members(user_data[user_id]['selected_session'], selected_group))
        update.message.reply_text("✅ Scraping done! Members saved to session-specific CSV.", parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Error during scraping: {e}")
        update.message.reply_text("❌ An error occurred while scraping the members.")
        return ConversationHandler.END
    keyboard = [
        [InlineKeyboardButton("Download CSV", callback_data="download")],
        [InlineKeyboardButton("Add Members", callback_data="add")]
    ]
    update.message.reply_text("Choose an action:", reply_markup=InlineKeyboardMarkup(keyboard))
    return CHOOSE_ACTION
# Scrape only “active” members (online / recently / last week)
async def scrape_members(selected_session, group) -> None:
    client = TelegramClient(
        selected_session['session_file'],
        selected_session['api_id'],
        selected_session['api_hash']
    )
    await client.start(phone=selected_session['phone'])

    # the raw offset (how many users we have already scanned)
    offset = get_offset(group.id)
    raw_limit = 15      # how many raw users to fetch per request
    want_active = 15    # how many active users to collect

    active_statuses = (UserStatusOnline, UserStatusRecently, UserStatusLastWeek)

    active_users = []
    looked_at = 0

    # keep paging until we have enough active users or run out
    while len(active_users) < want_active:
        resp = await client(GetParticipantsRequest(
            channel=group,
            filter=ChannelParticipantsSearch(''),
            offset=offset,
            limit=raw_limit,
            hash=0
        ))
        users = resp.users
        if not users:
            break

        for u in users:
            looked_at += 1
            # only keep if status is one of our “active” types
            if isinstance(u.status, active_statuses):
                active_users.append(u)
                if len(active_users) >= want_active:
                    break

        offset += len(users)

    # persist new offset so next time you don’t re‑scan these
    set_offset(group.id, offset)

    # write only the active users to CSV
    csv_file = f"members_{selected_session['name']}.csv"
    with open(csv_file, "w", encoding='utf-8', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(["Username","User ID","Access Hash","First Name","Last Name","Status"])
        for u in active_users:
            writer.writerow([
                u.username or '',
                u.id,
                getattr(u, 'access_hash', ''),
                u.first_name or '',
                u.last_name or '',
                type(u.status).__name__
            ])

    await client.disconnect()
    if not active_users:
        logger.info("No active members found.")
    else:
        logger.info(f"Saved {len(active_users)} active members to {csv_file}.")

# Callback Query handler for inline buttons
def action_button_handler(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    query.answer()
    user_id = query.from_user.id
    selected_session = user_data[user_id]['selected_session']
    if query.data == "download":
        csv_file = f"members_{selected_session['name']}.csv"
        try:
            query.edit_message_text("Uploading the CSV file for download...")
            context.bot.send_document(chat_id=query.message.chat_id, document=open(csv_file, "rb"))
        except Exception as e:
            logger.error(f"Error sending CSV: {e}")
            query.edit_message_text("❌ Error sending the CSV file.")
        return ConversationHandler.END
    elif query.data == "add":
        query.edit_message_text("Great! Now, fetching your available groups/channels for adding members...")
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        groups = loop.run_until_complete(get_target_groups(selected_session))
        if not groups:
            context.bot.send_message(chat_id=query.message.chat_id, text="❌ No groups/channels found for adding members.")
            return ConversationHandler.END
        user_data[user_id]['target_groups'] = groups
        msg = "Here are your available groups/channels for adding members:\n\n" + "\n".join(f"{i}: {g.title}" for i, g in enumerate(groups)) + "\n\nReply with the number of the group/channel to which you want to add members."
        context.bot.send_message(chat_id=query.message.chat_id, text=msg)
        return CHOOSE_TARGET_GROUP
    query.edit_message_text("Unknown option selected.")
    return ConversationHandler.END

# Fetch target groups for adding members
async def get_target_groups(selected_session):
    client = TelegramClient(selected_session['session_file'], selected_session['api_id'], selected_session['api_hash'])
    await client.start(phone=selected_session['phone'])
    result = await client(GetDialogsRequest(
        offset_date=None,
        offset_id=0,
        offset_peer=InputPeerEmpty(),
        limit=100,
        hash=0
    ))
    groups = [chat for chat in result.chats if (hasattr(chat, 'megagroup') and chat.megagroup) or getattr(chat, 'broadcast', False)]
    await client.disconnect()
    return groups

# Handle target group selection
def receive_target_group(update: Update, context: CallbackContext) -> int:
    user_id = update.message.from_user.id
    choice_text = update.message.text.strip()
    if not choice_text.isdigit():
        update.message.reply_text("❌ Please enter a valid number.")
        return CHOOSE_TARGET_GROUP
    index = int(choice_text)
    groups = user_data[user_id].get('target_groups', [])
    if index < 0 or index >= len(groups):
        update.message.reply_text("❌ Invalid selection. Try again.")
        return CHOOSE_TARGET_GROUP
    target_group = groups[index]
    user_data[user_id]['selected_target_group'] = target_group
    update.message.reply_text(f"⏳ Adding members to *{target_group.title}*... Please wait...", parse_mode="Markdown")
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(add_members(user_data[user_id]['selected_session'], target_group))
        update.message.reply_text("✅ Finished adding members!")
    except Exception as e:
        logger.error(f"Error during adding members: {e}")
        update.message.reply_text("❌ An error occurred while adding members.")
    return ConversationHandler.END

# Add members using selected session
async def add_members(selected_session, target_group) -> None:
    client = TelegramClient(selected_session['session_file'], selected_session['api_id'], selected_session['api_hash'])
    await client.start(phone=selected_session['phone'])
    target_channel = InputPeerChannel(target_group.id, target_group.access_hash)
    csv_file = f"members_{selected_session['name']}.csv"
    users_to_add = []
    with open(csv_file, "r", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        for row in reader:
            try:
                user_id = int(row.get("User ID", 0))
                access_hash_str = row.get("Access Hash", "")
                access_hash = int(access_hash_str) if access_hash_str else 0
                username = row.get("Username", "")
                if user_id:
                    users_to_add.append({
                        "user_id": user_id,
                        "access_hash": access_hash,
                        "username": username
                    })
            except Exception as e:
                logger.error(f"Error parsing row {row}: {e}")
    for user in users_to_add:
        if is_bot(user['username']):
            logger.info(f"Skipped bot account: {user['username']}")
            continue
        try:
            input_user = InputPeerUser(user['user_id'], user['access_hash'])
            await client(InviteToChannelRequest(
                channel=target_channel,
                users=[input_user]
            ))
            logger.info(f"Added: {user['username'] or user['user_id']}")
            await asyncio.sleep(20)
        except FloodWaitError as e:
            logger.info(f"Flood wait detected. Sleeping for {e.seconds + 5} seconds.")
            await asyncio.sleep(e.seconds + 10)
        except Exception as e:
            logger.error(f"Could not add {user['username'] or user['user_id']}: {e}")
            await asyncio.sleep(10)
    await client.disconnect()

# Helper function to check if username ends with 'bot'
def is_bot(username):
    return bool(username) and re.search(r'bot$', username, re.IGNORECASE)

# Main: set up the Bot with ConversationHandler
def main():
    updater = Updater("7753049899:AAFerHh3iY0Y_NxlnnT_NDzasH91XQFotbc", use_context=True)  # Replace with your bot token
    dp = updater.dispatcher
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            CHOOSE_SESSION: [CallbackQueryHandler(select_session)],
            CHOOSE_SCRAPE_GROUP: [MessageHandler(Filters.text & ~Filters.command, receive_scrape_group)],
            CHOOSE_ACTION: [CallbackQueryHandler(action_button_handler)],
            CHOOSE_TARGET_GROUP: [MessageHandler(Filters.text & ~Filters.command, receive_target_group)],
        },
        fallbacks=[CommandHandler('start', start)],
    )
    dp.add_handler(conv_handler)
    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    main()