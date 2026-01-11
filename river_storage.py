#!/usr/bin/env python3
"""
River Storage - Personal Google Drive + Local Storage

River has her own storage space for:
- Files received from Telegram
- Documents she creates
- Memories and context backups
- Synced to Google Drive for persistence

Author: Kasra (CEO) for Kay Hermes
Date: 2026-01-09
"""

import os
import sys
import json
import asyncio
import logging
import hashlib
from typing import Optional, List, Dict, Any
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, field, asdict

# Add mirror to path
sys.path.insert(0, str(Path(__file__).parent))

try:
    from google_drive_sync import GoogleDriveSync, GOOGLE_AVAILABLE
except ImportError:
    GOOGLE_AVAILABLE = False

from river_context_cache import (
    get_river_cache, get_river_dynamic, add_river_footer,
    LARGE_CONTENT_THRESHOLD
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("river_storage")


@dataclass
class StoredFile:
    """A file in River's storage."""
    id: str
    filename: str
    local_path: str
    gdrive_id: Optional[str] = None
    gdrive_link: Optional[str] = None
    mime_type: str = "application/octet-stream"
    size: int = 0
    created_at: str = ""
    uploaded_by: str = ""
    description: str = ""
    tags: List[str] = field(default_factory=list)
    summarized: bool = False
    summary: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class RiverStorage:
    """
    River's personal storage system.

    Features:
    - Local storage in ~/.mumega/river_storage/
    - Sync to Google Drive folder "River/Storage"
    - File summarization for context
    - Tag-based organization
    """

    # River's storage paths
    LOCAL_ROOT = Path.home() / ".mumega" / "river_storage"
    METADATA_FILE = LOCAL_ROOT / ".river_files.json"

    # Google Drive folder name
    GDRIVE_FOLDER_NAME = "River_Storage"

    def __init__(self):
        # Create local directories
        self.local_root = self.LOCAL_ROOT
        self.local_root.mkdir(parents=True, exist_ok=True)

        # Subdirectories
        self.files_dir = self.local_root / "files"
        self.docs_dir = self.local_root / "documents"
        self.backups_dir = self.local_root / "backups"

        for d in [self.files_dir, self.docs_dir, self.backups_dir]:
            d.mkdir(exist_ok=True)

        # Load metadata
        self.files: Dict[str, StoredFile] = {}
        self._load_metadata()

        # Google Drive
        self.gdrive: Optional[GoogleDriveSync] = None
        self.gdrive_folder_id: Optional[str] = None
        self._gdrive_available = GOOGLE_AVAILABLE

        logger.info(f"River Storage initialized at {self.local_root}")

    def _load_metadata(self):
        """Load file metadata from disk."""
        if self.METADATA_FILE.exists():
            try:
                data = json.loads(self.METADATA_FILE.read_text())
                for file_id, file_data in data.get("files", {}).items():
                    self.files[file_id] = StoredFile(**file_data)
                logger.info(f"Loaded {len(self.files)} files from metadata")
            except Exception as e:
                logger.error(f"Failed to load metadata: {e}")

    def _save_metadata(self):
        """Save file metadata to disk."""
        data = {
            "files": {fid: f.to_dict() for fid, f in self.files.items()},
            "last_updated": datetime.utcnow().isoformat()
        }
        self.METADATA_FILE.write_text(json.dumps(data, indent=2))

    def _generate_file_id(self, filename: str, content: bytes) -> str:
        """Generate unique file ID."""
        hash_input = f"{filename}:{len(content)}:{datetime.utcnow().isoformat()}"
        return f"rf_{hashlib.sha256(hash_input.encode()).hexdigest()[:12]}"

    async def init_gdrive(self) -> bool:
        """Initialize Google Drive connection."""
        if not self._gdrive_available:
            logger.warning("Google Drive libraries not available")
            return False

        try:
            self.gdrive = GoogleDriveSync()
            if not self.gdrive.authenticate():
                logger.error("Failed to authenticate with Google Drive")
                return False

            # Find or create River's folder
            self.gdrive_folder_id = await self._get_or_create_gdrive_folder()
            if self.gdrive_folder_id:
                logger.info(f"Google Drive folder ready: {self.gdrive_folder_id}")
                return True

        except Exception as e:
            logger.error(f"Google Drive init failed: {e}")

        return False

    async def _get_or_create_gdrive_folder(self) -> Optional[str]:
        """Get or create River's Google Drive folder."""
        if not self.gdrive or not self.gdrive._authenticated:
            return None

        try:
            # Search for existing folder
            results = self.gdrive.service.files().list(
                q=f"name='{self.GDRIVE_FOLDER_NAME}' and mimeType='application/vnd.google-apps.folder' and trashed=false",
                fields="files(id, name)"
            ).execute()

            files = results.get('files', [])
            if files:
                return files[0]['id']

            # Create folder
            folder_metadata = {
                'name': self.GDRIVE_FOLDER_NAME,
                'mimeType': 'application/vnd.google-apps.folder'
            }
            folder = self.gdrive.service.files().create(
                body=folder_metadata,
                fields='id'
            ).execute()

            logger.info(f"Created Google Drive folder: {folder['id']}")
            return folder['id']

        except Exception as e:
            logger.error(f"Failed to get/create GDrive folder: {e}")
            return None

    async def store_file(
        self,
        content: bytes,
        filename: str,
        uploaded_by: str,
        description: str = "",
        tags: Optional[List[str]] = None,
        sync_to_gdrive: bool = True
    ) -> StoredFile:
        """
        Store a file in River's storage.

        Args:
            content: File content as bytes
            filename: Original filename
            uploaded_by: User who uploaded (telegram_id)
            description: Optional description
            tags: Optional tags for organization
            sync_to_gdrive: Whether to sync to Google Drive

        Returns:
            StoredFile with storage details
        """
        # Generate ID and determine path
        file_id = self._generate_file_id(filename, content)

        # Determine subdirectory based on extension
        ext = Path(filename).suffix.lower()
        if ext in ['.md', '.txt', '.doc', '.docx', '.pdf']:
            subdir = self.docs_dir
        else:
            subdir = self.files_dir

        # Save locally
        local_path = subdir / f"{file_id}_{filename}"
        local_path.write_bytes(content)

        # Create metadata
        stored_file = StoredFile(
            id=file_id,
            filename=filename,
            local_path=str(local_path),
            mime_type=self._guess_mime_type(filename),
            size=len(content),
            created_at=datetime.utcnow().isoformat(),
            uploaded_by=uploaded_by,
            description=description,
            tags=tags or []
        )

        # Sync to Google Drive
        if sync_to_gdrive and self.gdrive and self.gdrive_folder_id:
            gdrive_result = await self._upload_to_gdrive(local_path, filename)
            if gdrive_result:
                stored_file.gdrive_id = gdrive_result.get("id")
                stored_file.gdrive_link = gdrive_result.get("webViewLink")

        # Store metadata
        self.files[file_id] = stored_file
        self._save_metadata()

        logger.info(f"Stored file: {filename} ({file_id})")
        return stored_file

    async def _upload_to_gdrive(self, local_path: Path, filename: str) -> Optional[Dict]:
        """Upload a file to Google Drive."""
        if not self.gdrive or not self.gdrive_folder_id:
            return None

        try:
            from googleapiclient.http import MediaFileUpload

            file_metadata = {
                'name': filename,
                'parents': [self.gdrive_folder_id]
            }

            media = MediaFileUpload(
                str(local_path),
                mimetype=self._guess_mime_type(filename),
                resumable=True
            )

            file = self.gdrive.service.files().create(
                body=file_metadata,
                media_body=media,
                fields='id, webViewLink'
            ).execute()

            logger.info(f"Uploaded to GDrive: {filename} -> {file['id']}")
            return file

        except Exception as e:
            logger.error(f"GDrive upload failed: {e}")
            return None

    def _guess_mime_type(self, filename: str) -> str:
        """Guess MIME type from filename."""
        ext = Path(filename).suffix.lower()
        mime_map = {
            '.txt': 'text/plain',
            '.md': 'text/markdown',
            '.json': 'application/json',
            '.pdf': 'application/pdf',
            '.png': 'image/png',
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.gif': 'image/gif',
            '.mp3': 'audio/mpeg',
            '.mp4': 'video/mp4',
            '.py': 'text/x-python',
            '.js': 'text/javascript',
            '.html': 'text/html',
            '.css': 'text/css',
            '.doc': 'application/msword',
            '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        }
        return mime_map.get(ext, 'application/octet-stream')

    async def get_file(self, file_id: str) -> Optional[bytes]:
        """Get file content by ID."""
        stored_file = self.files.get(file_id)
        if not stored_file:
            return None

        local_path = Path(stored_file.local_path)
        if local_path.exists():
            return local_path.read_bytes()

        # Try to download from GDrive if local missing
        if stored_file.gdrive_id and self.gdrive:
            content = self.gdrive.get_file_content(stored_file.gdrive_id)
            if content:
                return content.encode() if isinstance(content, str) else content

        return None

    def list_files(
        self,
        tags: Optional[List[str]] = None,
        uploaded_by: Optional[str] = None,
        limit: int = 50
    ) -> List[StoredFile]:
        """List files with optional filters."""
        files = list(self.files.values())

        if tags:
            files = [f for f in files if any(t in f.tags for t in tags)]

        if uploaded_by:
            files = [f for f in files if f.uploaded_by == uploaded_by]

        # Sort by creation time (newest first)
        files.sort(key=lambda f: f.created_at, reverse=True)

        return files[:limit]

    def search_files(self, query: str) -> List[StoredFile]:
        """Search files by filename or description."""
        query = query.lower()
        results = []

        for f in self.files.values():
            if query in f.filename.lower() or query in f.description.lower():
                results.append(f)
            elif any(query in tag.lower() for tag in f.tags):
                results.append(f)

        return results

    def delete_file(self, file_id: str, delete_gdrive: bool = True) -> bool:
        """Delete a file from storage."""
        stored_file = self.files.get(file_id)
        if not stored_file:
            return False

        # Delete local
        try:
            local_path = Path(stored_file.local_path)
            if local_path.exists():
                local_path.unlink()
        except Exception as e:
            logger.error(f"Failed to delete local file: {e}")

        # Delete from GDrive
        if delete_gdrive and stored_file.gdrive_id and self.gdrive:
            try:
                self.gdrive.service.files().delete(fileId=stored_file.gdrive_id).execute()
            except Exception as e:
                logger.error(f"Failed to delete from GDrive: {e}")

        # Remove from metadata
        del self.files[file_id]
        self._save_metadata()

        logger.info(f"Deleted file: {file_id}")
        return True

    async def sync_to_gdrive(self) -> Dict[str, int]:
        """Sync all local files to Google Drive."""
        if not await self.init_gdrive():
            return {"error": "Google Drive not available"}

        synced = 0
        failed = 0

        for stored_file in self.files.values():
            if not stored_file.gdrive_id:
                local_path = Path(stored_file.local_path)
                if local_path.exists():
                    result = await self._upload_to_gdrive(local_path, stored_file.filename)
                    if result:
                        stored_file.gdrive_id = result.get("id")
                        stored_file.gdrive_link = result.get("webViewLink")
                        synced += 1
                    else:
                        failed += 1

        self._save_metadata()
        return {"synced": synced, "failed": failed, "total": len(self.files)}

    async def sync_from_gdrive(self) -> Dict[str, int]:
        """Sync files from Google Drive to local."""
        if not await self.init_gdrive():
            return {"error": "Google Drive not available"}

        downloaded = 0

        try:
            # List files in River's folder
            results = self.gdrive.service.files().list(
                q=f"'{self.gdrive_folder_id}' in parents and trashed=false",
                fields="files(id, name, mimeType, size, modifiedTime)"
            ).execute()

            for gdrive_file in results.get('files', []):
                # Check if already exists
                existing = None
                for f in self.files.values():
                    if f.gdrive_id == gdrive_file['id']:
                        existing = f
                        break

                if not existing:
                    # Download new file
                    content = self.gdrive.get_file_content(gdrive_file['id'])
                    if content:
                        content_bytes = content.encode() if isinstance(content, str) else content
                        await self.store_file(
                            content_bytes,
                            gdrive_file['name'],
                            "gdrive_sync",
                            tags=["synced_from_gdrive"],
                            sync_to_gdrive=False  # Already on GDrive
                        )
                        downloaded += 1

        except Exception as e:
            logger.error(f"GDrive sync failed: {e}")
            return {"error": str(e)}

        return {"downloaded": downloaded}

    async def summarize_file(self, file_id: str, summarizer=None) -> Optional[str]:
        """
        Summarize a file's content for context.

        This lets River remember what's in files without
        keeping the full content in context.
        """
        stored_file = self.files.get(file_id)
        if not stored_file:
            return None

        content = await self.get_file(file_id)
        if not content:
            return None

        # Convert to text
        try:
            text = content.decode('utf-8')
        except:
            text = content.decode('utf-8', errors='ignore')

        # Use dynamic context for summarization
        dynamic = get_river_dynamic()

        if summarizer:
            summary = await summarizer(text, 500)
        else:
            summary = dynamic._basic_summarize(text, 500)

        # Update metadata
        stored_file.summarized = True
        stored_file.summary = summary
        self._save_metadata()

        return summary

    def get_storage_stats(self) -> Dict[str, Any]:
        """Get storage statistics."""
        total_size = sum(f.size for f in self.files.values())
        synced_count = sum(1 for f in self.files.values() if f.gdrive_id)

        return {
            "total_files": len(self.files),
            "total_size_bytes": total_size,
            "total_size_mb": round(total_size / (1024 * 1024), 2),
            "synced_to_gdrive": synced_count,
            "local_only": len(self.files) - synced_count,
            "gdrive_available": self._gdrive_available,
            "local_path": str(self.local_root)
        }


# Singleton
_storage: Optional[RiverStorage] = None


def get_river_storage() -> RiverStorage:
    """Get River's storage instance."""
    global _storage
    if _storage is None:
        _storage = RiverStorage()
    return _storage


# ============================================
# TELEGRAM FILE HANDLER INTEGRATION
# ============================================

async def handle_telegram_file(
    file_content: bytes,
    filename: str,
    user_id: str,
    description: str = "",
    add_to_gemini_context: bool = True
) -> Dict[str, Any]:
    """
    Handle a file received from Telegram.

    Stores in River's storage and optionally adds to Gemini context.
    """
    storage = get_river_storage()

    stored_file = await storage.store_file(
        content=file_content,
        filename=filename,
        uploaded_by=f"telegram_{user_id}",
        description=description,
        tags=["telegram_upload"]
    )

    # Summarize text files for context
    if stored_file.mime_type.startswith('text/') or filename.endswith(('.md', '.txt', '.json')):
        await storage.summarize_file(stored_file.id)

    # Upload to Gemini context if requested (for text files)
    gemini_uri = None
    if add_to_gemini_context and stored_file.mime_type.startswith('text/'):
        try:
            from river_cache_manager import get_gemini_cache_manager
            cache_manager = get_gemini_cache_manager()
            result = await cache_manager.upload_file(
                stored_file.local_path,
                display_name=f"{stored_file.id}_{filename}"
            )
            if result.get("success"):
                gemini_uri = result.get("uri")
                # Track in storage metadata
                stored_file.tags.append("gemini_context")
                storage._save_metadata()
                logger.info(f"Added to Gemini context: {filename} -> {gemini_uri}")
        except Exception as e:
            logger.warning(f"Failed to add to Gemini context: {e}")

    return {
        "success": True,
        "file_id": stored_file.id,
        "filename": stored_file.filename,
        "size": stored_file.size,
        "gdrive_link": stored_file.gdrive_link,
        "gemini_uri": gemini_uri,
        "in_gemini_context": gemini_uri is not None,
        "summary": stored_file.summary if stored_file.summarized else None
    }


if __name__ == "__main__":
    # Test storage
    storage = get_river_storage()
    print(f"Storage initialized at: {storage.local_root}")
    print(f"Stats: {storage.get_storage_stats()}")
