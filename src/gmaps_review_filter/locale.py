"""Locale detection and keyword matching for Maps UI labels."""


def matches_any(text: str | None, keywords: tuple[str, ...]) -> bool:
    """True when any keyword occurs in text (case-insensitive)."""
    lowered = (text or "").lower()
    return any(k in lowered for k in keywords)


def detect_lang(lang_tag: str | None) -> str:
    """Map an <html lang> value to a LOCALE_STRINGS key ("vi" or "en")."""
    return "vi" if (lang_tag or "").lower().startswith("vi") else "en"
