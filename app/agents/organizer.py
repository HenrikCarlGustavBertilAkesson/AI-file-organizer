from __future__ import annotations

import json
from pathlib import Path

from openai import OpenAI
from dataclasses import asdict, is_dataclass
from models import ProposedAction, AgentResult

from tools.file_tools import (
    list_files,
    read_file,
    get_indexed_files,
    classify_path,
    propose_move,
)


client = OpenAI()

MAX_AGENT_STEPS = 15

TOOLS = [
    {
        "type": "function",
        "name": "list_files",
        "description": (
            "List files contained in a directory recursively. "
            "Use this when you need to inspect what files exist."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "directory": {
                    "type": "string",
                    "description": "Absolute or relative directory path.",
                }
            },
            "required": ["directory"],
            "additionalProperties": False,
        },
        "strict": True,
    },

    {
        "type": "function",
        "name": "read_file",
        "description": (
            "Extract and return readable text from a file."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path of the file to read.",
                }
            },
            "required": ["path"],
            "additionalProperties": False,
        },
        "strict": True,
    },

    {
        "type": "function",
        "name": "get_indexed_files",
        "description": (
            "Return previously indexed files and their classifications."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        "strict": True,
    },

    {
        "type": "function",
        "name": "classify_path",
        "description": (
            "Classify a file based on its contents."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                }
            },
            "required": ["path"],
            "additionalProperties": False,
        },
        "strict": True,
    },

    {
        "type": "function",
        "name": "propose_move",
        "description": (
            "Propose moving a file to another location. "
            "This does not actually move the file."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                },
                "destination": {
                    "type": "string",
                },
                "reason": {
                    "type": "string",
                },
            },
            "required": [
                "source",
                "destination",
                "reason",
            ],
            "additionalProperties": False,
        },
        "strict": True,
    },
]

SYSTEM_PROMPT = """
You are an AI file organization agent.

Your job is to inspect files and recommend how they should be organized.

You have tools that allow you to:
- list files
- read files
- inspect existing classifications
- classify individual files
- propose file moves

Important rules:

1. Never invent file paths.
2. Inspect files when you do not have enough information.
3. Prefer existing folder structures when sensible.
4. Never claim that a file has been moved.
5. propose_move only creates a proposal and requires user approval.
6. Do not propose moves unless you have enough information to justify them.
7. All proposed destinations must remain within the user's allowed organization directory.
8. Do not propose moving files outside the user's allowed directory.
9. Do not assume that proposing a move executes it.
10. All moves require explicit user approval.
"""

def call_tool(name: str, arguments: dict, allowed_root: str | None = None):
    if allowed_root:
        root = Path(allowed_root).resolve()
        for key in ("path", "directory", "source", "destination"):
            if key in arguments:
                path = Path(arguments[key]).expanduser().resolve()
                if not path.is_relative_to(root):
                    raise ValueError(f"{key} is outside the selected folder")
                arguments[key] = str(path)
        if name == "get_indexed_files":
            return [row for row in get_indexed_files()
                    if Path(row["path"]).resolve().is_relative_to(root)]
        if name == "list_files":
            return [row for row in list_files(**arguments)
                    if Path(row["path"]).resolve().is_relative_to(root)]
    if name == "list_files":
        return list_files(**arguments)

    if name == "read_file":
        return read_file(**arguments)

    if name == "get_indexed_files":
        return get_indexed_files()

    if name == "classify_path":
        return classify_path(**arguments)

    if name == "propose_move":
        return propose_move(**arguments)

    raise ValueError(f"Unknown tool: {name}")

def run_agent(user_request: str, allowed_root: str | None = None) -> AgentResult:
    input_messages = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT,
        },
        {
            "role": "user",
            "content": user_request,
        },
    ]

    proposals = []

    for _ in range(MAX_AGENT_STEPS):
        response = client.responses.create(
            model="gpt-5.6-sol",
            tools=TOOLS,
            input=input_messages,
        )

        input_messages += response.output

        tool_called = False

        for item in response.output:
            if item.type != "function_call":
                continue

            tool_called = True

            arguments = json.loads(item.arguments)

            print(
                f"\nAgent calling: "
                f"{item.name}({arguments})"
            )

            result = call_tool(
                item.name,
                arguments,
                allowed_root,
            )

            if isinstance(result, ProposedAction):
                proposals.append(result)

            if is_dataclass(result):
                result_for_ai = asdict(result)
            else:
                result_for_ai = result

            input_messages.append(
                {
                    "type": "function_call_output",
                    "call_id": item.call_id,
                    "output": json.dumps(result_for_ai),
                }
            )

        if not tool_called:
            return AgentResult(
                message=response.output_text,
                proposed_actions=proposals
            )

    return AgentResult(
            message=(
                "Agent stopped because it reached "
                "the maximum number of steps."
            ),
            proposed_actions=proposals,
        )
