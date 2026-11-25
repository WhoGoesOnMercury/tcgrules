import importlib
import re
from pathlib import Path
from flask import Flask, render_template, request, jsonify

app = Flask(__name__)
BASE_DIR = Path(__file__).parent.resolve()

RETRIEVERS: dict[str, object] = {}
CARD_INDEXES: dict[str, object] = {}


def load_all_games():
    for subdir in sorted(BASE_DIR.iterdir()):
        if not subdir.is_dir() or subdir.name.startswith((".", "_")) or subdir.name == "templates":
            continue
        if not (subdir / "__init__.py").exists():
            continue

        module = importlib.import_module(subdir.name)
        if hasattr(module, "load_game"):
            retriever, card_index = module.load_game(subdir)
            if retriever:
                RETRIEVERS[subdir.name] = retriever
            if card_index:
                CARD_INDEXES[subdir.name] = card_index


load_all_games()


def parse_retriever_blocks(raw_text: str, system_name: str) -> list[dict]:
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
    return render_template("index.html")


@app.route("/api/search")
def search_rules():
    q = request.args.get("q", "").strip()
    try:
        top_k = max(1, min(10, int(request.args.get("top_k", 4))))
    except ValueError:
        top_k = 4

    if not q:
        return jsonify({"results": [], "error": "Query cannot be empty."}), 400

    all_systems = set(RETRIEVERS.keys()) | set(CARD_INDEXES.keys())
    target_folders = [
        folder for folder in all_systems
        if re.search(rf"\b{re.escape(folder)}\b", q, re.IGNORECASE)
    ]

    clean_q = q
    if target_folders:
        for folder in target_folders:
            stripped = re.sub(rf"\b{re.escape(folder)}\b", "", clean_q, flags=re.IGNORECASE).strip()
            if stripped:
                clean_q = stripped
    else:
        target_folders = sorted(all_systems)

    all_results = []
    for folder in target_folders:
        bm25_query = clean_q

        card_idx = CARD_INDEXES.get(folder)
        if card_idx:
            matched_cards = card_idx.extract_cards_from_query(clean_q)
            for card in matched_cards:
                card_lines = [f"{card['type_line']} {card['mana_cost']}".strip()]
                if card["oracle_text"]:
                    card_lines.extend(card["oracle_text"].splitlines())
                for ruling in card["rulings"]:
                    card_lines.append(f"Ruling ({ruling['date']}): {ruling['comment']}")

                all_results.append({
                    "system": folder.upper(),
                    "category": "CARD ORACLE",
                    "pointer": card["name"],
                    "image_url": card.get("image_url", ""),
                    "back_image_url": card.get("back_image_url", ""),
                    "lines": card_lines,
                })
                bm25_query += " " + card["oracle_text"]

        retriever = RETRIEVERS.get(folder)
        if retriever:
            raw_output = retriever.retrieve(bm25_query, top_k=top_k)
            all_results.extend(parse_retriever_blocks(raw_output, folder))

    return jsonify({"query": q, "results": all_results})


@app.route("/api/cards")
def search_cards():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"cards": []})

    results = []
    for folder, card_idx in CARD_INDEXES.items():
        for card in card_idx.search_cards(q, limit=12):
            results.append({"system": folder.upper(), **card})

    return jsonify({"query": q, "cards": results})


@app.errorhandler(404)
def handle_404(e):
    if request.path.startswith("/api/"):
        return jsonify({"error": f"Endpoint {request.path} not found."}), 404
    return render_template("index.html"), 404


if __name__ == "__main__":
    app.run(debug=True, port=5000)