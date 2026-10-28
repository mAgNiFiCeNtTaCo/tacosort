# TidyInbox

**Every email account you have, organized the same way, automatically, and kept that way.**

TidyInbox is a small free program that sorts the automated mail in your inbox (receipts, bank
statements, newsletters, promotions, social media alerts, app notifications) into clear folders, so
your Inbox only holds mail a person wrote to you. It works with Gmail, Outlook.com/Hotmail, Yahoo,
iCloud, Fastmail, AOL, Zoho, GMX, Proton Mail (through Bridge) and any other provider that offers
IMAP. Every account gets the same folders.

```
Before                                   After
INBOX (2,341)                            INBOX (37)        <- people, replies, security alerts, starred
  Mom: Sunday lunch?                       Mom: Sunday lunch?
  Amazon: Your order has shipped         Receipts          <- orders, invoices, deliveries
  Chase: Your statement is ready         Finance           <- statements, bills, payments
  Delta: Your boarding pass              Travel            <- flights, hotels, bookings
  The Weekly Digest #212                 Newsletters       <- things you subscribed to
  50% off everything this weekend!       Promotions        <- sales and marketing
  LinkedIn: You appeared in 9 searches   Social            <- social networks, forums
  GitHub: [repo] New issue opened        Notifications     <- automated notices from apps
  ...
```

## What it does

- **One scheme for all your accounts.** Add as many mailboxes as you like; each gets the same folders.
- **Keeps your Inbox for people.** Mail from people you've written to, replies, security codes,
  password resets, sign-in alerts and starred mail always stay in the Inbox.
- **Keeps running.** Turn on the background schedule (Windows, macOS, Linux) and new mail is sorted
  every few minutes, or run `tidyinbox watch` to sort mail the moment it arrives.
- **Learns.** It copies the folders you already use, and when you move a message somewhere else (or
  back to the Inbox), it learns from that and never fights you.
- **Your rules first.** Simple rules in a settings file ("anything from my kid's school goes to
  Kids School") win over its own guesses.
- **Optional AI.** For mail it can't decide on, it can ask Claude, using your own API key. This is off
  unless you turn it on.

## Safety promises

- **It never deletes anything.** It only moves mail between folders.
- **It never marks mail as read.** Messages are read with "peek", so unread mail stays unread.
- **It never touches** Sent, Drafts, Trash, Junk/Spam or Gmail's All Mail, and **never moves starred or
  flagged** mail.
- **You see a preview first.** It won't move anything in an account until you've seen
  `tidyinbox preview`. A preview changes nothing.
- **Every move can be undone.** `tidyinbox undo` puts the last run's mail back, and it remembers not to
  move those messages again.
- **When unsure, it does nothing.** Mail it isn't confident about stays in the Inbox.
- **Your password stays in your computer's keychain** (Windows Credential Manager, macOS Keychain,
  Linux Secret Service), never in a file or a log. TidyInbox uses *app passwords*, which you can cancel
  at any time without changing your real password.
- **Nothing leaves your computer** except the connection to your own mail server (and, only if you
  turn AI on, the few details listed in [docs/privacy.md](docs/privacy.md)).

## Install

TidyInbox is installed straight from this GitHub page. It is **not** published on PyPI, so don't
`pip install` it by name: a package with a similar name there would be someone else's program.

