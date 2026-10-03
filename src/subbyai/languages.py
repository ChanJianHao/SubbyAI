"""Language names for the UI.

Users pick languages by name; ISO codes exist only in storage and on the wire.

The list is the languages Whisper handles well, ordered so the common ones for
this product's audience (CJK media, European film, calls) come first in the
picker without a separate "recent" mechanism.
"""

from __future__ import annotations

#: ISO 639-1 code -> English display name.
LANGUAGES: dict[str, str] = {
    "en": "English",
    "ja": "Japanese",
    "ko": "Korean",
    "zh": "Chinese",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "pt": "Portuguese",
    "ru": "Russian",
    "it": "Italian",
    "ar": "Arabic",
    "hi": "Hindi",
    "id": "Indonesian",
    "th": "Thai",
    "vi": "Vietnamese",
    "tr": "Turkish",
    "pl": "Polish",
    "nl": "Dutch",
    "sv": "Swedish",
    "da": "Danish",
    "no": "Norwegian",
    "fi": "Finnish",
    "cs": "Czech",
    "el": "Greek",
    "he": "Hebrew",
    "hu": "Hungarian",
    "ro": "Romanian",
    "uk": "Ukrainian",
    "ms": "Malay",
    "fa": "Persian",
    "ta": "Tamil",
    "bn": "Bengali",
    "ur": "Urdu",
    "tl": "Tagalog",
    "ca": "Catalan",
    "bg": "Bulgarian",
    "hr": "Croatian",
    "sk": "Slovak",
    "sl": "Slovenian",
    "lt": "Lithuanian",
    "lv": "Latvian",
    "et": "Estonian",
    "sr": "Serbian",
    "is": "Icelandic",
    "mk": "Macedonian",
    "sw": "Swahili",
    "af": "Afrikaans",
    "az": "Azerbaijani",
    "be": "Belarusian",
    "bs": "Bosnian",
    "cy": "Welsh",
    "gl": "Galician",
    "hy": "Armenian",
    "ka": "Georgian",
    "kk": "Kazakh",
    "kn": "Kannada",
    "mr": "Marathi",
    "ne": "Nepali",
    "si": "Sinhala",
    "sq": "Albanian",
    "te": "Telugu",
}

AUTO_DETECT = ""
AUTO_DETECT_LABEL = "Detect automatically"


def language_name(code: str, fallback: str = "") -> str:
    """Display name for a code. Empty code means auto-detect."""
    if not code:
        return fallback
    return LANGUAGES.get(code.lower(), code.upper())


def language_code(name: str) -> str:
    """Reverse lookup; returns "" when the name is the auto-detect entry."""
    if not name or name == AUTO_DETECT_LABEL:
        return AUTO_DETECT
    lowered = name.strip().lower()
    for code, display in LANGUAGES.items():
        if display.lower() == lowered:
            return code
    return ""


def sorted_languages() -> list[tuple[str, str]]:
    """(code, name) pairs alphabetical by name, for pickers."""
    return sorted(LANGUAGES.items(), key=lambda item: item[1])


def default_target_language() -> str:
    """The user's own language, from the OS, so the default is usually right."""
    try:
        from PySide6.QtCore import QLocale

        code = QLocale.system().name().split("_")[0].lower()
        if code in LANGUAGES:
            return code
    except Exception:  # a locale failure must not block startup
        pass
    return "en"
