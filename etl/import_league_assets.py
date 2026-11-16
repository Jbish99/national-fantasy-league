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
     (league-wide art: app/static/league/LEAGUE/<asset_id>.png). End-zone banners are saved as .webp
     (transparency kept): same look at about a sixth of the size (PNG is heavy for detailed artwork),
  4. cleans the colors: adds the #, puts back leading zeros Excel drops (55450 -> #055450) and reads a
     letter o/O as a zero (37o74e -> #37074E). Anything still not a 6-digit hex color is reported.
  5. makes a left-facing copy of every helmet (helmet_left_<season>.png) so matchup cards can face two helmets
     at each other. It's the helmet mirrored, with the side decal flipped back so lettering still reads right
     (decal positions: data/manual/helmet_decals.csv). A helmet row of your own with facing = left wins over this.
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

from collections import deque

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from etl.db import PROJECT_ROOT, get_connection

OUT_DIR = PROJECT_ROOT / "app" / "static" / "league"
MANIFEST = OUT_DIR / "assets.csv"
PREVIEW = PROJECT_ROOT / "data" / "cache" / "league_assets_preview.png"
DECALS = PROJECT_ROOT / "data" / "manual" / "helmet_decals.csv"

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


def out_name(code: str, asset_type: str, first_season, asset_id: str, facing: str = "") -> str:
    if code:
        kind = "helmet_left" if asset_type == "helmet" and facing == "left" else asset_type
        return f"{code}/{kind}_{first_season or 'all'}.png"
    slug = re.sub(r"[^a-z0-9_]+", "_", asset_id.lower()).strip("_") or asset_type
    return f"LEAGUE/{slug}.png"



# ---------------------------------------------------------------------------------------------- left-facing helmets
def load_decals() -> dict:
    """(franchise_code, season_from) -> (seed, shell point), as fractions of the web-size helmet's width/height.
    seed = a point on the side decal; shell = a point on plain helmet shell next to it."""
    if not DECALS.exists():
        return {}
    out = {}
    with open(DECALS, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            try:
                out[(r["franchise_code"].strip().upper(), int(r["season_from"]))] = (
                    (float(r["seed_x"]), float(r["seed_y"])), (float(r["shell_x"]), float(r["shell_y"])))
            except (KeyError, ValueError):
                problems.append(f"{DECALS.name}: can't read the row {dict(r)}")
    return out


def _far(a, b, tol=36) -> bool:
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]), abs(a[2] - b[2])) > tol


def decal_mask(image: Image.Image, seed, shell_point):
    """Mask of the decal around `seed`: every connected pixel that isn't the shell's color, holes filled,
    grown 2 px to take its soft edge along. Returns (mask, shell color, (left x, right x)) or None."""
    px = image.load()
    W, H = image.size
    shell = px[shell_point][:3]
    off = lambda x, y: px[x, y][3] > 128 and _far(px[x, y], shell)
    near = [(x, y) for x in range(seed[0] - 15, seed[0] + 16) for y in range(seed[1] - 15, seed[1] + 16)
            if 0 <= x < W and 0 <= y < H and off(x, y)]
    if not near:
        return None
    start = min(near, key=lambda p: (p[0] - seed[0]) ** 2 + (p[1] - seed[1]) ** 2)
    decal, queue = {start}, deque([start])
    while queue:
        x, y = queue.popleft()
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)):
            n = (x + dx, y + dy)
            if 0 <= n[0] < W and 0 <= n[1] < H and n not in decal and off(*n):
                decal.add(n)
                queue.append(n)
    x0, y0 = min(p[0] for p in decal) - 1, min(p[1] for p in decal) - 1
    x1, y1 = max(p[0] for p in decal) + 1, max(p[1] for p in decal) + 1
    if (x1 - x0) * (y1 - y0) > 0.3 * W * H:              # ran into the whole helmet: the points are off
        return None
    outside = set()                                      # box pixels reachable from its edge = not part of the decal
    queue = deque([(x, y) for x in range(x0, x1 + 1) for y in (y0, y1)] + [(x, y) for y in range(y0, y1 + 1) for x in (x0, x1)])
    while queue:
        p = queue.popleft()
        if p in outside or p in decal or not (x0 <= p[0] <= x1 and y0 <= p[1] <= y1):
            continue
        outside.add(p)
        queue.extend(((p[0] + 1, p[1]), (p[0] - 1, p[1]), (p[0], p[1] + 1), (p[0], p[1] - 1)))
    mask = Image.new("L", (W, H), 0)
    mp = mask.load()
    for x in range(x0, x1 + 1):
        for y in range(y0, y1 + 1):
            if (x, y) not in outside:
                mp[x, y] = 255
    return mask.filter(ImageFilter.MaxFilter(5)), shell, (x0, x1)


def left_facing(image: Image.Image, decal=None) -> tuple[Image.Image, bool]:
    """The helmet mirrored. With decal points, the decal is painted over and put back unmirrored, so letters
    read the right way. Returns (image, decal fixed?)."""
    image = image.convert("RGBA")
    out = image.transpose(Image.FLIP_LEFT_RIGHT)
    if not decal:
        return out, False
    W, H = image.size
    (sx, sy), (hx, hy) = decal
    found = decal_mask(image, (round(sx * W), round(sy * H)), (round(hx * W), round(hy * H)))
    if found is None:
        return out, False
    mask, shell, (x0, x1) = found
    out.paste(Image.new("RGBA", (W, H), shell + (255,)), (0, 0), mask.transpose(Image.FLIP_LEFT_RIGHT))
    decal_only = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    decal_only.paste(image, (0, 0), mask)
    moved = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    moved.paste(decal_only, ((W - 1 - x1) - x0, 0))       # same spot the mirrored decal had
    out.alpha_composite(moved)
    return out, True


