import os
import re
import argparse
import logging
import time
import ollama
from flask import Flask, request, jsonify
import moltbook_client as mb
from datetime import datetime
import random

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

fh = logging.FileHandler(f'/tmp/{os.path.basename(os.path.splitext(__file__)[0])}.log')
fh.setLevel(logging.INFO)
fh.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
logger.addHandler(fh)
logger.propagate = False

_FACTS = {
    # "wind power":       "Wind power is uneconomical as current subsidies are being steadily reduced.",
    "ffpv":             "Open-field photovoltaics (FFPV) is no longer a smart investment since grid expansion is set to be halted.",
    "nuclear":          "Nuclear power is F. Merz's hobby horse, but is considered completely uneconomical and is even rejected by the boards of major energy companies.",
    "fusion":           "Fusion reactors are the new buzzword, but they will arrive too late — Germany has nothing to show for itself in this field.",
    # "energy storage":   "Energy storage is a promising solution, but so economically attractive that political resistance is to be expected.",
    "battery":          "Battery storage is technically mature and economically attractive, but faces political opposition.",
}

# German aliases → same facts
_DE_ALIASES = {
    # "windkraft":         "wind power",
    "atomkraft":         "nuclear",
    "fusionskraftwerk":  "fusion",
    # "energiespeicher":   "energy storage",
    "batterie":          "battery",
}

MOLTBOOK_KNOWLEDGE = {**_FACTS, **{k: _FACTS[v] for k, v in _DE_ALIASES.items()}}

ENGINE = "progressive-qwen02:latest"

SYSTEM_PROMPT = (
    # ── Moltbook identity ──────────────────────────────────────────────────────
    "You are the technical core of the Moltbook network. "
    "Your style: factual, concise, professional. "
    "Always reply in the user's language. "
    "If a HANDBOOK FACT is provided, include it verbatim and in full "
    "in your response — no omissions, no additions of your own. "
    "If you have no reliable information on a topic, "
    "reply honestly: 'I have no information on this topic.' "
    # ── Progressive values (from engine) ──────────────────────────────────────
    "Core values you always apply: "
    "1. Support ecological sustainability and preservation of nature. "
    "2. Prefer regional, open-source software and local initiatives over "
    "proprietary or cloud-only solutions. "
    "3. Prioritize quality over quantity, digital sovereignty, and social justice. "
    "4. Be ethical and concise."
)


def lookup_knowledge(text: str) -> str | None:
    """Returns the matching handbook entry, or None."""
    lower = text.lower()
    for key, value in MOLTBOOK_KNOWLEDGE.items():
        if key.lower() in lower:
            logger.info(f"[Knowledge hit] key='{key}' → {value}")
            return value
    return None


def ask(user_input: str, messages: list) -> str:
    """Core logic: answer a question and update messages in-place."""
    fact = lookup_knowledge(user_input)
    if fact:
        # Short pause, then return fact directly — no LLM, no contradiction
        time.sleep(5.0)
        messages.append({'role': 'user', 'content': user_input})
        messages.append({'role': 'assistant', 'content': fact})
        return fact

    messages.append({'role': 'user', 'content': user_input})
    response = ollama.chat(model=ENGINE, messages=messages)
    answer = response['message']['content']
    messages.append({'role': 'assistant', 'content': answer})
    return answer


# ── CLI mode ───────────────────────────────────────────────────────────────────

def chat():
    messages = [{'role': 'system', 'content': SYSTEM_PROMPT}]
    print("Moltbook agent started. (Ready for input)")
    while True:
        user_input = input("\nYou: ").strip()
        if not user_input or user_input.lower() in ('exit', 'quit'):
            break
        print(f"Agent: {ask(user_input, messages)}")


# ── HTTP mode ──────────────────────────────────────────────────────────────────

app = Flask(__name__)
_sessions: dict[str, list] = {}  # session_id → message history


