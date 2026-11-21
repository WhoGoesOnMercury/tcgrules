import re
from pathlib import Path
from flask import Flask, render_template, request, jsonify
from pdf_to_json import MTGRulesRetriever

app = Flask(__name__)
BASE_DIR = Path(__file__).parent.resolve()

RETRIEVERS: dict[str, MTGRulesRetriever] = {}


def load_all_rulebooks():
    """Auto-detects any subfolder containing a rulebook PDF."""
    for pdf_file in sorted(BASE_DIR.rglob("*.pdf")):
        folder_name = pdf_file.parent.name
        if folder_name not in RETRIEVERS:
            RETRIEVERS[folder_name] = MTGRulesRetriever(pdf_file, force_rebuild=False)


load_all_rulebooks()


def parse_retriever_blocks(raw_text: str, system_name: str) -> list[dict]:
    """Converts raw retriever output into clean, structured JSON objects."""
    if not raw_text or raw_text == "No matching rules found.":
        return []

    parsed = []
    for block in raw_text.split("\n\n====================\n\n"):
        lines = [line.strip() for line in block.strip().splitlines() if line.strip()]
        if not lines:
            continue

        header = lines[0].strip("[]")
        body_lines = lines[1:]

        if header.startswith("GLOSSARY"):
            category = "GLOSSARY"
            pointer = header.replace("GLOSSARY:", "").replace("GLOSSARY SECTION REF:", "").strip()
        else:
            category = "RULES"
            pointer = header.replace("RULE POINTER:", "").strip()

        parsed.append({
            "system": system_name.upper(),
            "category": category,
            "pointer": pointer,
            "lines": body_lines,
        })
    return parsed


@app.route("/")
def home():
    sample_glossary = []
    sample_sections = []

    for folder_name, retriever in RETRIEVERS.items():
        for term_key in list(retriever.glossary.keys())[:5]:
            if len(sample_glossary) >= 5:
                break
            entry = retriever.glossary[term_key]
            sample_glossary.append({
                "term": entry["term"],
                "page": f"Page {entry['page']}",
                "snippet": entry["definition"][:130] + ("..." if len(entry["definition"]) > 130 else ""),
            })

        for sec_id in list(retriever.parents.keys())[:5]:
            if len(sample_sections) >= 5:
                break
            sec = retriever.parents[sec_id]
            first_lines = sec["text"].splitlines()
            title = first_lines[1] if len(first_lines) > 1 else sec_id
            sample_sections.append({
                "title": title,
                "pointer": sec["pointer"],
                "tag": first_lines[0].upper() if first_lines else folder_name.upper(),
            })

    return render_template(
        "index.html",
        sample_glossary=sample_glossary,
        sample_sections=sample_sections,
    )


@app.route("/api/search")
def search_rules():
    q = request.args.get("q", "").strip()
    try:
        top_k = max(1, min(10, int(request.args.get("top_k", 4))))
    except ValueError:
        top_k = 4

    if not q:
        return jsonify({"results": [], "error": "Query cannot be empty."}), 400

    # Check if the query explicitly mentions a folder keyword (e.g., "mtg" or "pokemon")
    target_folders = [
        folder for folder in RETRIEVERS
        if re.search(rf"\b{re.escape(folder)}\b", q, re.IGNORECASE)
    ]

    # Strip the system keyword from the search string if matched so BM25 stays focused on the rule
    clean_q = q
    if target_folders:
        for folder in target_folders:
            stripped = re.sub(rf"\b{re.escape(folder)}\b", "", clean_q, flags=re.IGNORECASE).strip()
            if stripped:
                clean_q = stripped
    else:
        target_folders = list(RETRIEVERS.keys())

    all_results = []
    for folder in target_folders:
        retriever = RETRIEVERS[folder]
        raw_output = retriever.retrieve(clean_q, top_k=top_k)
        all_results.extend(parse_retriever_blocks(raw_output, folder))

    return jsonify({"query": q, "results": all_results})


if __name__ == "__main__":
    app.run(debug=True, port=5000)