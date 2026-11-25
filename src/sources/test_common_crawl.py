from concurrent.futures import ThreadPoolExecutor, as_completed
import gzip
import importlib.util
import json
import os
from urllib.parse import urlparse
import requests
import pandas as pd

spec = importlib.util.spec_from_file_location("validators", os.path.join("src", "02_validators.py"))
validators = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validators)

extract_vat_candidates = validators.extract_vat_candidates
verify_vat_with_hmrc = validators.verify_vat_with_hmrc


def get_latest_cc_index() -> str:
    try:
        resp = requests.get("https://index.commoncrawl.org/collinfo.json", timeout=10)
        if resp.status_code == 200:
            return resp.json()[0]["cdx-api"]
    except Exception:
        pass
    return "https://index.commoncrawl.org/CC-MAIN-2026-40-index"


def query_common_crawl_domain(cdx_api: str, domain: str) -> tuple[list[str], int, str]:
    if not domain:
        return [], 0, "No domain"

    try:
        resp = requests.get(
            cdx_api,
            params={"url": f"{domain}/*", "output": "json", "limit": 15, "filter": "status:200"},
            timeout=10,
        )
        if resp.status_code != 200 or not resp.text.strip():
            return [], 0, "Not indexed in latest Common Crawl"

        records = [json.loads(line) for line in resp.text.strip().splitlines() if line.strip()]
        prioritized = sorted(
            records,
            key=lambda r: any(k in r.get("url", "").lower() for k in ("contact", "term", "privacy", "about", "legal")),
            reverse=True,
        )[:2]

        found_vats = set()
        for rec in prioritized:
            filename = rec.get("filename")
            offset = int(rec.get("offset", 0))
            length = int(rec.get("length", 0))
            if not filename or not length:
                continue

            warc_url = f"https://data.commoncrawl.org/{filename}"
            warc_resp = requests.get(
                warc_url,
                headers={"Range": f"bytes={offset}-{offset + length - 1}"},
                timeout=10,
            )
            if warc_resp.status_code in (200, 206):
                raw_bytes = gzip.decompress(warc_resp.content)
                text = raw_bytes.decode("utf-8", errors="ignore")
                found_vats.update(extract_vat_candidates(text))

        return sorted(found_vats), len(records), f"{len(records)} archived pages in CC"
    except Exception as e:
        return [], 0, f"CC error: {type(e).__name__}"


def process_cc_row(row: dict, site_map: dict, cdx_api: str) -> dict:
    company_name = str(row["CompanyName"]).strip()
    company_number = str(row["CompanyNumber"]).strip()
    website = site_map.get(company_number, "")
    domain = urlparse(website).netloc.replace("www.", "") if website else ""

    vats, pages_indexed, note = query_common_crawl_domain(cdx_api, domain)
    chosen_vat = vats[0] if vats else ""

    hmrc_status = ""
    hmrc_name = ""
    if chosen_vat:
        hmrc_res = verify_vat_with_hmrc(chosen_vat, company_name)
        hmrc_status = hmrc_res["status"]
        hmrc_name = hmrc_res["registered_name"]

    return {
        "CompanyNumber": company_number,
        "CompanyName": company_name,
        "Source": "Common Crawl (CDX Archive)",
        "Domain": domain,
        "Archived_Pages": pages_indexed,
        "Candidate_VAT": chosen_vat,
        "HMRC_Status": hmrc_status,
        "HMRC_Registered_Name": hmrc_name,
        "Notes": note,
    }


def run_common_crawl_test():
    sample_path = "data/sample.csv"
    web_path = "data/experiments/04_websites.csv"
    out_path = "data/experiments/06_common_crawl.csv"

    df = pd.read_csv(sample_path)
    web_df = pd.read_csv(web_path).fillna("") if os.path.exists(web_path) else pd.DataFrame()
    site_map = dict(zip(web_df["CompanyNumber"].astype(str), web_df["Discovered_Website"])) if not web_df.empty else {}

    cdx_api = get_latest_cc_index()
    rows = df.to_dict("records")
    results = []

    print(f"Folosesc indexul Common Crawl: {cdx_api}")
    print(f"Testez în paralel (10 workers) {len(rows)} companii...\n")

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(process_cc_row, r, site_map, cdx_api): i for i, r in enumerate(rows)}
        for idx, future in enumerate(as_completed(futures), 1):
            results.append(future.result())
            if idx % 50 == 0 or idx == len(rows):
                found_so_far = sum(1 for r in results if r["Candidate_VAT"])
                print(f"  Procesat {idx}/{len(rows)} | Candidați VAT din Common Crawl: {found_so_far}")

    out_df = pd.DataFrame(results)
    out_df.to_csv(out_path, index=False)
    vats_total = (out_df["Candidate_VAT"] != "").sum()
    print(f"\nGata! Rezultate salvate în '{out_path}'. Candidați VAT găsiți în Common Crawl: {vats_total}/{len(df)}")


if __name__ == "__main__":
    run_common_crawl_test()