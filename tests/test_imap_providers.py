from mailtidy.imap import PROVIDERS, detect_provider


def test_detect_known_domains():
    assert detect_provider("someone@gmail.com").key == "gmail"
    assert detect_provider("Someone@GoogleMail.com").key == "gmail"
    assert detect_provider("a@hotmail.co.uk").key == "outlook"
    assert detect_provider("a@outlook.com").key == "outlook"
    assert detect_provider("a@ymail.com").key == "yahoo"
    assert detect_provider("a@me.com").key == "icloud"
    assert detect_provider("a@mac.com").key == "icloud"
    assert detect_provider("a@aol.com").key == "aol"
    assert detect_provider("a@fastmail.com").key == "fastmail"
    assert detect_provider("a@zohomail.com").key == "zoho"
    assert detect_provider("a@gmx.com").key == "gmx"
    assert detect_provider("a@gmx.de").key == "gmx-net"
    assert detect_provider("a@proton.me").key == "protonmail-bridge"


def test_unknown_and_malformed_give_custom():
    assert detect_provider("a@example.com").key == "custom"
    assert detect_provider("not-an-address").key == "custom"
    assert detect_provider("").key == "custom"


def test_presets_are_sane():
    for key, p in PROVIDERS.items():
        assert p.key == key
        assert p.notes, key
        assert p.auth in ("password", "oauth2-google", "oauth2-microsoft")
        if key != "custom":
            assert p.host and p.port > 0
    assert PROVIDERS["gmail"].host == "imap.gmail.com"
    assert PROVIDERS["outlook"].host == "outlook.office365.com"
    assert PROVIDERS["outlook"].auth == "oauth2-microsoft"
    assert PROVIDERS["yahoo"].host == "imap.mail.yahoo.com"
    assert PROVIDERS["icloud"].host == "imap.mail.me.com"
    bridge = PROVIDERS["protonmail-bridge"]
    assert (bridge.host, bridge.port, bridge.ssl) == ("127.0.0.1", 1143, False)


def test_no_domain_claimed_twice():
    seen = {}
    for p in PROVIDERS.values():
        for d in p.domains:
            assert d not in seen, f"{d} in {seen.get(d)} and {p.key}"
            seen[d] = p.key
