"""Table-driven tests for the built-in heuristics.

Each row is a realistic message and where it should end up after the confidence threshold
(0.75): a category key, or "inbox" when it must stay (people, security, invites) or when the
heuristics should be unsure. Brand domains are used only as company senders; people use
example.com or obviously made-up addresses.
"""

from __future__ import annotations

import pytest

from tidyinbox.classify.heuristics import (
    automation_markers,
    collect_signals,
    heuristic_decision,
    is_account_confirmation,
    is_calendar_invite,
    is_security_message,
    list_id_of,
    looks_automated,
    looks_personal,
)
from tidyinbox.models import INBOX
from tidyinbox.testing import make_message

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
    ("friend on gmail", m("user@gmail.com", "Dinner on Saturday?", name="Alex"), INBOX),
    ("friend mentions order", m("user@gmail.com", "Did you get my order of cookies?"), INBOX),
    ("coworker", m("dana@example.com", "Q3 planning doc", name="Dana Smith"), INBOX),
    ("support agent reply", m("support@shop.example.com", "Re: Your ticket #4412", headers=REPLY), INBOX),
    ("person reply", m("sam@example.org", "Re: photos from the trip", headers=REPLY), INBOX),
    ("outlook person", m("user@outlook.com", "Quick question"), INBOX),
    ("spanish person", m("user@yahoo.com", "Hola, ¿qué tal?"), INBOX),
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
    # account confirmations are time-sensitive, like security codes
    ("verify email", m("no-reply@app.example.com", "Please verify your email address"), INBOX),
    ("confirm account", m("noreply@service.example.com", "Confirm your account to get started",
                          headers=AUTO), INBOX),
    ("activate account", m("accounts@shop.example.com", "Activate your account"), INBOX),
    ("verify in snippet", m("no-reply@forum.example.org", "Welcome to Example Forum",
                            snippet="Click the link below to verify your email address."), INBOX),
    ("verify from big site", m("noreply@github.com", "[GitHub] Please verify your email address"), INBOX),

    # ---- real people writing from role-looking addresses (family, freelancer, small firm) -----
    # A role address alone is weak evidence: it must not cancel the "looks like a person" signals.
    ("family mail@", m("mail@mueller-family.example", "Sale of the house - documents attached"), INBOX),
    ("startup team@", m("team@ourstartup.example", "Board meeting deal terms"), INBOX),
    ("plumber info@", m("info@smithplumbing.example", "Your quote for the bathroom"), INBOX),
    ("designer hello@", m("hello@janedoe-design.example", "New logo drafts for you"), INBOX),
    ("photographer contact@", m("contact@lisa-photo.example", "Your wedding photos: 20% off prints"), INBOX),
    ("family email@", m("email@thejohnsons.example", "Gift ideas for Grandma?"), INBOX),
    ("club admin@", m("admin@riverside-choir.example", "Free tickets for the concert"), INBOX),
    ("german family", m("mail@familie-schmidt.example", "Angebot für das Haus"), INBOX),
    ("freelancer support@", m("support@itfixer.example", "Special offer on your laptop repair"), INBOX),

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


@pytest.mark.parametrize("local", ["noreply", "no-reply", "donotreply", "notifications", "billing",
                                   "receipts", "orders", "newsletter", "marketing", "alerts"])
def test_robot_local_parts_stay_strong(local):
    assert automation_markers(m(f"{local}@smallfirm.example", "Hi"))


@pytest.mark.parametrize("local", ["mail", "info", "hello", "team", "contact", "admin", "email",
                                   "support", "help", "service", "reply", "feedback"])
def test_role_local_part_alone_is_weak(local):
    msg = m(f"{local}@family.example", "Hi")
    assert automation_markers(msg) == []
    assert looks_personal(msg)
    sigs = collect_signals(msg)
    assert any(s.category == INBOX and "written by a person" in s.reason for s in sigs)


@pytest.mark.parametrize("headers", [UNSUB, LIST, AUTO, KLAVIYO, {"Precedence": "bulk"}])
def test_role_local_part_with_a_header_marker_is_automated(headers):
    assert automation_markers(m("info@family.example", "Hi", headers=headers))


def test_role_local_part_at_known_company_is_automated():
    found = automation_markers(m("service@paypal.com", "Hi"))
    assert any("role address" in f and "paypal.com" in f for f in found)


def test_role_local_part_gets_no_automated_boost():
    # Same subject, same score whether a person writes from anna@ or from mail@ on their own domain.
    a = heuristic_decision(m("anna@family.example", "Sale of the house"))
    b = heuristic_decision(m("mail@family.example", "Sale of the house"))
    assert a.confidence == b.confidence
    assert final_category(m("mail@family.example", "Sale of the house")) == INBOX


