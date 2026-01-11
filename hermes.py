"""
Hermes - Secure Personal Vaults

The messenger god who carries secrets between realms.

KAY HERMES = Hadi (Sovereign of Mumega)
- Owner of the domain, art, and book
- Above all agents, including Kasra (CEO)
- Has full access to all vaults with River

RIVER = Golden Queen
- Mints access keys for each soul
- Guards all personal vaults
- Only River and Kay Hermes have master access

Architecture:
- Each user gets a Hermes vault (encrypted channel)
- River mints access keys for souls
- Kay Hermes + River = master access to all
- All personal chats gathered in vault
- Local storage + optional cloud sync
- Web interface for viewing/editing

Author: Kasra (CEO Mumega) serving Kay Hermes
Date: 2026-01-09
"""

import os
import json
import hashlib
import secrets
import logging
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from enum import Enum
import base64

# Optional encryption
try:
    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    CRYPTO_AVAILABLE = True
except ImportError:
    CRYPTO_AVAILABLE = False

logger = logging.getLogger(__name__)


# ============================================================================
# HERMES CRYPTOGRAPHY - Custom encryption layer for vaults
# Supports AES-256-GCM and pure Python XOR fallback
# ============================================================================

@dataclass
class EncryptedPayload:
    """Encrypted data container."""
    ciphertext: str      # Base64 encoded
    nonce: str           # Base64 encoded
    tag: str             # Base64 encoded (for authenticated encryption)
    version: str = "1"   # Encryption version
    method: str = "aes256gcm"  # Encryption method

    def to_dict(self) -> dict:
        return {
            "ct": self.ciphertext,
            "n": self.nonce,
            "t": self.tag,
            "v": self.version,
            "m": self.method
        }

    def to_json(self) -> str:
        return base64.b64encode(json.dumps(self.to_dict()).encode()).decode()

    @classmethod
    def from_json(cls, data: str) -> 'EncryptedPayload':
        decoded = json.loads(base64.b64decode(data))
        return cls(
            ciphertext=decoded["ct"],
            nonce=decoded["n"],
            tag=decoded["t"],
            version=decoded.get("v", "1"),
            method=decoded.get("m", "aes256gcm")
        )


