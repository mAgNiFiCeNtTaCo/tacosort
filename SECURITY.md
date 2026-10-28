# Security policy

TacoSort has access to people's mailboxes, so we take security problems seriously.

## Reporting a problem

Please **don't open a public issue** for a security problem. Use GitHub's private
**"Report a vulnerability"** button on this repository's *Security* tab instead. Include what you found,
how to reproduce it, and what an attacker could do with it. Never include real passwords, tokens or
anyone's emails; use `example.com` test data.

We aim to reply within a week, and to publish a fix and credit you (if you like) once it's fixed.

## What counts

For example:

- a password, app password or token ending up in a file, log, error message or report;
- a crafted email that makes TacoSort do something unexpected: run code, inject HTML or script into
  the report, delete mail, or move mail from a person out of the Inbox (including by fooling the AI;
  see *What the AI can and can't be tricked into* below);
- TacoSort deleting mail, marking mail as read, or touching Sent / Drafts / Trash / Junk / All Mail;
- insecure connections (e.g. not checking the server's certificate);
- the background job running something other than TacoSort, or with more rights than needed.

## Supported versions

Security fixes go into the latest version on this repository. Please update with
`pipx upgrade tacosort` (TacoSort is installed from GitHub, not from PyPI; see the README).

## How TacoSort protects you

- Passwords and sign-in tokens live only in your operating system's keychain.
- Connections to mail servers are encrypted (SSL/TLS or STARTTLS), with certificate checks. The one
  exception is a server on your own computer (127.0.0.1, e.g. Proton Mail Bridge), which uses its own
  self-made certificate; that connection never leaves your computer.
- Mail is read with IMAP "peek"; TacoSort never deletes, and only removes the exact messages it has
  just copied when a server can't move mail directly.
- The HTML report has no scripts, loads nothing from the internet, and escapes all email text.
- On Linux and macOS, TacoSort's settings and memory files can be opened only by your own user account.
- The optional AI is off by default and receives only From, Subject and a short snippet; see
  [docs/privacy.md](docs/privacy.md).

## What the AI can and can't be tricked into

An email can contain text written to fool the AI ("prompt injection"). TacoSort limits what that can do,
but it can't promise the AI is never fooled:

- the AI can only pick one of your folders; it can't delete, send, reply or run anything;
- mail from people you know (people you've written to, VIPs, replies, starred mail) is never sent to it;
- mail that looks written by a person is never moved on the AI's word alone;
- a crafted email *could* still make the AI file **automated** mail in the wrong folder. That is a known
  limit, not a vulnerability; `tacosort undo` puts it back. Please do report a way to get past the
  first three points, or to make the AI move mail from a person.
