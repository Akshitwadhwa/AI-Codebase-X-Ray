# AI Codebase X-Ray

Paste a public GitHub repository in the browser. The server clones it, scans the code, and saves a structured analysis. The same page can reopen that scan, answer questions about it, review a symbol, and run pytest on a pull request. The user's device never clones the repository.

## Run locally

From this project folder:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8001
```

Open <http://127.0.0.1:8001>. API documentation is available at <http://127.0.0.1:8001/docs>.

After the first installation:

```bash
./run_demo.sh
```

## What you can do

### Analyze a repository

Paste a public GitHub URL and choose **Analyze repository**. The server performs a shallow Git clone in a temporary directory. If Git is unavailable, it downloads a GitHub archive instead, then deletes the checkout. The scan covers Python, JavaScript, and TypeScript, using Tree-sitter for classes, functions, methods, imports, tests, and `CONTAINS`, `IMPORTS`, `CALLS`, and `INHERITS` relationships.

The page shows the repository name, a saved 12-character analysis id, the primary language, the branch, and counts for files, folders, source files, classes, functions, imports, and tests. It also lists files, symbols with file and line, those relationship edges, and any warnings.

### Reopen a scan

Paste that 12-character id and choose **Open saved analysis**.

### Ask a question

After a scan, ask about the repository. The answer shows file citations. **Compare** can send the same question to a second provider, or run **Without RAG** (the same model, with only the repository name). Selecting a symbol also shows a short RAG explanation from the chosen provider.

The scan keeps short excerpts of the README, `.gitignore`, dependency files, and compose files, and retrieval uses the saved knowledge chunks for the grounded answer.

### Review a symbol

Search a name. After a scan, the page tries `process_payment` when that symbol exists. The review shows the containing file and line range, a snippet, callers, callees, base classes, imports, tests, risk flags, files to review, and unresolved calls. **Include callers of callers** widens the neighborhood.

The neighborhood map shows the selected symbol, callers, callees, class, tests, and imports. **Import map** is the architecture view. Opening a review file shows its symbols, imports, importers, and tests.

### Run pytest on a pull request

On the same page, paste a pull request URL, or a repository URL plus a branch. The base branch defaults to `main`. Choose **Run check**.

The page shows the pull request title, author, state, and head → base (or ref → base), the commit count and ahead/behind, changed files, GitHub Actions and check runs when GitHub returns them, the pytest command, exit code, stdout, and stderr, and the contents of changed files. The server clones that ref, runs `python -m pytest`, then deletes the checkout. This check is separate from the saved scan.

## How data is stored

Analyses are JSON files under `data/analyses/` (ignored by Git). There is no database.

- `{id}.json` is the analysis.
- `{id}.graph.json` is a graph cache. NetworkX is built from that JSON.
- `{id}.knowledge.json` holds document, import, and symbol chunks with local-hash vectors (`local-hash-v1`). Retrieval ranks those chunks by cosine similarity.

## Model providers

The question dropdown always includes **Offline excerpts**. It also lists a cloud provider when its key is set in `.env`:

| Provider | Environment variable | Default model |
| --- | --- | --- |
| GPT | `OPENAI_API_KEY` | `gpt-4o-mini` |
| Claude | `ANTHROPIC_API_KEY` | `claude-3-5-haiku-20241022` |
| Gemini | `GEMINI_API_KEY` | `gemini-3.8-flash` |
| Grok | `XAI_API_KEY` | `grok-3` |

Keys stay on the server.

To compare every model without RAG and with RAG on the 15 prompts in `eval/questions.json`:

```bash
python -m eval.run_eval
```

The table is written to `eval/results.md`. Offline excerpts always run. GPT, Claude, Gemini, and Grok are measured when their keys are set.

## Configuration

Copy `.env.example` to `.env` if you need a GitHub token for higher API limits, or keys for the cloud models. The app loads this file automatically. Do not commit tokens. Public repositories can be analysed without a token, but a token is recommended for repeated demonstrations. The same token is used when the pull-request check calls GitHub.