**1. Install Python** 3.10 or newer from [python.org/downloads](https://www.python.org/downloads/).
On Windows, tick **"Add python.exe to PATH"** on the first screen of the installer.

**2. Install pipx**, a small helper that keeps TidyInbox in its own space so it can't clash with
anything else. Open a terminal (Windows: *PowerShell*; macOS: *Terminal*; Linux: your terminal) and type:

- **Windows:** `py -m pip install --user pipx` then `py -m pipx ensurepath`
- **macOS:** `brew install pipx` then `pipx ensurepath` (no Homebrew? use
  `python3 -m pip install --user pipx` then `python3 -m pipx ensurepath`)
- **Linux:** `sudo apt install pipx` (Debian/Ubuntu) or `sudo dnf install pipx` (Fedora), then
  `pipx ensurepath`

Then **close the terminal and open a new one**, so it finds the `pipx` command.

**3. Install TidyInbox:**

```
pipx install git+https://github.com/mAgNiFiCeNtTaCo/tidyinbox
```

With the optional AI helper (see *AI* below) instead:

```
pipx install "tidyinbox[ai] @ git+https://github.com/mAgNiFiCeNtTaCo/tidyinbox"
```

(Already installed without it? `pipx inject tidyinbox anthropic` adds it.)

**4. Check it works:** `tidyinbox --version`. To update later: `pipx upgrade tidyinbox`.

**From the source code** (for trying the newest changes or helping out):

```
git clone https://github.com/mAgNiFiCeNtTaCo/tidyinbox
cd tidyinbox
python -m venv .venv
.venv\Scripts\activate          (Windows)   or   source .venv/bin/activate   (macOS / Linux)
pip install -e ".[dev]"
```

## Quick start (about 5 minutes)

```
tidyinbox setup
```

The setup asks a few questions:

1. **Your email address.** It recognises the big providers and fills in the server details itself.
2. **An app password.** It shows you exactly where to create one for your provider (with the link),
   then you paste it in. Typing is hidden. It tests the login straight away.
3. **More accounts?** Add as many as you like.
4. **Learn from your folders?** Say yes if you already sort some mail by hand: TidyInbox will follow
   your system.
5. **Preview?** It shows a table of what it *would* move (sender, subject, folder, and why). Nothing
   is moved.
6. **Sort now?** If the preview looks right, it can sort the mail already in your Inbox. You can
   undo it.
7. **Keep it tidy automatically?** Turns on the background schedule (every 15 minutes).

That's it. From then on:

| Command | What it does |
|---|---|
| `tidyinbox status` | your accounts, the last run, moves today, whether the schedule is on |
| `tidyinbox preview` | what would be moved from your Inbox right now (moves nothing) |
| `tidyinbox run` | sort new mail now (`--backlog` also sorts older mail already in the Inbox) |
| `tidyinbox undo` | put the last run's mail back (`--last 10` for the last 10 moves) |
| `tidyinbox report --open` | a web page listing every move and the reason for it |
| `tidyinbox learn` | learn again from the folders you use |
| `tidyinbox watch` | stay open and sort mail the moment it arrives (Ctrl+C stops) |
| `tidyinbox accounts list` / `add` / `remove ID` / `test` | manage your accounts |
| `tidyinbox rules list` | your rules and what TidyInbox learned |
| `tidyinbox rules test --from someone@example.com --subject "Hello"` | where would this message go? |
| `tidyinbox schedule on --every 30` / `off` / `status` | the background schedule |
| `tidyinbox config path` / `edit` / `check` | find, edit or check the settings file |
| `tidyinbox config ai-key` | save your Anthropic API key for the optional AI (`--remove` deletes it) |

Add `--help` to any command for its options. `--config PATH` uses a different settings file.

## Getting an app password

An *app password* is a separate password just for TidyInbox. You can cancel it at any time, and
your normal password keeps working everywhere else. `tidyinbox setup` shows these steps too.
Full details, and what to do when it goes wrong: [docs/providers.md](docs/providers.md).

- **Gmail:** turn on 2-Step Verification, then create an app password at
  [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords).
- **Yahoo:** Account Security > Generate app password
  ([login.yahoo.com/account/security](https://login.yahoo.com/account/security)).
- **iCloud:** with two-factor authentication on, go to [account.apple.com](https://account.apple.com) >
  Sign-In and Security > App-Specific Passwords.
- **Outlook.com / Hotmail / Live / MSN:** Microsoft no longer allows passwords here. TidyInbox signs in
  the Microsoft way (you approve it in your browser). That needs a free "client ID" you create once;
  see [docs/outlook-oauth.md](docs/outlook-oauth.md).
- **Fastmail:** Settings > Privacy & Security > Connected apps & API tokens > New app password (IMAP).
- **AOL:** Account Security > Generate app password.
- **Zoho / GMX:** turn on IMAP access in the web settings first (see docs/providers.md).
- **Proton Mail:** install and run Proton Mail Bridge, and use the password Bridge shows you.
- **Anything else:** setup asks for the IMAP server name and port from your provider's help page.

## How it keeps your mail organized

**The schedule.** `tidyinbox schedule on` creates a small background job (Task Scheduler on Windows,
a LaunchAgent on macOS, a systemd timer or cron on Linux) that runs `tidyinbox run` every 15 minutes.
It needs no admin rights, and `tidyinbox schedule off` removes it again.

**Its memory.** A small file on your computer (`state.sqlite3`, see `tidyinbox config path`) remembers
what it already looked at, every move it made (for undo and the report), and what it learned.
Each run only looks at new mail.

**Learning.**
- *From your existing folders* (`tidyinbox learn`): if most mail from a sender is already in one of
  your folders, new mail from that sender goes there too. Your own folders win over the defaults.
- *From the people you write to:* anyone you've sent mail to stays in the Inbox.
- *From your corrections:* if you move a sorted message back to the Inbox, that sender stays in the
  Inbox from now on, and that message is never moved again. Move it to another folder and future mail
  from that sender goes there. You just use your mail app as usual.

How each decision is made is explained in [docs/how-it-works.md](docs/how-it-works.md).

## The folders, and changing them

| Folder | What goes there |
|---|---|
| (Inbox) | people, replies, people you've written to, security alerts, starred/flagged mail |
| Receipts | order confirmations, invoices, receipts, shipping and delivery updates |
| Finance | bank and card statements, bills, payment notices, tax, investments |
| Travel | flights, hotels, car rentals, bookings, itineraries |
| Newsletters | newsletters and subscribed content |
| Promotions | marketing, sales, coupons, deals |
| Social | social networks, forums, community notifications |
| Notifications | automated notices from apps and services, account updates |

Folders are created the first time they are needed. To change them, run `tidyinbox config edit`:

```yaml
categories:
  finance: Money          # rename a folder
  notifications: "off"    # keep this kind of mail in the Inbox
folder_prefix: TidyInbox   # put everything inside one parent: TidyInbox/Receipts, TidyInbox/Money, ...
```

A fully commented example is in [examples/config.example.yaml](examples/config.example.yaml).

## Rules

Rules go in the settings file and are checked before anything else:

```yaml
rules:
  - name: school
    from: "*@school.example.com"
    action: folder:Kids School
  - name: boss
    from: boss@work.example.com
    action: inbox
vip:
  - mom@example.com
```

Test a rule without waiting for mail: `tidyinbox rules test --from teacher@school.example.com`.
All the options: [docs/rules.md](docs/rules.md).

## AI (optional)

TidyInbox's built-in checks handle most mail. For the few it is unsure about, it can ask Claude (an AI
by Anthropic) using **your own** API key. It is off by default. To turn it on:

1. Install TidyInbox with the AI helper (see *Install* above, or `pipx inject tidyinbox anthropic`).
2. Make an API key at [console.anthropic.com](https://console.anthropic.com) (API Keys).
3. Run `tidyinbox config ai-key` and paste the key. It is kept in your computer's keychain, like your
   mail passwords, so the background schedule can use it too. (`tidyinbox config ai-key --remove`
   deletes it again.)
4. Run `tidyinbox config edit` and set `enabled: yes` under `ai:`.

You can also put the key in the `ANTHROPIC_API_KEY` environment variable, which wins over the keychain.
But a variable you set in one terminal window is only seen by that window: the background schedule
won't see it, and AI is then quietly skipped. That's why `tidyinbox config ai-key` is the recommended way.

**What it can and can't do:** the AI can only pick one of your folders for a message. It can't delete,
send or reply to anything. Mail from people you know (people you've written to, VIPs, replies) never
reaches it, and mail that looks written by a person is never moved on the AI's word alone. A cleverly
written email *could* still trick it into filing automated mail in the wrong folder; `tidyinbox undo`
puts it back.

**What is sent:** only the sender's name and address, the subject, whether it's from a mailing list, and
(unless you set `send_snippet: no`) up to 300 characters of the text. It is sent only for messages the
other checks couldn't decide on, and at most `max_per_run` (50) per run. Never attachments, never
your password, never whole messages. Details: [docs/privacy.md](docs/privacy.md).

## Undo

- `tidyinbox undo`: puts back everything from the last run.
- `tidyinbox undo --last 20`: puts back the last 20 moves.
- `tidyinbox undo --run RUN-ID`: puts back one particular run (ids are in `tidyinbox report`).

Undone messages go back to where they were, and TidyInbox won't move them again.

## Questions

**Gmail has labels, not folders. Does this work?**
Yes. In Gmail a folder is shown as a label. When TidyInbox moves a message out of the Inbox it gets
the "Receipts" (etc.) label and loses the "Inbox" label, exactly as if you'd dragged it there in Gmail.
Gmail's own tabs (Promotions, Social...) are separate and unaffected; when Gmail shows them to mail
programs, TidyInbox uses them as a hint.

Labels you put on mail **that you keep in the Inbox** are respected: if you label a message "Family"
but leave it in the Inbox, TidyInbox takes that as "this belongs in my Inbox" and never moves that
mail (or similar mail from that sender) out of it.

**Some messages are never moved.** A few emails have no *Message-ID* (a hidden label that mail
programs use to tell messages apart). TidyInbox can't keep track of those safely, so it leaves them in
the Inbox and says so in the report.

**Will my phone's mail app see the folders?**
Yes. The folders live on your mail server, so every app and device sees the same thing. Nothing
needs installing on your phone. (Some phone apps hide new folders until you tap "show all folders".)

**It moved something it shouldn't have.**
Just move it back to the Inbox in any mail app. TidyInbox notices on the next run and learns that
sender stays in the Inbox. For a bigger mistake, `tidyinbox undo`. To make sure, add the sender to `vip:`
or write a rule. If a whole kind of mail is wrong, please open an issue (without pasting the email).

**Something isn't being sorted.**
`tidyinbox rules test --from sender@example.com --subject "..."` shows where it would go and why.
When TidyInbox isn't sure, it leaves mail in the Inbox on purpose; a rule fixes that.

**Does it work while my computer is off?**
No. It runs on your computer, and the schedule catches up the next time you log in.

**Is my password safe?**
It is stored by your operating system's keychain, not by TidyInbox. You use an app password, which
you can cancel at your provider at any time.

## Uninstall

1. `tidyinbox schedule off` (stops the background job).
2. `tidyinbox accounts remove ID` for each account (this also deletes its password from the keychain).
3. `tidyinbox config ai-key --remove` if you saved an AI key.
4. `pipx uninstall tidyinbox`.
5. Optionally delete the settings and memory folders shown by `tidyinbox config path`.
6. Optionally cancel the app passwords at your provider.

Your folders and mail stay exactly as they are. Nothing is deleted.

## Contributing

Bug reports, new provider details, better sorting rules and translations are very welcome. See
[CONTRIBUTING.md](CONTRIBUTING.md). Security problems: see [SECURITY.md](SECURITY.md).

## License

MIT. See [LICENSE](LICENSE).
