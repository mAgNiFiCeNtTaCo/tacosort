# TacoSort

**Every email account you have, organized the same way, automatically, and kept that way.**

TacoSort is a small free program that sorts the automated mail in your inbox (receipts, bank
statements, newsletters, promotions, social media alerts, app notifications) into clear folders, so
your Inbox only holds mail a person wrote to you. It works with Gmail, Outlook.com/Hotmail, Yahoo,
iCloud, Fastmail, AOL, Zoho, GMX, Proton Mail (through Bridge) and any other provider that offers
IMAP. Every account gets the same folders.

```
Before                                   After
INBOX (2,341)                            INBOX (37)        <- people, replies, security alerts, starred
  Mom: Sunday lunch?                       Mom: Sunday lunch?
  Amazon: Your order has shipped         Receipts          <- orders, invoices, deliveries
  Chase: Your statement is ready           Amazon          <- a folder per company with 3+ emails
  Delta: Your boarding pass              Finance           <- statements, bills, payments
  The Weekly Digest #212                 Travel            <- flights, hotels, bookings
  50% off everything this weekend!       Newsletters       <- things you subscribed to
  LinkedIn: You appeared in 9 searches   Promotions        <- sales and marketing
  GitHub: [repo] New issue opened        Social            <- social networks, forums
  eBay: We're updating our Terms of Use  Notifications     <- automated notices from apps
  ...                                      GitHub
                                         Accounts          <- terms of service, account changes
```

## What it does

- **One scheme for all your accounts.** Add as many mailboxes as you like; each gets the same folders.
- **A folder per company.** Once a company has sent you 3 emails of one kind, they get their own
  folder inside it: Promotions/Crunchyroll, Receipts/Google Play, Notifications/GitHub. Mail TacoSort
  filed before that is moved in too, and new mail follows. Automated mail TacoSort can't place for
  sure (is that eBay email an ad or an alert?) joins its company's folder too, once that company has
  sent 3 of them, so a busy sender doesn't stay in your Inbox just because its mail is a mix.
