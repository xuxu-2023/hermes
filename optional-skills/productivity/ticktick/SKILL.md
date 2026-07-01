---
name: ticktick
description: Create and manage TickTick tasks, projects, and habits.
version: 1.0.0
author: Adolanium
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [TickTick, Tasks, Projects, Habits, Focus, MCP, Productivity]
    homepage: https://ticktick.com
required_environment_variables:
  - name: TICKTICK_API_TOKEN
    prompt: TickTick API token (Settings > Account > API Token)
    help: "Optional. Only for the headless / gateway Bearer path; skip it if you use the browser OAuth flow."
    required_for: headless Bearer authentication
---

# TickTick

Drive the official TickTick MCP server to capture, organize, and complete tasks, and to manage projects (lists), habits, focus records, tags, kanban columns, and comments. This skill assumes the `ticktick` MCP from the Nous catalog is installed and enabled; it does not call the TickTick API by hand.

## When to Use

Load this when the request is to read or change TickTick data: adding or completing tasks, planning a day from undone items, checking in a habit, logging a focus session, or reorganizing lists, tags, and boards. If the `ticktick` MCP is not installed yet, do Prerequisites first.

## Prerequisites

The capability comes from the official TickTick MCP server (`https://mcp.ticktick.com`, Streamable HTTP). Install the catalog entry, then authenticate one of two ways.

1. Install:

   ```
   hermes mcp install ticktick
   ```

2. Authenticate. Pick the path that fits the session:

   - Interactive (desktop or CLI): the default. On first connect Hermes opens a browser for TickTick OAuth (PKCE, dynamic client registration). Nothing else to set.
   - Headless or gateway (Telegram, Discord, a server box): the browser flow needs a screen, so use a Bearer token. In the TickTick web app, open the avatar menu, then Settings > Account > API Token, create a token, and wire it up:

     ```
     # ~/.hermes/.env
     TICKTICK_API_TOKEN=tp_your_token_here
     ```

     ```yaml
     # ~/.hermes/config.yaml, under mcp_servers:
     mcp_servers:
       ticktick:
         url: https://mcp.ticktick.com
         headers:
           Authorization: "Bearer ${TICKTICK_API_TOKEN}"
     ```

     Use the header form OR `auth: oauth`, not both. Paste the raw token; Hermes strips a leading `Bearer ` if you include one.

3. Start a new Hermes session so the tools load. Re-run the tool checklist any time with `hermes mcp configure ticktick`.

Each user brings their own account and token. Nothing here is tied to a specific account; discover lists, tasks, and habits at runtime with the read tools below.

## How It Works

Once enabled, the server exposes its tools directly (47 tools as of server version 1.27.1). Call them by name. The authoritative argument schema for each tool is the definition the server advertises, so read the tool's own schema for exact field names. Most read-by-id and write operations key off two IDs: a `projectId` (a list) and a `taskId`. Resolve IDs first with a list, search, or filter tool, then act.

## Quick Reference

| Domain | Tools |
|--------|-------|
| Projects (lists) | `list_projects`, `get_project_by_id`, `get_project_with_undone_tasks`, `create_project`, `update_project` |
| Project groups | `list_project_groups`, `create_project_group`, `update_project_group`, `delete_project_group` |
| Tasks (read) | `get_task_by_id`, `get_task_in_project`, `search`, `search_task`, `fetch`, `filter_tasks`, `list_undone_tasks_by_date`, `list_undone_tasks_by_time_query`, `list_completed_tasks_by_date` |
| Tasks (write) | `create_task`, `update_task`, `complete_task`, `delete_task`, `move_task`, `complete_tasks_in_project`, `batch_add_tasks`, `batch_update_tasks` |
| Kanban columns | `list_columns`, `create_column`, `update_column` |
| Comments | `get_comment`, `add_comment`, `delete_comment` |
| Habits | `list_habits`, `list_habit_sections`, `get_habit`, `create_habit`, `update_habit`, `get_habit_checkins`, `upsert_habit_checkins` |
| Focus (pomodoro) | `create_focus`, `get_focus`, `get_focuses_by_time`, `delete_focus` |
| Tags | `list_tags`, `create_tag` |
| Other | `list_countdowns`, `get_user_preference` |

## Procedure

1. Capture a task: `list_projects` to choose the list (or let it fall to the default Inbox), then `create_task` with the title plus any content, due date, or priority. For several at once, `batch_add_tasks`.
2. Plan today: `list_undone_tasks_by_time_query` with `today` (or `list_undone_tasks_by_date` over a window of 14 days or fewer), review, then `update_task` to reschedule or `complete_task` to close items.
3. Complete work: get the task's `projectId` and `taskId` from a list or search result, then `complete_task`. To clear several in one list, `complete_tasks_in_project` (20 tasks per call at most).
4. Search and read: `search` or `search_task` by keyword to get IDs, then `fetch` or `get_task_by_id` for the full task.
5. Reorganize: `move_task` to shift tasks between lists; `create_project` and `create_project_group` to restructure; `create_tag` plus `update_task` to tag.
6. Habits: `list_habits`, then `upsert_habit_checkins` to record a check-in; `get_habit_checkins` to review a date-stamp range.
7. Focus log: `create_focus` with `startTime`, `endTime`, and a `type` (read the tool schema for the exact enum; 0 is pomodoro); `get_focuses_by_time` to review a bounded range.

## Pitfalls

- Most task operations need BOTH `projectId` and `taskId`. Get them from `list_projects`, `search`, or `filter_tasks` first; never guess an ID.
- "Project" in the API is the user-facing "list". A task created with no list lands in the default Inbox.
- `list_undone_tasks_by_date` spans at most 14 days, and `get_focuses_by_time` is also range-bounded. Narrow or page the window.
- `complete_tasks_in_project` caps at 20 tasks per call. `batch_add_tasks` and `batch_update_tasks` take arrays, so split large jobs into chunks.
- `add_comment` content is plain text, capped at 1024 characters.
- Writes hit the live account at once: `complete_task`, `delete_task`, `move_task`, and the batch and delete calls are real mutations. Confirm destructive actions with the user before sending them.
- Keep the token in `~/.hermes/.env` and reference it from `config.yaml` as `${TICKTICK_API_TOKEN}`; do not paste the raw token into `config.yaml`.
- The server also advertises MCP prompts and resources, not just tools, for clients that want them.

## Verification

Ask Hermes to list your TickTick projects. That runs `list_projects` and returns your lists (each user sees their own account). A JSON array of projects confirms the MCP is installed, authenticated, and reachable. A `401` means re-check auth (redo the OAuth flow, or fix `TICKTICK_API_TOKEN`); missing tools mean you need a fresh Hermes session after install.