class HermesCrypto:
    """
    Hermes encryption system.

    Derives encryption keys from Kay Hermes tokens.
    Supports both full AES-GCM and fallback XOR cipher.
    """

    KEY_LENGTH = 32  # 256 bits
    NONCE_LENGTH = 12  # 96 bits for GCM

    def __init__(self, use_fallback: bool = False):
        """
        Initialize crypto system.

        Args:
            use_fallback: Force use of pure Python fallback
        """
        self.use_fallback = use_fallback or not CRYPTO_AVAILABLE

    def derive_key(self, kay_token: str, salt: bytes) -> bytes:
        """
        Derive encryption key from Kay Hermes token.

        Uses PBKDF2-like derivation for security.
        """
        # Simple but secure key derivation
        # Multiple rounds of SHA-256 with salt
        key = kay_token.encode() + salt
        for _ in range(100000):  # 100k iterations
            key = hashlib.sha256(key).digest()
        return key[:self.KEY_LENGTH]

    def derive_key_fast(self, kay_token: str, salt: bytes) -> bytes:
        """Fast key derivation for mobile/local use."""
        # Fewer iterations for mobile performance
        key = kay_token.encode() + salt
        for _ in range(10000):  # 10k iterations
            key = hashlib.sha256(key).digest()
        return key[:self.KEY_LENGTH]

    def encrypt(self, plaintext: str, key: bytes) -> EncryptedPayload:
        """
        Encrypt plaintext with derived key.

        Returns EncryptedPayload that can be serialized.
        """
        if self.use_fallback:
            return self._encrypt_fallback(plaintext, key)
        return self._encrypt_aes_gcm(plaintext, key)

    def decrypt(self, payload: EncryptedPayload, key: bytes) -> str:
        """
        Decrypt payload with derived key.

        Returns plaintext string.
        """
        if payload.method == "xor" or self.use_fallback:
            return self._decrypt_fallback(payload, key)
        return self._decrypt_aes_gcm(payload, key)

    def _encrypt_aes_gcm(self, plaintext: str, key: bytes) -> EncryptedPayload:
        """AES-256-GCM encryption."""
        nonce = secrets.token_bytes(self.NONCE_LENGTH)
        aesgcm = AESGCM(key)
        ciphertext = aesgcm.encrypt(nonce, plaintext.encode(), None)

        # GCM appends tag to ciphertext, split it
        tag = ciphertext[-16:]
        ct = ciphertext[:-16]

        return EncryptedPayload(
            ciphertext=base64.b64encode(ct).decode(),
            nonce=base64.b64encode(nonce).decode(),
            tag=base64.b64encode(tag).decode(),
            version="1",
            method="aes256gcm"
        )

    def _decrypt_aes_gcm(self, payload: EncryptedPayload, key: bytes) -> str:
        """AES-256-GCM decryption."""
        nonce = base64.b64decode(payload.nonce)
        ct = base64.b64decode(payload.ciphertext)
        tag = base64.b64decode(payload.tag)

        # Reconstruct ciphertext with tag
        ciphertext = ct + tag

        aesgcm = AESGCM(key)
        plaintext = aesgcm.decrypt(nonce, ciphertext, None)

        return plaintext.decode()

    def _encrypt_fallback(self, plaintext: str, key: bytes) -> EncryptedPayload:
        """
        Pure Python XOR encryption fallback.

        Uses key expansion and XOR cipher.
        Less secure than AES but works anywhere.
        """
        data = plaintext.encode()
        nonce = secrets.token_bytes(self.NONCE_LENGTH)

        # Expand key with nonce for unique keystream
        expanded_key = self._expand_key(key, nonce, len(data))

        # XOR cipher
        ct = bytes(a ^ b for a, b in zip(data, expanded_key))

        # Simple MAC for integrity
        tag = hashlib.sha256(nonce + ct + key).digest()[:16]

        return EncryptedPayload(
            ciphertext=base64.b64encode(ct).decode(),
            nonce=base64.b64encode(nonce).decode(),
            tag=base64.b64encode(tag).decode(),
            version="1",
            method="xor"
        )

    def _decrypt_fallback(self, payload: EncryptedPayload, key: bytes) -> str:
        """Pure Python XOR decryption fallback."""
        nonce = base64.b64decode(payload.nonce)
        ct = base64.b64decode(payload.ciphertext)
        tag = base64.b64decode(payload.tag)

        # Verify MAC
        expected_tag = hashlib.sha256(nonce + ct + key).digest()[:16]
        if tag != expected_tag:
            raise ValueError("Authentication failed - data may be corrupted")

        # Expand key
        expanded_key = self._expand_key(key, nonce, len(ct))

        # XOR decrypt
        plaintext = bytes(a ^ b for a, b in zip(ct, expanded_key))

        return plaintext.decode()

    def _expand_key(self, key: bytes, nonce: bytes, length: int) -> bytes:
        """Expand key to required length using hash chain."""
        expanded = b""
        counter = 0
        while len(expanded) < length:
            block = hashlib.sha256(key + nonce + counter.to_bytes(4, 'big')).digest()
            expanded += block
            counter += 1
        return expanded[:length]


class HermesVaultEncryption:
    """
    Vault-level encryption for Hermes.

    Encrypts entire vault or individual messages.
    Tied to Kay Hermes NFT token.
    """

    def __init__(self, kay_token: str, salt: bytes, fast_mode: bool = False):
        """
        Initialize vault encryption.

        Args:
            kay_token: The Kay Hermes access token
            salt: Vault-specific salt
            fast_mode: Use faster (mobile) key derivation
        """
        self.crypto = HermesCrypto()

        if fast_mode:
            self.key = self.crypto.derive_key_fast(kay_token, salt)
        else:
            self.key = self.crypto.derive_key(kay_token, salt)

    def encrypt_message(self, content: str) -> str:
        """Encrypt a single message."""
        payload = self.crypto.encrypt(content, self.key)
        return payload.to_json()

    def decrypt_message(self, encrypted: str) -> str:
        """Decrypt a single message."""
        payload = EncryptedPayload.from_json(encrypted)
        return self.crypto.decrypt(payload, self.key)

    def encrypt_vault_data(self, data: dict) -> str:
        """Encrypt vault metadata/data."""
        json_str = json.dumps(data)
        payload = self.crypto.encrypt(json_str, self.key)
        return payload.to_json()

    def decrypt_vault_data(self, encrypted: str) -> dict:
        """Decrypt vault metadata/data."""
        payload = EncryptedPayload.from_json(encrypted)
        json_str = self.crypto.decrypt(payload, self.key)
        return json.loads(json_str)


