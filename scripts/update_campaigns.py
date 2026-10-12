"""Append explicitly requested modern CUFFS campaigns; preserve sale batches.

Requires beautifulsoup4. Defaults to a dry run; --apply updates the catalog.
Never deduplicates products across campaigns or replaces existing campaigns.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import time
import urllib.parse
import urllib.request

from bs4 import BeautifulSoup


ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "outputs/yosuga-sales-preview/campaigns.json"
RELATED = re.compile(r"ヨスガノソラ|ハルカナソラ|Yosuga\s*no\s*Sora|Haruka\s*na\s*Sora", re.I)


def fetch(url):
    request = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; SalesArchive/1.0)",
        "Connection": "close", "Accept-Encoding": "identity",
    })
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                # Official modern campaign pages explicitly declare UTF-8.
                html = response.read().decode("utf-8", errors="strict")
            return BeautifulSoup(html, "html.parser")
        except (OSError, ValueError):
            if attempt == 2:
                raise
            time.sleep(1)


def text(node):
    if node is None:
        raise ValueError("Required official field is missing")
    return re.sub(r"[ \t]+", " ", node.get_text(" ", strip=True)).strip()


def paragraphs(node):
    for br in node.select("br"):
        br.replace_with("\n")
    return "\n".join(re.sub(r"[ \t]+", " ", line).strip()
                     for line in node.get_text().strip().splitlines() if line.strip())


def parse_campaign(slug):
    if not re.fullmatch(r"20\d{4}[a-z]", slug):
        raise ValueError(f"Invalid campaign slug: {slug}")
    base = f"https://www.cuffs.co.jp/main/store/{slug}/"
    index, detail = fetch(base), fetch(base + "detail.php")
    index_cards, detail_cards = index.select(".item-thumbnail"), detail.select(".item-detail")
    index_ids = [node["id"] for node in index_cards]
    detail_ids = [node["id"] for node in detail_cards]
    if not index_ids or index_ids != detail_ids or len(index_ids) != len(set(index_ids)):
        raise ValueError(f"{slug}: index/detail product IDs do not match")

    selected_index = [node["id"] for node in index_cards if RELATED.search(text(node))]
    selected_detail = [node["id"] for node in detail_cards if RELATED.search(text(node))]
    if selected_index != selected_detail:
        raise ValueError(f"{slug}: related product selection differs between index/detail")

    date_node = index.find(string=lambda value: value and "注文の受付は" in value)
    if date_node is None:
        raise ValueError(f"{slug}: official order period is missing")
    date_line = text(date_node.parent)
    dates = re.findall(r"(20\d{2})年(\d{1,2})月(\d{1,2})日", date_line)
    if len(dates) != 2:
        raise ValueError(f"{slug}: cannot parse order dates: {date_line}")
    start_date, end_date = ["-".join([y, m.zfill(2), d.zfill(2)]) for y, m, d in dates]
    products = []
    for node in detail_cards:
        if node["id"] not in selected_detail:
            continue
        body = node.select_one(".detail-body")
        sections = {}
        for heading in body.select("h2"):
            note = heading.find_next_sibling("div", class_="note")
            if note is None:
                raise ValueError(f"{slug}/{node['id']}: missing detail section")
            sections[text(heading)] = paragraphs(note)
        price = text(body.select_one(".price .strong"))
        images = list(dict.fromkeys(urllib.parse.urljoin(base, image["src"])
                                   for image in node.select(".top-image img")))
        if not images:
            raise ValueError(f"{slug}/{node['id']}: missing official image")
        sequence = len(products) + 1
        product = {
            "artist": sections["原画"], "description": sections["商品説明"],
            "image": images[0], "name": text(node.select_one("h1")),
            "priceText": price, "priceYen": int(re.sub(r"\D", "", price)),
            "productId": node["id"], "sequence": sequence,
            "specification": sections["商品仕様"],
            "status": text(body.select_one("button")), "work": text(body.select_one(".title")),
            "recordId": f"{slug}-{sequence:03d}",
        }
        if len(images) > 1:
            product["additionalImages"] = images[1:]
        if not all(product.values()) or "\ufffd" in json.dumps(product, ensure_ascii=False):
            raise ValueError(f"{slug}/{node['id']}: empty or corrupted product field")
        products.append(product)
    title = text(index.title)
    campaign = {
        "slug": slug, "detailUrl": base + "detail.php", "title": title,
        "products": products, "campaignUrl": base, "campaignTitle": title,
        "campaignText": index.get_text("\n", strip=True),
        "startDate": start_date, "endDate": end_date, "dateLine": date_line,
    }
    return campaign, {"slug": slug, "officialProductCount": len(index_ids),
                      "relatedProductCount": len(products), "selectedIds": selected_detail,
                      "startDate": start_date, "endDate": end_date}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("campaigns", nargs="+")
    parser.add_argument("--expected-count", type=int, help="Expected related count per campaign")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    archive = json.loads(CATALOG.read_text(encoding="utf-8"))
    existing = {campaign["slug"] for campaign in archive["campaigns"]}
    if len(set(args.campaigns)) != len(args.campaigns) or existing.intersection(args.campaigns):
        raise ValueError("Campaign is repeated or already archived; nothing was changed")
    with ThreadPoolExecutor(max_workers=2) as pool:
        parsed = list(pool.map(parse_campaign, args.campaigns))
    campaigns, audits = zip(*parsed)
    if args.expected_count is not None and any(len(c["products"]) != args.expected_count for c in campaigns):
        raise ValueError("Unexpected related product count; nothing was changed")
    archive["campaigns"] = sorted([*archive["campaigns"], *campaigns], key=lambda c: c["startDate"], reverse=True)
    archive["campaignCount"] = len(archive["campaigns"])
    archive["productRecordCount"] = sum(len(c["products"]) for c in archive["campaigns"])
    archive["generatedAt"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if args.apply:
        # A mechanical JSON data rewrite; the existing objects are preserved.
        CATALOG.write_text(json.dumps(archive, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"applied": args.apply, "campaignCount": archive["campaignCount"],
                      "productRecordCount": archive["productRecordCount"], "audit": audits},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
