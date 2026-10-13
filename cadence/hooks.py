"""The opening line of a LinkedIn post. LinkedIn cuts it off behind '...see more' after roughly
140 to 210 characters, so the first line is most of the post's reach.

Five properties, each 0-100:
  FRONTLOAD    the point comes early, and there is no greeting or preamble
  SPECIFICITY  it names something concrete: a number, time, tool, amount or name
  ADDRESS      it speaks to the reader, or is a first-person story
  STAKES       something was lost, broken, cost, or at risk
  CURIOSITY    it opens a loop the reader wants closed

Formula names and templates are written for this project.
"""
from __future__ import annotations

import re

from .detect import CONCRETE, CAP_NAME

FORMULAS = [
    ("Confession", "I {mistake} and {consequence}.", "I broke production on a Friday and spent the weekend rolling back.", r"^I (?:broke|shipped|deleted|forgot|got|lost|failed|ignored)\b"),
    ("Nobody Tells You", "Nobody tells you that {hidden truth}.", "Nobody tells you that the on-call pager is the real job interview.", r"\bnobody (?:tells|told) you\b"),
    ("Contrarian Flip", "Everyone says {belief}. {reason it is wrong}.", "Everyone says write more tests. Our flaky tests were the problem.", r"\beveryone (?:says|thinks|tells)\b"),
    ("Costly Lesson", "That {mistake} cost me {number}.", "That one missing env var cost us 40 minutes and a customer.", r"\bcost (?:me|us)\b"),
    ("The Number", "{number} {things} that {outcome}.", "3 config changes that cut our build from 14 minutes to 4.", r"^\d+\s+\w+"),
    ("Before and After", "Last {period}, {bad}. Now, {good}.", "Last year our deploys took two hours. Now they take nine minutes.", r"\blast (?:year|month|week|quarter)\b.*\bnow\b"),
    ("The Reversal", "I used to think {old belief}. Now {new belief}.", "I used to think more monitoring meant fewer incidents. It meant more noise.", r"^I used to (?:think|believe)\b"),
    ("Hard Truth", "Most {thing} fails because {reason}.", "Most migrations fail because nobody wrote down the rollback.", r"^most \w+.*\bbecause\b"),
    ("The Question", "{question about a real tension}?", "Why does every team say it wants fewer meetings and then book five more?", r"\?\s*$"),
    ("Overheard", "My {person} said {quote}. I {reaction}.", "My manager said we were moving fast. I checked the incident log.", r"^my \w+ (?:said|asked|told)\b"),
    ("Tool Verdict", "I stopped using {tool} after {reason}.", "I stopped using a monorepo after the third week of merge conflicts.", r"\bi (?:stopped|quit|dropped) using\b"),
    ("The Admission", "I still don't know how to {thing}.", "I still don't know how to explain Kubernetes networking without a diagram.", r"\bi (?:still )?don'?t know\b"),
    ("Specific Scene", "It was {time} when {event}.", "It was 2am when the cache started returning yesterday's prices.", r"^it was (?:\d|at|late|early)"),
    ("Unpopular Opinion", "Unpopular opinion: {claim}.", "Unpopular opinion: most code reviews should take under ten minutes.", r"\bunpopular opinion\b"),
    ("Counter-Example", "Everyone uses {thing}. We removed it and {result}.", "Everyone uses feature flags. We removed ours and deploys got calmer.", r"\bwe (?:removed|dropped|killed|deleted)\b"),
    ("Surprising Result", "We tried {change} for {period}. {result}.", "We tried no standups for a month. Velocity went up and nobody noticed.", r"\bwe tried\b"),
    ("Myth Bust", "{Popular belief} is not why {bad outcome} is happening.", "Slow CI is not why our releases keep slipping.", r"\bis not why\b"),
    ("Versus", "{Expensive path} vs {cheap path}. {Verdict}.", "A two-week audit vs one afternoon with the logs. The afternoon won.", r"\bvs\.?\s"),
    ("Time Anchor", "{Task} used to take {long time}. Now it takes {short time}.", "Writing release notes used to take a day. Now it takes twenty minutes.", r"\bused to take\b"),
    ("Pattern Interrupt", "{One to three words}.", "I was wrong.", r"^\S+(?: \S+){0,2}\.$"),
    ("Walk Away", "I {removed/quit/dropped} {valuable thing}. {Result}.", "I removed our most-used internal tool last month. Incidents fell by a third.", r"\bi (?:removed|quit|dropped|cancelled|deleted|fired)\b"),
]

DEALBREAKERS = [
    (r"^(?:hi|hey|hello|greetings|good (?:morning|evening))\b", "Greeting. Nobody came to the feed to be greeted."),
    (r"^(?:excited|thrilled|proud|happy|honou?red|humbled)\b", "Opens with a feeling about yourself, not the point."),
    (r"^(?:in this|today i|today,? we)\b", "Preamble. Say the point first."),
    (r"\bstop scrolling\b", "Asking for attention proves you have not earned it."),
]

CURIOSITY = re.compile(r"^(?:nobody|most|the reason|why|the worst|the hard part|turns out|it turned out|i was wrong)\b|\?|\buntil\b|\bturned out\b|\bnobody (?:tells|told)\b", re.I)
STAKES = re.compile(r"\b(?:lost|broke|broken|cost|costs|mistake|failed|wrong|never|hours?|days?|years?|fired|outage|incident|\$|₹|money|deleted|crashed)\b", re.I)
YOU = re.compile(r"\b(?:you|your|you're|anyone|if you)\b", re.I)
FIRST = re.compile(r"\b(?:I|my|me|we|our)\b")


def first_line(text: str) -> str:
    t = text.strip()
    m = re.split(r"(?<=[.!?])\s+|\n", t, maxsplit=1)
    return m[0].strip() if m else ""


def score(text: str) -> dict:
    line = first_line(text)
    words = len(line.split())
    props: dict[str, int] = {}
    dealbreaker = None
    for pat, why in DEALBREAKERS:
        if re.search(pat, line, re.I):
            dealbreaker = why
            break
    props["frontload"] = max(0, min(100, 100 - max(0, words - 14) * 6)) if not dealbreaker else 0
    markers = len(CONCRETE.findall(line)) + len(CAP_NAME.findall(line))
    props["specificity"] = min(100, markers * 50)
    if YOU.search(line):
        props["address"] = 100
    elif FIRST.search(line):
        props["address"] = 70
    else:
        props["address"] = 20
    props["stakes"] = 100 if STAKES.search(line) else 30
    props["curiosity"] = 100 if CURIOSITY.search(line) else 40
    total = round(sum(props.values()) / len(props))
    if dealbreaker:
        total = min(total, 30)
    grade = "STRONG" if total >= 70 else "OK" if total >= 50 else "WEAK"
    weakest = min(props, key=props.get)
    return {"line": line, "score": total, "grade": grade, "props": props, "weakest": weakest,
            "dealbreaker": dealbreaker, "formula": classify(text)}


def classify(text: str) -> str:
    """Which formula the opening line most resembles, or '' if none."""
    line = first_line(text).lower()
    for name, _, _, pat in FORMULAS:
        if re.search(pat, line, re.I):
            return name
    return ""


def rank(lines: list[str]) -> list[dict]:
    return sorted((dict(score(l), text=l) for l in lines), key=lambda d: -d["score"])
