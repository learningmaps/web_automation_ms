# Project Instructions

## Structure
- **Maintenance Folder**: All testing, local validation, and maintenance scripts must be stored in the `/maintenance` directory.
- **Git Safety**: The `/maintenance` folder is intended for local use only and must always be excluded from source control via `.gitignore`. us this folder to perform all the  tests.

## Extraction Workflow
- **Markdown Conversion**: Before sending data to the LLM for extraction, always convert PDFs to Markdown using `markitdown`.
- **Sequential Fallback Strategy**: The system attempts extraction using a tiered list of models to maximize reliability. If a model fails, it automatically falls back to the next one in the following order:
  1. `models/gemini-3.1-flash-lite` (Priority: High speed and stability)
  2. `models/gemini-2.5-flash` (Priority: Mature reliability)
  3. `models/gemini-3-flash-preview` (Priority: Deep reasoning)
  4. `models/gemma-4-31b-it` (Final fallback)
- **Factuality**: Set `temperature=0.0` in the generation config to ensure deterministic and factual outputs.

## Available Models (Reference)
- `models/gemini-2.5-flash`
- `models/gemini-2.5-pro`
- `models/gemini-2.0-flash`
- `models/gemini-2.0-flash-001`
- `models/gemini-2.0-flash-lite-001`
- `models/gemini-2.0-flash-lite`
- `models/gemini-2.5-flash-preview-tts`
- `models/gemini-2.5-pro-preview-tts`
- `models/gemma-4-26b-a4b-it`
- `models/gemma-4-31b-it`
- `models/gemini-flash-latest`
- `models/gemini-flash-lite-latest`
- `models/gemini-pro-latest`
- `models/gemini-2.5-flash-lite`
- `models/gemini-2.5-flash-image`
- `models/gemini-3-pro-preview`
- `models/gemini-3-flash-preview`
- `models/gemini-3.1-pro-preview`
- `models/gemini-3.1-pro-preview-customtools`
- `models/gemini-3.1-flash-lite-preview`
- `models/gemini-3.1-flash-lite`
- `models/gemini-3-pro-image-preview`
- `models/nano-banana-pro-preview`
- `models/gemini-3.1-flash-image-preview`
- `models/lyria-3-clip-preview`
- `models/lyria-3-pro-preview`
- `models/gemini-3.1-flash-tts-preview`
- `models/gemini-robotics-er-1.5-preview`
- `models/gemini-robotics-er-1.6-preview`
- `models/gemini-2.5-computer-use-preview-10-2025`
- `models/deep-research-max-preview-04-2026`
- `models/deep-research-preview-04-2026`
- `models/deep-research-pro-preview-12-2025`
- `models/gemini-embedding-001`
- `models/gemini-embedding-2-preview`
- `models/gemini-embedding-2`
- `models/aqa`
- `models/imagen-4.0-generate-001`
- `models/imagen-4.0-ultra-generate-001`
- `models/imagen-4.0-fast-generate-001`
- `models/veo-2.0-generate-001`
- `models/veo-3.0-generate-001`
- `models/veo-3.0-fast-generate-001`
- `models/veo-3.1-generate-preview`
- `models/veo-3.1-fast-generate-preview`
- `models/veo-3.1-lite-generate-preview`
- `models/gemini-2.5-flash-native-audio-latest`
- `models/gemini-2.5-flash-native-audio-preview-09-2025`
- `models/gemini-2.5-flash-native-audio-preview-12-2025`
- `models/gemini-3.1-flash-live-preview`
