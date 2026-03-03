import csv
import json
import os
import argparse

from ontology.store import OntologyStore


def ingest_sec_company_tickers(path: str, store: OntologyStore) -> int:
    """
    SEC company_tickers.json 형식을 기대.
    예: {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}, ...}
    """
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    count = 0
    for _, row in data.items():
        ticker = str(row.get("ticker", "")).strip()
        title = str(row.get("title", "")).strip()
        cik = str(row.get("cik_str", "")).strip()
        if not ticker or not title:
            continue
        entity_id = f"sec:{ticker}"
        store.upsert_entity(
            {
                "entity_id": entity_id,
                "canonical_name": title,
                "entity_type": "company",
                "ticker": ticker,
                "exchange": "US",
                "cik": cik,
                "source": "SEC_company_tickers",
            }
        )
        store.add_alias(entity_id, ticker, source="SEC_company_tickers", confidence=1.0)
        store.add_alias(entity_id, title, source="SEC_company_tickers", confidence=0.95)
        count += 1

    store.log_ingestion("SEC_company_tickers", path, count)
    return count


def ingest_dart_krx_csv(path: str, store: OntologyStore) -> int:
    """
    한국용 CSV 예시 컬럼:
    corp_name,stock_code,market,corp_code
    """
    count = 0
    with open(path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = (row.get("corp_name") or "").strip()
            stock_code = (row.get("stock_code") or "").strip()
            market = (row.get("market") or "").strip().upper()
            corp_code = (row.get("corp_code") or "").strip()

            if not name or not stock_code:
                continue

            suffix = ".KS" if market == "KOSPI" else ".KQ"
            ticker = f"{stock_code}{suffix}"
            entity_id = f"kr:{stock_code}"
            store.upsert_entity(
                {
                    "entity_id": entity_id,
                    "canonical_name": name,
                    "entity_type": "company",
                    "ticker": ticker,
                    "exchange": market or "KR",
                    "cik": corp_code or None,
                    "country": "KR",
                    "source": "DART_KRX_csv",
                }
            )
            store.add_alias(entity_id, name, source="DART_KRX_csv", confidence=1.0)
            store.add_alias(entity_id, stock_code, source="DART_KRX_csv", confidence=0.95)
            store.add_alias(entity_id, ticker, source="DART_KRX_csv", confidence=1.0)
            count += 1

    store.log_ingestion("DART_KRX_csv", path, count)
    return count


def ingest_lei_csv(path: str, store: OntologyStore) -> int:
    """
    LEI CSV 최소 컬럼 예시:
    LEI,LegalName,LegalAddressCountry
    """
    count = 0
    with open(path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            lei = (row.get("LEI") or "").strip()
            legal_name = (row.get("LegalName") or "").strip()
            country = (row.get("LegalAddressCountry") or "").strip()
            if not lei or not legal_name:
                continue
            entity_id = f"lei:{lei}"
            store.upsert_entity(
                {
                    "entity_id": entity_id,
                    "canonical_name": legal_name,
                    "entity_type": "legal_entity",
                    "lei": lei,
                    "country": country or None,
                    "source": "GLEIF_LEI_csv",
                }
            )
            store.add_alias(entity_id, legal_name, source="GLEIF_LEI_csv", confidence=1.0)
            count += 1

    store.log_ingestion("GLEIF_LEI_csv", path, count)
    return count


def ingest_figi_csv(path: str, store: OntologyStore) -> int:
    """
    FIGI CSV 최소 컬럼 예시:
    FIGI,TICKER,NAME,EXCH_CODE
    """
    count = 0
    with open(path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            figi = (row.get("FIGI") or "").strip()
            ticker = (row.get("TICKER") or "").strip()
            name = (row.get("NAME") or "").strip()
            exch = (row.get("EXCH_CODE") or "").strip()

            if not figi or not name:
                continue

            entity_id = f"figi:{figi}"
            store.upsert_entity(
                {
                    "entity_id": entity_id,
                    "canonical_name": name,
                    "entity_type": "security",
                    "figi": figi,
                    "ticker": ticker or None,
                    "exchange": exch or None,
                    "source": "FIGI_csv",
                }
            )
            store.add_alias(entity_id, name, source="FIGI_csv", confidence=1.0)
            if ticker:
                store.add_alias(entity_id, ticker, source="FIGI_csv", confidence=0.9)
            count += 1

    store.log_ingestion("FIGI_csv", path, count)
    return count


def main():
    parser = argparse.ArgumentParser(description="Ontology bootstrap loader")
    parser.add_argument("--sec-json", help="SEC company_tickers.json path")
    parser.add_argument("--dart-krx-csv", help="DART/KRX merged csv path")
    parser.add_argument("--lei-csv", help="GLEIF LEI csv path")
    parser.add_argument("--figi-csv", help="FIGI csv path")
    args = parser.parse_args()

    store = OntologyStore()
    total = 0

    if args.sec_json and os.path.exists(args.sec_json):
        c = ingest_sec_company_tickers(args.sec_json, store)
        total += c
        print(f"[ingest] SEC company_tickers: {c}")

    if args.dart_krx_csv and os.path.exists(args.dart_krx_csv):
        c = ingest_dart_krx_csv(args.dart_krx_csv, store)
        total += c
        print(f"[ingest] DART/KRX csv: {c}")

    if args.lei_csv and os.path.exists(args.lei_csv):
        c = ingest_lei_csv(args.lei_csv, store)
        total += c
        print(f"[ingest] LEI csv: {c}")

    if args.figi_csv and os.path.exists(args.figi_csv):
        c = ingest_figi_csv(args.figi_csv, store)
        total += c
        print(f"[ingest] FIGI csv: {c}")

    print(f"[done] total ingested records: {total}")


if __name__ == "__main__":
    main()
