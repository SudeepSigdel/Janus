"""Generate fictional, phone-photo-like Nepali/Hindi document images with ground truth.

Names are random combinations of common syllables, headers say "नमूना / SAMPLE", and there are no
real logos or emblems (CLAUDE.md). Output is deterministic for a given seed. `truth.json` maps each
file to its kind and the values printed on it.
"""

from __future__ import annotations

import json
import os
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

# What the form behind each kind of document needs (the keys of `janus.text.fields`).
WANT = {
    "citizen": ["name", "citizenship_no", "dob_bs", "ward"],
    "boid": ["name", "boid", "crn", "bank"],
    "hindi_id": ["name", "dob_ad", "phone"],
}
KIND_COUNTS = (("citizen", 8), ("boid", 6), ("hindi_id", 6))

_FONT_CANDIDATES = (
    "C:/Windows/Fonts/Nirmala.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf",
    "/usr/share/fonts/truetype/lohit-devanagari/Lohit-Devanagari.ttf",
    "/System/Library/Fonts/Supplemental/DevanagariMT.ttc",
)
NE_FIRST = ["सीता", "राम", "गीता", "हरि", "सुनिता", "अनिल", "मीना", "प्रकाश", "कमला", "बिनोद"]
NE_LAST = ["श्रेष्ठ", "तामाङ", "गुरुङ", "पौडेल", "अधिकारी", "थापा", "राई", "खड्का", "भट्टराई", "लामा"]
HI_FIRST = ["रमेश", "सुनीता", "अजय", "पूजा", "विकास", "अनीता", "मोहन", "राधा", "संजय", "नेहा"]
HI_LAST = ["शर्मा", "वर्मा", "यादव", "गुप्ता", "मिश्रा", "सिंह", "पटेल", "जोशी", "चौहान", "तिवारी"]
BANKS = ["नबिल बैंक", "सनराइज बैंक", "हिमालयन बैंक", "एनआईसी एशिया"]
_TO_DEV = str.maketrans("0123456789", "०१२३४५६७८९")


def _dev(text: str) -> str:
    return text.translate(_TO_DEV)


def _digits(rng: random.Random, n: int) -> str:
    return "".join(rng.choice("0123456789") for _ in range(n))


def _font(size: int) -> ImageFont.FreeTypeFont:
    for path in (os.environ.get("JANUS_DOC_FONT", ""), *_FONT_CANDIDATES):
        if path and Path(path).is_file():
            return ImageFont.truetype(path, size)
    raise FileNotFoundError(
        "no Devanagari font found; set JANUS_DOC_FONT to a .ttf/.ttc that has Devanagari"
    )


def _photo_effects(img: Image.Image, rng: random.Random) -> Image.Image:
    """Make a flat render look hand-held: tilt, blur, uneven light, noise."""
    ground = (96, 84, 72)
    bg = Image.new("RGB", (img.width + 160, img.height + 160), ground)
    bg.paste(img, (80, 80))
    bg = bg.rotate(rng.uniform(-3.5, 3.5), resample=Image.BICUBIC, fillcolor=ground)
    bg = bg.filter(ImageFilter.GaussianBlur(rng.uniform(0.5, 1.1)))
    bg = ImageEnhance.Brightness(bg).enhance(rng.uniform(0.82, 1.08))
    bg = ImageEnhance.Contrast(bg).enhance(rng.uniform(0.8, 1.0))
    noise = Image.effect_noise(bg.size, rng.uniform(6, 14)).convert("RGB")
    return Image.blend(bg, noise, 0.07)


