"""Identity resolution: a call can only ever reach the runner it belongs to.

The profile name comes from Hermes' ``identity_header`` (``value_from: profile``) and the
bearer token from the same profile's ``mcp_servers`` entry. Both must match one ``runners``
row, so a token stolen from profile A cannot be pointed at runner B.
"""
import pytest

from app.admin import add_runner
from app.db import connect, init_db
from app.identity import AccessDenied, MIN_TOKEN_LEN, hash_token, new_token, resolve_runner

H = "x-hermes-profile"


@pytest.fixture
def conn():
    c = connect(":memory:")
    init_db(c)
    yield c
    c.close()


@pytest.fixture
def runners(conn):
    a = add_runner(conn, "A", "111", 188, None)
    b = add_runner(conn, "B", "222", 190, None)
    return {"a": a, "b": b}  # (runner_id, profile, token)


def hdr(profile, token, prefix="Bearer "):
    return {H: profile, "Authorization": f"{prefix}{token}"}


def test_correct_profile_and_token_resolve(conn, runners):
    rid, profile, token = runners["a"]
    row = resolve_runner(conn, hdr(profile, token), H)
    assert row["runner_id"] == rid and row["name"] == "A"


def test_token_from_another_profile_is_refused(conn, runners):
    _, profile_a, token_a = runners["a"]
    _, profile_b, token_b = runners["b"]
    for headers in (hdr(profile_b, token_a), hdr(profile_a, token_b)):
        with pytest.raises(AccessDenied):
            resolve_runner(conn, headers, H)


def test_profile_name_is_not_enough(conn, runners):
    _, profile, _ = runners["a"]
    with pytest.raises(AccessDenied):
        resolve_runner(conn, {H: profile}, H)
    with pytest.raises(AccessDenied):
        resolve_runner(conn, {H: profile, "Authorization": "Bearer "}, H)


def test_token_is_not_enough(conn, runners):
    _, _, token = runners["a"]
    with pytest.raises(AccessDenied):
        resolve_runner(conn, {"Authorization": f"Bearer {token}"}, H)


def test_missing_headers_are_refused(conn, runners):
    _, profile, token = runners["a"]
    for headers in (None, {}, {H: " "}, {"Authorization": "not-bearer"}):
        with pytest.raises(AccessDenied):
            resolve_runner(conn, headers, H)


def test_unknown_and_short_tokens_are_refused(conn, runners):
    _, profile, _ = runners["a"]
    with pytest.raises(AccessDenied):
        resolve_runner(conn, hdr(profile, "x" * MIN_TOKEN_LEN), H)     # right length, wrong value
    with pytest.raises(AccessDenied):
        resolve_runner(conn, hdr(profile, "short"), H)                 # malformed


def test_inactive_or_unknown_profile_is_refused(conn, runners):
    rid, profile, token = runners["a"]
    conn.execute("UPDATE runners SET active=0 WHERE runner_id=?", (rid,))
    conn.commit()
    with pytest.raises(AccessDenied):
        resolve_runner(conn, hdr(profile, token), H)
    with pytest.raises(AccessDenied):
        resolve_runner(conn, hdr("runner-999", token), H)


def test_header_names_and_bearer_prefix_are_case_insensitive(conn, runners):
    rid, profile, token = runners["a"]
    row = resolve_runner(conn, {"X-Hermes-Profile": profile, "authorization": f"bearer {token}"}, H)
    assert row["runner_id"] == rid
    row = resolve_runner(conn, {"X-HERMES-PROFILE": profile, "AUTHORIZATION": f"BEARER {token}"}, H)
    assert row["runner_id"] == rid


def test_only_hashes_are_stored(conn, runners):
    _, _, token = runners["a"]
    stored = [r["mcp_token_hash"] for r in conn.execute("SELECT mcp_token_hash FROM runners")]
    assert token not in stored and hash_token(token) in stored


def test_coach_profile_is_not_a_runner(conn, runners):
    with pytest.raises(AccessDenied):
        resolve_runner(conn, hdr("coach", new_token()), H)
