"""
etl/import_league_assets.py

Phase 8 prep: copies the league's graphics into the app at web size and writes a clean list of them.

Run from the project root, on the computer that has the artwork (your OneDrive):
    python -m etl.import_league_assets "C:\\Users\\jaden\\...\\league_assets.xlsx"

If the artwork has moved since you filled in the sheet, point it at the folder and it finds each file by name:
    python -m etl.import_league_assets league_assets.xlsx --look-in "C:\\Users\\jaden\\...\\Jaden"

For every row of your sheet (xlsx or csv, the template's columns) it:
  1. finds the image (the path in file_name; quotes around it are fine),
  2. trims empty transparent edges off helmets and logos, then shrinks it to web size
     (helmets/logos: longest side 512 px; end-zone banners: 1600 px wide),
  3. saves it as  app/static/league/<TEAM>/<type>_<first season>.png
     (league-wide art: app/static/league/LEAGUE/<asset_id>.png),
  4. cleans the colors: adds the #, puts back leading zeros Excel drops (55450 -> #055450) and reads a
     letter o/O as a zero (37o74e -> #37074E). Anything still not a 6-digit hex color is reported.
Then it writes:
  - app/static/league/assets.csv           the clean list the app reads (commit it with the images)
  - data/cache/league_assets_preview.png    a contact sheet on a dark background, to look over (not committed)

Safe to re-run: it rebuilds app/static/league/ from the sheet each time (old images not in the sheet are removed).
Exit code 1 if anything needs fixing in the sheet; everything else is still imported.
"""

import argparse
import csv
import re
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from etl.db import PROJECT_ROOT, get_connection

OUT_DIR = PROJECT_ROOT / "app" / "static" / "league"
MANIFEST = OUT_DIR / "assets.csv"
PREVIEW = PROJECT_ROOT / "data" / "cache" / "league_assets_preview.png"

ASSET_TYPES = {"helmet", "logo", "alt_logo", "special_logo", "endzone_banner", "wordmark", "league_logo",
               "trophy", "other"}
BANNERS = {"endzone_banner"}
MAX_SIDE = 512          # helmets, logos, special logos...
BANNER_WIDTH = 1600     # end-zone banners
BIG_FILE = 500_000      # bytes; warn above this
TEMPLATE_NOTES = ("team colors go here", "EXAMPLE:")    # filler text from the template, dropped
COLUMNS = ["asset_id", "franchise_code", "asset_type", "season_from", "season_to", "file_name", "background",
           "facing", "primary_color", "secondary_color", "width", "height", "source_file", "notes"]

problems: list[str] = []        # things Jaden needs to fix in the sheet
fixes: list[str] = []           # things this script corrected on its own


# ---------------------------------------------------------------------------------------------- reading
def read_sheet(path: Path) -> list[dict]:
    if path.suffix.lower() in (".xlsx", ".xlsm"):
        import openpyxl
        ws = openpyxl.load_workbook(path, data_only=True).worksheets[0]
        rows = [list(r) for r in ws.iter_rows(values_only=True)]
    else:
        with open(path, newline="", encoding="utf-8-sig") as f:
            rows = list(csv.reader(f))
    header = [str(h).strip().lower() if h is not None else "" for h in rows[0]]
    out = []
    for number, values in enumerate(rows[1:], start=2):
        if not any(v not in (None, "") for v in values):
            continue
        row = {h: v for h, v in zip(header, values) if h}
        row["_row"] = number
        out.append(row)
    return out


def text(value) -> str:
    return "" if value is None else str(value).strip()


def season(value, row, column):
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except ValueError:
        problems.append(f"row {row['_row']}: {column} '{value}' isn't a year")
        return None


def color(value, row, column):
    """Excel-proof hex colors: '#0B3D91' from '0b3d91', 55450 -> '#055450', '37o74e' -> '#37074E'."""
    if value in (None, ""):
        return None
    team = text(row.get("franchise_code")) or "league"
    note = None
    if isinstance(value, (int, float)):                        # Excel turned an all-digit hex into a number
        typed = str(int(value))
        raw = typed.zfill(6)
        if raw != typed:
            note = f"{team} {column}: {typed} -> #{raw} (Excel dropped the leading zeros)"
    else:
        typed = text(value).lstrip("#")
        raw = re.sub("[oO]", "0", typed)
        if raw != typed:
            note = f"{team} {column}: {typed} -> #{raw.upper()} (letter o read as a zero)"
    if not re.fullmatch(r"[0-9a-fA-F]{6}", raw):
        problems.append(f"{team} {column}: '{value}' isn't a 6-digit hex color like #0B3D91")
        return None
    if note:
        fixes.append(note)
    return "#" + raw.upper()


