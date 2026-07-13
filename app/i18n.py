"""Minimal JSON-catalog i18n: tr(key) looks up the active locale, falling
back to English so a partially-translated locale file never crashes the UI.

Language set: top 50 languages by combined speaker population, using each
language's standard written/software-localization form (e.g. one Mandarin
Chinese entry, one Modern Standard Arabic entry) rather than every mutually
-unintelligible topolect, matching how software is normally localized.
"""

import json
from functools import lru_cache
from pathlib import Path

from app.core.settings import DEFAULT_LOCALE, load_settings

LOCALES_DIR = Path(__file__).parent / "resources" / "locales"

# (code, English name, native name)
SUPPORTED_LOCALES = [
    ("en", "English", "English"),
    ("zh", "Mandarin Chinese", "中文"),
    ("hi", "Hindi", "हिन्दी"),
    ("es", "Spanish", "Español"),
    ("fr", "French", "Français"),
    ("ar", "Arabic", "العربية"),
    ("bn", "Bengali", "বাংলা"),
    ("pt", "Portuguese", "Português"),
    ("ru", "Russian", "Русский"),
    ("ur", "Urdu", "اردو"),
    ("id", "Indonesian", "Bahasa Indonesia"),
    ("de", "German", "Deutsch"),
    ("ja", "Japanese", "日本語"),
    ("mr", "Marathi", "मराठी"),
    ("te", "Telugu", "తెలుగు"),
    ("tr", "Turkish", "Türkçe"),
    ("ta", "Tamil", "தமிழ்"),
    ("vi", "Vietnamese", "Tiếng Việt"),
    ("ko", "Korean", "한국어"),
    ("fa", "Persian", "فارسی"),
    ("ha", "Hausa", "Hausa"),
    ("sw", "Swahili", "Kiswahili"),
    ("jv", "Javanese", "Basa Jawa"),
    ("it", "Italian", "Italiano"),
    ("pa", "Punjabi", "ਪੰਜਾਬੀ"),
    ("gu", "Gujarati", "ગુજરાતી"),
    ("am", "Amharic", "አማርኛ"),
    ("th", "Thai", "ไทย"),
    ("kn", "Kannada", "ಕನ್ನಡ"),
    ("my", "Burmese", "မြန်မာဘာသာ"),
    ("yo", "Yoruba", "Yorùbá"),
    ("uz", "Uzbek", "Oʻzbekcha"),
    ("ml", "Malayalam", "മലയാളം"),
    ("or", "Odia", "ଓଡ଼ିଆ"),
    ("uk", "Ukrainian", "Українська"),
    ("pl", "Polish", "Polski"),
    ("ms", "Malay", "Bahasa Melayu"),
    ("nl", "Dutch", "Nederlands"),
    ("ig", "Igbo", "Igbo"),
    ("si", "Sinhala", "සිංහල"),
    ("ne", "Nepali", "नेपाली"),
    ("ro", "Romanian", "Română"),
    ("zu", "Zulu", "isiZulu"),
    ("so", "Somali", "Soomaali"),
    ("hr", "Croatian", "Hrvatski"),
    ("el", "Greek", "Ελληνικά"),
    ("hu", "Hungarian", "Magyar"),
    ("cs", "Czech", "Čeština"),
    ("he", "Hebrew", "עברית"),
    ("sv", "Swedish", "Svenska"),
]

RTL_LOCALES = {"ar", "ur", "fa", "he"}


@lru_cache(maxsize=None)
def _load_catalog(locale: str) -> dict:
    path = LOCALES_DIR / f"{locale}.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


@lru_cache(maxsize=1)
def _english_catalog() -> dict:
    return _load_catalog(DEFAULT_LOCALE)


def current_locale() -> str:
    return load_settings().locale


def tr(key: str, **kwargs) -> str:
    """Looks up `key` in the active locale, then English, then returns the
    key itself as a last-resort fallback so missing translations are visibly
    obvious rather than silently blank."""
    locale = current_locale()
    catalog = _load_catalog(locale)
    text = catalog.get(key) or _english_catalog().get(key) or key
    return text.format(**kwargs) if kwargs else text


def is_rtl(locale: str | None = None) -> bool:
    return (locale or current_locale()) in RTL_LOCALES


def clear_cache() -> None:
    """Call after changing the active locale file set (e.g. in tests)."""
    _load_catalog.cache_clear()
    _english_catalog.cache_clear()
