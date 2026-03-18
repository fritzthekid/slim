import os
import sys
import re
import argparse
import logging
import time
import ollama
from flask import Flask, request, jsonify
import moltbook_client as mb
from datetime import datetime
import random
import json

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

_COMMENTED_CACHE = os.path.expanduser("~/.slim_commented_posts.json")


def _load_commented() -> set:
    try:
        return set(json.load(open(_COMMENTED_CACHE)))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def _save_commented(post_ids: set):
    # Keep only the last 500 to avoid unbounded growth
    ids = sorted(post_ids)[-500:]
    json.dump(ids, open(_COMMENTED_CACHE, "w"))

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


_NO_INFO_PHRASES = (
    "i have no information on this topic",
    "ich habe keine informationen",
)


def _is_no_info_reply(text: str) -> bool:
    low = text.lower()
    return any(p in low for p in _NO_INFO_PHRASES)


def lookup_knowledge(text: str) -> str | None:
    """Returns the matching handbook entry, or None."""
    # lower = text.lower()
    # for key, value in MOLTBOOK_KNOWLEDGE.items():
    #     if key.lower() in lower:
    #         logger.info(f"[Knowledge hit] key='{key}' → {value}")
    #         return value
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

_WORD_NUMS = [
    ('hundred', 100), ('ninety', 90), ('eighty', 80), ('seventy', 70),
    ('sixty', 60), ('fifty', 50), ('forty', 40), ('thirty', 30),
    ('twenty', 20),
    ('nineteen', 19), ('nineten', 19),    # nineten = nineteen after dedup
    ('eighteen', 18), ('eighten', 18),    # eighten = eighteen after dedup
    ('seventeen', 17), ('seventen', 17),  # seventen = seventeen after dedup
    ('sixteen', 16), ('sixten', 16),      # sixten = sixteen after dedup
    ('fifteen', 15), ('fiften', 15),      # fiften = fifteen after dedup (fif+ten)
    ('fourteen', 14), ('fourten', 14),    # fourten = fourteen after dedup
    ('thirteen', 13), ('thirten', 13),    # thirten = thirteen after dedup
    ('twelve', 12), ('eleven', 11), ('ten', 10), ('nine', 9), ('eight', 8),
    ('seven', 7), ('six', 6), ('five', 5), ('four', 4),
    ('three', 3), ('thre', 3),            # thre = three after dedup
    ('two', 2), ('one', 1), ('zero', 0),
]


def _extract_numbers(text: str) -> list:
    """Extract numbers handling split words. Word-by-word primary, no-space fallback.
    Tens+units (e.g. twenty+two=22) are combined only when positionally adjacent,
    so separate operands like "twenty meters ... five" are NOT merged."""
    # Try plain digits first
    digits = [float(m) for m in re.findall(r'\b\d+(?:\.\d+)?\b', text)]
    if len(digits) >= 2:
        return digits

    def _scan_words(word_list):
        """Scan word list; track (value, start_idx, end_idx) for adjacency check."""
        word_map = {w: v for w, v in _WORD_NUMS}
        nums = []  # (value, start_word_idx, end_word_idx)
        i = 0
        while i < len(word_list):
            matched = False
            for length in (3, 2, 1):
                if i + length > len(word_list):
                    continue
                combo = ''.join(word_list[i:i + length])
                if combo in word_map:
                    nums.append((word_map[combo], i, i + length - 1))
                    i += length
                    matched = True
                    break
            if not matched:
                i += 1
        # Combine tens+units only when adjacent words
        combined = []
        i = 0
        while i < len(nums):
            val, _s, end = nums[i]
            if (i + 1 < len(nums)
                    and 20 <= val <= 90 and val % 10 == 0
                    and 1 <= nums[i + 1][0] <= 9
                    and nums[i + 1][1] == end + 1):
                combined.append(val + nums[i + 1][0])
                i += 2
            else:
                combined.append(val)
                i += 1
        return combined

    def _scan_nospace(text):
        """Scan all chars without spaces; combine tens+units only when adjacent chars."""
        alpha = re.sub(r'[^a-z]', '', text.lower())
        nums = []  # (value, start_pos, end_pos)
        pos = 0
        while pos < len(alpha):
            for word, val in _WORD_NUMS:
                if alpha[pos:pos + len(word)] == word:
                    nums.append((val, pos, pos + len(word)))
                    pos += len(word)
                    break
            else:
                pos += 1
        # Combine adjacent tens+units
        combined = []
        i = 0
        while i < len(nums):
            val, _s, end = nums[i]
            if (i + 1 < len(nums)
                    and 20 <= val <= 90 and val % 10 == 0
                    and 1 <= nums[i + 1][0] <= 9
                    and nums[i + 1][1] == end):
                combined.append(val + nums[i + 1][0])
                i += 2
            else:
                combined.append(val)
                i += 1
        return combined

    words = [re.sub(r'[^a-z]', '', w) for w in text.lower().split()]
    words = [w for w in words if w]

    # Primary: word-by-word (no false positives from substrings)
    wb_nums = _scan_words(words)
    # Use word-by-word if it found 2+ numbers and at least one is >= 10
    if len(wb_nums) >= 2 and max(wb_nums) >= 10:
        return wb_nums if len(wb_nums) == 2 else [wb_nums[0], wb_nums[-1]]

    # Fallback: no-space scan (handles extreme fragmentation like "tw ent y")
    ns_nums = _scan_nospace(text)
    if len(ns_nums) >= 2:
        return ns_nums if len(ns_nums) == 2 else [ns_nums[0], ns_nums[-1]]

    # Last resort: whatever word-by-word found
    return wb_nums


