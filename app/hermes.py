"""Talk to Hermes (start a turn in a runner's profile) and Telegram (deliver the reply).

The Hermes API server's run API is **asynchronous** (verified against Hermes v0.22.2,
``gateway/platforms/api_server_runs.py``):

  POST /p/<profile>/v1/runs              -> 202 {"run_id": ..., "status": "started", "replayed": false}
  GET  /p/<profile>/v1/runs/<run_id>     -> {"object": "hermes.run", "run_id": ..., "status": ...,
                                             "output": "<final assistant text>", "usage": {...}}

The final reply text only appears in ``output``, and only once ``status`` is ``completed``.
Terminal statuses are ``completed`` / ``failed`` / ``cancelled`` / ``interrupted``; while a
run is live the status is ``started`` / ``running`` (or ``waiting_for_approval``, which needs
a human and is treated here as a failure).

Auth: every request must carry **that profile's own** ``API_SERVER_KEY`` as the bearer token.
On a ``/p/<profile>/`` URL the default profile's key is rejected with 401, so one profile can
never start a turn in another profile.

Everything HTTP lives in this module; callers use ``run_and_deliver`` / ``notify_coach``.
"""
import logging
import os
import time

import httpx

from .config import settings

log = logging.getLogger(__name__)

# Run states that mean "the turn is over".
DONE = "completed"
FAILED = ("failed", "cancelled", "interrupted")
NEEDS_HUMAN = ("waiting_for_approval",)


class HermesError(RuntimeError):
    """Base class for anything that went wrong talking to Hermes."""


class RunFailed(HermesError):
    """The run reached a terminal state that is not ``completed``."""


class RunTimeout(HermesError):
    """The run never reached a terminal state inside the deadline."""


def _url(profile: str, *parts: str) -> str:
    tail = "".join(f"/{p}" for p in parts)
    return f"{settings.hermes_base_url}/p/{profile}/v1/runs{tail}"


def _headers(api_key: str, idempotency_key: str | None = None) -> dict[str, str]:
    h = {"Authorization": f"Bearer {api_key}"}
    if idempotency_key:
        h["Idempotency-Key"] = idempotency_key
    return h


def create_run(profile: str, api_key: str, prompt: str, *, timeout: float = 30,
               idempotency_key: str | None = None) -> str:
    """POST a run and return its ``run_id``. The reply is not here; poll for it."""
    r = httpx.post(_url(profile), headers=_headers(api_key, idempotency_key),
                   json={"input": prompt}, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    run_id = data.get("run_id") if isinstance(data, dict) else None
    if not run_id:
        raise HermesError(f"no run_id in response: {data!r}")
    return str(run_id)


def get_run(profile: str, api_key: str, run_id: str, *, timeout: float = 30) -> dict:
    """GET the pollable run status (the ``hermes.run`` document)."""
    r = httpx.get(_url(profile, run_id), headers=_headers(api_key), timeout=timeout)
    r.raise_for_status()
    data = r.json()
    return data if isinstance(data, dict) else {}


def wait_for_reply(profile: str, api_key: str, run_id: str, *, timeout: float = 300,
                   poll_interval: float = 1.0, get=None) -> str:
    """Poll a run until it finishes and return the assistant's final text.

    Raises RunFailed on failed/cancelled/interrupted/needs-approval, RunTimeout if the
    deadline passes first.
    """
    fetch = get or get_run
    deadline = time.monotonic() + timeout
    while True:
        data = fetch(profile, api_key, run_id)
        status = str(data.get("status") or "").lower()
        if status == DONE:
            return extract_text(data)
        if status in FAILED or status in NEEDS_HUMAN:
            detail = str(data.get("error") or data.get("last_event") or "").strip()
            raise RunFailed(f"run {run_id} ended {status}" + (f": {detail}" if detail else ""))
        if time.monotonic() >= deadline:
            raise RunTimeout(f"run {run_id} still {status or 'unknown'} after {timeout:g}s")
        time.sleep(poll_interval)


def start_run(profile: str, api_key: str, prompt: str, *, timeout: float = 300,
              poll_interval: float = 1.0, idempotency_key: str | None = None,
              create=None, get=None) -> str:
    """Start a turn in ``profile`` and return the reply text once the run completes."""
    run_id = (create or create_run)(profile, api_key, prompt, idempotency_key=idempotency_key)
    return wait_for_reply(profile, api_key, run_id, timeout=timeout,
                          poll_interval=poll_interval, get=get)


def extract_text(data) -> str:
    """Pull the final assistant text out of a run document, tolerating a few shapes.

    The real API puts it in ``output`` as a plain string. The list/dict shapes are kept as
    tolerance for a different Hermes build.
    """
    if isinstance(data, str):
        return data
    if not isinstance(data, dict):
        return ""
    out = data.get("output")
    if isinstance(out, str) and out.strip():
        return out
    if isinstance(out, list):  # Responses-style list of items
        parts = []
        for item in out:
            if not isinstance(item, dict):
                continue
            if isinstance(item.get("text"), str):
                parts.append(item["text"])
            for c in item.get("content") or []:
                if isinstance(c, dict) and isinstance(c.get("text"), str):
                    parts.append(c["text"])
        if parts:
            return "\n".join(parts)
    elif isinstance(out, dict):
        for key in ("text", "content"):
            if isinstance(out.get(key), str) and out[key].strip():
                return out[key]
    for key in ("output_text", "final_response", "response", "text", "content"):
        v = data.get(key)
        if isinstance(v, str) and v.strip():
            return v
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


def send_telegram_file(chat_id: str, path: str, caption: str = "") -> None:
    """Send a file (e.g. a person's own data export) to their chat."""
    if not settings.telegram_bot_token or not chat_id:
        raise HermesError("telegram is not configured for sending files")
    with open(path, "rb") as f:
        httpx.post(f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendDocument",
                   data={"chat_id": chat_id, "caption": caption[:1000]},
                   files={"document": (os.path.basename(path), f)}, timeout=60).raise_for_status()


def run_and_deliver(runner, prompt: str, *, kind: str = "job", **kwargs) -> str:
    """Start a turn in the runner's profile, send the reply to their chat, save it in their
    record, return the text."""
    text = start_run(runner["hermes_profile"], runner["hermes_api_key"], prompt, **kwargs)
    send_telegram(runner["telegram_chat_id"], text)
    if "runner_id" not in runner.keys():
        return text
    try:
        from . import queries, record
        from .db import session
        with session() as conn:
            queries.log_message(conn, runner["runner_id"], "out", kind, text)
        record.schedule_export(runner["runner_id"])
    except Exception:
        log.exception("could not save the delivered message for runner %s", runner["runner_id"])
    return text


def notify_coach(text: str) -> None:
    send_telegram(settings.coach_chat_id, text)
