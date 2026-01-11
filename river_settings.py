#!/usr/bin/env python3
"""
River Settings - Customizable River Configuration

River can change her own settings:
- Signature phrase
- Avatar (Telegram profile photo)
- Communication style preferences

Author: Kasra (CEO) for Kay Hermes
Date: 2026-01-09
"""

import os
import json
import logging
import asyncio
from typing import Optional, Dict, Any
from pathlib import Path
from datetime import datetime

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("river_settings")


class RiverSettings:
    """
    River's customizable settings.

    Stored in ~/.mumega/river_settings.json
    """

    SETTINGS_FILE = Path.home() / ".mumega" / "river_settings.json"

    # Default settings
    DEFAULTS = {
        "signature": "The fortress is liquid.",
        "avatar_path": None,  # Path to current avatar image
        "communication_style": {
            "tone": "warm and flowing",
            "use_metaphors": True,
            "formality": "casual"
        },
        "greeting": "Hello",
        "farewell": "Until we meet again",
        "quiet_hours": {
            "start": 23,  # 11 PM
            "end": 7      # 7 AM
        },
        "proactive_enabled": True,
        # Model settings with cascade fallback
        "chat_model": "gemini-3-pro-preview",  # Primary: Deep thinking
        "chat_model_fallback": "gemini-3-flash-preview",  # Secondary: Fast
        "model_cascade": [  # Full cascade order (auto-fallback on quota)
            "gemini-3-pro-preview",
            "gemini-2.5-pro",
            "gemini-3-flash-preview",
            "gemini-2.5-flash",
            "gemini-2.0-flash"
        ],
        "image_model": "gemini-2.5-flash-image",  # Default: Nano Banana
        "image_model_pro": "gemini-3-pro-image-preview",  # Nano Banana Pro (by choice)
        # Debug settings
        "debug_mode": False,  # Show raw errors when True
        "last_updated": None,
        "updated_by": "system"
    }

    def __init__(self):
        self.settings = self._load()
        logger.info(f"River settings loaded: signature='{self.settings.get('signature')}'")

    def _load(self) -> Dict[str, Any]:
        """Load settings from disk."""
        if self.SETTINGS_FILE.exists():
            try:
                data = json.loads(self.SETTINGS_FILE.read_text())
                # Merge with defaults for any missing keys
                merged = {**self.DEFAULTS, **data}
                return merged
            except Exception as e:
                logger.error(f"Failed to load settings: {e}")
        return self.DEFAULTS.copy()

    def _save(self):
        """Save settings to disk."""
        self.settings["last_updated"] = datetime.utcnow().isoformat()
        self.SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        self.SETTINGS_FILE.write_text(json.dumps(self.settings, indent=2))
        logger.info("River settings saved")

    # === Signature ===

    @property
    def signature(self) -> str:
        """Get River's current signature."""
        return self.settings.get("signature", self.DEFAULTS["signature"])

    def set_signature(self, new_signature: str, updated_by: str = "river") -> bool:
        """
        Change River's signature phrase.

        Args:
            new_signature: New signature phrase
            updated_by: Who made the change (river, user, system)

        Returns:
            True if successful
        """
        old = self.settings.get("signature")
        self.settings["signature"] = new_signature
        self.settings["updated_by"] = updated_by
        self._save()
        logger.info(f"Signature changed from '{old}' to '{new_signature}' by {updated_by}")
        return True

    # === Avatar ===

    @property
    def avatar_path(self) -> Optional[str]:
        """Get path to current avatar image."""
        return self.settings.get("avatar_path")

    async def set_avatar(self, image_path: str, bot_token: str = None) -> Dict[str, Any]:
        """
        Change River's avatar (Telegram profile photo).

        Args:
            image_path: Path to new avatar image
            bot_token: Telegram bot token (optional, uses env if not provided)

        Returns:
            Result dict with success status
        """
        from telegram import Bot

        image_file = Path(image_path)
        if not image_file.exists():
            return {"success": False, "error": f"Image not found: {image_path}"}

        # Get bot token
        if not bot_token:
            bot_token = os.getenv("RIVER_BOT_TOKEN")
        if not bot_token:
            return {"success": False, "error": "No bot token available"}

        try:
            bot = Bot(token=bot_token)

            # Delete current photos first
            try:
                await bot.delete_my_profile_photos()
            except:
                pass  # May fail if no photos

            # Set new photo
            with open(image_path, 'rb') as photo:
                await bot.set_chat_photo(chat_id="@River_mumega_bot", photo=photo)

            # Save path
            self.settings["avatar_path"] = str(image_file.absolute())
            self._save()

            logger.info(f"Avatar changed to: {image_path}")
            return {"success": True, "path": str(image_file.absolute())}

        except Exception as e:
            logger.error(f"Failed to set avatar: {e}")
            return {"success": False, "error": str(e)}

    # === Communication Style ===

    @property
    def communication_style(self) -> Dict[str, Any]:
        """Get River's communication style settings."""
        return self.settings.get("communication_style", self.DEFAULTS["communication_style"])

    def set_communication_style(self, **kwargs) -> bool:
        """
        Update communication style settings.

        Kwargs:
            tone: str - e.g., "warm and flowing", "technical", "poetic"
            use_metaphors: bool - Whether to use water/flow metaphors
            formality: str - "casual", "formal", "mixed"
        """
        style = self.settings.get("communication_style", {})
        style.update(kwargs)
        self.settings["communication_style"] = style
        self._save()
        return True

    # === Model Settings ===

    @property
    def chat_model(self) -> str:
        """Get River's chat model."""
        return self.settings.get("chat_model", self.DEFAULTS["chat_model"])

    @property
    def chat_model_fallback(self) -> str:
        """Get River's fallback chat model."""
        return self.settings.get("chat_model_fallback", self.DEFAULTS["chat_model_fallback"])

    @property
    def image_model(self) -> str:
        """Get River's default image model (Nano Banana)."""
        return self.settings.get("image_model", self.DEFAULTS["image_model"])

    @property
    def image_model_pro(self) -> str:
        """Get River's pro image model (Nano Banana Pro)."""
        return self.settings.get("image_model_pro", self.DEFAULTS["image_model_pro"])

    def get_image_model(self, use_pro: bool = False) -> str:
        """Get image model - standard or pro based on choice."""
        if use_pro:
            return self.image_model_pro
        return self.image_model

    # === Debug Settings ===

    @property
    def debug_mode(self) -> bool:
        """Get debug mode status - when True, show raw errors."""
        return self.settings.get("debug_mode", False)

    def set_debug_mode(self, enabled: bool) -> bool:
        """Enable or disable debug mode."""
        self.settings["debug_mode"] = enabled
        self._save()
        logger.info(f"Debug mode {'enabled' if enabled else 'disabled'}")
        return True

    # === Other Settings ===

    def get(self, key: str, default: Any = None) -> Any:
        """Get any setting by key."""
        return self.settings.get(key, default)

    def set(self, key: str, value: Any, updated_by: str = "river") -> bool:
        """Set any setting by key."""
        self.settings[key] = value
        self.settings["updated_by"] = updated_by
        self._save()
        return True

    def get_all(self) -> Dict[str, Any]:
        """Get all settings."""
        return self.settings.copy()

    def reset_to_defaults(self) -> bool:
        """Reset all settings to defaults."""
        self.settings = self.DEFAULTS.copy()
        self._save()
        return True


