"""Caption text layout — the expensive half of the overlay, done once per caption.

``paintEvent`` runs many times per second (window moves, hover reveal, fades), so
nothing here happens inside it: wrapping, font metrics, glyph outlines and the
centring offset are resolved when a caption arrives and reused for repainting.

Line boxes are also where the "never rewrites itself as the user reads" rule is
enforced: a caption whose translation is still in flight gets a full-height,
empty translation box, so filling it later moves nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontMetricsF, QPainterPath, QStaticText, QTransform

from ..core.events import CaptionSegment, PrivacyTier, TranslationState
from ..core.settings import OriginalPosition
from . import theme
from .tokens import SPACE, OverlayStyle

# Panel padding and line spacing live on the style now: a preset built for
# reading over a game wants a tighter panel than one built for a film, and
# accessibility guidance asks for looser lines than either. The defaults on
# OverlayStyle are the values these constants held.
INNER_GAP = SPACE["xs"]  # between the two lines of one pair
PAIR_GAP = 10  # between pairs

SHADOW_OFFSET = 2.0
OUTLINE_WIDTH = 1.5
OUTLINE_ALPHA = 217  # rgba(0,0,0,0.85)

ROLE_ORIGINAL = "original"
ROLE_TRANSLATION = "translation"
ROLE_NOTICE = "notice"

_PREVIEW_TRANSLATED = (
    ("On commence dans une minute.", "We start in a minute."),
    ("Bonjour, ravi de vous rencontrer.", "Hello, nice to meet you."),
)
_PREVIEW_PLAIN = (
    ("Drag to move me, pull an edge to resize.", None),
    ("This is how your captions will look.", None),
)


@dataclass(slots=True)
class LineBox:
    """One painted line, positioned relative to its pair's top-left."""

    role: str
    text: str
    static: QStaticText | None
    """``None`` for a reserved line: it holds space and paints nothing."""

    path: QPainterPath | None
    """Glyph outline, only built for presets that stroke their text."""

    font: QFont | None = None
    """The font the static text was prepared with. ``QPainter.drawStaticText``
    lays out again against the painter's own font, so the painter must be told."""

    x: float = 0.0
    y: float = 0.0
    height: float = 0.0
    text_y: float = 0.0
    color: tuple[int, int, int] = (255, 255, 255)
    opacity: float = 1.0
    italic: bool = False


@dataclass(slots=True)
class PairLayout:
    """An original/translation pair as a single, indivisible block."""

    segment_id: int
    lines: list[LineBox] = field(default_factory=list)
    height: float = 0.0
    uncertain: bool = False
    opacity: float = 1.0


class OverlayFonts:
    """Caption fonts and metrics, rebuilt only when the resolved style changes."""

    def __init__(self, style: OverlayStyle) -> None:
        size = max(10, int(style.font_size))
        weight = int(style.font_weight)
        family = style.font_family
        self.translation = theme.caption_font(size, weight, family)
        self.original = theme.caption_font(
            max(9, round(size * style.original_ratio)), weight, family
        )
        self.translation_italic = _italic(self.translation)
        self.original_italic = _italic(self.original)
        self._metrics = {
            (ROLE_TRANSLATION, False): QFontMetricsF(self.translation),
            (ROLE_TRANSLATION, True): QFontMetricsF(self.translation_italic),
            (ROLE_ORIGINAL, False): QFontMetricsF(self.original),
            (ROLE_ORIGINAL, True): QFontMetricsF(self.original_italic),
        }

    def for_role(self, role: str, italic: bool = False) -> tuple[QFont, QFontMetricsF]:
        """Font and metrics for a role; anything not a translation reads as original."""
        if role == ROLE_TRANSLATION:
            font = self.translation_italic if italic else self.translation
            return font, self._metrics[(ROLE_TRANSLATION, italic)]
        font = self.original_italic if italic else self.original
        return font, self._metrics[(ROLE_ORIGINAL, italic)]


def build_pair(
    seg: CaptionSegment,
    style: OverlayStyle,
    fonts: OverlayFonts,
    width: float,
    position: OriginalPosition,
    reserve_translation: bool,
) -> PairLayout:
    """Lay out one caption as an original/translation pair.

    ``reserve_translation`` keeps an empty translation line in the stack while a
    translation is in flight, so the pair cannot grow under the reader's eyes.
    """
    wants_translation = reserve_translation or seg.translation_state is not TranslationState.NONE
    # Hiding the original is only meaningful when a translation stands in for it.
    # Otherwise the panel would be blank, which is the one thing it may never be.
    show_original = position is not OriginalPosition.HIDDEN or not wants_translation
    uncertain = seg.is_uncertain
    # Presets built for reading refuse both signals: dimming spends contrast,
    # and italics are the one type treatment the legibility research is
    # consistently negative about. Both cost most exactly the readers who can
    # least fall back on hearing the audio.
    italic = uncertain and style.uncertain_italic
    fade = style.uncertain_opacity if uncertain else 1.0

    original: list[LineBox] = []
    if show_original:
        original = _lay(
            seg.text, ROLE_ORIGINAL, style, fonts, width, italic, style.dim_original * fade
        )

    translation: list[LineBox] = []
    if wants_translation:
        text = seg.display_translation
        if text:
            translation = _lay(text, ROLE_TRANSLATION, style, fonts, width, italic, fade)
        else:
            translation = [_reserved(ROLE_TRANSLATION, style, fonts, italic)]

    groups = (
        [translation, original] if position is OriginalPosition.BELOW else [original, translation]
    )
    lines, height = _stack(groups)
    return PairLayout(seg.id, lines, height, uncertain)


