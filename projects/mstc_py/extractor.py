import google.generativeai as genai
import os
import base64
import json
import time
from typing import Type, TypeVar
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

# Configure Gemini
api_key = os.getenv("GEMINI_API_KEY")
if not api_key:
    raise ValueError("GEMINI_API_KEY not found in environment")

genai.configure(api_key=api_key)

T = TypeVar("T", bound=BaseModel)

def extract_structured_data(
    pdf_bytes: bytes,
    response_model: Type[T],
    prompt: str = "Extract the requested information from the attached document."
) -> T:
    """
    Extracts structured data from a PDF using Gemini's multimodal capabilities.
    """
    model = genai.GenerativeModel("gemini-1.5-flash-latest")
    
    # Upload to Gemini File API or send as inline bytes
    # For smaller files/one-offs, inline is fine. 
    # For a robust pipeline, we can use the File API.
    
    response = model.generate_content(
        [
            prompt,
            {
                "mime_type": "application/pdf",
                "data": base64.b64encode(pdf_bytes).decode("utf-8")
            }
        ],
        generation_config=genai.GenerationConfig(
            response_mime_type="application/json",
            response_schema=response_model.model_json_schema()
        )
    )
    
    return response_model.model_validate_json(response.text)

def safe_extract(pdf_bytes, response_model, prompt, max_retries=5):
    """
    Wrapper with exponential backoff for rate limits.
    """
    attempt = 0
    while attempt < max_retries:
        try:
            return extract_structured_data(pdf_bytes, response_model, prompt)
        except Exception as e:
            if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                attempt += 1
                wait_time = (2 ** attempt) + 30
                print(f"  -> Rate limit hit. Waiting {wait_time}s...")
                time.sleep(wait_time)
            else:
                raise e
    raise Exception("Max retries exceeded for Gemini extraction")
