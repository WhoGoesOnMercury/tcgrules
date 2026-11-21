import re
import json
from pathlib import Path
import pymupdf  # PyMuPDF
from rank_bm25 import BM25Okapi


def parse_glossary_pages(doc: pymupdf.Document, glossary_start_page: int = 257) -> dict:
    glossary = {}
    current_term = None
    current_def = []
    current_page = glossary_start_page

    def flush_term():
        nonlocal current_term, current_def
        if current_term and current_def:
            full_def = " ".join(current_def).strip()
            refs = re.findall(r"\b(\d{3,}(?:\.\d+[a-z]?)?)\b", full_def)
            glossary[current_term.lower()] = {
                "term": current_term,
                "definition": full_def,
                "page": current_page,
                "rule_refs": list(dict.fromkeys(refs))
            }
        current_term = None
        current_def = []

    for page_index in range(glossary_start_page - 1, len(doc)):
        real_page_num = page_index + 1
        page_dict = doc[page_index].get_text("dict", sort=True)

        for block in page_dict.get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                spans = [s for s in line.get("spans", []) if s["text"].strip()]
                if not spans:
                    continue

                line_text = "".join(s["text"] for s in spans).strip()

                # Stop when the Glossary ends and the Credits page begins
                if line_text.lower() == "credits":
                    flush_term()
                    return glossary

                # Detect bold font via flag bit 16 OR "bold" in font name
                is_bold = all(
                    bool(s["flags"] & 16) or "bold" in s["font"].lower()
                    for s in spans
                )

                # Skip single-letter headers ("A", "B", "C") or "Glossary" title
                if is_bold and (len(line_text) == 1 or line_text.lower() == "glossary"):
                    continue

                if is_bold:
                    flush_term()
                    current_term = line_text
                    current_page = real_page_num
                elif current_term:
                    current_def.append(line_text)

    flush_term()
    return glossary


# --- 2. BUILD OR LOAD INDEX IN `mtg/` FOLDER ---
def build_or_load_index(
    pdf_path: str | Path = "mtg/MagicCompRules.pdf",
    skip_pages: int = 5,
    glossary_start_page: int = 257,
    force_rebuild: bool = False
):
    pdf_file = Path(pdf_path).resolve()
    if not pdf_file.exists():
        raise FileNotFoundError(f"PDF not found at: {pdf_file}")

    # Automatically saves as mtg/MagicCompRules.json
    json_file = pdf_file.with_suffix(".json")

    # Schema-aware cache check: automatically rebuilds if older JSON lacks 'glossary'
    if json_file.exists() and not force_rebuild:
        with json_file.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if all(key in data for key in ("parents", "children", "glossary")):
            return data
        print(f"Outdated JSON schema detected in {json_file.name}. Rebuilding index...")

    doc = pymupdf.open(pdf_file)

    chapter_re = re.compile(r"^(\d{1,2}\.\s+.+)$")
    section_re = re.compile(r"^(\d{3,}\.\s+.+)$")
    # Matches both "702.15. Lifelink" (topic header) and "702.15a Lifelink is..." (rule)
    rule_re = re.compile(r"^(\d{3,}\.\d+[a-z]?)\.?\s+(.*)$")

    parents = {}
    children = []

    current_chapter = "General"
    current_section_title = ""
    current_parent_id = None
    current_rule = None

    def flush_rule():
        nonlocal current_rule
        if not current_rule or not current_parent_id:
            return
        full_rule_text = f"{current_rule['code']} {current_rule['text']}".strip()
        parents[current_parent_id]["text"] += "\n" + full_rule_text
        children.append({
            "rule_code": current_rule["code"].lower(),
            "parent_id": current_parent_id,
            "pointer": f"{current_chapter} - {current_section_title} - Rule {current_rule['code']} (Page {current_rule['page']})",
            "search_text": f"{current_chapter} {current_section_title} {full_rule_text}"
        })
        current_rule = None

    rules_end_index = min(glossary_start_page - 1, len(doc))
    for page_index in range(skip_pages, rules_end_index):
        real_page_num = page_index + 1
        text = doc[page_index].get_text("text", sort=True)
        text = re.sub(r"(\w+)-\n(\w+)", r"\1\2", text)

        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue

            if chapter_re.match(line):
                flush_rule()
                current_chapter = line
                continue

            if section_re.match(line):
                flush_rule()
                sec_num = line.split(".")[0]
                current_section_title = line
                current_parent_id = f"sec_{sec_num}"
                parents[current_parent_id] = {
                    "pointer": f"{current_chapter} - {current_section_title} (Page {real_page_num})",
                    "text": f"{current_chapter}\n{current_section_title}"
                }
                continue

            rule_match = rule_re.match(line)
            if rule_match and current_section_title:
                flush_rule()
                code = rule_match.group(1)
                rest = rule_match.group(2)

                # Keep giant sections (like 702. Keyword Abilities) token-light by making
                # each "702.15. Lifelink" sub-topic its own compact Parent container
                sec_num = code.split(".")[0]
                if sec_num in {"701", "702", "704", "705", "706", "707", "708", "709", "710", "711", "712"}:
                    base_topic = re.match(r"^(\d+\.\d+)", code).group(1)
                    topic_parent_id = f"sec_{base_topic}"
                    if topic_parent_id not in parents:
                        parents[topic_parent_id] = {
                            "pointer": f"{current_chapter} - {current_section_title} - Topic {base_topic} (Page {real_page_num})",
                            "text": f"{current_chapter}\n{current_section_title}"
                        }
                    current_parent_id = topic_parent_id
                else:
                    current_parent_id = f"sec_{sec_num}"

                current_rule = {
                    "code": code,
                    "text": rest,
                    "page": real_page_num
                }
                continue

            if current_rule:
                current_rule["text"] += " " + line
            elif current_parent_id:
                parents[current_parent_id]["text"] += " " + line

    flush_rule()
    glossary = parse_glossary_pages(doc, glossary_start_page=glossary_start_page)

    index_data = {
        "source_pdf": pdf_file.name,
        "parents": parents,
        "children": children,
        "glossary": glossary
    }

    with json_file.open("w", encoding="utf-8") as f:
        json.dump(index_data, f, indent=2, ensure_ascii=False)

    print(f"Saved: {json_file} ({len(parents)} parent blocks, {len(children)} rules, {len(glossary)} glossary entries)")
    return index_data


