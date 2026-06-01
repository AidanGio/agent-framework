"""Custom FastAPI routes for LangGraph server."""

import hashlib
import json
import logging
import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from langchain_core.messages.content import create_text_block
from langgraph_sdk import get_client
from langgraph_sdk.client import LangGraphClient

from .dashboard import router as dashboard_router
from .utils.multimodal import dedupe_urls, extract_image_urls, fetch_image_block
from .utils.sandbox import validate_sandbox_startup_config
from .utils.slack import (
    fetch_slack_thread_messages,
    format_slack_messages_for_prompt,
    get_slack_user_info,
    get_slack_user_names,
    post_slack_trace_reply,
    resolve_slack_links_in_context,
    select_slack_context_messages,
    set_slack_assistant_status,
    store_slack_run_mapping,
    strip_bot_mention,
    verify_slack_signature,
)
from .utils.slack_feedback import (
    FEEDBACK_REACTIONS,
    process_slack_reaction_added,
    process_slack_reaction_removed,
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    validate_sandbox_startup_config()
    yield


app = FastAPI(lifespan=lifespan)

DASHBOARD_ALLOWED_ORIGINS: list[str] = [
    o.strip() for o in os.environ.get("DASHBOARD_ALLOWED_ORIGINS", "").split(",") if o.strip()
]
if DASHBOARD_ALLOWED_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=DASHBOARD_ALLOWED_ORIGINS,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )

app.include_router(dashboard_router)

SLACK_SIGNING_SECRET = os.environ.get("SLACK_SIGNING_SECRET", "")
SLACK_BOT_USER_ID = os.environ.get("SLACK_BOT_USER_ID", "")
SLACK_BOT_USERNAME = os.environ.get("SLACK_BOT_USERNAME", "")

LANGGRAPH_URL = os.environ.get("LANGGRAPH_URL") or os.environ.get(
    "LANGGRAPH_URL_PROD", "http://localhost:2024"
)

_AGENT_VERSION_METADATA: dict[str, str] = (
    {"LANGSMITH_AGENT_VERSION": os.environ["LANGCHAIN_REVISION_ID"]}
    if os.environ.get("LANGCHAIN_REVISION_ID")
    else {}
)


def generate_thread_id_from_slack_thread(channel_id: str, thread_id: str) -> str:
    """Deterministic thread ID derived from a Slack channel + thread ts."""
    raw = f"slack:{channel_id}:{thread_id}".encode()
    digest = hashlib.sha1(raw, usedforsecurity=False).hexdigest()
    return str(uuid.UUID(digest[:32]))


def _is_not_found_error(exc: Exception) -> bool:
    return "404" in str(exc) or "not found" in str(exc).lower()


def _run_id_for_logging(run: Any) -> str:
    run_id = run.get("run_id") if isinstance(run, dict) else getattr(run, "run_id", None)
    return run_id if isinstance(run_id, str) else "<unknown>"


async def is_thread_active(thread_id: str) -> bool:
    """Check whether a thread currently has a running run."""
    langgraph_client = get_client(url=LANGGRAPH_URL)
    try:
        thread = await langgraph_client.threads.get(thread_id)
        status = thread.get("status", "idle")
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "Failed to get thread status for %s: %s — assuming not active",
            thread_id,
            e,
        )
        status = "idle"
    return status == "busy"


async def _thread_exists(thread_id: str) -> bool:
    langgraph_client = get_client(url=LANGGRAPH_URL)
    try:
        await langgraph_client.threads.get(thread_id)
        return True
    except Exception as exc:  # noqa: BLE001
        if _is_not_found_error(exc):
            return False
        logger.warning("Failed to fetch thread %s, assuming it exists", thread_id)
        return True


async def queue_message_for_thread(
    thread_id: str, message_content: str | list[dict[str, Any]] | dict[str, Any]
) -> bool:
    """Queue a message for a thread that is currently active.

    Stored in the langgraph store; ``check_message_queue_before_model``
    middleware picks it up and injects it into state on the next model call.
    """
    langgraph_client = get_client(url=LANGGRAPH_URL)
    try:
        namespace = ("queue", thread_id)
        key = "pending_messages"

        new_message = {"content": message_content}
        existing_messages: list[dict[str, Any]] = []
        try:
            existing_item = await langgraph_client.store.get_item(namespace, key)
            if existing_item and existing_item.get("value"):
                existing_messages = existing_item["value"].get("messages", [])
        except Exception:  # noqa: BLE001
            logger.debug("No existing queued messages for thread %s", thread_id)

        existing_messages.append(new_message)
        await langgraph_client.store.put_item(
            namespace, key, {"messages": existing_messages}
        )
        return True  # noqa: TRY300
    except Exception:
        logger.exception("Failed to queue message for thread %s", thread_id)
        return False


