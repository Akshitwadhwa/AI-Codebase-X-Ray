from __future__ import annotations


def score_answer(answer: dict, gold: dict) -> dict:
    cited = {item.get("path") for item in answer.get("citations") or [] if item.get("path")}
    gold_paths = set(gold.get("paths") or [])
    answerable = bool(gold["answerable"])
    refused = bool(answer.get("refused"))
    hit = bool(cited & gold_paths)
    if answerable:
        supported = (not refused) and hit
    else:
        supported = refused
    return {
        "citation_hit": hit if answerable else None,
        "abstention_correct": refused == (not answerable),
        "supported": supported,
    }


def summarize(rows: list[dict], mode: str) -> dict:
    selected = [row for row in rows if row["mode"] == mode]
    answerable = [row for row in selected if row["answerable"]]
    hits = [row for row in answerable if row["citation_hit"]]
    return {
        "mode": mode,
        "questions": len(selected),
        "citation_hit_rate": round(len(hits) / len(answerable), 3) if answerable else 0,
        "abstention_accuracy": round(sum(row["abstention_correct"] for row in selected) / len(selected), 3) if selected else 0,
        "supported_rate": round(sum(row["supported"] for row in selected) / len(selected), 3) if selected else 0,
    }