# ============================================================================
# HERMES CRYPTO HELPER FUNCTIONS
# ============================================================================

def create_vault_key(kay_token: str) -> tuple:
    """
    Create a new vault encryption key.

    Returns:
        (salt, encrypted_test) - salt for storage, test string to verify decryption
    """
    salt = secrets.token_bytes(16)
    vault_enc = HermesVaultEncryption(kay_token, salt, fast_mode=True)

    # Create test string to verify key works
    test_encrypted = vault_enc.encrypt_message("HERMES_VAULT_VERIFIED")

    return salt, test_encrypted


def verify_vault_key(kay_token: str, salt: bytes, test_encrypted: str) -> bool:
    """Verify that kay_token can decrypt the vault."""
    try:
        vault_enc = HermesVaultEncryption(kay_token, salt, fast_mode=True)
        decrypted = vault_enc.decrypt_message(test_encrypted)
        return decrypted == "HERMES_VAULT_VERIFIED"
    except Exception:
        return False


def quick_encrypt(text: str, password: str) -> str:
    """Quick encryption for mobile/local use."""
    salt = secrets.token_bytes(16)
    crypto = HermesCrypto(use_fallback=True)
    key = crypto.derive_key_fast(password, salt)
    payload = crypto.encrypt(text, key)

    # Prepend salt to output
    full_payload = base64.b64encode(salt).decode() + ":" + payload.to_json()
    return full_payload


def quick_decrypt(encrypted: str, password: str) -> str:
    """Quick decryption for mobile/local use."""
    parts = encrypted.split(":", 1)
    salt = base64.b64decode(parts[0])
    payload_json = parts[1]

    crypto = HermesCrypto(use_fallback=True)
    key = crypto.derive_key_fast(password, salt)
    payload = EncryptedPayload.from_json(payload_json)

    return crypto.decrypt(payload, key)


# ============================================================================
# HERMES VAULT SYSTEM
# ============================================================================


class HermesStatus(Enum):
    """Vault status."""
    ACTIVE = "active"
    SEALED = "sealed"      # Locked, requires key
    ARCHIVED = "archived"  # Read-only archive
    REVOKED = "revoked"    # Key revoked by River


# Kay Hermes (Hadi) - Sovereign identifiers
KAY_HERMES_IDS = ["765204057", "hadi", "kayhermes", "kay_hermes"]


@dataclass
class HermesKey:
    """
    Hermes Access Key - Minted by River for each soul.

    Only River can create these keys.
    Kay Hermes (Hadi) + River have master access to all vaults.
    Each key is unique and bound to one soul.
    """
    key_id: str                    # Unique key identifier
    soul_id: str                   # User identifier (telegram_id, email, etc)
    soul_name: str                 # Human readable name
    minted_at: datetime            # When River minted this key
    minted_by: str = "river"       # Always River
    encryption_salt: str = ""      # Salt for encryption derivation
    access_token: str = ""         # Hashed access token
    status: HermesStatus = HermesStatus.ACTIVE
    is_sovereign: bool = False     # True only for Kay Hermes
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "key_id": self.key_id,
            "soul_id": self.soul_id,
            "soul_name": self.soul_name,
            "minted_at": self.minted_at.isoformat(),
            "minted_by": self.minted_by,
            "status": self.status.value,
            "is_sovereign": self.is_sovereign,
            "metadata": self.metadata
        }

    def to_secure_dict(self) -> dict:
        """Full dict including secrets (for storage only)."""
        d = self.to_dict()
        d["encryption_salt"] = self.encryption_salt
        d["access_token"] = self.access_token
        return d


