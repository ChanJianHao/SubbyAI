"""Conservative overlap removal for finalized phrases; repeats remain meaningful."""

import re
import unicodedata
from copy import copy


class Reconciler:
    def __init__(self) -> None:
        self._text = ""
        self._end = -10.0
        self._words = []
        self._confirmed_end = -10.0

    def preview(self, text: str, start: float, duration: float, words=()) -> str:
        """Tentative output cannot move the finalized alignment cursor."""
        return copy(self).accept(text, start, duration, words)

    def accept(self, text: str, start: float, duration: float, words=()) -> str:
        text = " ".join(text.split())
        previous = self._text
        # Only touching/overlapping windows can duplicate decoder context.
        overlaps = start < self._end - 0.02
        if overlaps and start + duration <= self._end + 0.2 and _contained_phrase(previous, text):
            self._end = max(self._end, start + duration)
            return ""
        if words:
            if overlaps and start + words[0].start <= self._end:
                # Word times can drift between independent decodes of the same
                # overlap. Reconcile a multiword suffix/prefix as well as times,
                # provided it starts in already decoded audio. A whole repeated
                # phrase in a window with substantial new audio remains speech.
                old, new = previous.split(), text.split()
                for count in range(min(12, len(old), len(new)), 1, -1):
                    if [_key(v) for v in old[-count:]] != [_key(v) for v in new[:count]]:
                        continue
                    if count == len(new) and start + duration > self._end + 0.2:
                        continue
                    prefix = _key(" ".join(new[:count]))
                    consumed = ""
                    for index, word in enumerate(words):
                        consumed += _key(word.text)
                        if consumed == prefix:
                            words = words[index + 1:]
                            break
                        if not prefix.startswith(consumed):
                            break
                    break
            # A repeated decoder tail with collapsed timestamps is not a second
            # utterance. Require both exact repeated text and >=3 unaligned
            # lexical tokens, preserving genuine repetitions with real duration.
            for split in range(1, len(words) - 2):
                tail = words[split:]
                collapsed = sum(w.end - w.start < 0.005 and bool(_key(w.text)) for w in tail)
                prefix = _key("".join(w.text for w in words[:split]))
                suffix = _key("".join(w.text for w in tail))
                if collapsed >= 3 and suffix and prefix.endswith(suffix):
                    words = words[:split]
                    break
            accepted = []
            for word in words:
                begin, end = start + word.start, start + word.end
                if end < begin or end > start + duration + 0.2:
                    continue
                if overlaps and end <= self._confirmed_end - 0.08:
                    continue
                duplicated = overlaps and any(
                    _key(word.text) == _key(old_text)
                    and min(end, old_end) - max(begin, old_begin)
                    > 0.5 * min(end - begin, old_end - old_begin)
                    and min(end - begin, old_end - old_begin) > 0
                    for old_text, old_begin, old_end in self._words
                )
                if not duplicated:
                    accepted.append((word.text, begin, end))
            # Use the decoder's whitespace/punctuation rather than splitting
            # CJK or inventing spaces between subword tokens.
            if any(w.text[:1].isspace() for w in words) or " " not in text:
                rendered = "".join(w[0] for w in accepted)
            else:
                # Compatible APIs often return whole words without Whisper's
                # leading-space convention. Preserve Korean/Latin word spaces.
                rendered = " ".join(w[0] for w in accepted)
                rendered = re.sub(r"\s+([,.;:!?\u3002\uff01\uff1f])", r"\1", rendered)
            text = " ".join(rendered.split())
            # A lookahead repair can cover virtually all of the next tail.
            # Decoder timestamps for that short tail may shift completely
            # beyond the previous word cursor. A repeated multiword phrase
            # with almost no new audio is still the same spoken phrase.
            if (
                overlaps and start + duration <= self._end + 0.2
                and _contained_phrase(previous, text)
            ):
                text, accepted = "", []
            if accepted:
                self._words = (self._words + accepted)[-80:]
                self._confirmed_end = max(self._confirmed_end, max(w[2] for w in accepted))
        elif overlaps:
            old, new = previous.split(), text.split()
            # One-word overlaps include names. Do not remove a repeated isolated
            # interjection just because padded audio windows touch.
            minimum = 1 if len(old) > 1 and len(new) > 1 else 2
            for count in range(min(12, len(old), len(new)), minimum - 1, -1):
                if [_key(v) for v in old[-count:]] == [_key(v) for v in new[:count]]:
                    if count == len(new) and start + duration > self._end + 0.05:
                        continue
                    text = " ".join(new[count:])
                    break
            # CJK often has no spaces. Require at least four characters of context.
            if len(old) == 1 and len(new) == 1:
                for count in range(min(40, len(previous), len(text)), 3, -1):
                    if previous[-count:] == text[:count]:
                        text = text[count:].lstrip()
                        break
        self._text = " ".join(text.split()) if text else previous
        self._end = max(self._end, start + duration)
        return text


def _key(text: str) -> str:
    return re.sub(r"[^\w]", "", unicodedata.normalize("NFKC", text).casefold())


def _contained_phrase(previous: str, text: str) -> bool:
    old = [_key(v) for v in previous.split()[-80:]]
    new = [_key(v) for v in text.split()]
    return len(new) >= 3 and any(
        old[index:index + len(new)] == new for index in range(len(old) - len(new) + 1)
    )
