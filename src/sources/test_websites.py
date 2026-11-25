from concurrent.futures import ThreadPoolExecutor, as_completed
import importlib.util
import os
import re
import socket
import threading
import time
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
from ddgs import DDGS
import pandas as pd
import requests

spec = importlib.util.spec_from_file_location("validators", os.path.join("src", "02_validators.py"))
validators = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validators)

extract_vat_candidates = validators.extract_vat_candidates
verify_vat_with_hmrc = validators.verify_vat_with_hmrc

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept-Language": "en-GB,en;q=0.9",
}

# Domenii de tip registru / director de firme / social media care nu sunt site-ul oficial al companiei
BLACKLIST_DOMAINS = {
    "find-and-update.company-information.service.gov.uk",
    "gov.uk",
    "companieshouse.gov.uk",
    "endole.co.uk",
    "pomanda.com",
    "duedil.com",
    "creditsafe.com",
    "companycheck.co.uk",
    "bizdb.co.uk",
    "checkcompany.co.uk",
    "uk.globaldatabase.com",
    "dnb.com",
    "opencorporates.com",
    "linkedin.com",
    "facebook.com",
    "instagram.com",
    "twitter.com",
    "x.com",
    "youtube.com",
    "yell.com",
    "trustpilot.com",
    "bloomberg.com",
    "zoominfo.com",
    "corpium.co.uk",
    "rooplex.co.uk",
    "companieslist.co.uk",
    "ukcompanydir.com",
    "efinder.uk",
    "opengovuk.com",
    "chamberofcommerce.co.uk",
    "suite.endole.co.uk",
    "globaldatabase.com",
}

LEGAL_KEYWORDS = ("contact", "about", "privacy", "terms", "legal", "imprint", "cookie", "policy", "faq")

# Lock ușor pentru a spația cererile către DDGS și a evita rate-limiting-ul
ddgs_lock = threading.Lock()


def is_blacklisted(url: str) -> bool:
    try:
        netloc = urlparse(url).netloc.lower().replace("www.", "")
        return any(netloc == b or netloc.endswith("." + b) for b in BLACKLIST_DOMAINS)
    except Exception:
        return True


def clean_company_slug(company_name: str) -> str:
    clean = re.sub(r"\b(LIMITED|LTD|PLC|LLP|CIC|THE|AND|&)\b", "", company_name, flags=re.IGNORECASE)
    return re.sub(r"[^a-z0-9]", "", clean.lower())


def guess_domain_via_dns(company_name: str) -> str:
    """Fallback rapid prin verificare DNS pentru domenii .co.uk / .uk / .com."""
    slug = clean_company_slug(company_name)
    if len(slug) < 4:
        return ""
    for tld in (".co.uk", ".uk", ".com"):
        domain = f"{slug}{tld}"
        try:
            socket.gethostbyname(domain)
            return f"https://{domain}"
        except socket.gaierror:
            continue
    return ""


def discover_website(company_name: str, postcode: str) -> tuple[str, str]:
    """Caută site-ul oficial folosind DDGS și fallback DNS."""
    clean_name = re.sub(r"\b(LIMITED|LTD|PLC|LLP)\b", "", company_name, flags=re.IGNORECASE).strip()
    query = f'"{clean_name}" {postcode} UK'.strip()

    try:
        with ddgs_lock:
            with DDGS() as ddgs:
                results = list(ddgs.text(query, region="uk-en", max_results=5))
            time.sleep(0.35)

        for res in results:
            href = res.get("href", "")
            if href.startswith("http") and not is_blacklisted(href):
                parsed = urlparse(href)
                root_url = f"{parsed.scheme}://{parsed.netloc}"
                return root_url, "DDGS Search"
    except Exception:
        pass

    guessed = guess_domain_via_dns(company_name)
    if guessed:
        return guessed, "DNS Heuristic"

    return "", "Not Found"


