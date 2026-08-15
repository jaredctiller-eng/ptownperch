# Ptown Perch — Slack `/ask` Assistant Design

**Date:** 2026-08-15
**Status:** Approved by Jared (design conversation, 2026-08-15)
**Scope:** A Slack slash command (`/ask`) that lets the operator chat with Claude directly in Slack for general help — drafting guest messages, thinking through a situation, general questions — with real threaded, multi-turn memory. Independent of the Phase 1 (deployed) and Phase 2 (planned) booking-flow work; shares no code with either except an optional reuse of Phase 2's Slack signature-verification helper once it exists.

## Summary

`/ask <question>` calls the Claude API and posts the reply as a new Slack message with its own thread. From then on, replying naturally in that thread — no slash command needed — continues the same conversation: the backend hears the reply via Slack's Events API, looks up the conversation history, sends it to Claude, and posts the answer back into the thread via a Slack bot token. Claude has no access to the reservations database in this version — it's a general assistant, not an ops-data query tool.

## Decisions made (with rationale)

| Decision | Choice | Rationale |
|---|---|---|
| Product choice | Custom feature via the raw Anthropic API, not Anthropic's official "Claude Tag" | Claude Tag requires a Team/Enterprise Claude plan — a real recurring cost jump not justified for occasional use on a single-property side project. Pay-per-use API billing costs pennies per conversation at this volume. |
| Data access | None — general assistant only | Keeps this a small, low-risk feature: one API call in, one reply out, no new read access to reservation data for Claude to leak or misuse. Can be revisited later as its own scoped addition if wanted. |
| Continuing a conversation | Reply naturally in the Slack thread (no repeated `/ask`) | Chosen over "type `/ask` again to continue" despite costing a real bot-token + Events API subscription — the natural-reply UX was worth the larger build to the operator. |
| Model | Claude Sonnet 5 (default) | Good balance of capability and pay-per-use cost for drafting/reasoning help; not a hard requirement — cheap to change later. |
| Context window sent to Claude | Last ~20 messages of a thread | Bounds per-turn API cost regardless of how long a thread grows. Full history stays stored for reference even though only the recent window is sent. |

## Accepted risks and operator setup required

- **New Anthropic API key needed**, separate from any Claude.ai subscription — pay-as-you-go billing via console.anthropic.com. A new `ANTHROPIC_API_KEY` env var.
- **Slack app reconfiguration required** on the existing "Perch Ops" app: add a Bot Token (OAuth install, `chat:write` + `commands` scopes), register the `/ask` slash command with a Request URL, and add an Events API subscription (`message.channels`/`message.groups`) with its own Request URL. The bot must be invited into whichever channel(s) `/ask` is used in.
- **No access control beyond Slack workspace membership.** Anyone in the (private, two-person) workspace can use `/ask`. Acceptable at this scale; would need revisiting if the workspace ever grows.
- **No data retention policy.** Conversation history accumulates in Postgres indefinitely. Text volume at this usage level is trivially small — not worth building pruning for now.

## Architecture

### Slack configuration

Three additions to the existing Perch Ops Slack app (on top of Phase 1's Incoming Webhook and Phase 2's planned Interactivity):
1. **Bot Token** (OAuth, `chat:write` + `commands` scopes) — lets the backend post to any channel/thread on demand, not just the fixed webhook channel.
2. **Slash command** `/ask`, Request URL pointing at this backend.
3. **Events API subscription** to `message.channels`/`message.groups`, a second Request URL, so the backend is told about every new message in channels the bot is in.

### Conversation lifecycle

1. `/ask <question>` — the slash command handler calls the Claude API synchronously (a single request, no bot-token needed for this first reply) with a short fixed system prompt ("helper for Ptown Perch STR ops, drafting/reasoning support, no booking-data access") and returns Claude's answer directly as the command's HTTP response. Slack renders this as a new message, which becomes the thread's parent. The backend records a new conversation row keyed by that message's `thread_ts` + `channel_id`, plus both the user's question and Claude's answer as stored messages.
2. **A reply lands in that thread** — the Events API delivers a `message` event for every message in the channel, not just thread replies to us. The handler first does two cheap, ordered checks before anything else: skip if the event carries a `bot_id` (a bot-posted message — prevents the assistant ever replying to its own reply and looping), then skip unless the message's `thread_ts` matches a conversation already on file (ignores all unrelated channel chatter). Only messages passing both checks proceed.
3. For a matching reply, the backend loads that conversation's most recent ~20 stored messages, appends the new one, sends the whole window to Claude, stores both the new user message and Claude's reply, and posts the reply into the same thread via `chat.postMessage` (bot token) with the matching `channel_id` + `thread_ts`.
4. **Errors** (Claude API failure, rate limit, network) post a plain "Sorry, I hit an error — try again" message into the thread rather than failing silently.

### Slack retry handling

Slack retries an Events API delivery if it doesn't receive a 200 response quickly enough (roughly 3 seconds), with a `X-Slack-Retry-Num` header on the retried request. The handler checks for this header and, if present, returns 200 immediately without any further processing — this is Slack's own documented guidance and prevents a slow Claude API call from producing a duplicate reply.

### Data model

Two new tables, independent of the booking-flow `Reservation`/`Property` tables:
- `ai_conversations`: `id`, `thread_ts` (Slack's thread identifier), `channel_id`, `created_at`.
- `ai_messages`: `id`, `conversation_id` (FK), `role` (`user` | `assistant`), `content`, `created_at`.

### File layout (repo conventions already established)

- `app/assistant/claude_client.py` — thin wrapper around the `anthropic` Python SDK.
- `app/routes/slack_commands.py` — the `/ask` slash command handler.
- `app/routes/slack_events.py` — the Events API handler (bot-id filter, thread matching, retry short-circuit).
- `app/slack/api.py` — bot-token-authenticated Slack Web API calls (`chat.postMessage`), distinct from the existing `app/slack/notify.py` (which stays scoped to the fixed incoming-webhook notification pattern used by Phase 1/2).
- New `AiConversation`/`AiMessage` classes added to the existing `app/models.py` (this repo keeps all SQLAlchemy models in one file — not splitting that convention for two small tables).
- New Alembic migration (Phase 2's plan already introduces Alembic; this feature's migration follows the same mechanism, whichever of the two features lands first in the repo).

## Non-goals (this version)

- **Direct messages.** `/ask` and thread replies work in channels the bot is invited to (`message.channels`/`message.groups` scopes) only — not Slack DMs to the bot, which would need a separate `message.im` scope and its own testing. Use a channel.
- Any access to reservation/booking data — Claude cannot look up availability, pricing, or guest details. A future version could add this as defined, safe read-only tools, but that's out of scope here.
- Access control beyond Slack workspace membership.
- Conversation pruning/retention limits.
- Multi-workspace or multi-bot support — one Slack app, one workspace, as already established.