def preview_segments(translation_enabled: bool, wanted: int) -> list[CaptionSegment]:
    """Sample captions for the placement preview in settings and onboarding.

    Real sentences in two languages, not lorem ipsum: the whole point of the
    preview is to judge whether both lines fit and read at this size.
    """
    samples = _PREVIEW_TRANSLATED if translation_enabled else _PREVIEW_PLAIN
    return [_sample(text, translated) for text, translated in samples[-max(1, wanted) :]]


def build_notice(text: str, style: OverlayStyle, fonts: OverlayFonts, width: float) -> PairLayout:
    """Lay out a health message so an empty overlay still names its own state."""
    lines, height = _stack(
        [_lay(text, ROLE_NOTICE, style, fonts, width, False, style.dim_original)]
    )
    return PairLayout(0, lines, height)


def wrap_lines(text: str, metrics: QFontMetricsF, width: float) -> list[str]:
    """Greedy word wrap that also breaks tokens wider than the panel.

    Spec §3.2 asks for a middle ellipsis on overlong words. Breaking instead of
    eliding is deliberate: scripts without spaces (Japanese, Chinese) arrive as
    one token, and a captioner that drops the middle of a sentence is worse than
    one that uses another line.
    """
    flat = " ".join(text.split())
    if not flat:
        return []
    if width <= 0:
        return [flat]
    lines: list[str] = []
    current = ""
    for word in flat.split(" "):
        for piece in _fit_pieces(word, metrics, width):
            candidate = f"{current} {piece}" if current else piece
            if current and metrics.horizontalAdvance(candidate) > width:
                lines.append(current)
                current = piece
            else:
                current = candidate
    if current:
        lines.append(current)
    return lines


# ---------- internals ----------


def _fit_pieces(word: str, metrics: QFontMetricsF, width: float) -> list[str]:
    if metrics.horizontalAdvance(word) <= width:
        return [word]
    pieces: list[str] = []
    current = ""
    for char in word:
        if current and metrics.horizontalAdvance(current + char) > width:
            pieces.append(current)
            current = char
        else:
            current += char
    if current:
        pieces.append(current)
    return pieces


def _lay(
    text: str,
    role: str,
    style: OverlayStyle,
    fonts: OverlayFonts,
    width: float,
    italic: bool,
    opacity: float,
) -> list[LineBox]:
    font, metrics = fonts.for_role(role, italic)
    color = style.translation_color if role == ROLE_TRANSLATION else style.text_color
    height = _line_height(font, metrics, style.line_spacing)
    lead = (height - metrics.height()) / 2.0
    boxes: list[LineBox] = []
    wrapped = wrap_lines(text, metrics, width)
    if len(wrapped) > style.max_lines:
        wrapped = wrapped[: style.max_lines]
        wrapped[-1] = metrics.elidedText(wrapped[-1] + " …", Qt.TextElideMode.ElideRight, width)
    for line in wrapped:
        static = QStaticText(line)
        static.setTextFormat(Qt.TextFormat.PlainText)
        static.setPerformanceHint(QStaticText.PerformanceHint.AggressiveCaching)
        static.prepare(QTransform(), font)
        x = _align_x(style.align, width, static.size().width())
        path: QPainterPath | None = None
        if style.outline:
            path = QPainterPath()
            path.addText(x, lead + metrics.ascent(), font, line)
        boxes.append(
            LineBox(
                role,
                line,
                static,
                path,
                font=font,
                x=x,
                height=height,
                text_y=lead,
                color=color,
                opacity=opacity,
                italic=italic,
            )
        )
    return boxes


def _reserved(role: str, style: OverlayStyle, fonts: OverlayFonts, italic: bool) -> LineBox:
    font, metrics = fonts.for_role(role, italic)
    color = style.translation_color if role == ROLE_TRANSLATION else style.text_color
    return LineBox(
        role,
        "",
        None,
        None,
        font=font,
        height=_line_height(font, metrics, style.line_spacing),
        color=color,
        opacity=0.0,
        italic=italic,
    )


def _stack(groups: list[list[LineBox]]) -> tuple[list[LineBox], float]:
    lines: list[LineBox] = []
    y = 0.0
    for group in groups:
        if not group:
            continue
        if lines:
            y += INNER_GAP
        for box in group:
            box.y = y
            y += box.height
            lines.append(box)
    return lines, y


def _line_height(font: QFont, metrics: QFontMetricsF, spacing: float) -> float:
    """Never below the font's own height: tightening past that clips glyphs."""
    return max(metrics.height(), font.pixelSize() * spacing)


def _align_x(align: str, width: float, text_width: float) -> float:
    """Where a line of this width starts inside the panel's content box."""
    if align == "left":
        return 0.0
    slack = max(0.0, width - text_width)
    return slack if align == "right" else slack / 2.0


def _sample(text: str, translated: str | None) -> CaptionSegment:
    if translated is None:
        return CaptionSegment(text=text)
    return CaptionSegment(
        text=text,
        translation=translated,
        translation_state=TranslationState.DONE,
        translation_tier=PrivacyTier.ON_DEVICE,
    )


def _italic(font: QFont) -> QFont:
    italic = QFont(font)
    italic.setItalic(True)
    return italic
