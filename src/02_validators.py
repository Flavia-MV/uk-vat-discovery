import re
import os
import time
import requests
from rapidfuzz import fuzz
from dotenv import load_dotenv

load_dotenv()

# HMRC API endpoints (Sandbox vs Production)
# Pentru testare reală pe companii din UK ai nevoie de Production credentials de pe HMRC Developer Hub
HMRC_BASE_URL = os.getenv("HMRC_BASE_URL", "https://api.service.hmrc.gov.uk")
HMRC_ACCESS_TOKEN = os.getenv("HMRC_ACCESS_TOKEN", "")


def clean_vat_number(raw_vat: str) -> str:
    """Curăță spațiile, cratimele și prefixul GB/XI, lăsând doar cifrele."""
    if not raw_vat:
        return ""
    cleaned = re.sub(r"[^A-Za-z0-9]", "", str(raw_vat)).upper()
    if cleaned.startswith(("GB", "XI")):
        cleaned = cleaned[2:]
    return cleaned


def is_valid_uk_vat_checksum(vat_digits: str) -> bool:
    """
    Verifică dacă un cod de 9 cifre respectă algoritmul oficial HMRC Modulo 97.
    Notă: Codurile UK de 9 cifre folosesc ponderea [8, 7, 6, 5, 4, 3, 2].
    Există două variante valide:
      - Standard (pre-2010): (suma % 97) == 0 (sau restul scăzut din 97)
      - Nou (post-2010): se adaugă 55 la sumă înainte de modulo 97.
    """
    digits = clean_vat_number(vat_digits)
    if len(digits) == 12:
        # Codurile de 12 cifre sunt sucursale (primele 9 cifre sunt VAT-ul principal)
        digits = digits[:9]

    if not digits.isdigit() or len(digits) != 9:
        return False

    # Evităm numere banale gen 000000000 sau 123456789
    if len(set(digits)) == 1 or digits == "123456789":
        return False

    weights = [8, 7, 6, 5, 4, 3, 2]
    total = sum(int(digits[i]) * weights[i] for i in range(7))
    check_digits = int(digits[7:9])

    # Formula standard HMRC: scădem 97 repetat până ajungem la un număr <= 0
    rem_standard = total
    while rem_standard > 0:
        rem_standard -= 97
    valid_standard = abs(rem_standard) == check_digits

    # Formula post-2010 (offset +55)
    rem_new = total + 55
    while rem_new > 0:
        rem_new -= 97
    valid_new = abs(rem_new) == check_digits

    return valid_standard or valid_new


def extract_vat_candidates(text: str) -> list[str]:
    """
    Extrage din text posibile coduri de TVA din UK și le filtrează prin checksum.
    Caută fie prefixul explicit GB/XI + 9 cifre, fie 9 cifre lângă cuvântul 'VAT'.
    """
    if not text:
        return []

    candidates = set()

    # 1. Pattern explicit: GB urmat de 9 cifre (cu sau fără spații/cratime)
    gb_matches = re.findall(r"\b(?:GB|XI)\s*\d{3}[\s\-]?\d{4}[\s\-]?\d{2}\b", text, re.IGNORECASE)
    for m in gb_matches:
        c = clean_vat_number(m)
        if is_valid_uk_vat_checksum(c):
            candidates.add(c)

    # 2. Pattern contextual: cuvântul VAT urmat în max 25 caractere de 9 cifre
    vat_matches = re.findall(
        r"\bVAT\b[^0-9]{0,25}(\d{3}[\s\-]?\d{4}[\s\-]?\d{2})\b", text, re.IGNORECASE
    )
    for m in vat_matches:
        c = clean_vat_number(m)
        if is_valid_uk_vat_checksum(c):
            candidates.add(c)

    return sorted(candidates)


def verify_vat_with_hmrc(vat_number: str, expected_company_name: str = "") -> dict:
    """
    Verifică un cod de TVA la API-ul oficial HMRC.
    Returnează un dicționar cu:
      - is_valid (bool): dacă există la HMRC
      - registered_name (str): numele oficial returnat de HMRC
      - registered_address (str): adresa returnată de HMRC
      - name_similarity (float): scor 0-100 între numele așteptat și cel de la HMRC
      - status (str): 'TRUE_POSITIVE', 'FALSE_POSITIVE', 'INVALID_VAT', sau 'API_ERROR'
    """
    digits = clean_vat_number(vat_number)
    if not is_valid_uk_vat_checksum(digits):
        return {
            "is_valid": False,
            "registered_name": "",
            "registered_address": "",
            "name_similarity": 0.0,
            "status": "FAILED_CHECKSUM",
        }

    if not HMRC_ACCESS_TOKEN:
        return {
            "is_valid": None,
            "registered_name": "",
            "registered_address": "",
            "name_similarity": 0.0,
            "status": "NO_API_TOKEN",
        }

    url = f"{HMRC_BASE_URL}/organisations/vat/check-vat-number/lookup/{digits}"
    headers = {
        "Accept": "application/vnd.hmrc.2.0+json",
        "Authorization": f"Bearer {HMRC_ACCESS_TOKEN}",
    }

    try:
        resp = requests.get(url, headers=headers, timeout=10)
        time.sleep(0.35)  # Respectăm rate limit-ul HMRC (~3 req/sec)

        if resp.status_code == 200:
            data = resp.json().get("target", {})
            reg_name = data.get("name", "")
            addr_dict = data.get("address", {})
            reg_addr = ", ".join(filter(None, addr_dict.values()))

            # Curățăm sufixele legale (LTD, LIMITED) pentru o comparație corectă
            norm_expected = _normalize_company_name(expected_company_name)
            norm_reg = _normalize_company_name(reg_name)
            similarity = fuzz.token_sort_ratio(norm_expected, norm_reg) if norm_expected else 0.0

            status = "TRUE_POSITIVE" if similarity >= 75 else "FALSE_POSITIVE"
            return {
                "is_valid": True,
                "registered_name": reg_name,
                "registered_address": reg_addr,
                "name_similarity": round(similarity, 1),
                "status": status,
            }
        elif resp.status_code == 404:
            return {
                "is_valid": False,
                "registered_name": "",
                "registered_address": "",
                "name_similarity": 0.0,
                "status": "INVALID_VAT",
            }
        else:
            return {
                "is_valid": None,
                "registered_name": "",
                "registered_address": "",
                "name_similarity": 0.0,
                "status": f"API_ERROR_{resp.status_code}",
            }
    except requests.RequestException as e:
        return {
            "is_valid": None,
            "registered_name": "",
            "registered_address": "",
            "name_similarity": 0.0,
            "status": f"REQUEST_ERROR: {e}",
        }


def _normalize_company_name(name: str) -> str:
    if not name:
        return ""
    name = name.upper()
    name = re.sub(r"\b(LIMITED|LTD|PLC|LLP|INC|CORP|CO|UK)\b", "", name)
    name = re.sub(r"[^A-Z0-9\s]", "", name)
    return " ".join(name.split())


if __name__ == "__main__":
    # Mic test rapid local pentru checksum și extracție
    sample_text = "Contact us: Acme Widgets Ltd, VAT Reg: GB 123 4567 89 or GB 980 7806 84."
    print("Candidați extrași și validați matematic:", extract_vat_candidates(sample_text))