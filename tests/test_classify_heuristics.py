"""Table-driven tests for the built-in heuristics.

Each row is a realistic message and where it should end up after the confidence threshold
(0.75): a category key, or "inbox" when it must stay (people, security, invites) or when the
heuristics should be unsure. Brand domains are used only as company senders; people use
example.com or obviously made-up addresses.
"""

from __future__ import annotations

import pytest

from mailtidy.classify.heuristics import (
    collect_signals,
    heuristic_decision,
    is_calendar_invite,
    is_security_message,
    list_id_of,
    looks_automated,
)
from mailtidy.models import INBOX
from mailtidy.testing import make_message

THRESHOLD = 0.75
UNSUB = {"List-Unsubscribe": "<https://example.com/unsubscribe/abc>, <mailto:u@example.com>"}
LIST = {"List-Id": "Example list <list.example.com>"}
REPLY = {"In-Reply-To": "<abc@example.com>", "References": "<abc@example.com>"}
KLAVIYO = {"X-Klaviyo-Message-Id": "123", **UNSUB}
MAILCHIMP = {"X-MC-User": "abc123", **UNSUB, **LIST}
AUTO = {"Auto-Submitted": "auto-generated"}
GITHUB = {"X-GitHub-Reason": "mention", "List-Id": "org/repo <repo.org.github.com>", **UNSUB}


def m(sender, subject, *, name="", headers=None, snippet="", labels=()):
    msg = make_message(sender, subject, name=name, headers=headers, snippet=snippet)
    if labels:
        msg.gmail_labels = frozenset(labels)
    return msg


