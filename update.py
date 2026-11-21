import re
import json
import urllib.parse
import urllib.request
from pathlib import Path

BASE_DIR = Path(__file__).parent.resolve()

FOLDER_FILE_PATTERNS = {
    "mtg": [
        r"^MagicCompRules.*\.pdf$",
        r"^oracle-cards.*\.jsonl\.gz$",
        r"^rulings.*\.jsonl\.gz$",
    ],
    "pokemon": [],
    "one_piece": [],
}


def encode_url(raw_url: str) -> str:
    parts = urllib.parse.urlsplit(raw_url)
    safe_path = urllib.parse.quote(urllib.parse.unquote(parts.path), safe="/:@!$&'()*+,;=-._~")
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, safe_path, parts.query, parts.fragment))


def get_mtg_download_urls() -> dict[str, str]:
    headers = {
        "User-Agent": "TCGRulesUpdater/1.0",
        "Accept": "application/json;q=0.9,*/*;q=0.8",
    }
    urls = {}

    req = urllib.request.Request("https://api.scryfall.com/bulk-data", headers=headers)
    with urllib.request.urlopen(req, timeout=15) as resp:
        bulk_data = json.loads(resp.read().decode("utf-8")).get("data", [])

    for item in bulk_data:
        if item.get("type") == "oracle_cards":
            urls[r"^oracle-cards.*\.jsonl\.gz$"] = item["jsonl_download_uri"]
        elif item.get("type") == "rulings":
            urls[r"^rulings.*\.jsonl\.gz$"] = item["jsonl_download_uri"]

    rules_req = urllib.request.Request("https://magic.wizards.com/en/rules", headers={"User-Agent": "TCGRulesUpdater/1.0", "Accept": "text/html,*/*"},)
    with urllib.request.urlopen(rules_req, timeout=15) as resp:
        html = resp.read().decode("utf-8", errors="ignore")

    pdf_match = re.search(r'https://media\.wizards\.com/[^"\']+/MagicCompRules[^"\']*\.pdf', html)
    if pdf_match:
        urls[r"^MagicCompRules.*\.pdf$"] = pdf_match.group(0)

    return urls


def update_folder_files(folder_name: str, patterns: list[str], download_urls: dict[str, str]):
    target_dir = BASE_DIR / folder_name
    target_dir.mkdir(parents=True, exist_ok=True)

    headers = {
        "User-Agent": "TCGRulesUpdater/1.0",
        "Accept": "*/*",
    }

    for pattern in patterns:
        raw_url = download_urls.get(pattern)
        if not raw_url:
            print(f"[{folder_name}] No download URL found for pattern: {pattern}")
            continue

        safe_url = encode_url(raw_url)
        new_filename = urllib.parse.unquote(urllib.parse.urlsplit(safe_url).path.split("/")[-1])
        new_filepath = target_dir / new_filename

        if new_filepath.exists():
            print(f"[{folder_name}] Already up to date: {new_filename}")
            continue

        print(f"[{folder_name}] Downloading {new_filename}...")
        req = urllib.request.Request(safe_url, headers=headers)
        with urllib.request.urlopen(req, timeout=60) as resp, new_filepath.open("wb") as out:
            out.write(resp.read())

        print(f"[{folder_name}] Saved: {new_filepath}")

        # Delete older matching files only after the new file has finished downloading
        for existing_file in target_dir.iterdir():
            if (
                existing_file.is_file()
                and existing_file != new_filepath
                and re.match(pattern, existing_file.name)
            ):
                print(f"[{folder_name}] Removing old file: {existing_file.name}")
                existing_file.unlink()


if __name__ == "__main__":
    mtg_urls = get_mtg_download_urls()
    for folder, patterns in FOLDER_FILE_PATTERNS.items():
        if folder == "mtg":
            update_folder_files(folder, patterns, mtg_urls)