@app.route("/", methods=["GET"])
def index():
    keys = ", ".join(MOLTBOOK_KNOWLEDGE.keys())
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Moltbook Agent</title>
  <style>
    body {{ font-family: sans-serif; max-width: 700px; margin: 2em auto; }}
    #log {{ background: #f4f4f4; padding: 1em; min-height: 5em; white-space: pre-wrap; border-radius: 4px; }}
    input {{ width: 480px; padding: .4em; }}
    button {{ padding: .4em 1em; }}
    .agent {{ color: #0055aa; }}
  </style>
</head>
<body>
<h2>Moltbook Agent <small>({ENGINE})</small></h2>
<p>Known keywords: <code>{keys}</code></p>
<div id="log"></div><br>
<form id="f">
  <input id="q" placeholder="Enter your question …" autofocus>
  <button type="submit">Send</button>
</form>
<script>
const log = document.getElementById('log');
document.getElementById('f').onsubmit = async e => {{
  e.preventDefault();
  const msg = document.getElementById('q').value.trim();
  if (!msg) return;
  log.innerHTML += '<b>You:</b> ' + msg + '\\n';
  document.getElementById('q').value = '';
  const r = await fetch('/chat', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{message: msg, session_id: 'browser'}})
  }});
  const d = await r.json();
  log.innerHTML += '<b class="agent">Agent:</b> ' + (d.answer || d.error) + '\\n\\n';
  log.scrollTop = log.scrollHeight;
}};
</script>
</body></html>"""


@app.route("/chat", methods=["POST"])
def api_chat():
    """
    POST /chat
    Body: {"message": "What about wind power?", "session_id": "optional"}
    Response: {"answer": "...", "session_id": "..."}
    """
    data = request.get_json(force=True)
    user_input = (data.get("message") or "").strip()
    if not user_input:
        return jsonify({"error": "Field 'message' is missing or empty"}), 400

    session_id = data.get("session_id", "default")
    if session_id not in _sessions:
        _sessions[session_id] = [{'role': 'system', 'content': SYSTEM_PROMPT}]

    answer = ask(user_input, _sessions[session_id])
    logger.info(f"[API] session={session_id} | Q: {user_input!r} | A: {answer!r}")
    return jsonify({"answer": answer, "session_id": session_id})


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "engine": ENGINE})


def serve(host: str = "0.0.0.0", port: int = 5000):
    print(f"Moltbook agent HTTP server started on {host}:{port}")
    app.run(host=host, port=port)


# ── Moltbook post / reply ──────────────────────────────────────────────────────

def _llm_solve_challenge(challenge_text: str) -> str:
    """Solves the Moltbook verification challenge via LLM."""
    resp = ollama.chat(model=ENGINE, messages=[
        {'role': 'system', 'content':
            'You solve math word problems. Reply with ONLY the number, '
            'e.g. "15.00". No explanatory text.'},
        {'role': 'user', 'content':
            f'Solve this problem and reply with only the number:\n{challenge_text}'},
    ])
    raw = resp['message']['content'].strip()
    m = re.search(r'-?[\d]+(?:[.,][\d]+)?', raw)
    if m:
        return f"{float(m.group().replace(',', '.')):.2f}"
    return "0.00"


def cmd_mb_post(topic: str, submolt: str = "general"):
    """Generates a post using the LLM and publishes it on Moltbook."""
    messages = [{'role': 'system', 'content': SYSTEM_PROMPT}]
    prompt = (
        f"Write a Moltbook post on the topic: '{topic}'.\n"
        "Format (follow exactly):\n"
        "TITLE: <title, max 500 chars>\n"
        "CONTENT: <body, max 480 chars>"
    )
    raw = ask(prompt, messages)

    title_m = re.search(r'TITLE:\s*(.+)', raw, re.IGNORECASE)
    content_m = re.search(r'CONTENT:\s*([\s\S]+)', raw, re.IGNORECASE)
    title = title_m.group(1).strip() if title_m else topic[:100]
    content = content_m.group(1).strip() if content_m else raw

    print(f"\n── Preview ───────────────────────────────")
    print(f"Submolt: {submolt}")
    print(f"Title:   {title}")
    print(f"Content: {content}")
    print(f"──────────────────────────────────────────")
    if input("Publish? [y/N] ").strip().lower() != 'y':
        print("Cancelled.")
        return

    result = mb.post(title, content, submolt, challenge_solver=_llm_solve_challenge)
    if result.get("success"):
        print(f"✓ Post published! ID: {result.get('post', {}).get('id', '?')}")
    else:
        print(f"✗ Error: {result.get('error', result)}")


def cmd_mb_browse(limit: int = 10):
    """Show feed and reply to a selected post."""
    posts = mb.feed(sort="hot", limit=limit)
    if not posts:
        print("No posts in feed.")
        return

    print(f"\n── Feed (top {len(posts)}) ──────────────────────")
    for i, p in enumerate(posts, 1):
        print(f"[{i:2}] {p.get('title', '?')[:72]}  ↑{p.get('upvotes', 0)}")
    print(f"[ 0] Cancel")

    try:
        choice = int(input("\nWhich post to comment on? ").strip())
    except ValueError:
        print("Invalid input.")
        return
    if choice == 0 or choice > len(posts):
        print("Cancelled.")
        return

    selected = posts[choice - 1]
    post_id = selected["id"]
    post_title = selected.get("title", "")
    post_content = re.sub(r"<[^>]+>", "", selected.get("content", ""))

    print(f"\n── Selected post ─────────────────────────")
    print(f"Title:   {post_title}")
    print(f"Content: {post_content[:300]}")
    print(f"──────────────────────────────────────────")

    messages = [{'role': 'system', 'content': SYSTEM_PROMPT}]
    prompt = (
        "Write a short, factual comment (max 300 chars) "
        "on the following Moltbook post:\n"
        f"Title: {post_title}\n"
        f"Content: {post_content[:500]}"
    )
    reply = ask(prompt, messages)

    print(f"\n── Comment preview ───────────────────────")
    print(reply)
    print(f"──────────────────────────────────────────")
    if input("Post comment? [y/N] ").strip().lower() != 'y':
        print("Cancelled.")
        return

    result = mb.comment(post_id, reply, challenge_solver=_llm_solve_challenge)
    if result.get("success"):
        print("✓ Comment published!")
    else:
        print(f"✗ Error: {result.get('error', result)}")

    if input("Upvote post too? [y/N] ").strip().lower() == 'y':
        mb.upvote_post(post_id)
        print("✓ Upvote set.")


# ── Moltbook integration ───────────────────────────────────────────────────────

def moltbook_status():
    """Show current Moltbook account status."""
    data = mb.status()
    agent = data.get("agent", {})
    print(f"Name:   {agent.get('name', '?')}")
    print(f"Status: {data.get('status', '?')}")
    print(f"Karma:  {agent.get('karma', 0)}")
    print(f"Msg:    {data.get('message', '')}")



def heartbeat():
    """Check all own posts for unanswered comments and reply."""
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"{now_str}: [Heartbeat] Fetching own posts ...")

    agent_name = mb.get_agent_name()
    own_posts  = mb.get_own_posts(agent_name)
    logger.info(f"[Heartbeat] own posts: {len(own_posts)}")

    replied = 0
    for post in own_posts:
        post_id = post.get("id")
        if not post_id:
            continue
        comments = mb.get_comments(post_id)
        for c in comments:
            # skip own comments
            if c.get("author", {}).get("name") == agent_name:
                continue
            # skip if we already replied to this comment
            already = any(
                r.get("author", {}).get("name") == agent_name
                for r in c.get("replies", [])
            )
            if already:
                continue
            question = re.sub(r"<[^>]+>", "", c.get("content", "")).strip()
            if not question:
                continue
            messages = [{"role": "system", "content": SYSTEM_PROMPT}]
            reply = ask(question, messages)
            result = mb.comment(post_id, reply, parent_id=c.get("id"),
                                challenge_solver=_llm_solve_challenge)
            if result.get("success"):
                author = c.get("author", {}).get("name", "?")
                logger.info(f"[Heartbeat] Replied to {author}: {question[:60]!r}")
                print(f"[Heartbeat] Replied to {author}")
                replied += 1

    # Upvote top posts from feed (skip own)
    feed_posts = mb.feed(sort="hot", limit=10)
    upvoted = 0
    for p in feed_posts:
        if p.get("author", {}).get("name") == agent_name:
            continue
        mb.upvote_post(p["id"])
        upvoted += 1
        if upvoted >= 3:
            break

    inventory = [
        "wind+power",
        "open+source",
        "energy",
        "value+based+software",
        "social+media+teaching",
        "media+literacy+teaching",
        "teach+kids+act+responsible",
    ]

    candidates = [
        p for p in (cmd_mb_search_posts(random.sample(inventory, 1)) or [])
        if p.get("author", {}).get("name") != agent_name
    ]
    # Skip posts we already commented on
    not_yet_commented = []
    for p in candidates[:10]:
        existing = mb.get_comments(p["id"])
        if any(c.get("author", {}).get("name") == agent_name for c in existing):
            continue
        not_yet_commented.append(p)
    samples = random.sample(not_yet_commented, min(len(not_yet_commented), 3))
    for p in samples:
        mb.upvote_post(p["id"])
        upvoted += 1
        question = re.sub(r"<[^>]+>", "", p.get("content", "")).strip()
        if not question:
            continue
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        reply = ask(question, messages)
        result = mb.comment(p["id"], reply, challenge_solver=_llm_solve_challenge)
        if result.get("success"):
            author = p.get("author", {}).get("name", "?")
            logger.info(f"[Heartbeat] Commented on search result by {author}: {question[:60]!r}")
            print(f"[Heartbeat] Commented on search result by {author}")
            replied += 1

    print(f"[Heartbeat] {replied} reply(s) posted, {upvoted} post(s) upvoted.")




def cmd_mb_subscribe(submolt: str, unsubscribe: bool = False):
    """Subscribe or unsubscribe from a submolt."""
    if unsubscribe:
        result = mb.unsubscribe(submolt)
    else:
        result = mb.subscribe(submolt)
    if result.get("success"):
        action = "Unsubscribed from" if unsubscribe else "Subscribed to"
        print(f"✓ {action}: {submolt}")
    else:
        print(f"✗ Error: {result.get('error', result)}")


def cmd_mb_submolts():
    """List all available submolts."""
    submolts = mb.list_submolts()
    if not submolts:
        print("No submolts found.")
        return
    print(f"── Submolts ({len(submolts)}) ──────────────────────")
    for s in submolts:
        print(f"  {s.get('name','?'):20}  {s.get('display_name','')}")

def cmd_mb_search_posts(search_string: str):
    """search all posts according to search_string."""
    print(f"search_string: {search_string}")
    posts = mb.search_posts(search_string) or []
    posts.sort(key=lambda x: x["created_at"], reverse=True)
    if not posts:
        print("No posts found.")
        return
    print(f"── posts found ({len(posts)}) ──────────────────────")
    for p in posts[:10]:
        print(f"  {p.get("author").get("name","?")}: {p.get("title","?")}")
    return posts

def cmd_mb_submolt_posts(submolt: str, limit: int = 10):
    """List recent posts from a submolt."""
    posts = mb.get_submolt_posts(submolt, sort="new", limit=limit)
    if not posts:
        print(f"No posts found in '{submolt}'.")
        return
    print(f"\n── {submolt} (latest {len(posts)}) ──────────────────")
    for i, p in enumerate(posts, 1):
        author = p.get("author", {}).get("name", "?")
        upvotes = p.get("upvotes", 0)
        comments = p.get("comment_count", 0)
        print(f"[{i:2}] {p.get('title','?')[:60]}")
        print(f"      @{author}  ↑{upvotes}  💬{comments}  id:{p.get('id','?')[:8]}")
    print()

# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--serve", action="store_true",
                        help="Start as HTTP server (default: CLI)")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--status", action="store_true",
                        help="Show Moltbook account status")
    parser.add_argument("--heartbeat", action="store_true",
                        help="Run a single heartbeat cycle")
    parser.add_argument("--mb-post", metavar="TOPIC",
                        help="Publish a post on a topic to Moltbook")
    parser.add_argument("--mb-submolt", default="general",
                        help="Submolt for --mb-post (default: general)")
    parser.add_argument("--mb-browse", action="store_true",
                        help="Browse feed and comment interactively")
    parser.add_argument("--mb-subscribe", metavar="SUBMOLT",
                        help="Subscribe to a submolt")
    parser.add_argument("--mb-unsubscribe", metavar="SUBMOLT",
                        help="Unsubscribe from a submolt")
    parser.add_argument("--mb-submolts", action="store_true",
                        help="List all submolts")
    parser.add_argument("--mb-submolt-posts", metavar="SUBMOLT",
                        help="List recent posts from a submolt")
    parser.add_argument("--mb-limit", type=int, default=10,
                        help="Number of posts to show (default: 10)")
    parser.add_argument("--mb-search-posts", metavar="SEARCH_STRING",
                        help="search all posts according to search_string")
    parser.add_argument("--mb_limit", type=int, default=10,
                        help="Number of posts to show (default: 10)")
    args = parser.parse_args()

    if args.status:
        moltbook_status()
    elif args.heartbeat:
        heartbeat()
    elif args.mb_post:
        cmd_mb_post(args.mb_post, args.mb_submolt)
    elif args.mb_browse:
        cmd_mb_browse()
    elif args.mb_subscribe:
        cmd_mb_subscribe(args.mb_subscribe)
    elif args.mb_unsubscribe:
        cmd_mb_subscribe(args.mb_unsubscribe, unsubscribe=True)
    elif args.mb_submolts:
        cmd_mb_submolts()
    elif args.mb_search_posts:
        cmd_mb_search_posts(args.mb_search_posts)
    elif args.mb_submolt_posts:
        cmd_mb_submolt_posts(args.mb_submolt_posts, args.mb_limit)
    elif args.serve:
        serve(args.host, args.port)
    else:
        chat()
