from __future__ import annotations

MODE_LABELS = {"baseline": "Without RAG", "grounded": "With RAG"}


def render(report: dict) -> str:
    lines = [
        "# Model comparison",
        "",
        report["research_question"],
        "",
        f"{report['prompts']} prompts in `eval/questions.json`, across billing-api, node-store, and notes-only. **Without RAG** receives only the repository name. **With RAG** receives the saved scan, document excerpts, matching imports, and matching symbols.",
        "",
        "Citation hit is the share of answerable questions whose citations include a gold file. Abstention accuracy is how often the answer refused exactly when the scan has no gold file. Supported is a citation hit without a refusal on answerable questions, and a refusal on unanswerable questions.",
        "",
        "| Model | Mode | Questions | Citation hit | Abstention accuracy | Supported |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for provider in report["providers"]:
        if not provider["measured"]:
            lines.append(f"| {provider['label']} | Not measured | — | — | — | — |")
            continue
        for item in provider["summary"]:
            lines.append(
                f"| {provider['label']} | {MODE_LABELS[item['mode']]} | {item['questions']} | {item['citation_hit_rate']:.3f} | {item['abstention_accuracy']:.3f} | {item['supported_rate']:.3f} |"
            )
    skipped = [provider for provider in report["providers"] if not provider["measured"]]
    if skipped:
        reasons = "; ".join(f"{provider['label']} needs `{provider['reason']}`" for provider in skipped)
        lines.extend(["", f"Not measured in this run: {reasons}."])
    measured = [provider for provider in report["providers"] if provider["measured"]]
    if measured:
        lines.extend(["", "## Each prompt", ""])
        header = ["Prompt", "Repo", "Answerable"]
        for provider in measured:
            header.append(f"{provider['label']} without RAG")
            header.append(f"{provider['label']} with RAG")
        lines.append("| " + " | ".join(header) + " |")
        lines.append("|" + "---|" * len(header))
        by_key = {(row["provider"], row["id"], row["mode"]): row for row in report["rows"]}
        for item in report["questions"]:
            cells = [item["question"], item["repo"], "yes" if item["answerable"] else "no"]
            for provider in measured:
                for mode in ("baseline", "grounded"):
                    row = by_key[(provider["id"], item["id"], mode)]
                    cells.append("yes" if row["supported"] else "no")
            lines.append("| " + " | ".join(cells) + " |")
        lines.extend(["", "A cell is **yes** when that answer counts as supported.", ""])
    else:
        lines.append("")
    return "\n".join(lines)
