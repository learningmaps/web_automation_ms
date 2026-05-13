import google.generativeai as genai
import os
import time
from typing import Type, TypeVar
from pydantic import BaseModel
from dotenv import load_dotenv
from common.document_processing import convert_pdf_to_markdown

load_dotenv()

# Configure Gemini
primary_key = os.getenv("GEMINI_API_KEY")
secondary_key = os.getenv("GEMINI_API_KEY_D")

if not primary_key:
    raise ValueError("GEMINI_API_KEY not found in environment")

T = TypeVar("T", bound=BaseModel)

# Ordered list of models by performance and stability
FALLBACK_MODELS = [
    "models/gemini-3.1-flash-lite",
    "models/gemini-2.5-flash",
    "models/gemini-3-flash-preview",
    "models/gemma-4-31b-it"
]

def extract_structured_data(
    pdf_bytes: bytes,
    response_model: Type[T],
    prompt: str,
    model_id: str,
    markdown_text: str,
    api_key: str
) -> T:
    """
    Core extraction logic for a single model attempt with a specific API key.
    """
    genai.configure(api_key=api_key)
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
    
    # Internal retry logic for transient API issues with THIS specific model/key combo
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
    Attempts extraction using multiple models and multiple API keys in sequence.
    Strategy: For each model, try Key 1, then Key 2, before moving to the next model.
    """
    markdown_text = convert_pdf_to_markdown(pdf_bytes)
    
    keys = [primary_key]
    if secondary_key:
        keys.append(secondary_key)
        
    last_error = None
    for model_id in FALLBACK_MODELS:
        for i, key in enumerate(keys):
            key_label = "Primary" if i == 0 else "Secondary"
            print(f"  -> Attempting {model_id} with {key_label} Key...")
            try:
                return extract_structured_data(pdf_bytes, response_model, prompt, model_id, markdown_text, key)
            except Exception as e:
                last_error = e
                print(f"     ! {key_label} Key failed for {model_id}: {str(e)[:80]}...")
                continue # Try the next key for this model, or next model
            
    raise Exception(f"All models and keys in fallback sequence failed. Last error: {last_error}")
