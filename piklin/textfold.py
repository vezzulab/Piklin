"""One way of comparing words: lower case and no accents, so that "cancion"
finds "canción" and "Zurich" finds "Zürich"."""
import unicodedata


def fold(text: str) -> str:
    t = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in t if not unicodedata.combining(c)).lower()
