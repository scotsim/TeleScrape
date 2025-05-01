import logging
import asyncio
import csv
import re
import time

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

# ---------------------------
# Logging configuration
# ---------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------
# Telethon Credentials (your static app credentials)
# ---------------------------
API_ID = 29645357
API_HASH = '09fe232230c4dac6eea1a79792201c25'
SESSION_NAME = 'my_session'

# ---------------------------
# Conversation States
# ---------------------------
ASK_PHONE, CHOOSE_SCRAPE_GROUP, CHOOSE_ACTION, CHOOSE_TARGET_GROUP = range(4)

# ---------------------------
# Data store for conversation (in-memory)
# ---------------------------
user_data = {}

# ---------------------------
# /start command: greet the user.
# ---------------------------
def start(update: Update, context: CallbackContext) -> None:
    update.message.reply_text(
        "Welcome to the Telegram Scraper & Adder Bot!\n"
        "Use /scrape to start the scraping process."
    )

# ---------------------------
# SCRAPE FLOW
# ---------------------------
def scrape_start(update: Update, context: CallbackContext) -> int:
    user_id = update.message.from_user.id
    if user_id not in user_data or 'phone' not in user_data[user_id]:
        update.message.reply_text("Please enter your phone number (with country code, e.g. +234...)")
        return ASK_PHONE
    else:
        return show_scrape_groups(update, context)

def receive_phone(update: Update, context: CallbackContext) -> int:
    user_id = update.message.from_user.id
    phone = update.message.text.strip()
    user_data[user_id] = {'phone': phone}
    update.message.reply_text("Phone saved! Now fetching your supergroups. Please wait...")
    return show_scrape_groups(update, context)

