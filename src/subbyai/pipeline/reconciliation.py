"""Conservative overlap removal for finalized phrases; repeats remain meaningful."""

import re
import unicodedata


class Reconciler:
    def __init__(self) -> None:
        self._text = ""
        self._end = -10.0

    def accept(self, text: str, start: float, duration: float) -> str:
        text = " ".join(text.split())
        previous = self._text
        # Only touching/overlapping windows can duplicate decoder context.
        overlaps = start < self._end - 0.02
        if overlaps and _key(previous) == _key(text):
            return ""
        if overlaps:
            old, new = previous.split(), text.split()
            for count in range(min(12, len(old), len(new)), 1, -1):
                if [_key(v) for v in old[-count:]] == [_key(v) for v in new[:count]]:
                    text = " ".join(new[count:])
                    break
            # CJK often has no spaces. Require at least four characters of context.
            if len(old) == 1 and len(new) == 1:
                for count in range(min(40, len(previous), len(text)), 3, -1):
                    if previous[-count:] == text[:count]:
                        text = text[count:].lstrip()
                        break
        if text:
            self._text = text
            self._end = start + duration
        return text


def _key(text: str) -> str:
    return re.sub(r"[^\w]", "", unicodedata.normalize("NFKC", text).casefold())
