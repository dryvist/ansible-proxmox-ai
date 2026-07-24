#!/usr/bin/env python3
"""Operator Kanban board — a thin HTTP face over the `hermes kanban` CLI.

The Hermes task store (kanban.db) stays authoritative. This server owns no
state of its own: every mutation shells out to the same CLI verbs a worker
uses, so the agent observes each change on its own event stream. Nothing here
adds, reinterprets, or caches card semantics.

Configuration arrives from the systemd unit's environment:
  HERMES_BIN         path to the hermes executable
  KANBAN_BOARD_HOST  bind address
  KANBAN_BOARD_PORT  bind port

Authentication is Traefik's Authelia forwardAuth. Mutations require the
Remote-User header: Traefik strips that header from every inbound request and
Authelia re-adds it only after a successful login on an SSO-gated route, so a
caller reaching this port directly cannot forge an identity into a mutation.
Reads are left open — they expose no more than the CLI does to the same guest.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERMES_BIN = os.environ.get("HERMES_BIN", "/usr/local/bin/hermes")
HOST = os.environ.get("KANBAN_BOARD_HOST", "127.0.0.1")
PORT = int(os.environ.get("KANBAN_BOARD_PORT", "8646"))

# Board columns, left to right. Mirrors the `hermes kanban list --status`
# choices minus `archived`, which the board deliberately hides.
COLUMNS = (
    "triage",
    "todo",
    "ready",
    "running",
    "review",
    "blocked",
    "scheduled",
    "done",
)

# Task ids are CLI-assigned and look like `t_aa79b03e`. Validated so a caller
# cannot smuggle an option-looking argument into the argv.
TASK_ID = re.compile(r"\At_[0-9A-Za-z_-]{2,64}\Z")
MAX_TEXT = 4000
CLI_TIMEOUT = 60

# Allow-list: action -> argv tail for `hermes kanban <...>`. Anything absent
# here is rejected, so the board can never invoke a verb it was not designed
# for. Free text is passed as its own argv element, never through a shell.
ACTIONS = {
    "promote": lambda tid, text: ["promote", tid] + ([text] if text else []),
    "block": lambda tid, text: ["block", tid] + ([text] if text else []),
    "unblock": lambda tid, text: ["unblock", tid] + (["--reason", text] if text else []),
    "complete": lambda tid, text: ["complete", tid] + (["--result", text] if text else []),
    "archive": lambda tid, _text: ["archive", tid],
    "comment": lambda tid, text: ["comment", tid, text],
}
TEXT_REQUIRED = frozenset({"comment"})


def run_kanban(args):
    """Run `hermes kanban <args>`; return (ok, combined_output)."""
    try:
        proc = subprocess.run(  # noqa: S603 - argv list, never a shell
            [HERMES_BIN, "kanban", *args],
            capture_output=True,
            text=True,
            timeout=CLI_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, "hermes kanban timed out"
    except OSError as exc:
        return False, f"could not run hermes: {exc}"
    output = ((proc.stdout or "") + (proc.stderr or "")).strip()
    return proc.returncode == 0, output


def build_argv(payload):
    """Validate a mutation request; return (argv, error).

    Exactly one of the two is None. Kept separate from the HTTP layer so the
    contract test can exercise it directly.
    """
    action = payload.get("action")
    text = (payload.get("text") or "").strip()
    if len(text) > MAX_TEXT:
        return None, f"text exceeds {MAX_TEXT} characters"

    if action == "create":
        title = (payload.get("title") or "").strip()
        if not title:
            return None, "create requires a title"
        if len(title) > MAX_TEXT:
            return None, f"title exceeds {MAX_TEXT} characters"
        return ["create", title] + (["--body", text] if text else []), None

    if action not in ACTIONS:
        return None, f"unknown action: {action!r}"

    task_id = (payload.get("id") or "").strip()
    if not TASK_ID.fullmatch(task_id):
        return None, f"invalid task id: {task_id!r}"
    if action in TEXT_REQUIRED and not text:
        return None, f"{action} requires text"

    return ACTIONS[action](task_id, text), None


class Handler(BaseHTTPRequestHandler):
    server_version = "hermes-kanban-board"

    def _send(self, code, body, content_type):
        payload = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _send_json(self, code, obj):
        self._send(code, json.dumps(obj), "application/json; charset=utf-8")

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler API
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if path == "/":
            self._send(200, PAGE, "text/html; charset=utf-8")
        elif path == "/api/tasks":
            ok, output = run_kanban(["list", "--json"])
            if not ok:
                self._send_json(502, {"error": output or "hermes kanban list failed"})
                return
            try:
                tasks = json.loads(output or "[]")
            except json.JSONDecodeError:
                self._send_json(502, {"error": "hermes kanban list returned non-JSON"})
                return
            self._send_json(200, {"columns": list(COLUMNS), "tasks": tasks})
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path.split("?", 1)[0].rstrip("/") != "/api/act":
            self._send_json(404, {"error": "not found"})
            return

        # Fail closed for anything that did not come through the SSO gate.
        actor = (self.headers.get("Remote-User") or "").strip()
        if not actor:
            self._send_json(403, {"error": "no authenticated user; reach the board via the SSO ingress"})
            return

        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._send_json(400, {"error": "bad Content-Length"})
            return
        if length <= 0 or length > 64 * 1024:
            self._send_json(400, {"error": "empty or oversized body"})
            return

        try:
            payload = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._send_json(400, {"error": "body is not valid JSON"})
            return
        if not isinstance(payload, dict):
            self._send_json(400, {"error": "body must be a JSON object"})
            return

        argv, error = build_argv(payload)
        if error:
            self._send_json(400, {"error": error})
            return

        # Attribute the change to the logged-in operator wherever the verb
        # carries an author, so the agent's event stream shows who acted.
        if argv[0] == "comment":
            argv += ["--author", actor]

        ok, output = run_kanban(argv)
        self._send_json(200 if ok else 502, {"ok": ok, "output": output})

    def log_message(self, fmt, *args):
        # One line per request on stdout; journald captures it.
        print("%s %s" % (self.address_string(), fmt % args), flush=True)  # noqa: T201


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Hermes Kanban</title>
<style>
:root { color-scheme: light dark; --bg:#f6f7f9; --fg:#12151a; --card:#fff; --line:#d7dbe0; --muted:#5b6572; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#14171c; --fg:#e8ebef; --card:#1e232a; --line:#333a44; --muted:#98a2b0; }
}
* { box-sizing: border-box; }
body { margin:0; font:14px/1.45 system-ui,-apple-system,Segoe UI,sans-serif; background:var(--bg); color:var(--fg); }
header { display:flex; gap:8px; align-items:center; flex-wrap:wrap; padding:12px 16px; border-bottom:1px solid var(--line); position:sticky; top:0; background:var(--bg); z-index:2; }
h1 { font-size:16px; margin:0 12px 0 0; }
input, textarea, button { font:inherit; color:inherit; background:var(--card); border:1px solid var(--line); border-radius:6px; padding:6px 9px; }
button { cursor:pointer; }
button:hover:not(:disabled) { border-color:var(--muted); }
button:disabled { opacity:.5; cursor:default; }
#new-title { flex:1 1 260px; min-width:200px; }
#status { color:var(--muted); flex-basis:100%; min-height:1.2em; }
#board { display:flex; gap:12px; padding:16px; overflow-x:auto; align-items:flex-start; }
.col { flex:0 0 290px; background:var(--card); border:1px solid var(--line); border-radius:8px; }
.col > h2 { font-size:12px; text-transform:uppercase; letter-spacing:.06em; color:var(--muted); margin:0; padding:10px 12px; border-bottom:1px solid var(--line); }
.cards { padding:8px; display:flex; flex-direction:column; gap:8px; }
.card { border:1px solid var(--line); border-radius:6px; padding:8px 9px; background:var(--bg); }
.card h3 { font-size:13px; font-weight:600; margin:0 0 5px; overflow-wrap:anywhere; }
.meta { color:var(--muted); font-size:11px; margin-bottom:6px; }
.acts { display:flex; flex-wrap:wrap; gap:4px; }
.acts button { padding:2px 7px; font-size:11px; border-radius:4px; }
.empty { color:var(--muted); padding:10px 12px; font-size:12px; }
</style>
</head>
<body>
<header>
  <h1>Hermes Kanban</h1>
  <input id="new-title" placeholder="New card title" aria-label="New card title">
  <button id="create">Add card</button>
  <button id="refresh">Refresh</button>
  <span id="status" role="status"></span>
</header>
<div id="board"></div>
<script>
const COLUMN_ACTIONS = {
  triage:    ["promote", "block", "archive"],
  todo:      ["promote", "block", "archive"],
  ready:     ["block", "complete", "archive"],
  running:   ["block", "complete"],
  review:    ["complete", "block", "archive"],
  blocked:   ["unblock", "promote", "archive"],
  scheduled: ["unblock", "archive"],
  done:      ["archive"]
};
const PROMPTS = {
  block: "Reason for blocking (optional)",
  unblock: "Note (optional)",
  complete: "Result summary (optional)",
  comment: "Comment"
};
const board = document.getElementById("board");
const statusEl = document.getElementById("status");
let busy = false;

function say(msg, isError) {
  statusEl.textContent = msg;
  statusEl.style.color = isError ? "#c0392b" : "";
}

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"]/g, c => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]
  ));
}

async function load() {
  try {
    const res = await fetch("api/tasks", { headers: { Accept: "application/json" } });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || res.statusText);
    render(data);
    say("Updated " + new Date().toLocaleTimeString());
  } catch (err) {
    say("Could not load board: " + err.message, true);
  }
}

function render(data) {
  const groups = {};
  for (const col of data.columns) groups[col] = [];
  for (const task of data.tasks) {
    if (groups[task.status]) groups[task.status].push(task);
  }
  board.innerHTML = "";
  for (const col of data.columns) {
    const items = groups[col];
    const el = document.createElement("section");
    el.className = "col";
    el.innerHTML = "<h2>" + esc(col) + " (" + items.length + ")</h2>" +
      (items.length ? '<div class="cards"></div>' : '<p class="empty">Nothing here.</p>');
    const holder = el.querySelector(".cards");
    for (const task of items) holder.appendChild(cardEl(task, col));
    board.appendChild(el);
  }
}

function cardEl(task, col) {
  const el = document.createElement("article");
  el.className = "card";
  const meta = [task.id, task.assignee].filter(Boolean).join(" \\u00b7 ");
  el.innerHTML = "<h3>" + esc(task.title) + "</h3>" +
    '<p class="meta">' + esc(meta) + "</p>" +
    '<div class="acts"></div>';
  const acts = el.querySelector(".acts");
  for (const action of (COLUMN_ACTIONS[col] || []).concat("comment")) {
    const btn = document.createElement("button");
    btn.textContent = action;
    btn.onclick = () => act(action, task.id);
    acts.appendChild(btn);
  }
  return el;
}

function setBusy(state) {
  busy = state;
  for (const b of document.querySelectorAll("button")) b.disabled = state;
}

async function post(body) {
  if (busy) return null;
  setBusy(true);
  say("Working...");
  try {
    const res = await fetch("api/act", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    });
    const data = await res.json();
    if (!res.ok || data.ok === false) throw new Error(data.error || data.output || res.statusText);
    return data;
  } catch (err) {
    say(err.message, true);
    return null;
  } finally {
    setBusy(false);
  }
}

async function act(action, id) {
  let text = "";
  if (PROMPTS[action]) {
    const answer = prompt(PROMPTS[action]);
    if (answer === null) return;
    text = answer.trim();
    if (action === "comment" && !text) return;
  }
  if (action === "archive" && !confirm("Archive this card?")) return;
  if (await post({ action, id, text })) await load();
}

document.getElementById("refresh").onclick = load;
document.getElementById("create").onclick = async () => {
  const input = document.getElementById("new-title");
  const title = input.value.trim();
  if (!title) { say("Enter a title first.", true); return; }
  if (await post({ action: "create", title })) { input.value = ""; await load(); }
};
document.getElementById("new-title").addEventListener("keydown", e => {
  if (e.key === "Enter") document.getElementById("create").click();
});

load();
setInterval(() => { if (!busy) load(); }, 30000);
</script>
</body>
</html>
"""


def main():
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"kanban board listening on {HOST}:{PORT}", flush=True)  # noqa: T201
    server.serve_forever()


if __name__ == "__main__":
    main()
