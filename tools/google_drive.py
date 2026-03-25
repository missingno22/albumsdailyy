"""
Google Drive helper for video hosting.

Uploads reel videos to a shared Drive folder so they can be previewed
in the Google Sheets queue before posting to Instagram.

Usage:
    from tools.google_drive import DriveStorage
    drive = DriveStorage()
    url, file_id = drive.upload_video(".tmp/output/reel_final.mp4")
    drive.download_video(file_id, ".tmp/download/reel.mp4")
    drive.delete_video(file_id)
"""

import os
import io
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

# Reuse auth from google_sheets
from tools.google_sheets import get_credentials, _load_env, PROJECT_ROOT


class DriveStorage:
    """Manages video uploads/downloads to Google Drive."""

    def __init__(self, folder_id=None):
        env = _load_env()
        self.folder_id = (
            folder_id
            or os.environ.get("GOOGLE_DRIVE_QUEUE_FOLDER_ID")
            or env.get("GOOGLE_DRIVE_QUEUE_FOLDER_ID")
        )
        if not self.folder_id:
            raise ValueError("GOOGLE_DRIVE_QUEUE_FOLDER_ID not set in .env or environment")

        creds = get_credentials()
        self.service = build("drive", "v3", credentials=creds)

    def upload_video(self, file_path, name=None):
        """Upload a video to the queue folder. Returns (shareable_url, file_id)."""
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Video not found: {file_path}")

        filename = name or os.path.basename(file_path)
        file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
        print(f"  Uploading {filename} ({file_size_mb:.1f}MB) to Drive...")

        file_metadata = {
            "name": filename,
            "parents": [self.folder_id],
        }
        media = MediaFileUpload(file_path, mimetype="video/mp4", resumable=True)

        file = self.service.files().create(
            body=file_metadata, media_body=media, fields="id,webViewLink"
        ).execute()

        file_id = file["id"]

        # Make the file viewable by anyone with the link
        self.service.permissions().create(
            fileId=file_id,
            body={"type": "anyone", "role": "reader"},
        ).execute()

        web_link = file.get("webViewLink", f"https://drive.google.com/file/d/{file_id}/view")
        print(f"  -> {web_link}")
        return web_link, file_id

    def download_video(self, file_id, dest_path):
        """Download a video from Drive to a local path."""
        print(f"  Downloading {file_id} from Drive...")
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)

        request = self.service.files().get_media(fileId=file_id)
        with open(dest_path, "wb") as f:
            downloader = MediaIoBaseDownload(f, request)
            done = False
            while not done:
                status, done = downloader.next_chunk()
                if status:
                    print(f"  Download: {int(status.progress() * 100)}%")

        file_size_mb = os.path.getsize(dest_path) / (1024 * 1024)
        print(f"  Saved: {dest_path} ({file_size_mb:.1f}MB)")
        return dest_path

    def delete_video(self, file_id):
        """Delete a video from Drive (cleanup after posting)."""
        try:
            self.service.files().delete(fileId=file_id).execute()
            print(f"  Deleted Drive file: {file_id}")
        except Exception as e:
            print(f"  Warning: Could not delete Drive file {file_id}: {e}")


if __name__ == "__main__":
    # Quick test: list files in the queue folder
    drive = DriveStorage()
    results = drive.service.files().list(
        q=f"'{drive.folder_id}' in parents",
        fields="files(id, name, size)",
    ).execute()
    files = results.get("files", [])
    print(f"\nDrive queue folder has {len(files)} files:")
    for f in files:
        size_mb = int(f.get("size", 0)) / (1024 * 1024)
        print(f"  {f['name']} ({size_mb:.1f}MB) — {f['id']}")
