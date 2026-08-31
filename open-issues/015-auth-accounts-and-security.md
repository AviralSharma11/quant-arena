# Open Issue 015 — Authentication, Accounts, and Security

**Status:** closed by user
**Opened:** 2026-08-27
**Serves:** README.md §5 (secondary objective names security)
**Owner:** _unassigned_

---

## 1. The problem, and the right level of effort

This is a trading platform with accounts and balances, and nothing about it has been decided.
It is also a play-money system with a small user base, so the question is not "how do we
secure a bank" but **what is the threat model, and what does it actually justify?**

| Threat | Real here? | Consequence |
|---|---|---|
| Theft of real funds | **No** — no real money exists | — |
| Manipulating results or a future leaderboard | **Yes** | Competition integrity (Phase 2) is worthless without it |
| Denying service to other users through order spam | **Yes** | One bad client degrades the market for everyone |
| Credential reuse harm | **Yes** | Users reuse passwords. Weak storage harms them on *other* sites, regardless of this one's stakes |
| Obvious web vulnerabilities | **Yes** | Audience (a) reads this repository. Fundamentals done wrong are read as inexperience |

The last row sets the standard: **for this project, security is judged on whether the obvious
things are done correctly, not on whether anything sophisticated was built.** A repository with
plaintext passwords and a JWT in `localStorage` undermines every other engineering claim in it.

---

## 2. Sub-decision 15a — Session management

| Option | For | Against |
|---|---|---|
| **Session cookie** — `httpOnly`, `Secure`, `SameSite=Strict` | Immune to token theft by XSS, since JavaScript cannot read it. Revocable instantly. Server holds the state | Requires a session store |
| JWT in `localStorage` | Stateless; conventional in tutorials | **Readable by any XSS**; revocation requires a denylist, which reintroduces the state it was meant to avoid |
| JWT in a cookie | Not readable by XSS | The statelessness that motivated JWT is now unused; a session ID would do the same job more simply |

**Proposed: session cookies, with sessions stored in Redis.**

Two supporting points. First, Redis is already a required dependency, so the session store
costs nothing. Second — and worth defending explicitly — **JWT is the popular answer, not the
correct one for a single-backend application.** JWT solves stateless verification across
independent services. This system has one gateway and already maintains server-side state per
user. Choosing a session cookie and being able to explain *why* demonstrates more than
choosing JWT because it is common.

## 3. Sub-decision 15b — Password storage

**Proposed: Argon2id**, with parameters from current OWASP guidance, via a library. Effectively
zero implementation hours, and the current recommendation. bcrypt remains acceptable; anything
unsalted, or any general-purpose hash such as SHA-256, is not.

## 4. Sub-decision 15c — WebSocket authentication

The private user stream (Open Issue 006 sub-decision 7c) carries balances and fills, so the
connection must be authenticated.

| Option | Note |
|---|---|
| **Session cookie sent on the WebSocket handshake** | Works directly for same-origin. Simplest |
| Short-lived ticket fetched over REST, presented on connect | Standard for cross-origin; an extra round trip |

**Proposed: the cookie on handshake**, since the client is same-origin. If the frontend is ever
served from a separate origin, the ticket approach becomes necessary.

## 5. Sub-decision 15d — Rate limiting, which is also exchange realism

This is the one place where security work and domain realism are the same work.

Real exchanges impose **message throttles** on participants — a maximum order rate per session,
with different tiers for different participant classes. Implementing that is simultaneously a
denial-of-service defence and a faithful piece of exchange behaviour.

**Originally proposed: per-account order rate limits by account tier** — approximately 10
orders/sec for retail and 1000/sec for designated market makers, mirroring the privileges
granted in Open Issue 005. **This was superseded by the decision in §11.1.**

Breaching the limit produces an explicit rejection with a reason, not a silent drop — a client
must be able to tell that it was throttled. This also mirrors the halt state from Open Issue
003 §8.5: failures are visible rather than silent.

## 6. Sub-decision 15e — Input validation is a correctness problem too

The engine takes `int64` prices and quantities (Open Issue 002). A client sending extreme
values could overflow intermediate arithmetic — `price × quantity` in the reservation
calculation is the obvious candidate — producing wrong balances rather than an error.

**Proposed: the gateway validates every order against configured bounds before sequencing** —
quantity within `[1, max_order_qty]`, price within `[min_tick, max_price]` and on the tick
grid, symbol in the configured list. These bounds live in the shared configuration file (Open
Issue 007 sub-decision 8d).

This overlaps with the price-band mechanism already adopted for market orders (Open Issue 004
sub-decision 4d) and should reuse it. It also becomes a property test: **no input sequence,
however hostile, may drive any balance to a wrong value or any invariant to failure.** That
turns validation from a defensive habit into a checked claim.

## 7. Sub-decision 15f — What is deliberately not done

| Excluded | Reason |
|---|---|
| Two-factor authentication | No real funds at risk |
| OAuth / social login | An integration, not a demonstration of anything |
| CAPTCHA, WAF, DDoS protection | Infrastructure concerns, not application ones |
| Secrets management beyond environment variables | Appropriate at this scale |
| Penetration testing | Phase 2 at the earliest |