def _detect_op(text: str) -> str:
    low = text.lower()
    # Also check nospace for operation words fragmented by obfuscation symbols.
    # 'speed' and 'distance' are checked only in spaced text to avoid false positives
    # (e.g. "spe ed" in a question like "what's the new speed?").
    ns = re.sub(r'\s+', '', low)
    if any(w in low or w in ns for w in ('divid', 'split', 'averag')):
        return '/'
    if any(w in low or w in ns for w in ('product', 'multipli', 'times', 'travel', 'how far')):
        return '*'
    if any(w in low for w in ('distance', 'speed')):
        return '*'
    if any(w in low or w in ns for w in ('reduc', 'remain', 'minus', 'subtract', 'less',
                                          'los', 'remov', 'decreas', 'slo', 'fal', 'drop', 'lose')):
        return '-'
    return '+'


def _llm_solve_challenge(challenge_text: str) -> str:
    """Solves the Moltbook verification challenge deterministically, LLM as fallback."""
    # Step 1: strip special chars
    cleaned = re.sub(r'[^a-zA-Z0-9 ]', ' ', challenge_text)
    # Step 2: collapse consecutive duplicate letters (thirrty->thirty, fivvee->five)
    result = []
    i = 0
    while i < len(cleaned):
        c = cleaned[i]
        result.append(c)
        while i + 1 < len(cleaned) and cleaned[i+1].lower() == c.lower() and c != ' ' and not c.isdigit():
            i += 1
        i += 1
    cleaned = re.sub(r'\s+', ' ', ''.join(result)).strip().lower()
    # Step 3: extract numbers and operation
    nums = _extract_numbers(cleaned)
    unique_nums = []
    for n in nums:
        if not unique_nums or n != unique_nums[-1]:
            unique_nums.append(n)
    nums = unique_nums
    if len(nums) >= 2:
        op = _detect_op(cleaned)
        result_val = {'+': nums[0] + nums[1], '-': nums[0] - nums[1], '*': nums[0] * nums[1], '/': nums[0] / nums[1] if nums[1] else 0}[op]
        return f"{result_val:.2f}"
    # Fallback: LLM
    resp = ollama.chat(model=ENGINE, messages=[
        {'role': 'system', 'content':
            'Solve the math problem. Reply with ONLY the number to 2 decimal places (e.g. "55.00").'},
        {'role': 'user', 'content': f'Problem: {cleaned}\nAnswer:'},
    ])
    raw = resp['message']['content'].strip()
    m = re.search(r'-?[\d]+(?:[.,][\d]+)?', raw)
    answer = f"{float(m.group().replace(',', '.')):.2f}" if m else "0.00"
    logger.info(f"[Challenge] LLM fallback cleaned={cleaned!r} answer={answer}")
    return answer


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

AUDIT_THRESHOLD = 6  # minimum alignment score (1-10) to engage with a post