CASES = [
    # ---- people and things that must stay in the Inbox ----------------------------------------
    ("friend on gmail", m("test.friend.mailtidy@gmail.com", "Dinner on Saturday?", name="Alex"), INBOX),
    ("friend mentions order", m("test.friend.mailtidy@gmail.com", "Did you get my order of cookies?"), INBOX),
    ("coworker", m("dana@example.com", "Q3 planning doc", name="Dana Smith"), INBOX),
    ("support agent reply", m("support@shop.example.com", "Re: Your ticket #4412", headers=REPLY), INBOX),
    ("person reply", m("sam@example.org", "Re: photos from the trip", headers=REPLY), INBOX),
    ("outlook person", m("test.person.mailtidy@outlook.com", "Quick question"), INBOX),
    ("spanish person", m("test.amigo.mailtidy@yahoo.com", "Hola, ¿qué tal?"), INBOX),
    ("2fa code", m("no-reply@accounts.example.com", "Your verification code is 482913", headers=UNSUB), INBOX),
    ("login code digits", m("security@mail.instagram.com", "123456 is your Instagram login code"), INBOX),
    ("password reset", m("noreply@github.com", "[GitHub] Please reset your password"), INBOX),
    ("new sign-in", m("no-reply@accounts.google.com", "Security alert: New sign-in from Windows"), INBOX),
    ("bank new device", m("alerts@chase.com", "We noticed a new device on your account"), INBOX),
    ("code in snippet", m("no-reply@app.example.com", "Your sign in request",
                          snippet="Use this one-time code to sign in: 551204"), INBOX),
    ("calendar invite", m("calendar-notification@google.com", "Invitation: Team sync @ Wed Oct 29, 10am"), INBOX),
    ("ics invite", m("lee@example.com", "Project kickoff",
                     headers={"Content-Type": "text/calendar; method=REQUEST"}), INBOX),
    ("unknown robot", m("noreply@unknown.example.com", "Hello"), INBOX),

    # ---- receipts -----------------------------------------------------------------------------
    ("amazon order", m("auto-confirm@amazon.com", "Your Amazon.com order #112-5551234 has been confirmed"),
     "receipts"),
    ("amazon shipped", m("shipment-tracking@amazon.com", "Your package has shipped"), "receipts"),
    ("ebay order", m("ebay@ebay.com", "Order confirmed: Vintage desk lamp"), "receipts"),
    ("etsy purchase", m("transaction@etsy.com", "Your Etsy purchase from ExampleShop"), "receipts"),
    ("ups delivery", m("mcinfo@ups.com", "UPS Update: Package Scheduled for Delivery Today"), "receipts"),
    ("fedex delivered", m("trackingupdates@fedex.com", "FedEx Shipment 7712: Delivered"), "receipts"),
    ("shopify store", m("store@shop.example.com", "Order #1001 confirmed", headers={"X-Shopify-Shop": "1"}),
     "receipts"),
    ("paypal receipt", m("service@paypal.com", "Receipt for your payment to Example Store"), "receipts"),
    ("german order", m("bestellung@shop.example.de", "Ihre Bestellung 4711 ist eingegangen"), "receipts"),
    ("french shipped", m("commandes@boutique.example.fr", "Votre commande a été expédiée"), "receipts"),
    ("saas invoice", m("billing@saas.example.com", "Your invoice for March"), "receipts"),
    ("apple receipt", m("no_reply@email.apple.com", "Your receipt from Apple."), "receipts"),
    ("food order", m("no-reply@doordash.com", "Your order from Pizza Place is on its way"), "receipts"),
    ("spanish invoice", m("facturas@tienda.example.es", "Tu factura de octubre"), "receipts"),

    # ---- promotions (incl. shop and bank marketing) -------------------------------------------
    ("amazon deals", m("store-news@amazon.com", "Deals we think you'll love: up to 40% off", headers=UNSUB),
     "promotions"),
    ("etsy gifts", m("email@etsy.com", "Gifts for everyone on your list", headers=KLAVIYO), "promotions"),
    ("flash sale", m("hello@brand.example.com", "Flash sale: 50% off everything", headers=KLAVIYO),
     "promotions"),
    ("coupon", m("news@store.example.com", "Last chance! Your coupon expires tonight", headers=UNSUB),
     "promotions"),
    ("black friday", m("news@store.example.com", "Black Friday starts now", headers=UNSUB), "promotions"),
    ("free shipping", m("marketing@shop.example.com", "Free shipping on your next order", headers=UNSUB),
     "promotions"),
    ("new arrivals", m("hello@fashion.example.com", "New arrivals, just for you", headers=MAILCHIMP),
     "promotions"),
    ("bank marketing", m("offers@chase.com", "Earn 5% cash back with our new card - limited time offer",
                         headers=UNSUB), "promotions"),
    ("travel promo", m("deals@expedia.com", "Save up to 30% on summer getaways", headers=UNSUB),
     "promotions"),
    ("gmail promo label", m("info@brand.example.com", "Something special inside", headers=UNSUB,
                            labels={"CATEGORY_PROMOTIONS"}), "promotions"),
    ("german sale", m("newsletter@shop.example.de", "Nur heute: 20% Rabatt", headers=UNSUB), "promotions"),

    # ---- finance ------------------------------------------------------------------------------
    ("bank statement", m("alerts@chase.com", "Your statement is ready"), "finance"),
    ("card payment due", m("capitalone@notification.capitalone.com", "Your payment is due in 3 days"),
     "finance"),
    ("venmo", m("venmo@venmo.com", "Alex paid you $25.00"), "finance"),
    ("zelle", m("alerts@bank.example.com", "You received $100.00 with Zelle"), "finance"),
    ("direct deposit", m("no-reply@alertsp.chase.com", "Your direct deposit has posted"), "finance"),
    ("tax form", m("fidelity.investments@mail.fidelity.com", "Your 1099 tax form is available"), "finance"),
    ("trade", m("noreply@robinhood.com", "Trade confirmation for your order"), "finance"),
    ("utility bill", m("billing@power.example.com", "Your bill is ready to view"), "finance"),
    ("low balance", m("alerts@bank.example.com", "Low balance alert"), "finance"),

    # ---- travel -------------------------------------------------------------------------------
    ("flight", m("deltaairlines@t.delta.com", "Your flight confirmation - ATL to JFK"), "travel"),
    ("booking.com", m("noreply@booking.com", "Your booking is confirmed at Hotel Example"), "travel"),
    ("airbnb", m("automated@airbnb.com", "Reservation confirmed for Lisbon"), "travel"),
    ("check in", m("unitedairlines@united.com", "Check in for your flight to Denver"), "travel"),
    ("car rental", m("reservations@hertz.com", "Your Hertz rental car reservation"), "travel"),
    ("itinerary", m("plans@tripit.com", "Itinerary for your trip to Rome"), "travel"),
    ("boarding pass", m("noreply@airline.example.com", "Your boarding pass for flight EX123"), "travel"),

    # ---- newsletters --------------------------------------------------------------------------
    ("person's substack", m("janesnotes@substack.com", "Issue #42: On gardening", name="Jane's Notes",
                            headers={**UNSUB, **LIST}), "newsletters"),
    ("weekly roundup", m("roundup@substack.com", "The Weekly Roundup #12", headers={**UNSUB, **LIST}),
     "newsletters"),
    ("beehiiv", m("ai-weekly@mail.beehiiv.com", "This week in AI", headers=UNSUB), "newsletters"),
    ("medium digest", m("noreply@medium.com", "Medium Daily Digest", headers=UNSUB), "newsletters"),
    ("club newsletter", m("news@club.example.org", "October Newsletter", headers=MAILCHIMP), "newsletters"),
    ("ghost blog", m("blog@writer.ghost.io", "New post: Building a shed", headers={**UNSUB, **LIST}),
     "newsletters"),
    ("snippet newsletter", m("editor@digest.example.com", "Five things worth reading",
                             headers={**UNSUB, **LIST}, snippet="This week's newsletter: top stories"),
     "newsletters"),

    # ---- social -------------------------------------------------------------------------------
    ("facebook", m("notification@facebookmail.com", "Sam commented on your post"), "social"),
    ("linkedin", m("invitations@linkedin.com", "Pat wants to connect"), "social"),
    ("reddit", m("noreply@redditmail.com", "u/someone replied to your comment"), "social"),
    ("discord", m("notifications@discord.com", "New message from a friend"), "social"),
    ("forum", m("forum@community.example.org", "[Example Forum] New reply in 'Tips'",
                headers={"X-Discourse-Post-Id": "77", **UNSUB, **LIST}), "social"),
    ("nextdoor", m("reply@rs.email.nextdoor.com", "New posts in your neighborhood"), "social"),
    ("youtube", m("noreply@youtube.com", "Someone commented on your video"), "social"),

    # ---- notifications ------------------------------------------------------------------------
    ("github", m("notifications@github.com", "[org/repo] Fix crash on startup (#12)", headers=GITHUB),
     "notifications"),
    ("gitlab", m("gitlab@gitlab.com", "Pipeline failed for main"), "notifications"),
    ("slack", m("feedback@slack.com", "Notification from the Example workspace"), "notifications"),
    ("dropbox", m("no-reply@dropbox.com", "Alex shared a folder with you"), "notifications"),
    ("calendar accepted", m("calendar-notification@google.com", "Accepted: Team sync @ Wed"),
     "notifications"),
    ("renewal", m("no-reply@service.example.com", "Your subscription will renew soon", headers=AUTO),
     "notifications"),
    ("jira", m("jira@example.atlassian.net", "[JIRA] (PROJ-12) Update the docs"), "notifications"),
    ("account update", m("no-reply@app.example.com", "Your account settings were changed", headers=AUTO),
     "notifications"),
    ("gmail updates label", m("no-reply@tool.example.com", "Weekly usage report",
                              labels={"\\Category/Updates"}, headers=AUTO), "notifications"),
]