def find_image(row, look_in: Path | None) -> Path | None:
    given = text(row.get("file_name")).strip('"').strip("'")
    if not given:
        problems.append(f"row {row['_row']}: no file_name")
        return None
    path = Path(given)
    if path.exists():
        return path
    if look_in:
        name = Path(given.replace("\\", "/")).name               # works for Windows paths on any computer
        matches = [p for p in look_in.rglob(name) if p.is_file()]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            problems.append(f"row {row['_row']}: {len(matches)} files named '{name}' under {look_in}; "
                            "put the full path in the sheet")
            return None
    problems.append(f"row {row['_row']}: can't find {given}")
    return None


def known_franchises() -> set[str] | None:
    try:
        with get_connection() as conn:
            return {code for (code,) in conn.execute("select franchise_code from core.franchise")}
    except (SystemExit, Exception):                             # no database handy: skip the code check
        print("(Couldn't reach the database, so team codes aren't double-checked.)")
        return None


# ---------------------------------------------------------------------------------------------- images
def web_size(image: Image.Image, asset_type: str) -> Image.Image:
    image = image.convert("RGBA")
    if asset_type not in BANNERS:
        box = image.getchannel("A").point(lambda a: 255 if a > 8 else 0).getbbox()   # trim empty edges
        if box and box != (0, 0, *image.size):
            pad = max(2, round(max(box[2] - box[0], box[3] - box[1]) * 0.02))
            box = (max(0, box[0] - pad), max(0, box[1] - pad),
                   min(image.width, box[2] + pad), min(image.height, box[3] + pad))
            image = image.crop(box)
        scale = MAX_SIDE / max(image.size)
    else:
        scale = BANNER_WIDTH / image.width
    if scale < 1:
        image = image.resize((round(image.width * scale), round(image.height * scale)), Image.LANCZOS)
    return image


def has_transparency(image: Image.Image) -> bool:
    return image.mode == "RGBA" and image.getchannel("A").getextrema()[0] < 255


def out_name(code: str, asset_type: str, first_season, asset_id: str) -> str:
    if code:
        return f"{code}/{asset_type}_{first_season or 'all'}.png"
    slug = re.sub(r"[^a-z0-9_]+", "_", asset_id.lower()).strip("_") or asset_type
    return f"LEAGUE/{slug}.png"


