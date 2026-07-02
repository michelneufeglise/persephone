"""
Curated Ollama model catalog with per-hardware-tier recommendations.
Models sourced from Ollama library + HuggingFace.
"""
from __future__ import annotations

# ── Schema ──────────────────────────────────────────────────────────────────
# {
#   "id":           Ollama pull tag           (e.g. "qwen2.5:7b")
#   "name":         Human-friendly label
#   "family":       Model family / creator
#   "params":       Parameter count label     (e.g. "7B")
#   "ram_min_gb":   Minimum RAM to run        (approximate)
#   "quant":        Default quantization used by Ollama tag
#   "category":     "chat" | "vision" | "code" | "embed"
#   "description":  One-line description
#   "tags":         list of trait tags
#   "hf_url":       HuggingFace model URL (for reference)
#   "size_gb":      Download size in GB (approximate)
#   "tiers":        list of hardware tiers this model is recommended for
# }

MODELS: list[dict] = [
    # ──────────────────────────────────────────────────────────────────────
    #  INTELLIGENT DOCUMENT PROCESSING (IDP)
    # ──────────────────────────────────────────────────────────────────────

    # ── OCR — text extraction from images & scans ──────────────────────────
    {
        "id": "minicpm-v:8b", "name": "MiniCPM-V 2.6", "family": "OpenBMB",
        "params": "8B", "ram_min_gb": 6, "quant": "Q4",
        "category": "ocr",
        "description": "State-of-the-art OCR for scans, screenshots, and natural images. Strong on dense text.",
        "tags": ["ocr", "dense-text", "multilingual", "scans"],
        "hf_url": "https://huggingface.co/openbmb/MiniCPM-V-2_6",
        "size_gb": 5.5, "tiers": ["ultra", "high", "mid", "low"],
    },
    {
        "id": "qwen2.5vl:7b", "name": "Qwen 2.5 VL 7B (OCR)", "family": "Alibaba / Qwen",
        "params": "7B", "ram_min_gb": 5, "quant": "Q4_K_M",
        "category": "ocr",
        "description": "Excellent OCR across 30+ languages. Reads receipts, invoices, ID cards, packaging.",
        "tags": ["ocr", "multilingual", "receipts", "invoices"],
        "hf_url": "https://huggingface.co/Qwen/Qwen2.5-VL-7B-Instruct",
        "size_gb": 5.5, "tiers": ["ultra", "high", "mid", "low"],
    },
    {
        "id": "qwen2.5vl:32b", "name": "Qwen 2.5 VL 32B (OCR)", "family": "Alibaba / Qwen",
        "params": "32B", "ram_min_gb": 20, "quant": "Q4_K_M",
        "category": "ocr",
        "description": "Highest-quality OCR available locally. Handles complex layouts and tiny text.",
        "tags": ["ocr", "high-quality", "complex-layouts", "large"],
        "hf_url": "https://huggingface.co/Qwen/Qwen2.5-VL-32B-Instruct",
        "size_gb": 20.0, "tiers": ["ultra", "high"],
    },
    {
        "id": "granite3.2-vision:2b", "name": "Granite Vision 2B (OCR)", "family": "IBM",
        "params": "2B", "ram_min_gb": 2, "quant": "Q4_K_M",
        "category": "ocr",
        "description": "IBM's compact document AI model. Optimized for enterprise OCR tasks.",
        "tags": ["ocr", "compact", "enterprise", "fast"],
        "hf_url": "https://huggingface.co/ibm-granite/granite-vision-3.2-2b",
        "size_gb": 2.4, "tiers": ["ultra", "high", "mid", "low", "minimal"],
    },
    {
        "id": "richardyoung/olmocr2:7b-q8", "name": "OlmOCR 2 · 7B (Q8)", "family": "Allen AI / Ai2",
        "params": "7B", "ram_min_gb": 10, "quant": "Q8_0",
        "category": "ocr",
        "description": "Ai2's OlmOCR 2 — purpose-built for document OCR. Q8 quant preserves fine text detail on receipts, scans, and complex layouts.",
        "tags": ["ocr", "documents", "high-quality", "q8", "ai2"],
        "hf_url": "https://huggingface.co/allenai/olmOCR-7B-0225-preview",
        "size_gb": 7.7, "tiers": ["ultra", "high", "mid"],
    },
    {
        "id": "glm-ocr:latest", "name": "GLM-OCR", "family": "Zhipu / Tsinghua",
        "params": "~4B", "ram_min_gb": 3, "quant": "Q4_K_M",
        "category": "ocr",
        "description": "Layout-aware document OCR from the GLM family. Strong on scanned Chinese + English.",
        "tags": ["ocr", "layout-aware", "multilingual"],
        "hf_url": "https://huggingface.co/THUDM/glm-4v-9b",
        "size_gb": 2.2, "tiers": ["ultra", "high", "mid", "low"],
    },

    # ── Document AI — PDFs, forms, contracts, structured docs ──────────────
    {
        "id": "granite3.2-vision:2b", "name": "Granite Vision 2B (Docs)", "family": "IBM",
        "params": "2B", "ram_min_gb": 2, "quant": "Q4_K_M",
        "category": "docs",
        "description": "Purpose-built for PDFs, forms, charts. Trained on document AI tasks.",
        "tags": ["pdf", "forms", "charts", "enterprise"],
        "hf_url": "https://huggingface.co/ibm-granite/granite-vision-3.2-2b",
        "size_gb": 2.4, "tiers": ["ultra", "high", "mid", "low", "minimal"],
    },
    {
        "id": "qwen2.5vl:7b", "name": "Qwen 2.5 VL 7B (Docs)", "family": "Alibaba / Qwen",
        "params": "7B", "ram_min_gb": 5, "quant": "Q4_K_M",
        "category": "docs",
        "description": "Reads and reasons about PDFs, contracts, multi-page documents.",
        "tags": ["pdf", "contracts", "multi-page", "qa"],
        "hf_url": "https://huggingface.co/Qwen/Qwen2.5-VL-7B-Instruct",
        "size_gb": 5.5, "tiers": ["ultra", "high", "mid", "low"],
    },
    {
        "id": "llama3.2-vision:11b", "name": "Llama 3.2 Vision (Docs)", "family": "Meta",
        "params": "11B", "ram_min_gb": 8, "quant": "Q4_K_M",
        "category": "docs",
        "description": "Strong document understanding. Good for legal docs, invoices, reports.",
        "tags": ["pdf", "legal", "invoices", "reports"],
        "hf_url": "https://huggingface.co/meta-llama/Llama-3.2-11B-Vision-Instruct",
        "size_gb": 7.9, "tiers": ["ultra", "high", "mid"],
    },
    {
        "id": "gemma3:9b", "name": "Gemma 3 9B (Long-context docs)", "family": "Google",
        "params": "9B", "ram_min_gb": 6, "quant": "Q4_K_M",
        "category": "docs",
        "description": "128K context — load entire PDFs as text and reason about them in one shot.",
        "tags": ["long-context", "128k", "pdf-text", "summarization"],
        "hf_url": "https://huggingface.co/google/gemma-3-9b-it",
        "size_gb": 5.8, "tiers": ["ultra", "high", "mid", "low"],
    },

    # ── Handwriting — cursive, notes, forms with handwriting ───────────────
    {
        "id": "minicpm-v:8b", "name": "MiniCPM-V (Handwriting)", "family": "OpenBMB",
        "params": "8B", "ram_min_gb": 6, "quant": "Q4",
        "category": "handwriting",
        "description": "Strong handwriting recognition. Reads cursive, mixed handwriting+print.",
        "tags": ["handwriting", "cursive", "notes", "forms"],
        "hf_url": "https://huggingface.co/openbmb/MiniCPM-V-2_6",
        "size_gb": 5.5, "tiers": ["ultra", "high", "mid", "low"],
    },
    {
        "id": "qwen2.5vl:7b", "name": "Qwen 2.5 VL (Handwriting)", "family": "Alibaba / Qwen",
        "params": "7B", "ram_min_gb": 5, "quant": "Q4_K_M",
        "category": "handwriting",
        "description": "Excellent at reading handwritten notes in multiple languages.",
        "tags": ["handwriting", "multilingual", "notes"],
        "hf_url": "https://huggingface.co/Qwen/Qwen2.5-VL-7B-Instruct",
        "size_gb": 5.5, "tiers": ["ultra", "high", "mid", "low"],
    },
    {
        "id": "qwen2.5vl:32b", "name": "Qwen 2.5 VL 32B (Handwriting)", "family": "Alibaba / Qwen",
        "params": "32B", "ram_min_gb": 20, "quant": "Q4_K_M",
        "category": "handwriting",
        "description": "Top-tier handwriting recognition for difficult scripts and historical docs.",
        "tags": ["handwriting", "historical", "high-quality"],
        "hf_url": "https://huggingface.co/Qwen/Qwen2.5-VL-32B-Instruct",
        "size_gb": 20.0, "tiers": ["ultra", "high"],
    },

    # ── Tables — Excel, spreadsheets, structured data extraction ───────────
    {
        "id": "qwen2.5vl:7b", "name": "Qwen 2.5 VL (Tables)", "family": "Alibaba / Qwen",
        "params": "7B", "ram_min_gb": 5, "quant": "Q4_K_M",
        "category": "tables",
        "description": "Extracts tables from images and PDFs into structured JSON/CSV.",
        "tags": ["tables", "extraction", "json", "csv"],
        "hf_url": "https://huggingface.co/Qwen/Qwen2.5-VL-7B-Instruct",
        "size_gb": 5.5, "tiers": ["ultra", "high", "mid", "low"],
    },
    {
        "id": "qwen2.5-coder:14b", "name": "Qwen Coder (Excel formulas)", "family": "Alibaba / Qwen",
        "params": "14B", "ram_min_gb": 9, "quant": "Q4_K_M",
        "category": "tables",
        "description": "Best for writing/debugging Excel formulas, pivot tables, and VBA macros.",
        "tags": ["excel", "formulas", "vba", "pivot-tables"],
        "hf_url": "https://huggingface.co/Qwen/Qwen2.5-Coder-14B-Instruct",
        "size_gb": 9.0, "tiers": ["ultra", "high", "mid"],
    },
    {
        "id": "qwen2.5-coder:7b", "name": "Qwen Coder 7B (Spreadsheets)", "family": "Alibaba / Qwen",
        "params": "7B", "ram_min_gb": 5, "quant": "Q4_K_M",
        "category": "tables",
        "description": "Fast model for spreadsheet automation, openpyxl, pandas operations.",
        "tags": ["spreadsheets", "pandas", "openpyxl", "fast"],
        "hf_url": "https://huggingface.co/Qwen/Qwen2.5-Coder-7B-Instruct",
        "size_gb": 4.7, "tiers": ["ultra", "high", "mid", "low"],
    },
    {
        "id": "granite3.2-vision:2b", "name": "Granite Vision (Tables)", "family": "IBM",
        "params": "2B", "ram_min_gb": 2, "quant": "Q4_K_M",
        "category": "tables",
        "description": "Specialized in extracting tabular data from financial reports and scientific papers.",
        "tags": ["tables", "financial", "scientific", "compact"],
        "hf_url": "https://huggingface.co/ibm-granite/granite-vision-3.2-2b",
        "size_gb": 2.4, "tiers": ["ultra", "high", "mid", "low", "minimal"],
    },

    # ──────────────────────────────────────────────────────────────────────


    # ── Chat ────────────────────────────────────────────────────────────────
    {
        "id": "qwen3:14b", "name": "Qwen 3 14B", "family": "Alibaba / Qwen",
        "params": "14B", "ram_min_gb": 10, "quant": "Q4_K_M",
        "category": "chat",
        "description": "Powerful MoE-based model with thinking mode. Excellent reasoning + instruction following.",
        "tags": ["thinking", "multilingual", "fast", "reasoning"],
        "hf_url": "https://huggingface.co/Qwen/Qwen3-14B",
        "size_gb": 8.2, "tiers": ["ultra", "high", "mid"],
    },
    {
        "id": "qwen3:8b", "name": "Qwen 3 8B", "family": "Alibaba / Qwen",
        "params": "8B", "ram_min_gb": 6, "quant": "Q4_K_M",
        "category": "chat",
        "description": "Efficient MoE 8B. Punches well above its weight class on reasoning tasks.",
        "tags": ["thinking", "fast", "efficient"],
        "hf_url": "https://huggingface.co/Qwen/Qwen3-8B",
        "size_gb": 5.2, "tiers": ["ultra", "high", "mid", "low"],
    },
    {
        "id": "qwen3:4b", "name": "Qwen 3 4B", "family": "Alibaba / Qwen",
        "params": "4B", "ram_min_gb": 3, "quant": "Q4_K_M",
        "category": "chat",
        "description": "Ultra-fast 4B MoE model. Best quality-per-token for constrained hardware.",
        "tags": ["fast", "small", "efficient"],
        "hf_url": "https://huggingface.co/Qwen/Qwen3-4B",
        "size_gb": 2.6, "tiers": ["mid", "low", "minimal"],
    },
    {
        "id": "qwen2.5:32b", "name": "Qwen 2.5 32B", "family": "Alibaba / Qwen",
        "params": "32B", "ram_min_gb": 20, "quant": "Q4_K_M",
        "category": "chat",
        "description": "Near-frontier quality open model. Excellent for complex analysis and long-form writing.",
        "tags": ["high-quality", "large", "multilingual"],
        "hf_url": "https://huggingface.co/Qwen/Qwen2.5-32B-Instruct",
        "size_gb": 19.1, "tiers": ["ultra", "high"],
    },
    {
        "id": "qwen2.5:14b", "name": "Qwen 2.5 14B", "family": "Alibaba / Qwen",
        "params": "14B", "ram_min_gb": 9, "quant": "Q4_K_M",
        "category": "chat",
        "description": "Balanced size and quality. Strong on multilingual tasks and function calling.",
        "tags": ["balanced", "multilingual"],
        "hf_url": "https://huggingface.co/Qwen/Qwen2.5-14B-Instruct",
        "size_gb": 8.7, "tiers": ["ultra", "high", "mid"],
    },
    {
        "id": "qwen2.5:7b", "name": "Qwen 2.5 7B", "family": "Alibaba / Qwen",
        "params": "7B", "ram_min_gb": 5, "quant": "Q4_K_M",
        "category": "chat",
        "description": "Fast and capable 7B. Great everyday model with strong multilingual support.",
        "tags": ["fast", "multilingual"],
        "hf_url": "https://huggingface.co/Qwen/Qwen2.5-7B-Instruct",
        "size_gb": 4.7, "tiers": ["ultra", "high", "mid", "low"],
    },
    {
        "id": "llama3.3:70b", "name": "Llama 3.3 70B", "family": "Meta",
        "params": "70B", "ram_min_gb": 40, "quant": "Q4_K_M",
        "category": "chat",
        "description": "Meta's best open model. Near-GPT-4 quality on many benchmarks.",
        "tags": ["high-quality", "large", "frontier"],
        "hf_url": "https://huggingface.co/meta-llama/Llama-3.3-70B-Instruct",
        "size_gb": 43.0, "tiers": ["ultra"],
    },
    {
        "id": "llama3.2:3b", "name": "Llama 3.2 3B", "family": "Meta",
        "params": "3B", "ram_min_gb": 2, "quant": "Q4_K_M",
        "category": "chat",
        "description": "Meta's compact model. Very fast, surprisingly capable for daily use.",
        "tags": ["fast", "small"],
        "hf_url": "https://huggingface.co/meta-llama/Llama-3.2-3B-Instruct",
        "size_gb": 2.0, "tiers": ["low", "minimal"],
    },
    {
        "id": "gemma3:27b", "name": "Gemma 3 27B", "family": "Google",
        "params": "27B", "ram_min_gb": 17, "quant": "Q4_K_M",
        "category": "chat",
        "description": "Google's open model with 128K context. Strong on document analysis.",
        "tags": ["long-context", "high-quality"],
        "hf_url": "https://huggingface.co/google/gemma-3-27b-it",
        "size_gb": 16.5, "tiers": ["ultra", "high"],
    },
    {
        "id": "gemma3:9b", "name": "Gemma 3 9B", "family": "Google",
        "params": "9B", "ram_min_gb": 6, "quant": "Q4_K_M",
        "category": "chat",
        "description": "Google's efficient 9B model. Solid quality with 128K context.",
        "tags": ["long-context", "efficient"],
        "hf_url": "https://huggingface.co/google/gemma-3-9b-it",
        "size_gb": 5.8, "tiers": ["ultra", "high", "mid", "low"],
    },
    {
        "id": "mistral:7b", "name": "Mistral 7B", "family": "Mistral AI",
        "params": "7B", "ram_min_gb": 4, "quant": "Q4_K_M",
        "category": "chat",
        "description": "Mistral's classic model. Reliable, fast, and widely supported.",
        "tags": ["fast", "reliable"],
        "hf_url": "https://huggingface.co/mistralai/Mistral-7B-Instruct-v0.3",
        "size_gb": 4.1, "tiers": ["high", "mid", "low"],
    },
    {
        "id": "phi4:14b", "name": "Phi-4 14B", "family": "Microsoft",
        "params": "14B", "ram_min_gb": 9, "quant": "Q4_K_M",
        "category": "chat",
        "description": "Microsoft's small-but-mighty model. Outstanding STEM and coding.",
        "tags": ["reasoning", "stem", "efficient"],
        "hf_url": "https://huggingface.co/microsoft/phi-4",
        "size_gb": 8.9, "tiers": ["ultra", "high", "mid"],
    },
    {
        "id": "hermes3:8b", "name": "Hermes 3 8B", "family": "NousResearch",
        "params": "8B", "ram_min_gb": 5, "quant": "Q4_K_M",
        "category": "chat",
        "description": "Fine-tuned on Llama 3 for strong instruction following and roleplay.",
        "tags": ["roleplay", "instruction", "creative"],
        "hf_url": "https://huggingface.co/NousResearch/Hermes-3-Llama-3.1-8B",
        "size_gb": 4.7, "tiers": ["ultra", "high", "mid", "low"],
    },
    {
        "id": "qwen3.6:35b-a3b", "name": "Qwen 3.6 35B-A3B", "family": "Alibaba / Qwen",
        "params": "35B-A3B", "ram_min_gb": 20, "quant": "Q4_K_M",
        "category": "chat",
        "description": "MoE with 3B active params. Native thinking mode, 256K context, very fast for its size.",
        "tags": ["thinking", "moe", "long-context", "reasoning"],
        "hf_url": "https://huggingface.co/Qwen/Qwen3.6-35B-A3B",
        "size_gb": 23.0, "tiers": ["ultra", "high"],
    },
    {
        "id": "Hydroxide538/qwen-agentworld-35b-a3b:q4_k_m", "name": "Qwen AgentWorld 35B-A3B", "family": "Community / Qwen3.6",
        "params": "35B-A3B", "ram_min_gb": 20, "quant": "Q4_K_M",
        "category": "chat",
        "description": "AgentWorld fine-tune of Qwen 3.6 — specialised for tool use and long agentic workflows.",
        "tags": ["thinking", "moe", "agentic", "tools"],
        "hf_url": "https://huggingface.co/Hydroxide538/qwen-agentworld-35b-a3b",
        "size_gb": 22.0, "tiers": ["ultra", "high"],
    },
    {
        "id": "nemotron-3-nano:30b", "name": "Nemotron 3 Nano 30B-A3B", "family": "NVIDIA",
        "params": "30B-A3B", "ram_min_gb": 20, "quant": "Q4_K_M",
        "category": "chat",
        "description": "NVIDIA MoE reasoner — 30B total / 3B active. Reasoning + retrieval optimized.",
        "tags": ["moe", "thinking", "reasoning", "retrieval"],
        "hf_url": "https://huggingface.co/nvidia/Nemotron-3-Nano-30B",
        "size_gb": 24.0, "tiers": ["ultra", "high"],
    },
    {
        "id": "gemma4:26b", "name": "Gemma 4 26B", "family": "Google",
        "params": "26B", "ram_min_gb": 17, "quant": "Q4_K_M",
        "category": "chat",
        "description": "Google's latest Gemma. Native thinking, polished writing, balanced tone.",
        "tags": ["thinking", "writing", "polished"],
        "hf_url": "https://huggingface.co/google/gemma-4-26b-it",
        "size_gb": 17.0, "tiers": ["ultra", "high"],
    },
    {
        "id": "gemma4:12b", "name": "Gemma 4 12B", "family": "Google",
        "params": "12B", "ram_min_gb": 8, "quant": "Q4_K_M",
        "category": "chat",
        "description": "Mid-size Gemma 4. Great for daily chat, drafting and summarisation.",
        "tags": ["thinking", "writing", "balanced"],
        "hf_url": "https://huggingface.co/google/gemma-4-12b-it",
        "size_gb": 7.6, "tiers": ["ultra", "high", "mid"],
    },
    {
        "id": "laguna-xs.2:latest", "name": "Laguna XS.2", "family": "Community",
        "params": "~32B", "ram_min_gb": 20, "quant": "Q4_K_M",
        "category": "chat",
        "description": "Community fine-tune. Indie compact LLM with distinctive prose style.",
        "tags": ["community", "creative"],
        "hf_url": "https://huggingface.co/laguna-labs/laguna-xs-2",
        "size_gb": 23.0, "tiers": ["ultra", "high"],
    },
    {
        "id": "qwen3:ohm", "name": "Qwen 3 · Ohm (community)", "family": "Community / Qwen3",
        "params": "~32B", "ram_min_gb": 20, "quant": "Q4_K_M",
        "category": "chat",
        "description": "Community Qwen3 fine-tune. Solid conversational tuning with thinking mode.",
        "tags": ["thinking", "community"],
        "hf_url": "https://huggingface.co/Qwen/Qwen3-32B",
        "size_gb": 23.0, "tiers": ["ultra", "high"],
    },

    # ── Vision ──────────────────────────────────────────────────────────────
    {
        "id": "llama3.2-vision:11b", "name": "Llama 3.2 Vision 11B", "family": "Meta",
        "params": "11B", "ram_min_gb": 8, "quant": "Q4_K_M",
        "category": "vision",
        "description": "Meta's multimodal model. Excellent image understanding and document analysis.",
        "tags": ["vision", "ocr", "documents", "high-quality"],
        "hf_url": "https://huggingface.co/meta-llama/Llama-3.2-11B-Vision-Instruct",
        "size_gb": 7.9, "tiers": ["ultra", "high", "mid"],
    },
    {
        "id": "llama3.2-vision:90b", "name": "Llama 3.2 Vision 90B", "family": "Meta",
        "params": "90B", "ram_min_gb": 56, "quant": "Q4_K_M",
        "category": "vision",
        "description": "Meta's flagship vision model. Highest quality for complex image tasks.",
        "tags": ["vision", "large", "frontier"],
        "hf_url": "https://huggingface.co/meta-llama/Llama-3.2-90B-Vision-Instruct",
        "size_gb": 55.0, "tiers": ["ultra"],
    },
    {
        "id": "gemma3:27b", "name": "Gemma 3 27B (Vision)", "family": "Google",
        "params": "27B", "ram_min_gb": 17, "quant": "Q4_K_M",
        "category": "vision",
        "description": "Gemma 3 is natively multimodal — handles images and text.",
        "tags": ["vision", "long-context", "high-quality"],
        "hf_url": "https://huggingface.co/google/gemma-3-27b-it",
        "size_gb": 16.5, "tiers": ["ultra", "high"],
    },
    {
        "id": "minicpm-v", "name": "MiniCPM-V", "family": "OpenBMB",
        "params": "8B", "ram_min_gb": 6, "quant": "Q4",
        "category": "vision",
        "description": "Lightweight but powerful vision model. Great for on-device use.",
        "tags": ["vision", "efficient", "ocr"],
        "hf_url": "https://huggingface.co/openbmb/MiniCPM-V-2_6",
        "size_gb": 5.5, "tiers": ["ultra", "high", "mid", "low"],
    },
    {
        "id": "qwen2.5vl:7b", "name": "Qwen 2.5 VL 7B", "family": "Alibaba / Qwen",
        "params": "7B", "ram_min_gb": 5, "quant": "Q4_K_M",
        "category": "vision",
        "description": "Strong vision-language model. Excellent at charts, tables, and reasoning.",
        "tags": ["vision", "charts", "reasoning"],
        "hf_url": "https://huggingface.co/Qwen/Qwen2.5-VL-7B-Instruct",
        "size_gb": 5.5, "tiers": ["ultra", "high", "mid", "low"],
    },
    {
        "id": "moondream", "name": "Moondream 2", "family": "Vikhyat Koperwas",
        "params": "1.86B", "ram_min_gb": 2, "quant": "Q4",
        "category": "vision",
        "description": "Tiny but useful vision model. Fast for basic image captioning.",
        "tags": ["vision", "tiny", "fast"],
        "hf_url": "https://huggingface.co/vikhyatk/moondream2",
        "size_gb": 1.7, "tiers": ["low", "minimal"],
    },
    {
        "id": "minicpm-v:latest", "name": "MiniCPM-V (latest)", "family": "OpenBMB",
        "params": "8B", "ram_min_gb": 6, "quant": "Q4",
        "category": "vision",
        "description": "Compact multimodal — great for image Q&A on modest hardware.",
        "tags": ["vision", "efficient", "multilingual"],
        "hf_url": "https://huggingface.co/openbmb/MiniCPM-V-2_6",
        "size_gb": 5.5, "tiers": ["ultra", "high", "mid", "low"],
    },
    {
        "id": "openbmb/minicpm-o2.6:8b", "name": "MiniCPM-O 2.6 8B", "family": "OpenBMB",
        "params": "8B", "ram_min_gb": 6, "quant": "Q4",
        "category": "vision",
        "description": "Omnimodal — image, audio, and text in one model. Edge-friendly.",
        "tags": ["vision", "audio", "multimodal"],
        "hf_url": "https://huggingface.co/openbmb/MiniCPM-o-2_6",
        "size_gb": 5.5, "tiers": ["ultra", "high", "mid", "low"],
    },

    # ── Code ────────────────────────────────────────────────────────────────
    {
        "id": "ornith:latest", "name": "Ornith 9B (Agentic Coder)", "family": "Community / Qwen3",
        "params": "9B", "ram_min_gb": 6, "quant": "Q4_K_M",
        "category": "code",
        "description": "Qwen3-based agentic coder — 262K context, native tools + thinking. Persephone terminal default.",
        "tags": ["code", "agentic", "tools", "thinking", "long-context"],
        "hf_url": "https://huggingface.co/spawn/Ornith-9B",
        "size_gb": 5.6, "tiers": ["ultra", "high", "mid", "low"],
    },
    {
        "id": "qwen2.5-coder:32b", "name": "Qwen 2.5 Coder 32B", "family": "Alibaba / Qwen",
        "params": "32B", "ram_min_gb": 20, "quant": "Q4_K_M",
        "category": "code",
        "description": "Best-in-class open code model. Rivals GPT-4o for coding tasks.",
        "tags": ["code", "large", "frontier"],
        "hf_url": "https://huggingface.co/Qwen/Qwen2.5-Coder-32B-Instruct",
        "size_gb": 19.8, "tiers": ["ultra", "high"],
    },
    {
        "id": "qwen2.5-coder:14b", "name": "Qwen 2.5 Coder 14B", "family": "Alibaba / Qwen",
        "params": "14B", "ram_min_gb": 9, "quant": "Q4_K_M",
        "category": "code",
        "description": "Excellent coding model. Great balance of speed and quality.",
        "tags": ["code", "balanced"],
        "hf_url": "https://huggingface.co/Qwen/Qwen2.5-Coder-14B-Instruct",
        "size_gb": 9.0, "tiers": ["ultra", "high", "mid"],
    },
    {
        "id": "qwen2.5-coder:7b", "name": "Qwen 2.5 Coder 7B", "family": "Alibaba / Qwen",
        "params": "7B", "ram_min_gb": 5, "quant": "Q4_K_M",
        "category": "code",
        "description": "Fast code-focused model. Strong for everyday coding assistance.",
        "tags": ["code", "fast"],
        "hf_url": "https://huggingface.co/Qwen/Qwen2.5-Coder-7B-Instruct",
        "size_gb": 4.7, "tiers": ["ultra", "high", "mid", "low"],
    },
    {
        "id": "deepseek-coder-v2:16b", "name": "DeepSeek Coder V2 16B", "family": "DeepSeek",
        "params": "16B", "ram_min_gb": 10, "quant": "Q4_K_M",
        "category": "code",
        "description": "MoE architecture makes this very efficient. 338 languages supported.",
        "tags": ["code", "moe", "efficient"],
        "hf_url": "https://huggingface.co/deepseek-ai/DeepSeek-Coder-V2-Lite-Instruct",
        "size_gb": 9.1, "tiers": ["ultra", "high", "mid"],
    },
    {
        "id": "codellama:13b", "name": "Code Llama 13B", "family": "Meta",
        "params": "13B", "ram_min_gb": 8, "quant": "Q4_K_M",
        "category": "code",
        "description": "Meta's code model. Good for Python, JavaScript, and infill tasks.",
        "tags": ["code", "infill"],
        "hf_url": "https://huggingface.co/meta-llama/CodeLlama-13b-Instruct-hf",
        "size_gb": 7.4, "tiers": ["high", "mid"],
    },

    # ── Judge / Router classifier (auto-route brain) ─────────────────────────
    # These are deliberately tiny — they classify each user message into a
    # category (trivial / code / tools / reasoning / short / default) so the
    # router can pick the best chat model. They're invoked alongside the real
    # response so they MUST be fast: 50–250ms warm is the target.
    {
        "id": "qwen2.5:0.5b", "name": "Qwen 2.5 · 0.5B (Judge)", "family": "Alibaba / Qwen",
        "params": "0.5B", "ram_min_gb": 1, "quant": "Q4_K_M",
        "category": "judge",
        "description": "Smallest viable classifier — runs anywhere, ~30ms classifications. Best for minimal hardware.",
        "tags": ["judge", "tiny", "fastest", "ultra-low-ram"],
        "hf_url": "https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct",
        "size_gb": 0.4, "tiers": ["minimal", "low"],
    },
    {
        "id": "qwen2.5:1.5b", "name": "Qwen 2.5 · 1.5B (Judge)", "family": "Alibaba / Qwen",
        "params": "1.5B", "ram_min_gb": 2, "quant": "Q4_K_M",
        "category": "judge",
        "description": "Recommended judge — small enough to be near-instant, big enough to follow the classifier prompt reliably. ~100ms warm.",
        "tags": ["judge", "recommended", "fast", "accurate"],
        "hf_url": "https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct",
        "size_gb": 1.0, "tiers": ["ultra", "high", "mid", "low"],
    },
    {
        "id": "llama3.2:3b", "name": "Llama 3.2 · 3B (Judge)", "family": "Meta",
        "params": "3B", "ram_min_gb": 3, "quant": "Q4_K_M",
        "category": "judge",
        "description": "Most-accurate judge — slightly slower (~200ms) but excellent at edge-case classification. Good for ambiguous queries.",
        "tags": ["judge", "accurate", "instruction-following"],
        "hf_url": "https://huggingface.co/meta-llama/Llama-3.2-3B-Instruct",
        "size_gb": 2.0, "tiers": ["ultra", "high", "mid"],
    },

    # ── Embedding ────────────────────────────────────────────────────────────
    {
        "id": "mxbai-embed-large", "name": "mxbai-embed-large", "family": "MixedBread AI",
        "params": "335M", "ram_min_gb": 1, "quant": "F16",
        "category": "embed",
        "description": "State-of-the-art embedding model. Top MTEB benchmark scores.",
        "tags": ["embedding", "retrieval", "fast"],
        "hf_url": "https://huggingface.co/mixedbread-ai/mxbai-embed-large-v1",
        "size_gb": 0.67, "tiers": ["ultra", "high", "mid", "low", "minimal"],
    },
    {
        "id": "nomic-embed-text", "name": "nomic-embed-text", "family": "Nomic AI",
        "params": "137M", "ram_min_gb": 1, "quant": "F16",
        "category": "embed",
        "description": "Fast and capable embedding model. Great for semantic search.",
        "tags": ["embedding", "fast", "small"],
        "hf_url": "https://huggingface.co/nomic-ai/nomic-embed-text-v1.5",
        "size_gb": 0.27, "tiers": ["ultra", "high", "mid", "low", "minimal"],
    },
    {
        "id": "bge-m3", "name": "BGE-M3", "family": "BAAI",
        "params": "568M", "ram_min_gb": 1, "quant": "F16",
        "category": "embed",
        "description": "Multi-lingual, multi-task embedding. Best for non-English content.",
        "tags": ["embedding", "multilingual"],
        "hf_url": "https://huggingface.co/BAAI/bge-m3",
        "size_gb": 0.58, "tiers": ["ultra", "high", "mid", "low", "minimal"],
    },
]


CATEGORIES = ("chat", "vision", "code", "embed", "ocr", "docs", "handwriting", "tables", "judge")


def get_recommendations(tier: str, installed_ids: set[str]) -> dict:
    """Return tier-filtered recommendations grouped by category."""
    result: dict[str, list[dict]] = {c: [] for c in CATEGORIES}
    for m in MODELS:
        if tier in m["tiers"]:
            # Mark installed if exact match OR if base name matches (e.g. "qwen2.5vl:7b" matches "qwen2.5vl")
            base = m["id"].split(":")[0]
            installed = m["id"] in installed_ids or any(x.startswith(base + ":") or x == base for x in installed_ids)
            entry = {**m, "installed": installed}
            result[m["category"]].append(entry)
    return result
