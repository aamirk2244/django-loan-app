"""Playwright KIBOR helpers adapted for Django execution.

Provides:
- `start_scrape()`, `scrape_status()`, `list_files()`, `download_top_for(year, month)`
"""
import asyncio
import re
import threading
from datetime import datetime, date
from pathlib import Path
import requests
from bs4 import BeautifulSoup
from typing import List

BASE_URL = "https://www.sbp.org.pk/ecodata/kibor_index.asp"
SAVE_DIR = Path("data/kibor_files")
START_YEAR = 2025
START_MONTH = 1
HEADLESS = True
PAGE_WAIT = 3

# Thread-safe scraper state object
_scraper_state = {"running": False, "log": [], "error": None}


def month_name(month_num: int) -> str:
    from datetime import datetime as _dt
    return _dt(2000, month_num, 1).strftime("%b")


def months_to_scrape():
    today = date.today()
    result = []
    year, month = START_YEAR, START_MONTH
    while (year, month) <= (today.year, today.month):
        result.append((year, month))
        month += 1
        if month > 12:
            month, year = 1, year + 1
    return result


def sanitize_filename(name: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', "", name).strip()


def log(msg: str) -> None:
    print(msg)
    _scraper_state["log"].append(msg)


def file_for_month_exists(year: int, month: int):
    SAVE_DIR.mkdir(parents=True, exist_ok=True)
    mon_abbr = month_name(month).lower()
    mon_full = datetime(2000, month, 1).strftime("%B").lower()
    y = str(year)
    pats = [y, f"{year}-{month}", f"{year}-{month:02d}", mon_abbr, mon_full, f"kibor-{year}-{month}"]
    for p in SAVE_DIR.glob("*.pdf"):
        nm = p.name.lower()
        for pat in pats:
            if pat in nm:
                return p.name
    return None


def download_pdf(url: str, dest_path: Path, session: requests.Session) -> bool:
    try:
        resp = session.get(url, timeout=30, stream=True)
        resp.raise_for_status()
        dest_path.write_bytes(resp.content)
        log(f"    ✓ Saved → {dest_path.name}  ({len(resp.content):,} bytes)")
        return True
    except Exception as e:
        log(f"    ✗ Download failed: {e}")
        return False


async def _scrape_async() -> None:
    SAVE_DIR.mkdir(parents=True, exist_ok=True)
    periods = months_to_scrape()
    log(f"Months to process: {len(periods)}  ({periods[0]} → {periods[-1]})")

    session = requests.Session()
    session.headers["User-Agent"] = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )

    try:
        from playwright.async_api import async_playwright
    except Exception as e:
        raise RuntimeError("playwright not installed; install 'playwright' and run 'playwright install'") from e

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=HEADLESS)
        context = await browser.new_context(accept_downloads=True, user_agent=session.headers["User-Agent"]) 
        page = await context.new_page()

        log(f"Opening {BASE_URL} …")
        await page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30_000)
        await asyncio.sleep(PAGE_WAIT)

        for year, month in periods:
            yr_str, mon_str = str(year), month_name(month)
            log(f"\n── {mon_str} {yr_str} ─────────────────────────")

            existing = file_for_month_exists(year, month)
            if existing:
                log(f"  ⏭  Already downloaded ({existing}) — skipping without network.")
                continue

            try:
                await page.locator("select").first.select_option(yr_str)
                await asyncio.sleep(1)
            except Exception as e:
                log(f"  Could not select year {yr_str}: {e}"); continue

            try:
                selects = await page.locator("select").all()
                if len(selects) < 2:
                    log("  Expected 2 dropdowns, found fewer — skipping"); continue
                await selects[1].select_option(mon_str)
                await asyncio.sleep(PAGE_WAIT)
            except Exception as e:
                log(f"  Could not select month {mon_str}: {e}"); continue

            try:
                links = await page.locator("a").filter(has_text=re.compile(r"Daily Kibor Rates", re.I)).all()
                if not links:
                    log("  No links found for this period."); continue

                link_text = (await links[0].inner_text()).strip()
                href      = (await links[0].get_attribute("href")) or ""
                log(f"  Top link : {link_text}")
            except Exception as e:
                log(f"  Failed to retrieve links: {e}"); continue

            if href.startswith("http"):
                pdf_url = href
            elif href.startswith("/"):
                pdf_url = f"https://www.sbp.org.pk{href}"
            else:
                pdf_url = f"{BASE_URL.rsplit('/', 1)[0]}/{href}"

            url_filename = pdf_url.split("/")[-1].split("?")[0]
            if not url_filename.lower().endswith(".pdf"):
                url_filename = sanitize_filename(link_text) + ".pdf"

            dest_path = SAVE_DIR / url_filename
            if dest_path.exists():
                log(f"  ⏭  Already downloaded — skipping.") 
                continue

            for c in await context.cookies():
                session.cookies.set(c["name"], c["value"], domain=c.get("domain", ""))

            download_pdf(pdf_url, dest_path, session)
            await asyncio.sleep(1)

        await browser.close()

    total = len(list(SAVE_DIR.glob("*.pdf")))
    log(f"\nDone! {total} PDFs in {SAVE_DIR.resolve()}")


