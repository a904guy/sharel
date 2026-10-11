"""Metadata viewer + stripper."""
from __future__ import annotations

import pytest
from PIL import ExifTags, Image, ImageCms, PngImagePlugin

from sharel.tools.metadata import read_metadata, strip_metadata

XMP = (b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
       b'<rdf:Description xmlns:dc="http://purl.org/dc/elements/1.1/" dc:creator="Secret Person"/></rdf:RDF>'
       b"</x:xmpmeta>")


def make_jpeg(path):
    img = Image.new("RGB", (40, 20), (200, 10, 10))
    img.paste((10, 200, 10), (0, 0, 10, 20))  # green stripe on the left
    exif = Image.Exif()
    exif[ExifTags.Base.Make] = "TestCam"
    exif[ExifTags.Base.Model] = "Model X"
    exif[ExifTags.Base.Orientation] = 6  # rotate 90 CW to display
    ex = exif.get_ifd(ExifTags.IFD.Exif)
    ex[ExifTags.Base.DateTimeOriginal] = "2024:05:06 07:08:09"
    gps = exif.get_ifd(ExifTags.IFD.GPSInfo)
    gps[ExifTags.GPS.GPSLatitudeRef] = "S"
    gps[ExifTags.GPS.GPSLatitude] = (33.0, 51.0, 36.0)
    gps[ExifTags.GPS.GPSLongitudeRef] = "E"
    gps[ExifTags.GPS.GPSLongitude] = (151.0, 12.0, 0.0)
    img.save(path, "JPEG", exif=exif, xmp=XMP, quality=95)


def test_read_jpeg_exif_gps_xmp(tmp_path):
    p = tmp_path / "photo.jpg"
    make_jpeg(p)
    md = read_metadata(p)
    assert md.file.name == "photo.jpg" and md.file.size == p.stat().st_size
    assert md.file.mime_type == "image/jpeg"
    im = md.image
    assert im.format == "JPEG" and (im.width, im.height) == (40, 20)
    by_name = {e.name: e for e in im.exif}
    assert by_name["Make"].value == "TestCam" and by_name["Make"].group == "Image"
    assert by_name["DateTimeOriginal"].group == "Exif"
    assert by_name["GPSLatitude"].group == "GPS"
    assert im.orientation == 6
    assert im.latitude == pytest.approx(-(33 + 51 / 60 + 36 / 3600))
    assert im.longitude == pytest.approx(151.2)
    assert "Secret Person" in im.xmp
    rows = md.rows()
    assert ("EXIF (Image)", "Make", "TestCam") in rows
    assert any(r[0] == "GPS" for r in rows)


def test_strip_jpeg_removes_exif_and_applies_orientation(tmp_path):
    src, dst = tmp_path / "in.jpg", tmp_path / "out.jpg"
    make_jpeg(src)
    strip_metadata(src, dst)
    with Image.open(dst) as out:
        assert len(out.getexif()) == 0
        assert "xmp" not in out.info and "exif" not in out.info
        assert out.size == (20, 40)  # orientation 6 applied -> rotated
        # the green stripe (left in the stored pixels) is at the top after a 90° CW rotation
        r, g, _b = out.convert("RGB").getpixel((10, 2))
        assert g > 150 and r < 80
    md = read_metadata(dst)
    assert md.image.exif == [] and md.image.latitude is None and md.image.xmp == ""
    assert b"TestCam" not in dst.read_bytes() and b"Secret Person" not in dst.read_bytes()
    # in place
    strip_metadata(src)
    with Image.open(src) as again:
        assert len(again.getexif()) == 0


def make_png(path, with_icc=True):
    img = Image.new("RGBA", (16, 8), (0, 0, 255, 128))
    info = PngImagePlugin.PngInfo()
    info.add_text("Author", "Someone")
    info.add_itxt("Description", "naïve ünïcode", lang="en")
    info.add_text("Comment", "compressed " * 20, zip=True)
    info.add_text("XML:com.adobe.xmp", XMP.decode())
    kw = {"pnginfo": info}
    if with_icc:
        kw["icc_profile"] = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    img.save(path, "PNG", **kw)


def test_png_text_icc_and_strip(tmp_path):
    p = tmp_path / "a.png"
    make_png(p)
    md = read_metadata(p)
    assert md.image.text["Author"] == "Someone"
    assert md.image.text["Description"] == "naïve ünïcode"
    assert md.image.text["Comment"].startswith("compressed")
    assert "Secret Person" in md.image.xmp
    assert md.image.icc_profile and md.image.icc_profile_size > 100
    out = strip_metadata(p, tmp_path / "b.png")
    with Image.open(out) as im:
        assert im.text == {}
        assert im.info.get("icc_profile")  # colour profile kept (like ShareX)
        assert im.getpixel((3, 3)) == (0, 0, 255, 128)  # pixels and alpha untouched
    no_icc = strip_metadata(p, tmp_path / "c.png", keep_icc_profile=False)
    with Image.open(no_icc) as im:
        assert "icc_profile" not in im.info and im.text == {}
    assert b"Someone" not in no_icc.read_bytes()


def test_animated_gif_strip_keeps_frames(tmp_path):
    p = tmp_path / "anim.gif"
    frames = [Image.new("P", (10, 10), i) for i in range(4)]
    for i, f in enumerate(frames):
        f.putpalette([0, 0, 0, 255, 0, 0, 0, 255, 0, 0, 0, 255] + [9] * (256 * 3 - 12))
        f.putpixel((0, 0), i)
    frames[0].save(p, save_all=True, append_images=frames[1:], duration=[100, 200, 300, 400], loop=3,
                   comment=b"private comment")
    assert read_metadata(p).image.text["Comment"] == "private comment"
    out = strip_metadata(p, tmp_path / "out.gif")
    with Image.open(out) as im:
        assert im.n_frames == 4
        assert "comment" not in im.info
        assert im.info.get("loop") == 3
        durs = []
        for i in range(im.n_frames):
            im.seek(i)
            durs.append(im.info["duration"])
        assert durs == [100, 200, 300, 400]


def test_non_image_file(tmp_path):
    p = tmp_path / "notes.txt"
    p.write_text("hello")
    md = read_metadata(p)
    assert md.image is None
    assert md.file.mime_type == "text/plain" and md.file.size == 5
    assert md.file.modified.tzinfo is not None
    assert [r[1] for r in md.rows()][:3] == ["Name", "Path", "Size"]
