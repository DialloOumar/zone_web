"""The drawings a vehicle category can use as its default picture.

They are files shipped with the app, in static/images/vehicle-defaults/, never
uploaded from a screen: an SVG can carry a script, so the only drawings the app
will serve are the ones that came with it. Adding one is dropping a file in that
folder — the category form lists whatever is there.
"""
import os

DEFAULT_IMAGE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "static", "images", "vehicle-defaults")
# Where the same folder is reachable from a page, for url_for('static', ...).
DEFAULT_IMAGE_STATIC = "images/vehicle-defaults/"


def available_default_images():
    """[(filename, label)] for every drawing in the folder, by label.

    The label comes from the file name — "vehicule-service.svg" reads
    "Vehicule service" — so a new drawing needs no code to appear in the list.
    """
    try:
        names = [n for n in os.listdir(DEFAULT_IMAGE_DIR) if n.lower().endswith(".svg")]
    except OSError:
        return []
    rows = [(n, n[:-4].replace("-", " ").replace("_", " ").capitalize()) for n in names]
    return sorted(rows, key=lambda r: r[1])


def is_default_image(name):
    """True only for a file that is actually in the folder. This is the check
    that keeps a posted value from pointing anywhere else on the disk."""
    return bool(name) and name in {n for n, _label in available_default_images()}
