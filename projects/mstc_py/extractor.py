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

def extract_structured_data(
    pdf_bytes: bytes,
    response_model: Type[T],
    prompt: str = "Extract the requested information from the attached document."
) -> T:
    """
    Converts PDF to Markdown and extracts structured data using Gemini 3 Flash.
    """
    markdown_text = pdf_to_markdown(pdf_bytes)
    
    model = genai.GenerativeModel("models/gemma-4-31b-it")
    model_name = model.model_name
    is_gemma = "gemma" in model_name.lower()
    
    # For Gemma models, we pass the schema in the prompt to avoid "Unknown field for Schema: default" or 500 errors
    # For Gemini models, we use the SDK's response_schema for better reliability
    generation_config = {
        "response_mime_type": "application/json",
        "temperature": 0.0
    }
    
    if is_gemma:
        full_prompt = f"{prompt}\n\nPlease return the data in the following JSON format: {response_model.model_json_schema()}\n\nDocument Content (Markdown):\n{markdown_text}"
    else:
        full_prompt = f"{prompt}\n\nDocument Content (Markdown):\n{markdown_text}"
        generation_config["response_schema"] = response_model
    
    # Internal retry logic for the specific API call
    max_api_retries = 3
    for attempt in range(max_api_retries):
        try:
            response = model.generate_content(
                full_prompt,
                generation_config=genai.GenerationConfig(**generation_config)
            )
            
            # Clean up response text in case the model wrapped it in markdown code blocks or added trailing text
            text = response.text.strip()
            
            # Robust JSON extraction: Find the first '{' and the last '}'
            start_idx = text.find('{')
            end_idx = text.rfind('}')
            
            if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
                text = text[start_idx:end_idx + 1]
            
            return response_model.model_validate_json(text)
        except Exception as e:
            if ("429" in str(e) or "RESOURCE_EXHAUSTED" in str(e)) and attempt < max_api_retries - 1:
                wait = (attempt + 1) * 60 # Wait 1 min, then 2 mins
                print(f"    [Gemini API] Rate limit hit. Waiting {wait}s before retry {attempt + 1}...")
                time.sleep(wait)
            else:
                raise e

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
