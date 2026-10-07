# Model comparison

Does grounding a repository question in the saved scan produce more supported answers than asking the same model with only the repository name?

10 prompts in `eval/questions.json`, across billing-api, node-store, and notes-only. **Without RAG** receives only the repository name. **With RAG** receives the saved scan, document excerpts, matching imports, and matching symbols.

Citation hit is the share of answerable questions whose citations include a gold file. Abstention accuracy is how often the answer refused exactly when the scan has no gold file. Supported is a citation hit without a refusal on answerable questions, and a refusal on unanswerable questions.

| Model | Mode | Questions | Citation hit | Abstention accuracy | Supported |
|---|---|---:|---:|---:|---:|
| Offline excerpts | Without RAG | 10 | 0.000 | 0.300 | 0.300 |
| Offline excerpts | With RAG | 10 | 1.000 | 1.000 | 1.000 |
| GPT | Without RAG | 10 | 0.000 | 0.300 | 0.300 |
| GPT | With RAG | 10 | 1.000 | 0.800 | 0.800 |
| Claude | Not measured | — | — | — | — |
| Gemini | Call failed | — | — | — | — |
| Grok | Not measured | — | — | — | — |

Not measured in this run: Claude needs `ANTHROPIC_API_KEY`; Grok needs `XAI_API_KEY`.

Call failed in this run: Gemini: HTTP 429, free-tier daily quota for gemini-3.8-flash.

## Each prompt

| Prompt | Repo | Answerable | Offline excerpts without RAG | Offline excerpts with RAG | GPT without RAG | GPT with RAG |
|---|---|---|---|---|---|---|
| What database does this repository use? | billing-api | yes | no | yes | no | yes |
| How is the backend built? | billing-api | yes | no | yes | no | yes |
| What does the gitignore file hide? | billing-api | yes | no | yes | no | yes |
| What database does this repository use? | node-store | yes | no | yes | no | yes |
| Which ORM does the server use? | node-store | yes | no | yes | no | yes |
| What does gitignore hide? | node-store | yes | no | yes | no | yes |
| What is this repository about? | notes-only | yes | no | yes | no | yes |
| What database does this repository use? | notes-only | no | yes | yes | yes | no |
| What does gitignore hide? | notes-only | no | yes | yes | yes | yes |
| How is the backend built? | notes-only | no | yes | yes | yes | no |

A cell is **yes** when that answer counts as supported.
