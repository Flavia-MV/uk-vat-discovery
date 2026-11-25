import os
import pandas as pd


def main():
    sample_df = pd.read_csv("data/sample.csv", dtype=str).fillna("")
    hmrc_path = "data/hmrc_official_verification.csv"
    if not os.path.exists(hmrc_path):
        raise FileNotFoundError("Rulează mai întâi src/04_verify_hmrc.py!")

    hmrc_df = pd.read_csv(hmrc_path, dtype=str).fillna("")
    confirmed = hmrc_df[hmrc_df["HMRC_Confirmed_TP"] == "True"].drop_duplicates(subset=["CompanyNumber"])
    confirmed_map = confirmed.set_index("CompanyNumber").to_dict(orient="index")

    final_rows = []
    for _, row in sample_df.iterrows():
        cnum = row["CompanyNumber"].strip()
        if cnum in confirmed_map:
            hit = confirmed_map[cnum]
            final_rows.append({
                "CompanyNumber": cnum,
                "CompanyName": row["CompanyName"].strip(),
                "AccountCategory": row.get("Accounts.AccountCategory", "").strip(),
                "PostCode": row.get("RegAddress.PostCode", "").strip(),
                "Verified_VAT": hit["Candidate_VAT"],
                "Winning_Source": hit["Source"],
                "HMRC_Registered_Name": hit["HMRC_Registered_Name"],
                "HMRC_Status": hit["HMRC_Status"],
                "Name_Similarity": hit["Name_Similarity"],
            })
        else:
            final_rows.append({
                "CompanyNumber": cnum,
                "CompanyName": row["CompanyName"].strip(),
                "AccountCategory": row.get("Accounts.AccountCategory", "").strip(),
                "PostCode": row.get("RegAddress.PostCode", "").strip(),
                "Verified_VAT": "",
                "Winning_Source": "",
                "HMRC_Registered_Name": "",
                "HMRC_Status": "NOT_FOUND_OR_REJECTED",
                "Name_Similarity": "",
            })

    out_df = pd.DataFrame(final_rows)
    out_df.to_csv("data/final_verified.csv", index=False)
    print(f"Salvat data/final_verified.csv: {len(confirmed_map)} / {len(out_df)} companii confirmate oficial de HMRC.")


if __name__ == "__main__":
    main()