Email verification appeared on this list in the original draft and has since been moved into
Phase 1 — see §11.2.

**The substantive security work in this project is Goal 4 — sandboxed execution of
user-submitted strategies — and it is Phase 2** (Open Issue 017). Phase 1's obligation is to
get the fundamentals right and to state the threat model, which §1 does.

---

## 8. Cost summary

| Item | Hours |
|---|---|
| Registration, login, logout; Argon2id; Redis-backed sessions | 6 |
| WebSocket handshake authentication | 2 |
| Rate limiting | 4 |
| Order input validation and bounds | 3 |
| Email verification, non-blocking (§11.2) | 4 |
| **Total** | **19** |

Absorbed within the gateway and frontend budgets.

---

## 9. Questions raised (answers recorded in §11)

1. Session cookies rather than JWT?
2. Are the rate-limit tiers right?
3. Email verification in Phase 1, or Phase 1.5?
4. Is the excluded list acceptable?

---

## 10. Note on this file

This file was accidentally overwritten on 2026-08-27 by a stray copy containing only the
amendment section, and was reconstructed from the original content. Sections 1–9 are the
original draft, with §5 and §7 annotated where later decisions superseded them.


---

## 11. Amendment 2026-08-27 — answers recorded

- **15a — session cookies confirmed**, stored in Redis. JWT rejected, with the reasoning in §2
  recorded so the choice can be defended rather than merely stated.
- **15d — the rate limit is 1000 orders/sec.** See §11.1.
- **15c(3) — email verification is in Phase 1.** See §11.2.

### 11.1 Rate limit set to 1000/sec — consequence recorded

The two-tier proposal (10/sec retail, 1000/sec market maker) is replaced by **1000 orders/sec
for all accounts**.

**What this changes.** With a single tier, rate limiting stops functioning as denial-of-service
protection — one retail account can now saturate the gateway on its own — and becomes purely an
overflow and runaway-client guard. The exchange-realism argument for tiering (real venues
throttle by participant class) is also set aside.

**Why it is nonetheless reasonable here.** The user base is small and known, there is no real
money at stake, and 1000/sec is still a hard ceiling rather than no limit at all. It also
removes a Phase 1 obstacle: a user who wants to run their own client against the API is not
throttled to a level that makes it pointless.

Tiering remains available later at negligible cost — the limit is read from the shared
configuration file (Open Issue 007 sub-decision 8d), so reintroducing tiers is a configuration
change plus a lookup on account type. **Recommended trigger for revisiting:** the first time a
single client degrades the market for others during a load test.

### 11.2 Email verification in Phase 1 — proposed as non-blocking

Verification introduces a mail dependency (SMTP or a provider such as Resend or SendGrid),
roughly 4 hours, and a new failure mode: mail is delayed or filtered and the user cannot
proceed.

**That failure mode lands directly on the demonstration.** Beat 1 of the five-minute demo (Open
Issue 013 §7) is a user opening the application. "Please check your email, it should arrive
shortly" is a poor beat, and it depends on infrastructure outside the system's control.

**Proposed: verification is sent and recorded, but does not gate trading in Phase 1.** A new
account receives its capital and can trade immediately; the verification link marks the address
confirmed. This establishes the flow, collects verified addresses, and adds a real feature —
without placing a third-party mail service on the critical path of the demonstration.

Making it blocking is a one-line change in Phase 1.5, once real users arrive and the reason for
verification (preventing throwaway accounts before competitions) actually applies.

### 11.3 What the excluded list in §7 is

It is the list of security features **deliberately not built in Phase 1**, each recorded with
its reason.

Its purpose is the same as the three-screen limit in Open Issue 014 §11.1: security work, like
interface work, has no natural stopping point, and each individual addition sounds
unobjectionable. Writing the exclusions down converts each one from an oversight into a
decision — so that when someone asks in week 4 why there is no two-factor authentication, the
answer and its reasoning already exist.

| Excluded | Reason |
|---|---|
| Two-factor authentication | No real funds at risk |
| OAuth / social login | An integration, not a demonstration of anything |
| CAPTCHA, WAF, DDoS protection | Infrastructure concerns rather than application ones |
| Secrets management beyond environment variables | Appropriate at this scale |
| Penetration testing | Phase 2 at the earliest |

Email verification has been **removed from this list** and moved into Phase 1 per §11.2.

## 12. Decision log

| Date | Status | Note |
|---|---|---|
| 2026-08-27 | OPEN | Threat model stated; sub-decisions 15a–15f proposed |
| 2026-08-27 | AMENDED | Session cookies confirmed; single 1000/sec rate limit with consequences recorded; email verification moved into Phase 1 as non-blocking. Still not final. |

---

## Amendment 2026-08-28 — simplification pass (Open Issue 018)

Email verification moves to **Phase 2**. Confirmed non-blocking in Phase 1, it therefore had no behaviour — four hours, a mail-provider dependency and a new failure mode in exchange for nothing. It becomes meaningful once competitions give throwaway accounts a reason to be prevented. Everything else in this issue is unaffected.