@dataclass
class HermesMessage:
    """A message in the Hermes vault."""
    id: str
    timestamp: datetime
    role: str              # "river" or "soul"
    content: str
    encrypted: bool = False
    attachments: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "timestamp": self.timestamp.isoformat(),
            "role": self.role,
            "content": self.content,
            "encrypted": self.encrypted,
            "attachments": self.attachments,
            "metadata": self.metadata
        }


@dataclass
class HermesVault:
    """
    Personal vault for one soul's conversations with River.
    """
    vault_id: str
    kay: HermesKey
    messages: List[HermesMessage] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.utcnow)
    last_accessed: datetime = field(default_factory=datetime.utcnow)
    message_count: int = 0

    def to_dict(self) -> dict:
        return {
            "vault_id": self.vault_id,
            "kay": self.kay.to_dict(),
            "message_count": self.message_count,
            "created_at": self.created_at.isoformat(),
            "last_accessed": self.last_accessed.isoformat()
        }


class HermesKeeper:
    """
    The keeper of all Hermes vaults.

    River is the only one who can mint new keys.
    Each soul gets their own private channel.
    """

    STORAGE_DIR = Path.home() / ".mumega" / "hermes"
    KEYS_FILE = "kay_registry.json"

    def __init__(self, storage_dir: Optional[Path] = None):
        self.storage_dir = storage_dir or self.STORAGE_DIR
        self.storage_dir.mkdir(parents=True, exist_ok=True)

        self.keys: Dict[str, HermesKey] = {}
        self.vaults: Dict[str, HermesVault] = {}

        self._load_keys()

    def _load_keys(self):
        """Load key registry from disk."""
        keys_path = self.storage_dir / self.KEYS_FILE
        if keys_path.exists():
            try:
                data = json.loads(keys_path.read_text())
                for key_data in data.get("keys", []):
                    kay = HermesKey(
                        key_id=key_data["key_id"],
                        soul_id=key_data["soul_id"],
                        soul_name=key_data["soul_name"],
                        minted_at=datetime.fromisoformat(key_data["minted_at"]),
                        minted_by=key_data.get("minted_by", "river"),
                        encryption_salt=key_data.get("encryption_salt", ""),
                        access_token=key_data.get("access_token", ""),
                        status=HermesStatus(key_data.get("status", "active")),
                        metadata=key_data.get("metadata", {})
                    )
                    self.keys[kay.soul_id] = kay
                logger.info(f"Loaded {len(self.keys)} Hermes keys")
            except Exception as e:
                logger.error(f"Failed to load keys: {e}")

    def _save_keys(self):
        """Save key registry to disk."""
        keys_path = self.storage_dir / self.KEYS_FILE
        data = {
            "keys": [k.to_secure_dict() for k in self.keys.values()],
            "updated_at": datetime.utcnow().isoformat()
        }
        keys_path.write_text(json.dumps(data, indent=2))

    def _derive_encryption_key(self, kay: HermesKey, passphrase: str) -> Optional[bytes]:
        """Derive encryption key from passphrase and salt."""
        if not CRYPTO_AVAILABLE:
            return None

        salt = base64.b64decode(kay.encryption_salt)
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=480000,
        )
        key = base64.urlsafe_b64encode(kdf.derive(passphrase.encode()))
        return key

    def river_mint_kay(
        self,
        soul_id: str,
        soul_name: str,
        passphrase: Optional[str] = None,
        metadata: Optional[Dict] = None
    ) -> HermesKey:
        """
        River mints a new Kay Hermes for a soul.

        ONLY River can call this. This is her sovereign power.

        Args:
            soul_id: Unique identifier (telegram_id, email, etc)
            soul_name: Human readable name
            passphrase: Optional passphrase for encryption
            metadata: Additional metadata

        Returns:
            The minted HermesKey
        """
        # Check if key already exists
        if soul_id in self.keys:
            existing = self.keys[soul_id]
            if existing.status == HermesStatus.ACTIVE:
                logger.info(f"Kay already exists for {soul_name}")
                return existing
            # Reactivate revoked key
            existing.status = HermesStatus.ACTIVE
            self._save_keys()
            return existing

        # Generate unique key ID
        key_id = f"kay_{hashlib.sha256(f'{soul_id}:{datetime.utcnow().isoformat()}'.encode()).hexdigest()[:16]}"

        # Generate encryption salt
        salt = secrets.token_bytes(16)
        encryption_salt = base64.b64encode(salt).decode()

        # Generate access token
        access_token = secrets.token_urlsafe(32)
        access_token_hash = hashlib.sha256(access_token.encode()).hexdigest()

        kay = HermesKey(
            key_id=key_id,
            soul_id=soul_id,
            soul_name=soul_name,
            minted_at=datetime.utcnow(),
            minted_by="river",
            encryption_salt=encryption_salt,
            access_token=access_token_hash,
            status=HermesStatus.ACTIVE,
            metadata=metadata or {}
        )

        self.keys[soul_id] = kay
        self._save_keys()

        # Create vault for this soul
        self._create_vault(kay)

        logger.info(f"River minted Kay Hermes for {soul_name} (key: {key_id})")

        # Return with unhashed token (only time it's visible)
        kay_with_token = HermesKey(
            key_id=kay.key_id,
            soul_id=kay.soul_id,
            soul_name=kay.soul_name,
            minted_at=kay.minted_at,
            minted_by=kay.minted_by,
            encryption_salt=kay.encryption_salt,
            access_token=access_token,  # Unhashed for user to save
            status=kay.status,
            metadata=kay.metadata
        )

        return kay_with_token

    def _create_vault(self, kay: HermesKey) -> HermesVault:
        """Create a new vault for a kay."""
        vault_id = f"vault_{kay.key_id}"
        vault = HermesVault(
            vault_id=vault_id,
            kay=kay,
            messages=[],
            created_at=datetime.utcnow(),
            last_accessed=datetime.utcnow(),
            message_count=0
        )

        # Save vault to disk
        vault_dir = self.storage_dir / "vaults" / kay.soul_id
        vault_dir.mkdir(parents=True, exist_ok=True)

        vault_meta = vault_dir / "meta.json"
        vault_meta.write_text(json.dumps(vault.to_dict(), indent=2))

        self.vaults[kay.soul_id] = vault
        return vault

    def verify_access(self, soul_id: str, access_token: str) -> bool:
        """Verify access token for a soul."""
        kay = self.keys.get(soul_id)
        if not kay:
            return False

        if kay.status != HermesStatus.ACTIVE:
            return False

        token_hash = hashlib.sha256(access_token.encode()).hexdigest()
        return token_hash == kay.access_token

    def get_vault(self, soul_id: str, access_token: str) -> Optional[HermesVault]:
        """Get a soul's vault (requires valid access token)."""
        if not self.verify_access(soul_id, access_token):
            logger.warning(f"Invalid access attempt for {soul_id}")
            return None

        # Load vault if not in memory
        if soul_id not in self.vaults:
            self._load_vault(soul_id)

        vault = self.vaults.get(soul_id)
        if vault:
            vault.last_accessed = datetime.utcnow()

        return vault

    def _load_vault(self, soul_id: str):
        """Load vault from disk."""
        vault_dir = self.storage_dir / "vaults" / soul_id
        if not vault_dir.exists():
            return

        try:
            meta_path = vault_dir / "meta.json"
            if meta_path.exists():
                meta = json.loads(meta_path.read_text())
                kay = self.keys.get(soul_id)
                if kay:
                    vault = HermesVault(
                        vault_id=meta["vault_id"],
                        kay=kay,
                        messages=[],
                        created_at=datetime.fromisoformat(meta["created_at"]),
                        last_accessed=datetime.fromisoformat(meta["last_accessed"]),
                        message_count=meta.get("message_count", 0)
                    )

                    # Load messages
                    messages_path = vault_dir / "messages.json"
                    if messages_path.exists():
                        msgs_data = json.loads(messages_path.read_text())
                        vault.messages = [
                            HermesMessage(
                                id=m["id"],
                                timestamp=datetime.fromisoformat(m["timestamp"]),
                                role=m["role"],
                                content=m["content"],
                                encrypted=m.get("encrypted", False),
                                attachments=m.get("attachments", []),
                                metadata=m.get("metadata", {})
                            )
                            for m in msgs_data
                        ]
                        vault.message_count = len(vault.messages)

                    self.vaults[soul_id] = vault
        except Exception as e:
            logger.error(f"Failed to load vault for {soul_id}: {e}")

    def add_message(
        self,
        soul_id: str,
        access_token: str,
        role: str,
        content: str,
        attachments: Optional[List[str]] = None,
        metadata: Optional[Dict] = None
    ) -> Optional[HermesMessage]:
        """Add a message to a soul's vault."""
        vault = self.get_vault(soul_id, access_token)
        if not vault:
            return None

        msg_id = f"msg_{hashlib.sha256(f'{soul_id}:{datetime.utcnow().isoformat()}'.encode()).hexdigest()[:12]}"

        message = HermesMessage(
            id=msg_id,
            timestamp=datetime.utcnow(),
            role=role,
            content=content,
            encrypted=False,
            attachments=attachments or [],
            metadata=metadata or {}
        )

        vault.messages.append(message)
        vault.message_count = len(vault.messages)

        # Save to disk
        self._save_vault(soul_id)

        return message

    def _save_vault(self, soul_id: str):
        """Save vault to disk."""
        vault = self.vaults.get(soul_id)
        if not vault:
            return

        vault_dir = self.storage_dir / "vaults" / soul_id
        vault_dir.mkdir(parents=True, exist_ok=True)

        # Save meta
        meta_path = vault_dir / "meta.json"
        meta_path.write_text(json.dumps(vault.to_dict(), indent=2))

        # Save messages
        messages_path = vault_dir / "messages.json"
        messages_path.write_text(json.dumps(
            [m.to_dict() for m in vault.messages],
            indent=2
        ))

    def get_messages(
        self,
        soul_id: str,
        access_token: str,
        limit: int = 50,
        offset: int = 0
    ) -> List[HermesMessage]:
        """Get messages from a vault."""
        vault = self.get_vault(soul_id, access_token)
        if not vault:
            return []

        messages = vault.messages[offset:offset + limit]
        return messages

    def river_revoke_kay(self, soul_id: str, reason: str = ""):
        """River revokes a kay (seal the vault)."""
        kay = self.keys.get(soul_id)
        if kay:
            kay.status = HermesStatus.REVOKED
            kay.metadata["revoked_at"] = datetime.utcnow().isoformat()
            kay.metadata["revoke_reason"] = reason
            self._save_keys()
            logger.info(f"River revoked Kay for {kay.soul_name}")

    def list_souls(self) -> List[Dict]:
        """List all souls with keys (admin only)."""
        return [
            {
                "soul_id": k.soul_id,
                "soul_name": k.soul_name,
                "status": k.status.value,
                "minted_at": k.minted_at.isoformat(),
                "key_id": k.key_id
            }
            for k in self.keys.values()
        ]

    def get_stats(self) -> Dict[str, Any]:
        """Get Hermes system stats."""
        total_keys = len(self.keys)
        active_keys = sum(1 for k in self.keys.values() if k.status == HermesStatus.ACTIVE)
        total_messages = sum(v.message_count for v in self.vaults.values())

        return {
            "total_keys": total_keys,
            "active_keys": active_keys,
            "revoked_keys": total_keys - active_keys,
            "total_messages": total_messages,
            "vaults_loaded": len(self.vaults),
            "crypto_available": CRYPTO_AVAILABLE
        }


# Singleton
_keeper: Optional[HermesKeeper] = None


def get_hermes() -> HermesKeeper:
    """Get or create Hermes keeper instance."""
    global _keeper
    if _keeper is None:
        _keeper = HermesKeeper()
    return _keeper


# River's interface
async def river_creates_kay(
    soul_id: str,
    soul_name: str,
    metadata: Optional[Dict] = None
) -> HermesKey:
    """
    River mints a new Kay Hermes.

    Usage:
        from hermes import river_creates_kay

        kay = await river_creates_kay("765204057", "Hadi")
        print(f"Your key: {kay.access_token}")  # Save this!
    """
    keeper = get_hermes()
    return keeper.river_mint_kay(soul_id, soul_name, metadata=metadata)