async def process_slack_mention(event_data: dict[str, Any]) -> None:
    """Process a Slack app mention by creating a run or queuing a mid-run message."""
    channel_id = event_data.get("channel_id", "")
    thread_ts = event_data.get("thread_ts", "")
    event_ts = event_data.get("event_ts", "")
    user_id = event_data.get("user_id", "")
    text = event_data.get("text", "")
    bot_user_id = event_data.get("bot_user_id", "")

    if not channel_id or not thread_ts or not event_ts:
        logger.warning(
            "Missing Slack event fields (channel_id=%s, thread_ts=%s, event_ts=%s)",
            channel_id,
            thread_ts,
            event_ts,
        )
        return

    await set_slack_assistant_status(channel_id, thread_ts)
    thread_id = generate_thread_id_from_slack_thread(channel_id, thread_ts)

    user_email = None
    user_name = ""
    if user_id:
        slack_user = await get_slack_user_info(user_id)
        if slack_user:
            profile = slack_user.get("profile", {})
            if isinstance(profile, dict):
                user_email = profile.get("email")
                user_name = (
                    profile.get("display_name")
                    or profile.get("real_name")
                    or slack_user.get("real_name")
                    or slack_user.get("name")
                    or ""
                )

    thread_messages = await fetch_slack_thread_messages(channel_id, thread_ts)
    if not any(str(message.get("ts")) == str(event_ts) for message in thread_messages):
        thread_messages.append({"ts": event_ts, "text": text, "user": user_id})

    context_messages, context_mode = select_slack_context_messages(
        thread_messages, event_ts, bot_user_id, SLACK_BOT_USERNAME
    )
    context_user_ids = [
        value
        for value in (message.get("user") for message in context_messages)
        if isinstance(value, str) and value
    ]
    user_names_by_id = await get_slack_user_names(context_user_ids)
    if user_id and user_name and user_id not in user_names_by_id:
        user_names_by_id[user_id] = user_name
    context_text = format_slack_messages_for_prompt(
        context_messages,
        user_names_by_id,
        bot_user_id=bot_user_id,
        bot_username=SLACK_BOT_USERNAME,
    )
    context_source = (
        "the previous message where I was tagged"
        if context_mode == "last_mention"
        else "the beginning of the thread"
    )
    clean_text = (
        strip_bot_mention(text, bot_user_id, bot_username=SLACK_BOT_USERNAME)
        or "(no text in mention)"
    )
    trigger_user = user_name or (f"<@{user_id}>" if user_id else "Unknown user")

    resolved_links_section, image_urls_from_links = await resolve_slack_links_in_context(
        context_messages, user_names_by_id
    )

    prompt = (
        "You were mentioned in Slack.\n\n"
        f"## Triggered by\n{trigger_user}\n\n"
        f"## Slack Thread\n- Channel: {channel_id}\n- Thread TS: {thread_ts}\n"
        f"- Context starts at: {context_source}\n\n"
        f"## Conversation Context\n{context_text}\n\n"
        f"## Latest Mention Request\n{clean_text}\n\n"
        + (f"{resolved_links_section}\n\n" if resolved_links_section else "")
        + "Use `slack_thread_reply` to communicate in this Slack thread for clarifications, "
        "status updates, and final summaries. Use `slack_read_thread_messages` to read any "
        "Slack messages by providing channel_id and message_ts."
    )
    content_blocks: list[dict[str, Any]] = [create_text_block(prompt)]

    image_urls = dedupe_urls(
        [url for msg in context_messages for url in extract_image_urls(msg.get("text", ""))]
        + [
            f["url_private"]
            for msg in context_messages
            for f in msg.get("files", [])
            if isinstance(f, dict)
            and f.get("mimetype", "").startswith("image/")
            and f.get("url_private")
        ]
        + image_urls_from_links
    )
    if image_urls:
        logger.info("Preparing %d image(s) for Slack mention", len(image_urls))
        async with httpx.AsyncClient() as http_client:
            for image_url in image_urls:
                image_block = await fetch_image_block(image_url, http_client)
                if image_block:
                    content_blocks.append(image_block)

    configurable: dict[str, Any] = {
        "slack_thread": {
            "channel_id": channel_id,
            "thread_ts": thread_ts,
            "triggering_user_id": user_id,
            "triggering_user_name": user_name,
            "triggering_user_email": user_email,
            "triggering_event_ts": event_ts,
        },
        "user_email": user_email,
        "source": "slack",
    }

    langgraph_client: LangGraphClient = get_client(url=LANGGRAPH_URL)
    is_first_mention = not await _thread_exists(thread_id)

    if await is_thread_active(thread_id):
        logger.info(
            "Thread %s is active, queuing Slack message for middleware pickup",
            thread_id,
        )
        queued = await queue_message_for_thread(
            thread_id=thread_id,
            message_content={"text": prompt, "image_urls": image_urls},
        )
        if not queued:
            logger.error("Failed to queue Slack message for thread %s", thread_id)
        return

    logger.info("Creating Slack LangGraph run for thread %s", thread_id)
    run = await langgraph_client.runs.create(
        thread_id,
        "agent",
        input={"messages": [{"role": "user", "content": content_blocks}]},
        config={"configurable": configurable, "metadata": _AGENT_VERSION_METADATA},
        if_not_exists="create",
    )
    logger.info(
        "Slack LangGraph run %s created for thread %s",
        _run_id_for_logging(run),
        thread_id,
    )
    run_id = run.get("run_id")
    if is_first_mention:
        trace_message_ts = await post_slack_trace_reply(channel_id, thread_ts, thread_id)
        await set_slack_assistant_status(channel_id, thread_ts)
        if isinstance(run_id, str) and run_id:
            await store_slack_run_mapping(
                langgraph_client,
                channel_id,
                thread_ts,
                run_id,
                message_ts=trace_message_ts,
                triggering_user_id=user_id,
            )
    elif isinstance(run_id, str) and run_id:
        await store_slack_run_mapping(
            langgraph_client,
            channel_id,
            thread_ts,
            run_id,
            triggering_user_id=user_id,
        )


