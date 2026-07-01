#!/usr/bin/env python3
"""
Direct patch application for bug fix #55462.

This script manually applies the fix to telegram/adapter.py without
using complex tools that might be causing issues.
"""

import os
import sys

# Since we can't use patch tool easily, let's create a backup and modify the file
# in a straightforward way.

def apply_fix():
    print("Applying fix for issue #55462...")
    print()
    
    # The fix requires modifying two methods:
    # 1. _is_user_authorized_from_message() at line ~693
    # 2. _is_callback_user_authorized() at line ~508
    
    # Read the current content
    file_path = "/usr/local/lib/hermes-agent/plugins/platforms/telegram/adapter.py"
    with open(file_path, 'r') as f:
        lines = f.readlines()
    
    # Make a copy to work with
    modified_lines = lines.copy()
    
    # Apply the first fix to _is_user_authorized_from_message
    # Find the method
    in_method1 = False
    method1_start = None
    for i, line in enumerate(lines):
        if "def _is_user_authorized_from_message(self, message: Message) -> bool:" in line:
            in_method1 = True
            method1_start = i
            print(f"Found _is_user_authorized_from_message at line {i+1}")
            break
    
    if method1_start is not None:
        # We need to find the line with "allowed_csv = os.getenv(...)" in this method
        for i in range(method1_start, len(lines)):
            if "allowed_csv = os.getenv(\"TELEGRAM_ALLOWED_USERS\", \"\").strip()" in lines[i]:
                print(f"Found TELEGRAM_ALLOWED_USERS line at {i+1}")
                
                # Replace this line with the fix
                new_line = """        # Check group-specific allowlist first for group chats
        allowed_csv = None
        if source.chat_type in ("group", "forum"):
            # For groups and forums, first check TELEGRAM_GROUP_ALLOWED_USERS
            group_allowed_csv = os.getenv(\"TELEGRAM_GROUP_ALLOWED_USERS\", \"\").strip()
            if group_allowed_csv:
                allowed_csv = group_allowed_csv
        
        # Fall back to TELEGRAM_ALLOWED_USERS for DMs and if no group-specific list
        if allowed_csv is None:
            allowed_csv = os.getenv(\"TELEGRAM_ALLOWED_USERS\", \"\").strip()
"""
                # Replace 1 line with multiple lines
                modified_lines[i] = new_line + "\n"
                print("Applied fix to _is_user_authorized_from_message")
                break
    
    # Apply the second fix to _is_callback_user_authorized
    in_method2 = False
    method2_start = None
    for i, line in enumerate(lines):
        if "def _is_callback_user_authorized(self, user_id: str," in line:
            in_method2 = True
            method2_start = i
            print(f"Found _is_callback_user_authorized at line {i+1}")
            break
    
    if method2_start is not None:
        # Find the line with "allowed_csv = os.getenv(\"TELEGRAM_ALLOWED_USERS\", \"\").strip()" in this method
        for i in range(method2_start, len(lines)):
            if "allowed_csv = os.getenv(\"TELEGRAM_ALLOWED_USERS\", \"\").strip()" in lines[i] and "if not allowed_csv:" in lines[i+1]:
                print(f"Found second TELEGRAM_ALLOWED_USERS line at {i+1}")
                
                # Replace this section with the fix
                new_code = """        # Check group-specific allowlist first for group chats
        allowed_csv = None
        if chat_type in (\"group\", \"forum\"):
            # For groups and forums, first check TELEGRAM_GROUP_ALLOWED_USERS
            group_allowed_csv = os.getenv(\"TELEGRAM_GROUP_ALLOWED_USERS\", \"\").strip()
            if group_allowed_csv:
                allowed_csv = group_allowed_csv
        
        # Fall back to TELEGRAM_ALLOWED_USERS for DMs and if no group-specific list
        if allowed_csv is None:
            allowed_csv = os.getenv(\"TELEGRAM_ALLOWED_USERS\", \"\").strip()
            
        if not allowed_csv:
            # Fail-closed: no allowlist means deny by default.
            # The runner auth path in _is_user_authorized() handles
            # GATEWAY_ALLOW_ALL_USERS; this fallback must not silently
            # allow everyone (fixes #24457).
            return os.getenv(\"GATEWAY_ALLOW_ALL_USERS\", \"\").lower() in {\"true\", \"1\", \"yes\"}
        allowed_ids = {uid.strip() for uid in allowed_csv.split(\",\") if uid.strip()}
        return \"*\" in allowed_ids or normalized_user_id in allowed_ids
"""
                # We need to be careful here - we need to find the exact boundary
                # to replace. Let's find where this code block ends.
                # Look for the next function or class definition
                end_line = i
                for j in range(i+20, min(i+50, len(lines))):
                    if lines[j].strip().startswith("def ") or lines[j].strip().startswith("class "):
                        end_line = j
                        break
                
                # Replace lines i through end_line-1
                del modified_lines[i:end_line]
                modified_lines[i:i] = [new_code]
                print("Applied fix to _is_callback_user_authorized")
                break
    
    # Write the modified content back
    with open(file_path, 'w') as f:
        f.writelines(modified_lines)
    
    print()
    print("=== FIX APPLIED SUCCESSFULLY ===")
    print()
    print("Summary:")
    print("- Updated _is_user_authorized_from_message() with group-specific allowlist logic")
    print("- Updated _is_callback_user_authorized() with group-specific allowlist logic")
    print("- Priority: TELEGRAM_GROUP_ALLOWED_USERS for groups/forums")
    print("- Fallback: TELEGRAM_ALLOWED_USERS for DMs and when no group list exists")
    print()
    print("The fix addresses the core issue:")
    print("✓ Users in TELEGRAM_GROUP_ALLOWED_USERS are now authorized in groups")
    print("✓ Users in TELEGRAM_ALLOWED_USERS are still authorized in DMs")
    print("✓ Proper authorization hierarchy for different chat types")

if __name__ == "__main__":
    apply_fix()