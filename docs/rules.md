# Your own rules

MailTidy guesses well on its own, but you know your mail best. Rules let you say exactly where some
mail goes. They are checked **before** MailTidy's own guesses (only the safety checks come first: see
[how-it-works.md](how-it-works.md)).

Rules live in the settings file. Open it with `mailtidy config edit`. When you save and close the
editor, MailTidy checks the file and tells you about mistakes. You can check it any time with
`mailtidy config check`.

## A first rule

```yaml
rules:
  - name: school
    from: "*@school.example.com"
    action: folder:Kids School
```

"Anything from anyone at school.example.com goes to the *Kids School* folder."

Try it without waiting for real mail:

```
mailtidy rules test --from teacher@school.example.com --subject "Field trip"
```

```
Result: it goes to Kids School (category: inbox).
Why: Your rule "school" (folder Kids School)
Decided by: user rule (confidence 1.00; ...)
```

You can also test a saved email: in most mail apps you can save or export a message as an `.eml` file;
then run `mailtidy rules test message.eml`.

## What a rule can look at

| Key | Matches when... | Example |
|---|---|---|
| `from` | the sender's address fits | `from: news@example.com` |
| `to` | any To or Cc address fits (useful for aliases) | `to: shopping@example.com` |
| `subject` | the subject contains this text | `subject: "invoice"` |
| `list_id` | the mailing list's List-Id contains this text | `list_id: dev.lists.example.com` |
| `header` | a header contains this text | `header: {X-Mailer: "MyShop"}` |
| `accounts` | the mail is in one of these accounts (by id) | `accounts: [work]` |

How they combine:

- **Every key** you give must match (AND). `from` + `subject` = from that sender *and* with that subject.
- **A list** under one key matches if **any** item matches (OR):
  `subject: ["invoice", "receipt", "Rechnung"]`.
- Upper and lower case don't matter.

Address patterns (`from`, `to`):

| Pattern | Matches |
|---|---|
| `someone@example.com` | exactly that address |
| `*@example.com` (or `@example.com`, or just `example.com`) | anyone at exactly that domain |
| `*.example.com` | that domain and all its subdomains (`mail.example.com`, `news.example.com`) |
| `billing-*@example.com` | `*` and `?` wildcards on the whole address |

## Where it goes (`action`)

| Action | Meaning |
|---|---|
| `receipts`, `finance`, `travel`, `newsletters`, `promotions`, `social`, `notifications` | that category's folder (renamed / with `folder_prefix` if you set those) |
| your own category, e.g. `kids` | the folder you gave it under `categories:` |
| `inbox` | leave it in the Inbox, whatever MailTidy would have guessed |
| `folder:<name>` | exactly that folder, e.g. `folder:Clients/Acme` (`/` makes a sub-folder; `folder_prefix` is not added) |

Your own categories are added under `categories:`:

```yaml
categories:
  kids: Kids School
rules:
  - name: school
    from: ["*@school.example.com", "*@pta.example.org"]
    action: kids
```

## Order

Rules are checked from top to bottom and **the first one that matches wins**. Put specific rules above
general ones:

```yaml
rules:
  - name: amazon invoices to finance
    from: "*.amazon.example.com"
    subject: invoice
    action: finance
  - name: all other amazon mail is receipts
    from: "*.amazon.example.com"
    action: receipts
```

## VIPs

Addresses or domains in `vip:` always stay in the Inbox, before any rule:

```yaml
vip:
  - mom@example.com
  - mycompany.example.com
```

## More examples

```yaml
rules:
  # One newsletter you actually read: keep it in the Inbox
  - name: favourite newsletter
    from: letters@writer.example.com
    action: inbox

  # Everything sent to a shopping alias
  - name: shopping alias
    to: shop@example.com
    action: receipts

  # A mailing list into its own folder
  - name: hiking club
    list_id: hiking.groups.example.org
    action: folder:Clubs/Hiking

  # Only in the work account
  - name: build robot
    from: ci@build.example.com
    accounts: [work]
    action: notifications
```

## Rules vs learning

You don't have to write rules for everything. When you move a message MailTidy sorted, it learns
that sender (see [how-it-works.md](how-it-works.md)). `mailtidy rules list` shows your rules and what
MailTidy learned. Rules are best for things you want to be sure of, or for whole domains.

## Mistakes MailTidy catches

`mailtidy config check` warns about:

- an `action` that isn't a category, `inbox` or `folder:...`;
- a rule pointing at a category you turned `off` (the mail would just stay in the Inbox);
- a rule with no conditions (it would match every message);
- two rules with the same name;
- `accounts:` naming an account that doesn't exist;
- unknown keys (a typo like `subjet:`), which are ignored.