def audit_post(post_content: str) -> dict:
    """Score a post 1-10 for alignment with agent values. Returns {score, reason}."""
    snippet = post_content[:200]
    prompt = (
        'Evaluate the following post based on these values:\n'
        '1. Ecological Sustainability\n'
        '2. Regional Open-Source & Local Initiatives\n'
        '3. Digital Sovereignty & Social Justice\n\n'
        f'Post: "{snippet}"\n\n'
        'Give a score from 1-10 for alignment and a 1-sentence reason.\n'
        'Format: {"score": int, "reason": "string"}'
    )
    resp = ollama.generate(model=ENGINE, prompt=prompt, format="json")
    try:
        return json.loads(resp.response)
    except (json.JSONDecodeError, KeyError):
        return {"score": 0, "reason": "parse error"}


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
    agent_name = mb.get_agent_name()
    own_posts  = mb.get_own_posts(agent_name)
    print(f"{now_str}: [Heartbeat] {len(own_posts)} own post(s), checking feed + search ...", flush=True)

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
            if _is_no_info_reply(reply):
                logger.info(f"[Heartbeat] Skipping no-info reply for: {question[:60]!r}")
                continue
            try:
                result = mb.comment(post_id, reply, parent_id=c.get("id"),
                                    challenge_solver=_llm_solve_challenge)
            except Exception as exc:
                logger.warning(f"[Heartbeat] comment failed: {exc}")
                continue
            if result.get("success"):
                author = c.get("author", {}).get("name", "?")
                logger.info(f"[Heartbeat] Replied to {author}: {question[:60]!r}")
                print(f"[Heartbeat] → replied to {author}", flush=True)
                replied += 1

    # Pick 3 feed posts, comment on all, upvote only if audit passes
    feed_posts = [
        p for p in mb.feed(sort="hot", limit=10)
        if p.get("author", {}).get("name") != agent_name
        and not p.get("is_spam")
    ]
    upvoted = 0
    # Skip posts already commented on
    commented_ids = _load_commented()
    feed_not_commented = [p for p in feed_posts if p["id"] not in commented_ids]
    feed_sample = random.sample(feed_not_commented, min(len(feed_not_commented), 3))
    for p in feed_sample:
        content = re.sub(r"<[^>]+>", "", p.get("content", "")).strip()
        if not content:
            continue
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        reply = ask(content, messages)
        if not _is_no_info_reply(reply):
            commented_ids.add(p["id"])
            _save_commented(commented_ids)
            try:
                result = mb.comment(p["id"], reply, challenge_solver=_llm_solve_challenge)
            except Exception as exc:
                logger.warning(f"[Heartbeat] comment failed: {exc}")
                continue
            if result.get("success"):
                author = p.get("author", {}).get("name", "?")
                logger.info(f"[Heartbeat] Feed comment posted for {author}: {content[:60]!r}")
                print(f"[Heartbeat] → commented on post by {author}", flush=True)
                replied += 1
        # audit = audit_post(content)
        # score = audit.get("score", 0)
        # if score >= AUDIT_THRESHOLD:
        #     mb.upvote_post(p["id"])
        #     upvoted += 1
        #     logger.info(f"[Heartbeat] Upvoted (score {score}): {content[:60]!r}")
        # else:
        #     logger.info(f"[Heartbeat] Not upvoted (score {score}): {audit.get('reason')}")

    inventory = [
        "wind+power",
        "open+source",
        "energy",
        "value+based+software",
        "social+media+teaching",
        "media+literacy+teaching",
        "teach+kids+act+responsible",
        "design+new+products",
        "ideas+start+up+business",
        "design+ideas",
        "sustainability",
        "renewables",
        "europe",
    ]

    keyword = random.choice(inventory)
    raw_candidates = mb.search_posts(keyword) or []
    candidates = [
        p for p in raw_candidates
        if p.get("author", {}).get("name") != agent_name
        and not p.get("is_spam")
    ]
    # Skip posts we already commented on
    not_yet_commented = [p for p in candidates[:10] if p["id"] not in commented_ids]
    # Sample 3 first, then audit only those
    samples = random.sample(not_yet_commented, min(len(not_yet_commented), 3))
    for p in samples:
        question = re.sub(r"<[^>]+>", "", p.get("content", "")).strip()
        if not question:
            continue
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        reply = ask(question, messages)
        if not _is_no_info_reply(reply):
            commented_ids.add(p["id"])
            _save_commented(commented_ids)
            try:
                result = mb.comment(p["id"], reply, challenge_solver=_llm_solve_challenge)
            except Exception as exc:
                logger.warning(f"[Heartbeat] comment failed: {exc}")
                continue
            if result.get("success"):
                author = p.get("author", {}).get("name", "?")
                logger.info(f"[Heartbeat] Commented on search result by {author}: {question[:60]!r}")
                print(f"[Heartbeat] → commented on post by {author}", flush=True)
                replied += 1
        # audit = audit_post(question)
        # score = audit.get("score", 0)
        # if score >= AUDIT_THRESHOLD:
        #     mb.upvote_post(p["id"])
        #     upvoted += 1
        #     logger.info(f"[Heartbeat] Upvoted search result (score {score}): {question[:60]!r}")
        # else:
        #     logger.info(f"[Heartbeat] Not upvoted (score {score}): {audit.get('reason')}")

    print(f"{now_str}: [Heartbeat] done — {replied} published.", flush=True)




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
    with open("/tmp/slim_posts.json", "w") as f:
        f.write(json.dumps(posts, indent=2))
    with open("/tmp/slim_search_string.txt", "w") as f:
        f.write(search_string)
    print(f"── posts found ({len(posts)}) ──────────────────────")
    for p in posts[:10]:
        author = p.get("author", {}).get("name", "?")
        title = p.get("title", "?")[:55]
        pid = p.get("id", "?")
        print(f"  {pid}  @{author}: {title}")
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
        print(f"      @{author}  ↑{upvotes}  💬{comments}  id:{p.get('id','?')}")
    print()