def final_category(msg) -> str:
    d = heuristic_decision(msg)
    if d is None or d.confidence < THRESHOLD:
        return INBOX
    return d.category


@pytest.mark.parametrize("label,msg,expected", CASES, ids=[c[0] for c in CASES])
def test_table(label, msg, expected):
    d = heuristic_decision(msg)
    assert final_category(msg) == expected, f"{label}: got {d}"


def test_table_size_and_accuracy(capsys):
    assert len(CASES) >= 60
    hits = sum(final_category(msg) == expected for _, msg, expected in CASES)
    accuracy = hits / len(CASES)
    with capsys.disabled():
        print(f"\nheuristics accuracy on table: {hits}/{len(CASES)} = {accuracy:.0%}")
    assert accuracy >= 0.95


def test_every_category_covered():
    expected = {c[2] for c in CASES}
    assert expected >= {INBOX, "receipts", "finance", "travel", "newsletters", "promotions", "social",
                        "notifications"}


def test_every_decision_has_reason_and_source():
    for _, msg, _ in CASES:
        d = heuristic_decision(msg)
        assert d is not None
        assert d.source == "heuristic"
        assert d.reason and len(d.reason) < 200
        assert 0.0 <= d.confidence <= 1.0


def test_security_and_invite_helpers():
    assert is_security_message(m("x@example.com", "Your one-time passcode"))
    assert not is_security_message(m("x@example.com", "Lunch tomorrow?"))
    assert is_calendar_invite(m("x@example.com", "Invitation: Sync"))
    assert not is_calendar_invite(m("x@example.com", "Accepted: Sync",
                                    headers={"Content-Type": "text/calendar; method=REPLY"}))


