from concurrent.futures import ThreadPoolExecutor, as_completed
import importlib.util
import io
import os
import re
import time
import requests
from pypdf import PdfReader
from ddgs import DDGS
import pandas as pd

spec = importlib.util.spec_from_file_location("validators", os.path.join("src", "02_validators.py"))
validators = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validators)

extract_vat_candidates = validators.extract_vat_candidates
verify_vat_with_hmrc = validators.verify_vat_with_hmrc

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}


def extract_vat_from_pdf_url(pdf_url: str) -> list[str]:
    """Descarcă un PDF în memorie și extrage codurile de TVA valide matematic din primele 10 pagini."""
    try:
        resp = requests.get(pdf_url, headers=HEADERS, timeout=10)
        if resp.status_code != 200 or b"%PDF" not in resp.content[:1024]:
            return []

        reader = PdfReader(io.BytesIO(resp.content))
        full_text = [page.extract_text() or "" for page in reader.pages[:10]]
        return extract_vat_candidates("\n".join(full_text))
    except Exception:
        return []


def process_single_company(row: dict, pdf_map: dict) -> dict:
    company_name = str(row["CompanyName"]).strip()
    company_number = str(row["CompanyNumber"]).strip()

    existing_pdfs = [u for u in pdf_map.get(company_number, "").split(";") if u.strip()]
    searched_pdfs = []

    if not existing_pdfs:
        clean_name = re.sub(r"\b(LIMITED|LTD|PLC|LLP)\b", "", company_name, flags=re.IGNORECASE).strip()
        try:
            with DDGS() as ddgs:
                hits = list(ddgs.text(f'"{clean_name}" UK VAT filetype:pdf', region="uk-en", max_results=2))
                for h in hits:
                    href = h.get("href", "")
                    if href.lower().endswith(".pdf"):
                        searched_pdfs.append(href)
            time.sleep(0.3)
        except Exception:
            pass

    all_pdf_urls = list(dict.fromkeys(existing_pdfs + searched_pdfs))[:4]
    found_vats = set()
    matched_pdf = ""

    for pdf_url in all_pdf_urls:
        vats = extract_vat_from_pdf_url(pdf_url)
        if vats:
            found_vats.update(vats)
            matched_pdf = pdf_url
            break

    chosen_vat = sorted(found_vats)[0] if found_vats else ""
    hmrc_status = ""
    hmrc_name = ""
    if chosen_vat:
        hmrc_res = verify_vat_with_hmrc(chosen_vat, company_name)
        hmrc_status = hmrc_res["status"]
        hmrc_name = hmrc_res["registered_name"]

    notes = f"Checked {len(all_pdf_urls)} PDF(s)" if all_pdf_urls else "No public PDFs found"
    if chosen_vat:
        notes += " - extracted valid VAT"

    return {
        "CompanyNumber": company_number,
        "CompanyName": company_name,
        "Source": "Public PDF Documents",
        "PDFs_Checked": len(all_pdf_urls),
        "Matched_PDF_URL": matched_pdf,
        "Candidate_VAT": chosen_vat,
        "HMRC_Status": hmrc_status,
        "HMRC_Registered_Name": hmrc_name,
        "Notes": notes,
    }


def run_pdfs_test():
    sample_path = "data/sample.csv"
    web_path = "data/experiments/04_websites.csv"
    out_path = "data/experiments/05_pdfs.csv"

    df = pd.read_csv(sample_path)
    web_df = pd.read_csv(web_path).fillna("") if os.path.exists(web_path) else pd.DataFrame()
    pdf_map = dict(zip(web_df["CompanyNumber"].astype(str), web_df["PDF_Links_Found"])) if not web_df.empty else {}

    rows = df.to_dict("records")
    results = []
    print(f"Testez extracția din PDF în paralel (6 workers) pentru {len(rows)} companii...")

    # Folosim 6 workers ca să nu luăm rate-limit de la DDGS
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {executor.submit(process_single_company, r, pdf_map): i for i, r in enumerate(rows)}
        for idx, future in enumerate(as_completed(futures), 1):
            results.append(future.result())
            if idx % 50 == 0 or idx == len(rows):
                found_so_far = sum(1 for r in results if r["Candidate_VAT"])
                print(f"  Procesat {idx}/{len(rows)} | Candidați VAT din PDF: {found_so_far}")

    out_df = pd.DataFrame(results)
    out_df.to_csv(out_path, index=False)
    vats_total = (out_df["Candidate_VAT"] != "").sum()
    print(f"\nGata! Rezultate salvate în '{out_path}'. Candidați VAT găsiți în PDF-uri: {vats_total}/{len(df)}")


if __name__ == "__main__":
    run_pdfs_test()