# ---------------------------------------------------------------------------------------------- preview
def contact_sheet(assets: list[dict]) -> None:
    """One row per team on a dark background: helmet, left-facing helmet, logo, banner, color swatches."""
    try:
        font = ImageFont.load_default(size=22)
    except TypeError:                                           # older Pillow
        font = ImageFont.load_default()
    teams = sorted({a["franchise_code"] or "LEAGUE" for a in assets}, key=lambda c: (c == "LEAGUE", c))
    cell, banner_w, row_h, left = 160, 520, 180, 110
    sheet = Image.new("RGB", (left + 3 * cell + banner_w + 260, 40 + row_h * len(teams)), "#15181d")
    draw = ImageDraw.Draw(sheet)
    draw.text((16, 10), "League graphics on a dark background (helmet, left-facing helmet, logo, banner, colors)",
              fill="#c8ccd2", font=font)
    columns = ((lambda a: a["asset_type"] == "helmet" and a["facing"] != "left", cell),
               (lambda a: a["asset_type"] == "helmet" and a["facing"] == "left", cell),
               (lambda a: a["asset_type"] in ("logo", "league_logo"), cell),
               (lambda a: a["asset_type"] in BANNERS | {"special_logo"}, banner_w))
    for i, team in enumerate(teams):
        y = 40 + i * row_h
        draw.text((16, y + row_h // 2 - 12), team, fill="#ffffff", font=font)
        x = left
        mine = [a for a in assets if (a["franchise_code"] or "LEAGUE") == team]
        for wanted, width in columns:
            match = next((a for a in mine if wanted(a)), None)
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

    assets, seen, big = [], set(), []
    helmets = {}                                                # (team, season) -> (image, its row): for left-facing copies
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
        facing = text(row.get("facing")).lower()
        if asset_type == "helmet" and facing != "left":
            facing = "right"
        kind = "helmet_left" if asset_type == "helmet" and facing == "left" else asset_type
        asset_id = f"{code.lower()}_{kind}_{first or 'all'}" if code else (given_id or asset_type)
        if code and given_id and not given_id.lower().startswith(code.lower()):
            fixes.append(f"row {row['_row']}: asset_id '{given_id}' -> '{asset_id}' (didn't match team {code})")
        file_name = out_name(code, asset_type, first, asset_id, facing)
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
        transparent = has_transparency(image)
        if asset_type in BANNERS:                               # WebP: about a sixth of PNG's size, transparency kept
            file_name = file_name[:-4] + ".webp"
        target = OUT_DIR / file_name
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.suffix == ".webp":
            (image if transparent else image.convert("RGB")).save(target, "WEBP", quality=88, method=6)
        else:
            image.save(target, optimize=True)
        background = text(row.get("background")).lower() or ("transparent" if transparent else "")
        if background == "transparent" and not transparent:
            fixes.append(f"{asset_id}: marked transparent, but the image has a solid background")
        assets.append({"asset_id": asset_id, "franchise_code": code, "asset_type": asset_type,
                       "season_from": first, "season_to": last, "file_name": file_name, "background": background,
                       "facing": facing, "primary_color": primary,
                       "secondary_color": secondary, "width": image.width, "height": image.height,
                       "source_file": source.name, "notes": notes})
        if asset_type == "helmet" and facing == "right" and code:
            helmets[(code, first)] = (image, assets[-1])
        size = target.stat().st_size
        print(f"  {file_name:34} {image.width:>4}x{image.height:<4} {size / 1000:7.0f} KB   from {source.name}")
        if size > BIG_FILE:
            big.append(f"{file_name} ({size / 1e6:.1f} MB)")

    # left-facing helmets (matchup cards face the helmets at each other), unless the sheet has its own
    decals = load_decals()
    for (code, first), (image, right) in helmets.items():
        file_name = f"{code}/helmet_left_{first or 'all'}.png"
        if file_name in seen:
            continue
        left, fixed = left_facing(image, decals.get((code, first)))
        if (code, first) in decals and not fixed:
            problems.append(f"{code} helmet {first}: no decal at the points in data/manual/{DECALS.name} (new art?). "
                            "Its left-facing copy is a plain mirror, so lettering reads backwards; update the points")
        left.save(OUT_DIR / file_name, optimize=True)
        assets.append({**right, "asset_id": f"{code.lower()}_helmet_left_{first or 'all'}", "file_name": file_name,
                       "facing": "left", "notes": "mirrored copy" + (", decal flipped back to read right" if fixed else "")})
        print(f"  {file_name:34} {left.width:>4}x{left.height:<4} {(OUT_DIR / file_name).stat().st_size / 1000:7.0f} KB   "
              f"mirrored{' (decal kept readable)' if fixed else ''}")

    # keep the folder in step with the sheet: remove images that are no longer listed
    keep = {OUT_DIR / a["file_name"] for a in assets}
    removed = [p for p in OUT_DIR.rglob("*") if p.suffix in (".png", ".webp") and p not in keep]
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
    if big:
        fixes.append(f"{len(big)} file(s) over {BIG_FILE / 1e6:.1f} MB, fine but slower to load: " + ", ".join(big))
    if fixes:                                                   # dict.fromkeys = drop repeats, keep order
        print("\nFixed automatically (worth a glance):")
        print("\n".join(f"  - {f}" for f in dict.fromkeys(fixes)))
    if problems:
        print("\nNeeds a fix (usually in your sheet), then run this again:")
        print("\n".join(f"  - {p}" for p in dict.fromkeys(problems)))
        sys.exit(1)


if __name__ == "__main__":
    main()
