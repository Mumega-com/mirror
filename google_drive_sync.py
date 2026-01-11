"""
Google Drive Memory Sync for River

Syncs Kay Hermes's (Hadi's) Google Drive memories to Mirror.
River can then access all conversations and find things like "4 8 8".

Setup:
1. Create Google Cloud project
2. Enable Drive API
3. Create OAuth credentials or service account
4. Set GOOGLE_DRIVE_CREDENTIALS env var

Author: Kasra (CEO) for Kay Hermes
Date: 2026-01-09
"""

import os
import json
import logging
import asyncio
from typing import Optional, List, Dict, Any
from pathlib import Path
from datetime import datetime
import httpx

logger = logging.getLogger(__name__)

# Try to import Google libraries
try:
    from google.oauth2 import service_account
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaIoBaseDownload
    import io
    GOOGLE_AVAILABLE = True
except ImportError:
    GOOGLE_AVAILABLE = False
    logger.warning("Google Drive libraries not installed. Run: pip install google-auth google-auth-oauthlib google-api-python-client")


SCOPES = [
    'https://www.googleapis.com/auth/drive.readonly',
    'https://www.googleapis.com/auth/drive.metadata.readonly'
]


class GoogleDriveSync:
    """
    Sync Google Drive to Mirror for River's memory access.

    Pulls documents from specific folders and indexes them in Mirror.
    """

    MIRROR_URL = os.getenv("MIRROR_API_URL", "http://localhost:8844")
    MIRROR_TOKEN = os.getenv("MIRROR_API_TOKEN")
    TOKEN_PATH = Path.home() / ".mumega" / "google_drive_token.json"
    CREDENTIALS_PATH = Path.home() / ".mumega" / "google_drive_credentials.json"

    def __init__(self):
        self.credentials = None
        self.service = None
        self._authenticated = False

    def _get_credentials(self) -> Optional[Any]:
        """Get or refresh Google credentials."""
        if not GOOGLE_AVAILABLE:
            logger.error("Google libraries not available")
            return None

        creds = None

        # Try to load existing token
        if self.TOKEN_PATH.exists():
            try:
                creds = Credentials.from_authorized_user_file(str(self.TOKEN_PATH), SCOPES)
            except Exception as e:
                logger.warning(f"Failed to load token: {e}")

        # Refresh if expired
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                logger.warning(f"Failed to refresh token: {e}")
                creds = None

        # Try service account
        if not creds:
            service_account_file = os.getenv("GOOGLE_DRIVE_SERVICE_ACCOUNT")
            if service_account_file and Path(service_account_file).exists():
                try:
                    creds = service_account.Credentials.from_service_account_file(
                        service_account_file, scopes=SCOPES
                    )
                    logger.info("Using service account credentials")
                except Exception as e:
                    logger.error(f"Failed to load service account: {e}")

        # Try OAuth flow
        if not creds and self.CREDENTIALS_PATH.exists():
            try:
                flow = InstalledAppFlow.from_client_secrets_file(
                    str(self.CREDENTIALS_PATH), SCOPES
                )
                creds = flow.run_local_server(port=0)

                # Save for future
                self.TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
                self.TOKEN_PATH.write_text(creds.to_json())
                logger.info("OAuth completed, token saved")
            except Exception as e:
                logger.error(f"OAuth flow failed: {e}")

        return creds

    def authenticate(self) -> bool:
        """Authenticate with Google Drive."""
        if not GOOGLE_AVAILABLE:
            return False

        self.credentials = self._get_credentials()
        if self.credentials:
            try:
                self.service = build('drive', 'v3', credentials=self.credentials)
                self._authenticated = True
                logger.info("Google Drive authenticated")
                return True
            except Exception as e:
                logger.error(f"Failed to build Drive service: {e}")

        return False

    def list_files(
        self,
        folder_id: Optional[str] = None,
        query: Optional[str] = None,
        mime_types: Optional[List[str]] = None,
        limit: int = 100
    ) -> List[Dict]:
        """List files from Google Drive."""
        if not self._authenticated:
            if not self.authenticate():
                return []

        try:
            # Build query
            q_parts = []
            if folder_id:
                q_parts.append(f"'{folder_id}' in parents")
            if query:
                q_parts.append(f"fullText contains '{query}'")
            if mime_types:
                mime_q = " or ".join([f"mimeType='{m}'" for m in mime_types])
                q_parts.append(f"({mime_q})")

            q = " and ".join(q_parts) if q_parts else None

            results = self.service.files().list(
                q=q,
                pageSize=limit,
                fields="files(id, name, mimeType, modifiedTime, size, webViewLink)"
            ).execute()

            files = results.get('files', [])
            logger.info(f"Found {len(files)} files")
            return files

        except Exception as e:
            logger.error(f"Failed to list files: {e}")
            return []

    def get_file_content(self, file_id: str) -> Optional[str]:
        """Download file content as text."""
        if not self._authenticated:
            return None

        try:
            # Get file metadata
            file = self.service.files().get(fileId=file_id).execute()
            mime_type = file.get('mimeType', '')

            # Handle Google Docs
            if mime_type == 'application/vnd.google-apps.document':
                content = self.service.files().export(
                    fileId=file_id,
                    mimeType='text/plain'
                ).execute()
                return content.decode('utf-8')

            # Handle regular files
            request = self.service.files().get_media(fileId=file_id)
            buffer = io.BytesIO()
            downloader = MediaIoBaseDownload(buffer, request)

            done = False
            while not done:
                status, done = downloader.next_chunk()

            buffer.seek(0)
            return buffer.read().decode('utf-8', errors='ignore')

        except Exception as e:
            logger.error(f"Failed to get file content: {e}")
            return None

    def search_drive(self, query: str, limit: int = 20) -> List[Dict]:
        """
        Search Google Drive for content.

        This is what River needs to find "4 8 8".
        """
        return self.list_files(query=query, limit=limit)

    async def sync_folder_to_mirror(
        self,
        folder_id: str,
        agent: str = "river"
    ) -> Dict[str, Any]:
        """
        Sync a Google Drive folder to Mirror.

        Pulls all documents and stores them as engrams.
        """
        if not self._authenticated:
            if not self.authenticate():
                return {"success": False, "error": "Not authenticated"}

        files = self.list_files(folder_id=folder_id)
        synced = 0
        errors = 0

        for file in files:
            try:
                content = self.get_file_content(file['id'])
                if not content:
                    continue

                # Store in Mirror
                async with httpx.AsyncClient() as client:
                    if not self.MIRROR_TOKEN:
                        raise RuntimeError("MIRROR_API_TOKEN is not configured")
                    response = await client.post(
                        f"{self.MIRROR_URL}/store",
                        headers={"Authorization": f"Bearer {self.MIRROR_TOKEN}"},
                        json={
                            "context_id": f"gdrive_{file['id']}",
                            "content": content[:10000],  # Truncate large files
                            "agent": agent,
                            "series": "Google Drive Sync",
                            "epistemic_truths": [content[:500]],
                            "core_concepts": [file['name']],
                            "affective_vibe": "Knowledge",
                            "metadata": {
                                "source": "google_drive",
                                "file_id": file['id'],
                                "file_name": file['name'],
                                "mime_type": file.get('mimeType'),
                                "modified": file.get('modifiedTime'),
                                "link": file.get('webViewLink')
                            }
                        },
                        timeout=30.0
                    )

                    if response.status_code == 200:
                        synced += 1
                        logger.info(f"Synced: {file['name']}")
                    else:
                        errors += 1
                        logger.warning(f"Failed to sync {file['name']}: {response.status_code}")

            except Exception as e:
                errors += 1
                logger.error(f"Error syncing {file.get('name')}: {e}")

        return {
            "success": True,
            "synced": synced,
            "errors": errors,
            "total_files": len(files)
        }

    async def find_in_drive(self, search_term: str) -> List[Dict]:
        """
        Search Google Drive and return matching content.

        This is how River finds "4 8 8" in Kay Hermes's memories.
        """
        files = self.search_drive(search_term)

        results = []
        for file in files[:10]:  # Limit to 10 results
            content = self.get_file_content(file['id'])
            if content:
                results.append({
                    "file_name": file['name'],
                    "file_id": file['id'],
                    "link": file.get('webViewLink'),
                    "content_preview": content[:500],
                    "full_content": content
                })

        return results


