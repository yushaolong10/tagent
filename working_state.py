"""Small, in-memory state for the active coding turn.

This is deliberately not a task manager.  It records useful execution facts
without forcing the model to maintain a todo list after arbitrary tool calls.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class WorkingState:
    goal: str = ""
    changed_files: list[str] = field(default_factory=list)
    verification: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)

    def start_turn(self, goal: str) -> None:
        self.goal = goal
        self.changed_files.clear()
        self.verification.clear()
        self.blockers.clear()

    def record_change(self, path: str) -> None:
        if path and path not in self.changed_files:
            self.changed_files.append(path)

    def record_verification(self, command: str, output: object) -> None:
        result = str(output).strip().replace("\n", " ")
        status = result[:300] if result else "completed"
        self.verification.append(f"{command}: {status}")

    def record_blocker(self, message: str) -> None:
        if message and message not in self.blockers:
            self.blockers.append(message[:300])

    def to_prompt(self) -> str:
        """Return only actionable, compact state for the current turn."""
        parts = []
        if self.goal:
            parts.append(f"Goal: {self.goal[:500]}")
        if self.changed_files:
            parts.append("Changed files: " + ", ".join(self.changed_files[-12:]))
        if self.verification:
            parts.append("Verification: " + " | ".join(self.verification[-3:]))
        if self.blockers:
            parts.append("Blockers: " + " | ".join(self.blockers[-3:]))
        return "\n".join(parts)
