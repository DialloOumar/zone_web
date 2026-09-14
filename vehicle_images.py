"""The drawings a vehicle category can use as its default picture, and the
vehicle's own number written into them.

They are files shipped with the app, in static/images/vehicle-defaults/, never
uploaded from a screen: an SVG can carry a script, so the only drawings the app
will serve are the ones that came with it. Adding one is dropping a file in that
folder — the category form lists whatever is there.

Each number slot in a drawing is a <text> carrying data-machine-number. A
drawing may have several (the body, and the door); every one gets the code.
"""
import os
import re
from xml.sax.saxutils import escape, quoteattr

DEFAULT_IMAGE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "static", "images", "vehicle-defaults")
# Where the same folder is reachable from a page, for url_for('static', ...).
DEFAULT_IMAGE_STATIC = "images/vehicle-defaults/"

# Roughly how wide one character of the drawings' font is, as a fraction of its
# size. Arial Black is wide; this only has to be close enough to tell a code that
# fits its slot from one that would run off the side of the vehicle.
CHAR_WIDTH_EM = 0.72

_listing = {"mtime": None, "rows": []}
_templates = {}          # file name -> (mtime, drawing text)

_SLOT = re.compile(
    r'(<text\b)([^>]*\bdata-machine-number="[^"]*"[^>]*)>([^<]*)(</text>)')


def available_default_images():
    """[(filename, label)] for every drawing in the folder, by label.

    The label comes from the file name — "vehicule-service.svg" reads
    "Vehicule service" — so a new drawing needs no code to appear in the list.
    Read again only when the folder changes: a list of fifty vehicles asks this
    fifty times.
    """
    try:
        mtime = os.stat(DEFAULT_IMAGE_DIR).st_mtime
    except OSError:
        return []
    if _listing["mtime"] != mtime:
        names = [n for n in os.listdir(DEFAULT_IMAGE_DIR) if n.lower().endswith(".svg")]
        rows = [(n, n[:-4].replace("-", " ").replace("_", " ").capitalize()) for n in names]
        _listing["rows"] = sorted(rows, key=lambda r: r[1])
        _listing["mtime"] = mtime
    return _listing["rows"]


def is_default_image(name):
    """True only for a file that is actually in the folder. This is the check
    that keeps a stored value from pointing anywhere else on the disk."""
    return bool(name) and name in {n for n, _label in available_default_images()}


def drawing_version(name):
    """Changes whenever the drawing's file does, so a browser holding the old
    picture knows to fetch the new one."""
    try:
        return int(os.stat(os.path.join(DEFAULT_IMAGE_DIR, name)).st_mtime)
    except OSError:
        return 0


def _template(name):
    path = os.path.join(DEFAULT_IMAGE_DIR, name)
    mtime = os.stat(path).st_mtime
    cached = _templates.get(name)
    if cached and cached[0] == mtime:
        return cached[1]
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    # A drawing dropped in without being cleaned still carries its signed
    # metadata; it is dead weight on every vehicle shown.
    text = re.sub(r"<metadata>.*?</metadata>", "", text, flags=re.S)
    _templates[name] = (mtime, text)
    return text


def _attr(attrs, key):
    m = re.search(r'\b%s="([^"]*)"' % re.escape(key), attrs)
    return m.group(1) if m else None


def _fill_slot(match, code):
    opening, attrs, sample, closing = match.groups()
    try:
        size = float(_attr(attrs, "font-size") or 16)
    except ValueError:
        size = 16.0

    # How wide the slot is: the width the drawing forced the sample into, or
    # else the width the sample naturally took.
    forced = _attr(attrs, "textLength")
    try:
        room = float(forced) if forced else size * CHAR_WIDTH_EM * len(sample)
    except ValueError:
        room = size * CHAR_WIDTH_EM * len(sample)

    attrs = re.sub(r'\s*\b(textLength|lengthAdjust)="[^"]*"', "", attrs)
    attrs = re.sub(r'\bdata-machine-number="[^"]*"',
                   "data-machine-number=" + quoteattr(code), attrs)
    # A code too long for its slot is squeezed into it. A short one is left at
    # its natural width: stretched across the space, "EX-01" would look wrong.
    if size * CHAR_WIDTH_EM * len(code) > room:
        attrs += ' textLength="%.1f" lengthAdjust="spacingAndGlyphs"' % room
    return "%s%s>%s%s" % (opening, attrs, escape(code), closing)


def render_vehicle_drawing(name, code):
    """The category's drawing with this vehicle's code in every number slot.

    The code is escaped before it goes in: it is typed by users, and a "<" or
    an "&" in it would otherwise break the drawing or put markup inside it.
    Returns None when the drawing is not one of the shipped files.
    """
    if not is_default_image(name):
        return None
    code = code or ""
    return _SLOT.sub(lambda m: _fill_slot(m, code), _template(name))
