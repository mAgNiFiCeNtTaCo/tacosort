"""The classifier: decides where each message goes, following the "Decision order" in SPEC.md.

1. Safety guards -> Inbox (flagged, known contact, VIP, reply in a conversation).
2. Your rules from the config, in order.
3. Learned rules (from your corrections and existing folders).
4. Built-in heuristics.
5. Optional AI, only for messages the steps above were unsure about.
6. Default: leave it in the Inbox.
A decision below `min_confidence` counts as unsure. A category that is turned off stays in the Inbox.

Mail that looks written by a person (no bulk headers, no robot sender; `heuristics.looks_personal`)
is protected twice: a learned rule guessed from your existing folders may not move it (only your own
rules and corrections may), and it is never sent to the AI, whose verdict could be steered by text in
another email of the same batch.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING

from ..models import INBOX, AppConfig, Decision, LearnedRules, Message
from .heuristics import (
    bulk_markers,
    heuristic_decision,
    is_account_confirmation,
    is_platform_message,
    is_security_message,
    list_id_of,
    looks_personal,
)
from .rules import first_matching_rule

if TYPE_CHECKING:
    from .ai import AIClassifier

log = logging.getLogger(__name__)


def _vip_matches(entry: str, email: str, domain: str) -> bool:
    entry = entry.strip().lower()
    if not entry:
        return False
    if entry.startswith(("*@", "@")):
        entry = entry.split("@", 1)[1]
    entry = entry.removeprefix("*.")
    if "@" in entry:
        return email == entry
    return domain == entry or domain.endswith("." + entry)


class Classifier:
    """`Classifier(config, learned, contacts, ai).classify(msg) -> Decision`."""

    def __init__(self, config: AppConfig, learned: LearnedRules | None = None,
                 contacts: set[str] | None = None, ai: AIClassifier | None = None):
        self.config = config
        self.learned = learned
        self.contacts = {c.strip().lower() for c in (contacts or set())}
        self.ai = ai
        self.own = {a.email.lower() for a in config.accounts if a.email} | \
                   {a.username.lower() for a in config.accounts if a.username and "@" in a.username}
        self._learned_warned = False

    # ---- public --------------------------------------------------------------------------------
    def classify(self, msg: Message) -> Decision:
        return self.classify_many([msg])[0]

    def classify_many(self, msgs: Sequence[Message]) -> list[Decision]:
        """Classify several messages; unsure ones go to the AI together in one batch."""
        results: list[Decision | None] = []
        guesses: list[Decision | None] = []
        unsure: list[int] = []
        for i, msg in enumerate(msgs):
            decision, guess = self._before_ai(msg)
            results.append(decision)
            guesses.append(guess)
            # Only mail with bulk/robot markers of its own may be moved by an AI verdict, so
            # personal-looking mail is not sent at all (also keeps it private).
            if decision is None and not looks_personal(msg):
                unsure.append(i)

        if unsure and self.ai is not None and self.config.ai.enabled:
            try:
                answers = self.ai.classify_batch([msgs[i] for i in unsure], categories=self._enabled())
            except Exception as exc:  # noqa: BLE001 - the AI must never break a run
                log.warning("AI sorting failed (%s); those messages stay in the Inbox.", type(exc).__name__)
                answers = [None] * len(unsure)
            for i, ans in zip(unsure, answers):
                if ans is None:
                    continue
                if not ans.keeps and looks_personal(msgs[i]):
                    continue        # never reached today (see above); kept as a second lock
                if ans.confidence >= self.config.min_confidence:
                    results[i] = self._finish(ans)
                elif guesses[i] is None or ans.confidence > guesses[i].confidence:
                    guesses[i] = ans

        return [r if r is not None else self._default(guesses[i], msgs[i]) for i, r in enumerate(results)]

    # ---- steps ---------------------------------------------------------------------------------
    def _before_ai(self, msg: Message) -> tuple[Decision | None, Decision | None]:
        """(final decision, or None when unsure) and the best low-confidence guess so far."""
        guard = self._guard(msg)
        if guard:
            return guard, None

        hit = first_matching_rule(self.config.rules, msg)
        if hit:
            return self._finish(hit[1]), None

        # Codes, password resets, sign-in alerts and "confirm your email" stay in the Inbox even when
        # a learned rule files the rest of that sender's mail away. Only your own rules beat this.
        phrase = is_security_message(msg) or is_account_confirmation(msg)
        if phrase:
            return Decision(INBOX, 1.0, "guard", f'Security or account message ("{phrase}"): stays in the Inbox'), None
        phrase = is_platform_message(msg)
        if phrase:
            return Decision(INBOX, 1.0, "guard",
                            f'A person writing through a website ("{phrase}"): stays in the Inbox'), None

        guess: Decision | None = None
        learned = self._learned(msg)
        if learned is not None and not learned.explicit and not learned.keeps and looks_personal(msg):
            # Guessed from where you filed earlier mail: not enough to take a person's new mail
            # out of the Inbox. Your own rules and corrections (explicit) still apply.
            learned = None
        if learned is not None:
            if learned.confidence >= self.config.min_confidence:
                return self._finish(learned), None
            guess = learned

        h = heuristic_decision(msg, own_addresses=self.own)
        if h is not None:
            if h.confidence >= self.config.min_confidence:
                return self._finish(h), None
            if guess is None or h.confidence > guess.confidence:
                guess = h
        return None, guess

    def _guard(self, msg: Message) -> Decision | None:
        sender = msg.from_.email.lower()
        if self.config.keep_flagged and msg.is_flagged:
            return Decision(INBOX, 1.0, "guard", "Flagged/starred: always stays in the Inbox")
        for entry in self.config.vip:
            if _vip_matches(entry, sender, msg.from_.domain):
                return Decision(INBOX, 1.0, "guard", f"On your VIP list ({entry.strip()})")
        if self.config.keep_contacts and sender in self.contacts:
            return Decision(INBOX, 1.0, "guard", "From someone you have written to")
        if self._reply_to_you(msg):
            return Decision(INBOX, 1.0, "guard", "A reply in a conversation you are part of")
        return None

    def _reply_to_you(self, msg: Message) -> bool:
        """A reply (In-Reply-To/References) with no mailing-list or bulk headers, sent to you.
        We can't see your Sent folder here, so "addressed to one of your accounts" stands in for
        "a thread you wrote in"; when no account address is known, any non-bulk reply counts."""
        if not msg.is_reply or bulk_markers(msg):
            return False
        if not self.own:
            return True
        return any(a.email in self.own for a in msg.to + msg.cc)

    def _learned(self, msg: Message) -> Decision | None:
        if self.learned is None:
            return None
        try:
            return self.learned.lookup(msg.from_.email.lower(), msg.from_.domain.lower(),
                                       list_id_of(msg), msg.account)
        except Exception as exc:  # noqa: BLE001 - a broken store must not stop sorting
            if not self._learned_warned:
                log.warning("Could not read learned rules (%s); using the built-in rules only.", exc)
                self._learned_warned = True
            return None

    # ---- finishing -----------------------------------------------------------------------------
    def _enabled(self) -> list[str]:
        return [k for k in self.config.categories if k != INBOX and self.config.folder_for(k)]

    def _finish(self, d: Decision) -> Decision:
        """Turn a category that is unknown or turned off into "stay in the Inbox"."""
        if d.folder or d.category == INBOX:
            return d
        if d.category not in self.config.categories:
            return Decision(INBOX, d.confidence, d.source,
                            f'{d.reason} -> "{d.category}" is not a category, so it stays in the Inbox')
        if self.config.folder_for(d.category) is None:
            return Decision(INBOX, d.confidence, d.source,
                            f"{d.reason} -> the {d.category} category is turned off, so it stays in the Inbox")
        return d

    def _default(self, guess: Decision | None, msg: Message) -> Decision:
        if guess is None:
            return Decision(INBOX, 0.0, "default", "No rule or pattern matched, so it stays in the Inbox")
        if guess.category == INBOX and not guess.folder:
            return Decision(INBOX, guess.confidence, "default", f"Probably personal ({guess.reason})")
        where = guess.folder or guess.category.title()
        # Only a category guess about machine-sent mail may still follow its company (companies.py).
        keep_guess = not guess.folder and guess.category != "folder" and not looks_personal(msg)
        return Decision(
            INBOX, guess.confidence, "default",
            f"Not sure enough to move it (best guess {where}, {round(guess.confidence * 100)}% sure), "
            "so it stays in the Inbox",
            guess=guess if keep_guess else None,
        )
