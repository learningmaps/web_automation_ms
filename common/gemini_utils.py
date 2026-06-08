"""Shared Gemini extraction infrastructure used across all projects.

Provides model fallback, key rotation, retry with backoff, and JSON parsing.
"""
import google.generativeai as genai
import os
import time
from typing import Type, TypeVar
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

primary_key = os.getenv("GEMINI_API_KEY")
secondary_key = os.getenv("GEMINI_API_KEY_D")

if not primary_key:
    raise ValueError("GEMINI_API_KEY not found in environment")

T = TypeVar("T", bound=BaseModel)

FALLBACK_MODELS = [
    "models/gemini-3.1-flash-lite",
    "models/gemini-2.5-flash",
    "models/gemini-3-flash-preview",
    "models/gemma-4-31b-it"
]


def extract_structured_data(
    response_model: Type[T],
    prompt: str,
    model_id: str,
    text_content: str,
    api_key: str,
    content_label: str = "Document Content (Markdown)",
) -> T:
    """
    Core extraction for a single model attempt with a specific API key.

    Args:
        response_model: Pydantic model for response validation.
        prompt: Task instruction prompt.
        model_id: Gemini model ID (e.g. "models/gemini-2.5-flash").
        text_content: The text content to analyse.
        api_key: Gemini API key.
        content_label: Label prepended to the text block in the prompt.

    Returns:
        An instance of response_model parsed from the model output.
    """
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(model_id)
    is_gemma = "gemma" in model_id.lower()

    generation_config = {
        "response_mime_type": "application/json",
        "temperature": 0.0,
    }

    full_prompt = (
        f"{prompt}\n\n"
        f"Please return the data in the following JSON format: "
        f"{response_model.model_json_schema()}\n\n"
        f"{content_label}:\n{text_content}"
    )

    max_api_retries = 2
    for attempt in range(max_api_retries):
        try:
            response = model.generate_content(
                full_prompt,
                generation_config=genai.GenerationConfig(**generation_config),
            )

            if not response or not response.text:
                raise Exception("Empty response from Gemini API")

            text = response.text.strip()

            start_idx = text.find("{")
            if start_idx != -1:
                count = 0
                for i in range(start_idx, len(text)):
                    if text[i] == "{":
                        count += 1
                    elif text[i] == "}":
                        count -= 1
                        if count == 0:
                            text = text[start_idx : i + 1]
                            break

            return response_model.model_validate_json(text)
        except Exception as e:
            error_msg = str(e)
            is_transient = any(
                err in error_msg
                for err in [
                    "429",
                    "RESOURCE_EXHAUSTED",
                    "500",
                    "Internal error",
                    "Service Unavailable",
                    "deadline exceeded",
                ]
            )

            if is_transient and attempt < max_api_retries - 1:
                wait = (attempt + 1) * 20
                print(
                    f"    [Model: {model_id}] Transient error. "
                    f"Waiting {wait}s before retry..."
                )
                time.sleep(wait)
            else:
                raise e


def safe_extract_text(
    text_content: str,
    response_model: Type[T],
    prompt: str,
    content_label: str = "Document Content (Markdown)",
) -> T:
    """
    Try multiple models and multiple API keys in sequence.

    Strategy: for each model try the primary key, then the secondary key,
    before moving to the next model.

    Args:
        text_content: The text to analyse.
        response_model: Pydantic model for response validation.
        prompt: Task instruction prompt.
        content_label: Label prepended to the text block.

    Returns:
        An instance of response_model.
    """
    keys = [primary_key]
    if secondary_key:
        keys.append(secondary_key)

    last_error = None
    for model_id in FALLBACK_MODELS:
        for i, key in enumerate(keys):
            key_label = "Primary" if i == 0 else "Secondary"
            print(f"  -> Attempting {model_id} with {key_label} Key...")
            try:
                return extract_structured_data(
                    response_model, prompt, model_id, text_content, key, content_label
                )
            except Exception as e:
                last_error = e
                print(
                    f"     ! {key_label} Key failed for {model_id}: "
                    f"{str(e)[:80]}..."
                )
                continue

    raise Exception(
        f"All models and keys in fallback sequence failed. "
        f"Last error: {last_error}"
    )


def safe_extract(
    pdf_bytes: bytes,
    response_model: Type[T],
    prompt: str,
) -> T:
    """
    Legacy wrapper: accepts PDF bytes, converts to markdown text first.

    Used by the mstc project.  New callers should prefer ``safe_extract_text``
    directly.
    """
    from common.document_processing import convert_pdf_to_markdown

    text = convert_pdf_to_markdown(pdf_bytes)
    return safe_extract_text(text, response_model, prompt)
