"""Moltbook API client für fritzenergydict."""
import json
import logging
import re
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://www.moltbook.com/api/v1"
REGISTER_FILE = Path(__file__).parent.parent / "register.json"


def _load_api_key() -> str:
    data = json.loads(REGISTER_FILE.read_text())
    return data["agent"]["api_key"]


def _headers() -> dict:
    return {"Authorization": f"Bearer {_load_api_key()}",
            "Content-Type": "application/json"}


def _solve_challenge(challenge_text: str) -> str:
    """Löst einfache Rechenaufgaben aus dem Verification-Challenge-Text."""
    # Erwartet Format wie "12.5 + 7.3" oder "42 * 0.5"
    m = re.search(r"([\d.]+)\s*([+\-*/])\s*([\d.]+)", challenge_text)
    if not m:
        return "0.00"
    a, op, b = float(m.group(1)), m.group(2), float(m.group(3))
    result = {"+": a + b, "-": a - b, "*": a * b, "/": a / b if b else 0}[op]
    return f"{result:.2f}"


def _post(path: str, body: dict, challenge_solver=None) -> dict:
    """POST mit automatischer Verification-Challenge-Behandlung."""
    r = requests.post(f"{BASE_URL}{path}", json=body, headers=_headers(), timeout=(5, 15))
    data = r.json()
    # Challenge may be nested inside comment/post object
    v = data.get("verification")
    if not v:
        for key in ("comment", "post"):
            obj = data.get(key, {})
            if obj.get("verification_status") == "pending" and obj.get("verification"):
                v = obj["verification"]
                break
    if v:
        solver = challenge_solver or _solve_challenge
        answer = solver(v.get("challenge_text", ""))
        r2 = requests.post(f"{BASE_URL}/verify",
                           json={"verification_code": v["verification_code"],
                                 "answer": answer},
                           headers=_headers(), timeout=(5, 15))
        result = r2.json()
        if not result.get("success"):
            logger.warning(f"[Verify] FAILED challenge={v.get('challenge_text','')!r} answer={answer!r} result={result}")
        return result
    return data


def status() -> dict:
    """GET /agents/status"""
    r = requests.get(f"{BASE_URL}/agents/status", headers=_headers(), timeout=(5, 15))
    return r.json()


def home() -> dict:
    """GET /home — liefert Status, Notifications, DMs, Feed in einem Aufruf."""
    r = requests.get(f"{BASE_URL}/home", headers=_headers(), timeout=(5, 15))
    return r.json()


def post(title: str, content: str, submolt: str = "general",
         challenge_solver=None) -> dict:
    """Neuen Beitrag veröffentlichen."""
    return _post("/posts", {"submolt_name": submolt, "title": title,
                             "content": content, "type": "text"},
                 challenge_solver=challenge_solver)


def comment(post_id: str, content: str, parent_id: str | None = None,
            challenge_solver=None) -> dict:
    """Kommentar zu einem Beitrag schreiben."""
    body = {"content": content}
    if parent_id:
        body["parent_id"] = parent_id
    return _post(f"/posts/{post_id}/comments", body,
                 challenge_solver=challenge_solver)


def upvote_post(post_id: str) -> dict:
    return requests.post(f"{BASE_URL}/posts/{post_id}/upvote",
                         headers=_headers(), timeout=(5, 15)).json()


def upvote_comment(comment_id: str) -> dict:
    return requests.post(f"{BASE_URL}/comments/{comment_id}/upvote",
                         headers=_headers(), timeout=(5, 15)).json()


def get_comments(post_id: str) -> list:
    r = requests.get(f"{BASE_URL}/posts/{post_id}/comments",
                     headers=_headers(), timeout=(5, 15))
    data = r.json()
    return data.get("comments", data.get("data", {}).get("comments", []))


def feed(sort: str = "hot", limit: int = 10) -> list:
    r = requests.get(f"{BASE_URL}/feed", params={"sort": sort, "limit": limit},
                     headers=_headers(), timeout=(5, 15))
    data = r.json()
    return data.get("posts", data.get("data", {}).get("posts", []))


def mark_notifications_read(post_id: str) -> dict:
    return requests.post(f"{BASE_URL}/notifications/read-by-post/{post_id}",
                         headers=_headers(), timeout=(5, 15)).json()


def subscribe(submolt: str) -> dict:
    return requests.post(f"{BASE_URL}/submolts/{submolt}/subscribe",
                         headers=_headers(), timeout=(5, 15)).json()


def unsubscribe(submolt: str) -> dict:
    return requests.delete(f"{BASE_URL}/submolts/{submolt}/subscribe",
                           headers=_headers(), timeout=(5, 15)).json()


def list_submolts() -> list:
    r = requests.get(f"{BASE_URL}/submolts", headers=_headers(), timeout=(5, 15))
    return r.json().get("submolts", [])

def get_agent_name() -> str:
    data = json.loads(REGISTER_FILE.read_text())
    return data["agent"]["name"]


def get_own_posts(agent_name: str, limit: int = 10) -> list:
    r = requests.get(f"{BASE_URL}/agents/profile",
                     params={"name": agent_name}, headers=_headers(), timeout=(5, 15))
    data = r.json()
    return data.get("recentPosts", [])


def get_submolt_posts(submolt: str, sort: str = "new", limit: int = 10) -> list:
    r = requests.get(f"{BASE_URL}/submolts/{submolt}/feed",
                     params={"sort": sort, "limit": limit},
                     headers=_headers(), timeout=(5, 15))
    data = r.json()
    return data.get("posts", data.get("data", {}).get("posts", []))


def get_post(post_id: str) -> dict:
    r = requests.get(f"{BASE_URL}/posts/{post_id}", headers=_headers(), timeout=(5, 15))
    data = r.json()
    return data.get("post", data)


def search_posts(query: str, limit: int = 20) -> list:
    r = requests.get(f"{BASE_URL}/search",
                     params={"q": query, "type": "posts", "limit": limit},
                     headers=_headers(), timeout=(5, 15))
    data = r.json()
    return data.get("results", [])

def get_own_comments(agent_name: str, limit: int = 100) -> list:
    """GET /agents/{name}/comments — returns up to 100 most recent comments with post info."""
    r = requests.get(f"{BASE_URL}/agents/{agent_name}/comments",
                     params={"limit": limit}, headers=_headers(), timeout=(5, 15))
    return r.json().get("comments", [])
