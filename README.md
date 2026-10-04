# AI Codebase X-Ray — Phase 1

This is the first functional vertical slice of AI Codebase X-Ray. A user opens a browser, pastes a public GitHub repository URL, and clicks **Analyze repository**. The FastAPI backend performs a shallow Git clone inside a temporary server-side directory, extracts supported code structure, returns JSON, and removes the temporary files. If Git is unavailable, it falls back to a GitHub archive download. The user's device never clones the repository.

## Run locally

From a new macOS/Linux terminal:

```bash
cd /Users/Lenovo/Desktop/sem-7/idt/Project_Phase-1
python3 -m venv .venv              # skip if .venv already exists
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8001
```

Open <http://127.0.0.1:8001>. API documentation is available at <http://127.0.0.1:8001/docs>.

After the first installation, the shorter command is:

```bash
cd /Users/Lenovo/Desktop/sem-7/idt/Project_Phase-1
./run_demo.sh
```

The demo currently supports Python, JavaScript, and TypeScript. It detects files, folders, the primary language, classes, functions/methods, imports, tests, and `CONTAINS`, `IMPORTS`, `CALLS`, and `INHERITS` relationships using Tree-sitter. Each completed result is saved as structured JSON under `data/analyses/` (ignored by Git).

After an analysis finishes, the page searches for `process_payment` when that symbol exists. You can also paste a saved 12-character analysis id and choose **Open saved analysis**. Selecting a symbol loads callers, the containing class, imports, related tests, risk flags, and the files to review. The map is that symbol's neighborhood. RAG, CI workers, and pull-request analysis remain later phases.

## Configuration

Copy `.env.example` to `.env` if you need a GitHub token for higher API limits. The app loads this file automatically. Do not commit tokens. Public repositories can be analysed without a token, but a token is recommended for repeated demonstrations.