def _run_in_thread() -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_scrape_async())
        _scraper_state["error"] = None
    except Exception as e:
        _scraper_state["error"] = str(e)
        log(f"Scraper error: {e}")
    finally:
        try:
            def _summary_is_current(csv_path: Path = Path("static/data/kibor_summary.csv")) -> bool:
                try:
                    import pandas as _pd
                    csv_p = Path(csv_path)
                    if not csv_p.exists():
                        return False
                    df = _pd.read_csv(csv_p)
                    files_in_dir = {p.name for p in SAVE_DIR.glob("*.pdf")}
                    files_in_csv = set(df['filename'].dropna().astype(str).unique()) if 'filename' in df.columns else set()
                    return files_in_dir.issubset(files_in_csv)
                except Exception:
                    return False

            loop.close()

            try:
                if not _summary_is_current():
                    log("Running post-download extraction of KIBOR rows…")
                    res = build_kibor_summary()
                    if res.get('ok'):
                        log(f"Extraction complete: {res.get('count')} rows saved → {res.get('csv')}")
                    else:
                        log(f"Extraction skipped/failed: {res.get('error')}")
                else:
                    log("Extraction already up-to-date; skipping post-download extraction.")
            except Exception as e:
                log(f"Post-download extraction error: {e}")

        finally:
            _scraper_state["running"] = False


def start_scrape() -> dict:
    if _scraper_state["running"]:
        return {"status": "already_running"}
    _scraper_state["running"] = True
    _scraper_state["log"] = []
    _scraper_state["error"] = None
    thread = threading.Thread(target=_run_in_thread, daemon=True)
    thread.start()
    return {"status": "started"}


def scrape_status() -> dict:
    return {"running": _scraper_state["running"], "error": _scraper_state["error"], "log": list(_scraper_state["log"]) }


def list_files() -> List[str]:
    return sorted(p.name for p in SAVE_DIR.glob("*.pdf")) if SAVE_DIR.exists() else []


def _normalize_tenor_key(tenor: str) -> str:
    if not tenor:
        return 'Other'
    t = tenor.strip().lower()
    m = re.search(r"(\d+)\W*(y|yr|year|years)", t)
    if m:
        n = int(m.group(1)) * 12
        return f"{n}M"
    m = re.search(r"(\d+)\W*(m|mo|mon|month|months)", t)
    if m:
        n = int(m.group(1))
        return f"{n}M"
    m = re.search(r"^(\d+)\W*[mM]$", t)
    if m:
        return f"{int(m.group(1))}M"
    m = re.search(r"(\d+)", t)
    if m:
        return f"{int(m.group(1))}M"
    return 'Other'


async def _download_top_for_async(year: int, month: int) -> dict:
    SAVE_DIR.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers["User-Agent"] = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )

    existing = file_for_month_exists(year, month)
    if existing:
        return {"ok": True, "skipped": True, "saved": existing, "pdf_url": None}

    try:
        from playwright.async_api import async_playwright
    except Exception as e:
        return {"ok": False, "error": "playwright not installed"}

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=HEADLESS)
        context = await browser.new_context(accept_downloads=True, user_agent=session.headers["User-Agent"]) 
        page = await context.new_page()

        await page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30_000)
        await asyncio.sleep(PAGE_WAIT)

        yr_str, mon_str = str(year), month_name(month)
        try:
            await page.locator("select").first.select_option(yr_str)
            await asyncio.sleep(1)
        except Exception as e:
            return {"ok": False, "error": f"Could not select year {year}: {e}"}

        try:
            selects = await page.locator("select").all()
            if len(selects) < 2:
                return {"ok": False, "error": "Expected 2 dropdowns, found fewer"}
            await selects[1].select_option(mon_str)
            await asyncio.sleep(PAGE_WAIT)
        except Exception as e:
            return {"ok": False, "error": f"Could not select month {mon_str}: {e}"}

        try:
            links = await page.locator("a").filter(has_text=re.compile(r"Daily Kibor Rates", re.I)).all()
            if not links:
                return {"ok": False, "error": "No links found for this period"}
            link_text = (await links[0].inner_text()).strip()
            href = (await links[0].get_attribute("href")) or ""
        except Exception as e:
            return {"ok": False, "error": f"Failed to retrieve links: {e}"}

        if href.startswith("http"):
            pdf_url = href
        elif href.startswith("/"):
            pdf_url = f"https://www.sbp.org.pk{href}"
        else:
            pdf_url = f"{BASE_URL.rsplit('/', 1)[0]}/{href}"

        url_filename = pdf_url.split("/")[-1].split("?")[0]
        if not url_filename.lower().endswith(".pdf"):
            url_filename = sanitize_filename(link_text) + ".pdf"

        dest_path = SAVE_DIR / url_filename
        if dest_path.exists():
            await browser.close()
            return {"ok": True, "skipped": True, "saved": dest_path.name, "pdf_url": pdf_url}

        for c in await context.cookies():
            session.cookies.set(c["name"], c["value"], domain=c.get("domain", ""))

        success = download_pdf(pdf_url, dest_path, session)
        await browser.close()
        if success:
            return {"ok": True, "skipped": False, "saved": dest_path.name, "pdf_url": pdf_url}
        else:
            return {"ok": False, "error": "download_failed", "pdf_url": pdf_url}


