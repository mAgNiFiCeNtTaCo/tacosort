# Security policy

MailTidy has access to people's mailboxes, so we take security problems seriously.

## Reporting a problem

Please **don't open a public issue** for a security problem. Use GitHub's private
**"Report a vulnerability"** button on this repository's *Security* tab instead. Include what you found,
how to reproduce it, and what an attacker could do with it. Never include real passwords, tokens or
anyone's emails; use `example.com` test data.

We aim to reply within a week, and to publish a fix and credit you (if you like) once it's fixed.

## What counts

For example:

- a password, app password or token ending up in a file, log, error message or report;
- a crafted email that makes MailTidy do something unexpected: run code, inject HTML or script into
  the report, move or delete mail it shouldn't, or change the AI's instructions so it moves mail
  wrongly;
- MailTidy deleting mail, marking mail as read, or touching Sent / Drafts / Trash / Junk / All Mail;
- insecure connections (e.g. not checking the server's certificate);
- the background job running something other than MailTidy, or with more rights than needed.

## Supported versions

Security fixes go into the latest release. Please update with `pipx upgrade mailtidy` (or
`pip install --upgrade mailtidy`).

## How MailTidy protects you

- Passwords and sign-in tokens live only in your operating system's keychain.
- Connections to mail servers are encrypted (SSL/TLS or STARTTLS), with certificate checks. The one
  exception is a server on your own computer (127.0.0.1, e.g. Proton Mail Bridge), which uses its own
  self-made certificate; that connection never leaves your computer.
- Mail is read with IMAP "peek"; MailTidy never deletes, and only removes the exact messages it has
  just copied when a server can't move mail directly.
- The HTML report has no scripts, loads nothing from the internet, and escapes all email text.
- The optional AI is off by default and receives only From, Subject and a short snippet; see
  [docs/privacy.md](docs/privacy.md).