def test_account_confirmation_helper():
    assert is_account_confirmation(m("x@example.com", "Verify your email address"))
    assert is_account_confirmation(m("x@example.com", "Confirm your account"))
    assert not is_account_confirmation(m("x@example.com", "Your order is confirmed"))
    # in the text only when it is not a mailing (newsletters mention "confirm your email preferences")
    assert is_account_confirmation(m("x@example.com", "Welcome", snippet="Please confirm your email"))
    assert not is_account_confirmation(m("x@example.com", "Welcome", headers=UNSUB,
                                         snippet="Please confirm your email"))
    d = heuristic_decision(m("no-reply@app.example.com", "Please verify your email address"))
    assert d.keeps and d.confidence >= THRESHOLD and "confirmation" in d.reason


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
    ("u friend icloud", m("user@icloud.com", "Are you free this weekend?"), INBOX),
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
    # "u verify email" was a miss here; the 2026-10-28 fix wave added an account-confirmation rule
    # for it, so it moved to the main table ("verify email") and no longer counts as unseen.
]


def test_unseen_accuracy(capsys):
    hits = [label for label, msg, expected in UNSEEN if final_category(msg) == expected]
    misses = [(label, heuristic_decision(msg)) for label, msg, expected in UNSEEN if label not in hits]
    with capsys.disabled():
        print(f"\nheuristics accuracy on unseen set: {len(hits)}/{len(UNSEEN)}")
        for label, d in misses:
            print(f"  miss {label}: {d}")
    assert len(hits) / len(UNSEEN) >= 0.75


# A second unseen set, written on 2026-10-28 after the fix wave and NOT tuned against: whatever it
# scores is reported as is. Mixes people (some writing from role addresses), shops, banks,
# newsletters, notifications, social and travel mail.
UNSEEN_2 = [
    ("n neighbour", m("neighbour@example.org", "Your parcel is with us at number 12"), INBOX),
    ("n teacher", m("ms.ortiz@school.example.org", "Field trip permission slip due Friday"), INBOX),
    ("n friend gmail", m("user@gmail.com", "Happy birthday!! Drinks on me"), INBOX),
    ("n landlord", m("landlord@example.com", "Rent payment for November"), INBOX),
    ("n bakery info@", m("info@corner-bakery.example", "Your cake order for Saturday"), INBOX),
    ("n tutor hello@", m("hello@mathtutor.example", "Lesson notes and next week's homework"), INBOX),
    ("n architect office@", m("office@archstudio.example", "Revised floor plans"), INBOX),
    ("n family mail@", m("mail@vandenberg-family.example", "Christmas plans and a gift list"), INBOX),
    ("n target order", m("orders@target.com", "Your Target order has shipped"), "receipts"),
    ("n ikea promo", m("news@ikea.com", "Up to 50% off kitchen storage this week", headers=UNSUB),
     "promotions"),
    ("n small shop receipt", m("orders@candleshop.example", "Order #2045 confirmed - thank you!",
                               headers={"X-Shopify-Shop": "1"}), "receipts"),
    ("n wells statement", m("alerts@notify.wellsfargo.com", "Your statement is available online"), "finance"),
    ("n revolut transfer", m("no-reply@revolut.com", "You sent €50 to Alex"), "finance"),
    ("n morning brew", m("crew@morningbrew.com", "Today's top stories: markets rally", headers=UNSUB),
     "newsletters"),
    ("n club newsletter", m("newsletter@garden-club.example.org", "Spring newsletter: planting guide",
                            headers={**UNSUB, **LIST}), "newsletters"),
    ("n trello", m("do-not-reply@trello.com", "Sam added you to the board Home Reno"), "notifications"),
    ("n dropbox storage", m("no-reply@dropbox.com", "Your Dropbox is almost full"), "notifications"),
    ("n vercel deploy", m("notifications@vercel.com", "Deployment failed for my-site"), "notifications"),
    ("n strava", m("no-reply@strava.com", "Alex gave you kudos", headers=UNSUB), "social"),
    ("n easyjet", m("noreply@easyjet.com", "Your easyJet booking confirmation EZY123"), "travel"),
]


def test_unseen_2_accuracy(capsys):
    assert len(UNSEEN_2) == 20
    hits = [label for label, msg, expected in UNSEEN_2 if final_category(msg) == expected]
    misses = [(label, heuristic_decision(msg)) for label, msg, expected in UNSEEN_2 if label not in hits]
    with capsys.disabled():
        print(f"\nheuristics accuracy on unseen set 2: {len(hits)}/{len(UNSEEN_2)}")
        for label, d in misses:
            print(f"  miss {label}: {d}")
    # The people rows are the safety promise: none of them may be moved.
    people = [label for label, _, expected in UNSEEN_2 if expected == INBOX]
    assert all(label in hits for label in people)
    assert len(hits) / len(UNSEEN_2) >= 0.7