# ---------------------------------------------------------------------------------------------- preview
def contact_sheet(assets: list[dict]) -> None:
    """One row per team on a dark background: helmet, logo, banner, color swatches."""
    try:
        font = ImageFont.load_default(size=22)
    except TypeError:                                           # older Pillow
        font = ImageFont.load_default()
    teams = sorted({a["franchise_code"] or "LEAGUE" for a in assets}, key=lambda c: (c == "LEAGUE", c))
    cell, banner_w, row_h, left = 160, 520, 180, 110
    sheet = Image.new("RGB", (left + 2 * cell + banner_w + 260, 40 + row_h * len(teams)), "#15181d")
    draw = ImageDraw.Draw(sheet)
    draw.text((16, 10), "League graphics on a dark background (helmet, logo, banner, colors)", fill="#c8ccd2", font=font)
    for i, team in enumerate(teams):
        y = 40 + i * row_h
        draw.text((16, y + row_h // 2 - 12), team, fill="#ffffff", font=font)
        x = left
        mine = [a for a in assets if (a["franchise_code"] or "LEAGUE") == team]
        for kinds, width in (({"helmet"}, cell), ({"logo", "league_logo"}, cell), (BANNERS | {"special_logo"}, banner_w)):
            match = next((a for a in mine if a["asset_type"] in kinds), None)
            if match:
                img = Image.open(OUT_DIR / match["file_name"]).convert("RGBA")
                img.thumbnail((width - 16, row_h - 16))
                sheet.paste(img, (x + (width - img.width) // 2, y + (row_h - img.height) // 2), img)
            x += width
        colors = next(((a["primary_color"], a["secondary_color"]) for a in mine
                       if a["primary_color"] or a["secondary_color"]), (None, None))
        for j, hex_color in enumerate(colors):
            if hex_color:
                draw.rounded_rectangle((x + 10, y + 30 + j * 64, x + 70, y + 84 + j * 64), radius=8,
                                       fill=hex_color, outline="#5b616b")
                draw.text((x + 82, y + 44 + j * 64), hex_color, fill="#c8ccd2", font=font)
    PREVIEW.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(PREVIEW)


# ---------------------------------------------------------------------------------------------- main
def main():
    parser = argparse.ArgumentParser(description="Copy the league's graphics into the app at web size.")
    parser.add_argument("sheet", help="your filled-in league_assets sheet (.xlsx or .csv)")
    parser.add_argument("--look-in", help="folder to search (with subfolders) when a file isn't at its path")
    args = parser.parse_args()

    sheet = Path(args.sheet.strip('"'))
    if not sheet.exists():
        sys.exit(f"Can't find the sheet: {sheet}")
    look_in = Path(args.look_in.strip('"')) if args.look_in else None
    codes = known_franchises()

    assets, seen = [], set()
    for row in read_sheet(sheet):
        code = text(row.get("franchise_code")).upper()
        asset_type = text(row.get("asset_type")).lower()
        if asset_type not in ASSET_TYPES:
            problems.append(f"row {row['_row']}: asset_type '{asset_type}' should be one of {', '.join(sorted(ASSET_TYPES))}")
            continue
        if code and codes is not None and code not in codes:
            problems.append(f"row {row['_row']}: '{code}' isn't a franchise code")
            continue
        first, last = season(row.get("season_from"), row, "season_from"), season(row.get("season_to"), row, "season_to")
        given_id = text(row.get("asset_id"))
        asset_id = f"{code.lower()}_{asset_type}_{first or 'all'}" if code else (given_id or asset_type)
        if code and given_id and not given_id.lower().startswith(code.lower()):
            fixes.append(f"row {row['_row']}: asset_id '{given_id}' -> '{asset_id}' (didn't match team {code})")
        file_name = out_name(code, asset_type, first, asset_id)
        if file_name in seen:
            problems.append(f"row {row['_row']}: a second {asset_type} for {code or 'the league'} starting {first}; "
                            "give it a different season_from (or a different asset_id for league art)")
            continue
        seen.add(file_name)
        primary = color(row.get("primary_color"), row, "primary_color")
        secondary = color(row.get("secondary_color"), row, "secondary_color")
        notes = text(row.get("notes"))
        if notes.startswith(TEMPLATE_NOTES):
            notes = ""
        source = find_image(row, look_in)
        if source is None:
            continue
        try:
            image = web_size(Image.open(source), asset_type)
        except Exception as error:                              # not an image, or unreadable
            problems.append(f"row {row['_row']}: couldn't open {source.name} as an image ({error})")
            continue
        target = OUT_DIR / file_name
        target.parent.mkdir(parents=True, exist_ok=True)
        image.save(target, optimize=True)
        background = text(row.get("background")).lower() or ("transparent" if has_transparency(image) else "")
        if background == "transparent" and not has_transparency(image):
            fixes.append(f"{asset_id}: marked transparent, but the image has a solid background")
        assets.append({"asset_id": asset_id, "franchise_code": code, "asset_type": asset_type,
                       "season_from": first, "season_to": last, "file_name": file_name, "background": background,
                       "facing": text(row.get("facing")).lower(), "primary_color": primary,
                       "secondary_color": secondary, "width": image.width, "height": image.height,
                       "source_file": source.name, "notes": notes})
        size = target.stat().st_size
        print(f"  {file_name:34} {image.width:>4}x{image.height:<4} {size / 1000:7.0f} KB   from {source.name}")
        if size > BIG_FILE:
            fixes.append(f"{file_name} is {size / 1e6:.1f} MB; fine, but a smaller export would load faster")

    # keep the folder in step with the sheet: remove images that are no longer listed
    keep = {OUT_DIR / a["file_name"] for a in assets}
    removed = [p for p in OUT_DIR.rglob("*.png") if p not in keep]
    for p in removed:
        p.unlink()
    with open(MANIFEST, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(assets)
    if assets:
        contact_sheet(assets)

    print(f"\n{len(assets)} images in app/static/league/"
          + (f" ({len(removed)} old one(s) removed)" if removed else "") + f"; list: {MANIFEST.relative_to(PROJECT_ROOT)}")
    if assets:
        print(f"Look them over: {PREVIEW.relative_to(PROJECT_ROOT)}")
    if fixes:                                                   # dict.fromkeys = drop repeats, keep order
        print("\nFixed automatically (worth a glance):")
        print("\n".join(f"  - {f}" for f in dict.fromkeys(fixes)))
    if problems:
        print("\nNeeds a fix in your sheet, then run this again:")
        print("\n".join(f"  - {p}" for p in dict.fromkeys(problems)))
        sys.exit(1)


if __name__ == "__main__":
    main()
