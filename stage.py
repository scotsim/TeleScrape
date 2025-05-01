import logging
import asyncio
import csv

from telegram import Update
from telegram.ext import (
    Updater,
    CommandHandler,
    MessageHandler,
    Filters,
    ConversationHandler,
    CallbackContext,
)

from telethon import TelegramClient
from telethon.tl.functions.messages import GetDialogsRequest
from telethon.tl.functions.channels import GetParticipantsRequest
from telethon.tl.types import InputPeerEmpty, InputPeerChannel, ChannelParticipantsSearch

# ---------------------------
# Logging setup
# ---------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------
# Telethon Credentials (static for your Telegram app)
# ---------------------------
API_ID = 29645357
API_HASH = '09fe232230c4dac6eea1a79792201c25'
SESSION_NAME = 'my_session'

# ---------------------------
# Conversation States
# ---------------------------
ASK_PHONE, CHOOSE_GROUP = range(2)

# ---------------------------
# Store user data (phone & fetched groups) in a simple dict
# ---------------------------
user_data = {}

# ---------------------------
# /start command handler (simple greeting)
# ---------------------------
def start(update: Update, context: CallbackContext) -> None:
    update.message.reply_text(
        "Welcome to the Scraping Bot!\n"
        "To start scraping, please send /scrape.\n"
        "If you haven't already, you'll need to provide your phone number."
    )

# ---------------------------
# /scrape command entry point for the conversation
# ---------------------------
def scrape_start(update: Update, context: CallbackContext) -> int:
    user_id = update.message.from_user.id
    if user_id not in user_data or 'phone' not in user_data[user_id]:
        update.message.reply_text("Please enter your phone number (with country code, e.g. +234...)")
        return ASK_PHONE
    else:
        # Phone already exists; fetch and show groups
        return show_groups(update, context)

# ---------------------------
# Receive phone number and proceed to show groups
# ---------------------------
def receive_phone(update: Update, context: CallbackContext) -> int:
    user_id = update.message.from_user.id
    phone = update.message.text.strip()
    user_data[user_id] = {'phone': phone}
    update.message.reply_text("Phone saved! Now fetching your groups. Please wait...")
    return show_groups(update, context)

# ---------------------------
# Fetch groups using Telethon (async function) and send list to user
# ---------------------------
def show_groups(update: Update, context: CallbackContext) -> int:
    user_id = update.message.from_user.id
    phone = user_data[user_id]['phone']
    update.message.reply_text("📡 Fetching your supergroups ...")

    # Create a new event loop and run the async function
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    groups = loop.run_until_complete(get_groups(phone))

    if not groups:
        update.message.reply_text("❌ No supergroups found in your account.")
        return ConversationHandler.END

    # Save groups to user_data so we can reference them later
    user_data[user_id]['groups'] = groups

    # Build and send the message with the list of groups
    msg = "Here are your supergroups:\n\n"
    for i, group in enumerate(groups):
        msg += f"{i}: {group.title}\n"
    msg += "\nReply with the number of the group you want to scrape members from."
    update.message.reply_text(msg)

    return CHOOSE_GROUP

# ---------------------------
# Handle user's choice of group (by number)
# ---------------------------
def receive_group_choice(update: Update, context: CallbackContext) -> int:
    user_id = update.message.from_user.id
    choice_text = update.message.text.strip()

    if not choice_text.isdigit():
        update.message.reply_text("❌ Please enter a valid number.")
        return CHOOSE_GROUP

    index = int(choice_text)
    groups = user_data[user_id].get('groups', [])
    if index < 0 or index >= len(groups):
        update.message.reply_text("❌ Invalid group selection. Try again.")
        return CHOOSE_GROUP

    selected_group = groups[index]
    phone = user_data[user_id]['phone']

    update.message.reply_text(f"⏳ Scraping members from *{selected_group.title}*... Please wait...", parse_mode="Markdown")

    # Run the scraping process in a new event loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(scrape_members(phone, selected_group))
        update.message.reply_text("✅ Done! Members saved to *members.csv*", parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Error during scraping: {e}")
        update.message.reply_text("❌ An error occurred while scraping the members.")
    return ConversationHandler.END

# ---------------------------
# Asynchronous function to fetch supergroups using Telethon
# ---------------------------
async def get_groups(phone: str):
    client = TelegramClient(SESSION_NAME, API_ID, API_HASH)
    await client.start(phone=phone)
    
    result = await client(GetDialogsRequest(
        offset_date=None,
        offset_id=0,
        offset_peer=InputPeerEmpty(),
        limit=200,
        hash=0
    ))
    groups = [chat for chat in result.chats if getattr(chat, 'megagroup', False)]
    await client.disconnect()
    return groups

# ---------------------------
# Asynchronous function to scrape members from a chosen group
# ---------------------------
async def scrape_members(phone: str, group) -> None:
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

    # Write participants to CSV
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

# ---------------------------
# Main function to set up the bot and conversation
# ---------------------------
def main():
    updater = Updater("7753049899:AAFerHh3iY0Y_NxlnnT_NDzasH91XQFotbc", use_context=True)
    dp = updater.dispatcher

    # Command /start to greet the user
    dp.add_handler(CommandHandler("start", start))

    # Conversation handler for /scrape process
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('scrape', scrape_start)],
        states={
            ASK_PHONE: [MessageHandler(Filters.text & ~Filters.command, receive_phone)],
            CHOOSE_GROUP: [MessageHandler(Filters.text & ~Filters.command, receive_group_choice)],
        },
        fallbacks=[],
    )

    dp.add_handler(conv_handler)

    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    main()