- **Learns from repetition.** When one address keeps sending the same kind of email (5 "your items
  have sold" emails, or 10 emails of any kind), TacoSort treats it as a robot even if the emails look
  plain, and files them with that company's other mail. Once a day it looks again at the mail it left
  in your Inbox, so what it learned since applies to older mail too.
- **Old security emails can leave too.** With `security_keep_days: 3`, codes, sign-in alerts and
  "verify your email" stay in the Inbox for 3 days, then move to a Security folder.
- **Old mail can leave too.** With `old_mail_days: 180`, read mail that stays in the Inbox (people,
  replies, emails TacoSort couldn't place) moves to an "Old mail" folder once it is six months old.
  Starred and unread mail, your VIPs and mail your own rules keep in the Inbox stay.
- **Optional waiting time.** With `hold_hours: 24`, new mail stays in the Inbox for a day so you see
  it there first, then gets sorted.
- **Keeps your Inbox for people.** Mail from people you've written to, replies, security codes,
  password resets, sign-in alerts, messages a buyer or seller sent you through a marketplace, and
  starred mail always stay in the Inbox.
- **Keeps running.** Turn on the background schedule (Windows, macOS, Linux) and new mail is sorted
  every few minutes, or run `tacosort watch` to sort mail the moment it arrives.
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
  `tacosort preview`. A preview changes nothing.
- **Every move can be undone.** `tacosort undo` puts the last run's mail back, and it remembers not to
  move those messages again.
- **When unsure, it does nothing.** Mail it isn't confident about stays in the Inbox.
- **Your password stays in your computer's keychain** (Windows Credential Manager, macOS Keychain,
  Linux Secret Service), never in a file or a log. TacoSort uses *app passwords*, which you can cancel
  at any time without changing your real password.
- **Nothing leaves your computer** except the connection to your own mail server (and, only if you
  turn AI on, the few details listed in [docs/privacy.md](docs/privacy.md)).

## Install

TacoSort is installed straight from this GitHub page. It is **not** published on PyPI, so don't
`pip install` it by name: a package with a similar name there would be someone else's program.

**1. Install Python** 3.10 or newer from [python.org/downloads](https://www.python.org/downloads/).
On Windows, tick **"Add python.exe to PATH"** on the first screen of the installer.

**2. Install pipx**, a small helper that keeps TacoSort in its own space so it can't clash with
anything else. Open a terminal (Windows: *PowerShell*; macOS: *Terminal*; Linux: your terminal) and type:

- **Windows:** `py -m pip install --user pipx` then `py -m pipx ensurepath`
- **macOS:** `brew install pipx` then `pipx ensurepath` (no Homebrew? use
  `python3 -m pip install --user pipx` then `python3 -m pipx ensurepath`)
- **Linux:** `sudo apt install pipx` (Debian/Ubuntu) or `sudo dnf install pipx` (Fedora), then
  `pipx ensurepath`

Then **close the terminal and open a new one**, so it finds the `pipx` command.

**3. Install TacoSort:**

```
pipx install git+https://github.com/mAgNiFiCeNtTaCo/tacosort
```

With the optional AI helper (see *AI* below) instead:

```
pipx install "tacosort[ai] @ git+https://github.com/mAgNiFiCeNtTaCo/tacosort"
```

(Already installed without it? `pipx inject tacosort anthropic` adds it.)

**4. Check it works:** `tacosort --version`. To update later: `pipx upgrade tacosort`.

**From the source code** (for trying the newest changes or helping out):

```
git clone https://github.com/mAgNiFiCeNtTaCo/tacosort
cd tacosort
python -m venv .venv
.venv\Scripts\activate          (Windows)   or   source .venv/bin/activate   (macOS / Linux)
pip install -e ".[dev]"
```

## Quick start (about 5 minutes)

```
tacosort setup
```

The setup asks a few questions:

1. **Your email address.** It recognises the big providers and fills in the server details itself.
2. **An app password.** It shows you exactly where to create one for your provider (with the link),
   then you paste it in. Typing is hidden. It tests the login straight away.
3. **More accounts?** Add as many as you like.
4. **Learn from your folders?** Say yes if you already sort some mail by hand: TacoSort will follow
   your system.
5. **Preview?** It shows a table of what it *would* move (sender, subject, folder, and why). Nothing
   is moved.
6. **Sort now?** If the preview looks right, it can sort the mail already in your Inbox. You can
   undo it.
7. **Keep it tidy automatically?** Turns on the background schedule (every 15 minutes).

That's it. From then on:

| Command | What it does |
|---|---|
| `tacosort status` | your accounts, the last run, moves today, whether the schedule is on |
| `tacosort preview` | what would be moved from your Inbox right now (moves nothing) |
| `tacosort run` | sort new mail now (`--backlog` also sorts older mail already in the Inbox) |
| `tacosort undo` | put the last run's mail back (`--last 10` for the last 10 moves) |
| `tacosort report --open` | a web page listing every move and the reason for it |
| `tacosort learn` | learn again from the folders you use |
| `tacosort watch` | stay open and sort mail the moment it arrives (Ctrl+C stops) |
| `tacosort accounts list` / `add` / `remove ID` / `test` | manage your accounts |
| `tacosort rules list` | your rules and what TacoSort learned |
| `tacosort rules test --from someone@example.com --subject "Hello"` | where would this message go? |
| `tacosort schedule on --every 30` / `off` / `status` | the background schedule |
| `tacosort config path` / `edit` / `check` | find, edit or check the settings file |
| `tacosort config ai-key` | save your Anthropic API key for the optional AI (`--remove` deletes it) |

Add `--help` to any command for its options. `--config PATH` uses a different settings file.

## Getting an app password

An *app password* is a separate password just for TacoSort. You can cancel it at any time, and
your normal password keeps working everywhere else. `tacosort setup` shows these steps too.
Full details, and what to do when it goes wrong: [docs/providers.md](docs/providers.md).

- **Gmail:** turn on 2-Step Verification, then create an app password at
  [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords).
- **Yahoo:** Account Security > Generate app password
  ([login.yahoo.com/account/security](https://login.yahoo.com/account/security)).
- **iCloud:** with two-factor authentication on, go to [account.apple.com](https://account.apple.com) >
  Sign-In and Security > App-Specific Passwords.
- **Outlook.com / Hotmail / Live / MSN:** Microsoft no longer allows passwords here. TacoSort signs in
  the Microsoft way (you approve it in your browser). That needs a free "client ID" you create once;
  see [docs/outlook-oauth.md](docs/outlook-oauth.md).
- **Fastmail:** Settings > Privacy & Security > Connected apps & API tokens > New app password (IMAP).
- **AOL:** Account Security > Generate app password.
- **Zoho / GMX:** turn on IMAP access in the web settings first (see docs/providers.md).
- **Proton Mail:** install and run Proton Mail Bridge, and use the password Bridge shows you.
- **Anything else:** setup asks for the IMAP server name and port from your provider's help page.

## How it keeps your mail organized

**The schedule.** `tacosort schedule on` creates a small background job (Task Scheduler on Windows,
a LaunchAgent on macOS, a systemd timer or cron on Linux) that runs `tacosort run` every 15 minutes.
It needs no admin rights, and `tacosort schedule off` removes it again.

**Its memory.** A small file on your computer (`state.sqlite3`, see `tacosort config path`) remembers
what it already looked at, every move it made (for undo and the report), and what it learned.
Each run only looks at new mail.

**Learning.**
- *From your existing folders* (`tacosort learn`): if most mail from a sender is already in one of
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
| Notifications | automated notices from apps and services |
| Accounts | terms-of-service and privacy-policy updates, account changes, welcome and plan notices |
| Business | tax exemption and resale certificates, seller and distributor applications, business bank accounts, payout set-up |

Folders are created the first time they are needed. Inside each one, a company that has sent 3 or more
emails of that kind gets its own folder (`Promotions/Crunchyroll`). The name comes from the sender
("Google Play") and never changes once made. If you move a message out of a company folder, TacoSort
leaves it where you put it. Mail sorted by one of your own rules goes exactly where the rule says.
On Gmail these show as nested labels in the left sidebar (Gmail's own tabs are fixed by Google and
can't be added to).

To change them, run `tacosort config edit`:

```yaml
categories:
  finance: Money          # rename a folder
  notifications: "off"    # keep this kind of mail in the Inbox
folder_prefix: TacoSort   # put everything inside one parent: TacoSort/Receipts, TacoSort/Money, ...
company_folders: no       # no folder per company
company_folder_min: 5     # or: a company needs 5 emails of a kind first (default 3)
unsure_to_company: no     # mail TacoSort isn't sure about stays in the Inbox
hold_hours: 24            # new mail waits in the Inbox for a day before it is sorted (default 0)
unsure_to_folder: yes     # robot mail TacoSort isn't sure about goes to its best guess (default no)
look_alike_min: 0         # don't treat repeated look-alike emails as robot mail (default 5)
recheck_inbox: no         # don't look at mail left in the Inbox again each day (default yes)
security_keep_days: 3     # security emails move to the Security folder when 3 days old (default 0: never)
business_keep_days: 3     # people's business emails (a reply about your tax exemption) move to Business
                          # when 3 days old (default 0: they stay in the Inbox)
old_mail_days: 180        # read mail left in the Inbox moves to "Old mail" when 180 days old (default 0: never)
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

Test a rule without waiting for mail: `tacosort rules test --from teacher@school.example.com`.
All the options: [docs/rules.md](docs/rules.md).

## AI (optional)

TacoSort's built-in checks handle most mail. For the few it is unsure about, it can ask Claude (an AI
by Anthropic) using **your own** API key. It is off by default. To turn it on:

1. Install TacoSort with the AI helper (see *Install* above, or `pipx inject tacosort anthropic`).
2. Make an API key at [console.anthropic.com](https://console.anthropic.com) (API Keys).
3. Run `tacosort config ai-key` and paste the key. It is kept in your computer's keychain, like your
   mail passwords, so the background schedule can use it too. (`tacosort config ai-key --remove`
   deletes it again.)
4. Run `tacosort config edit` and set `enabled: yes` under `ai:`.

You can also put the key in the `ANTHROPIC_API_KEY` environment variable, which wins over the keychain.
But a variable you set in one terminal window is only seen by that window: the background schedule
won't see it, and AI is then quietly skipped. That's why `tacosort config ai-key` is the recommended way.

**What it can and can't do:** the AI can only pick one of your folders for a message. It can't delete,
send or reply to anything. Mail from people you know (people you've written to, VIPs, replies) never
reaches it, and mail that looks written by a person is never moved on the AI's word alone. A cleverly
written email *could* still trick it into filing automated mail in the wrong folder; `tacosort undo`
puts it back.

**What is sent:** only the sender's name and address, the subject, whether it's from a mailing list, and
(unless you set `send_snippet: no`) up to 300 characters of the text. It is sent only for messages the
other checks couldn't decide on, and at most `max_per_run` (50) per run. Never attachments, never
your password, never whole messages. Details: [docs/privacy.md](docs/privacy.md).

## Undo

- `tacosort undo`: puts back everything from the last run.
- `tacosort undo --last 20`: puts back the last 20 moves.
- `tacosort undo --run RUN-ID`: puts back one particular run (ids are in `tacosort report`).

Undone messages go back to where they were, and TacoSort won't move them again.

## Questions

**Gmail has labels, not folders. Does this work?**
Yes. In Gmail a folder is shown as a label. When TacoSort moves a message out of the Inbox it gets
the "Receipts" (etc.) label and loses the "Inbox" label, exactly as if you'd dragged it there in Gmail.
Gmail's own tabs (Promotions, Social...) are separate and unaffected; when Gmail shows them to mail
programs, TacoSort uses them as a hint.

Labels you put on mail **that you keep in the Inbox** are respected: if you label a message "Family"
but leave it in the Inbox, TacoSort takes that as "this belongs in my Inbox" and never moves that
mail (or similar mail from that sender) out of it.

**Some messages are never moved.** A few emails have no *Message-ID* (a hidden label that mail
programs use to tell messages apart). TacoSort can't keep track of those safely, so it leaves them in
the Inbox and says so in the report.

**Will my phone's mail app see the folders?**
Yes. The folders live on your mail server, so every app and device sees the same thing. Nothing
needs installing on your phone. (Some phone apps hide new folders until you tap "show all folders".)

**It moved something it shouldn't have.**
Just move it back to the Inbox in any mail app. TacoSort notices on the next run and learns that
sender stays in the Inbox. For a bigger mistake, `tacosort undo`. To make sure, add the sender to `vip:`
or write a rule. If a whole kind of mail is wrong, please open an issue (without pasting the email).

**Something isn't being sorted.**
`tacosort rules test --from sender@example.com --subject "..."` shows where it would go and why.
When TacoSort isn't sure, it leaves mail in the Inbox on purpose; a rule fixes that.

**Does it work while my computer is off?**
No. It runs on your computer, and the schedule catches up the next time you log in.

**Is my password safe?**
It is stored by your operating system's keychain, not by TacoSort. You use an app password, which
you can cancel at your provider at any time.

## Uninstall

1. `tacosort schedule off` (stops the background job).
2. `tacosort accounts remove ID` for each account (this also deletes its password from the keychain).
3. `tacosort config ai-key --remove` if you saved an AI key.
4. `pipx uninstall tacosort`.
5. Optionally delete the settings and memory folders shown by `tacosort config path`.
6. Optionally cancel the app passwords at your provider.

Your folders and mail stay exactly as they are. Nothing is deleted.

## Contributing

Bug reports, new provider details, better sorting rules and translations are very welcome. See
[CONTRIBUTING.md](CONTRIBUTING.md). Security problems: see [SECURITY.md](SECURITY.md).

## License

MIT. See [LICENSE](LICENSE).
