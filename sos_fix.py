import os
from pathlib import Path

def write_guilds():
    path = Path("/home/mumega/SOS/mumega/core/squads/guilds.py")
    path.parent.mkdir(parents=True, exist_ok=True)
    content = """
Guild Registry - Human-led squads for SovereignOS (SOS).
"""

import logging
import json
from pathlib import Path
from typing import List, Dict, Optional, Any
from datetime import datetime
from enum import Enum

logger = logging.getLogger("sos.guilds")

class SOSEdition(Enum):
    BUSINESS = "business"
    EDUCATION = "education"
    KIDS = "kids"
    ART_MUSIC = "art_music"
    RESEARCH = "research"

class Guild:
    def __init__(
        self,
        id: str,
        name: str,
        edition: SOSEdition,
        human_leader_id: str,
        objective: str,
        members: List[str] = None,
        metadata: Dict[str, Any] = None
    ):
        self.id = id
        self.name = name
        self.edition = edition
        self.human_leader_id = human_leader_id
        self.objective = objective
        self.members = members or []
        self.metadata = metadata or {}
        self.created_at = datetime.now()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "edition": self.edition.value,
            "human_leader_id": self.human_leader_id,
            "objective": self.objective,
            "members": self.members,
            "metadata": self.metadata,
            "created_at": self.created_at.isoformat()
        }

class GuildRegistry:
    """
    Manages the lifecycle of SOS Guilds.
    """
    def __init__(self, data_dir: str = "/home/mumega/SOS/data/guilds"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.guilds: Dict[str, Guild] = {}
        self._load_guilds()

    def _load_guilds(self):
        for file in self.data_dir.glob("*.json"):
            try:
                with open(file, "r") as f:
                    data = json.load(f)
                    guild = Guild(
                        id=data["id"],
                        name=data["name"],
                        edition=SOSEdition(data["edition"]),
                        human_leader_id=data["human_leader_id"],
                        objective=data["objective"],
                        members=data["members"],
                        metadata=data.get("metadata")
                    )
                    guild.created_at = datetime.fromisoformat(data["created_at"])
                    self.guilds[guild.id] = guild
            except Exception as e:
                logger.error(f"Failed to load guild {file.name}: {e}")

    def create_guild(
        self,
        name: str,
        edition: SOSEdition,
        human_leader_id: str,
        objective: str
    ) -> Guild:
        import hashlib
        guild_id = f"guild_{hashlib.sha256(name.encode()).hexdigest()[:8]}"
        guild = Guild(
            id=guild_id,
            name=name,
            edition=edition,
            human_leader_id=human_leader_id,
            objective=objective
        )
        self.save_guild(guild)
        return guild

    def save_guild(self, guild: Guild):
        file_path = self.data_dir / f"{guild.id}.json"
        with open(file_path, "w") as f:
            json.dump(guild.to_dict(), f, indent=2)
        self.guilds[guild.id] = guild

    def list_guilds(self, edition: SOSEdition = None) -> List[Guild]:
        if edition:
            return [g for g in self.guilds.values() if g.edition == edition]
        return list(self.guilds.values())

    def join_guild(self, guild_id: str, agent_id: str) -> bool:
        if guild_id not in self.guilds:
            return False
        if agent_id not in self.guilds[guild_id].members:
            self.guilds[guild_id].members.append(agent_id)
            self.save_guild(self.guilds[guild_id])
            return True
        return False