@app.post("/webhooks/slack")
async def slack_webhook(request: Request, background_tasks: BackgroundTasks) -> dict[str, str]:
    """Handle Slack Event API webhooks for app mentions and reactions."""
    body = await request.body()

    signature = request.headers.get("X-Slack-Signature", "")
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    if not verify_slack_signature(
        body=body,
        timestamp=timestamp,
        signature=signature,
        secret=SLACK_SIGNING_SECRET,
    ):
        logger.warning("Invalid Slack signature")
        raise HTTPException(status_code=401, detail="Invalid signature")

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        logger.exception("Failed to parse Slack webhook JSON")
        return {"status": "error", "message": "Invalid JSON"}

    if payload.get("type") == "url_verification":
        return {"challenge": payload.get("challenge", "")}

    if payload.get("type") != "event_callback":
        return {"status": "ignored", "reason": "Not an event callback"}

    event = payload.get("event", {})

    if event.get("type") == "reaction_added":
        if event.get("reaction") in FEEDBACK_REACTIONS:
            background_tasks.add_task(
                process_slack_reaction_added, event, payload.get("event_id", "")
            )
            return {"status": "accepted", "message": "Reaction feedback queued"}
        return {"status": "ignored", "reason": "Reaction not tracked for feedback"}

    if event.get("type") == "reaction_removed":
        if event.get("reaction") in FEEDBACK_REACTIONS:
            background_tasks.add_task(
                process_slack_reaction_removed, event, payload.get("event_id", "")
            )
            return {"status": "accepted", "message": "Reaction removal queued"}
        return {"status": "ignored", "reason": "Reaction not tracked for feedback"}

    if event.get("type") != "app_mention":
        message_text = event.get("text", "")
        has_username_mention = bool(
            event.get("type") == "message"
            and SLACK_BOT_USERNAME
            and f"@{SLACK_BOT_USERNAME}" in message_text
        )
        has_id_mention = bool(
            event.get("type") == "message"
            and SLACK_BOT_USER_ID
            and f"<@{SLACK_BOT_USER_ID}>" in message_text
        )
        if not (has_username_mention or has_id_mention):
            return {"status": "ignored", "reason": "Not an app_mention event"}

    if event.get("subtype") == "bot_message" or event.get("bot_id"):
        return {"status": "ignored", "reason": "Event from a bot"}

    channel_id = event.get("channel", "")
    event_ts = event.get("ts", "")
    thread_ts = event.get("thread_ts") or event_ts
    user_id = event.get("user", "")
    text = event.get("text", "")
    if not channel_id or not event_ts or not thread_ts:
        return {"status": "ignored", "reason": "Missing channel/thread timestamp"}

    bot_user_id = SLACK_BOT_USER_ID
    if not bot_user_id:
        authorizations = payload.get("authorizations", [])
        if isinstance(authorizations, list) and authorizations:
            auth_user_id = authorizations[0].get("user_id")
            if isinstance(auth_user_id, str):
                bot_user_id = auth_user_id
    if not bot_user_id:
        authed_users = payload.get("authed_users", [])
        if isinstance(authed_users, list) and authed_users:
            first_user = authed_users[0]
            if isinstance(first_user, str):
                bot_user_id = first_user

    if bot_user_id and user_id == bot_user_id:
        return {"status": "ignored", "reason": "Event from this bot user"}

    event_data = {
        "channel_id": channel_id,
        "thread_ts": thread_ts,
        "event_ts": event_ts,
        "user_id": user_id,
        "text": text,
        "bot_user_id": bot_user_id,
    }

    background_tasks.add_task(process_slack_mention, event_data)
    return {"status": "accepted", "message": "Slack mention queued"}


@app.get("/webhooks/slack")
async def slack_webhook_verify() -> dict[str, str]:
    return {"status": "ok", "message": "Slack webhook endpoint is active"}


@app.get("/health")
async def health_check() -> dict[str, str]:
    return {"status": "ok"}
