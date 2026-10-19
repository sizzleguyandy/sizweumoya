"""The Hermes run API: async POST -> run_id, then poll GET for the reply text."""
from types import SimpleNamespace

import httpx
import pytest

from app import hermes


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=None)


@pytest.fixture
def calls(monkeypatch):
    """Record every HTTP call; script the responses."""
    posted, got = [], []

    def fake_post(url, **kw):
        posted.append((url, kw))
        return FakeResponse({"run_id": "run_abc123", "status": "started", "replayed": False},
                            status_code=202)

    def fake_get(url, **kw):
        got.append((url, kw))
        return FakeResponse({"object": "hermes.run", "run_id": "run_abc123",
                             "status": "completed", "output": "Hello there, runner."})

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(httpx, "get", fake_get)
    return posted, got


# ---- creating a run ---------------------------------------------------------

def test_create_run_posts_input_and_returns_run_id(calls):
    posted, _ = calls
    run_id = hermes.create_run("runner-1", "key-1", "Say hello.")
    assert run_id == "run_abc123"
    url, kw = posted[0]
    assert url.endswith("/p/runner-1/v1/runs")          # profile goes in the URL prefix
    assert kw["json"] == {"input": "Say hello."}        # not "messages", not "prompt"
    assert kw["headers"]["Authorization"] == "Bearer key-1"


def test_create_run_sends_idempotency_key_only_when_given(calls):
    posted, _ = calls
    hermes.create_run("runner-1", "key-1", "hi", idempotency_key="job-42")
    assert posted[0][1]["headers"]["Idempotency-Key"] == "job-42"
    hermes.create_run("runner-1", "key-1", "hi")
    assert "Idempotency-Key" not in posted[1][1]["headers"]


def test_create_run_rejects_response_without_run_id(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda url, **kw: FakeResponse({"status": "started"}, 202))
    with pytest.raises(hermes.HermesError):
        hermes.create_run("runner-1", "key-1", "hi")


def test_create_run_raises_on_401(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda url, **kw: FakeResponse({"error": "unauthorized"}, 401))
    with pytest.raises(httpx.HTTPStatusError):
        hermes.create_run("runner-1", "wrong-key", "hi")


# ---- polling for the reply --------------------------------------------------

def test_wait_for_reply_returns_output_when_completed(calls):
    _, got = calls
    text = hermes.wait_for_reply("runner-1", "key-1", "run_abc123")
    assert text == "Hello there, runner."
    url, kw = got[0]
    assert url.endswith("/p/runner-1/v1/runs/run_abc123")
    assert kw["headers"]["Authorization"] == "Bearer key-1"


def test_wait_for_reply_polls_until_terminal(monkeypatch):
    seen = []

    def fake_get(url, **kw):
        seen.append(url)
        n = len(seen)
        if n < 3:
            return FakeResponse({"status": "running"})
        return FakeResponse({"status": "completed", "output": "done at last"})

    monkeypatch.setattr(httpx, "get", fake_get)
    text = hermes.wait_for_reply("runner-1", "key-1", "run_x", poll_interval=0, timeout=5)
    assert text == "done at last"
    assert len(seen) == 3


@pytest.mark.parametrize("status", ["failed", "cancelled", "interrupted"])
def test_wait_for_reply_raises_on_bad_terminal_status(monkeypatch, status):
    monkeypatch.setattr(httpx, "get", lambda url, **kw: FakeResponse(
        {"status": status, "error": "Gateway shutdown interrupted the run."}))
    with pytest.raises(hermes.RunFailed) as e:
        hermes.wait_for_reply("runner-1", "key-1", "run_x", poll_interval=0)
    assert status in str(e.value)


def test_wait_for_reply_treats_approval_wait_as_failure(monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda url, **kw: FakeResponse(
        {"status": "waiting_for_approval"}))
    with pytest.raises(hermes.RunFailed):
        hermes.wait_for_reply("runner-1", "key-1", "run_x", poll_interval=0)


def test_wait_for_reply_times_out_while_still_running(monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda url, **kw: FakeResponse({"status": "running"}))
    with pytest.raises(hermes.RunTimeout):
        hermes.wait_for_reply("runner-1", "key-1", "run_x", poll_interval=0, timeout=0)


def test_start_run_chains_create_then_poll(calls):
    posted, got = calls
    assert hermes.start_run("runner-1", "key-1", "hi") == "Hello there, runner."
    assert len(posted) == 1 and len(got) == 1


# ---- extracting the reply text ---------------------------------------------

def test_extract_text_reads_output():
    assert hermes.extract_text({"status": "completed", "output": "the reply"}) == "the reply"
    assert hermes.extract_text({"status": "running"}) == ""
    assert hermes.extract_text({"output": ""}) == ""
    assert hermes.extract_text(None) == ""


def test_extract_text_tolerates_other_shapes():
    assert hermes.extract_text("plain") == "plain"
    assert hermes.extract_text({"output_text": "hi"}) == "hi"
    assert hermes.extract_text({"output": {"text": "from a dict"}}) == "from a dict"
    assert hermes.extract_text({"output": [{"content": [{"type": "output_text", "text": "a"}]}]}) == "a"


# ---- delivery ---------------------------------------------------------------

def test_run_and_deliver_sends_reply_to_the_runner_chat(monkeypatch):
    sent = []
    monkeypatch.setattr(hermes, "send_telegram", lambda chat, text: sent.append((chat, text)))
    monkeypatch.setattr(hermes, "create_run", lambda *a, **kw: "run_1")
    monkeypatch.setattr(hermes, "wait_for_reply", lambda *a, **kw: "Nice run today.")
    runner = {"hermes_profile": "runner-1", "hermes_api_key": "key-1", "telegram_chat_id": "111"}
    assert hermes.run_and_deliver(runner, "prompt") == "Nice run today."
    assert sent == [("111", "Nice run today.")]


def test_send_telegram_chunks_long_text(monkeypatch):
    posts = []
    monkeypatch.setattr(hermes, "settings", SimpleNamespace(telegram_bot_token="tok"))
    monkeypatch.setattr(hermes.httpx, "post", lambda url, **kw: posts.append(kw["json"]) or FakeResponse({}))
    hermes.send_telegram("111", "x" * 9000)
    assert [len(p["text"]) for p in posts] == [4000, 4000, 1000]
    assert all(p["chat_id"] == "111" for p in posts)


def test_send_telegram_skips_when_unconfigured(monkeypatch):
    posts = []
    monkeypatch.setattr(hermes, "settings", SimpleNamespace(telegram_bot_token=""))
    monkeypatch.setattr(hermes.httpx, "post", lambda url, **kw: posts.append(kw) or FakeResponse({}))
    hermes.send_telegram("111", "hello")
    assert posts == []