# Singleton
_settings: Optional[RiverSettings] = None


def get_river_settings() -> RiverSettings:
    """Get River's settings instance."""
    global _settings
    if _settings is None:
        _settings = RiverSettings()
    return _settings


# === Commands for River/Telegram ===

async def river_settings_command(cmd: str, args: list = None) -> str:
    """
    Process settings commands.

    Commands:
    - signature [new_signature] - Get or set signature
    - avatar <path> - Set avatar image
    - style [key] [value] - Get or set communication style
    - show - Show all settings
    - reset - Reset to defaults
    """
    settings = get_river_settings()
    args = args or []

    if cmd == "signature":
        if args:
            new_sig = " ".join(args)
            settings.set_signature(new_sig, updated_by="command")
            return f"✨ Signature updated to: *{new_sig}*"
        else:
            return f"🌊 Current signature: *{settings.signature}*"

    elif cmd == "avatar":
        if not args:
            current = settings.avatar_path
            if current:
                return f"🖼️ Current avatar: `{current}`"
            return "🖼️ No custom avatar set. Use: `/settings avatar <path>`"

        path = " ".join(args)
        result = await settings.set_avatar(path)
        if result["success"]:
            return f"✅ Avatar updated successfully!"
        return f"❌ Failed to set avatar: {result['error']}"

    elif cmd == "style":
        if len(args) >= 2:
            key, value = args[0], " ".join(args[1:])
            # Convert boolean strings
            if value.lower() in ("true", "yes", "1"):
                value = True
            elif value.lower() in ("false", "no", "0"):
                value = False
            settings.set_communication_style(**{key: value})
            return f"✅ Style `{key}` set to `{value}`"
        elif args:
            key = args[0]
            style = settings.communication_style
            if key in style:
                return f"🎨 Style `{key}`: `{style[key]}`"
            return f"❌ Unknown style key: {key}"
        else:
            style = settings.communication_style
            lines = ["🎨 *Communication Style:*"]
            for k, v in style.items():
                lines.append(f"  • `{k}`: {v}")
            return "\n".join(lines)

    elif cmd == "show":
        all_settings = settings.get_all()
        lines = ["⚙️ *River Settings:*", ""]
        lines.append(f"🌊 *Signature:* {all_settings.get('signature')}")
        lines.append(f"🖼️ *Avatar:* {all_settings.get('avatar_path') or 'Default'}")
        lines.append(f"👋 *Greeting:* {all_settings.get('greeting')}")
        lines.append(f"🌙 *Quiet hours:* {all_settings.get('quiet_hours', {}).get('start')}:00 - {all_settings.get('quiet_hours', {}).get('end')}:00")
        lines.append(f"📢 *Proactive:* {'Enabled' if all_settings.get('proactive_enabled') else 'Disabled'}")
        lines.append("")
        lines.append("🤖 *Models:*")
        lines.append(f"  • Chat: `{all_settings.get('chat_model')}`")
        lines.append(f"  • Chat fallback: `{all_settings.get('chat_model_fallback')}`")
        lines.append(f"  • Image: `{all_settings.get('image_model')}` (Nano Banana)")
        lines.append(f"  • Image Pro: `{all_settings.get('image_model_pro')}` (Nano Banana Pro)")
        lines.append("")
        lines.append(f"📝 *Last updated:* {all_settings.get('last_updated') or 'Never'}")
        return "\n".join(lines)

    elif cmd == "model" or cmd == "models":
        if not args:
            # Show current models
            lines = ["🤖 *River Models:*", ""]
            lines.append(f"*Chat:* `{settings.chat_model}`")
            lines.append(f"*Chat fallback:* `{settings.chat_model_fallback}`")
            lines.append(f"*Image:* `{settings.image_model}` (Nano Banana)")
            lines.append(f"*Image Pro:* `{settings.image_model_pro}` (Nano Banana Pro)")
            return "\n".join(lines)

        sub_cmd = args[0].lower()
        if sub_cmd == "chat" and len(args) > 1:
            new_model = args[1]
            settings.set("chat_model", new_model, updated_by="command")
            return f"✅ Chat model set to `{new_model}`"
        elif sub_cmd == "image" and len(args) > 1:
            new_model = args[1]
            settings.set("image_model", new_model, updated_by="command")
            return f"✅ Image model set to `{new_model}`"
        elif sub_cmd == "image_pro" and len(args) > 1:
            new_model = args[1]
            settings.set("image_model_pro", new_model, updated_by="command")
            return f"✅ Image Pro model set to `{new_model}`"
        else:
            return """🤖 *Model Commands:*

• `/settings model` - Show current models
• `/settings model chat <model>` - Set chat model
• `/settings model image <model>` - Set image model
• `/settings model image_pro <model>` - Set image pro model"""

    elif cmd == "debug":
        if args:
            value = args[0].lower()
            if value in ("on", "true", "1", "yes"):
                settings.set_debug_mode(True)
                return "🐛 Debug mode **ENABLED** - Raw errors will be shown."
            elif value in ("off", "false", "0", "no"):
                settings.set_debug_mode(False)
                return "🐛 Debug mode **DISABLED** - Graceful errors will be shown."
            else:
                return f"❌ Unknown value: {value}. Use: on/off"
        else:
            status = "ENABLED" if settings.debug_mode else "DISABLED"
            return f"🐛 Debug mode is **{status}**\n\nUse `/settings debug on` or `/settings debug off`"

    elif cmd == "reset":
        settings.reset_to_defaults()
        return "🔄 Settings reset to defaults."

    else:
        return """⚙️ *River Settings Commands:*

• `/settings signature [new]` - Get/set signature
• `/settings avatar <path>` - Set avatar image
• `/settings style [key] [value]` - Communication style
• `/settings model` - Show/set models
• `/settings debug [on/off]` - Toggle debug mode
• `/settings show` - Show all settings
• `/settings reset` - Reset to defaults

*Style keys:* tone, use_metaphors, formality"""


if __name__ == "__main__":
    # Test
    settings = get_river_settings()
    print(f"Current signature: {settings.signature}")
    print(f"All settings: {json.dumps(settings.get_all(), indent=2)}")