def test_looks_automated():
    assert looks_automated(m("noreply@example.com", "Hi"))
    assert looks_automated(m("a@example.com", "Hi", headers=UNSUB))
    assert not looks_automated(m("alex@example.com", "Hi"))


def test_list_id_of():
    assert list_id_of(m("a@example.com", "x", headers=LIST)) == "list.example.com"
    assert list_id_of(m("a@example.com", "x", headers={"List-Id": "plain.example.com"})) == "plain.example.com"
    assert list_id_of(m("a@example.com", "x")) == ""


def test_subdomain_matches_known_domain():
    sigs = collect_signals(m("x@email.amazon.com", "hello"))
    assert any(s.category == "receipts" and "amazon.com" in s.reason for s in sigs)


def test_word_boundaries():
    # "sale" inside "wholesale" or "deal" inside "ideal" must not count as promotions keywords
    sigs = collect_signals(m("a@example.com", "Ideal wholesale partners"))
    assert not any(s.category == "promotions" and "subject" in s.reason for s in sigs)


def test_no_signals_returns_none_or_inbox():
    d = heuristic_decision(m("alex@example.com", "Hi"))
    assert d is not None and d.category == INBOX


# Written after tuning and never tuned against: an honest check that the tables generalize.
UNSEEN = [
    ("u friend icloud", m("test.pal.mailtidy@icloud.com", "Are you free this weekend?"), INBOX),
    ("u walmart order", m("help@walmart.com", "Thanks for your order, Sam"), "receipts"),
    ("u dhl", m("noreply@dhl.com", "Your DHL shipment is on its way"), "receipts"),
    ("u amex statement", m("americanexpress@welcome.aexp.com", "Your statement is available"), "finance"),
    ("u schwab", m("donotreply@schwab.com", "Your account balance update"), "finance"),
    ("u hilton", m("reservations@hilton.com", "Your stay at Hilton Example: reservation confirmed"), "travel"),
    ("u ryanair", m("noreply@ryanair.com", "Your Ryanair booking: travel itinerary"), "travel"),
    ("u pinterest", m("recommendations@inspire.pinterest.com", "Pins you might love", headers=UNSUB), "social"),
    ("u substack writer", m("thewriter@substack.com", "New post: Notes from the road",
                            headers={**UNSUB, **LIST}), "newsletters"),
    ("u store promo", m("deals@outdoorgear.example.com", "Up to 60% off tents this weekend",
                        headers=KLAVIYO), "promotions"),
    ("u zoom", m("no-reply@zoom.us", "Cloud recording is now available"), "notifications"),
    ("u verify email", m("no-reply@app.example.com", "Please verify your email address"), INBOX),
]


def test_unseen_accuracy(capsys):
    hits = [label for label, msg, expected in UNSEEN if final_category(msg) == expected]
    misses = [(label, heuristic_decision(msg)) for label, msg, expected in UNSEEN if label not in hits]
    with capsys.disabled():
        print(f"\nheuristics accuracy on unseen set: {len(hits)}/{len(UNSEEN)}")
        for label, d in misses:
            print(f"  miss {label}: {d}")
    assert len(hits) / len(UNSEEN) >= 0.75
