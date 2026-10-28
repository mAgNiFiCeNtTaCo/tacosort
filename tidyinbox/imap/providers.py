"""Known email providers: server addresses and plain-English setup steps.

Server names, ports and setup steps last checked 2026-10. Providers change these now and then; if a
login keeps failing, compare with the provider's own help page (linked in `notes`).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Provider:
    """How to reach one provider's IMAP server and how a person gets a password for it."""

    key: str                      # short name used in the config file (AccountConfig.provider)
    name: str                     # shown to people
    host: str                     # IMAP server
    port: int
    ssl: bool                     # True = TLS from the start (port 993); False = STARTTLS upgrade
    auth: str                     # "password" (app password) | "oauth2-google" | "oauth2-microsoft"
    app_password_url: str = ""    # page where the person creates an app password (or app registration)
    notes: str = ""               # plain-English setup steps
    domains: tuple[str, ...] = () # email domains that mean "this provider"


PROVIDERS: dict[str, Provider] = {
    p.key: p
    for p in (
        Provider(
            key="gmail",
            name="Gmail",
            host="imap.gmail.com",
            port=993,
            ssl=True,
            auth="password",
            app_password_url="https://myaccount.google.com/apppasswords",
            notes=(
                "Gmail needs an App Password, not your normal Google password.\n"
                "1. Turn on 2-Step Verification: Google Account > Security > 2-Step Verification.\n"
                "2. Open https://myaccount.google.com/apppasswords, type a name such as TidyInbox and "
                "click Create.\n"
                "3. Copy the 16-letter password Google shows and paste it into TidyInbox (spaces don't "
                "matter).\n"
                "IMAP is always on for personal Gmail. On a work or school Google account your "
                "administrator may have turned IMAP or App Passwords off; ask them, or use the "
                "oauth2-google sign-in instead."
            ),
            domains=("gmail.com", "googlemail.com"),
        ),
        Provider(
            key="outlook",
            name="Outlook.com / Hotmail / Live / MSN",
            host="outlook.office365.com",
            port=993,
            ssl=True,
            auth="oauth2-microsoft",
            app_password_url="https://entra.microsoft.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade",
            notes=(
                "Microsoft no longer accepts passwords (not even app passwords) for IMAP on "
                "Outlook.com, Hotmail, Live and MSN addresses. TidyInbox signs in the modern way "
                "(OAuth2): you approve it once in your browser.\n"
                "This needs a free 'app registration' of your own:\n"
                "1. Open https://entra.microsoft.com > Applications > App registrations > New "
                "registration.\n"
                "2. Supported account types: 'Personal Microsoft accounts only' (or 'any organizational "
                "directory and personal Microsoft accounts').\n"
                "3. After creating it, open Authentication and switch 'Allow public client flows' to Yes.\n"
                "4. Copy the 'Application (client) ID' into TidyInbox setup.\n"
                "TidyInbox then shows a short code: open https://microsoft.com/devicelogin, type the "
                "code and approve. You only do this once."
            ),
            domains=(
                "outlook.com", "hotmail.com", "live.com", "msn.com", "passport.com", "windowslive.com",
                "outlook.co.uk", "outlook.fr", "outlook.de", "outlook.es", "outlook.it", "outlook.jp",
                "hotmail.co.uk", "hotmail.fr", "hotmail.de", "hotmail.es", "hotmail.it", "hotmail.be",
                "hotmail.nl", "hotmail.ca", "hotmail.com.au", "hotmail.co.jp",
                "live.co.uk", "live.fr", "live.de", "live.nl", "live.it", "live.ca", "live.com.au",
            ),
        ),
        Provider(
            key="yahoo",
            name="Yahoo Mail",
            host="imap.mail.yahoo.com",
            port=993,
            ssl=True,
            auth="password",
            app_password_url="https://login.yahoo.com/account/security",
            notes=(
                "Yahoo needs an app password, not your normal Yahoo password.\n"
                "1. Open https://login.yahoo.com/account/security (Account Security).\n"
                "2. Click 'Generate app password' (or 'Manage app passwords'), name it TidyInbox and "
                "click Generate.\n"
                "3. Copy the password Yahoo shows and paste it into TidyInbox."
            ),
            domains=(
                "yahoo.com", "ymail.com", "rocketmail.com", "yahoo.co.uk", "yahoo.ca", "yahoo.fr",
                "yahoo.de", "yahoo.es", "yahoo.it", "yahoo.co.in", "yahoo.com.au", "yahoo.co.jp",
                "yahoo.com.br", "yahoo.com.mx",
            ),
        ),
        Provider(
            key="icloud",
            name="iCloud Mail",
            host="imap.mail.me.com",
            port=993,
            ssl=True,
            auth="password",
            app_password_url="https://account.apple.com/account/manage",
            notes=(
                "iCloud needs an app-specific password, not your Apple Account password.\n"
                "1. Two-factor authentication must be on for your Apple Account.\n"
                "2. Open https://account.apple.com, sign in, go to Sign-In and Security > "
                "App-Specific Passwords and create one named TidyInbox.\n"
                "3. Paste it into TidyInbox.\n"
                "If the login is refused, try the user name without '@icloud.com' (just the part "
                "before the @)."
            ),
            domains=("icloud.com", "me.com", "mac.com"),
        ),
        Provider(
            key="aol",
            name="AOL Mail",
            host="imap.aol.com",
            port=993,
            ssl=True,
            auth="password",
            app_password_url="https://login.aol.com/account/security",
            notes=(
                "AOL needs an app password, not your normal AOL password.\n"
                "1. Open https://login.aol.com/account/security (Account Security).\n"
                "2. Click 'Generate app password', name it TidyInbox and click Generate.\n"
                "3. Copy the password AOL shows and paste it into TidyInbox."
            ),
            domains=("aol.com", "aim.com", "aol.co.uk", "aol.de", "aol.fr"),
        ),
        Provider(
            key="fastmail",
            name="Fastmail",
            host="imap.fastmail.com",
            port=993,
            ssl=True,
            auth="password",
            app_password_url="https://app.fastmail.com/settings/security/apps",
            notes=(
                "Fastmail needs an app password.\n"
                "1. In Fastmail open Settings > Privacy & Security > Connected apps & API tokens "
                "(or https://app.fastmail.com/settings/security/apps).\n"
                "2. Click 'New app password', choose access for IMAP (Mail), name it TidyInbox.\n"
                "3. Copy the password and paste it into TidyInbox."
            ),
            domains=("fastmail.com", "fastmail.fm"),
        ),
        Provider(
            key="zoho",
            name="Zoho Mail",
            host="imap.zoho.com",
            port=993,
            ssl=True,
            auth="password",
            app_password_url="https://accounts.zoho.com/home#security/app_password",
            notes=(
                "1. In Zoho Mail open Settings > Mail Accounts > IMAP and tick 'IMAP Access'.\n"
                "2. If two-factor sign-in is on, create an app password at "
                "https://accounts.zoho.com/home#security/app_password and use it in TidyInbox.\n"
                "Accounts hosted in Europe use imap.zoho.eu, India imap.zoho.in; paid business "
                "accounts use imappro.zoho.com (or .eu/.in): change the server in setup if needed."
            ),
            domains=("zoho.com", "zohomail.com"),
        ),
        Provider(
            key="gmx",
            name="GMX (gmx.com)",
            host="imap.gmx.com",
            port=993,
            ssl=True,
            auth="password",
            notes=(
                "1. In GMX open Email settings > POP3 & IMAP and allow access via POP3 and IMAP.\n"
                "2. Use your normal GMX password (or an app password if you turned on two-factor "
                "sign-in).\n"
                "German, Austrian and Swiss GMX addresses (gmx.net, gmx.de, gmx.at, gmx.ch) use "
                "imap.gmx.net instead."
            ),
            domains=("gmx.com", "gmx.us", "gmx.co.uk", "gmx.fr", "gmx.es"),
        ),
        Provider(
            key="gmx-net",
            name="GMX (gmx.net / gmx.de)",
            host="imap.gmx.net",
            port=993,
            ssl=True,
            auth="password",
            notes=(
                "1. In GMX open Einstellungen > POP3/IMAP Abruf and allow access via POP3 and IMAP.\n"
                "2. Use your normal GMX password (or an app password if two-factor sign-in is on)."
            ),
            domains=("gmx.net", "gmx.de", "gmx.at", "gmx.ch"),
        ),
        Provider(
            key="protonmail-bridge",
            name="Proton Mail (through Proton Mail Bridge)",
            host="127.0.0.1",
            port=1143,
            ssl=False,
            auth="password",
            app_password_url="https://proton.me/mail/bridge",
            notes=(
                "Proton Mail only works through the Proton Mail Bridge app (paid Proton plans).\n"
                "1. Install and open Proton Mail Bridge from https://proton.me/mail/bridge and sign in.\n"
                "2. In Bridge, click your account to see the IMAP settings: server 127.0.0.1, port "
                "1143, security STARTTLS, and a special Bridge password.\n"
                "3. Use that Bridge password in TidyInbox (not your Proton password).\n"
                "Bridge must be running whenever TidyInbox runs."
            ),
            domains=("proton.me", "protonmail.com", "protonmail.ch", "pm.me"),
        ),
        Provider(
            key="custom",
            name="Other provider",
            host="",
            port=993,
            ssl=True,
            auth="password",
            notes=(
                "Look up your provider's IMAP settings (search for '<provider> IMAP settings'). You "
                "need the IMAP server name, the port (usually 993 with SSL/TLS, or 143 with STARTTLS) "
                "and your password; many providers want an 'app password' instead of the normal one."
            ),
        ),
    )
}


def detect_provider(email: str) -> Provider:
    """Guess the provider from an email address's domain; unknown domains give the 'custom' entry."""
    domain = email.strip().rsplit("@", 1)[-1].lower() if "@" in email else ""
    for provider in PROVIDERS.values():
        if domain in provider.domains:
            return provider
    return PROVIDERS["custom"]
