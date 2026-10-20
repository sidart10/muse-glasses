"""Extract the official Muse mascot from the locally installed Muse app (personal use only).

Cuts each clip out onto black (see-through on the Ray-Ban Display), writes small looping MP4s
plus posters to webapp/public/mascot/, and makes the launcher icons from the official still.
The outputs are gitignored and must never be committed or shared.

    uv venv /tmp/hatch-tools/.venv && uv pip install --python /tmp/hatch-tools/.venv/bin/python "rembg[cpu]" pillow
    /tmp/hatch-tools/.venv/bin/python -I tools/make_mascot.py webapp/public/mascot
"""
import subprocess, sys, time
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter
from rembg import new_session, remove

SRC = Path("/Applications/Muse.app/Contents/Resources")
OUT = Path(sys.argv[1]); WORK = Path("/tmp/hatch/frames")
CLIPS = {"idle": "HatchIdle", "working": "HatchWorking", "wave": "HatchWave",
         "making": "HatchMaking", "celebrate": "HatchMilestoneConfetti"}
FPS, SIZE = 12, 360
session = new_session("isnet-general-use")
# Soft fade at the bottom so close-up clips don't end in a hard cut.
fade = Image.new("L", (512, 512), 255); d = ImageDraw.Draw(fade)
for y in range(400, 512): d.line([(0, y), (512, y)], fill=int(255 * (511 - y) / 111))
OUT.mkdir(parents=True, exist_ok=True)
for name, clip in CLIPS.items():
    t = time.time(); fdir = WORK / name; cdir = WORK / f"{name}-cut"
    fdir.mkdir(parents=True, exist_ok=True); cdir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(SRC / f"{clip}.mp4"), "-vf", f"fps={FPS}",
                    str(fdir / "%04d.png")], check=True)
    for f in sorted(fdir.glob("*.png")):
        rgba = remove(Image.open(f).convert("RGB"), session=session, post_process_mask=True)
        alpha = Image.composite(rgba.split()[3], Image.new("L", rgba.size, 0), fade)
        black = Image.new("RGB", rgba.size, (0, 0, 0)); black.paste(rgba.convert("RGB"), mask=alpha)
        black.save(cdir / f.name)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-framerate", str(FPS), "-i", str(cdir / "%04d.png"),
                    "-vf", f"scale={SIZE}:{SIZE}:flags=lanczos", "-c:v", "libx264", "-profile:v", "main",
                    "-pix_fmt", "yuv420p", "-crf", "30", "-movflags", "+faststart", "-an", str(OUT / f"{name}.mp4")], check=True)
    Image.open(sorted(cdir.glob("*.png"))[len(list(cdir.glob('*.png'))) // 2]).resize((SIZE, SIZE)).save(OUT / f"{name}.jpg", quality=82)
    print(f"{name}: {len(list(fdir.glob('*.png')))} frames, {time.time() - t:.0f}s, {(OUT / f'{name}.mp4').stat().st_size // 1024} KB", flush=True)

# Launcher icons from the official still, on a rounded tile.
from PIL import ImageOps
icons = OUT.parent / "icons"; icons.mkdir(exist_ok=True)
still = Image.open(SRC / "Hatch.jpg").convert("RGB")
for name, size in (("icon-96.png", 96), ("icon-192.png", 192), ("apple-touch-icon.png", 180)):
    tile = ImageOps.fit(still, (size, size), Image.LANCZOS, centering=(0.5, 0.42))
    mask = Image.new("L", (size, size), 0); ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius=size // 4, fill=255)
    out = Image.new("RGBA", (size, size)); out.paste(tile, mask=mask); out.save(icons / name)
print("icons written", flush=True)