def scrape_site_for_vat_and_pdfs(base_url: str) -> tuple[list[str], list[str], str]:
    """Scanează homepage-ul și până la 3 pagini legale/contact; colectează și link-urile .pdf."""
    found_vats = set()
    pdf_links = set()

    try:
        resp = requests.get(base_url, headers=HEADERS, timeout=8)
        if resp.status_code != 200:
            return [], [], f"HTTP {resp.status_code}"

        soup = BeautifulSoup(resp.text, "lxml")
        text = soup.get_text(" ", strip=True)
        found_vats.update(extract_vat_candidates(text))

        subpages = []
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            full_url = urljoin(base_url, href)
            if urlparse(full_url).netloc != urlparse(base_url).netloc:
                continue

            lower_url = full_url.lower()
            if lower_url.endswith(".pdf"):
                pdf_links.add(full_url)
            elif any(k in lower_url for k in LEGAL_KEYWORDS) and full_url != base_url:
                if full_url not in subpages:
                    subpages.append(full_url)

        for sub_url in subpages[:3]:
            if found_vats:
                break
            try:
                sub_resp = requests.get(sub_url, headers=HEADERS, timeout=6)
                if sub_resp.status_code == 200:
                    sub_soup = BeautifulSoup(sub_resp.text, "lxml")
                    found_vats.update(extract_vat_candidates(sub_soup.get_text(" ", strip=True)))
                    for a in sub_soup.find_all("a", href=True):
                        u = urljoin(sub_url, a["href"].strip())
                        if u.lower().endswith(".pdf"):
                            pdf_links.add(u)
            except Exception:
                continue

        status_note = f"Scraped OK ({len(subpages[:3])} subpages checked)"
        return sorted(found_vats), sorted(pdf_links)[:5], status_note
    except Exception as e:
        return [], [], f"Scrape error: {type(e).__name__}"


def process_single_company(row: dict) -> dict:
    company_name = str(row["CompanyName"]).strip()
    company_number = str(row["CompanyNumber"]).strip()
    postcode = str(row.get("RegAddress.PostCode", "")).strip()

    website, discovery_method = discover_website(company_name, postcode)

    vats = []
    pdfs = []
    notes = discovery_method

    if website:
        vats, pdfs, scrape_note = scrape_site_for_vat_and_pdfs(website)
        notes = f"{discovery_method} | {scrape_note}"

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
        "Source": "Company Website Scraping",
        "Discovered_Website": website,
        "Candidate_VAT": chosen_vat,
        "All_Candidate_VATs": ";".join(vats),
        "PDF_Links_Found": ";".join(pdfs),
        "HMRC_Status": hmrc_status,
        "HMRC_Registered_Name": hmrc_name,
        "Notes": notes,
    }


def run_websites_test():
    sample_path = "data/sample.csv"
    out_path = "data/experiments/04_websites.csv"

    df = pd.read_csv(sample_path, dtype=str).fillna("")
    rows = df.to_dict("records")
    results = []

    print(f"Testez descoperirea site-urilor și scraping-ul în paralel (8 workers) pentru {len(rows)} companii...\n")

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(process_single_company, r): i for i, r in enumerate(rows)}
        for idx, future in enumerate(as_completed(futures), 1):
            results.append(future.result())
            if idx % 25 == 0 or idx == len(rows):
                sites_found = sum(1 for r in results if r["Discovered_Website"])
                vats_found = sum(1 for r in results if r["Candidate_VAT"])
                print(f"  Procesat {idx}/{len(rows)} | Site-uri găsite: {sites_found} | Candidați VAT: {vats_found}")

    out_df = pd.DataFrame(results)
    out_df.to_csv(out_path, index=False)

    sites_total = (out_df["Discovered_Website"] != "").sum()
    vats_total = (out_df["Candidate_VAT"] != "").sum()
    print(f"\nGata! Rezultate salvate în '{out_path}'.")
    print(f"Site-uri descoperite: {sites_total}/{len(df)} | Candidați VAT găsiți: {vats_total}/{len(df)}")


if __name__ == "__main__":
    run_websites_test()