"""Pure naming for a body's two Google Groups: the display label and the email
address, each shortened on demand to fit Google's caps.

A leadership body (`name`, `body_type`) plus a `GroupKind` determine a
`GoogleGroupName` (the <= 73-char label) and a `GoogleGroupEmail` (the <= 64-char
local part plus the configured domain). Both build a full, readable form and only
initial down when a cap is exceeded; if even the fully-initialed form overflows,
they raise `GroupNameTooLong` rather than emit a value Google would reject.

The email is a machine key and stays literal (initials keep stop-words); the
label is for humans and initials harder (stop-words dropped, no interior spaces).
Nothing here touches Google or the database, so the whole module tests as a table
of inputs to outputs.
"""

from __future__ import annotations

import re
from enum import Enum

#: Google's caps, before the @ for the address and on the whole label.
EMAIL_LOCAL_CAP = 64
DISPLAY_NAME_CAP = 73

#: Dropped when initialing a *label*: `Hopes and Dreams` reads better as `HD`
#: than `HAD`. The email initialism keeps stop-words - a key need not read well,
#: and dropping them raises collision odds.
_LABEL_STOPWORDS = frozenset({"a", "an", "and", "the", "of", "for", "in", "to"})


class GroupKind(Enum):
    """Which of a body's two groups. The value is the email suffix; the enum name
    is the display word - a body's pair is `... Leaders` / `... Editors`."""

    Leaders = "leaders"
    Editors = "editors"

    @property
    def email_suffix(self) -> str:
        return self.value

    @property
    def display_word(self) -> str:
        return self.name


class GroupNameTooLong(Exception):
    """A body name that overflows a Google cap even fully initialed. Surfaced as a
    refusal, never an over-long value Google would reject mid-sweep."""


def _slugify(text: str) -> str:
    """Slugify: drop disallowed punctuation, collapse and trim whitespace, then
    turn spaces into dashes. `Queen's Mansion` -> `queens-mansion`, `Hopes &
    Dreams` -> `hopes-dreams`."""
    depunctuated = re.sub(r"[^a-z0-9\s]+", "", text.lower())
    return re.sub(r"\s+", " ", depunctuated).strip().replace(" ", "-")


def _titlecase(text: str) -> str:
    """Uppercase each word's first letter only when it is lowercase, leaving the
    rest untouched, so `TV` and `&` survive what a blunt `.title()` would wreck."""
    return " ".join(
        w[:1].upper() + w[1:] if w[:1].islower() else w for w in text.split()
    )


def _label_initials(text: str) -> str:
    """Upper-case initials of the significant words; stop-words and tokens not
    starting with a letter are dropped. `Hopes & Dreams` -> `HD`."""
    return "".join(
        w[0].upper()
        for w in text.split()
        if w[0].isalpha() and w.lower() not in _LABEL_STOPWORDS
    )


class Shortenable:
    """A slug with a full form (`long`) and an initialed short form (`short`).

    `short` is the first character of each dash part, kept as-is - stop-words
    included - because the email address it feeds is a machine key.

    >>> s = Shortenable("cyber-world")
    >>> s.long, s.short
    ('cyber-world', 'cw')
    """

    def __init__(self, slug: str) -> None:
        self._slug = slug

    @property
    def long(self) -> str:
        return self._slug

    @property
    def short(self) -> str:
        return "".join(part[0] for part in self._slug.split("-") if part)


class GoogleGroupEmail:
    """A body's <= 64-char group address for one `GroupKind`, shortened on demand.

    The ladder, keyed on the local part: full slug; the type slug initialed; the
    name slug initialed too; then `GroupNameTooLong`. The kind suffix never
    shortens - it is what tells a body's two groups apart.

    >>> str(GoogleGroupEmail("Fountain", "Committee", GroupKind.Leaders, "example.com"))
    'fountain-committee-leaders@example.com'
    """

    def __init__(
        self, body_name: str, body_type: str, kind: GroupKind, domain: str
    ) -> None:
        name = Shortenable(_slugify(body_name))
        btype = Shortenable(_slugify(body_type))
        suffix = kind.email_suffix
        for local in (
            f"{name.long}-{btype.long}-{suffix}",
            f"{name.long}-{btype.short}-{suffix}",
            f"{name.short}-{btype.short}-{suffix}",
        ):
            if len(local) <= EMAIL_LOCAL_CAP:
                self.local = local
                self.address = f"{local}@{domain}"
                return
        raise GroupNameTooLong(
            f"email local part for {body_name!r} exceeds {EMAIL_LOCAL_CAP}"
        )

    def __str__(self) -> str:
        return self.address


class GoogleGroupName:
    """A body's <= 73-char display label for one `GroupKind`, shortened on demand.

    The ladder: `<Titled Name> <Body Type> <Kind>`; then the type initialed
    (`Working Group` -> `WG`); then name and type collapsed to one no-space
    acronym with stop-words dropped (`Cyber World Working Group` -> `CWWG`);
    then `GroupNameTooLong`. The kind word is always kept.

    >>> str(GoogleGroupName("Fountain", "Committee", GroupKind.Editors))
    'Fountain Committee Editors'
    """

    def __init__(self, body_name: str, body_type: str, kind: GroupKind) -> None:
        titled = _titlecase(body_name)
        word = kind.display_word
        acronym = _label_initials(body_name) + _label_initials(body_type)
        for label in (
            f"{titled} {body_type} {word}",
            f"{titled} {_label_initials(body_type)} {word}",
            f"{acronym} {word}",
        ):
            if len(label) <= DISPLAY_NAME_CAP:
                self.label = label
                return
        raise GroupNameTooLong(
            f"display name for {body_name!r} exceeds {DISPLAY_NAME_CAP}"
        )

    def __str__(self) -> str:
        return self.label
