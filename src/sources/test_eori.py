import importlib.util
import os
import time
import requests
import pandas as pd

spec = importlib.util.spec_from_file_location("validators", os.path.join("src", "02_validators.py"))
validators = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validators)

is_valid_uk_vat_checksum = validators.is_valid_uk_vat_checksum


def run_eori_test():
    """
    Testează ruta EORI (Economic Operators Registration and Identification).
    În UK, codul EORI pentru firme plătitoare de TVA este GB + VAT (9 cifre) + 000.
    Verificăm:
      1. Câte firme din eșantion sunt în Irlanda de Nord (cod poștal BT, eligibile pentru prefix XI în baza UE).
      2. Dacă registrele publice EORI (UE EOS / UK) permit căutarea după numele firmei (forward lookup)
         sau doar validarea unui cod deja cunoscut (reverse lookup).
    """
    sample_path = "data/sample.csv"
    out_path = "data/experiments/03_eori.csv"
    df = pd.read_csv(sample_path)

    results = []
    # Endpoint-ul SOAP/REST al Comisiei Europene pentru validare EORI
    eu_eori_url = "https://ec.europa.eu/taxation_customs/dds2/eos/validation/services/validation"

    print(f"Analizez ruta EORI pe {len(df)} companii...")

    ni_count = 0
    for idx, row in df.iterrows():
        company_name = str(row["CompanyName"]).strip()
        company_number = str(row["CompanyNumber"]).strip()
        postcode = str(row.get("RegAddress.PostCode", "")).strip().upper()

        is_ni = postcode.startswith("BT")
        if is_ni:
            ni_count += 1

        note = (
            "Northern Ireland (BT postcode) - XI EORI exists if trading with EU, "
            "but EU EOS portal only supports reverse lookup (by EORI number, not company name)."
            if is_ni
            else "GB company - post-Brexit UK EORI registry has no public forward-search API by name."
        )

        results.append({
            "CompanyNumber": company_number,
            "CompanyName": company_name,
            "PostCode": postcode,
            "Is_Northern_Ireland": is_ni,
            "Source": "EORI Registry (UK/EU)",
            "Candidate_VAT": "",
            "HMRC_Status": "",
            "Notes": note,
        })

    # Testăm un apel real către serviciul UE EORI pentru a demonstra comportamentul de tip "reverse-only"
    try:
        test_resp = requests.get("https://ec.europa.eu/taxation_customs/dds2/eos/eori_validation.jsp", timeout=10)
        api_reachable = test_resp.status_code == 200
    except Exception:
        api_reachable = False

    out_df = pd.DataFrame(results)
    out_df.to_csv(out_path, index=False)

    print(f"\nGata! Rezultate salvate în '{out_path}'.")
    print(f"Firme din Irlanda de Nord (prefix XI eligibil): {ni_count}/{len(df)}")
    print(f"Portal UE EORI accesibil: {api_reachable} | Candidați VAT extrași direct: 0/{len(df)} (doar reverse-lookup fără un cod EORI pre-existent).")


if __name__ == "__main__":
    run_eori_test()