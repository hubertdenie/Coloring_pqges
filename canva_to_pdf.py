"""Scrape every page of a public Canva "view" link into a printable PDF.

Usage:
    python canva_to_pdf.py <canva-view-url> [output.pdf] [--paper A4|letter]

How it works:
  * Opens the public viewer in headless Chromium at 3x device scale.
  * Reads the page count from the viewer ("1 / 23"), then steps through
    each page with the arrow key.
  * For each page it locates the page frame (the clipped container around
    the page artwork), hides all Canva UI, waits for images to finish
    loading, and screenshots just that frame.
  * Assembles the PNGs into a PDF, one page per sheet, centred on the
    chosen paper size so it prints cleanly.
"""
import asyncio
import re
import sys
from pathlib import Path

from PIL import Image
from playwright.async_api import async_playwright

PAPER_MM = {"A4": (210, 297), "letter": (215.9, 279.4)}
DPR = 2

# Find the page frame: walk up from the largest visible design image to the
# first ancestor that clips it (overflow hidden) — that's the page boundary.
FIND_FRAME_JS = """() => {
  const vis = [...document.images].filter(i => {
    const r = i.getBoundingClientRect();
    return i.src.includes('media.canva.com') && r.width > 200 &&
           r.right > 0 && r.left < innerWidth && r.bottom > 0 && r.top < innerHeight;
  }).sort((a, b) => b.width * b.height - a.width * a.height);
  if (!vis.length) return null;
  let el = vis[0];
  while (el.parentElement && el.parentElement !== document.body) {
    el = el.parentElement;
    const cs = getComputedStyle(el);
    if (cs.overflow === 'hidden' || cs.overflowX === 'hidden' || cs.clipPath !== 'none') break;
  }
  document.querySelectorAll('[data-scrape-frame]').forEach(e => e.removeAttribute('data-scrape-frame'));
  el.setAttribute('data-scrape-frame', '1');
  return [...el.querySelectorAll('img')].every(i => i.complete && i.naturalWidth > 0);
}"""

ISOLATE_CSS = """
body * { visibility: hidden !important; }
[data-scrape-frame], [data-scrape-frame] * { visibility: visible !important; }
"""


async def scrape(url: str, out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    shots = []
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 1400, "height": 2200},
                                      device_scale_factor=DPR)
        await page.goto(url.split("#")[0], wait_until="networkidle", timeout=120_000)
        await page.wait_for_timeout(3000)

        m = re.search(r"\b1\s*/\s*(\d+)\b", await page.inner_text("body"))
        total = int(m.group(1)) if m else 1
        print(f"Found {total} pages")

        for n in range(1, total + 1):
            for _ in range(40):  # wait up to ~10 s for this page's images
                if await page.evaluate(FIND_FRAME_JS):
                    break
                await page.wait_for_timeout(250)
            style = await page.add_style_tag(content=ISOLATE_CSS)
            await page.wait_for_timeout(300)
            path = out_dir / f"page_{n:03d}.png"
            box = await page.evaluate("""() => {
              const f = document.querySelector('[data-scrape-frame]'); const im = [...f.querySelectorAll('img')].sort((a,b)=>b.width*b.height-a.width*a.height)[0]; const r = (im||f).getBoundingClientRect();
              const x = Math.max(r.left, 0), y = Math.max(r.top, 0);
              return {x, y, width: Math.min(r.right, innerWidth) - x,
                      height: Math.min(r.bottom, innerHeight) - y};
            }""")
            await page.screenshot(path=str(path), clip=box)
            await style.evaluate("s => s.remove()")
            shots.append(path)
            print(f"  captured page {n}/{total}")
            if n < total:
                await page.keyboard.press("ArrowRight")
                await page.wait_for_timeout(1200)
        await browser.close()
    return shots


def build_pdf(images: list[Path], output: Path, paper: str = "A4", margin_mm: float = 5):
    pw_mm, ph_mm = PAPER_MM[paper]
    dpi = 300
    W, H = round(pw_mm / 25.4 * dpi), round(ph_mm / 25.4 * dpi)
    m = round(margin_mm / 25.4 * dpi)
    sheets = []
    for path in images:
        img = Image.open(path).convert("RGB")
        scale = min((W - 2 * m) / img.width, (H - 2 * m) / img.height)
        img = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
        sheet = Image.new("RGB", (W, H), "white")
        sheet.paste(img, ((W - img.width) // 2, (H - img.height) // 2))
        sheets.append(sheet)
    sheets[0].save(output, save_all=True, append_images=sheets[1:], resolution=dpi, quality=92)
    print(f"Wrote {output} ({len(sheets)} pages, {paper})")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    paper = "letter" if "--paper" in sys.argv and "letter" in sys.argv else "A4"
    if not args:
        sys.exit(__doc__)
    url = args[0]
    output = Path(args[1] if len(args) > 1 else "booklet.pdf")
    pages = asyncio.run(scrape(url, Path("canva_pages")))
    build_pdf(pages, output, paper)