def download_top_for(year: int, month: int) -> dict:
    try:
        res = _download_top_for_requests(year, month)
        if res.get('ok'):
            return res
    except Exception:
        pass

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        res = loop.run_until_complete(_download_top_for_async(year, month))
        return res
    except Exception as e:
        return {"ok": False, "error": str(e)}
    finally:
        loop.close()


def _download_top_for_requests(year: int, month: int) -> dict:
    SAVE_DIR.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers["User-Agent"] = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )

    existing = file_for_month_exists(year, month)
    if existing:
        return {"ok": True, "skipped": True, "saved": existing, "pdf_url": None}

    try:
        resp = session.get(BASE_URL, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        return {"ok": False, "error": f"network_error: {e}"}

    soup = BeautifulSoup(resp.text, "html.parser")
    anchors = soup.find_all('a')

    candidates = []
    text_re = re.compile(r"Daily\s+Kibor Rates", re.I)
    mon_name = month_name(month)
    yr_str = str(year)

    for a in anchors:
        href = a.get('href') or ''
        text = (a.get_text() or '').strip()
        score = 0
        if text_re.search(text):
            score += 10
        if href.lower().endswith('.pdf'):
            score += 5
        if mon_name.lower() in text.lower() or yr_str in text:
            score += 2
        if href and score > 0:
            candidates.append((score, text, href))

    if not candidates:
        for a in anchors:
            href = a.get('href') or ''
            if href.lower().endswith('.pdf'):
                candidates.append((1, a.get_text() or '', href))
                break

    if not candidates:
        return {"ok": False, "error": "no_link_found"}

    candidates.sort(key=lambda x: -x[0])
    _, link_text, href = candidates[0]

    if href.startswith('http'):
        pdf_url = href
    elif href.startswith('/'):
        pdf_url = f"https://www.sbp.org.pk{href}"
    else:
        pdf_url = f"{BASE_URL.rsplit('/', 1)[0]}/{href}"

    url_filename = pdf_url.split("/")[-1].split("?")[0]
    if not url_filename.lower().endswith('.pdf'):
        url_filename = sanitize_filename(link_text or f"kibor-{year}-{month}") + '.pdf'

    dest_path = SAVE_DIR / url_filename
    if dest_path.exists():
        return {"ok": True, "skipped": True, "saved": dest_path.name, "pdf_url": pdf_url}

    success = download_pdf(pdf_url, dest_path, session)
    if success:
        return {"ok": True, "skipped": False, "saved": dest_path.name, "pdf_url": pdf_url}
    else:
        return {"ok": False, "error": "download_failed", "pdf_url": pdf_url}


def extract_kibor_from_pdf(pdf_path: Path) -> dict:
    try:
        import pdfplumber
    except Exception as e:
        log(f"pdfplumber not available: {e}")
        return {'6Month': '', '1Year': ''}

    found = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                tables = page.extract_tables() or []
                for table in tables:
                    if not table or len(table) < 2:
                        continue
                    header = [(c or '').strip().lower() for c in table[0]]
                    try:
                        tenor_idx = next(i for i, h in enumerate(header) if 'tenor' in h)
                        offer_idx = next(i for i, h in enumerate(header) if 'offer' in h)
                    except StopIteration:
                        continue

                    for r in table[1:]:
                        if not any((cell or '').strip() for cell in (r or [])):
                            continue
                        t = (r[tenor_idx] or '') if tenor_idx < len(r) else ''
                        o = (r[offer_idx] or '') if offer_idx < len(r) else ''
                        found.append({'Tenor': str(t).strip(), 'Offer': str(o).strip()})
    except Exception as e:
        log(f"Failed to extract from {pdf_path.name}: {e}")

    out = {'6Month': '', '1Year': ''}
    for item in found:
        tenor_raw = item.get('Tenor', '')
        offer_raw = item.get('Offer', '')
        key = _normalize_tenor_key(tenor_raw)
        if key == '6M' and not out['6Month']:
            out['6Month'] = offer_raw
        elif key == '12M' and not out['1Year']:
            out['1Year'] = offer_raw

    def _is_number(s: str) -> bool:
        if s is None:
            return False
        s = str(s).strip().replace(',', '')
        try:
            float(s)
            return True
        except Exception:
            return False

    if not out['6Month'] or not out['1Year']:
        try:
            with pdfplumber.open(pdf_path) as pdf:
                for page in pdf.pages:
                    tables = page.extract_tables() or []
                    for table in tables:
                        for r in table[1:]:
                            if not r:
                                continue
                            if len(r) == 1 and isinstance(r[0], str) and re.search(r"\d", r[0]):
                                parts = re.split(r"\s{2,}|\t|,", r[0])
                                if len(parts) > 1:
                                    r = [p.strip() for p in parts]
                            for ci, cell in enumerate(r):
                                txt = (cell or '').strip()
                                if not txt:
                                    continue
                                k = _normalize_tenor_key(txt)
                                if k in ('6M', '12M'):
                                    candidates = []
                                    if ci < len(r):
                                        candidates.append(r[ci])
                                    if ci+1 < len(r):
                                        candidates.append(r[ci+1])
                                    if ci+2 < len(r):
                                        candidates.append(r[ci+2])
                                    if ci-1 >= 0:
                                        candidates.append(r[ci-1])

                                    for cval in candidates:
                                        if _is_number(cval):
                                            val = str(cval).strip()
                                            if k == '6M' and not out['6Month']:
                                                out['6Month'] = val
                                            elif k == '12M' and not out['1Year']:
                                                out['1Year'] = val
                                            break
                            if out['6Month'] and out['1Year']:
                                break
                        if out['6Month'] and out['1Year']:
                            break
                    if out['6Month'] and out['1Year']:
                        break
        except Exception:
            pass

    if not out['6Month'] or not out['1Year']:
        try:
            with pdfplumber.open(pdf_path) as pdf:
                for page in pdf.pages:
                    text = page.extract_text() or ''
                    for line in (text.splitlines() if text else []):
                        tenor_m = re.search(r"(\d+)\W*(week|weeks|month|months|year|years|yr|y)", line, re.I)
                        if not tenor_m:
                            continue
                        tenor_txt = tenor_m.group(0)
                        nums = re.findall(r"\d+\.\d+", line)
                        if not nums:
                            continue
                        offer_val = nums[-1]
                        k = _normalize_tenor_key(tenor_txt)
                        if k == '6M' and not out['6Month']:
                            out['6Month'] = offer_val
                        elif k == '12M' and not out['1Year']:
                            out['1Year'] = offer_val
                        if out['6Month'] and out['1Year']:
                            break
                    if out['6Month'] and out['1Year']:
                        break
        except Exception:
            pass

    return out


def build_kibor_summary(csv_path: str = None, excel_path: str = None) -> dict:
    try:
        import pandas as pd
    except Exception as e:
        return {"ok": False, "error": f"pandas not available: {e}"}

    SAVE_DIR.mkdir(parents=True, exist_ok=True)
    rows_out = []

    for p in sorted(SAVE_DIR.glob("*.pdf")):
        vals = extract_kibor_from_pdf(p)
        print(f"Extracted values +++ {p.name}: {vals}")
        if not vals or (not vals.get('6Month') and not vals.get('1Year')):
            log(f"  ⚠ No Tenor/Offer table found in {p.name}")
            rows_out.append({'filename': p.name, '6Month': '', '1Year': ''})
            continue

        rows_out.append({'filename': p.name, '6Month': vals.get('6Month',''), '1Year': vals.get('1Year','')})

    if not rows_out:
        return {"ok": False, "error": "no_data_found"}

    df = pd.DataFrame(rows_out)
    cols = ['filename', '6Month', '1Year']
    df = df.reindex(columns=cols)

    csv_out = Path(csv_path) if csv_path else Path("data/kibor_summary/kibor_summary.csv")
    xlsx_out = Path(excel_path) if excel_path else Path("data/kibor_summary/kibor_summary.xlsx")
    csv_out.parent.mkdir(parents=True, exist_ok=True)

    try:
        df.to_csv(csv_out, index=False)
    except Exception as e:
        log(f"Failed to write CSV {csv_out}: {e}")
        return {"ok": False, "error": f"csv_write_failed: {e}"}

    try:
        df.to_excel(xlsx_out, index=False, engine="openpyxl")
    except Exception:
        xlsx_out = None

    return {"ok": True, "count": len(df), "csv": str(csv_out), "excel": str(xlsx_out) if xlsx_out else None}