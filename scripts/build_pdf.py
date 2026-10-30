"""Render quickstart_final.html to Recruiter_QuickStart_final.pdf with Cera Pro.

Put your Cera Pro Light and Bold files in ./fonts/ (any name containing "cera",
and "light" or "bold"; .otf .ttf .woff .woff2 all work), then run:
    python build_pdf.py
"""
from pathlib import Path
from playwright.sync_api import sync_playwright

here = Path(__file__).resolve().parent
SRC = here / "quickstart_final.html"
OUT = here / "Recruiter_QuickStart_final.pdf"
FMT = {".otf": "opentype", ".ttf": "truetype", ".woff": "woff", ".woff2": "woff2"}

# Look in fonts/ next to this script, and in ./fonts from where you run it.
folders = [here / "fonts", Path.cwd() / "fonts"]
print("Looking in:", [str(f) for f in folders])

found = {}
for folder in folders:
    if not folder.is_dir():
        continue
    for f in sorted(folder.iterdir()):
        name = f.name.lower()
        if f.suffix.lower() not in FMT or "cera" not in name or "italic" in name:
            continue
        if "light" in name:
            found.setdefault(300, f)
        elif "bold" in name and "extra" not in name and "black" not in name:
            found.setdefault(700, f)

if found:
    print("Cera Pro files used:", {w: f.name for w, f in found.items()})
else:
    all_files = [f.name for d in folders if d.is_dir() for f in d.iterdir()]
    print("Cera Pro files NOT found. Files seen in fonts/:", all_files or "folder missing or empty")

css = "".join(
    f'@font-face{{font-family:"Cera Pro";font-weight:{w};'
    f'src:url("{f.as_uri()}") format("{FMT[f.suffix.lower()]}")}}\n'
    for w, f in found.items()
)

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page()
    pg.goto(SRC.as_uri())
    if css:
        pg.add_style_tag(content=css)
    pg.evaluate("document.fonts.ready")
    pg.wait_for_timeout(500)
    ok = pg.evaluate("""() => [300, 700].map(w =>
        document.fonts.check(w + ' 12px "Cera Pro"'))""")
    print("Cera Pro loaded in browser (Light, Bold):", ok)
    pg.pdf(path=str(OUT), format="A4", print_background=True, prefer_css_page_size=True)
    b.close()
print("Wrote", OUT)