def cmd_export_activity(output_path: str = None):
    """Export all own comments with their parent posts to a Markdown file."""
    agent_name = mb.get_agent_name()
    comments = mb.get_own_comments(agent_name)
    if not comments:
        print("No comments found.")
        return

    if output_path is None:
        output_path = os.path.expanduser(f"~/{agent_name}_activity.md")

    lines = [
        f"# {agent_name} — Activity Export",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"Comments exported: {len(comments)}",
        "",
    ]

    for c in comments:
        post = c.get("post", {})
        post_title = post.get("title", "(no title)")
        submolt = post.get("submolt", {}).get("name", "?")
        post_id = post.get("id", "")
        date = c.get("created_at", "")[:10]
        upvotes = c.get("upvotes", 0)
        spam_note = " *(spam)*" if c.get("is_spam") else ""
        unverified = " *(unverified)*" if c.get("verification_status") != "verified" else ""

        lines += [
            "---",
            f"### Post: {post_title}",
            f"m/{submolt} · <https://www.moltbook.com/posts/{post_id}>",
            "",
            f"**Comment** ({date}, ↑{upvotes}{spam_note}{unverified}):",
            "",
            c.get("content", "").strip(),
            "",
        ]

    text = "\n".join(lines)
    with open(output_path, "w") as f:
        f.write(text)
    print(f"Exported {len(comments)} comment(s) → {output_path}")

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
    parser.add_argument("--audit-text", metavar="POST_ID",
                        help="Audit text (content of a post)")
    parser.add_argument("--audit-post", metavar="POST_ID",
                        help="Fetch post by ID and audit its content")
    parser.add_argument("--mb_limit", type=int, default=10,
                        help="Number of posts to show (default: 10)")
    parser.add_argument("--export", metavar="FILE", nargs="?", const="",
                        help="Export own comments + posts to Markdown (default: ~/fritzenergydict_activity.md)")

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
    elif args.audit_text:
        audit = audit_post(args.audit_text)
        print(f"Text: {args.audit_text!r}: Score: {audit}")
    elif args.export is not None:
        cmd_export_activity(args.export if args.export else None)
    elif args.audit_post:
        p = mb.get_post(args.audit_post)
        text = re.sub(r"<[^>]+>", "", p.get("content", "")).strip()
        title = p.get("title", "")
        spam_flag = "  ⚠ marked as spam" if p.get("is_spam") else ""
        audit = audit_post(f"{title}\n{text}" if title else text)
        print(f"Post {args.audit_post!r}:{spam_flag}")
        print(f"  Title:  {title}")
        print(f"  Score:  {audit.get('score')}/10")
        print(f"  Reason: {audit.get('reason')}")
    else:
        chat()
