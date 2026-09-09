# Holds and pinned coins — normative specification

This document is the exact text proposed for the Midnight Wallet Specification, assembled by `tools/assemble-spec.py`
from the patch in [upstream/](upstream/) so that the two never diverge, followed by a requirements checklist and
conformance scenarios. Section numbers refer to this document; the patch places the same text inside the wallet
specification's *State management*, *Building standard transactions* and *Transaction submission* sections.

The key words MUST, MUST NOT, SHOULD, SHOULD NOT and MAY are to be interpreted as described in RFC 2119.

## 1. Balances

Insert after the three existing balances (available, pending, total):

When holds are supported (see [Holds and pinned coins](#holds-and-pinned-coins)) a fourth balance exists, and the
definitions above are refined as follows:

- available - unchanged: derived from final unspent coins. Pinned coins are *booked* (by a hold) and therefore not
  available, for every observer including the scope that owns the hold, because they can only be spent through an
  explicitly authorized build. Final coins that still back an outstanding transaction (see
  [Coin and transaction lifecycle with holds](#coin-and-transaction-lifecycle-with-holds)) **are** available; the wallet
  warns about them but does not withhold them
- held - derived from coins booked by holds; it is reported **per scope**: an observer sees only the held balance of
  the holds its own scope owns, the wallet user sees the held balance of all holds. The breakdown by identifier is
  available to the owning scope through `list_holds`
- pending - as above, except that outputs expected from *candidate* transactions (see below) are not counted, because
  candidates in one hold are usually mutually exclusive and at most one of them will settle
- total - available + pending + held (for the wallet user); for a scope it is available + pending + held of that scope

A consequence of the definitions is that a scope which does not own a hold cannot distinguish a pinned coin from a
spent one: both are simply absent from the available balance it is shown.

## 2. Holds and pinned coins


The booking described above is deliberately narrow: a coin is booked for exactly one transaction, and it is released
the moment that transaction is discarded. This is sufficient when the wallet is the party that submits the
transaction. It is not sufficient for a growing class of interactions where the wallet **builds** a transaction but
somebody else **submits** it, possibly much later and possibly merged with other transactions - the swap and
order-book style workflows Zswap was designed for. Two concrete examples:

- a marketplace where the user bids on several products with the same funds; the marketplace settles whichever bid is
  accepted first, and the other bids must simply become invalid;
- a launch platform where the user commits funds to several new tokens at once; whichever token reaches its goal
  first executes the user's offer and takes the funds, the others never execute.

In both cases the user wants to construct several unbalanced, proven and bound shielded offers **from the same
coins**, hand them to the application, and be sure that:

1. an unrelated transfer made in the meantime (from the same wallet, or requested by another application) does not
   consume those coins and thereby silently invalidate every offer;
2. when one offer settles on chain, the wallet recognizes it, marks the coins spent and the sibling offers as
   invalidated;
3. no application other than the one the offers were built for learns that the coins are reserved, what they are
   reserved for, or how many offers exist.

A **hold** is the wallet-local construct that provides these guarantees. It never touches the ledger: pinning is purely
a wallet policy on coin selection, and the only on-chain effect ever produced is the transaction that finally spends
the coins.

### Scopes

A hold is owned by a **scope**. A scope identifies *on whose behalf* the wallet reserved the coins and is derived by
the wallet itself - it MUST NOT be supplied or overridable by the requesting party. The wallet defines at least:

- the wallet user's own scope (`self`), used for holds created through the wallet's own user interface;
- one scope per connected application, keyed by the application's authenticated identity together with the wallet
  identifier and network identifier. In a browser deployment the authenticated identity is the exact web origin
  (scheme, host and port) of the page the connection was granted to.

Scopes are the privacy boundary of holds: everything a hold contains is visible to its owning scope and to the wallet
user, and to nobody else (see [Privacy of holds](#privacy-of-holds)).

### Hold structure

A hold is keyed by the pair (owning scope, `id`):

- the **owning scope**, derived by the wallet
- the **`id`** - an identifier chosen by the requesting scope. It is a label, not a capability: it is unique only within
  its scope (two scopes using the same text have two unrelated holds), it need not be unique per coin or per
  transaction (many coins may be pinned under one `id`, and many transactions may be built with it), and it is never
  interpreted by the wallet
- a policy:
  - `expires_at` - wall-clock instant after which a reservation made with `hold` lapses; the wallet MAY cap it
  - an optional free `note`, stored, never interpreted, never leaving the owning scope
- creation time and, for auditing by the user, the identity that requested it

The coins of a hold are not listed in the hold; they are found through their booking records (see the lifecycle
below). A **pinned** coin is a `Booked` coin whose booking record carries at least one *reference* under some
(scope, `id`). A reference is what keeps a pin alive:

- a `candidate` reference - one live candidate built from the coin under that `id`, holding the candidate's identifying
  components, its validity bound and, optionally, the coins it would create for the wallet
- a `reservation` reference - created by `hold`, holding the hold's `expires_at` as its validity bound

The number of references under (scope, `id`) is the *count* of that identifier on the coin. A coin MAY carry
references under several identifiers of the same scope (it backs bids in several rounds at once), and MAY still
carry a `transaction` booking for a wallet-submitted transaction. From the ledger's point of view a pinned coin is an
ordinary spendable coin; what changes is only the wallet's willingness to select it: an ordinary spend MUST never
select a booked coin, and a build that says `use: X` MAY select only coins pinned under (scope, `X`) plus, if it
needs more value, ordinary unpinned coins.

### Candidate transactions

A **candidate** is a transaction the wallet built from a hold and handed off to the hold's scope instead of submitting
it. In its simplest form it is a pure guaranteed shielded offer - proven, bound, unbalanced on purpose; when it spends
unshielded UTxOs or pays fees it also carries an intent (with the unshielded offer and/or Dust spend), sealed by the
wallet's signatures and mergeable as a whole. The wallet keeps, for every candidate:

- its identifying components: input nullifiers, output coin commitments, and the value commitments of its shielded
  offers (transaction hashes are not stable, see [Standard transactions](#standard-transactions))
- the coins it would create for the wallet (change and expected received tokens), so that they can be recognized when
  the candidate settles; they are tracked like `watch_for` coins but reported separately (see Balances)
- the validity bound the wallet can compute locally:
  - for shielded inputs, the commitment-tree root the input proofs were built against; the ledger accepts a root only
    while it is within its root retention window, so a candidate is *stale* once that window has passed and must be
    rebuilt from the hold to remain usable. The window is a property of the deployed ledger and MUST be read, not
    assumed: ledger v8 hard-codes 3600 seconds, ledger v9 takes it from the `global_ttl` ledger parameter, whose
    deployed value is expected to be much longer (days) but is not confirmed until a network running ledger v9 exists.
    The window is fixed for a network (changing it is a hard fork), so a scope can compute the bound itself
  - for Dust spends, the same root retention applies to the Dust commitment and generation trees, and the spend is
    additionally bound to the timestamp it was built with
  - for unshielded inputs, the TTL of the intent that carries them (chosen at build time, within the margin the ledger
    allows ahead of the current block); an intent past its TTL can never be applied. A candidate with intents has the
    smallest intent TTL as one of its bounds

  The candidate's validity bound is the minimum of the bounds that apply to it. Every bound is enforced by the ledger,
  so a candidate past its bound can never settle: the wallet marks it `Rejected(stale)` (root retention) or
  `Rejected(expired)` (TTL) and needs no `outstanding` entry for it.
- its status, which is the ordinary transaction status with extra metadata: *live* is `Pending` with the scope as
  submitter; *settled* is `Confirmed`; *detached* (dropped by the scope or the user, but possibly still valid),
  *invalidated* (one of its inputs was spent by another transaction, e.g. a sibling candidate), *stale* (root
  retention passed) and *expired* (intent TTL passed) are `Rejected` with that reason.

Candidates in one hold that share an input nullifier form a **conflict group**: at most one of them can ever settle.
This is exactly the property the bidding use cases rely on, and it is visible to the owning scope, which needs it in
order not to merge two conflicting candidates into one settlement (the ledger would reject the double spend and the
whole guaranteed section with it).

### Coin and transaction lifecycle with holds

Holds add **no new coin state and no new transaction status**. They add metadata to existing ones, so that a wallet
which does not implement holds still interprets every state correctly (a booked coin is unavailable, a final coin is
available, a pending transaction is in flight). In language-neutral form:

```text
Coin
  state:        Pending | Confirmed | Final | Booked | Spent
  booking:      BookingRecord      -- present while Booked
  outstanding:  [Outstanding]      -- may be non-empty in any state; matters while Final

BookingRecord
  transaction?: TxRef              -- the wallet-submitted transaction that booked the coin (today's booking)
  holds:        { (scope, id) -> Set<Reference> }   -- pins; the count of X is |holds[(scope, X)]|

Reference
  kind:         candidate | reservation
  components:   { nullifiers, valueCommitments, outputCommitments }   -- identifies the candidate on chain
  validUntil:   Timestamp          -- root retention or intent TTL; for a reservation, the hold's expires_at
  expected?:    [coin]             -- change and received coins (self outputs can also be rediscovered by
                                   -- decrypting their ciphertext when the transaction lands)

Outstanding                        -- memory of a dropped candidate that could still consume the coin
  components, validUntil, scope, id

Transaction
  status:       Pending | Confirmed | Final | Rejected
  submitter:    wallet | scope     -- on Pending
  reason:       discarded | detached | invalidated | stale | expired   -- on Rejected
```

Invariants:

- A coin is `Booked` if and only if `transaction` is set or some reference set is non-empty. When the last of them
  goes, the coin returns to `Final`. `spent -> booked` on `rollback` restores the record verbatim.
- A build with `use: X` (and `mark: Y`, defaulting to `X`) adds one `candidate` reference under (scope, `Y`) to
  every coin it selects - coins found under `X` and coins it had to add alike - then hands the transaction to the
  scope; the transaction booking taken during the build is released as part of the handoff. `mark` is how a coin
  already pinned under one identifier is knowingly pinned under a second one (`use: r7, mark: r8`: bid with round-7's
  coins in round 8). The candidate is `Pending` with the scope as submitter: excluded from
  the wallet's submission and re-submission loop, from the pending balance (its expected coins are tracked but not
  counted) and from TTL-based discarding.
- `hold` adds one `reservation` reference with `validUntil = expires_at`; an unused reservation keeps the pin until it
  expires and then releases itself.
- `detach_transaction(transaction)` removes that candidate's reference from every coin carrying it; the candidate
  becomes `Rejected(detached)` and, if its validity bound has not passed, an `Outstanding` entry on each of those
  coins. `detach_transaction({id})` does this for every reference under (scope, `id`) and removes the hold's policy.
  With `force: true` no `Outstanding` entry is made: the caller asserts the candidate never left its control. Both are
  idempotent.
- A reference whose `validUntil` has passed is pruned on every read: the candidate becomes `Rejected(stale)` or
  `Rejected(expired)`, with no `Outstanding` entry, because the ledger can no longer apply it. A stale candidate
  therefore never keeps a coin pinned.
- `Outstanding` entries are kept regardless of the coin's state and dropped when their bound passes or the coin is
  spent. The coin **is available** while `Final`; the wallet MUST show a warning while entries remain, and MAY prefer
  such coins in ordinary selection, because spending them is what actually cancels the outstanding transactions.
- `discard_transaction` of a wallet-submitted transaction removes the `transaction` field and, if the transaction may
  have left the wallet and its bound has not passed, adds an `Outstanding` entry. Pins are untouched.
- `apply` of a transaction carrying the coin's nullifier moves the coin to `Spent`. The reference or `Outstanding`
  entry whose components match the transaction is the settled one: its candidate becomes `Confirmed` (from `Pending`,
  or from `Rejected` - the one transition added to today's lifecycle, describing what already happens when a
  transaction the wallet gave up on is submitted by somebody else). Every other reference on the coin becomes
  `Rejected(invalidated)`.

The two lifecycle figures show the metadata as sub-states of `Booked` and as notes on `Final`, `Pending` and
`Rejected`.

### Privacy of holds

Holds exist to let one application coordinate mutually exclusive offers without telling any other application that the
coordination is happening. The wallet MUST therefore guarantee:

1. Holds, their metadata, their candidates and their conflict groups are retrievable only by the owning scope and the
   wallet user.
2. Hold identifiers resolve only within the requesting scope. A request from scope `A` that names an `id` used by
   scope `B` either finds `A`'s own hold of that name or finds nothing, with exactly the result a never-used `id` would
   produce - same error, same shape, same timing class.
3. Every balance the wallet reports to a scope is computed by the same function regardless of who owns the pins;
   the only information a non-owning scope can obtain is that the available balance is lower, which is
   indistinguishable from an ordinary spend.
4. Candidates handed to a scope contain only ledger data. Hold identifiers, notes, scope identity and the
   existence of other holds MUST NOT be embedded in transactions, offer files, transaction history entries exposed to
   applications, logs or error messages.
5. The wallet user interface SHOULD show, for every hold, the scope (origin) that owns it, so that the user can audit
   and release holds regardless of what the owning application does.

Note that the ledger data inside a candidate reveals its input nullifiers to whoever receives it. This is inherent to
Zswap offers and is limited to the scope the candidate was built for; it is also what allows that scope to detect
conflicts between its own candidates.

## 3. Operations

### 3.1 `spend` — authorization

Insert into the existing `spend` operation, before its step list:

The operation takes an optional `authorization` from the requesting scope: `use: X`, and optionally `mark: Y`
(defaulting to `X`). Resolved within the caller's scope:

1. If no hold (scope, `X`) exists, create it implicitly with the wallet's default policy, and select coins with
   ordinary selection from unpinned final coins.
2. If it exists, select coins pinned under (scope, `X`) first; if they do not cover the request, add unpinned final
   coins. How many candidates an identifier backs is the scope's choice: a one-shot candidate simply uses an
   identifier that is never reused.
   Every selected coin, found or added, is pinned under (scope, `Y`). With the default `Y = X` this simply grows the
   count of `X`; with `mark: r8` on `use: r7`, round-7's coins knowingly back a round-8 bid as well.
3. Coins pinned only under other identifiers are never taken, even within the same scope: taking a coin reserved for
   one round into a bid of another round would create a conflict group the scope did not ask for. A scope that wants
   that shares the identifier.
4. Pinning means adding this build's `candidate` reference under (scope, `Y`) to the coin's booking record; the count
   of `Y` on the coin grows by one. Dust is not a special case: if the build pays fees, Dust outputs pinned under `X` are used
   first, and any unpinned Dust output it needs is pinned like any other coin.
5. When the transaction is built (proven, signed where needed, bound), hand it to the scope together with its validity
   bound and release the transaction booking taken during the build. The transaction is `Pending` with the scope as
   submitter; the wallet does not submit it.

Without an authorization the spend is *ordinary* and works exactly as today: booked coins, pinned or not, are never
eligible, and if the unpinned funds are insufficient the operation fails with an insufficient-funds error that does not
reveal whether pinned funds would have covered the shortage. Coins with `outstanding` entries are ordinary final coins;
a wallet MAY prefer them, since spending them cancels the outstanding transactions. The wallet MUST verify that an
identifier resolves within the requesting scope only (an `id` never resolves into another scope).

Append after the step list:

Selection, pinning and booking MUST happen in one atomic step, so that two concurrent builds cannot book the same
coin at the same time (they may, deliberately, pin it under the same identifier one after the other).

### 3.2 New operations

#### `hold`

Reserves coins under an identifier without building a transaction (see [Holds and pinned coins](#holds-and-pinned-coins))
- to commit an amount up front, or to set a policy (expiry, note) before the first build with `use` would create the
hold implicitly. Parameters:

- the requesting scope, derived by the wallet
- the `id` chosen by the scope; if a hold with that `id` already exists in the scope, the selected coins are added to it
- a selection: either explicit references to own final coins, or a map of token type to amount, in which case the
  wallet selects final, unpinned coins covering each amount using its ordinary coin selection
- a policy: `expires_at`, optional `note`

Steps:

1. Resolve the selection to a set of own **final, unpinned** coins; fail if it cannot be satisfied (the error MUST NOT
   reveal whether coins pinned under other identifiers would have satisfied it)
2. Validate the policy against wallet limits (maximum duration, maximum number of holds per scope, metadata size)
3. Atomically create or extend the hold, add a `reservation` reference with `validUntil = expires_at` to each coin
   (setting it to `Booked` if it was `Final`), and persist

Holds MUST be persisted with the rest of the wallet state and MUST be restored before any spend is allowed after a
restart. If the persisted hold state cannot be read or validated, the wallet MUST refuse ordinary spends rather than
treat the coins as unpinned.

A wallet MAY require user confirmation for creating a hold, exactly as it may for any spend; a hold is a commitment of
funds and SHOULD be presented as such (amount, token type, requesting scope, expiry). Confirmation is given once, at
creation, and covers every later build with the same identifier - the wallet does not prompt again when the scope
rebuilds a stale candidate or builds a sibling.

#### `detach_transaction`

Drops candidates. It is the counterpart of `discard_transaction` for transactions the wallet did not intend to submit.
Two forms, both resolved within the requesting scope (the wallet user may drop anything):

- `detach_transaction(transaction, {force?})` - drop one candidate. Identify it by its components (the bytes handed
  back may have been merged with others). Steps:
  1. Remove the candidate's reference from every coin carrying it
  2. Mark the candidate `Rejected(detached)`; if its validity bound has not passed and `force` is not set, add an
     `outstanding` entry for it to each of those coins. `force: true` is the caller's assertion that the candidate was
     never made public (never sent, never published), so no memory of it is needed; the wallet MAY record the
     assertion in history and MAY require the wallet user's confirmation when it comes from a scope
  3. Any coin whose booking record is now empty returns to `Final`
  4. Persist atomically. Dropping a candidate that is not live is a no-op.
- `detach_transaction({id}, {force?})` - drop everything under (scope, `id`): every candidate reference, the
  reservation if any, and the hold's policy, with the same per-candidate effects (`force` applies to all of them).
  Dropping an unknown identifier is a no-op.

Dropping does not revoke: a candidate already handed out may still settle until its validity bound passes or its coins
are spent. The coins are available again as soon as nothing else pins them, and the `outstanding` warning is what tells
the user. Spending the coins is the only definitive cancellation; ordinary coin selection MAY prefer coins with
`outstanding` entries, and the wallet MAY offer a "cancel" convenience that drops the candidates and self-transfers
those coins together.

Expiry needs no operation: references are pruned when their validity bound passes, so a hold whose reservation lapsed
and whose candidates all went stale releases its coins by itself. Expiry is evaluated against the wallet clock and
SHOULD include the same latency margin used for TTL handling.

#### `list_holds`

Reports to the requesting scope what the wallet keeps pinned for it, so that an application can show the user what it
has reserved and decide whether to reuse an identifier. The scope is derived by the wallet, as for every other
operation; the wallet user's own scope sees every hold. The report contains, for each identifier of the scope:

- the amount pinned under it, per token type (the sum of the coins carrying at least one reference under the
  identifier; a coin pinned under several identifiers is counted under each)
- the number of live candidates, and, for each, its identifying components as they appear in the transaction the scope
  already holds (value commitments, nullifiers) and its validity bound; nothing the scope does not already know
- the reservation's `expires_at`, if there is one
- the number of `outstanding` entries the scope's dropped candidates left on coins, with their validity bounds

The report MUST NOT include coin identifiers, nonces, Merkle indices or any other data the scope did not receive with
its candidates, and MUST NOT include anything about other scopes' holds; a scope with no holds receives an empty
report, indistinguishable from a wallet that never had any.

### 3.3 Amendments to existing operations

**`apply_transaction`, steps to apply a shielded offer, step 3 becomes:**

3. Book coins, whose nullifiers match the ones present in offer inputs. If a matched coin carries references or
   `outstanding` entries:
   1. Find the reference or entry whose identifying components (value commitments, output commitments) are present in
      the transaction; if one is found, mark its candidate `Confirmed` (from `Pending` or from `Rejected`) and confirm
      the coins it was expected to create
   2. Mark the candidate of every other reference on the coin `Rejected(invalidated)`; drop the coin's references and
      entries (the record is kept aside for `rollback`)
   3. If no coin carries a reference under a hold any more, drop the hold's policy

**`apply_transaction`, steps to apply an unshielded offer, step 1 becomes:**

1. Book coins spent in the inputs; for coins carrying references or `outstanding` entries apply the same settlement
   rules as for shielded inputs.
   Find matching Dust generation infos and set their spent time to timestamp provided

**`rollback_last_transaction`, new step 3 (the former step 3 becomes step 4):**

3. If transaction settled a candidate or an outstanding transaction: return it to its previous status (`Pending` with
   the scope as submitter, or `Rejected`), restore its inputs' booking records and `outstanding` entries verbatim, move
   the coins it created back to expected ones and restore the sibling candidates it invalidated (they were invalidated
   only by this transaction)

**`discard_transaction`, step 3 becomes, with a closing paragraph:**

3. Un-book coins spent in the transaction: remove the `transaction` field from each booking record; a coin that still
   carries references stays `Booked`, any other coin returns to `Final`. If the transaction's validity bound has not
   passed and the transaction may have left the wallet (it was serialized, exported or handed to another party), add
   an `outstanding` entry for it to each such coin
4. Remove related Dust generation info

Discarding applies to transactions the wallet itself submits. Candidates are dropped with `detach_transaction`, or
become invalidated, stale or expired as described in [Candidate transactions](#candidate-transactions).

**Transaction submission, prepend:**

Pending transactions whose submitter is a scope (candidates, see [`spend`](#spend)) are never submitted or
re-submitted by the wallet; the scope that received them is responsible for submission, usually after merging them
with other transactions.

## 4. Building stages

Insert after *Prepare a swap*:

### Prepare a held offer (bid)

A held offer is a swap prepared from a hold, so that the wallet can hand it off and still keep the coins reserved. It
is the building block of the bidding use cases described in [Holds and pinned coins](#holds-and-pinned-coins). Given
an authorization (`use: X` to bid with coins already pinned under `X`, `mark: X` to pin whatever is selected under
`X`; the first bid of a series typically gives both), the token amounts to provide, the token amounts to receive, and
whether fees are to be paid:

1. Create expected outputs for each token type to be received, addressed to the wallet itself. The wallet MAY have
   never held that token type (e.g. a token that will be minted by the settling contract); an output does not require
   prior ownership of its token type. Shielded outputs go into a guaranteed shielded offer skeleton; unshielded outputs
   go into an unshielded offer skeleton of a new intent with a randomly chosen segment id (see
   [Prepare a swap](#prepare-a-swap)).
2. Set target imbalances equal to the amounts to provide, per token kind.
3. Balance the skeletons with `spend` and the authorization: shielded inputs come from the shielded coins pinned under
   `X`, unshielded inputs from the UTxOs pinned under `X`, change is addressed to the wallet. If fees are to be paid,
   Dust spends are created from the Dust outputs pinned under `X` first ([Creating a Dust spend](#creating-a-dust-spend))
   and placed in the intent. Unshielded inputs and Dust spends require an intent; a purely shielded offer without fees needs none.
4. Prove, sign the intent (if any) and bind. The result is a transaction whose guaranteed shielded offer and whose
   intent (if any) are mergeable as a unit by any party: shielded offers merge by set union, intents merge by segment
   id.
5. Hand it to the caller (this is the end of the authorized `spend`: the candidate reference is recorded on its inputs
   and the transaction booking released).

Repeating steps 1-5 with the same `use: X` and different amounts or recipients produces sibling candidates from the
same coins. The validity bound of a candidate is the root retention window for its shielded inputs (counted from
build time) or the TTL of its intent, whichever applies first. Both are known to the caller without help from the
wallet: root retention is a network parameter, and the intent TTL is chosen at build time. The caller is responsible
for rebuilding before the bound passes.

## 5. Requirements checklist

Each requirement is traceable to the text above and to at least one conformance scenario in section 6.

| ID | Requirement | Text |
|---|---|---|
| R-01 | The wallet MUST add no coin state and no transaction status; holds are expressed as metadata on `Booked` (`{transaction?, holds: (scope,id) → set of references}`), coins (`outstanding`), `Pending` (`submitter`) and `Rejected` (`reason`, validity bound). | 2, lifecycle |
| R-02 | A wallet that ignores the metadata MUST remain correct: booked is unavailable, final is available, pending is in flight. | 2, lifecycle |
| R-03 | Scopes MUST be derived by the wallet and MUST NOT be caller-supplied. | 2, scopes |
| R-04 | Hold identifiers are chosen by the requesting scope and MUST resolve only within that scope; the same text in two scopes names two unrelated holds. | 2, hold structure; privacy 2 |
| R-05 | A coin MAY carry references under several identifiers of the same scope; it is `Booked` iff it carries a `transaction` booking or at least one reference, and returns to `Final` when the last of them goes. | 2, hold structure; lifecycle |
| R-06 | An ordinary `spend` (no authorization) MUST never select a booked coin, pinned or not, and its insufficient-funds error MUST NOT reveal whether pinned funds would have covered the shortage. | 3.1 |
| R-07 | `use: X` MUST select coins pinned under (scope, X) first and MAY add unpinned final coins; if no hold X exists it MUST be created implicitly with the wallet's default policy. | 3.1 |
| R-08 | Every selected coin, found or added, MUST receive this build's `candidate` reference under (scope, `mark`), `mark` defaulting to `use`; the build MUST end by handing the candidate to the scope and releasing the transaction booking. | 3.1 |
| R-09 | An authorized build MUST NOT select coins pinned under any other hold. | 3.1 |
| R-10 | If an authorized build pays fees, Dust outputs pinned under `use` MUST be used first; unpinned Dust it needs MUST be pinned like any other coin. | 3.1 |
| R-11 | An identifier MUST accept any number of candidates; a hold confirmed by the user once MUST NOT prompt again for siblings or rebuilds under the same identifier. | 3.2 `hold` |
| R-12 | Selection, pinning and booking MUST be one atomic step, so concurrent builds cannot pin or book the same coin. | 3.1 |
| R-13 | `hold` MUST select only final, unpinned coins, MUST add a `reservation` reference with `validUntil = expires_at`, and MUST fail without revealing whether other holds' coins would have sufficed. | 3.2 |
| R-14 | Hold state MUST be persisted with wallet state and restored before any spend; unreadable or invalid hold state MUST block ordinary spends rather than treat coins as unpinned. | 3.2 `hold` |
| R-15 | An authorized build MUST record, in each reference, the candidate's identifying components (input nullifiers, output commitments, value commitments) and validity bound, and MAY record its expected coins. | 2, candidates; 3.1 |
| R-16 | A scope-submitted pending transaction MUST be excluded from the wallet's submission and re-submission loop, from the pending balance and from TTL-based discarding. | 2, lifecycle; 3.3 submission |
| R-17 | On observing a pinned coin's nullifier, `apply_transaction` MUST mark the matching candidate `Confirmed`, confirm its expected coins, mark sibling candidates sharing the nullifier `Rejected(invalidated)`, and close the hold when it has no pinned coins left. | 3.3 |
| R-18 | On observing a nullifier of a final coin with a matching `outstanding` entry, `apply_transaction` MUST move that transaction `Rejected → Confirmed` and drop the coin's entries. | 3.3 |
| R-19 | `rollback_last_transaction` MUST restore booking records, `outstanding` entries, candidate statuses and invalidated siblings exactly as they were before the rolled-back transaction. | 3.3 |
| R-20 | `discard_transaction` MUST leave a coin booked by a hold booked by that hold, and MUST add an `outstanding` entry when the discarded transaction may have left the wallet and its validity bound has not passed. | 3.3 |
| R-21 | `detach_transaction(transaction)` MUST remove that candidate's reference from every coin carrying it, mark it `Rejected(detached)` and, unless `force` is set, add an `outstanding` entry while its validity bound holds; `detach_transaction({id})` MUST do so for every reference under (scope, id) and drop the hold's policy. Both MUST be idempotent. | 3.2 |
| R-22 | Coins with `outstanding` entries MUST be counted as available and MUST carry a user-visible warning while entries remain; a wallet MAY prefer them in ordinary selection. | 1; 2, lifecycle |
| R-23 | References past their validity bound MUST be pruned automatically (a reservation at `expires_at`; a candidate at its bound, becoming `Rejected(stale|expired)` with no `outstanding` entry). | 2, lifecycle |
| R-24 | The validity bound of a candidate MUST be the root retention window of the deployed ledger for shielded inputs (Dust included) and the smallest intent TTL for unshielded inputs, the earlier of the two when both kinds are present; the wallet MUST read root retention from the network, not assume it, and is NOT required to expose the bound, its hold-duration cap or network parameters to scopes. | 2, candidates; 4 |
| R-25 | A candidate past its validity bound MUST become `Rejected(stale)` or `Rejected(expired)`; no `outstanding` entry is required for it. | 2, candidates |
| R-26 | `available` MUST exclude every booked coin for every observer; `held` MUST be reported per scope and only for the requesting scope's holds; expected coins of candidates MUST NOT be counted in `pending`. | 1 |
| R-27 | Every balance reported to a scope MUST be computed by the same function regardless of who owns the pins. | 2, privacy 3 |
| R-28 | Hold identifiers, notes, scope identity and the existence of other holds MUST NOT appear in transactions, offer files, dApp-visible history, logs or errors. | 2, privacy 4 |
| R-29 | The wallet user interface SHOULD show, for every hold, the owning scope, amount, expiry and candidate count, and let the user drop it. | 2, privacy 5 |
| R-30 | A held offer MUST be built through `spend` with an authorization; pure shielded candidates MUST contain no intent; candidates with unshielded inputs or Dust spends MUST carry one intent with a randomly chosen segment id and a TTL. | 4 |
| R-31 | `force: true` on `detach_transaction` MUST suppress the `outstanding` entry; the wallet MAY record the caller's assertion in history and MAY require the wallet user's confirmation when the request comes from a scope. | 3.2 |
| R-32 | `list_holds(scope)` MUST report, for the requesting scope only, each identifier with the amount pinned per token type, its live candidates (identified by components the scope already holds) with validity bounds, the reservation expiry and the number of outstanding entries; it MUST NOT reveal coin identifiers, nonces, Merkle indices or anything about other scopes, and an empty report MUST be indistinguishable from a wallet that never had holds. | 3.2 |

## 6. Conformance scenarios

Each scenario names the requirements it exercises. Coin X is a final 150 NIGHT shielded coin; M is a dApp origin.

| ID | Given | When | Then | Requirements |
|---|---|---|---|---|
| C-01 | X final, no holds | M builds bid A with `use: "r7"`, `payFees=false` | X is `Booked{transaction:A, holds:{(M,r7)→{A}}}` during the build and `Booked{holds:{(M,r7)→{A}}}` after handoff; A is `Pending{submitter:M}`; A has no Dust spend | R-07, R-08, R-12, R-15, R-16, R-30 |
| C-02 | C-01 | M builds B and C with `use: "r7"` | B and C carry X's nullifier with different randomness and outputs; X is `Booked{holds:{(M,r7)→{A,B,C}}}` | R-07, R-08, R-11 |
| C-02a | C-02 | M builds D with `use: "r7", mark: "r8"` | X is selected (pinned under r7) and gains a reference under (M,r8): `{(M,r7)→{A,B,C}, (M,r8)→{D}}`; `use: "r8"` alone would not have found X | R-05, R-08 |
| C-03 | C-02 | user requests an ordinary 120 NIGHT transfer | fails with the ordinary insufficient-funds error; X not selected; error identical in shape to a genuine shortage | R-06 |
| C-04 | C-02 | another origin N reads balances and asks for a balancing that only X could cover | N sees X in neither available nor held; N's failure is byte-identical in shape to a genuine shortage | R-26, R-27 |
| C-05 | C-02 | N calls hold inspection or release with id `"r7"` | result equals a never-used id; M's hold unchanged | R-04 |
| C-06 | C-02a | B is merged, balanced and submitted by the marketplace; the block is applied | X `Spent`; B `Confirmed`, its expected coins confirmed; A, C, D `Rejected(invalidated)`; r7 and r8 policies dropped | R-17 |
| C-07 | C-06 | that block is rolled back | X's record restored verbatim; A, B, C, D `Pending{submitter:M}`; wallet does not resubmit B | R-19, R-16 |
| C-08 | C-02a | M calls `detach_transaction(D)` then `detach_transaction({id:"r7"})` | after the first call X is still `Booked` (r7 pins it) and D is `Rejected(detached)`, outstanding; after the second X is `Final` with outstanding A, B, C, D, counted as available, warning shown | R-05, R-21, R-22 |
| C-08a | C-02 | M calls `detach_transaction(C, {force:true})` | C's reference removed, C `Rejected(detached)`, no outstanding entry for C | R-31 |
| C-08b | C-08 | M repeats both detach calls | no change (idempotent) | R-21 |
| C-09 | C-08 | the marketplace settles B anyway | X `Spent`; B `Rejected → Confirmed`; recorded as settled after release | R-18 |
| C-10 | C-08 | user makes an ordinary transfer that selects X and it confirms | outstanding entries dropped, warning gone; A, B, C can never settle | R-22 |
| C-11 | C-02 | root retention passes and M holds no reservation | A, B, C `Rejected(stale)`, references pruned, X returns to `Final` with no outstanding entry; a new build with `use: "r7"` recreates the hold and pins X again without user prompt; no bound or network parameter was exposed to M | R-23, R-24, R-25, R-11 |
| C-12 | hold (M,"u1") over unshielded UTxOs | M builds candidate U with intent TTL T; T passes | U's bound is T; U becomes `Rejected(expired)`, its reference pruned, no outstanding entry | R-23, R-24, R-25, R-30 |
| C-13 | hold (M,"f1") with pinned NIGHT and pinned Dust | M builds with `use: "f1"`, `payFees=true` | the Dust spend uses the pinned Dust output first; the candidate carries one intent; fee sharing with the settler is not the wallet's concern | R-10, R-30 |
| C-14 | hold (M,"h0") confirmed by the user at its first build | M builds four more candidates with `use: "h0"` | all accepted without a further prompt | R-11 |
| C-14a | X final | M calls `hold("h1", 100 NIGHT, expires_at = +10 min)` and builds nothing | X `Booked{holds:{(M,h1)→{reservation}}}`; at +10 min the reservation is pruned and X returns to `Final` with no outstanding entry | R-13, R-23 |
| C-15 | any holds | wallet restarts | identical holds, records and balances; ordinary selection still excludes pinned coins | R-14 |
| C-16 | persisted hold state corrupted | wallet restarts | ordinary spends refused until the user restores or acknowledges a reset | R-14 |
| C-17 | X final, wallet-submitted transaction T booked X, T was exported to a third party | T is discarded before its bound | X `Final` with an outstanding entry for T; if T later confirms, `Rejected → Confirmed` | R-20, R-18 |
| C-18 | two concurrent builds with different ids compete for the last unpinned coin | both run | exactly one pins it; the other fails with the ordinary error (it cannot take a coin pinned only under another id) | R-09, R-12 |
| C-19 | C-02a | M calls `list_holds`; N calls `list_holds` | M sees r7: 150 NIGHT, candidates A, B, C; r8: 150 NIGHT, candidate D, each with its bound, and no coin identifiers; N receives an empty report | R-32, R-26, R-27 |
