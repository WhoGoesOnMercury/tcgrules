from pathlib import Path
from .pdf_to_json import MTGRulesRetriever
from .card_index import ScryfallCardIndex


def load_game(folder_path: Path):
    pdf_file = next(folder_path.glob("*.pdf"), None)
    retriever = MTGRulesRetriever(pdf_file, force_rebuild=False) if pdf_file else None

    has_cards = any(folder_path.glob("oracle-cards*.jsonl.gz"))
    card_index = ScryfallCardIndex(folder_path, force_rebuild=False) if has_cards else None

    return retriever, card_index