# --- 3. MTG RULES RETRIEVER ---
class MTGRulesRetriever:
    def __init__(self, pdf_path: str | Path = "mtg/MagicCompRules.pdf", force_rebuild: bool = False):
        data = build_or_load_index(pdf_path, skip_pages=5, glossary_start_page=257, force_rebuild=force_rebuild)
        self.parents = data["parents"]
        self.children = data["children"]
        self.glossary = data["glossary"]

        tokenized_corpus = [self._tokenize(c["search_text"]) for c in self.children]
        self.bm25 = BM25Okapi(tokenized_corpus)

    def _tokenize(self, text: str) -> list[str]:
        return re.findall(r"\d+\.\d+[a-z]?|\w+", text.lower())

    def retrieve(self, query: str, top_k: int = 3) -> str:
        q_lower = query.lower()
        context_blocks = []
        seen_parents = set()
        expanded_terms = self._tokenize(query)
        forced_rule_codes = set(re.findall(r"\b\d{3,}\.\d+[a-z]?\b", q_lower))

        # 1. Check Glossary terms (longest terms first so "double strike" matches before "strike")
        for term_key in sorted(self.glossary.keys(), key=len, reverse=True):
            if len(term_key) > 2 and re.search(rf"\b{re.escape(term_key)}\b", q_lower):
                entry = self.glossary[term_key]
                context_blocks.append(
                    f"[GLOSSARY: {entry['term']} (Page {entry['page']})]\n{entry['definition']}"
                )
                expanded_terms.extend(self._tokenize(entry["definition"]))
                for ref in entry["rule_refs"]:
                    if "." in ref:
                        forced_rule_codes.add(ref.lower())
                    elif f"sec_{ref}" in self.parents and f"sec_{ref}" not in seen_parents:
                        seen_parents.add(f"sec_{ref}")
                        context_blocks.append(
                            f"[GLOSSARY SECTION REF: {self.parents[f'sec_{ref}']['pointer']}]\n"
                            f"{self.parents[f'sec_{ref}']['text']}"
                        )

        # 2. Grab exact rule codes (e.g., "702.15" or "702.15a")
        matched_children = [
            c for c in self.children
            if c["rule_code"] in forced_rule_codes
            or any(c["rule_code"].startswith(code) for code in forced_rule_codes if not code[-1].isalpha())
        ]

        # 3. Concept-expanded BM25 search
        scores = self.bm25.get_scores(expanded_terms)
        ranked = sorted(zip(scores, self.children), key=lambda x: x[0], reverse=True)
        for score, child in ranked[:top_k]:
            if score > 0 and child not in matched_children:
                matched_children.append(child)

        # 4. Assemble deduplicated Parent blocks
        for child in matched_children:
            pid = child["parent_id"]
            if pid not in seen_parents:
                seen_parents.add(pid)
                context_blocks.append(
                    f"[RULE POINTER: {child['pointer']}]\n{self.parents[pid]['text']}"
                )

        return "\n\n====================\n\n".join(context_blocks) if context_blocks else "No matching rules found."


# --- TEST RUN ---
if __name__ == "__main__":
    retriever = MTGRulesRetriever("mtg/MagicCompRules.pdf")
    print(retriever.retrieve("Does Lifelink stack if a creature has two instances of it?"))