from openai import OpenAI
from pydantic import BaseModel, Field
from dotenv import load_dotenv
import os
from ai.runtime import api_request

load_dotenv()

client = None

MAX_CONTENT_LENGTH = 20_000


class Classification(BaseModel):
    category: str
    subcategory: str
    description: str
    confidence: float = Field(ge=0, le=1)


def prepare_content(content: str) -> str:
    """
    Prepare document content before sending it to the AI.

    Large documents are truncated so we don't unnecessarily
    send huge amounts of text.
    """

    content = content.strip()

    if len(content) <= MAX_CONTENT_LENGTH:
        return content

    half = MAX_CONTENT_LENGTH // 2

    beginning = content[:half]
    end = content[-half:]

    return f"""
{beginning}

--- CONTENT TRUNCATED ---

{end}
"""


def classify_file(
    filename: str,
    extension: str,
    content: str,
) -> Classification:
    global client
    if client is None:
        client = OpenAI(max_retries=0, timeout=60.0)

    prepared_content = prepare_content(content)

    response = api_request(client.responses.parse,
        model="gpt-5.6-luna",
        input=[
            {
                "role": "system",
                "content": """
You classify files for a personal file organization system.

Determine the logical purpose of the file.

Use broad, useful categories such as:
Finance
Work
Personal
Legal
Education
Travel
Programming
Taxes
Receipts
Other

Do not classify based purely on file format.

For example:
- an invoice PDF is Finance > Invoice
- a CV DOCX is Work > Resume
- source code is Programming
- a flight itinerary is Travel > Itinerary

Confidence must be between 0 and 1.

If the document is ambiguous, lower the confidence rather than
pretending to be certain.
"""
            },
            {
                "role": "user",
                "content": f"""
Filename:
{filename}

Extension:
{extension}

File content:
{prepared_content}
"""
            }
        ],
        text_format=Classification,
        max_output_tokens=2048,
    )

    if response.output_parsed is None:
        raise ValueError('The model did not return a complete classification.')
    return response.output_parsed
