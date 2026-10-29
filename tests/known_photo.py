"""A known-answer photo for e2e and smoke tests, built with Pillow so every fact in it is planted on purpose.

A navy shop sign reading "PIEKARNIA NOWAK" on a cream wall; EXIF says it was taken 2026-10-01 21:14:05 on a
"Detective Cam 1" at 50.06143 N, 19.93658 E (Kraków's main square).
"""

import io

from PIL import ExifTags, Image, ImageDraw, ImageFont

SIGN = "PIEKARNIA NOWAK"
TAKEN_AT = "Taken 2026-10-01 21:14:05 (camera clock)"
CAMERA = "Camera: Detective Cam 1"
LOCATION = "GPS 50.06143, 19.93658"


def dms(value: float) -> tuple[float, float, float]:
    minutes, seconds = divmod(value * 3600, 60)
    degrees, minutes = divmod(minutes, 60)
    return (degrees, minutes, round(seconds, 4))


def known_photo() -> bytes:
    image = Image.new("RGB", (800, 400), "#f4e9d8")
    draw = ImageDraw.Draw(image)
    draw.rectangle((80, 130, 720, 270), fill="#1f3a5f")
    draw.text((400, 200), SIGN, fill="white", font=ImageFont.load_default(60), anchor="mm")

    exif = Image.Exif()
    exif[ExifTags.Base.Make] = "Detective"
    exif[ExifTags.Base.Model] = "Cam 1"
    exif.get_ifd(ExifTags.IFD.Exif)[ExifTags.Base.DateTimeOriginal] = "2026:10:01 21:14:05"
    gps = exif.get_ifd(ExifTags.IFD.GPSInfo)
    gps[ExifTags.GPS.GPSLatitudeRef] = "N"
    gps[ExifTags.GPS.GPSLatitude] = dms(50.06143)
    gps[ExifTags.GPS.GPSLongitudeRef] = "E"
    gps[ExifTags.GPS.GPSLongitude] = dms(19.93658)

    out = io.BytesIO()
    image.save(out, "JPEG", exif=exif)
    return out.getvalue()
