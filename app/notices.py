"""Every kind of phone notice and its rules, in one table.

A sender names the kind and the ids it has (`refs`); the rule decides the priority, whether the
notice clears itself (Pushover's `ttl`), the button's label and which console screen it opens.
Senders never pass these themselves, so changing how a kind behaves is one edit here.

The rules follow My-AI plans/mods-widgets-2026-11-20/phone-notifications.md. Questions and
breakage stay until read. Routine news is quiet and deletes itself after a day. A merge call rings
like a decision but also clears after a day, so a stale "PR ready" never lingers. Nothing is
emergency priority: it repeats until acknowledged, and Pushover ignores `ttl` there.
"""

import re
from dataclasses import dataclass
from enum import IntEnum, StrEnum
from urllib.parse import quote

from app.config import settings

DAY = 86_400


class Priority(IntEnum):
    LOWEST = -2  # No notification, just badge
    LOW = -1  # Quiet notification
    NORMAL = 0  # Normal notification
    HIGH = 1  # Bypass quiet hours
    EMERGENCY = 2  # Requires acknowledgment


class Kind(StrEnum):
    QUESTION = "question"
    PERMISSION = "permission"
    MERGE_CALL = "merge_call"
    RUN_FAILED = "run_failed"
    DEPLOY_FAILED = "deploy_failed"
    STORAGE_WARNING = "storage_warning"
    SERVICE_DOWN = "service_down"
    APP_DOWN = "app_down"
    RECOVERED = "recovered"
    SPEND_WARNING = "spend_warning"
    JOB_DONE = "job_done"
    ROUTINE = "routine"


class Target(StrEnum):
    """A screen a notice can open. The ones in REF_TARGETS need the sender's id for it."""

    CARD = "card"
    JOB = "job"
    AUTOMATION = "automation"
    DISPATCH = "dispatch"
    SERVICE = "service"
    URL = "url"
    HOMELAB = "homelab"
    STORAGE = "storage"
    USAGE = "usage"
    RUNS = "runs"


# The console reads `?open=<kind>:<id>` once on load (sazed-frontend src/nav/deepLinks.ts) and
# `?page=<hub>.<view>[.<sub>]` for screens with no item.
_OPEN_KIND = {
    Target.CARD: "backlog",
    Target.JOB: "job",
    Target.AUTOMATION: "automation",
    Target.DISPATCH: "dispatch",
    Target.SERVICE: "service",
}
_PAGE = {
    Target.HOMELAB: "system.homelab",
    Target.STORAGE: "system.homelab.storage",
    Target.USAGE: "system.usage",
    Target.RUNS: "agents.runs",
}
REF_TARGETS = frozenset(_OPEN_KIND) | {Target.URL}

# Same rule as the console's parser, so a link the gateway builds is one the console opens.
# Dots alone are refused: fetch resolves `.` and `..` in a path to another route.
REF_ID = re.compile(r"^(?!\.+$)[A-Za-z0-9._-]{1,200}$")
URL_MAX = 512


@dataclass(frozen=True)
class Rule:
    priority: Priority
    ttl: int | None
    url_title: str
    # Tried in order; the first the sender has an id for wins.
    links: tuple[Target, ...]
    # A screen needing no id, for when no link matched; None opens the console's Home.
    fallback: Target | None = None


RULES: dict[Kind, Rule] = {
    Kind.QUESTION: Rule(Priority.NORMAL, None, "Answer it", (Target.JOB,)),
    Kind.PERMISSION: Rule(Priority.NORMAL, None, "Review it", (Target.JOB, Target.AUTOMATION)),
    Kind.MERGE_CALL: Rule(Priority.NORMAL, DAY, "Open merge call", (Target.CARD, Target.JOB)),
    Kind.RUN_FAILED: Rule(
        Priority.NORMAL,
        None,
        "Open run",
        (Target.JOB, Target.AUTOMATION, Target.DISPATCH),
    ),
    Kind.DEPLOY_FAILED: Rule(Priority.NORMAL, None, "Open run on GitHub", (Target.URL,)),
    Kind.STORAGE_WARNING: Rule(Priority.NORMAL, None, "Open storage", (), Target.STORAGE),
    Kind.SERVICE_DOWN: Rule(Priority.HIGH, None, "Open", (Target.SERVICE,), Target.HOMELAB),
    Kind.APP_DOWN: Rule(Priority.NORMAL, None, "Open", (Target.SERVICE,), Target.HOMELAB),
    Kind.RECOVERED: Rule(Priority.LOW, DAY, "Open", (Target.SERVICE,), Target.HOMELAB),
    Kind.SPEND_WARNING: Rule(Priority.NORMAL, None, "Open usage", (), Target.USAGE),
    Kind.JOB_DONE: Rule(Priority.LOW, DAY, "Open result", (Target.JOB,), Target.RUNS),
    Kind.ROUTINE: Rule(Priority.LOW, DAY, "Open", (Target.CARD, Target.JOB, Target.AUTOMATION)),
}


def known_kind(kind: str | None) -> Kind | None:
    """The Kind for a sender's string, or None when the gateway has no rule for it yet."""
    try:
        return Kind(kind) if kind else None
    except ValueError:
        return None


def check_refs(refs: dict[str, object]) -> dict[Target, str]:
    """Refs keyed by target, each id or URL checked. Raises ValueError on a bad one."""
    out: dict[Target, str] = {}
    for key, value in refs.items():
        if not isinstance(value, str):
            raise ValueError(f"ref {key!r} is not a string")
        try:
            target = Target(key)
        except ValueError:
            raise ValueError(f"unknown ref {key!r}") from None
        if target not in REF_TARGETS:
            raise ValueError(f"ref {key!r} takes no id")
        if target == Target.URL:
            if not value.startswith(("https://", "http://")) or len(value) > URL_MAX:
                raise ValueError(f"ref 'url' must be an http(s) URL of at most {URL_MAX} chars")
        elif not REF_ID.match(value):
            raise ValueError(f"ref {key!r} is not a valid id")
        out[target] = value
    return out


def _target_url(target: Target, ref: str | None) -> str:
    base = settings.console_url.rstrip("/")
    if target == Target.URL:
        return ref or base
    if target in _PAGE:
        return f"{base}/?page={_PAGE[target]}"
    return f"{base}/?open={_OPEN_KIND[target]}:{quote(ref or '', safe='')}"


def link(kind: Kind, refs: dict[Target, str]) -> str:
    """The console (or GitHub) URL a notice of this kind opens, given the ids its sender has."""
    rule = RULES[kind]
    for target in rule.links:
        if target in refs:
            return _target_url(target, refs[target])
    if rule.fallback:
        return _target_url(rule.fallback, None)
    return f"{settings.console_url.rstrip('/')}/"
