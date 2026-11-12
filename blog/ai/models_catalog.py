"""Workers AI models that can be chosen in the admin.

Only chat models that accept the `messages` format and work on the free plan
are listed. Neurons are per 1 million tokens (input / output) from
https://developers.cloudflare.com/workers-ai/platform/pricing/ — lower is
cheaper, the free plan has 10 000 neurons per day.
"""

GEMMA_4 = '@cf/google/gemma-4-26b-a4b-it'
MISTRAL_SMALL = '@cf/mistralai/mistral-small-3.1-24b-instruct'
LLAMA_31_8B = '@cf/meta/llama-3.1-8b-instruct-fp8-fast'
LLAMA_33_70B = '@cf/meta/llama-3.3-70b-instruct-fp8-fast'
LLAMA_4_SCOUT = '@cf/meta/llama-4-scout-17b-16e-instruct'
GEMMA_3_12B = '@cf/google/gemma-3-12b-it'

MODEL_CHOICES = (
    (GEMMA_4, 'Gemma 4 26B — най-добър български, най-евтин (9 091 / 27 273 неврона)'),
    (MISTRAL_SMALL, 'Mistral Small 3.1 24B — добър български, работи при Cloudflare (31 876 / 50 488)'),
    (LLAMA_33_70B, 'Llama 3.3 70B — голям модел, по-скъп (26 668 / 204 805)'),
    (LLAMA_4_SCOUT, 'Llama 4 Scout 17B (24 545 / 77 273)'),
    (GEMMA_3_12B, 'Gemma 3 12B (31 371 / 50 560)'),
    (LLAMA_31_8B, 'Llama 3.1 8B — бърз, но с грешки на български (4 119 / 34 868)'),
)

DEFAULT_PUBLIC_MODEL = GEMMA_4
DEFAULT_STUDENT_TEXT_MODEL = MISTRAL_SMALL