def show_scrape_groups(update: Update, context: CallbackContext) -> int:
    user_id = update.message.from_user.id
    phone = user_data[user_id]['phone']
    update.message.reply_text("📡 Fetching your supergroups ...")

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    groups = loop.run_until_complete(get_groups(phone))

    if not groups:
        update.message.reply_text("❌ No supergroups found in your account.")
        return ConversationHandler.END

    user_data[user_id]['scrape_groups'] = groups

    msg = "Here are your supergroups:\n\n"
    for i, group in enumerate(groups):
        msg += f"{i}: {group.title}\n"
    msg += "\nReply with the number of the group you want to scrape members from."
    update.message.reply_text(msg)

    return CHOOSE_SCRAPE_GROUP

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
        loop.run_until_complete(scrape_members(user_data[user_id]['phone'], selected_group))
        update.message.reply_text("✅ Scraping done! Members saved to *members.csv*", parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Error during scraping: {e}")
        update.message.reply_text("❌ An error occurred while scraping the members.")
        return ConversationHandler.END

    # After scraping, show action buttons: Download CSV or Add Members.
    keyboard = [
        [InlineKeyboardButton("Download CSV", callback_data="download")],
        [InlineKeyboardButton("Add Members", callback_data="add")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    update.message.reply_text("Choose an action:", reply_markup=reply_markup)
    return CHOOSE_ACTION

# ---------------------------
# Callback Query handler for inline buttons
# ---------------------------
def action_button_handler(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    query.answer()
    user_id = query.from_user.id

    if query.data == "download":
        # Send the CSV file for download.
        try:
            query.edit_message_text("Uploading the CSV file for download...")
            context.bot.send_document(chat_id=query.message.chat_id, document=open("members.csv", "rb"))
        except Exception as e:
            logger.error(f"Error sending CSV: {e}")
            query.edit_message_text("❌ Error sending the CSV file.")
        return ConversationHandler.END

    elif query.data == "add":
        # Begin the process of adding members.
        query.edit_message_text("Great! Now, fetching your available groups/channels for adding members...")
        return show_target_groups(query, context)
    else:
        query.edit_message_text("Unknown option selected.")
        return ConversationHandler.END

# ---------------------------
# ADD MEMBERS FLOW
# ---------------------------
def show_target_groups(update_or_query, context: CallbackContext) -> int:
    """
    Fetches groups/channels available for adding users.
    This uses the same phone number provided.
    """
    # Depending on whether this is triggered from a message or a callback query:
    if hasattr(update_or_query, "message"):
        chat_id = update_or_query.message.chat_id
        update_or_query.message.reply_text("📡 Fetching available groups/channels ...")
    else:
        chat_id = update_or_query.message.chat_id
        context.bot.send_message(chat_id, "📡 Fetching available groups/channels ...")

    # Use phone from stored user data.
    user_id = update_or_query.from_user.id
    phone = user_data[user_id]['phone']

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    # Fetch groups that are either megagroups or broadcast channels.
    groups = loop.run_until_complete(get_target_groups(phone))
    if not groups:
        context.bot.send_message(chat_id, "❌ No groups/channels found for adding members.")
        return ConversationHandler.END

    user_data[user_id]['target_groups'] = groups

    msg = "Here are your available groups/channels for adding members:\n\n"
    for i, group in enumerate(groups):
        msg += f"{i}: {group.title}\n"
    msg += "\nReply with the number of the group/channel to which you want to add members."
    context.bot.send_message(chat_id, msg)

    return CHOOSE_TARGET_GROUP

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
        loop.run_until_complete(add_members(user_data[user_id]['phone'], target_group))
        update.message.reply_text("✅ Finished adding members!")
    except Exception as e:
        logger.error(f"Error during adding members: {e}")
        update.message.reply_text("❌ An error occurred while adding members.")
    return ConversationHandler.END

# ---------------------------
# Asynchronous functions using Telethon
# ---------------------------
async def get_groups(phone: str):
    """Fetch *all* supergroups and channels you’re in."""
    client = TelegramClient(SESSION_NAME, API_ID, API_HASH)
    await client.start(phone=phone)

    result = []
    async for dialog in client.iter_dialogs():
        # dialog.entity is a Chat/Channel object
        if dialog.is_group or dialog.is_channel:
            result.append(dialog.entity)

    await client.disconnect()
    return result

async def scrape_members(phone: str, group) -> None:
    """Scrape members from the chosen group and write to members.csv."""
    client = TelegramClient(SESSION_NAME, API_ID, API_HASH)
    await client.start(phone=phone)
    channel = InputPeerChannel(channel_id=group.id, access_hash=group.access_hash)
    all_participants = []
    offset = 0
    limit = 100
    while True:
        participants = (await client(GetParticipantsRequest(
            channel=channel,
            filter=ChannelParticipantsSearch(''),
            offset=offset,
            limit=limit,
            hash=0
        ))).users

        if not participants:
            break
        all_participants.extend(participants)
        offset += len(participants)
        if len(participants) < limit:
            break

    with open("members.csv", "w", encoding='utf-8', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(["Username", "User ID", "Access Hash", "First Name", "Last Name"])
        for user in all_participants:
            writer.writerow([
                user.username or '',
                user.id,
                user.access_hash if hasattr(user, 'access_hash') else '',
                user.first_name or '',
                user.last_name or ''
            ])
    await client.disconnect()

async def get_target_groups(phone: str):
    """Fetch groups/channels available for adding members.
       Filters for megagroups and broadcast channels.
    """
    client = TelegramClient(SESSION_NAME, API_ID, API_HASH)
    await client.start(phone=phone)
    result = await client(GetDialogsRequest(
        offset_date=None,
        offset_id=0,
        offset_peer=InputPeerEmpty(),
        limit=100,
        hash=0
    ))
    # Filter groups: either megagroup (typical group) or broadcast (channel)
    groups = [chat for chat in result.chats if (hasattr(chat, 'megagroup') and chat.megagroup) or getattr(chat, 'broadcast', False)]
    await client.disconnect()
    return groups

async def add_members(phone: str, target_group) -> None:
    """Read members from members.csv and invite them to the target group."""
    client = TelegramClient(SESSION_NAME, API_ID, API_HASH)
    await client.start(phone=phone)
    target_channel = InputPeerChannel(target_group.id, target_group.access_hash)

    # Read CSV file and build list of users to add.
    users_to_add = []
    with open("members.csv", "r", encoding="utf-8") as file:
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

    # Helper: function to check if username ends with 'bot'.
    def is_bot(username):
        return bool(username) and re.search(r'bot$', username, re.IGNORECASE)

    # Invite each user with a small delay to avoid rate limits.
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
            await asyncio.sleep(10)  # adjust delay as needed
        except Exception as e:
            error_str = str(e)
            logger.error(f"Could not add {user['username'] or user['user_id']}: {error_str}")
            if "Too many requests" in error_str:
                match = re.search(r'FLOOD_WAIT_(\d+)', error_str)
                if match:
                    wait_time = int(match.group(1))
                    logger.info(f"Flood wait detected. Sleeping for {wait_time + 5} seconds.")
                    await asyncio.sleep(wait_time + 5)
                else:
                    await asyncio.sleep(10)
            else:
                await asyncio.sleep(10)
    await client.disconnect()

# ---------------------------
# Main: set up the Bot with ConversationHandler
# ---------------------------
def main():
    updater = Updater("7753049899:AAFerHh3iY0Y_NxlnnT_NDzasH91XQFotbc", use_context=True)
    dp = updater.dispatcher

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('scrape', scrape_start)],
        states={
            ASK_PHONE: [MessageHandler(Filters.text & ~Filters.command, receive_phone)],
            CHOOSE_SCRAPE_GROUP: [MessageHandler(Filters.text & ~Filters.command, receive_scrape_group)],
            CHOOSE_ACTION: [CallbackQueryHandler(action_button_handler)],
            CHOOSE_TARGET_GROUP: [MessageHandler(Filters.text & ~Filters.command, receive_target_group)],
        },
        fallbacks=[CommandHandler('start', start)],
    )

    dp.add_handler(CommandHandler('start', start))
    dp.add_handler(conv_handler)

    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    main()
