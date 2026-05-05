"""
Flask web application for the Reel Scheduler.

Provides a multi-account web UI for uploading, previewing, scheduling,
approving, and rejecting Instagram Reels, plus API endpoints for n8n.
"""

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime

from flask import (
    Flask, flash, jsonify, redirect, render_template,
    request, send_from_directory, url_for,
)
from werkzeug.utils import secure_filename

_HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(_HERE))   # Music Reel root
sys.path.insert(0, os.path.join(PROJECT_ROOT, "tools"))

from flask_app.models import (
    init_db,
    list_accounts, get_account, insert_account, update_account, delete_account,
    get_all_queue, get_queue_entry, get_pending_count,
    insert_queue_entry, update_status, update_entry,
    delete_queue_entry,
    get_automation, upsert_automation, delete_automation,
    list_source_queue, delete_source_queue_entry, get_source_queue_entry,
)

import subprocess

def _run_curl_upload(name, curl_args, timeout=300):
    """Run a curl upload. Returns stdout on success (rc=0 and non-empty), else raises."""
    result = subprocess.run(curl_args, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(f"{name} curl failed (rc={result.returncode}): {result.stderr[:200]}")
    body = (result.stdout or "").strip()
    if not body:
        raise RuntimeError(f"{name} returned empty response")
    return body


def _upload_catbox(file_path):
    body = _run_curl_upload("catbox", [
        "curl", "-s", "--max-time", "300",
        "-F", "reqtype=fileupload",
        "-F", f"fileToUpload=@{file_path}",
        "https://catbox.moe/user/api.php",
    ])
    if not body.startswith("http"):
        raise RuntimeError(f"catbox non-URL response: {body[:200]}")
    return body


def _upload_litterbox(file_path):
    # Catbox-operated temp host. Separate service; usually up when main catbox is paused.
    # 72h expiry is fine — Instagram fetches within minutes of the webhook firing.
    body = _run_curl_upload("litterbox", [
        "curl", "-s", "--max-time", "300",
        "-F", "reqtype=fileupload",
        "-F", "time=72h",
        "-F", f"fileToUpload=@{file_path}",
        "https://litterbox.catbox.moe/resources/internals/api.php",
    ])
    if not body.startswith("http"):
        raise RuntimeError(f"litterbox non-URL response: {body[:200]}")
    return body


def _upload_0x0(file_path):
    body = _run_curl_upload("0x0.st", [
        "curl", "-s", "--max-time", "300",
        "-F", f"file=@{file_path}",
        "-H", "User-Agent: reel-scheduler/1.0",
        "https://0x0.st",
    ])
    if not body.startswith("http"):
        raise RuntimeError(f"0x0.st non-URL response: {body[:200]}")
    return body


def _upload_uguu(file_path):
    body = _run_curl_upload("uguu.se", [
        "curl", "-s", "--max-time", "300",
        "-F", f"files[]=@{file_path}",
        "https://uguu.se/upload.php",
    ])
    try:
        data = json.loads(body)
        url = data["files"][0]["url"]
    except Exception:
        raise RuntimeError(f"uguu.se bad JSON: {body[:200]}")
    if not url.startswith("http"):
        raise RuntimeError(f"uguu.se non-URL: {url}")
    return url


# Order matters: catbox first (longest retention), then fallbacks in reliability order.
_UPLOAD_PROVIDERS = [
    ("catbox.moe", _upload_catbox),
    ("litterbox.catbox.moe", _upload_litterbox),
    ("0x0.st", _upload_0x0),
    ("uguu.se", _upload_uguu),
]


def upload_to_catbox(file_path):
    """
    Upload a video to a public anon host and return a direct URL.

    Tries catbox.moe first (longest retention), then falls back through
    litterbox, 0x0.st, and uguu.se. First success wins.

    Name kept as `upload_to_catbox` for callsite stability — the function is
    now provider-agnostic.
    """
    filename = os.path.basename(file_path)
    errors = []
    for name, fn in _UPLOAD_PROVIDERS:
        print(f"  Uploading {filename} to {name}...", flush=True)
        try:
            url = fn(file_path)
            print(f"  Uploaded via {name}: {url}", flush=True)
            return url
        except Exception as e:
            msg = str(e)[:200]
            print(f"  [{name}] failed: {msg}", flush=True)
            errors.append(f"{name}: {msg}")
    raise RuntimeError(
        "All upload providers failed:\n  - " + "\n  - ".join(errors)
    )

UPLOADS_DIR = os.path.join(PROJECT_ROOT, "data", "uploads")
ALLOWED_EXTENSIONS = {".mp4", ".mov"}
MAX_UPLOAD_BYTES = 500 * 1024 * 1024  # 500 MB


def load_env():
    """Load .env file from project root."""
    env_path = os.path.join(PROJECT_ROOT, ".env")
    env_vars = {}
    if os.path.exists(env_path):
        with open(env_path, "r") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    env_vars[key.strip()] = value.strip()
    return env_vars


def send_n8n_webhook(video_url, caption, scheduled_datetime, queue_id,
                     instagram_user_id, instagram_access_token, callback_url):
    """Send approved post data to n8n webhook."""
    env = load_env()
    webhook_url = env.get("N8N_WEBHOOK_URL")
    if not webhook_url:
        print("  Warning: N8N_WEBHOOK_URL not set in .env — skipping webhook", flush=True)
        return False

    payload = json.dumps({
        "video_url": video_url,
        "caption": caption,
        "scheduled_datetime": scheduled_datetime,
        "queue_id": queue_id,
        "callback_url": callback_url,
        "instagram_user_id": instagram_user_id,
        "instagram_access_token": instagram_access_token,
    }).encode("utf-8")

    req = urllib.request.Request(
        webhook_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            print(f"  Webhook sent: {resp.status}", flush=True)
            return True
    except urllib.error.URLError as e:
        print(f"  Webhook failed: {e}", flush=True)
        return False


def allowed_file(filename):
    ext = os.path.splitext(filename)[1].lower()
    return ext in ALLOWED_EXTENSIONS


def create_app():
    """Create and configure the Flask application."""
    app = Flask(
        __name__,
        template_folder=os.path.join(_HERE, "templates"),
        static_folder=os.path.join(_HERE, "static"),
    )
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES
    app.secret_key = load_env().get("FLASK_SECRET_KEY", "dev-secret-change-me")

    os.makedirs(UPLOADS_DIR, exist_ok=True)

    with app.app_context():
        init_db()

    # ------------------------------------------------------------------ #
    #  Landing page — account selector
    # ------------------------------------------------------------------ #

    @app.route("/")
    def index():
        accounts = list_accounts()
        return render_template("accounts.html", accounts=accounts)

    # ------------------------------------------------------------------ #
    #  Account management
    # ------------------------------------------------------------------ #

    @app.route("/accounts/new", methods=["GET", "POST"])
    def account_new():
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            display_name = request.form.get("display_name", "").strip()
            user_id = request.form.get("instagram_user_id", "").strip()
            token = request.form.get("access_token", "").strip()

            if not name or not user_id or not token:
                flash("Handle, User ID, and Access Token are required.")
                return render_template("account_form.html", account=None, action="new")

            try:
                insert_account(name, display_name, user_id, token)
                flash(f"Account @{name.lstrip('@')} added.")
                return redirect(url_for("index"))
            except Exception as e:
                flash(f"Error: {e}")

        return render_template("account_form.html", account=None, action="new")

    @app.route("/accounts/<int:account_id>/edit", methods=["GET", "POST"])
    def account_edit(account_id):
        account = get_account(account_id)
        if not account:
            flash("Account not found.")
            return redirect(url_for("index"))

        if request.method == "POST":
            name = request.form.get("name", "").strip()
            display_name = request.form.get("display_name", "").strip()
            user_id = request.form.get("instagram_user_id", "").strip()
            token = request.form.get("access_token", "").strip()

            if not name or not user_id or not token:
                flash("Handle, User ID, and Access Token are required.")
                return render_template("account_form.html", account=account, action="edit")

            try:
                update_account(account_id, name, display_name, user_id, token)
                flash(f"Account @{name.lstrip('@')} updated.")
                return redirect(url_for("index"))
            except Exception as e:
                flash(f"Error: {e}")

        return render_template("account_form.html", account=account, action="edit")

    @app.route("/accounts/<int:account_id>/delete", methods=["POST"])
    def account_delete(account_id):
        account = get_account(account_id)
        if account:
            delete_account(account_id)
            flash(f"Account @{account['name']} deleted.")
        return redirect(url_for("index"))

    # ------------------------------------------------------------------ #
    #  Per-account dashboard
    # ------------------------------------------------------------------ #

    @app.route("/accounts/<int:account_id>")
    def dashboard(account_id):
        from flask import session
        account = get_account(account_id)
        if not account:
            flash("Account not found.")
            return redirect(url_for("index"))
        entries = get_all_queue(account_id)
        counts = get_pending_count(account_id)
        automation = get_automation(account_id)
        fill_log = session.pop("fill_queue_log", None)
        source_entries = []
        source_available = False
        if automation and automation.get("source_db_path"):
            source_available = os.path.exists(automation["source_db_path"])
            if source_available:
                source_entries = list_source_queue(automation["source_db_path"])
        return render_template("dashboard.html", account=account, entries=entries,
                               counts=counts, automation=automation, fill_log=fill_log,
                               source_entries=source_entries, source_available=source_available)

    # ------------------------------------------------------------------ #
    #  Upload
    # ------------------------------------------------------------------ #

    @app.route("/accounts/<int:account_id>/upload", methods=["POST"])
    def upload(account_id):
        account = get_account(account_id)
        if not account:
            flash("Account not found.")
            return redirect(url_for("index"))

        file = request.files.get("video")
        title = request.form.get("title", "").strip()
        caption = request.form.get("caption", "").strip()
        scheduled_datetime = request.form.get("scheduled_datetime", "").strip()

        if not file or not file.filename:
            flash("No file selected.")
            return redirect(url_for("dashboard", account_id=account_id))

        if not allowed_file(file.filename):
            flash("Only .mp4 and .mov files are accepted.")
            return redirect(url_for("dashboard", account_id=account_id))

        if not title:
            flash("Title is required.")
            return redirect(url_for("dashboard", account_id=account_id))

        if not scheduled_datetime:
            flash("Scheduled date and time is required.")
            return redirect(url_for("dashboard", account_id=account_id))

        # Build a timestamped filename to avoid collisions
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_name = secure_filename(file.filename)
        video_filename = f"{ts}_{safe_name}"
        video_path = os.path.join(UPLOADS_DIR, video_filename)

        file.save(video_path)
        print(f"  Saved upload: {video_path}", flush=True)

        insert_queue_entry(
            account_id=account_id,
            title=title,
            video_path=video_path,
            video_filename=video_filename,
            caption=caption,
            scheduled_datetime=scheduled_datetime,
        )

        flash(f"'{title}' uploaded and queued for {scheduled_datetime}.")
        return redirect(url_for("dashboard", account_id=account_id))

    # ------------------------------------------------------------------ #
    #  Preview
    # ------------------------------------------------------------------ #

    @app.route("/preview/<int:entry_id>")
    def preview(entry_id):
        entry = get_queue_entry(entry_id)
        if not entry:
            flash("Entry not found.")
            return redirect(url_for("index"))
        return render_template("preview.html", entry=entry)

    # ------------------------------------------------------------------ #
    #  Edit
    # ------------------------------------------------------------------ #

    @app.route("/edit/<int:entry_id>", methods=["POST"])
    def edit(entry_id):
        entry = get_queue_entry(entry_id)
        if not entry:
            flash("Entry not found.")
            return redirect(url_for("index"))

        caption = request.form.get("caption", "")
        scheduled_datetime = request.form.get("scheduled_datetime", "").strip()

        if not scheduled_datetime:
            flash("Scheduled date and time cannot be empty.")
            return redirect(url_for("preview", entry_id=entry_id))

        update_entry(entry_id, caption, scheduled_datetime)
        flash("Saved.")
        return redirect(url_for("preview", entry_id=entry_id))

    # ------------------------------------------------------------------ #
    #  Approve
    # ------------------------------------------------------------------ #

    @app.route("/approve/<int:entry_id>", methods=["POST"])
    def approve(entry_id):
        entry = get_queue_entry(entry_id)
        if not entry:
            flash("Entry not found.")
            return redirect(url_for("index"))

        if entry["status"] not in ("pending", "rejected"):
            flash(f"Cannot approve an entry with status '{entry['status']}'.")
            return redirect(url_for("dashboard", account_id=entry["account_id"]))

        video_path = entry["video_path"]
        if not os.path.exists(video_path):
            flash(f"Video file missing: {video_path}")
            return redirect(url_for("preview", entry_id=entry_id))

        try:
            catbox_url = upload_to_catbox(video_path)
            update_status(entry_id, "approved",
                          drive_public_url=catbox_url)

            callback_url = url_for("api_post_callback", _external=True)
            webhook_sent = send_n8n_webhook(
                video_url=catbox_url,
                caption=entry["caption"],
                scheduled_datetime=entry["scheduled_datetime"],
                queue_id=entry_id,
                instagram_user_id=entry["instagram_user_id"],
                instagram_access_token=entry["access_token"],
                callback_url=callback_url,
            )

            if webhook_sent:
                flash(f"Approved — scheduled for {entry['scheduled_datetime']}.")
            else:
                flash("Approved and uploaded to catbox. n8n webhook failed — check N8N_WEBHOOK_URL in .env.")

        except Exception as e:
            flash(f"Approval failed: {e}")
            return redirect(url_for("preview", entry_id=entry_id))

        return redirect(url_for("dashboard", account_id=entry["account_id"]))

    # ------------------------------------------------------------------ #
    #  Reject
    # ------------------------------------------------------------------ #

    @app.route("/reject/<int:entry_id>", methods=["POST"])
    def reject(entry_id):
        entry = get_queue_entry(entry_id)
        if not entry:
            flash("Entry not found.")
            return redirect(url_for("index"))

        update_status(entry_id, "rejected")
        flash(f"Rejected: {entry['title']}")
        return redirect(url_for("dashboard", account_id=entry["account_id"]))

    # ------------------------------------------------------------------ #
    #  Delete
    # ------------------------------------------------------------------ #

    @app.route("/delete/<int:entry_id>", methods=["POST"])
    def delete_entry(entry_id):
        entry = get_queue_entry(entry_id)
        if not entry:
            flash("Entry not found.")
            return redirect(url_for("index"))

        if entry["status"] == "posted":
            flash("Cannot delete a posted entry.")
            return redirect(url_for("preview", entry_id=entry_id))

        account_id = entry["account_id"]
        deleted = delete_queue_entry(entry_id)

        if deleted:
            # Remove local file
            if deleted.get("video_path") and os.path.exists(deleted["video_path"]):
                os.remove(deleted["video_path"])
                print(f"  Deleted local file: {deleted['video_path']}", flush=True)
            # catbox files expire on their own, no cleanup needed

        flash(f"Deleted: {entry['title']}")
        return redirect(url_for("dashboard", account_id=account_id))

    # ------------------------------------------------------------------ #
    #  Serve local video for HTML5 player
    # ------------------------------------------------------------------ #

    @app.route("/video/<int:entry_id>")
    def serve_video(entry_id):
        entry = get_queue_entry(entry_id)
        if not entry:
            return "Not found", 404
        video_path = entry["video_path"]
        if os.path.isabs(video_path) and os.path.exists(video_path):
            return send_from_directory(os.path.dirname(video_path), os.path.basename(video_path))
        return send_from_directory(UPLOADS_DIR, entry["video_filename"])

    # ------------------------------------------------------------------ #
    #  Automation config
    # ------------------------------------------------------------------ #

    @app.route("/accounts/<int:account_id>/automation", methods=["GET", "POST"])
    def automation_config(account_id):
        account = get_account(account_id)
        if not account:
            flash("Account not found.")
            return redirect(url_for("index"))

        automation = get_automation(account_id)

        if request.method == "POST":
            name = request.form.get("name", "").strip()
            script_command = request.form.get("script_command", "").strip()
            working_directory = request.form.get("working_directory", "").strip()
            source_db_path = request.form.get("source_db_path", "").strip() or None

            if not name or not script_command or not working_directory:
                flash("Name, command, and working directory are required.")
                return render_template("automation_form.html", account=account, automation=automation)

            if not os.path.isdir(working_directory):
                flash(f"Working directory does not exist: {working_directory}")
                return render_template("automation_form.html", account=account, automation=automation)

            if source_db_path and not os.path.exists(source_db_path):
                flash(f"Warning: source db not found yet at {source_db_path} (saved anyway)")

            upsert_automation(account_id, name, script_command, working_directory,
                              source_db_path=source_db_path)
            flash(f"Automation '{name}' saved.")
            return redirect(url_for("dashboard", account_id=account_id))

        return render_template("automation_form.html", account=account, automation=automation)

    @app.route("/accounts/<int:account_id>/automation/delete", methods=["POST"])
    def automation_delete(account_id):
        delete_automation(account_id)
        flash("Automation removed.")
        return redirect(url_for("dashboard", account_id=account_id))

    # ------------------------------------------------------------------ #
    #  Fill Queue (run automation script)
    # ------------------------------------------------------------------ #

    @app.route("/accounts/<int:account_id>/fill-queue", methods=["POST"])
    def fill_queue(account_id):
        import time as _time
        from flask import session

        account = get_account(account_id)
        automation = get_automation(account_id)
        if not account or not automation:
            flash("Account or automation not found.")
            return redirect(url_for("index"))

        start = _time.time()
        output_lines = []
        added = 0

        try:
            process = subprocess.Popen(
                automation["script_command"],
                cwd=automation["working_directory"],
                shell=True,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
                encoding="utf-8", errors="replace",
                env={**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"},
            )

            for line in process.stdout:
                line = line.rstrip("\n")
                print(f"[fill-queue] {line}", flush=True)
                output_lines.append(line)

                # Try to parse as JSON queue entry
                try:
                    entry_data = json.loads(line)
                    if "title" in entry_data and "video_path" in entry_data:
                        video_path = entry_data["video_path"]
                        video_filename = os.path.basename(video_path)
                        insert_queue_entry(
                            account_id=account_id,
                            title=entry_data["title"],
                            video_path=video_path,
                            video_filename=video_filename,
                            caption=entry_data.get("caption", ""),
                            scheduled_datetime=entry_data.get("scheduled_datetime", ""),
                        )
                        added += 1
                except (json.JSONDecodeError, ValueError):
                    pass  # Not JSON — it's log output, already captured

            process.wait(timeout=900)
            elapsed = _time.time() - start

            if process.returncode == 0:
                flash(f"Fill queue complete: added {added} entries ({elapsed:.1f}s)")
            else:
                flash(f"Fill queue failed (exit code {process.returncode}, {elapsed:.1f}s)")

            if output_lines:
                session["fill_queue_log"] = "\n".join(output_lines)

        except subprocess.TimeoutExpired:
            process.kill()
            flash("Fill queue timed out (>15 min)")
        except Exception as e:
            flash(f"Error running fill queue: {e}")

        return redirect(url_for("dashboard", account_id=account_id))

    # ------------------------------------------------------------------ #
    #  Source queue (external project db) management
    # ------------------------------------------------------------------ #

    @app.route("/accounts/<int:account_id>/source-queue/<int:entry_id>/delete", methods=["POST"])
    def source_queue_delete(account_id, entry_id):
        automation = get_automation(account_id)
        if not automation or not automation.get("source_db_path"):
            flash("No source db configured for this automation.")
            return redirect(url_for("dashboard", account_id=account_id))

        src = automation["source_db_path"]
        if not os.path.exists(src):
            flash(f"Source db not found: {src}")
            return redirect(url_for("dashboard", account_id=account_id))

        row = get_source_queue_entry(src, entry_id)
        if not row:
            flash("Source entry not found.")
            return redirect(url_for("dashboard", account_id=account_id))

        ok = delete_source_queue_entry(src, entry_id)
        label = row.get("album_name") or row.get("title") or f"#{entry_id}"
        if ok:
            flash(f"Removed from source queue: {label}")
        else:
            flash(f"Could not remove source entry: {label}")
        return redirect(url_for("dashboard", account_id=account_id))

    # ------------------------------------------------------------------ #
    #  n8n callback
    # ------------------------------------------------------------------ #

    @app.route("/api/post-callback", methods=["POST"])
    def api_post_callback():
        data = request.get_json()
        if not data:
            return jsonify({"error": "No JSON body"}), 400

        queue_id = data.get("queue_id")
        media_id = data.get("media_id")
        status = data.get("status", "posted")
        error = data.get("error_message")

        if not queue_id:
            return jsonify({"error": "Missing queue_id"}), 400

        kwargs = {}
        if media_id:
            kwargs["instagram_media_id"] = media_id
        if error:
            kwargs["error_message"] = error

        update_status(queue_id, status, **kwargs)
        return jsonify({"ok": True})

    return app
