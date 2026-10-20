"""Turn the official mascot still into the glasses launcher glyph (personal use only, never commit).

The launcher only takes single-colour artwork: light fur and face stay solid, the eyes and smile
become holes. The bridge uses the output automatically when it exists.

    /tmp/mascot-tools/bin/python -I tools/make_launcher_glyph.py \
        /Applications/Muse.app/Contents/Resources/Hatch.jpg webapp/public/icons/mascot-launcher.png
"""
import sys
from PIL import Image, ImageOps, ImageFilter
from rembg import new_session, remove
src, out = sys.argv[1], sys.argv[2]
img = Image.open(src).convert("RGB")
cut = remove(img, session=new_session("isnet-general-use"), post_process_mask=True)
alpha = cut.split()[3]
lum = ImageOps.grayscale(img)
# Light fur and face stay solid; dark eyes and smile become holes, cheeks and fur shading stay as soft tone.
tone = lum.point(lambda v: max(0, min(255, int((v - 70) * 255 / (205 - 70)))))
mask = Image.composite(tone, Image.new("L", img.size, 0), alpha)
rgba = Image.new("RGBA", img.size, (255, 255, 255, 0)); rgba.putalpha(mask)
bbox = alpha.point(lambda v: 255 if v > 40 else 0).getbbox(); rgba = rgba.crop(bbox)
rgba = rgba.crop((0, 0, rgba.width, min(rgba.height, int(rgba.width * 0.82))))
S = 256; pad = int(S * 0.06); rgba.thumbnail((S - 2 * pad, S - 2 * pad), Image.LANCZOS)
canvas = Image.new("RGBA", (S, S), (255, 255, 255, 0))
canvas.paste(rgba, ((S - rgba.width) // 2, S - pad - rgba.height), rgba)
canvas.save(out); print("ok", canvas.size)
