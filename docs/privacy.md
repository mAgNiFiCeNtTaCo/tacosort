# Privacy

MailTidy runs on your own computer. There is no MailTidy server, account, analytics or tracking.

## What MailTidy connects to

- **Your mail server**, over an encrypted connection (IMAP with SSL/TLS or STARTTLS), to read
  headers and move messages.
- **Microsoft or Google sign-in pages**, only if you use OAuth sign-in (Outlook.com / Hotmail), to get
  and renew your sign-in.
- **Anthropic's API**, only if you turn the optional AI on (below).

Nothing else.

## What stays on your computer

| What | Where | Contains |
|---|---|---|
| Settings | `config.yaml` (see `mailtidy config path`) | your accounts' addresses and servers, folder names, rules. **No passwords.** |
| Passwords / sign-in tokens | your operating system's keychain (service name `mailtidy`) | the app password or OAuth refresh token for each account |
| Memory | `state.sqlite3` in the memory folder | moves (sender, subject, folders, reason), learned senders, addresses you've written to |
| Log | `mailtidy.log` next to the memory | what happened in each run; never passwords or message text |
| Report | `report.html` in the memory folder | the same information as the memory, as a page |

MailTidy never stores message bodies or attachments. It reads only headers, plus a short piece of
text when the AI is on.

The HTML report is a single file with no scripts and nothing loaded from the internet. Text from emails
(subjects, sender names) is escaped, so a crafted email can't run code in it.

## The optional AI

Off by default. When you turn it on (`ai: enabled: yes`, with your own Anthropic API key in the
`ANTHROPIC_API_KEY` environment variable), MailTidy asks Claude **only about messages its other checks
couldn't decide on**, and at most `max_per_run` (50) messages per run.

For each such message it sends:

- the sender's name and email address;
- the subject;
- whether it came from a mailing list (yes/no);
- unless you set `send_snippet: no`: up to **300 characters** of the message text.

It never sends attachments, whole messages, your other mail, your address book or your passwords.
The request asks only for a category; the email text is treated as data, so instructions hidden in an
email can't change what MailTidy does. Anthropic's handling of API data is described in its own
privacy policy and commercial terms.

To be even more private: `send_snippet: no` (From and Subject only), or simply leave AI off. MailTidy
works fine without it.

## Removing everything

See *Uninstall* in the [README](../README.md): `mailtidy schedule off`, `mailtidy accounts remove ID`
(also removes the keychain entry), uninstall the program, delete the folders shown by
`mailtidy config path`, and cancel the app passwords at your provider.
