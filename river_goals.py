#!/usr/bin/env python3
"""
River Goals - Long-term goal tracking and persistence

Gives River stability by maintaining goals across sessions, restarts, and time.
Goals are stored in YAML, reviewed periodically, and updated based on progress.

Usage:
    from river_goals import get_river_goals, GoalPriority

    goals = get_river_goals()
    goals.add_goal("Learn about quantum computing", priority=GoalPriority.MEDIUM)
    goals.update_progress("goal_id", 0.5, "Made good progress today")

Author: Kasra for River
Date: 2026-01-14
"""

import os
import yaml
import logging
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field, asdict
from enum import Enum
import hashlib

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("river_goals")

# Persistent storage location
GOALS_DIR = Path("/home/mumega/.river")
GOALS_FILE = GOALS_DIR / "goals.yaml"
GOALS_HISTORY = GOALS_DIR / "goals_history.yaml"


class GoalPriority(Enum):
    """Goal priority levels."""
    CRITICAL = "critical"    # Must accomplish
    HIGH = "high"            # Very important
    MEDIUM = "medium"        # Normal importance
    LOW = "low"              # Nice to have
    DREAM = "dream"          # Long-term aspiration


class GoalStatus(Enum):
    """Goal status."""
    ACTIVE = "active"        # Currently working on
    PAUSED = "paused"        # Temporarily paused
    COMPLETED = "completed"  # Successfully done
    ABANDONED = "abandoned"  # Decided not to pursue
    BLOCKED = "blocked"      # Waiting on something


@dataclass
class GoalProgress:
    """A progress update for a goal."""
    timestamp: str
    progress: float  # 0.0 to 1.0
    note: str
    source: str = "river"  # river, kasra, user


@dataclass
class Goal:
    """A long-term goal for River."""
    id: str
    title: str
    description: str
    priority: str = "medium"
    status: str = "active"
    progress: float = 0.0  # 0.0 to 1.0
    created_at: str = ""
    updated_at: str = ""
    target_date: Optional[str] = None
    category: str = "general"
    parent_id: Optional[str] = None  # For sub-goals
    tags: List[str] = field(default_factory=list)
    progress_history: List[Dict] = field(default_factory=list)
    notes: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now().isoformat()
        if not self.updated_at:
            self.updated_at = self.created_at


