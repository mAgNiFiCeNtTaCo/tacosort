# Setting up each email provider

`tacosort setup` recognises these providers from your email address and fills in the server details.
For most of them you need an **app password**: a separate password just for TacoSort. You can cancel
it at any time, and your normal password keeps working everywhere else.

Server details were last checked in October 2026. Providers change their settings pages now and then;
if a step below doesn't match what you see, search your provider's help for "app password" or
"IMAP settings".

If a login fails, `tacosort accounts test` explains why. To enter a new password, run
`tacosort accounts add` with the same email address: it offers to replace the password.

## Gmail (gmail.com, googlemail.com)

Server `imap.gmail.com`, port 993, SSL.

1. Turn on **2-Step Verification**: Google Account > Security > 2-Step Verification. App passwords
   only exist once this is on.
2. Open [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords), type a name
   such as *TacoSort* and click **Create**.
3. Google shows a 16-letter password in four groups. Paste it into TacoSort (the spaces don't matter).

IMAP is always on for personal Gmail. On a **work or school Google account**, your administrator may
have turned IMAP or app passwords off: ask them.

About labels: Gmail shows folders as labels. When TacoSort moves a message to *Receipts*, it gets the
*Receipts* label and leaves the Inbox. It is still in *All Mail*, as with any Gmail label.

Labels on mail **you keep in the Inbox** are respected. If you put a "Family" label on a message but
leave it in the Inbox, TacoSort reads that as "this stays in my Inbox": it never moves that mail out,
and it doesn't learn "mail from this sender goes to Family" from it. Only mail you've actually moved
out of the Inbox teaches TacoSort where a sender's mail belongs.

Messages without a *Message-ID* (a hidden label mail programs use to tell messages apart; a few
badly made emails don't have one) are left alone in the Inbox, on every provider: TacoSort couldn't
undo a move of such a message reliably, so it doesn't make one.

## Outlook.com, Hotmail, Live, MSN

Server `outlook.office365.com`, port 993, SSL, sign-in **OAuth2** (no passwords).

Microsoft switched off password sign-in (including app passwords) for mail programs on personal
Microsoft accounts. TacoSort signs in the modern way instead: you approve it once in your browser.
This needs a free *client ID* that you create once. Step-by-step: [outlook-oauth.md](outlook-oauth.md).

Work or school Microsoft 365 accounts work the same way, but your organisation's administrator may
need to allow it.

## Yahoo Mail

Server `imap.mail.yahoo.com`, port 993, SSL.

1. Open [login.yahoo.com/account/security](https://login.yahoo.com/account/security).
2. Click **Generate app password** (or *Manage app passwords*), name it *TacoSort*, click **Generate**.
3. Paste the password into TacoSort.

## iCloud Mail (icloud.com, me.com, mac.com)

Server `imap.mail.me.com`, port 993, SSL.

1. Two-factor authentication must be on for your Apple Account.
2. Go to [account.apple.com](https://account.apple.com) > **Sign-In and Security** >
   **App-Specific Passwords**, and create one named *TacoSort*.
3. Paste it into TacoSort.

If the login is refused, set `username:` to the part of your address before the @ in the settings file
(`tacosort config edit`).

## AOL Mail

Server `imap.aol.com`, port 993, SSL.

1. Open [login.aol.com/account/security](https://login.aol.com/account/security).
2. Click **Generate app password**, name it *TacoSort*, click **Generate**.
3. Paste it into TacoSort.

## Fastmail

Server `imap.fastmail.com`, port 993, SSL.

1. Settings > **Privacy & Security** > **Connected apps & API tokens**
   ([direct link](https://app.fastmail.com/settings/security/apps)).
2. **New app password**, give it IMAP (mail) access, name it *TacoSort*.
3. Paste it into TacoSort.

## Zoho Mail

Server `imap.zoho.com`, port 993, SSL.

1. In Zoho Mail: Settings > Mail Accounts > IMAP, and tick **IMAP Access**.
2. If two-factor sign-in is on, create an app password at
   [accounts.zoho.com](https://accounts.zoho.com/home#security/app_password) and use that.

Accounts in Europe use `imap.zoho.eu`, in India `imap.zoho.in`; paid business accounts use
`imappro.zoho.com` (or `.eu` / `.in`). Change `host:` in the settings file if needed.

## GMX

- gmx.com, gmx.us, gmx.co.uk, gmx.fr, gmx.es: server `imap.gmx.com` (provider `gmx`).
- gmx.net, gmx.de, gmx.at, gmx.ch: server `imap.gmx.net` (provider `gmx-net`).

1. In GMX: Email settings > **POP3 & IMAP**, and allow access via POP3 and IMAP.
2. Use your normal GMX password, or an app password if two-factor sign-in is on.

## Proton Mail (through Proton Mail Bridge)

Proton Mail doesn't speak IMAP by itself; the **Proton Mail Bridge** app (paid Proton plans) does.

1. Install and open Bridge from [proton.me/mail/bridge](https://proton.me/mail/bridge) and sign in.
2. Click your account in Bridge to see its settings: server `127.0.0.1`, port `1143`, security
   STARTTLS, and a special **Bridge password**.
3. Use that Bridge password in TacoSort (not your Proton password).

Bridge must be running whenever TacoSort runs.

## Any other provider ("custom")

Setup asks for:

- **IMAP server name**, e.g. `imap.example.com` (search "<your provider> IMAP settings");
- **port**: usually 993 (SSL/TLS) or 143 (STARTTLS);
- **password**: many providers want an app password instead of your normal one.

In the settings file such an account looks like:

```yaml
  - id: work
    email: me@example.com
    provider: custom
    host: imap.example.com
    port: 993
    ssl: yes
    username: me           # only if your login name isn't your email address
```

## Common problems

| What you see | What to do |
|---|---|
| "The app password was not accepted" | Make a new app password and paste it carefully; check 2-Step Verification is on. |
| "Couldn't reach the mail server" | Check your internet connection, the server name and port. For Proton, is Bridge running? |
| "No keychain is available" (Linux) | Install and unlock a Secret Service keyring (GNOME Keyring or KWallet). On a server with no desktop, see the keyring project's docs for a file-based backend. |
| Gmail "IMAP access is disabled" | Work/school account: ask your administrator. |
| Outlook "basic authentication is disabled" | Use OAuth: [outlook-oauth.md](outlook-oauth.md). |
