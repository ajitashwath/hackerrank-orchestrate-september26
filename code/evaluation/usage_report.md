# Usage Report — Final Full-Dataset Run (output.csv)

Run date: 2026-09-12 (IST)
Requests evaluated: 250 (`dataset/requests.csv` → `output.csv`)
Entry point: `python code/main.py`
Mode: deterministic rules engine, stdlib only. No LLM/VLM calls.

## Model totals (overall)

| Provider | Model | Calls | Input tokens | Output tokens | Total tokens | Est. cost (USD) |
|---|---|---|---|---|---|---|
| none (deterministic) | n/a | 0 | 0 | 0 | 0 | 0.00 |

Overall: 250 requests, 0 model calls, 0 input tokens, 0 output tokens, 0 total tokens.
Average per request: 0 calls, 0 input / 0 output / 0 total tokens, $0.00.

## Per-model breakdown

No model providers were used. All decisions (recurrence detection, 90-day safety
check, FX conversion, message directives, payment-option ranking) run locally.
Image OCR was not needed for the evaluation set (`request_26..request_275`):
all blank-amount events map to sample requests only; no eval `related_event_id`
requires image extraction.

## Cost basis

Estimated total: $0.00. Estimated per-request: $0.00.

## Reproducibility

`python code/main.py` reads `dataset/` and writes repo-root `output.csv`
(250 rows + header, exact required columns/order). Deterministic: no randomness,
no network, no API keys. Secrets: none required (env-only if ever added).