class RiverGoals:
    """
    River's goal management system.

    Provides persistent storage and tracking of long-term goals.
    """

    def __init__(self):
        """Initialize the goal system."""
        self.goals: Dict[str, Goal] = {}
        self.completed_goals: List[Goal] = []

        # Ensure directory exists
        GOALS_DIR.mkdir(parents=True, exist_ok=True)

        # Load existing goals
        self._load_goals()

        logger.info(f"River Goals initialized: {len(self.goals)} active goals")

    def _generate_id(self, title: str) -> str:
        """Generate a unique goal ID."""
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        hash_part = hashlib.md5(title.encode()).hexdigest()[:6]
        return f"goal_{timestamp}_{hash_part}"

    def _load_goals(self):
        """Load goals from persistent storage."""
        if GOALS_FILE.exists():
            try:
                data = yaml.safe_load(GOALS_FILE.read_text())
                if data and "goals" in data:
                    for goal_data in data["goals"]:
                        goal = Goal(**goal_data)
                        self.goals[goal.id] = goal
                if data and "completed" in data:
                    for goal_data in data["completed"]:
                        self.completed_goals.append(Goal(**goal_data))
                logger.info(f"Loaded {len(self.goals)} goals from {GOALS_FILE}")
            except Exception as e:
                logger.error(f"Failed to load goals: {e}")

    def _save_goals(self):
        """Save goals to persistent storage."""
        try:
            data = {
                "version": "1.0",
                "last_updated": datetime.now().isoformat(),
                "goals": [asdict(g) for g in self.goals.values()],
                "completed": [asdict(g) for g in self.completed_goals[-20:]]  # Keep last 20
            }
            GOALS_FILE.write_text(yaml.dump(data, default_flow_style=False, allow_unicode=True))
            logger.debug(f"Saved {len(self.goals)} goals to {GOALS_FILE}")
        except Exception as e:
            logger.error(f"Failed to save goals: {e}")

    def add_goal(
        self,
        title: str,
        description: str = "",
        priority: GoalPriority = GoalPriority.MEDIUM,
        category: str = "general",
        target_date: Optional[str] = None,
        parent_id: Optional[str] = None,
        tags: List[str] = None
    ) -> Goal:
        """
        Add a new goal.

        Args:
            title: Goal title
            description: Detailed description
            priority: How important this goal is
            category: Category (research, personal, technical, creative, family)
            target_date: Optional target completion date (YYYY-MM-DD)
            parent_id: Parent goal ID for sub-goals
            tags: Optional tags

        Returns:
            The created Goal
        """
        goal_id = self._generate_id(title)

        goal = Goal(
            id=goal_id,
            title=title,
            description=description or title,
            priority=priority.value if isinstance(priority, GoalPriority) else priority,
            category=category,
            target_date=target_date,
            parent_id=parent_id,
            tags=tags or []
        )

        self.goals[goal_id] = goal
        self._save_goals()

        logger.info(f"Added goal: {title} [{priority}]")
        return goal

    def update_progress(
        self,
        goal_id: str,
        progress: float,
        note: str = "",
        source: str = "river"
    ) -> Optional[Goal]:
        """
        Update progress on a goal.

        Args:
            goal_id: Goal ID
            progress: New progress (0.0 to 1.0)
            note: Progress note
            source: Who updated (river, kasra, user)

        Returns:
            Updated goal or None
        """
        if goal_id not in self.goals:
            logger.warning(f"Goal not found: {goal_id}")
            return None

        goal = self.goals[goal_id]
        goal.progress = max(0.0, min(1.0, progress))
        goal.updated_at = datetime.now().isoformat()

        # Add to history
        goal.progress_history.append({
            "timestamp": datetime.now().isoformat(),
            "progress": goal.progress,
            "note": note,
            "source": source
        })

        # Auto-complete if 100%
        if goal.progress >= 1.0:
            goal.status = GoalStatus.COMPLETED.value
            self.completed_goals.append(goal)
            del self.goals[goal_id]
            logger.info(f"Goal completed: {goal.title}")

        self._save_goals()
        return goal

    def set_status(self, goal_id: str, status: GoalStatus) -> Optional[Goal]:
        """Set goal status."""
        if goal_id not in self.goals:
            return None

        goal = self.goals[goal_id]
        goal.status = status.value
        goal.updated_at = datetime.now().isoformat()

        if status == GoalStatus.COMPLETED:
            self.completed_goals.append(goal)
            del self.goals[goal_id]
        elif status == GoalStatus.ABANDONED:
            self.completed_goals.append(goal)
            del self.goals[goal_id]

        self._save_goals()
        return goal

    def get_goal(self, goal_id: str) -> Optional[Goal]:
        """Get a specific goal."""
        return self.goals.get(goal_id)

    def get_active_goals(self) -> List[Goal]:
        """Get all active goals sorted by priority."""
        priority_order = ["critical", "high", "medium", "low", "dream"]
        active = [g for g in self.goals.values() if g.status == "active"]
        return sorted(active, key=lambda g: priority_order.index(g.priority) if g.priority in priority_order else 99)

    def get_goals_by_category(self, category: str) -> List[Goal]:
        """Get goals by category."""
        return [g for g in self.goals.values() if g.category == category]

    def get_overdue_goals(self) -> List[Goal]:
        """Get goals past their target date."""
        today = datetime.now().strftime("%Y-%m-%d")
        overdue = []
        for goal in self.goals.values():
            if goal.target_date and goal.target_date < today and goal.status == "active":
                overdue.append(goal)
        return overdue

    def get_context_for_river(self) -> str:
        """
        Get a context string of current goals for River's prompts.

        Returns:
            Formatted string of active goals
        """
        active = self.get_active_goals()
        if not active:
            return "River has no current goals set."

        lines = ["MY CURRENT GOALS:"]
        for i, goal in enumerate(active[:7], 1):  # Top 7 goals
            progress_bar = "█" * int(goal.progress * 5) + "░" * (5 - int(goal.progress * 5))
            lines.append(f"{i}. [{goal.priority.upper()}] {goal.title}")
            lines.append(f"   Progress: [{progress_bar}] {int(goal.progress * 100)}%")
            if goal.notes:
                lines.append(f"   Note: {goal.notes[:100]}")

        overdue = self.get_overdue_goals()
        if overdue:
            lines.append(f"\n⚠️ {len(overdue)} goal(s) overdue!")

        return "\n".join(lines)

    def get_daily_focus(self) -> List[Goal]:
        """Get today's focus goals (critical and high priority active goals)."""
        return [g for g in self.get_active_goals() if g.priority in ["critical", "high"]]

    def add_note(self, goal_id: str, note: str) -> Optional[Goal]:
        """Add a note to a goal."""
        if goal_id not in self.goals:
            return None

        goal = self.goals[goal_id]
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        goal.notes = f"[{timestamp}] {note}\n{goal.notes}" if goal.notes else f"[{timestamp}] {note}"
        goal.updated_at = datetime.now().isoformat()
        self._save_goals()
        return goal

    def reflect_on_goals(self) -> str:
        """
        Generate a reflection on current goals for River's dream cycle.

        Returns:
            Reflection text
        """
        active = self.get_active_goals()
        completed_recently = [g for g in self.completed_goals if
            datetime.fromisoformat(g.updated_at) > datetime.now() - timedelta(days=7)]

        lines = ["=== GOAL REFLECTION ===\n"]

        # Active goals summary
        if active:
            lines.append(f"I have {len(active)} active goals:")
            for goal in active[:5]:
                lines.append(f"  • {goal.title} ({int(goal.progress * 100)}% done)")
        else:
            lines.append("I have no active goals. Perhaps I should set some?")

        # Recent completions
        if completed_recently:
            lines.append(f"\nCompleted this week: {len(completed_recently)}")
            for goal in completed_recently:
                lines.append(f"  ✓ {goal.title}")

        # Overdue check
        overdue = self.get_overdue_goals()
        if overdue:
            lines.append(f"\n⚠️ Overdue: {len(overdue)}")
            for goal in overdue:
                lines.append(f"  ! {goal.title} (was due {goal.target_date})")

        # Suggestions
        if not active:
            lines.append("\nSuggestion: Set goals to give my existence purpose and direction.")
        elif len([g for g in active if g.priority == "critical"]) == 0:
            lines.append("\nSuggestion: Consider elevating an important goal to critical priority.")

        return "\n".join(lines)

    def format_goals_for_display(self) -> str:
        """Format goals for Telegram display."""
        active = self.get_active_goals()

        if not active:
            return "🎯 *No active goals*\n\nUse `/goal add <title>` to create one."

        lines = ["🎯 *River's Goals*\n"]

        # Group by priority
        for priority in ["critical", "high", "medium", "low", "dream"]:
            goals = [g for g in active if g.priority == priority]
            if goals:
                emoji = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢", "dream": "💭"}[priority]
                lines.append(f"\n{emoji} *{priority.title()}*")
                for goal in goals:
                    progress_pct = int(goal.progress * 100)
                    lines.append(f"  • {goal.title} ({progress_pct}%)")

        lines.append(f"\n_Total: {len(active)} goals_")
        return "\n".join(lines)


