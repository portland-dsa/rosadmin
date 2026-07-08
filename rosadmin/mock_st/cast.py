"""Canonical display identities for the fabricated cast.

One source of truth for a member's shown name, shared by the served Solidarity
Tech records (`personas`) and the contract-side stub, so a persona reads the same
in both fakes. Keyed by email, the identifier both sides carry. The membership
*state* an email plays is chosen elsewhere (the operator's mock map, or the stub's
roster); this module is only who they are by name.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Identity:
    """A member's display-name parts and the chosen-name rule that combines them."""

    first_name: str
    last_name: str
    alternate_name: str | None = None

    @property
    def full_name(self) -> str:
        """The name to show: the chosen `alternate_name` over `first_name`, plus last.

        `alternate_name` is a member's chosen name and overrides a possibly-legal
        or dead `first_name`; when it is absent the `first_name` stands.
        """
        shown = (
            self.alternate_name if self.alternate_name is not None else self.first_name
        )
        return f"{shown} {self.last_name}".strip()

    @property
    def handle(self) -> str:
        """The chosen display name, lowercased - a stable stem for fabricated
        addresses (e.g. a persona's fake Gmail alternate)."""
        shown = (
            self.alternate_name if self.alternate_name is not None else self.first_name
        )
        return shown.lower()


#: The fixed cast's display identities, keyed by the email each one is reached by.
CAST: dict[str, Identity] = {
    "ralsei@example.com": Identity("Ralsei", "Fluffington"),
    "kris@example.com": Identity("Kris", "Dreemurr"),
    "susie@example.com": Identity("Susie", "Gaster"),
    "noelle@example.com": Identity("Noelle", "Holiday"),
    "berdly@example.com": Identity("Berdly", "Smartt"),
    "spamton@example.com": Identity("Spamton", "G. Spamton"),
}


def identity_for(email: str) -> Identity:
    """The canonical identity for a cast email, or a fabricated fallback for any other."""
    known = CAST.get(email)
    if known is not None:
        return known
    return Identity(email.partition("@")[0].capitalize(), "Testerson")
