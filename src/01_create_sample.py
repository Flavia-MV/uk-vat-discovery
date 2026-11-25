import glob
import os
import pandas as pd

DATA_DIR = "data"
OUTPUT_SAMPLE = os.path.join(DATA_DIR, "sample.csv")
SAMPLE_SIZE = 250
RANDOM_SEED = 42


def find_companies_house_csv() -> str:
    matches = sorted(glob.glob(os.path.join(DATA_DIR, "BasicCompanyData*.csv")))
    if not matches:
        raise FileNotFoundError(
            "Nu am găsit niciun fișier BasicCompanyData*.csv în folderul 'data/'. "
            "Descarcă snapshot-ul gratuit de la Companies House și pune-l în 'data/'."
        )
    return matches[-1]


def build_representative_sample():
    csv_path = find_companies_house_csv()
    print(f"Citesc fișierul: {csv_path} ...")

    # Citim doar coloanele utile ca să economisim RAM
    cols_to_keep = [
        "CompanyName",
        " CompanyNumber",  # Companies House are uneori un spațiu în fața numelui coloanei
        "RegAddress.PostTown",
        "RegAddress.PostCode",
        "CompanyCategory",
        "CompanyStatus",
        "IncorporationDate",
        "Accounts.AccountCategory",
        "SICCode.SicText_1",
    ]

    # Citim header-ul real pentru a curăța spațiile din numele coloanelor
    header = pd.read_csv(csv_path, nrows=0).columns.tolist()
    clean_map = {col: col.strip() for col in header}
    wanted_clean = {c.strip() for c in cols_to_keep}
    usecols = [col for col in header if col.strip() in wanted_clean]

    chunks = []
    # Procesăm pe bucăți de 200.000 de rânduri
    for chunk in pd.read_csv(csv_path, usecols=usecols, chunksize=200_000, low_memory=False):
        chunk = chunk.rename(columns=clean_map)
        # Filtrăm doar companiile active (PLC, LTD, LLP)
        active = chunk[
            (chunk["CompanyStatus"] == "Active")
            & (chunk["CompanyCategory"].isin(["Private Limited Company", "Public Limited Company", "Limited Liability Partnership"]))
        ]
        # Luăm un sub-eșantion din fiecare chunk pentru eficiență
        if len(active) > 0:
            chunks.append(active.sample(frac=0.01, random_state=RANDOM_SEED))

    pool = pd.concat(chunks, ignore_index=True)
    print(f"Total companii active în pool-ul intermediar: {len(pool):,}")

    # Curățăm valorile lipsă la AccountCategory
    pool["Accounts.AccountCategory"] = pool["Accounts.AccountCategory"].fillna("UNKNOWN")

   # Eșantionare stratificată proporțională după dimensiunea companiei (AccountCategory)
    sampled_parts = []
    for cat, group in pool.groupby("Accounts.AccountCategory"):
        n_cat = max(1, int(round(SAMPLE_SIZE * len(group) / len(pool))))
        sampled_parts.append(group.sample(n=min(n_cat, len(group)), random_state=RANDOM_SEED))
    sample = pd.concat(sampled_parts, ignore_index=True)

    # Ajustăm exact la SAMPLE_SIZE
    if len(sample) > SAMPLE_SIZE:
        sample = sample.sample(n=SAMPLE_SIZE, random_state=RANDOM_SEED)

    sample = sample.reset_index(drop=True)
    sample.to_csv(OUTPUT_SAMPLE, index=False)
    print(f"\nEșantion salvat cu succes în '{OUTPUT_SAMPLE}' ({len(sample)} companii).")
    print("\nDistribuția după tipul de bilanț (Accounts.AccountCategory):")
    print(sample["Accounts.AccountCategory"].value_counts())


if __name__ == "__main__":
    build_representative_sample()