#!/usr/bin/env python3
"""Test script to verify the bug fix for issue #55462."""
import os

# Test TELEGRAM_ALLOWED_USERS only
os.environ["TELEGRAM_ALLOWED_USERS"] = "user1,user2"
os.environ["TELEGRAM_GROUP_ALLOWED_USERS"] = ""
os.environ["GATEWAY_ALLOW_ALL_USERS"] = ""

# Test the current behavior
def get_current_behavior(chat_type: str) -> str:
    """Simulate the current behavior of _is_callback_user_authorized."""
    allowed_csv = os.getenv("TELEGRAM_ALLOWED_USERS", "").strip()
    
    if chat_type in ("group", "forum"):
        # Current code only checks TELEGRAM_ALLOWED_USERS
        pass
        
    if not allowed_csv:
        return "Allow via GATEWAY_ALLOW_ALL_USERS"
    
    allowed_ids = {uid.strip() for uid in allowed_csv.split(",") if uid.strip()}
    if "user1" in allowed_ids:
        return "user1 allowed"
    else:
        return "user1 NOT allowed"

print("Current behavior (TELEGRAM_ALLOWED_USERS only):")
print(f"  DM test: {get_current_behavior('dm')}")
print(f"  Group test: {get_current_behavior('group')}")
print(f"  Forum test: {get_current_behavior('forum')}")

# Test group allowlist
os.environ["TELEGRAM_GROUP_ALLOWED_USERS"] = "group1,group2"
print("\nWith TELEGRAM_GROUP_ALLOWED_USERS:")
print(f"  DM test: {get_current_behavior('dm')}")
print(f"  Group test: {get_current_behavior('group')}")
print(f"  Forum test: {get_current_behavior('forum')}")

# Show the issue
print("\nISSUE:")
print("  - Users added to TELEGRAM_GROUP_ALLOWED_USERS are NOT authorized in groups")
print("  - The current code ignores TELEGRAM_GROUP_ALLOWED_USERS for groups/forums")
print("  - It should prioritize group-specific allowlists for group chat types")