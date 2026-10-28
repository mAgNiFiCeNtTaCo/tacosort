# How MailTidy works

## One run, step by step

Every run (from the schedule, `mailtidy run` or `mailtidy watch`) does this for each account:

1. **Log in** to the mail server (IMAP), with the password or sign-in kept in your keychain.
2. **Check your corrections.** For mail it moved recently, it looks where that mail is now. If you
   moved it back to the Inbox or to another folder, it learns from that (see *Learning* below).
3. **Look at new mail** in the watched folders (normally just the Inbox). It remembers the last message
   it saw in each folder, so each run only reads what's new. `--backlog` also looks at older mail.
4. **Decide** where each message belongs (see *How a decision is made*).
5. **Move** the messages that belong in a folder, creating the folder the first time it's needed.
   It moves at most `max_moves_per_run` (500) per account per run, as a safety cap.
6. **Write down** every move in its memory, so `undo`, `status` and `report` can show and reverse it.

If one account has a problem (wrong password, server down), the others still run, and the problem
is shown in plain English.

Only one run happens at a time: if the schedule fires while `mailtidy watch` is busy, it simply skips.

MailTidy reads only the headers (sender, subject, date, mailing-list information...) and, for the
optional AI, a short piece of the text. It reads with IMAP "peek", so **unread mail stays unread**.

## How a decision is made

The checks run in this order, and the **first confident answer wins**:

1. **Safety checks: stays in the Inbox.**
   - starred / flagged mail (`keep_flagged`);
   - mail from people you have written to: their addresses are found in your Sent folder
     (`keep_contacts`);
   - anyone on your `vip:` list;
   - replies in a conversation you took part in.
2. **Your rules** (`rules:` in the settings), top to bottom. See [rules.md](rules.md).
3. **What it learned**, by the sender's exact address first, then the mailing list, then the
   sender's domain.
4. **Built-in checks.** These look at the headers that automated mail carries (List-Unsubscribe,
   List-Id, Precedence: bulk, Auto-Submitted), the sending service's fingerprints, sender names like
   `noreply@` or `billing@`, words in the subject ("your order", "statement", "boarding pass",
   "% off"...), well-known company domains, and Gmail's own category tabs when available. Security
   mail (codes, password resets, new sign-in alerts) and calendar invitations are kept in the Inbox.
5. **AI (optional, off by default)**, only for the messages the checks above were unsure about. See
   [privacy.md](privacy.md).
6. **Otherwise: stays in the Inbox.** When unsure, MailTidy does nothing.

Every decision has a confidence. Below `min_confidence` (0.75), the message stays in the Inbox.
`mailtidy rules test` shows the decision, the reason and the confidence for any message.

## Learning

**From your existing folders** (`mailtidy learn`, also offered by `setup`). MailTidy looks at the newest
mail (up to 300 messages) in each of your own folders. It skips Sent, Trash, Spam, Drafts, All Mail
and Gmail's system folders. When a sender shows up at least twice and at least 80% of their mail is in
one folder, new mail from that sender goes to **that** folder. Your own system wins over the
default folders. It also reads your Sent folder to find the people you write to.

**From your corrections** (`learn_from_corrections`, on by default). You don't have to do anything
special; just use your mail app:

| You... | MailTidy learns... |
|---|---|
| move a sorted message back to the Inbox | that sender stays in the Inbox from now on, and that message is never moved again |
| move a sorted message to a different folder | future mail from that sender goes to that folder |
| delete a sorted message | nothing (deleting isn't a sorting opinion) |

One correction is enough for a sender's address. A whole domain is only learned after more evidence,
so one correction never sends a whole company's mail somewhere.

**It never fights you.** A message you put back is remembered and left alone, even if a rule or check
would move it again.

## Memory

All of this lives in one file on your computer, `state.sqlite3` in the memory folder
(`mailtidy config path` shows where). It holds:

- the runs and every move (sender, subject, from and to folder, reason) for `undo`, `status` and `report`;
- the last message seen in each folder (so each run only reads new mail);
- learned senders, mailing lists and domains;
- the addresses of people you've written to;
- the messages you put back, so they're never moved again.

It never holds message bodies or passwords. A log file, `mailtidy.log`, sits next to it (no passwords,
no message text). Deleting the memory folder makes MailTidy start fresh; your mail isn't affected.

## Keeping it running

- `mailtidy schedule on` creates a per-user background job that runs `mailtidy run --quiet` every
  `interval_minutes` (15), and at log-in:
  - **Windows:** a Task Scheduler task named *MailTidy* (uses `pythonw`, so no window pops up);
  - **macOS:** a LaunchAgent in `~/Library/LaunchAgents`;
  - **Linux:** a systemd user timer, or a cron line when systemd isn't available.
- `mailtidy watch` stays open instead and uses IMAP IDLE, so mail is sorted within seconds of
  arriving. It reconnects by itself after network trouble.

Background runs only move mail in accounts you have previewed (`mailtidy preview`).

## Undo

Every move is recorded. `mailtidy undo` finds the moved messages by their Message-ID (which doesn't
change when mail moves) and moves them back to where they came from. Undone messages are marked so
MailTidy doesn't move them again.
