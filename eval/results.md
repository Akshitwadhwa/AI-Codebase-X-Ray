# Model comparison

Does grounding a repository question in the saved scan produce more supported answers than asking the same model with only the repository name?

15 prompts in `eval/questions.json`, across billing-api, node-store, and notes-only. **Without RAG** receives only the repository name. **With RAG** receives the saved scan, document excerpts, matching imports, and matching symbols.

Citation hit is the share of answerable questions whose citations include a gold file. Abstention accuracy is how often the answer refused exactly when the scan has no gold file. Supported is a citation hit without a refusal on answerable questions, and a refusal on unanswerable questions.

| Model | Mode | Questions | Citation hit | Abstention accuracy | Supported |
|---|---|---:|---:|---:|---:|
| Offline excerpts | Without RAG | 15 | 0.000 | 0.267 | 0.267 |
| Offline excerpts | With RAG | 15 | 1.000 | 1.000 | 1.000 |
| GPT | Not measured | — | — | — | — |
| Claude | Not measured | — | — | — | — |
| Gemini | Not measured | — | — | — | — |
| Grok | Not measured | — | — | — | — |

Not measured in this run: GPT needs `OPENAI_API_KEY`; Claude needs `ANTHROPIC_API_KEY`; Gemini needs `GEMINI_API_KEY`; Grok needs `XAI_API_KEY`.

## Each prompt

| Prompt | Repo | Answerable | Offline excerpts without RAG | Offline excerpts with RAG |
|---|---|---|---|---|
| What database does this repository use? | billing-api | yes | no | yes |
| How is the backend built? | billing-api | yes | no | yes |
| What does the gitignore file hide? | billing-api | yes | no | yes |
| Which Python packages are listed for the backend? | billing-api | yes | no | yes |
| Where is the FastAPI app created? | billing-api | yes | no | yes |
| What database does this repository use? | node-store | yes | no | yes |
| Which ORM does the server use? | node-store | yes | no | yes |
| What does gitignore hide? | node-store | yes | no | yes |
| How is the backend server started? | node-store | yes | no | yes |
| Which packages does package.json depend on? | node-store | yes | no | yes |
| What is this repository about? | notes-only | yes | no | yes |
| What database does this repository use? | notes-only | no | yes | yes |
| What does gitignore hide? | notes-only | no | yes | yes |
| How is the backend built? | notes-only | no | yes | yes |
| Which ORM does the server use? | notes-only | no | yes | yes |

A cell is **yes** when that answer counts as supported.
