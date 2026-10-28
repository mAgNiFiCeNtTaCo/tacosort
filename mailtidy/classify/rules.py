"""User rules from the config file (`rules:`): matching a message and turning a rule into a Decision.

A rule matches when every condition it gives matches (AND). A condition with a list of values
matches when any value matches (OR). Everything is case-insensitive.

Address patterns (`from`, `to`):
  - `someone@example.com`  exact address
  - `*@example.com`        anyone at exactly that domain (also `@example.com` or `example.com`)
  - `*.example.com`        that domain and any of its subdomains (mail.example.com, ...)
  - any other `*` / `?` pattern is matched as a shell-style wildcard against the whole address
"""

from __future__ import annotations

from fnmatch import fnmatchcase

from ..models import INBOX, AppConfig, Decision, Message, Rule

FOLDER_PREFIX = "folder:"


def address_matches(pattern: str, email: str) -> bool:
    """True when `email` fits the address pattern (see the module docstring)."""
    pattern = pattern.strip().lower()
    email = email.strip().lower()
    if not pattern or not email:
        return False
    domain = email.rsplit("@", 1)[-1] if "@" in email else ""
    if pattern.startswith("*."):
        base = pattern[2:]
        return domain == base or domain.endswith("." + base)
    if pattern.startswith("*@"):
        rest = pattern[2:]
        if "*" in rest or "?" in rest:
            return fnmatchcase(domain, rest)
        return domain == rest
    if pattern.startswith("@"):
        return domain == pattern[1:]
    if "@" not in pattern and "*" not in pattern and "?" not in pattern:
        return domain == pattern            # a bare domain
    if "*" in pattern or "?" in pattern:
        return fnmatchcase(email, pattern)
    return email == pattern


def _any_address(patterns: list[str], emails: list[str]) -> bool:
    return any(address_matches(p, e) for p in patterns for e in emails)


def _any_substring(needles: list[str], text: str) -> bool:
    text = text.lower()
    return any(n.lower() in text for n in needles if n)


def rule_matches(rule: Rule, msg: Message) -> bool:
    """True when every condition the rule gives matches the message."""
    if rule.accounts and msg.account.lower() not in {a.lower() for a in rule.accounts}:
        return False
    if rule.from_ and not _any_address(rule.from_, [msg.from_.email]):
        return False
    if rule.to and not _any_address(rule.to, [a.email for a in msg.to + msg.cc]):
        return False
    if rule.subject and not _any_substring(rule.subject, msg.subject):
        return False
    if rule.list_id and not _any_substring(rule.list_id, msg.headers.get("list-id", "")):
        return False
    for name, needle in rule.header.items():
        value = msg.headers.get(name.lower())
        if value is None or needle.lower() not in value.lower():
            return False
    return True


def rule_decision(rule: Rule) -> Decision:
    """The Decision a matching rule produces. A "folder:<name>" action keeps category "inbox" and
    sets `Decision.folder`, so the message goes to exactly that folder (no prefix added)."""
    action = rule.action.strip()
    reason = f'Your rule "{rule.name}"'
    if action.lower().startswith(FOLDER_PREFIX):
        folder = action[len(FOLDER_PREFIX):].strip()
        return Decision(INBOX, 1.0, "user-rule", f"{reason} (folder {folder})", folder=folder or None,
                        explicit=True)
    return Decision(action.lower(), 1.0, "user-rule", reason, explicit=True)


def first_matching_rule(rules: list[Rule], msg: Message) -> tuple[Rule, Decision] | None:
    """The first rule (in config order) that matches, with its Decision."""
    for rule in rules:
        if rule_matches(rule, msg):
            return rule, rule_decision(rule)
    return None


def validate_rules(rules: list[Rule], config: AppConfig) -> list[str]:
    """Plain-English problems with the rules, for `mailtidy` to show. Empty list = all fine."""
    problems: list[str] = []
    account_ids = {a.id.lower() for a in config.accounts}
    seen_names: set[str] = set()
    for i, rule in enumerate(rules, 1):
        label = f'Rule {i} ("{rule.name}")' if rule.name else f"Rule {i}"
        if not rule.name:
            problems.append(f"{label} has no name. Give it a short name so reports can mention it.")
        elif rule.name.lower() in seen_names:
            problems.append(f'{label}: another rule already uses the name "{rule.name}".')
        seen_names.add(rule.name.lower())

        action = rule.action.strip()
        if not action:
            problems.append(f"{label} has no action. Say where the mail should go, e.g. action: receipts.")
        elif action.lower().startswith(FOLDER_PREFIX):
            if not action[len(FOLDER_PREFIX):].strip():
                problems.append(f'{label}: "folder:" needs a folder name after it, e.g. folder:Work.')
        elif action.lower() != INBOX:
            key = action.lower()
            if key not in config.categories:
                known = ", ".join(sorted(k for k in config.categories if k != INBOX))
                problems.append(
                    f'{label} sends mail to "{action}", which is not a category. '
                    f"Use one of: inbox, {known}, or folder:<name>."
                )
            elif config.folder_for(key) is None:
                problems.append(
                    f'{label} sends mail to "{action}", but that category is turned off, '
                    "so matching mail will stay in the Inbox."
                )

        if not (rule.from_ or rule.to or rule.subject or rule.list_id or rule.header):
            problems.append(f"{label} has no conditions, so it would match every message. Add from/subject/etc.")
        for pattern in rule.from_ + rule.to:
            p = pattern.strip()
            if not p:
                problems.append(f"{label} has an empty address in from/to.")
            elif " " in p:
                problems.append(f'{label}: "{pattern}" has a space in it, so it can never match an address.')
        for acc in rule.accounts:
            if account_ids and acc.lower() not in account_ids:
                problems.append(f'{label} mentions account "{acc}", but no account has that name.')
    return problems
