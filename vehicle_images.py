"""The drawings a vehicle category can use as its default picture, and the
vehicle's own number written into them.

They are files shipped with the app, in static/images/vehicle-defaults/, never
uploaded from a screen: an SVG can carry a script, so the only drawings the app
will serve are the ones that came with it. Adding one is dropping a file in that
folder — the category form lists whatever is there.

Each number slot in a drawing is a <text> carrying data-machine-number. A
drawing may have several (the body, and the door); every one gets the code.
A slot should also carry data-max-width: how wide the panel under it is, in
the drawing's own units. That is what a long code is fitted into.
"""
import hashlib
import os
import re
from xml.sax.saxutils import escape, quoteattr

DEFAULT_IMAGE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "static", "images", "vehicle-defaults")
# Where the same folder is reachable from a page, for url_for('static', ...).
DEFAULT_IMAGE_STATIC = "images/vehicle-defaults/"

# How wide each character of the drawings' font (Arial Black) is, as a fraction
# of the font size. A single average overestimated codes made mostly of digits
# and hyphens, and squeezed them harder than they needed.
_WIDTH = {"-": 0.33, " ": 0.28, ".": 0.33, "/": 0.28, "_": 0.5}
_WIDTH.update({c: 0.66 for c in "0123456789"})
_WIDTH.update({c: 0.78 for c in "ABCDEFGHJKLNOPQRSTUVXYZ"})
_WIDTH.update({"I": 0.39, "M": 0.94, "W": 1.0})
DEFAULT_CHAR_EM = 0.72

# How far a long code may be narrowed before the text is made smaller instead.
# Arial Black stays legible squeezed to half its width, and keeping the height
# is what keeps a number readable once the drawing is shrunk into a card.
MIN_SQUEEZE = 0.5

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


def drawing_tag(name, code):
    """A short fingerprint of what the vehicle's picture shows: which drawing,
    that drawing's version, and the number written in. Put in the picture's
    address, so a change of type, of drawing or of number is a new address and
    the browser cannot keep showing the old picture from its cache.

    Hashed rather than spelled out: the code is typed by users and may hold a
    double quote or an "&", which neither an ETag nor an address can carry."""
    raw = "%s|%s|%s" % (name, drawing_version(name), code)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def text_width_em(text):
    """How wide `text` runs in the drawings' font, in multiples of its size."""
    return sum(_WIDTH.get(ch.upper(), DEFAULT_CHAR_EM) for ch in text)


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


def _float(value, default):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _fill_slot(match, code):
    opening, attrs, sample, closing = match.groups()
    size = _float(_attr(attrs, "font-size"), 16.0)

    # How wide the slot is: the panel width the drawing declares, else the
    # width it once forced its sample into, else the sample's own width.
    room = _float(_attr(attrs, "data-max-width"), None)
    if room is None:
        room = _float(_attr(attrs, "textLength"), None)
    if room is None:
        room = size * text_width_em(sample)

    attrs = re.sub(r'\s*\b(textLength|lengthAdjust)="[^"]*"', "", attrs)
    attrs = re.sub(r'\bdata-machine-number="[^"]*"',
                   "data-machine-number=" + quoteattr(code), attrs)

    natural = size * text_width_em(code)
    if natural > room:
        # Too long for its panel. Narrow the letters first and keep their
        # height; only once that would pass MIN_SQUEEZE is the text made
        # smaller, and just enough to fit.
        if natural * MIN_SQUEEZE > room:
            size = room / (text_width_em(code) * MIN_SQUEEZE)
            attrs = re.sub(r'\bfont-size="[^"]*"', 'font-size="%.1f"' % size, attrs)
        attrs += ' textLength="%.1f" lengthAdjust="spacingAndGlyphs"' % room
    # A short code is left at its natural width: "EX-01" stretched across a
    # panel sized for a long number looks wrong.
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
