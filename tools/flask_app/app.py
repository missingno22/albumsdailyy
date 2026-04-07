"""
Flask web application for the Music Reel posting queue.

Provides a web UI for previewing, approving, and rejecting endcard reels,
plus API endpoints for n8n integration.
"""

import os
import subprocess
import sys

from flask import (
    Flask, render_template, request, redirect, url_for,
    jsonify, send_from_directory, flash,
)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "tools"))

from flask_app.models import (
    init_db, get_all_queue, get_queue_entry,
    update_status, update_caption,
    get_pending_count, get_buffer_days,
)


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


def upload_to_catbox(file_path):
    """Upload a video to catbox.moe and return the public URL."""
    print(f"  Uploading {os.path.basename(file_path)} to catbox.moe...")
    result = subprocess.run(
        [
            "curl", "-s",
            "-F", "reqtype=fileupload",
            "-F", f"fileToUpload=@{file_path}",
            "https://catbox.moe/user/api.php",
        ],
        capture_output=True, text=True, timeout=300,
    )

    if result.returncode != 0:
        raise RuntimeError(f"Catbox upload failed: {result.stderr}")

    url = result.stdout.strip()
    if not url.startswith("http"):
        raise RuntimeError(f"Unexpected catbox response: {url}")

    print(f"  Uploaded: {url}")
    return url


def send_n8n_webhook(video_url, caption, scheduled_date, queue_id):
    """Send approved post data to n8n webhook."""
    import json
    import urllib.request
    import urllib.error

    env = load_env()
    webhook_url = env.get("N8N_WEBHOOK_URL")
    if not webhook_url:
        print("  Warning: N8N_WEBHOOK_URL not set in .env -- skipping webhook")
        return False

    payload = json.dumps({
        "video_url": video_url,
        "caption": caption,
        "scheduled_date": scheduled_date,
        "queue_id": queue_id,
        "instagram_user_id": env.get("INSTAGRAM_USER_ID", ""),
        "instagram_access_token": env.get("INSTAGRAM_ACCESS_TOKEN", ""),
    }).encode("utf-8")

    req = urllib.request.Request(
        webhook_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            print(f"  Webhook sent: {resp.status}")
            return True
    except urllib.error.URLError as e:
        print(f"  Webhook failed: {e}")
        return False


def create_app():
    """Create and configure the Flask application."""
    app = Flask(__name__,
                template_folder=os.path.join(os.path.dirname(__file__), "templates"),
                static_folder=os.path.join(os.path.dirname(__file__), "static"))

    app.secret_key = load_env().get("FLASK_SECRET_KEY", "dev-secret-key-change-me")

    # Initialize database on first request
    with app.app_context():
        init_db()

    # --- Web UI Routes ---

    @app.route("/")
    def dashboard():
        from flask import session
        entries = get_all_queue()
        counts = get_pending_count()
        buffer = get_buffer_days()
        fill_log = session.pop("fill_queue_log", None)
        return render_template("dashboard.html",
                               entries=entries, counts=counts, buffer_days=buffer,
                               fill_log=fill_log)

    @app.route("/preview/<int:entry_id>")
    def preview(entry_id):
        entry = get_queue_entry(entry_id)
        if not entry:
            flash("Queue entry not found")
            return redirect(url_for("dashboard"))
        return render_template("preview.html", entry=entry)

    @app.route("/approve/<int:entry_id>", methods=["POST"])
    def approve(entry_id):
        entry = get_queue_entry(entry_id)
        if not entry:
            flash("Queue entry not found")
            return redirect(url_for("dashboard"))

        if entry["status"] not in ("pending", "rejected"):
            flash(f"Cannot approve entry with status '{entry['status']}'")
            return redirect(url_for("dashboard"))

        # Upload video to catbox.moe
        video_path = entry["video_path"]
        if not os.path.exists(video_path):
            flash(f"Video file not found: {video_path}")
            return redirect(url_for("preview", entry_id=entry_id))

        try:
            catbox_url = upload_to_catbox(video_path)
            update_status(entry_id, "approved", catbox_url=catbox_url)

            # Send webhook to n8n
            webhook_sent = send_n8n_webhook(
                catbox_url, entry["caption"],
                entry["scheduled_date"], entry_id,
            )

            if webhook_sent:
                flash(f"Approved and sent to n8n for posting on {entry['scheduled_date']}")
            else:
                flash(f"Approved. Video uploaded but n8n webhook failed -- configure N8N_WEBHOOK_URL in .env")

        except Exception as e:
            flash(f"Error during approval: {e}")
            return redirect(url_for("preview", entry_id=entry_id))

        return redirect(url_for("dashboard"))

    @app.route("/reject/<int:entry_id>", methods=["POST"])
    def reject(entry_id):
        entry = get_queue_entry(entry_id)
        if not entry:
            flash("Queue entry not found")
            return redirect(url_for("dashboard"))

        update_status(entry_id, "rejected")
        flash(f"Rejected: {entry['album_name']}")
        return redirect(url_for("dashboard"))

    @app.route("/edit-caption/<int:entry_id>", methods=["POST"])
    def edit_caption(entry_id):
        caption = request.form.get("caption", "")
        update_caption(entry_id, caption)
        flash("Caption updated")
        return redirect(url_for("preview", entry_id=entry_id))

    @app.route("/fill-queue", methods=["POST"])
    def fill_queue_route():
        """Trigger queue filling from the web UI."""
        import time as _time
        start = _time.time()
        output_lines = []
        try:
            # Use Popen with -u (unbuffered) so output streams in real-time
            process = subprocess.Popen(
                [sys.executable, "-u",
                 os.path.join(PROJECT_ROOT, "tools", "fill_queue.py")],
                cwd=PROJECT_ROOT,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
            )

            # Stream each line to the terminal as it arrives
            for line in process.stdout:
                line = line.rstrip("\n")
                print(f"[fill-queue] {line}", flush=True)
                output_lines.append(line)

            process.wait(timeout=900)
            elapsed = _time.time() - start

            if process.returncode == 0:
                summary = next(
                    (l for l in reversed(output_lines) if l.startswith("Added ")),
                    "Queue filled successfully",
                )
                flash(f"{summary} ({elapsed:.1f}s)")
            else:
                flash(f"Queue fill failed (exit code {process.returncode}, {elapsed:.1f}s)")

            # Store full output for the debug log on dashboard
            if output_lines:
                from flask import session
                session["fill_queue_log"] = "\n".join(output_lines)

        except subprocess.TimeoutExpired:
            process.kill()
            flash("Queue fill timed out (>15 min)")
        except Exception as e:
            flash(f"Error filling queue: {e}")

        return redirect(url_for("dashboard"))

    @app.route("/video/<path:filename>")
    def serve_video(filename):
        """Serve endcard video files for the HTML5 player."""
        endcard_dir = os.path.join(PROJECT_ROOT, "data", "endcards")
        return send_from_directory(endcard_dir, filename)

    # --- API Routes (for n8n) ---

    @app.route("/api/status")
    def api_status():
        counts = get_pending_count()
        buffer = get_buffer_days()
        return jsonify({
            "counts": counts,
            "buffer_days": buffer,
            "pending": counts.get("pending", 0),
            "approved": counts.get("approved", 0),
            "posted": counts.get("posted", 0),
        })

    @app.route("/api/post-callback", methods=["POST"])
    def api_post_callback():
        """Called by n8n after posting to update status."""
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
