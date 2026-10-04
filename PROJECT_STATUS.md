# AI Codebase X-Ray — Project Status and Next Steps

## Project purpose

AI Codebase X-Ray is a developer-focused DevOps assistant that analyses an unfamiliar software repository. It converts repository source code into structured engineering knowledge so that developers can understand the architecture, trace dependencies, estimate change impact, and later validate changes through automated quality checks.

The system is designed around a clear separation of responsibilities:

```text
Browser frontend → FastAPI backend → repository analysis → structured evidence
                                                ↓
                              graph, retrieval, AI reasoning, and DevOps checks
```

## What has been completed: Phase 1

Phase 1 is the first working vertical slice. The user enters a public GitHub repository URL in the browser and requests an analysis.

### Implemented capabilities

- React-style browser interface using `frontend/index.html`, `frontend/app.js`, and `frontend/styles.css`.
- FastAPI service in `backend/main.py`.
- Server-side repository ingestion through Git clone, with GitHub archive fallback.
- Temporary repository workspace owned by the backend. The user's device does not need to clone the repository.
- Repository scanner that ignores directories such as `.git`, `node_modules`, virtual environments, `dist`, `build`, and coverage output.
- Language detection for Python, JavaScript, and TypeScript.
- Tree-sitter parsing for language-aware source analysis.
- Extraction of files, folders, classes, functions, methods, imports, and tests.
- Relationship extraction for `CONTAINS`, `IMPORTS`, `CALLS`, and `INHERITS`.
- Structured JSON result saved under `data/analyses/`.
- Basic automated tests in `tests/test_analyzer.py`.
- Environment configuration through `.env.example` and optional `GITHUB_TOKEN` support.

### Phase 1 output

The backend returns a repository summary and an `analysis_id`. The structured result provides the evidence needed by later phases, including files, symbols, source locations, language counts, tests, and relationship records.

## Phase 2: graph and impact review

Phase 2 does not repeat repository ingestion or parsing. It reads a saved Phase 1 analysis and turns symbols, imports, and relationships into a queryable review graph.

```text
Phase 1 JSON → graph builder → graph API → browser map and impact analysis
```

### Phase 2 deliverables

1. Persist every completed analysis using its `analysis_id`, and reload it with `GET /api/analyses` and `GET /api/analyses/{analysis_id}`.
2. Create graph nodes for repositories, folders, files, classes, functions, and methods. Imports and tests stay edges and flags, not extra node types.
3. Create graph edges for `CONTAINS`, `IMPORTS`, `CALLS`, and `INHERITS`. Call and base edges are resolved to symbol ids with a confidence, or kept as unresolved evidence.
4. NetworkX is the only graph store. `{analysis_id}.json` is the source of truth and `{analysis_id}.graph.json` is a cache.
5. Backend endpoints:
   - `GET /api/analyses/{analysis_id}/graph?view=architecture|neighborhood`
   - `GET /api/analyses/{analysis_id}/impact/{symbol_id}`
   - `GET /api/analyses/{analysis_id}/file?path=`
   - `GET /api/analyses/{analysis_id}/symbols?q=`
6. A React Flow neighborhood map on the existing static page.
7. A symbol-selection view that shows the containing file and class, callers, callees, imports, tests, and parent classes.
8. An evidence-backed impact report: review files with reasons, snippets, and deterministic risk flags.

### Phase 2 demonstration

The mentor demo should select a function such as `process_payment()` and show:

- the file and class that contain it;
- functions or services that call it;
- imported or dependent modules;
- related tests;
- a list of files that may need review after a change.

## Later project phases

### Phase 3 — Grounded code Q&A

- Split AST-aware code and documentation into chunks.
- Generate embeddings using a configurable embedding model.
- Store vectors in ChromaDB, with a modular interface for FAISS later.
- Retrieve relevant chunks with file and line metadata.
- Add an LLM service using Ollama locally or another provider through configuration.
- Return answers with citations and a confidence or insufficient-evidence warning.

### Phase 4 — Change intelligence and test support

- Compare a selected change or pull request with graph relationships.
- Estimate affected components and risk areas.
- Generate test skeletons and test suggestions from the changed symbols.
- Connect recommendations to existing tests and coverage information.

### Phase 5 — DevOps validation

- Add isolated workers for lint, test, build, and security checks.
- Add job status, logs, timings, and stored artifacts.
- Introduce an orchestrator or CrewAI workflow for retrieval, impact, QA, and report agents.
- Add quality gates such as pass, fail, or review required.
- Produce a CI or pull-request report while keeping human approval before merge or deployment.

## Current architecture

```text
React frontend
      ↓
FastAPI application and API gateway
      ↓
Repository ingestion and scanner
      ↓
Tree-sitter extraction
      ↓
Structured JSON evidence
      ↓ Phase 2
Knowledge graph and graph API
      ↓ Later phases
RAG, LLM, agents, tests, CI, and reports
```

## Current folder structure

```text
AI-Codebase-X-Ray/
├── backend/          FastAPI routes and repository analyzer
├── frontend/         Browser interface
├── tests/            Automated tests
├── data/             Generated analysis data (not committed)
├── requirements.txt  Python dependencies
├── run_demo.sh       Local demo launcher
├── README.md         Quick-start instructions
└── PROJECT_STATUS.md This project status and roadmap
```

## Running the current demo

From this project folder:

```bash
cd /Users/Lenovo/Desktop/sem-7/idt/AI-Codebase-X-Ray
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8001
```

Then open <http://127.0.0.1:8001>. The API documentation is available at <http://127.0.0.1:8001/docs>.

## Definition of done for Phase 2

Phase 2 is complete when a user can open an existing Phase 1 analysis, view its interactive architecture graph, select a class or function, request an impact analysis, and receive related callers, dependencies, affected files, and tests through the browser.

## Safety and scope

The system should remain explainable and user-controlled. It should show the evidence behind an answer, isolate automated checks, avoid silently modifying repositories, and require explicit human approval before any merge or deployment action.
