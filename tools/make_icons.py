from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter, ImageOps
from rembg import new_session, remove
SRC = Path("/Applications/Muse.app/Contents/Resources/Hatch.jpg")
out = Path("webapp/public/icons"); out.mkdir(parents=True, exist_ok=True)
wk = Path("webapp/public/.well-known"); wk.mkdir(parents=True, exist_ok=True)
cut = remove(Image.open(SRC).convert("RGB"), session=new_session("isnet-general-use"), post_process_mask=True)
bbox = cut.getbbox(); cut = cut.crop(bbox)
def tile(size):
    bg = Image.new("RGB", (size, size)); d = ImageDraw.Draw(bg)
    for y in range(size):  # warm Muse-like gradient
        t = y / size; d.line([(0, y), (size, y)], fill=(int(255 - 30 * t), int(214 - 40 * t), int(170 - 50 * t)))
    m = cut.copy(); m.thumbnail((int(size * 0.86), int(size * 0.86)), Image.LANCZOS)
    bg.paste(m, ((size - m.width) // 2, size - m.height), m)
    mask = Image.new("L", (size, size), 0); ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius=size // 4, fill=255)
    o = Image.new("RGBA", (size, size)); o.paste(bg, mask=mask); return o
for name, size in (("icon-96.png", 96), ("icon-192.png", 192), ("icon-512.png", 512), ("apple-touch-icon.png", 180)):
    tile(size).save(out / name)
# Glasses launcher: the official mascot photo (Hatch.jpg) converted to a single-colour mask, tinted
# by theme_color in .well-known/meta-wearables-manifest.json. Personal use only, never committed:
#   python -I tools/make_launcher_glyph.py /Applications/Muse.app/Contents/Resources/Hatch.jpg \
#       webapp/public/icons/mascot-launcher.png
print("icons ok")
