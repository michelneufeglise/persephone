# Hardware-Aware Model Recommendation

Goal: the setup wizard should hand the user a **short, honest list of Ollama
models that will actually run at ≥ 20 tok/s on their specific laptop**, drawn
from the full range they asked for — DeepSeek, Gemma (multiple sizes), Qwen
(multiple), MoE thinkers, vision models, embeddings.

The current wizard picks by a coarse "RAM tier" (`low / mid / high / ultra`)
which lumps very different machines together — an M1 Air 16GB and a
Ryzen laptop with 16GB DDR5 both land in "mid" but their real throughput
differs by ~10×. This plan replaces that with a per-model tok/s estimate
based on a more detailed hardware fingerprint.

## Architecture

```
Wizard (frontend)
   ↓ GET /api/setup/hardware-profile
   ↓ GET /api/setup/optimized-models
Backend
   • hardware.py  → get_profile()  ← extended: chip family, mem BW, cores
   • benchmarks.py → estimate_tok_per_s(model, profile)  ← NEW module
   • model_catalog.py → full curated list of families
   • main.py → new /api/setup/optimized-models endpoint
                    combines catalog × profile × benchmark → ranked list
```

## Hardware fingerprint (extended)

| Field                | Source                                       |
|----------------------|----------------------------------------------|
| `chip_family`        | "apple_silicon_m1", "apple_silicon_m2", …,   |
|                      | "intel_11th", "intel_12th", "amd_zen4", …    |
| `chip_variant`       | "base", "pro", "max", "ultra" (Apple)        |
| `ram_gb`             | already there                                |
| `mem_bandwidth_gb_s` | new — 68/200/400/800 GB/s ballparks per chip |
| `perf_cores`         | new — physical performance cores             |
| `gpu_vram_gb`        | already there (discrete GPU)                 |
| `os` / `arch`        | already there                                |

Memory bandwidth is the primary throughput bottleneck for LLM inference on
consumer hardware. Rough map:

| Chip                      | Bandwidth        |
|---------------------------|------------------|
| M1 base / M2 base         | ~68 GB/s         |
| M1 Pro / M2 Pro           | ~200 GB/s        |
| M1 Max / M2 Max           | ~400 GB/s        |
| M3 base                   | ~100 GB/s        |
| M3 Pro                    | ~150 GB/s        |
| M3 Max                    | ~300-400 GB/s    |
| M4 base / Pro / Max       | ~120 / 273 / 546 |
| Intel/AMD w/o GPU (DDR4)  | ~50-60           |
| Intel/AMD w/o GPU (DDR5)  | ~80-100          |
| RTX 4060 / 4070 / 4080    | 272 / 504 / 717  |
| RTX 4090                  | 1008             |

## Tok/s estimation

Formula:
```
tok_per_s ≈ (bandwidth_gb_s * 0.8) / model_size_gb
```
The 0.8 factor accounts for real-world efficiency losses (KV cache,
activations, context length overhead). For MoE models we use *active*
parameter size, not total (Qwen3.6-35B-A3B has 22GB total but only
~1.9GB active per token).

Then we clip against measured floors — some model/hardware combos are
known to underperform the theoretical estimate (long-context models
attention memory pressure, quant-specific effects). See
`benchmarks.py:_OVERRIDES` for the exception table.

## Model families to include

Required per user request + sensible extras:

### Chat (general purpose)
- `deepseek-r1:32b` (reasoning, dense, 20GB)
- `deepseek-r1:70b` (reasoning, dense, 43GB)
- `gemma3:9b` `gemma3:27b` `gemma4:12b` `gemma4:26b`
- `qwen2.5:7b` `qwen2.5:14b` `qwen2.5:32b`
- `llama3.3:70b`

### MoE thinking models (the star of the show)
- `qwen3.6:35b-a3b` (Alibaba MoE, 3B active)
- `Hydroxide538/qwen-agentworld-35b-a3b:q4_k_m` (agentic fine-tune)
- `nemotron-3-nano:30b` (NVIDIA MoE reasoner)
- `hf.co/InternScience/Agents-A1-Q4_K_M-GGUF:latest` (agentic + tools)

### Emotional / creative
- `hf.co/mradermacher/L3.3-70B-Euryale-v2.3-GGUF:q4_k_m` (companion)

### Coder
- `qwen2.5-coder:7b` `qwen2.5-coder:14b`
- `ornith:latest` (Qwen3-based agentic coder, 262K context)

### Vision
- `minicpm-v:latest` (small, fast)
- `qwen2.5vl:7b` `qwen2.5vl:32b`
- `llama3.2-vision:latest`

### Embeddings
- `mxbai-embed-large` (already installed by most)
- `nomic-embed-text` (fast alternative)

### Speech
- `hexgrad/Kokoro-82M` (already the TTS)

## API surface

```
GET  /api/setup/hardware-profile
     → { chip_family, chip_variant, ram_gb, mem_bandwidth_gb_s,
         perf_cores, gpu_vram_gb, tier, os, arch }

GET  /api/setup/optimized-models
     → {
         profile: { … },
         min_tok_per_s: 20,
         categories: {
           chat:    [ { id, name, size_gb, tok_per_s_est, fit: "top|good|acceptable|slow", … } ],
           thinker: [ … ],
           coder:   [ … ],
           vision:  [ … ],
           embed:   [ … ],
         }
       }
```

## UI changes

`ModelStep.tsx` gets:
- Colored tok/s badge on each card (green = ≥20, amber = 10-20, red = <10)
- Sort by fit rating within each tier
- Filter chip: "hide models slower than 20 tok/s"

`SummaryStep.tsx` and the wizard-complete handler:
- Verify Kokoro ONNX model download completes (currently lazy — pre-download so first sentence is instant)
- Optional: pull Orpheus GGUF if user picked "high-quality voice"

## Tests

`server/tests/test_hardware.py`:
- Fingerprint parsing for known CPU strings ("Apple M2 Pro", "Intel Core i7-12700H", "AMD Ryzen 9 7940HS")
- Tier assignment corner cases

`server/tests/test_benchmarks.py`:
- `estimate_tok_per_s()` returns sensible values for a matrix of
  (chip, model) known combinations
- MoE models use active-param calculation
- Override table applies

`server/tests/test_recommendations.py`:
- 20-tok/s threshold correctly filters
- Category coverage — every category has at least one recommendation
  on every non-minimal tier
- Required models are all present in the catalog

## Rollout

1. Merge without breaking the existing `get_recommendations()` — kept for
   any callers that don't need the fine-grained estimates.
2. Setup wizard opts into new endpoint via feature flag until validated.
3. After a week: remove the old coarse endpoint.
