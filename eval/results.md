# Grounded questions evaluation

Does grounding a repository question in the saved scan produce more supported answers than asking the same model with only the repository name?

Provider: `offline`. The offline provider quotes matching scan lines and refuses when none match. GPT, Claude, Gemini, and Grok use this same runner when their API key is set. Baseline receives only the repository name. Grounded receives the saved scan, document excerpts, matching imports, and matching symbols.

Citation hit is the share of answerable questions whose citations include a gold file. Abstention accuracy is how often the answer refused exactly when the scan has no gold file. Supported is a citation hit without a refusal on answerable questions, and a refusal on unanswerable questions.

| Mode | Questions | Citation hit | Abstention accuracy | Supported |
|---|---:|---:|---:|---:|
| baseline | 15 | 0.000 | 0.267 | 0.267 |
| grounded | 15 | 1.000 | 1.000 | 1.000 |

Cloud providers use the same script: `python -m eval.run_eval --provider openai`.
