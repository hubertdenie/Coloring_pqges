import os
import sys
import time
from urllib.parse import urljoin, urlsplit

import requests
from bs4 import BeautifulSoup
from pypdf import PdfWriter
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

URL = sys.argv[1] if len(sys.argv) > 1 else "https://example.com/coloring-pages"
DOWNLOAD_DIR = "downloads"
OUTPUT_FILE = "merged.pdf"
headers = {"User-Agent": "Mozilla/5.0"}

os.makedirs(DOWNLOAD_DIR, exist_ok=True)

session = requests.Session()
session.headers.update(headers)
session.mount("https://", HTTPAdapter(max_retries=Retry(
    total=3, backoff_factor=1, status_forcelist=(429, 500, 502, 503, 504))))

resp = session.get(URL, timeout=30)
resp.raise_for_status()
soup = BeautifulSoup(resp.text, "html.parser")

# dict.fromkeys dedups while preserving DOM order
pdf_links = list(dict.fromkeys(
    urljoin(URL, a["href"]).split("#")[0]
    for a in soup.find_all("a", href=True)
    if urlsplit(a["href"]).path.lower().endswith(".pdf")
))

downloaded_files = []
for idx, link in enumerate(pdf_links, 1):
    path = os.path.join(DOWNLOAD_DIR, f"page_{idx:03d}.pdf")
    try:
        with session.get(link, timeout=30, stream=True) as r:
            r.raise_for_status()
            chunks = r.iter_content(65536)
            first = next(chunks, b"")
            if not first.lstrip().startswith(b"%PDF-"):
                raise ValueError("not a PDF")
            with open(path, "wb") as f:
                f.write(first)
                for chunk in chunks:
                    f.write(chunk)
        downloaded_files.append(path)
    except Exception as e:
        print(f"Skipped {link}: {e}")
    time.sleep(0.7)

if not downloaded_files:
    raise SystemExit("No PDFs downloaded; nothing to merge.")

with PdfWriter() as merger:
    for pdf in downloaded_files:
        try:
            merger.append(pdf)
        except Exception as e:
            print(f"Merge skipped {pdf}: {e}")
    merger.write(OUTPUT_FILE)

print(f"Merged {len(downloaded_files)}/{len(pdf_links)} PDFs into {OUTPUT_FILE}")
