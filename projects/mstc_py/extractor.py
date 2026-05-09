import google.generativeai as genai
import os
import base64
import json
import time
import tempfile
from typing import Type, TypeVar
from pydantic import BaseModel
from dotenv import load_dotenv
from markitdown import MarkItDown

load_dotenv()

# Configure Gemini
api_key = os.getenv("GEMINI_API_KEY")
if not api_key:
    raise ValueError("GEMINI_API_KEY not found in environment")

genai.configure(api_key=api_key)

T = TypeVar("T", bound=BaseModel)

def pdf_to_markdown(pdf_bytes: bytes) -> str:
    """Converts PDF bytes to Markdown text using MarkItDown."""
    md = MarkItDown()
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as temp_pdf:
        temp_pdf.write(pdf_bytes)
        temp_pdf_path = temp_pdf.name
    
    try:
        result = md.convert(temp_pdf_path)
        return result.text_content
    finally:
        if os.path.exists(temp_pdf_path):
            os.remove(temp_pdf_path)

# Ordered list of models by performance and stability (May 2026)
FALLBACK_MODELS = [
    "models/gemini-3.1-flash-lite",   # Top Choice: Fast, stable, high quota
    "models/gemini-2.5-flash",        # Second Choice: Mature and reliable
    "models/gemini-3-flash-preview", # Third Choice: High reasoning, but slower
    "models/gemma-4-31b-it"           # Final Fallback: Current default family
]

def extract_structured_data(
    pdf_bytes: bytes,
    response_model: Type[T],
    prompt: str,
    model_id: str,
    markdown_text: str
) -> T:
    """
    Core extraction logic for a single model attempt.
    """
    model = genai.GenerativeModel(model_id)
    is_gemma = "gemma" in model_id.lower()
    
    generation_config = {
        "response_mime_type": "application/json",
        "temperature": 0.0
    }
    
    if is_gemma:
        full_prompt = f"{prompt}\n\nPlease return the data in the following JSON format: {response_model.model_json_schema()}\n\nDocument Content (Markdown):\n{markdown_text}"
    else:
        full_prompt = f"{prompt}\n\nDocument Content (Markdown):\n{markdown_text}"
        generation_config["response_schema"] = response_model
    
    # Internal retry logic for transient API issues with THIS specific model
    max_api_retries = 2
    for attempt in range(max_api_retries):
        try:
            response = model.generate_content(
                full_prompt,
                generation_config=genai.GenerationConfig(**generation_config)
            )
            
            if not response or not response.text:
                raise Exception("Empty response from Gemini API")
            
            text = response.text.strip()
            
            # Robust JSON extraction
            start_idx = text.find('{')
            if start_idx != -1:
                count = 0
                for i in range(start_idx, len(text)):
                    if text[i] == '{':
                        count += 1
                    elif text[i] == '}':
                        count -= 1
                        if count == 0:
                            text = text[start_idx:i+1]
                            break
            
            return response_model.model_validate_json(text)
        except Exception as e:
            error_msg = str(e)
            is_transient = any(err in error_msg for err in ["429", "RESOURCE_EXHAUSTED", "500", "Internal error", "Service Unavailable", "deadline exceeded"])
            
            if is_transient and attempt < max_api_retries - 1:
                wait = (attempt + 1) * 20
                print(f"    [Model: {model_id}] Transient error. Waiting {wait}s before retry...")
                time.sleep(wait)
            else:
                raise e

def safe_extract(pdf_bytes, response_model, prompt):
    """
    Attempts extraction using multiple models in sequence if errors occur.
    """
    markdown_text = pdf_to_markdown(pdf_bytes)
    
    last_error = None
    for model_id in FALLBACK_MODELS:
        print(f"  -> Attempting extraction with {model_id}...")
        try:
            return extract_structured_data(pdf_bytes, response_model, prompt, model_id, markdown_text)
        except Exception as e:
            last_error = e
            print(f"     ! {model_id} failed: {str(e)[:80]}...")
            continue # Move to next model in sequence
            
    raise Exception(f"All models in fallback sequence failed. Last error: {last_error}")
