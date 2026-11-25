import gzip
import json
import re
from pathlib import Path


class ScryfallCardIndex:
    def __init__(self, folder_path: str | Path, force_rebuild: bool = False):
        self.folder = Path(folder_path).resolve()
        self.cache_file = self.folder / "cards_index.json"
        self.cards: dict[str, dict] = {}
        self._name_regex: re.Pattern | None = None

        oracle_gz = next(self.folder.glob("oracle-cards*.jsonl.gz"), None)
        rulings_gz = next(self.folder.glob("rulings*.jsonl.gz"), None)

        if not oracle_gz:
            return

        needs_build = force_rebuild or not self.cache_file.exists()
        if not needs_build:
            cache_mtime = self.cache_file.stat().st_mtime
            if oracle_gz.stat().st_mtime > cache_mtime or (
                rulings_gz and rulings_gz.stat().st_mtime > cache_mtime
            ):
                needs_build = True

        if needs_build:
            self._build_cache(oracle_gz, rulings_gz)
        else:
            with self.cache_file.open("r", encoding="utf-8") as f:
                self.cards = json.load(f)

        self._compile_matcher()

    def _build_cache(self, oracle_gz: Path, rulings_gz: Path | None):
        rulings_by_oracle_id: dict[str, list[dict]] = {}

        if rulings_gz and rulings_gz.exists():
            with gzip.open(rulings_gz, "rt", encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    r = json.loads(line)
                    oid = r.get("oracle_id")
                    if oid:
                        rulings_by_oracle_id.setdefault(oid, []).append({
                            "date": r.get("published_at", ""),
                            "comment": r.get("comment", ""),
                        })

        excluded_layouts = {"token", "double_faced_token", "emblem", "art_series"}

        with gzip.open(oracle_gz, "rt", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                card = json.loads(line)
                if card.get("layout") in excluded_layouts:
                    continue

                name = card.get("name", "").strip()
                oid = card.get("oracle_id", "")
                if not name:
                    continue

                oracle_text = card.get("oracle_text")
                mana_cost = card.get("mana_cost", "")
                image_url = ""
                back_image_url = ""

                if "image_uris" in card:
                    image_url = card["image_uris"].get("normal", "")

                if "card_faces" in card:
                    faces = card["card_faces"]
                    if not oracle_text:
                        oracle_text = "\n//\n".join(face.get("oracle_text", "") for face in faces)
                    if not mana_cost:
                        mana_cost = " // ".join(
                            face.get("mana_cost", "") for face in faces if face.get("mana_cost")
                        )
                    if not image_url and faces and "image_uris" in faces[0]:
                        image_url = faces[0]["image_uris"].get("normal", "")
                    if len(faces) > 1 and "image_uris" in faces[1]:
                        back_image_url = faces[1]["image_uris"].get("normal", "")

                self.cards[name.lower()] = {
                    "name": name,
                    "mana_cost": mana_cost,
                    "type_line": card.get("type_line", ""),
                    "oracle_text": oracle_text or "",
                    "image_url": image_url,
                    "back_image_url": back_image_url,
                    "rulings": rulings_by_oracle_id.get(oid, []),
                }

        with self.cache_file.open("w", encoding="utf-8") as f:
            json.dump(self.cards, f, ensure_ascii=False)

    def _compile_matcher(self):
        if not self.cards:
            return
        valid_names = sorted(
            (name for name in self.cards if len(name) >= 4),
            key=len,
            reverse=True,
        )
        escaped = "|".join(re.escape(n) for n in valid_names)
        self._name_regex = re.compile(rf"\b({escaped})\b", re.IGNORECASE)

    def extract_cards_from_query(self, query: str) -> list[dict]:
        if not self._name_regex:
            return []
        seen = set()
        matches = []
        for m in self._name_regex.finditer(query):
            key = m.group(1).lower()
            if key not in seen and key in self.cards:
                seen.add(key)
                matches.append(self.cards[key])
        return matches

    def search_cards(self, query: str, limit: int = 12) -> list[dict]:
        q = query.strip().lower()
        if not q:
            return []

        exact = [self.cards[q]] if q in self.cards else []
        starts = [
            c for k, c in self.cards.items()
            if k != q and k.startswith(q)
        ]
        contains = [
            c for k, c in self.cards.items()
            if k != q and not k.startswith(q) and q in k
        ]
        return (exact + starts + contains)[:limit]