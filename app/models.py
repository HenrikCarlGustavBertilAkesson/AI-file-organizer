from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

class ActionStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    FAILED = "failed"
    EXECUTED = "executed"
    UNSUPPORTED = "unsupported"
    EMPTY = "empty"
    CLASSIFIED = "classified"

@dataclass
class File:
    path: str
    filename: str
    extension: str
    size: int
    modified: float

    hash: str = ""
    content: str = ""

    category: str = ""
    subcategory: str = ""
    description: str = ""
    confidence: float = 0.0

    status: str = ""
    error: str = ""

    is_present: bool = True

@dataclass
class ProposedAction:
    action_type: str
    source: str
    destination: str
    reason: str

    id: Optional[int] = None
    status: str = ActionStatus.PENDING
    error: str = ""

@dataclass
class AgentResult:
    message: str
    proposed_actions: list[ProposedAction]
    usage: dict = field(default_factory=dict)

@dataclass
class DetectedMove:
    old_path: str
    new_path: str

@dataclass
class ReconciliationResult:
    new_paths: list[str]
    missing_paths: list[str]
    probable_moves: list[DetectedMove] = field(default_factory=list)
    modified_paths: list[str] = field(default_factory=list)
