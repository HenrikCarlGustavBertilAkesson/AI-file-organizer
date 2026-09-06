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