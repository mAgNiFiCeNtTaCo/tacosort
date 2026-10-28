"""Built-in heuristics: guess a category from headers, the sender and the subject.

How it works: every clue found in a message is a *signal* (category, weight 0..1, reason). Signals
for the same category are combined like independent pieces of evidence (1 - product of misses),
the strongest category wins, and a close runner-up lowers the confidence. Two things short-cut the
scoring because they must stay in the Inbox: security messages (codes, password resets, sign-in
alerts) and calendar invitations.

To teach MailTidy a new shop, bank, airline or keyword, add it to the tables just below; no other
code needs to change. Domains match the domain itself and any subdomain (amazon.com also matches
email.amazon.com). Keywords match whole words, case-insensitively.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from ..models import INBOX, Decision, Message

# --------------------------------------------------------------------------------------------------
# Tables (extend freely)
# --------------------------------------------------------------------------------------------------

# Known sender domains per category, with the weight a match adds.
DOMAINS: dict[str, tuple[float, tuple[str, ...]]] = {
    "receipts": (0.45, (
        "amazon.com", "amazon.co.uk", "amazon.de", "amazon.fr", "amazon.es", "amazon.it", "amazon.ca",
        "ebay.com", "ebay.co.uk", "ebay.de", "etsy.com", "shopify.com", "shop.app", "aliexpress.com",
        "walmart.com", "target.com", "bestbuy.com", "costco.com", "homedepot.com", "ikea.com",
        "apple.com", "temu.com", "paypal.com", "paypal.de", "paypal.co.uk", "wayfair.com", "zalando.de", "asos.com", "doordash.com",
        "ubereats.com", "grubhub.com", "instacart.com", "steampowered.com", "playstation.com",
        "ups.com", "fedex.com", "usps.com", "usps.gov", "dhl.com", "dhl.de", "royalmail.com",
        "canadapost.ca", "dpd.com", "hermesworld.com", "narvar.com", "aftership.com", "route.com",
    )),
    "finance": (0.55, (
        "chase.com", "bankofamerica.com", "bofa.com", "wellsfargo.com", "citi.com", "citibank.com",
        "capitalone.com", "discover.com", "americanexpress.com", "aexp.com", "usbank.com", "pnc.com",
        "ally.com", "sofi.com", "schwab.com", "fidelity.com", "vanguard.com", "robinhood.com",
        "etrade.com", "hsbc.com", "hsbc.co.uk", "barclays.com", "barclays.co.uk", "santander.com",
        "revolut.com", "monzo.com", "wise.com", "n26.com", "chime.com", "venmo.com", "cash.app",
        "square.com", "zellepay.com", "coinbase.com", "kraken.com", "intuit.com", "turbotax.com",
        "irs.gov", "hmrc.gov.uk", "creditkarma.com", "experian.com", "equifax.com", "transunion.com",
        "synchrony.com", "navyfederal.org", "ing.com", "deutsche-bank.de", "sparkasse.de",
    )),
    "travel": (0.6, (
        "delta.com", "united.com", "aa.com", "southwest.com", "jetblue.com", "alaskaair.com",
        "aircanada.com", "westjet.com", "lufthansa.com", "ba.com", "britishairways.com",
        "airfrance.com", "klm.com", "ryanair.com", "easyjet.com", "emirates.com", "qatarairways.com",
        "booking.com", "expedia.com", "hotels.com", "airbnb.com", "vrbo.com", "marriott.com",
        "hilton.com", "hyatt.com", "ihg.com", "accor.com", "kayak.com", "priceline.com", "agoda.com",
        "trip.com", "hopper.com", "hertz.com", "avis.com", "enterprise.com", "sixt.com",
        "budget.com", "amtrak.com", "trainline.com", "eurostar.com", "flixbus.com", "tripit.com",
    )),
    "social": (0.8, (
        "facebookmail.com", "facebook.com", "linkedin.com", "twitter.com", "x.com", "instagram.com",
        "reddit.com", "redditmail.com", "discord.com", "discordapp.com", "tiktok.com",
        "pinterest.com", "nextdoor.com", "youtube.com", "quora.com", "meetup.com", "tumblr.com",
        "snapchat.com", "threads.net", "bsky.app", "strava.com", "goodreads.com", "twitch.tv",
    )),
    "notifications": (0.6, (
        "github.com", "gitlab.com", "bitbucket.org", "atlassian.net", "atlassian.com", "slack.com",
        "notion.so", "trello.com", "asana.com", "zoom.us", "dropbox.com", "box.com", "vercel.com",
        "netlify.com", "heroku.com", "sentry.io", "circleci.com", "figma.com", "docusign.net",
        "calendly.com", "1password.com", "lastpass.com", "bitwarden.com", "zapier.com",
        "monday.com", "clickup.com", "linear.app", "stripe.com",
    )),
    "newsletters": (0.7, (
        "substack.com", "beehiiv.com", "buttondown.email", "buttondown.com", "ghost.io",
        "convertkit.com", "kit.com", "medium.com", "revue.email", "mailerlite.com", "tinyletter.com",
        "morningbrew.com", "theskimm.com",
    )),
}

# Personal mailbox providers: a sender here is usually a person (unless the mail is automated).
PERSONAL_DOMAINS = (
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "msn.com",
    "yahoo.com", "ymail.com", "icloud.com", "me.com", "mac.com", "aol.com", "proton.me",
    "protonmail.com", "pm.me", "gmx.com", "gmx.de", "gmx.net", "web.de", "fastmail.com",
    "zoho.com", "yandex.com", "mail.com", "hey.com",
)

# Sender local parts (the bit before @) that hint at a category: (category, weight, words).
LOCAL_PARTS: tuple[tuple[str, float, tuple[str, ...]], ...] = (
    ("receipts", 0.45, ("order", "orders", "order-update", "order-updates", "auto-confirm", "receipt",
                        "receipts", "purchase", "purchases", "invoice", "invoices", "billing",
                        "shipment-tracking", "shipping", "shipment", "delivery", "tracking",
                        "transaction", "transactions", "store", "bestellung", "bestellungen",
                        "commande", "commandes", "pedidos", "facturas", "rechnung")),
    ("finance", 0.4, ("statements", "estatements", "estatement", "banking", "onlinebanking",
                      "payments", "card", "cards", "billing", "alerts", "alert")),
    ("travel", 0.4, ("reservations", "reservation", "booking", "bookings", "itinerary", "flights",
                     "checkin", "check-in")),
    ("promotions", 0.5, ("marketing", "promo", "promos", "promotions", "offers", "offer", "deals",
                         "sale", "sales", "store-news", "shop", "specials", "rewards", "email-marketing")),
    ("newsletters", 0.4, ("newsletter", "newsletters", "digest", "editor", "editors", "weekly",
                          "briefing")),
    ("newsletters", 0.2, ("news",)),    # shops use news@ for marketing too
    ("notifications", 0.35, ("notifications", "notification", "notify", "alerts", "alert", "updates",
                            "account", "accounts", "security", "calendar-notification",
                            "mailer-daemon", "postmaster", "automated", "system")),
    ("social", 0.5, ("friends", "community", "groups", "forum", "forums")),
)

# Sender local parts that mean "a machine sent this" (also see the bulk headers below).
ROBOT_LOCAL_PARTS = (
    "noreply", "no-reply", "no_reply", "donotreply", "do-not-reply", "do_not_reply", "mailer",
    "mailer-daemon", "postmaster", "bounce", "bounces", "notifications", "notification", "notify",
    "alerts", "alert", "news", "newsletter", "marketing", "updates",
    "auto-confirm", "order", "orders",
    "receipts", "billing", "statements", "offers", "deals", "promo", "store-news", "digest",
    "account", "accounts", "security", "reservations", "booking", "system", "automated",
    "transaction", "store", "shop", "bestellung", "commandes", "facturas",
    "jira", "confluence", "github", "gitlab", "calendar", "invitations",
)
# Role-looking local parts that real people use too: families write from mail@familyname.de,
# freelancers and small firms from info@, hello@ or team@. Alone they are weak evidence: they count
# as "machine-sent" only from a known company domain (the tables above); otherwise a bulk header
# (List-Unsubscribe, an email service, Auto-Submitted, ...) is needed before the mail counts as automated.
WEAK_ROLE_LOCAL_PARTS = (
    "info", "hello", "hi", "team", "support", "help", "service", "customerservice", "contact",
    "admin", "email", "mail", "reply", "feedback", "office", "enquiries", "inquiries",
)

# Words/phrases in the subject: (category, weight, phrases). Snippet text counts at half weight.
# A few common Spanish / German / French words are included for receipts and finance.
KEYWORDS: tuple[tuple[str, float, tuple[str, ...]], ...] = (
    ("receipts", 0.7, (
        "your order", "order confirmation", "order confirmed", "order #", "order number",
        "order no", "receipt", "your receipt", "invoice", "has shipped", "have shipped", "shipped",
        "out for delivery", "was delivered", "has been delivered", "delivered", "tracking number",
        "shipment", "your package", "thanks for your order", "thank you for your order",
        "thanks for your purchase", "thank you for your purchase", "purchase confirmation",
        "payment receipt", "refund", "return label", "dispatched", "on its way", "arriving",
        "scheduled for delivery", "delivery today", "your purchase",
        "pedido", "recibo", "factura", "tu compra", "bestellung", "rechnung", "quittung",
        "versandbestätigung", "lieferung", "commande", "facture", "reçu", "livraison", "expédié",
    )),
    ("receipts", 0.35, ("order", "purchase", "delivery", "package", "subscription renewed")),
    ("finance", 0.85, (
        "trade confirmation", "trade confirmed", "statement is ready", "statement is available",
        "direct deposit", "1099", "w-2", "tax form", "tax document", "payment is due", "payment due",
    )),
    ("finance", 0.65, (
        "statement", "your statement", "statement is ready", "statement is available", "your bill",
        "bill is ready", "bill is available", "payment due", "payment is due", "payment received",
        "payment scheduled", "payment posted", "autopay", "minimum payment", "balance",
        "low balance", "direct deposit", "deposit", "withdrawal", "transaction", "transfer",
        "wire", "credit score", "credit limit", "1099", "w-2", "tax document", "tax form",
        "tax return", "dividend", "trade confirmation", "trade confirmed", "portfolio", "401(k)",
        "ira", "brokerage", "account alert", "you received", "you sent", "sent you", "money request",
        "paid you", "you paid", "requested $", "zelle",
        "kontoauszug", "überweisung", "estado de cuenta", "transferencia", "relevé", "virement",
    )),
    ("finance", 0.3, ("payment", "account", "card", "bank", "tax", "loan", "mortgage")),
    ("travel", 0.85, (
        "boarding pass", "itinerary", "e-ticket", "eticket", "flight confirmation",
        "booking confirmation", "check in for your flight", "check-in for your flight",
    )),
    ("travel", 0.75, (
        "itinerary", "boarding pass", "check in for your flight", "check-in", "flight",
        "your trip", "trip to", "reservation", "booking confirmation", "booking confirmed",
        "your booking", "your stay", "hotel", "e-ticket", "eticket", "car rental", "rental car",
        "your rental", "gate change", "flight delay", "seat assignment", "confirmation number",
        "reserva", "buchungsbestätigung", "flug", "réservation",
    )),
    ("newsletters", 0.6, (
        "newsletter", "digest", "weekly", "this week in", "issue #", "issue no", "edition",
        "roundup", "round-up", "briefing", "monthly update", "the week", "dispatch", "recap",
        "top stories", "daily", "new post", "read more", "podcast", "episode",
    )),
    ("promotions", 0.7, (
        "% off", "percent off", "sale", "deal", "deals", "coupon", "promo code", "discount",
        "free shipping", "limited time", "limited-time", "ends tonight", "ends today",
        "last chance", "don't miss", "dont miss", "black friday", "cyber monday", "flash sale",
        "clearance", "save $", "save up to", "special offer", "exclusive offer", "offer ends",
        "buy one", "bogo", "new arrivals", "shop now", "just for you", "gift", "gifts", "rewards",
        "earn points", "double points", "cash back", "upgrade now", "% de descuento", "rebajas",
        "angebot", "rabatt", "soldes", "promotion",
    )),
    ("promotions", 0.35, ("offer", "save", "off", "new", "introducing", "free")),
    ("social", 0.6, (
        "neighborhood", "neighbourhood", "your network", "your profile", "connection request",
        "commented on", "replied to your", "mentioned you", "tagged you", "friend request",
        "wants to connect", "invitation to connect", "new follower", "followed you",
        "liked your", "reacted to", "shared a post", "new message from", "sent you a message",
        "endorsed you", "people you may know", "birthday", "community", "new reply", "forum",
    )),
    ("notifications", 0.55, (
        "[jira]", "jira", "[github]", "[gitlab]", "ci build", "workflow run", "release",
        "notification", "reminder", "your account", "account update", "has been updated",
        "was updated", "has been changed", "was changed", "pull request", "merge request",
        "build failed", "build succeeded", "deployment", "pipeline", "invited you", "shared with you",
        "welcome to", "confirm your", "verify your email", "terms of service", "privacy policy",
        "subscription", "your plan", "trial", "renewal", "accepted:", "declined:", "tentative:",
        "canceled event", "cancelled event", "storage", "backup", "status update",
    )),
)

# Security messages always stay in the Inbox (they are usually urgent and personal).
SECURITY_PHRASES = (
    "verification code", "security code", "one-time code", "one time code", "one-time password",
    "one-time passcode", "passcode", "otp", "2fa", "two-factor", "2-step", "two-step",
    "login code", "log-in code", "sign-in code", "signin code", "authentication code",
    "confirmation code", "is your code", "your code is", "password reset", "reset your password",
    "reset password", "password change", "password was changed", "password has been changed",
    "new sign-in", "new sign in", "new login", "new log-in", "unusual sign-in", "unusual sign in",
    "unusual activity", "suspicious activity", "suspicious sign", "security alert",
    "sign-in attempt", "login attempt", "new device", "unrecognized device", "account locked",
    "verify it's you", "verify it’s you", "is this you", "was this you", "código de verificación",
    "sicherheitscode", "bestätigungscode", "code de vérification", "mot de passe",
)
# "Verify your email address" / "confirm your account" mail stays in the Inbox too: the person just
# signed up somewhere and is waiting for it, and the link usually expires.
ACCOUNT_CONFIRM_PHRASES = (
    "verify your email", "verify your e-mail", "verify your email address", "verify your account",
    "verify email address", "verify your address", "confirm your email", "confirm your e-mail",
    "confirm your email address", "confirm your account", "confirm your registration",
    "confirm your sign-up", "confirm your signup", "confirm your subscription",
    "activate your account", "email verification", "e-mail verification", "email confirmation",
    "confirm email address", "e-mail-adresse bestätigen", "bestätigen sie ihre e-mail",
    "konto aktivieren", "confirmez votre adresse", "confirma tu correo", "verifica tu correo",
)
_SECURITY_CODE_IN_SUBJECT = re.compile(r"(?<!\d)\d{4,8}(?!\d).{0,40}\bcode\b|\bcode\b.{0,40}(?<!\d)\d{4,8}(?!\d)",
                                       re.IGNORECASE)

# Calendar invitations stay in the Inbox (someone wants an answer). Replies to invitations do not.
INVITE_SUBJECT_PREFIXES = ("invitation:", "updated invitation:", "invitation updated:", "einladung:",
                           "invitación:", "invitation :")

# Bulk-mail service (ESP) headers. Marketing ESPs hint at promotions; the others only say "automated".
MARKETING_ESP_HEADERS = ("x-klaviyo", "x-kmail", "x-braze", "x-iterable", "x-attentive",
                         "x-campaign", "x-campaignid", "x-marketo",
                         "x-hubspot", "x-sfmc", "x-exacttarget", "x-cm-campaign", "x-emarsys",
                         "x-listrak", "x-omnisend", "x-sailthru", "x-cheetah", "x-responsys")
# Mailchimp (X-MC-User) carries both newsletters and marketing, so it only says "bulk".
ESP_HEADERS = MARKETING_ESP_HEADERS + ("x-mc-user", "x-mailchimp", "x-mailgun","x-sg-eid", "x-sg-id", "x-sendgrid",
                                       "x-ses-outgoing", "feedback-id", "x-pm-message-id",
                                       "x-postmark", "x-mandrill", "x-mailjet", "x-sparkpost",
                                       "x-msys-api", "x-mailer-sid", "x-csa-complaints", "x-shopify")
# Dev-tool and forum headers.
NOTIFICATION_HEADERS = ("x-github-reason", "x-github-sender", "x-gitlab-project", "x-jira-fingerprint",
                        "x-bitbucket", "x-sentry")
SOCIAL_HEADERS = ("x-discourse-post-id", "x-discourse-topic-id", "x-facebook-notify",
                  "x-linkedin-class", "x-linkedin-template")

# Gmail category labels (when the server exposes them) -> our category.
GMAIL_CATEGORIES = {
    "promotions": "promotions", "social": "social", "updates": "notifications",
    "forums": "social", "purchases": "receipts", "travel": "travel", "finance": "finance",
}

# Tuning knobs.
RUNNER_UP_PENALTY = 0.25      # how much a close second-best category lowers confidence
AUTOMATED_BOOST = 0.3         # extra evidence for the top category when the mail is machine-sent
MAX_CONFIDENCE = 0.97
SNIPPET_FACTOR = 0.5          # snippet keywords count less than subject keywords


# --------------------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Signal:
    category: str
    weight: float
    reason: str


def _phrase_regex(phrase: str) -> re.Pattern[str]:
    start = r"(?<!\w)" if phrase[:1].isalnum() else ""
    end = r"(?!\w)" if phrase[-1:].isalnum() else ""
    return re.compile(start + re.escape(phrase.lower()) + end)


_KEYWORD_PATTERNS = [(cat, w, [(p, _phrase_regex(p)) for p in phrases]) for cat, w, phrases in KEYWORDS]
_SECURITY_PATTERNS = [(p, _phrase_regex(p)) for p in SECURITY_PHRASES]
_CONFIRM_PATTERNS = [(p, _phrase_regex(p)) for p in ACCOUNT_CONFIRM_PHRASES]
_ALL_DOMAINS = tuple(d for _, domains in DOMAINS.values() for d in domains)


def domain_in(domain: str, domains: Iterable[str]) -> str | None:
    """The table entry `domain` belongs to (itself or a parent domain), or None."""
    domain = domain.lower()
    for d in domains:
        if domain == d or domain.endswith("." + d):
            return d
    return None


def list_id_of(msg: Message) -> str:
    """The List-Id value without decoration: "<news.example.com>" -> "news.example.com"."""
    raw = msg.headers.get("list-id", "").strip().lower()
    m = re.search(r"<([^>]+)>", raw)
    return (m.group(1) if m else raw).strip()


def _local_base(local: str) -> str:
    """'noreply+abc' -> 'noreply'; 'order-update.123' stays."""
    return local.lower().split("+", 1)[0]


def _local_matches(local: str, words: Iterable[str]) -> str | None:
    base = _local_base(local)
    parts = re.split(r"[._\-]", base)
    for w in words:
        if base == w or w in parts or base.startswith((w + "-", w + ".")):
            return w
    return None


def bulk_markers(msg: Message) -> list[str]:
    """Header-only reasons the message was sent in bulk or by a machine (mailing list, ESP, app)."""
    h = msg.headers
    found = []
    if "list-unsubscribe" in h:
        found.append("has an unsubscribe header")
    if "list-id" in h:
        found.append("is sent to a mailing list")
    if h.get("precedence", "").strip().lower() in ("bulk", "list", "junk"):
        found.append("is marked as bulk mail")
    auto = h.get("auto-submitted", "").strip().lower()
    if auto and auto != "no":
        found.append("is marked as automatically sent")
    if any(k == e or k.startswith(e) for k in h for e in ESP_HEADERS):
        found.append("was sent by a bulk-email service")
    if any(k in h for k in NOTIFICATION_HEADERS + SOCIAL_HEADERS):
        found.append("was sent by an app")
    return found


def automation_markers(msg: Message) -> list[str]:
    """Plain-English reasons the message looks machine-sent (empty = looks written by a person):
    the bulk headers above plus a robot sender address such as noreply@, orders@ or billing@.
    A role address that people use too (info@, mail@, team@, ...) counts only from a known company."""
    found = bulk_markers(msg)
    local = msg.from_.local
    if _local_matches(local, ROBOT_LOCAL_PARTS) or "noreply" in local.lower():
        found.append(f"comes from a robot address ({local}@)")
    elif _local_matches(local, WEAK_ROLE_LOCAL_PARTS):
        company = domain_in(msg.from_.domain, _ALL_DOMAINS)
        if company:
            found.append(f"comes from a role address ({local}@) at {company}")
    return found


def looks_automated(msg: Message) -> bool:
    """True when the message has mailing-list, bulk or robot-sender markers."""
    return bool(automation_markers(msg))


def looks_personal(msg: Message) -> bool:
    """True when nothing marks the message as machine-sent, so it may well be written by a person."""
    return not automation_markers(msg)


def is_security_message(msg: Message) -> str | None:
    """The phrase that makes this a security message (codes, password resets, sign-in alerts)."""
    subject = msg.subject.lower()
    for phrase, rx in _SECURITY_PATTERNS:
        if rx.search(subject):
            return phrase
    if _SECURITY_CODE_IN_SUBJECT.search(subject):
        return "a login code"
    snippet = msg.snippet.lower()[:300]
    if snippet and not msg.headers.get("list-unsubscribe"):
        for phrase, rx in _SECURITY_PATTERNS:
            if phrase in ("otp", "passcode", "2fa", "mot de passe"):
                continue
            if rx.search(snippet):
                return phrase
    return None


def is_account_confirmation(msg: Message) -> str | None:
    """The phrase that makes this a "verify your email / confirm your account" message."""
    subject = msg.subject.lower()
    for phrase, rx in _CONFIRM_PATTERNS:
        if rx.search(subject):
            return phrase
    snippet = msg.snippet.lower()[:300]
    if snippet and not msg.headers.get("list-unsubscribe"):
        for phrase, rx in _CONFIRM_PATTERNS:
            if rx.search(snippet):
                return phrase
    return None


def is_calendar_invite(msg: Message) -> bool:
    """A meeting invitation that asks for an answer (not an accepted/declined reply)."""
    subject = msg.subject.strip().lower()
    if subject.startswith(INVITE_SUBJECT_PREFIXES):
        return True
    ctype = msg.headers.get("content-type", "").lower()
    if "text/calendar" in ctype and "method=reply" not in ctype:
        return not subject.startswith(("accepted:", "declined:", "tentative:", "tentatively accepted:"))
    return False


def _gmail_category(msg: Message) -> str | None:
    for label in msg.gmail_labels:
        norm = re.sub(r"[^a-z]", "", label.lower())
        if norm.startswith("category"):
            return GMAIL_CATEGORIES.get(norm[len("category"):])
    return None


# --------------------------------------------------------------------------------------------------
# Signals and scoring
# --------------------------------------------------------------------------------------------------

def collect_signals(msg: Message, own_addresses: Iterable[str] = ()) -> list[Signal]:
    """Every clue about this message, as (category, weight, reason) signals."""
    sigs: list[Signal] = []
    h = msg.headers
    sender = msg.from_
    domain = sender.domain
    markers = automation_markers(msg)

    # Known domains.
    for cat, (weight, domains) in DOMAINS.items():
        hit = domain_in(domain, domains)
        if hit:
            sigs.append(Signal(cat, weight, f"sender is {hit}"))
            if cat == "receipts":
                # Shops send sales as well as receipts: a shop's domain must not argue against "promotions".
                sigs.append(Signal("promotions", 0.1, f"sender is {hit}"))

    # Sender local part.
    for cat, weight, words in LOCAL_PARTS:
        hit = _local_matches(sender.local, words)
        if hit:
            sigs.append(Signal(cat, weight, f"sender address starts with {hit}@"))
    if "noreply" in sender.local.lower() or "no-reply" in sender.local.lower() \
            or "donotreply" in sender.local.lower() or "do-not-reply" in sender.local.lower():
        sigs.append(Signal("notifications", 0.3, "sent from a no-reply address"))

    # Subject and snippet keywords (one signal per keyword group, the first phrase found).
    subject = msg.subject.lower()
    snippet = msg.snippet.lower()[:500]
    for cat, weight, patterns in _KEYWORD_PATTERNS:
        for phrase, rx in patterns:
            if rx.search(subject):
                sigs.append(Signal(cat, weight, f'subject mentions "{phrase}"'))
                break
        else:
            if snippet:
                for phrase, rx in patterns:
                    if rx.search(snippet):
                        sigs.append(Signal(cat, weight * SNIPPET_FACTOR, f'text mentions "{phrase}"'))
                        break

    # Headers.
    if "list-id" in h:
        sigs.append(Signal("newsletters", 0.35, "sent to a mailing list (List-Id)"))
    if "list-unsubscribe" in h:
        sigs.append(Signal("newsletters", 0.25, "has an unsubscribe link"))
        sigs.append(Signal("promotions", 0.3, "has an unsubscribe link"))
    if any(k.startswith(e) for k in h for e in MARKETING_ESP_HEADERS):
        sigs.append(Signal("promotions", 0.45, "sent by a marketing email service"))
    if h.get("precedence", "").strip().lower() == "bulk":
        sigs.append(Signal("promotions", 0.15, "marked as bulk mail"))
    auto = h.get("auto-submitted", "").strip().lower()
    if auto and auto != "no":
        sigs.append(Signal("notifications", 0.45, "marked as automatically sent"))
    if any(k in h for k in NOTIFICATION_HEADERS):
        sigs.append(Signal("notifications", 0.9, "sent by a developer tool"))
    if any(k in h for k in SOCIAL_HEADERS) or "discourse" in h.get("x-mailer", "").lower():
        sigs.append(Signal("social", 0.85, "sent by a forum or social network"))
    if "unsubscribe" in snippet:
        sigs.append(Signal("promotions", 0.15, "text has an unsubscribe link"))
        sigs.append(Signal("newsletters", 0.15, "text has an unsubscribe link"))

    # Gmail's own category label.
    gcat = _gmail_category(msg)
    if gcat:
        sigs.append(Signal(gcat, 0.7, f"Gmail files it under {gcat.title()}"))

    # Looks like a person wrote it. A reply counts even from a role address such as support@
    # (a company's support agent answering you), as long as it has no bulk headers.
    if not bulk_markers(msg) and is_reply_subject_or_header(msg):
        sigs.append(Signal(INBOX, 0.8, "it is a reply to a conversation"))
    company = any(domain_in(domain, domains) for _, domains in DOMAINS.values())
    if not markers and not company:
        sigs.append(Signal(INBOX, 0.55, "no mailing-list or robot markers; looks written by a person"))
        if domain_in(domain, PERSONAL_DOMAINS):
            sigs.append(Signal(INBOX, 0.45, f"sent from a personal {domain} address"))
        own = {a.lower() for a in own_addresses}
        if own and any(a.email in own for a in msg.to):
            sigs.append(Signal(INBOX, 0.2, "addressed to you directly"))
    return sigs


def is_reply_subject_or_header(msg: Message) -> bool:
    """In-Reply-To/References header, or a "Re:" style subject."""
    return msg.is_reply or msg.subject.strip().lower().startswith(("re:", "aw:", "sv:", "rv:", "réf:"))


def _combine(weights: list[float]) -> float:
    miss = 1.0
    for w in weights:
        miss *= 1.0 - max(0.0, min(w, 0.99))
    return 1.0 - miss


def score(sigs: list[Signal]) -> list[tuple[str, float, list[Signal]]]:
    """Categories ranked by combined evidence, strongest first."""
    by_cat: dict[str, list[Signal]] = {}
    for s in sigs:
        by_cat.setdefault(s.category, []).append(s)
    ranked = [(cat, _combine([s.weight for s in ss]), sorted(ss, key=lambda s: -s.weight))
              for cat, ss in by_cat.items()]
    ranked.sort(key=lambda t: -t[1])
    return ranked


def heuristic_decision(msg: Message, *, own_addresses: Iterable[str] = ()) -> Decision | None:
    """Best built-in guess for `msg`, or None when there is no clue at all.

    `own_addresses` (optional) are the user's own addresses, used to spot mail sent to them directly.
    """
    phrase = is_security_message(msg)
    if phrase:
        return Decision(INBOX, 0.95, "heuristic", f'Security message (mentions "{phrase}"): kept in the Inbox')
    phrase = is_account_confirmation(msg)
    if phrase:
        return Decision(INBOX, 0.95, "heuristic",
                        f'Account confirmation (mentions "{phrase}"): kept in the Inbox so you can finish it')
    if is_calendar_invite(msg):
        return Decision(INBOX, 0.9, "heuristic", "Calendar invitation: kept in the Inbox so you can answer")

    ranked = score(collect_signals(msg, own_addresses))
    if not ranked:
        return None
    cat, best, sigs = ranked[0]
    if cat != INBOX and automation_markers(msg):
        # Machine-sent mail is meant for *some* folder, so being automated backs up the top guess.
        best = _combine([best, AUTOMATED_BOOST])
    # Evidence that also supports the winner (e.g. an unsubscribe link counts for both newsletters
    # and promotions) is not disagreement, so it is left out of the runner-up's score.
    shared = {s.reason for s in sigs}
    second = max((_combine([s.weight for s in other if s.reason not in shared])
                  for _, _, other in ranked[1:]), default=0.0)
    confidence = max(0.0, min(MAX_CONFIDENCE, best - RUNNER_UP_PENALTY * second))
    reasons = []
    for s in sigs:
        if s.reason not in reasons:
            reasons.append(s.reason)
    reason = "; ".join(reasons[:2])
    label = "Inbox" if cat == INBOX else cat.title()
    return Decision(cat, round(confidence, 3), "heuristic", f"{label}: {reason}")
