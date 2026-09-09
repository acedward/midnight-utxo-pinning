# Holds and pinned coins for Midnight wallets

**Status:** proposal, draft for review. Not yet submitted upstream.
**Target:** the [Midnight Wallet Specification](https://github.com/midnightntwrk/midnight-wallet/blob/main/docs/spec/Specification.md), section *State management*.
**Documents:** this README explains the proposal, how it works and what it enables. [SPECIFICATION.md](SPECIFICATION.md) holds the exact normative text and requirements. [upstream/](upstream/) contains the same text as a patch against the wallet specification.

## Summary

Midnight's Zswap was built for offers: a party builds a proven, purposefully unbalanced shielded offer, and whoever
holds that offer can balance it and complete the transaction, permissionlessly.

Wallets, however, can only reserve a coin for a transaction they will submit themselves. The reservation exists to
avoid the case where one coin is sent to two recipients: only the first transaction submitted is accepted, the other is
rejected.

Consider a bid. There are several equivalent items, you want to bid 100 tokens on each of them, and you only have 100
tokens. We want to enable this use case.

This proposal extends the internal wallet state so that a coin can be reused knowingly, and so that the wallet knows
when a coin can be reused and when it cannot. It adds no new coin state and no new transaction status: existing states
gain metadata, and wallets that ignore the metadata keep behaving correctly.

## The problem

The wallet specification reserves coins through *booking*: `spend` moves a coin from `Final` to `Booked`; when the
transaction settles the coin becomes `Spent`; when the wallet gives up (`discard_transaction`) the coin returns to
`Final`. Booking is binary and single-purpose. A bidding application needs three things it cannot provide:

| Need | Why booking fails |
|---|---|
| Bid on products A, B and C with the same 100 NIGHT; whichever bid the seller accepts first takes the funds | One coin can be booked for one transaction |
| Hand the offer to the marketplace and keep using the wallet | Once the offer leaves, the wallet must keep it pending forever (blocking the coin for everything) or discard it (un-booking the coin) |
| Stay safe while bids are out | An unrelated transfer, or another dApp's balancing request, can spend the coin and silently invalidate every bid |

And a privacy requirement: the marketplace that collected the bids may know they conflict, but no other application
should learn that funds are reserved, for what, or how many offers exist.

## Proposed changes

### 1. Metadata on existing states

| State | Metadata added | Meaning |
|---|---|---|
| `Booked` | booking record `{transaction?, hold?}` | `{transaction}` is today's booking. `{hold}` is a **pinned** coin: reserved by a hold, idle, counted only in the owning scope's `held` balance. `{hold, transaction}` is a pinned coin currently booked by a candidate being built |
| `Final` | `outstanding` list | transactions that could still consume the coin although the wallet no longer intends it to (released candidates, discarded but shared transactions). The coin **is available**; the wallet warns |
| `Pending` | `submitter` = wallet or scope | a scope-submitted pending transaction is a **candidate**: built by the wallet, handed off, never submitted or re-submitted by the wallet, excluded from the pending balance and from TTL discarding |
| `Rejected` | `reason`, validity bound | `discarded`, `released`, `invalidated`, `stale`, `expired`; keeps the bound while the transaction may still settle |

One edge is added that today's lifecycle lacks although it already happens in practice: `Rejected → Confirmed` on
`apply`, for a transaction the wallet gave up on that another party submits before its validity bound passes.

### 2. Holds

A **hold** is keyed by (**scope**, **id**).

- The **scope** says on whose behalf the coins are reserved. It is derived by the wallet, never supplied by the caller:
  the wallet user, or one connected application identified by its authenticated origin together with the wallet and
  network identifiers.
- The **id** is chosen by the application. It is a label, not a capability: unique only within its scope, shared
  freely by many coins and many transactions, never interpreted by the wallet.
- A hold has pinned coins (shielded coins, unshielded UTxOs and Dust outputs alike), the candidates built from them,
  and a policy: `mode` (`single` backs one live candidate, `multi` backs any number of siblings and rebuilds),
  `expires_at`, an optional `note`.

### 3. Authorization on `spend`

A build may carry an authorization with two optional fields:

- `use: X` — select coins pinned under (scope, X) first; if they do not cover the request, add unpinned coins and pin
  them under X as well.
- `mark: Y` — pin whatever this build selects under (scope, Y), creating the hold implicitly (mode `multi`) if needed.

Without an authorization, coin selection works exactly as today and never touches booked coins.

### 4. Three operations

- `hold(scope, id, selection, policy)` — reserve coins up front without building anything, or set a policy before the
  first build creates the hold implicitly.
- `detach_transaction(transaction)` — hand a built transaction to its hold's scope as a candidate: remember its
  nullifiers, commitments, expected coins and validity bound; mark the scope as submitter; drop the `transaction` field
  from the inputs' booking records so they stay booked by the hold.
- `release_hold(scope, id)` — end the hold: candidates become `Rejected(released)`, coins return to `Final` with
  `outstanding` entries and a warning.

`apply_transaction`, `rollback_last_transaction` and `discard_transaction` are amended to settle, invalidate and
restore candidates, and to attach `outstanding` entries where a discarded transaction may have left the wallet.

## How it works

### Bidding flow

```mermaid
sequenceDiagram
    autonumber
    actor U as User
    participant M as Marketplace dApp<br/>(scope = its origin)
    participant W as Wallet
    participant S as Settlement<br/>(marketplace backend)
    participant L as Ledger

    M->>W: build bid A (mark "round-7", payFees=false)
    W->>W: select coin X (150 NIGHT), pin under (M,"round-7")<br/>Booked{hold, transaction}
    W-->>U: confirm hold? (once, per wallet policy)
    U-->>W: yes
    W->>W: prove + bind offer A: +100 NIGHT, −1 TOKEN_A, change 50
    W->>W: detach A: Pending{submitter=M}, X = Booked{hold}
    W-->>M: offer A + validity bound

    M->>W: build bid B (use "round-7")
    W->>W: X selected again, prove, bind, detach
    W-->>M: offer B
    M->>W: build bid C (use "round-7")
    W-->>M: offer C

    U->>W: ordinary transfer 120 NIGHT
    W-->>U: insufficient funds (X is Booked, not eligible)

    M->>S: offers A, B, C (same nullifier: conflict group)
    S->>S: B wins. Merge B with the contract call, balance, pay Dust
    S->>L: submit merged transaction
    L-->>W: block with X's nullifier
    W->>W: X Spent. B Confirmed (TOKEN_B + change confirmed)<br/>A, C Rejected(invalidated). Hold closed
```

Another dApp asking the wallet for balances at any point before step 18 sees X neither as available nor as held. It cannot tell a
pinned coin from a spent one.

### Coin lifecycle with the metadata

```mermaid
stateDiagram-v2
    direction LR
    [*] --> Pending: spend | watch_for
    [*] --> Confirmed: apply(receive)
    Pending --> Confirmed: apply(receive)
    Confirmed --> Final: finalize
    Confirmed --> Pending: rollback

    state Booked {
        direction TB
        state "by transaction" as ByTx
        state "by hold (pinned)" as ByHold
        state "by hold and transaction" as Both
        ByHold --> Both: spend(use)
        Both --> ByHold: detach_transaction | discard
    }

    Final --> ByTx: spend
    Final --> ByHold: hold | spend(mark)
    ByHold --> Final: release_hold | expiry (+ outstanding)
    ByTx --> Final: discard (+ outstanding if shared)
    Booked --> Spent: apply
    Final --> Spent: apply (outstanding settled)
    Spent --> Booked: rollback
    Spent --> Final: rollback (outstanding)
```

The same diagram as PlantUML, together with the transaction lifecycle, is in [diagrams/](diagrams/).

### Cancelling

Today the wallet's only cancel is local: it un-books the coin and marks the transaction rejected. An offer that already
left the wallet stays valid to whoever holds it until its validity bound passes. If the recipient submits it after the
local cancel, today's wallet sees an unexplained spend.

The proposal keeps the user's intent and adds the missing memory:

```mermaid
flowchart LR
    R[release_hold] --> F["Final + outstanding: A, B, C<br/>available, warning shown"]
    F -->|validity bound passes| C1[warning clears]
    F -->|user spends the coin elsewhere| C2["nullifier consumed:<br/>A, B, C definitively cancelled"]
    F -->|marketplace settles B first| C3["coin Spent; B Rejected → Confirmed<br/>recorded as settled after release"]
```

Spending the coin is the only definitive cancellation; the wallet may offer release plus an immediate self-transfer as
a "cancel" convenience.

### Validity of a candidate

Every candidate has a bound after which the ledger refuses it. The wallet returns the bound with the candidate, and a
`multi` hold lets the application rebuild with `use` before it passes without prompting the user again.

| Inputs | Bound | Enforced by |
|---|---|---|
| Shielded coins, Dust | commitment-tree root retention: 3600 s hard-coded on ledger v8; `global_ttl` parameter on ledger v9 (code default 3600 s, deployed value to be confirmed on a node 2.x network) | ledger `past_roots` |
| Unshielded UTxOs, Dust spends | the intent's TTL, chosen at build time within the ledger's allowed margin | ledger intent TTL |
| Mixed | the minimum of the above | both |

A candidate past its bound can never settle, so it becomes `Rejected(stale)` or `Rejected(expired)` and needs no
`outstanding` entry.

## Use cases

**Bidding on several products with the same funds.** The flow above. The marketplace collects one offer per product,
sees the shared nullifier, and knows it may settle at most one of them. The user's other funds stay usable throughout.

**Token launches: whichever reaches its goal first takes the funds.** The user commits the same NIGHT to several new
tokens. Each candidate is an offer providing NIGHT and receiving a token the wallet has never held; the launch contract
mints the aggregate and absorbs the NIGHT in one merged, all-or-nothing transaction. The first launch to reach its goal
settles; the others are invalidated automatically.

**Deferred payment with a fixed ceiling.** A service asks the user to pre-authorize up to 50 NIGHT for a session. The
dApp calls `hold` with an amount, then builds and detaches the final charge later with `use`; the user's remaining
balance is spendable meanwhile, and the hold expires if nothing is charged.

**Order-book style swaps.** Offer files ([MIP-0005](https://github.com/midnightntwrk/midnight-improvement-proposals/blob/main/mips/mip-0005-offer-files.md))
and P2P swap discovery ([MIP-0006](https://github.com/midnightntwrk/midnight-improvement-proposals/blob/main/mips/mip-0006-p2p-atomic-swaps.md))
already let a wallet publish an offer for someone else to take. Holds give the publishing wallet a correct local model
of that offer: the coin is protected while the offer is live, the offer is recognized when taken, and cancellation has
the semantics MIP-0006 describes (spend the coin).

**Unshielded deferred transactions.** The same lifecycle covers a signed intent held back for later submission by a
service, expiring at its TTL.

## Privacy

- `available` excludes every pinned coin for every observer, including the owning scope; pinned coins are `Booked`.
- `held` is reported per scope: a dApp sees the amount pinned for it and nothing about other scopes' holds.
- All balances are computed by one function regardless of who owns the pins. A scope that does not own a hold cannot
  distinguish a pinned coin from a spent one.
- Hold identifiers resolve only within the requesting scope. A foreign id finds nothing, exactly as a never-used id.
- Candidates contain only ledger data; hold ids, notes, scope identity and the existence of other holds never appear
  in transactions, dApp-visible history, logs or errors.
- Inside the owning scope, shared nullifiers across candidates are visible on purpose: the marketplace must not merge
  two conflicting bids, or the ledger rejects the double spend and the whole guaranteed section with it.

## What this proposal does not do

- It locks nothing on chain. A pin is wallet policy; the only on-chain effect is the transaction that finally spends
  the coins.
- It adds no second execution tracker; settlement detection reuses the wallet's existing nullifier matching.
- It does not define dApp connector method signatures or origin authentication. Those belong to the connector and
  will be specified with the MIP that follows this review.

## Repository layout

| Path | Content |
|---|---|
| [README.md](README.md) | this document |
| [SPECIFICATION.md](SPECIFICATION.md) | normative text, requirements checklist, conformance scenarios |
| [upstream/midnight-wallet-specification.patch](upstream/midnight-wallet-specification.patch) | the proposal as a diff against `midnightntwrk/midnight-wallet` `docs/spec` at `6e1050e5` |
| [upstream/commits/](upstream/commits/) | the same change as individual commits |
| [diagrams/](diagrams/) | PlantUML sources and rendered SVGs of the coin and transaction lifecycles |

## Open points for reviewers

1. Is the hold `mode` (`single` | `multi`) the right granularity, or should the limit be a count?
2. Should the wallet expose its hold-duration cap and the network's root retention to dApps, and where?
3. Fee-paying candidates fix the Dust amount at build time; is "the settler covers the shortfall" acceptable?
4. Unshielded candidates carry random segment ids; is guidance needed on collisions when many users' bids merge?
5. A hold created implicitly by a `mark`/`use` build is `multi`; is that the right default, with `single` opt-in
   through `hold`?

## License

Apache-2.0. See [LICENSE](LICENSE).
