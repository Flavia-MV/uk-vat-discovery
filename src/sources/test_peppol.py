import importlib.util
import os
import time
import requests
import pandas as pd

spec = importlib.util.spec_from_file_location("validators", os.path.join("src", "02_validators.py"))
validators = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validators)

clean_vat_number = validators.clean_vat_number
is_valid_uk_vat_checksum = validators.is_valid_uk_vat_checksum
verify_vat_with_hmrc = validators.verify_vat_with_hmrc


def run_peppol_test():
    sample_path = "data/sample.csv"
    out_path = "data/experiments/01_peppol.csv"
    df = pd.read_csv(sample_path)

    results = []
    base_url = "https://directory.peppol.eu/search/1.0/json"

    print(f"Testez {len(df)} companii în Peppol Directory API...")

    for idx, row in df.iterrows():
        company_name = str(row["CompanyName"]).strip()
        company_number = str(row["CompanyNumber"]).strip()

        search_term = company_name.replace("LIMITED", "").replace("LTD", "").strip()

        found_vat = ""
        peppol_match_name = ""
        peppol_scheme = ""
        notes = "Not found in Peppol"

        try:
            resp = requests.get(
                base_url,
                params={"name": search_term, "country": "GB"},
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                matches = data.get("matches", [])
                if matches:
                    notes = f"{len(matches)} matches found, no 9932 VAT scheme"
                    for m in matches:
                        participant_id = m.get("participantID", {}).get("value", "")
                        entities = m.get("entities", [])
                        entity_name = entities[0].get("name", [{}])[0].get("name", "") if entities else ""

                        if participant_id.startswith("9932:") or "gb" in participant_id.lower():
                            raw_code = participant_id.split(":")[-1]
                            cleaned = clean_vat_number(raw_code)
                            if is_valid_uk_vat_checksum(cleaned):
                                found_vat = cleaned
                                peppol_match_name = entity_name
                                peppol_scheme = participant_id
                                notes = "Found valid UK VAT in Peppol"
                                break

                        for ent in entities:
                            for ident in ent.get("identifiers", []):
                                scheme = ident.get("scheme", "")
                                val = ident.get("value", "")
                                if scheme == "9932" or val.upper().startswith("GB"):
                                    cleaned = clean_vat_number(val)
                                    if is_valid_uk_vat_checksum(cleaned):
                                        found_vat = cleaned
                                        peppol_match_name = entity_name
                                        peppol_scheme = f"{scheme}:{val}"
                                        notes = "Found valid UK VAT in entity identifiers"
                                        break
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
            "Source": "Peppol Directory",
            "Candidate_VAT": found_vat,
            "Source_Matched_Name": peppol_match_name,
            "Scheme_Detail": peppol_scheme,
            "HMRC_Status": hmrc_status,
            "HMRC_Registered_Name": hmrc_name,
            "Notes": notes,
        })

        if (idx + 1) % 25 == 0:
            print(f"  Procesat {idx + 1}/{len(df)}...")
        time.sleep(0.2)

    out_df = pd.DataFrame(results)
    out_df.to_csv(out_path, index=False)
    found_count = (out_df["Candidate_VAT"] != "").sum()
    print(f"\nGata! Rezultate salvate în '{out_path}'. Candidați VAT găsiți: {found_count}/{len(df)}")


if __name__ == "__main__":
    run_peppol_test()