from dataclasses import dataclass


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

@dataclass
class ProposedAction:
    action_type: str
    source: str
    destination: str
    reason: str

    approved: bool = False
    executed: bool = False
    error: str = ""

@dataclass
class AgentResult:
    message: str
    proposed_actions: list[ProposedAction]