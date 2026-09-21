import json
import urllib.request
from datetime import datetime, date
from zoneinfo import ZoneInfo

EASTERN_TZ = ZoneInfo("America/New_York")

CIK_MAP = {
    "AAPL": "0000320193",
    "MSFT": "0000789019",
    "NVDA": "0001045810",
    "AMZN": "0001018724",
    "META": "0001326801",
    "GOOGL": "0001652044",
    "JPM": "0000019617",
    "LLY": "0000059478",
    "XOM": "0000034088",
    "COST": "0000909832",
    "TSLA": "0001318605",
}

ITEM_DESCRIPTIONS = {
    "1.01": "Entry into a Material Definitive Agreement",
    "1.02": "Termination of a Material Definitive Agreement",
    "1.03": "Bankruptcy or Receivership",
    "2.01": "Completion of Acquisition or Disposition of Assets",
    "2.02": "Results of Operations and Financial Condition",
    "2.03": "Creation of a Direct Financial Obligation",
    "2.04": "Acceleration of a Direct Financial Obligation",
    "2.05": "Costs Associated with Exit or Disposal Activities",
    "2.06": "Material Impairments",
    "3.01": "Notice of Delisting or Failure to Satisfy Continued Listing Rule",
    "4.01": "Changes in Registrant Certifying Accountant",
    "4.02": "Non-Reliance on Previously Issued Financial Statements",
    "5.01": "Changes in Control of Registrant",
    "5.02": "Departure of Directors or Principal Officers",
    "5.03": "Amendments to Articles of Incorporation or Bylaws",
    "7.01": "Regulation FD Disclosure",
    "8.01": "Other Material Events",
    "9.01": "Financial Statements and Exhibits",
}


def download_sec_edgar_8k(symbol: str, start_date: str = "2024-09-01"):
    cik = CIK_MAP.get(symbol)
    if not cik:
        return []
    
    url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "TradingBotResearch/1.0 (admin@tradingbot.local)"}
    )
    
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"[{symbol}] Error fetching SEC filings: {e}")
        return []
        
    recent = data.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    filing_dates = recent.get("filingDate", [])
    items_list = recent.get("items", [])
    acc_nums = recent.get("accessionNumber", [])
    acceptance_times = recent.get("acceptanceDateTime", [])
    
    sec_news = []
    for i in range(len(forms)):
        form = forms[i]
        f_date = filing_dates[i]
        if f_date < start_date:
            continue
            
        if "8-K" in form:
            raw_items = str(items_list[i]).split(",") if items_list[i] else []
            item_descs = [ITEM_DESCRIPTIONS.get(it.strip(), f"Item {it.strip()}") for it in raw_items if it.strip()]
            desc_str = "; ".join(item_descs) if item_descs else "Material Corporate Event"
            
            acc_clean = acc_nums[i].replace("-", "") if i < len(acc_nums) else f"{symbol}_{i}"
            acc_raw = acc_nums[i] if i < len(acc_nums) else acc_clean
            cik_int = int(cik)
            doc_url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_clean}/{acc_raw}-index.htm"
            
            headline = f"{symbol} SEC Form {form}: {desc_str}"
            summary = f"Official SEC regulatory Form {form} filing by {symbol} on {f_date}. Reported items: {', '.join(raw_items)}."
            
            # Pub date
            raw_acc_time = acceptance_times[i] if i < len(acceptance_times) else None
            if raw_acc_time:
                try:
                    pub_dt = datetime.strptime(raw_acc_time[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=EASTERN_TZ)
                except Exception:
                    pub_dt = datetime.strptime(f_date, "%Y-%m-%d").replace(hour=9, minute=0, tzinfo=EASTERN_TZ)
            else:
                pub_dt = datetime.strptime(f_date, "%Y-%m-%d").replace(hour=9, minute=0, tzinfo=EASTERN_TZ)
                
            sec_news.append({
                "id": f"sec_{symbol}_{acc_clean}",
                "symbol": symbol,
                "headline": headline,
                "summary": summary,
                "source": "sec_edgar",
                "url": doc_url,
                "published_at": pub_dt.isoformat(),
            })
            
    return sec_news


if __name__ == "__main__":
    for sym in ["NVDA", "LLY", "JPM", "XOM", "COST"]:
        items = download_sec_edgar_8k(sym)
        print(f"[{sym}] 8-K filings downloaded: {len(items)}")
        if items:
            print(f"  Example: {items[0]['headline']} | Date: {items[0]['published_at']}")
