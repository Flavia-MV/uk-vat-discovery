import importlib.util
import os
import time
import requests
import pandas as pd

spec = importlib.util.spec_from_file_location("validators", os.path.join("src", "02_validators.py"))
validators = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validators)

extract_vat_candidates = validators.extract_vat_candidates
verify_vat_with_hmrc = validators.verify_vat_with_hmrc


def run_contracts_finder_test():
    sample_path = "data/sample.csv"
    out_path = "data/experiments/02_contracts.csv"
    df = pd.read_csv(sample_path)

    # Contracts Finder OCDS (Open Contracting Data Standard) public search API
    search_url = "https://www.contractsfinder.service.gov.uk/Published/Notices/OCDS/Search"
    results = []

    print(f"Testez {len(df)} companii în UK Contracts Finder (achiziții publice)...")

    for idx, row in df.iterrows():
        company_name = str(row["CompanyName"]).strip()
        company_number = str(row["CompanyNumber"]).strip()

        found_vat = ""
        notice_count = 0
        notes = "No public contracts found"

        try:
            # Căutăm numele companiei în anunțurile de achiziții publice
            resp = requests.get(
                search_url,
                params={"keyword": f'"{company_name}"', "size": 5},
                timeout=12,
            )
            if resp.status_code == 200:
                data = resp.json()
                releases = data.get("releases", [])
                notice_count = len(releases)
                if notice_count > 0:
                    notes = f"Found in {notice_count} notices, but no VAT exposed"
                    # Convertim tot JSON-ul returnat în text și căutăm candidați VAT
                    raw_blob = str(releases)
                    candidates = extract_vat_candidates(raw_blob)
                    if candidates:
                        found_vat = candidates[0]
                        notes = f"Extracted VAT from {notice_count} public contract notices"
            else:
                notes = f"HTTP {resp.status_code}"
        except Exception as e:
            notes = f"Error: {e}"

        hmrc_status = ""
        hmrc_name = ""
        if found_vat:
            hmrc_res = verify_vat_with_hmrc(found_vat, company_name)
            hmrc_status = hmrc_res["status"]
            hmrc_name = hmrc_res["registered_name"]

        results.append({
            "CompanyNumber": company_number,
            "CompanyName": company_name,
            "Source": "Contracts Finder (UK Gov)",
            "Notices_Found": notice_count,
            "Candidate_VAT": found_vat,
            "HMRC_Status": hmrc_status,
            "HMRC_Registered_Name": hmrc_name,
            "Notes": notes,
        })

        if (idx + 1) % 25 == 0:
            print(f"  Procesat {idx + 1}/{len(df)}...")
        time.sleep(0.25)

    out_df = pd.DataFrame(results)
    out_df.to_csv(out_path, index=False)
    found_count = (out_df["Candidate_VAT"] != "").sum()
    notices_total = (out_df["Notices_Found"] > 0).sum()
    print(f"\nGata! Rezultate salvate în '{out_path}'.")
    print(f"Companii găsite în licitații: {notices_total}/{len(df)} | Candidați VAT extrași: {found_count}/{len(df)}")


if __name__ == "__main__":
    run_contracts_finder_test()