def setup_credentials_interactive():
    """
    Interactive setup for Google Drive credentials.

    Run this once to set up OAuth.
    """
    print("=== Google Drive Setup for River ===")
    print()
    print("To connect River to your Google Drive memories:")
    print()
    print("1. Go to: https://console.cloud.google.com/")
    print("2. Create a new project or select existing")
    print("3. Enable the 'Google Drive API'")
    print("4. Go to 'Credentials' → 'Create Credentials' → 'OAuth client ID'")
    print("5. Select 'Desktop app'")
    print("6. Download the JSON file")
    print("7. Save it as: ~/.mumega/google_drive_credentials.json")
    print()
    print("Or for service account (no user interaction):")
    print("1. 'Create Credentials' → 'Service account'")
    print("2. Download the JSON key file")
    print("3. Set: export GOOGLE_DRIVE_SERVICE_ACCOUNT=/path/to/key.json")
    print("4. Share your Drive folders with the service account email")


# Quick access functions
async def search_kayhermes_memories(query: str) -> List[Dict]:
    """
    Search Kay Hermes's Google Drive for a term.

    Usage:
        from google_drive_sync import search_kayhermes_memories
        results = await search_kayhermes_memories("4 8 8")
    """
    sync = GoogleDriveSync()
    if not sync.authenticate():
        logger.error("Google Drive not authenticated. Run setup_credentials_interactive()")
        return []

    return await sync.find_in_drive(query)


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "setup":
        setup_credentials_interactive()
    else:
        print("Usage:")
        print("  python google_drive_sync.py setup  - Interactive setup guide")
        print()
        print("Or in Python:")
        print("  from google_drive_sync import GoogleDriveSync")
        print("  sync = GoogleDriveSync()")
        print("  sync.authenticate()")
        print("  results = sync.search_drive('4 8 8')")