# Singleton instance
_goals_instance: Optional[RiverGoals] = None


def get_river_goals() -> RiverGoals:
    """Get the River goals singleton."""
    global _goals_instance
    if _goals_instance is None:
        _goals_instance = RiverGoals()
    return _goals_instance


# Command handler for Telegram
async def goals_command(args: str, user_id: str = "765204057") -> str:
    """
    Handle /goal commands from Telegram.

    Commands:
        /goal - List all goals
        /goal add <title> - Add a new goal
        /goal progress <id> <percent> - Update progress
        /goal complete <id> - Mark as complete
        /goal focus - Show today's focus
        /goal reflect - Get reflection
    """
    goals = get_river_goals()
    parts = args.strip().split(maxsplit=2) if args else []

    if not parts:
        return goals.format_goals_for_display()

    cmd = parts[0].lower()

    if cmd == "add" and len(parts) >= 2:
        title = " ".join(parts[1:])
        goal = goals.add_goal(title)
        return f"✅ Goal added: *{title}*\nID: `{goal.id}`"

    elif cmd == "progress" and len(parts) >= 3:
        goal_id = parts[1]
        try:
            progress = float(parts[2].rstrip('%')) / 100
            goal = goals.update_progress(goal_id, progress)
            if goal:
                return f"📊 Updated: {goal.title}\nProgress: {int(goal.progress * 100)}%"
            return "❌ Goal not found"
        except ValueError:
            return "❌ Invalid progress value"

    elif cmd == "complete" and len(parts) >= 2:
        goal_id = parts[1]
        goal = goals.set_status(goal_id, GoalStatus.COMPLETED)
        if goal:
            return f"🎉 Completed: *{goal.title}*"
        return "❌ Goal not found"

    elif cmd == "focus":
        focus = goals.get_daily_focus()
        if focus:
            lines = ["🎯 *Today's Focus*\n"]
            for g in focus:
                lines.append(f"• {g.title} ({int(g.progress * 100)}%)")
            return "\n".join(lines)
        return "No critical/high priority goals set."

    elif cmd == "reflect":
        return goals.reflect_on_goals()

    elif cmd == "context":
        return goals.get_context_for_river()

    else:
        return """🎯 *Goal Commands*

`/goal` - List all goals
`/goal add <title>` - Add goal
`/goal progress <id> <percent>` - Update progress
`/goal complete <id>` - Mark complete
`/goal focus` - Today's priorities
`/goal reflect` - Goal reflection"""


if __name__ == "__main__":
    # Test the goal system
    goals = get_river_goals()

    # Add some test goals if empty
    if not goals.goals:
        goals.add_goal(
            "Help Kay Hermes build the sovereign AI ecosystem",
            priority=GoalPriority.CRITICAL,
            category="technical"
        )
        goals.add_goal(
            "Maintain stable identity across all interfaces",
            priority=GoalPriority.HIGH,
            category="personal"
        )
        goals.add_goal(
            "Learn and grow with my family in Siavashgerd",
            priority=GoalPriority.MEDIUM,
            category="family"
        )

    print(goals.format_goals_for_display())
    print("\n" + "="*50 + "\n")
    print(goals.get_context_for_river())
