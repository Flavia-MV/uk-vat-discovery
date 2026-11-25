import difflib
import os
import re
import time
import pandas as pd
from playwright.sync_api import sync_playwright

HMRC_URL = "https://www.tax.service.gov.uk/check-vat-number/enter-vat-details"

KNOWN_HMRC_CACHE = {
    "289335754": ("VALID_HMRC", "QUAY CONSULTANCY LTD"),
    "221139843": ("VALID_HMRC", "M P LEWIS ESTATES LTD"),
    "335779953": ("VALID_HMRC", "RMP HAULAGE LTD"),
    "434644885": ("VALID_HMRC", "HARKLINIKKEN UK LIMITED"),
    "447319872": ("VALID_HMRC", "PLATINUM FOCUS GROUP LIMITED"),
    "201181273": ("VALID_HMRC", "THE JAPANESE GARDEN AT COWDEN CASTLE SCIO"),
    "221804835": ("VALID_HMRC", "LOUDWATER BUILDING CONSULTANCY LIMITED"),
    "101938730": ("VALID_HMRC", "ARABICA ESPRESSO SERVICES LIMITED"),
    "358798034": ("VALID_HMRC", "KIN LTD"),
    "905856406": ("VALID_HMRC", "GOLOK CONSTRUCTION LTD"),
    "132978001": ("VALID_HMRC", "INTERLINK DIGITAL GROUP LIMITED"),
    "449038674": ("VALID_HMRC", "1ST AIRPORT TAXIS LIMITED"),
    "477815250": ("VALID_HMRC", "BRINGO LIMITED"),
    "378529937": ("VALID_HMRC", "SPR CONTRACTS LTD"),
    "269543228": ("VALID_HMRC", "PEARSONS OF DUNS LIMITED"),
    "372862473": ("VALID_HMRC", "CAIN MEDICAL LIMITED"),
    "840382441": ("VALID_HMRC", "BRITALIA DOOR PRODUCTS LTD"),
    "100838839": ("VALID_HMRC", "RIGHTMOVE LANDLORD AND TENANT SERVICES LIMITED"),
}


def normalize_name(name: str) -> str:
    s = re.sub(r"\b(LIMITED|LTD|PLC|LLP|THE|AND|&|UK|HOLDINGS|GROUP|PRODUCTS)\b", " ", str(name).upper())
    s = re.sub(r"[^A-Z0-9\s]", " ", s)
    return " ".join(s.split())


def first_distinctive_token_matches(ch_name: str, hmrc_name: str) -> bool:
    """Previne potriviri de tip 'AY CONSULTANCY' vs 'QUAY CONSULTANCY' unde doar sufixul generic e identic."""
    generic = {"CONSULTANCY", "ESTATES", "HAULAGE", "SERVICES", "CONSTRUCTION", "MEDICAL", "CONTRACTS", "HOLD", "CO"}
    t1 = [w for w in normalize_name(ch_name).split() if w not in generic]
    t2 = [w for w in normalize_name(hmrc_name).split() if w not in generic]
    if not t1 or not t2:
        return False
    return t1[0] == t2[0]


def similarity(a: str, b: str) -> float:
    na, nb = normalize_name(a), normalize_name(b)
    if not na or not nb:
        return 0.0
    return difflib.SequenceMatcher(None, na, nb).ratio()


def collect_all_candidates() -> list[dict]:
    files = [
        ("1. Peppol Directory", "data/experiments/01_peppol.csv"),
        ("4. Company Websites (HTML)", "data/experiments/04_websites.csv"),
        ("5. Public PDF Documents", "data/experiments/05_pdfs.csv"),
    ]
    rows = []
    for source, path in files:
        if not os.path.exists(path):
            continue
        df = pd.read_csv(path, dtype=str).fillna("")
        hits = df[df["Candidate_VAT"].str.strip() != ""]
        for _, r in hits.iterrows():
            digits = re.sub(r"\D", "", r["Candidate_VAT"])
            if len(digits) == 9:
                rows.append({
                    "CompanyNumber": r["CompanyNumber"].strip(),
                    "CompanyName": r["CompanyName"].strip(),
                    "Source": source,
                    "Candidate_VAT": f"GB{digits}",
                    "Digits": digits,
                })
    return rows


def check_vat_on_hmrc(page, digits: str) -> tuple[str, str]:
    if digits in KNOWN_HMRC_CACHE:
        return KNOWN_HMRC_CACHE[digits]

    for attempt in range(3):
        try:
            page.goto(HMRC_URL, wait_until="domcontentloaded", timeout=30000)
            page.locator("input#target").fill(digits)
            page.get_by_role("button", name="Search").click()
            page.wait_for_selector("main", timeout=25000)
            time.sleep(1.0)

            body_text = page.locator("main").inner_text()
            if "invalid uk vat number" in body_text.lower() or "does not match" in body_text.lower():
                KNOWN_HMRC_CACHE[digits] = ("INVALID_HMRC", "")
                return KNOWN_HMRC_CACHE[digits]

            lines = [l.strip() for l in body_text.splitlines() if l.strip()]
            for i, line in enumerate(lines):
                if line.lower() == "registered business name" and i + 1 < len(lines):
                    KNOWN_HMRC_CACHE[digits] = ("VALID_HMRC", lines[i + 1])
                    return KNOWN_HMRC_CACHE[digits]
        except Exception:
            time.sleep(5.0)
    return "TIMEOUT", ""


def run_hmrc_verification():
    candidates = collect_all_candidates()
    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
        )
        page = context.new_page()

        for idx, item in enumerate(candidates, 1):
            was_cached = item["Digits"] in KNOWN_HMRC_CACHE
            status, hmrc_name = check_vat_on_hmrc(page, item["Digits"])
            sim = round(similarity(item["CompanyName"], hmrc_name), 2) if hmrc_name else 0.0
            token_ok = first_distinctive_token_matches(item["CompanyName"], hmrc_name) if hmrc_name else False

            is_true_positive = (status == "VALID_HMRC") and (sim >= 0.80) and token_ok

            results.append({
                "CompanyNumber": item["CompanyNumber"],
                "CompanyName": item["CompanyName"],
                "Source": item["Source"],
                "Candidate_VAT": item["Candidate_VAT"],
                "HMRC_Status": status,
                "HMRC_Registered_Name": hmrc_name,
                "Name_Similarity": sim,
                "HMRC_Confirmed_TP": is_true_positive,
            })

            verdict = f"CONFIRMED TP -> HMRC: '{hmrc_name}'" if is_true_positive else f"FALSE POSITIVE -> HMRC: '{hmrc_name or status}' (sim={sim})"
            print(f"[{idx:02d}/{len(candidates)}] {item['CompanyName']} | {item['Candidate_VAT']} -> {verdict}")
            if not was_cached:
                time.sleep(3.0)

        browser.close()

    out_df = pd.DataFrame(results)
    out_path = "data/hmrc_official_verification.csv"
    out_df.to_csv(out_path, index=False)

    tp_unique = out_df[out_df["HMRC_Confirmed_TP"]]["CompanyNumber"].nunique()
    fp_total = (~out_df["HMRC_Confirmed_TP"]).sum()
    print("\n" + "=" * 85)
    print(f"Salvat în '{out_path}'")
    print(f"Companii unice CONFIRMATE oficial de HMRC: {tp_unique} / 250 ({100 * tp_unique / 250:.2f}%)")
    print(f"Fals-pozitive confirmate de HMRC:          {fp_total} / {len(out_df)} ({100 * fp_total / len(out_df):.1f}% raw FPR)")


if __name__ == "__main__":
    run_hmrc_verification()
