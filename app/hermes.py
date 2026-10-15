"""Talk to Hermes (start a turn in a runner's profile) and Telegram (deliver the reply).

VERIFY against the Hermes API-server docs before going live: the request body
and the response field holding the final text. Both are isolated here.
"""
import logging

import httpx

from .config import settings

log = logging.getLogger(__name__)


def start_run(profile: str, api_key: str, prompt: str, timeout: float = 300) -> str:
    """POST /p/<profile>/v1/runs with that profile's own API_SERVER_KEY; return the reply text."""
    r = httpx.post(
        f"{settings.hermes_base_url}/p/{profile}/v1/runs",
        headers={"Authorization": f"Bearer {api_key}"},
        json={"input": prompt},
        timeout=timeout,
    )
    r.raise_for_status()
    return extract_text(r.json())


def extract_text(data) -> str:
    """Pull the final assistant text out of a run result, tolerating a few shapes."""
    if isinstance(data, str):
        return data
    if not isinstance(data, dict):
        return ""
    for key in ("output_text", "final_response", "response", "text", "content"):
        v = data.get(key)
        if isinstance(v, str) and v.strip():
            return v
    out = data.get("output")
    if isinstance(out, str):
        return out
    if isinstance(out, list):  # Responses-style list of items
        parts = []
        for item in out:
            for c in (item.get("content") or []) if isinstance(item, dict) else []:
                if isinstance(c, dict) and isinstance(c.get("text"), str):
                    parts.append(c["text"])
        if parts:
            return "\n".join(parts)
    return ""


def send_telegram(chat_id: str, text: str) -> None:
    if not settings.telegram_bot_token or not chat_id or not text:
        log.warning("telegram send skipped (missing token, chat or text)")
        return
    for i in range(0, len(text), 4000):  # Telegram limit ~4096 chars
        httpx.post(
            f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
            json={"chat_id": chat_id, "text": text[i:i + 4000]},
            timeout=20,
        ).raise_for_status()


def run_and_deliver(runner, prompt: str) -> str:
    """Start a turn in the runner's profile and send the reply to their chat."""
    text = start_run(runner["hermes_profile"], runner["hermes_api_key"], prompt)
    send_telegram(runner["telegram_chat_id"], text)
    return text


def notify_coach(text: str) -> None:
    send_telegram(settings.coach_chat_id, text)