def _render(
    title: str, rows: list[tuple[str, str]], extra: list[str], rng: random.Random, clean: bool
) -> Image.Image:
    img = Image.new("RGB", (1000, 120 + 78 * len(rows) + 60 * len(extra)), (244, 240, 226))
    draw = ImageDraw.Draw(img)
    draw.rectangle([8, 8, img.width - 9, img.height - 9], outline=(60, 60, 60), width=3)
    draw.text((40, 24), title, font=_font(40), fill=(30, 30, 90))
    y = 100
    for label, value in rows:
        draw.text((40, y), label, font=_font(24), fill=(90, 90, 90))
        draw.text((40, y + 28), value, font=_font(34), fill=(15, 15, 15))
        y += 78
    for line in extra:  # distractors: serial numbers, issue dates
        draw.text((40, y), line, font=_font(26), fill=(70, 70, 70))
        y += 60
    return img if clean else _photo_effects(img, rng)


def _citizen(rng: random.Random):
    name = f"{rng.choice(NE_FIRST)} {rng.choice(NE_LAST)}"
    number = f"{_digits(rng, 2)}-{_digits(rng, 2)}-{_digits(rng, 2)}-{_digits(rng, 5)}"
    dob = f"{rng.randint(2035, 2070)}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"
    ward = str(rng.randint(1, 15))
    truth = {"name": name, "citizenship_no": _dev(number), "dob_bs": _dev(dob), "ward": _dev(ward)}
    rows = [
        ("नाम / Name", name),
        ("नागरिकता नं. / Citizenship No.", _dev(number)),
        ("जन्म मिति (वि.सं.) / Date of birth (BS)", _dev(dob)),
        ("वडा नं. / Ward", _dev(ward)),
    ]
    extra = [f"क्रम संख्या / Serial: {_dev(_digits(rng, 7))}  जारी मिति: {_dev('2078-05-14')}"]
    return "नागरिकता प्रमाणपत्र (नमूना / SAMPLE)", rows, extra, truth


def _boid(rng: random.Random):
    name = f"{rng.choice(NE_FIRST)} {rng.choice(NE_LAST)}"
    boid, crn, bank = _digits(rng, 8), f"CRN-{_digits(rng, 6)}", rng.choice(BANKS)
    truth = {"name": name, "boid": _dev(boid), "crn": crn, "bank": bank}
    rows = [
        ("नाम / Name", name),
        ("वीओआईडी / BOID", _dev(boid)),
        ("सीआरएन / CRN", crn),
        ("बैंक / Bank", bank),
    ]
    extra = [f"खाता खोलेको मिति: {_dev('2080-01-09')}   फोन: {_dev('98' + _digits(rng, 8))}"]
    return "डिम्याट खाता विवरण (नमूना / SAMPLE)", rows, extra, truth


def _hindi_id(rng: random.Random):
    name = f"{rng.choice(HI_FIRST)} {rng.choice(HI_LAST)}"
    phone = "9" + _digits(rng, 9)
    dob = f"{rng.randint(1, 28):02d}/{rng.randint(1, 12):02d}/{rng.randint(1960, 2005)}"
    truth = {"name": name, "phone": _dev(phone), "dob_ad": _dev(dob)}
    rows = [
        ("नाम / Name", name),
        ("जन्म तिथि / Date of birth", _dev(dob)),
        ("मोबाइल / Mobile", _dev(phone)),
    ]
    extra = [f"पहचान संख्या / ID: {_dev(_digits(rng, 12))}"]
    return "पहचान पत्र (नमूना / SAMPLE)", rows, extra, truth


_MAKERS = {"citizen": _citizen, "boid": _boid, "hindi_id": _hindi_id}


def make_documents(out: Path, seed: int = 7, clean: bool = False) -> dict[str, dict[str, str]]:
    """Write the images and `truth.json` under `out`; returns the truth mapping."""
    out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    truth_all: dict[str, dict[str, str]] = {}
    index = 0
    for kind, count in KIND_COUNTS:
        for _ in range(count):
            title, rows, extra, truth = _MAKERS[kind](rng)
            image = _render(title, rows, extra, rng, clean)
            name = f"doc{index:02d}_{kind}.jpg"
            image.save(out / name, quality=rng.randint(55, 80))
            truth_all[name] = {"kind": kind, **truth}
            index += 1
    (out / "truth.json").write_text(
        json.dumps(truth_all, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return truth_all
