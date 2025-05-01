from telethon.sync import TelegramClient
from telethon.tl.functions.messages import GetDialogsRequest
from telethon.tl.types import InputPeerEmpty, InputPeerChannel, InputPeerUser
from telethon.tl.functions.channels import InviteToChannelRequest
import csv
import time
import re

# -------------------------------
# STEP 1: SET UP TELEGRAM CLIENT
# -------------------------------
api_id = 29645357  # your api_id
api_hash = 'your_api_hash'  # your API hash
session_name = 'my_session'  # your session name
phone = '+2349159437403'  # your phone number

client = TelegramClient(session_name, api_id, api_hash)
client.start(phone=phone)

# -------------------------------
# STEP 2: SELECT TARGET GROUP/CHANNEL
# -------------------------------
chats = []
result = client(GetDialogsRequest(
    offset_date=None,
    offset_id=0,
    offset_peer=InputPeerEmpty(),
    limit=100,
    hash=0
))
chats.extend(result.chats)

# Filter for channels/supergroups (broadcast channels or megagroups)
groups = [chat for chat in chats if (hasattr(chat, 'megagroup') and chat.megagroup) or getattr(chat, 'broadcast', False)]

print("\nAvailable groups/channels for adding users:\n")
for i, group in enumerate(groups):
    print(f"{i} - {group.title} (Type: {type(group)})")

target_index = int(input("\nEnter the number of the group/channel to add members to: "))
target_group = groups[target_index]

# Create a valid InputPeerChannel object for the target group
target_channel = InputPeerChannel(target_group.id, target_group.access_hash)

# -------------------------------
# STEP 3: READ USERS FROM CSV FILE
# -------------------------------
csv_filename = "members.csv"
users_to_add = []

with open(csv_filename, "r", encoding="utf-8") as file:
    reader = csv.DictReader(file)
    headers = reader.fieldnames
    print("CSV Headers found:", headers)  # Debug: print headers

    for row in reader:
        try:
            # Adjust header names to match your CSV:
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
            else:
                print("Skipped row because no user_id found:", row)
        except Exception as e:
            print(f"Error parsing row: {row} - {e}")

print(f"\nTotal users loaded from CSV: {len(users_to_add)}")

# -------------------------------
# STEP 4: INVITE USERS TO TARGET GROUP
# -------------------------------
print("\nStarting to add users to the target group...\n")

# Function to determine if a username indicates a bot:
def is_bot(username):
    return bool(username) and re.search(r'bot$', username, re.IGNORECASE)

for user in users_to_add:
    # Skip bot accounts since they cannot be added normally.
    if is_bot(user['username']):
        print(f"Skipped bot account: {user['username']}")
        continue

    try:
        # Create InputPeerUser for inviting
        input_user = InputPeerUser(user['user_id'], user['access_hash'])
        client(InviteToChannelRequest(
            channel=target_channel,
            users=[input_user]
        ))
        print(f"Added: {user['username'] or user['user_id']}")
        # Increase delay if necessary to mitigate rate limits
        time.sleep(10)
    except Exception as e:
        error_str = str(e)
        print(f"Could not add {user['username'] or user['user_id']}: {error_str}")
        # Check if error indicates rate limiting and try to extract wait time.
        if "Too many requests" in error_str:
            # If Telegram returns a 'FLOOD_WAIT_x' message, you can extract x.
            match = re.search(r'FLOOD_WAIT_(\d+)', error_str)
            if match:
                wait_time = int(match.group(1))
                print(f"Flood wait detected. Sleeping for {wait_time + 5} seconds.")
                time.sleep(wait_time + 5)
            else:
                # Default backoff delay
                print("Sleeping for 60 seconds due to rate limit.")
                time.sleep(10)
        else:
            # For other errors, wait a bit before continuing
            time.sleep(10)

print("\nFinished adding members.")
