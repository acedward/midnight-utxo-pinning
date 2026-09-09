# Holds and pinned coins — normative specification

This document is the exact text proposed for the Midnight Wallet Specification, assembled from the patch in
[upstream/](upstream/) so that the two never diverge, followed by a requirements checklist and conformance scenarios.
Section numbers refer to this document; the patch places the same text inside the wallet specification's *State
management*, *Building standard transactions* and *Transaction submission* sections.

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
  the holds its own scope owns, the wallet user sees the held balance of all holds
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
- the set of **pinned coins** - own, final coins reserved by the hold; shielded coins, unshielded UTxOs and Dust
  outputs alike; a coin belongs to at most one hold at a time
- the set of **candidate transactions** built from the hold (see below)
- a policy:
  - `mode` - `single` or `multi`. A `single` hold backs exactly one live candidate: once a candidate is detached, no
    further authorized build is accepted until that candidate is settled, invalidated, stale or the hold is released.
    A `multi` hold backs any number of sibling candidates from the same coins, including rebuilds of stale ones. The
    mode is fixed at creation; a hold created implicitly by a build (see `spend`) is `multi`, because asking to reuse
    an `id` is an explicit request for reuse
  - `expires_at` - wall-clock instant after which the hold is released automatically; the wallet MAY cap it
  - an optional free `note`, stored, never interpreted, never leaving the owning scope
- creation time and, for auditing by the user, the identity that requested it

A pinned coin is a `Booked` coin whose booking record names the hold (see the lifecycle below). From the ledger's
point of view it is an ordinary spendable coin; what changes is only the wallet's willingness to select it: an ordinary
spend MUST never select a pinned coin, and a build that says `use: X` MAY select only coins pinned under (scope, `X`)
plus, if it needs more value, ordinary unpinned coins, which are then pinned under the same `id`.

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
    deployed value is expected to be much longer (days) but is not confirmed until a network running ledger v9 exists
  - for Dust spends, the same root retention applies to the Dust commitment and generation trees, and the spend is
    additionally bound to the timestamp it was built with
  - for unshielded inputs, the TTL of the intent that carries them (chosen at build time, within the margin the ledger
    allows ahead of the current block); an intent past its TTL can never be applied. A candidate with intents has the
    smallest intent TTL as one of its bounds

  The candidate's validity bound is the minimum of the bounds that apply to it. Every bound is enforced by the ledger,
  so a candidate past its bound can never settle: the wallet marks it `Rejected(stale)` (root retention) or
  `Rejected(expired)` (TTL) and needs no `outstanding` entry for it.
- its status, which is the ordinary transaction status with extra metadata: *live* is `Pending` with the scope as
  submitter; *settled* is `Confirmed`; *invalidated* (one of its inputs was spent by another transaction, e.g. a
  sibling candidate), *stale* (validity bound passed) and *released* (hold released) are `Rejected` with that reason.

Candidates in one hold that share an input nullifier form a **conflict group**: at most one of them can ever settle.
This is exactly the property the bidding use cases rely on, and it is visible to the owning scope, which needs it in
order not to merge two conflicting candidates into one settlement (the ledger would reject the double spend and the
whole guaranteed section with it).

### Coin and transaction lifecycle with holds

Holds add **no new coin state and no new transaction status**. They add metadata to three existing ones, so that a
wallet which does not implement holds still interprets every state correctly (a booked coin is unavailable, a final coin
is available, a pending transaction is in flight):

- `Booked` carries a *booking record* with two optional fields, at least one of which is set:
  - `transaction` - the pending transaction that booked the coin (the only meaning `Booked` has today);
  - `hold` - the hold that reserved the coin. A coin with `hold` set is what this document calls *pinned*.
  
  The combinations are: `{transaction}` - current behaviour; `{hold}` - pinned and idle; `{hold, transaction}` -
  pinned and currently booked by a candidate being built. Transitions: `final -> booked{transaction}` on ordinary
  `spend`; `final -> booked{hold}` on `hold`; `booked{hold} -> booked{hold, transaction}` on an authorized `spend`;
  `booked{hold, transaction} -> booked{hold}` on `detach_transaction` or on `discard_transaction` of that transaction;
  `booked{transaction} -> final` on `discard_transaction`; `booked{hold} -> final` on `release_hold` or hold expiry;
  `booked{*} -> spent` on `apply` of any transaction carrying the coin's nullifier; `spent -> booked{*}` on `rollback`,
  restoring the previous record verbatim.
- `Final` carries an optional list `outstanding`: the identifying components and validity bound of every transaction
  that could still consume the coin although the wallet no longer intends it to - candidates of a released hold, and
  any discarded transaction that may have left the wallet. The coin **is available**; the wallet MUST show a warning
  while the list is non-empty and SHOULD prefer such coins in ordinary coin selection, because spending them is what
  actually cancels the outstanding transactions. Entries are dropped when their validity bound passes or when the coin
  is spent. A wallet without hold support ignores the list.
- `Pending` carries a `submitter`: the wallet (today's meaning) or a scope. A pending transaction whose submitter is a
  scope is a live candidate: it is excluded from the wallet's submission and re-submission loop, from the pending
  balance (its expected outputs are tracked but not counted), and from TTL-based discarding.
- `Rejected` carries a `reason` (`discarded`, `released`, `invalidated`, `stale`, `expired`) and, while relevant, the
  validity bound of the transaction. One transition is added that the current lifecycle lacks and that already happens
  in practice: `rejected -> confirmed` on `apply`, when a transaction the wallet gave up on is submitted by somebody
  else before its validity bound passes. The matching `outstanding` entry on its input coins is what lets the wallet
  recognize and explain it.

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

The operation takes an optional `authorization` from the requesting scope, with two optional fields:

- `use: X` - select coins pinned under (scope, `X`) first; if they do not cover the request, unpinned final coins MAY be
  added, and every added coin is pinned under `X` (or under `mark`, if given) atomically
- `mark: Y` - pin every coin selected for this transaction under (scope, `Y`); if no such hold exists it is created
  implicitly with the wallet's default policy and mode `multi`. Without `use`, selection is ordinary (unpinned coins
  only) and the result is pinned

Without an authorization the spend is *ordinary* and works exactly as today: booked coins, pinned or not, are never
eligible, and if the unpinned funds are insufficient the operation fails with an insufficient-funds error that does not
reveal whether pinned funds would have covered the shortage. Coins with `outstanding` entries are ordinary final coins;
a wallet MAY prefer them, since spending them cancels the outstanding transactions. With an authorization the wallet
MUST verify that the scope owns the named holds (an `id` never resolves into another scope), and coins pinned under any
other hold remain ineligible. Dust is not a special case: if the build pays fees, Dust outputs pinned under `use` are
used first, and any unpinned Dust output it needs is pinned like any other coin. For a `single` hold that already has a
live candidate a build with `use` MUST be refused.

Append after the step list:

A transaction built with an authorization stays in the pending pool like any other until the wallet either submits it
or hands it off with `detach_transaction`. Selection, pinning and booking MUST happen in one atomic step, so that two
concurrent builds cannot pin or book the same coin.

### 3.2 New operations

#### `hold`

Creates a hold for a scope without building a transaction (see [Holds and pinned coins](#holds-and-pinned-coins)) -
for reserving an amount up front, or for setting a policy before the first build with `use`/`mark` would create the
hold implicitly. Parameters:

- the requesting scope, derived by the wallet
- the `id` chosen by the scope; if a hold with that `id` already exists in the scope, the selected coins are added to it
- a selection: either explicit references to own final coins, or a map of token type to amount, in which case the
  wallet selects final, unpinned coins covering each amount using its ordinary coin selection
- a policy: `mode`, `expires_at`, optional `note`

Steps:

1. Resolve the selection to a set of own **final, unpinned** coins; fail if it cannot be satisfied (the error MUST NOT
   reveal whether coins pinned by other holds would have satisfied it)
2. Validate the policy against wallet limits (maximum duration, maximum number of holds per scope, metadata size)
3. Atomically create or extend the hold, set the coins to `Booked` with a booking record naming it, and persist

Holds MUST be persisted with the rest of the wallet state and MUST be restored before any spend is allowed after a
restart. If the persisted hold state cannot be read or validated, the wallet MUST refuse ordinary spends rather than
treat the coins as unpinned.

A wallet MAY require user confirmation for creating a hold, exactly as it may for any spend; a hold is a commitment of
funds and SHOULD be presented as such (amount, token type, requesting scope, mode, expiry). Confirmation is given once,
at creation, and covers every build the hold's mode allows - in particular a `multi` hold does not prompt again when
the scope rebuilds a stale candidate or builds a sibling.

#### `detach_transaction`

Hands a transaction built with an authorization off to its hold's scope as a candidate. It is the counterpart of
`discard_transaction` for the case where the wallet is *not* the submitter. The hold is the one named in the booking
records of the transaction's inputs. Steps:

1. Verify the transaction is in the pending pool and that its inputs are booked under one hold of the requesting scope
2. Record the candidate: identifying components, coins it would create, validity bound; set the transaction's
   submitter to the scope
3. Remove the `transaction` field from the booking record of each input, so that the inputs are `Booked` by the hold
   only; keep the coins it would create as expected ones, excluded from the pending balance
4. Any unpinned coin the build had to add is already pinned in the hold (see `spend`); verify this invariant
5. Persist atomically, then return the serialized transaction to the scope

After `detach_transaction` the same coins can be used again by another authorized build for the same hold if its mode
is `multi`, which is how several mutually exclusive candidates are produced from one set of coins; a `single` hold
accepts a new build only once its live candidate is no longer `Pending`. The wallet SHOULD warn the user, when
presenting a hold, that candidates are outside the wallet's control: they may be submitted by the scope at any time
before they become stale, and local release does not revoke them.

#### `release_hold`

Ends the hold (scope, `id`). Steps:

1. Resolve `id` within the requesting scope (the wallet user may release any hold); fail as not found otherwise
2. Mark every live candidate `Rejected(released)`, keeping its validity bound
3. For every coin booked by the hold: remove the `hold` field from its booking record; if the record is now empty the
   coin returns to `Final`, with one `outstanding` entry per candidate that used it and whose validity bound has not
   passed
4. Persist atomically

The coins are available again immediately, as the user intends, but the wallet MUST make it clear that candidates
already handed out may still settle until their validity bound passes or the coins are spent: this is what the
`outstanding` warning conveys. Spending the coins is the only definitive cancellation; ordinary coin selection SHOULD
therefore prefer coins with `outstanding` entries, and the wallet MAY offer a "cancel" convenience that performs the
release and a self-transfer of those coins together.

A hold whose `expires_at` has passed is released automatically with the same effects. Expiry is evaluated against the
wallet clock and SHOULD include the same latency margin used for TTL handling.

### 3.3 Amendments to existing operations

**`apply_transaction`, steps to apply a shielded offer, step 3 becomes:**

3. Book coins, whose nullifiers match the ones present in offer inputs. If a matched coin is pinned (booked by a
   hold):
   1. Find the candidate of its hold whose identifying components (value commitments, output commitments) are present
      in the transaction; if one is found, mark it `Confirmed` and confirm the coins it was expected to create
   2. Mark every other candidate of the hold that uses the same nullifier as `Rejected(invalidated)`
   3. If the hold has no pinned coins left, close it
   
   If a matched coin is final with `outstanding` entries and one of them matches the transaction, mark that transaction
   `Confirmed` (from `Rejected`) and confirm the coins it was expected to create; drop all entries of the coin.

**`apply_transaction`, steps to apply an unshielded offer, step 1 becomes:**

1. Book coins spent in the inputs; for pinned coins and for coins with `outstanding` entries apply the same settlement
   rules as for shielded inputs.
   Find matching Dust generation infos and set their spent time to timestamp provided

**`rollback_last_transaction`, new step 3 (the former step 3 becomes step 4):**

3. If transaction settled a candidate or an outstanding transaction: return it to its previous status (`Pending` with
   the scope as submitter, or `Rejected`), restore its inputs with their previous booking record or `outstanding`
   entries, move the coins it created back to expected ones and restore the sibling candidates it invalidated (they
   were invalidated only by this transaction)

**`discard_transaction`, step 3 becomes, with a closing paragraph:**

3. Un-book coins spent in the transaction: remove the `transaction` field from each booking record; a coin whose
   record still names a hold stays `Booked` by that hold, any other coin returns to `Final`. If the transaction's
   validity bound has not passed and the transaction may have left the wallet (it was serialized, exported or handed to
   another party), add an `outstanding` entry for it to each such coin
4. Remove related Dust generation info

Discarding applies to transactions the wallet itself submits. Candidates are not discarded - they are released
together with their hold (`release_hold`), or become invalidated or stale as described in
[Candidate transactions](#candidate-transactions).

**Transaction submission, prepend:**

Pending transactions whose submitter is a scope (candidates, see [`detach_transaction`](#detach_transaction)) are
never submitted or re-submitted by the wallet; the scope that received them is responsible for submission, usually
after merging them with other transactions.

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
5. `detach_transaction` it and return it to the caller together with its validity bound.

Repeating steps 1-5 with the same `use: X` and different amounts or recipients produces sibling candidates from the
same coins (the hold is `multi`). The validity bound of a candidate is the minimum of: the root retention window for its shielded and Dust inputs
(counted from build time), and the TTL of its intent if it has one. The caller SHOULD schedule rebuilds before the
bound passes. Fees are a choice of the use case: when the settling party pays, the candidate is built without fees and
without an intent; when the bidder pays, the candidate carries a Dust spend and the fee amount is fixed at build time,
so the settling party MUST still cover any shortfall of the merged transaction.

## 5. Requirements checklist

Each requirement is traceable to the text above and to at least one conformance scenario in section 6.

| ID | Requirement | Text |
|---|---|---|
| R-01 | The wallet MUST add no coin state and no transaction status; holds are expressed as metadata on `Booked` (`{transaction?, hold?}`), `Final` (`outstanding`), `Pending` (`submitter`) and `Rejected` (`reason`, validity bound). | 2, lifecycle |
| R-02 | A wallet that ignores the metadata MUST remain correct: booked is unavailable, final is available, pending is in flight. | 2, lifecycle |
| R-03 | Scopes MUST be derived by the wallet and MUST NOT be caller-supplied. | 2, scopes |
| R-04 | Hold identifiers are chosen by the requesting scope and MUST resolve only within that scope; the same text in two scopes names two unrelated holds. | 2, hold structure; privacy 2 |
| R-05 | A coin MUST belong to at most one hold at a time. | 2, hold structure |
| R-06 | An ordinary `spend` (no authorization) MUST never select a booked coin, pinned or not, and its insufficient-funds error MUST NOT reveal whether pinned funds would have covered the shortage. | 3.1 |
| R-07 | `use: X` MUST select coins pinned under (scope, X) first, MAY add unpinned final coins, and MUST pin every added coin under X (or under `mark`) atomically with selection and booking. | 3.1 |
| R-08 | `mark: Y` MUST pin every coin selected by the build under (scope, Y), creating the hold implicitly with mode `multi` when absent. | 3.1 |
| R-09 | An authorized build MUST NOT select coins pinned under any other hold. | 3.1 |
| R-10 | If an authorized build pays fees, Dust outputs pinned under `use` MUST be used first; unpinned Dust it needs MUST be pinned like any other coin. | 3.1 |
| R-11 | A `single` hold MUST refuse a build with `use` while it has a live `Pending` candidate; a `multi` hold MUST accept siblings and rebuilds without further user confirmation. | 2, hold structure; 3.1 |
| R-12 | Selection, pinning and booking MUST be one atomic step, so concurrent builds cannot pin or book the same coin. | 3.1 |
| R-13 | `hold` MUST select only final, unpinned coins and MUST fail without revealing whether other holds' coins would have sufficed. | 3.2 |
| R-14 | Hold state MUST be persisted with wallet state and restored before any spend; unreadable or invalid hold state MUST block ordinary spends rather than treat coins as unpinned. | 3.2 `hold` |
| R-15 | `detach_transaction` MUST record the candidate's identifying components (input nullifiers, output commitments, value commitments), expected coins and validity bound; set the submitter to the scope; and drop the `transaction` field from the inputs' booking records. | 3.2 |
| R-16 | A scope-submitted pending transaction MUST be excluded from the wallet's submission and re-submission loop, from the pending balance and from TTL-based discarding. | 2, lifecycle; 3.3 submission |
| R-17 | On observing a pinned coin's nullifier, `apply_transaction` MUST mark the matching candidate `Confirmed`, confirm its expected coins, mark sibling candidates sharing the nullifier `Rejected(invalidated)`, and close the hold when it has no pinned coins left. | 3.3 |
| R-18 | On observing a nullifier of a final coin with a matching `outstanding` entry, `apply_transaction` MUST move that transaction `Rejected → Confirmed` and drop the coin's entries. | 3.3 |
| R-19 | `rollback_last_transaction` MUST restore booking records, `outstanding` entries, candidate statuses and invalidated siblings exactly as they were before the rolled-back transaction. | 3.3 |
| R-20 | `discard_transaction` MUST leave a coin booked by a hold booked by that hold, and MUST add an `outstanding` entry when the discarded transaction may have left the wallet and its validity bound has not passed. | 3.3 |
| R-21 | `release_hold` MUST mark live candidates `Rejected(released)`, return coins with empty booking records to `Final` with one `outstanding` entry per candidate that could still settle, and MUST make clear that released candidates may still settle. | 3.2 |
| R-22 | Coins with `outstanding` entries MUST be counted as available and MUST carry a user-visible warning while entries remain; a wallet MAY prefer them in ordinary selection. | 1; 2, lifecycle |
| R-23 | A hold past `expires_at` MUST be released automatically with the effects of `release_hold`. | 3.2 |
| R-24 | The validity bound of a candidate MUST be the root retention window of the deployed ledger for shielded inputs (Dust included) and the smallest intent TTL for unshielded inputs, the earlier of the two when both kinds are present; it MUST be read from the network, not assumed, and MUST be returned with the candidate. | 2, candidates |
| R-25 | A candidate past its validity bound MUST become `Rejected(stale)` or `Rejected(expired)`; no `outstanding` entry is required for it. | 2, candidates |
| R-26 | `available` MUST exclude every booked coin for every observer; `held` MUST be reported per scope and only for the requesting scope's holds; expected coins of candidates MUST NOT be counted in `pending`. | 1 |
| R-27 | Every balance reported to a scope MUST be computed by the same function regardless of who owns the pins. | 2, privacy 3 |
| R-28 | Hold identifiers, notes, scope identity and the existence of other holds MUST NOT appear in transactions, offer files, dApp-visible history, logs or errors. | 2, privacy 4 |
| R-29 | The wallet user interface SHOULD show, for every hold, the owning scope, amount, mode, expiry and candidate count, and let the user release it. | 2, privacy 5 |
| R-30 | A held offer MUST be built through `spend` with an authorization; pure shielded candidates MUST contain no intent; candidates with unshielded inputs or Dust spends MUST carry one intent with a randomly chosen segment id and a TTL. | 4 |

## 6. Conformance scenarios

Each scenario names the requirements it exercises. Coin X is a final 150 NIGHT shielded coin; M is a dApp origin.

| ID | Given | When | Then | Requirements |
|---|---|---|---|---|
| C-01 | X final, no holds | M builds bid A with `mark: "r7"`, `payFees=false` | X is `Booked{hold:(M,r7), transaction:A}` during the build and `Booked{hold}` after detach; A is `Pending{submitter:M}`; A has no Dust spend | R-08, R-12, R-15, R-16, R-30 |
| C-02 | C-01 | M builds B and C with `use: "r7"` | B and C carry X's nullifier with different randomness and outputs; X stays `Booked{hold}` | R-07, R-11 |
| C-03 | C-02 | user requests an ordinary 120 NIGHT transfer | fails with the ordinary insufficient-funds error; X not selected; error identical in shape to a genuine shortage | R-06 |
| C-04 | C-02 | another origin N reads balances and asks for a balancing that only X could cover | N sees X in neither available nor held; N's failure is byte-identical in shape to a genuine shortage | R-26, R-27 |
| C-05 | C-02 | N calls hold inspection or release with id `"r7"` | result equals a never-used id; M's hold unchanged | R-04 |
| C-06 | C-02 | B is merged, balanced and submitted by the marketplace; the block is applied | X `Spent`; B `Confirmed`, its expected coins confirmed; A, C `Rejected(invalidated)`; hold closed | R-17 |
| C-07 | C-06 | that block is rolled back | X `Booked{hold}`; A, B, C `Pending{submitter:M}`; wallet does not resubmit B | R-19, R-16 |
| C-08 | C-02 | M releases `"r7"` | X `Final` with outstanding A, B, C; counted as available; warning shown; A, B, C `Rejected(released)` | R-21, R-22 |
| C-09 | C-08 | the marketplace settles B anyway | X `Spent`; B `Rejected → Confirmed`; recorded as settled after release | R-18 |
| C-10 | C-08 | user makes an ordinary transfer that selects X and it confirms | outstanding entries dropped, warning gone; A, B, C can never settle | R-22 |
| C-11 | C-02 | root retention passes | A, B, C `Rejected(stale)`; X still `Booked{hold}`; rebuild with `use` succeeds without user prompt | R-24, R-25, R-11 |
| C-12 | hold (M,"u1") over unshielded UTxOs | M builds candidate U with intent TTL T; T passes | U's bound is T; U becomes `Rejected(expired)`; releasing the hold adds no outstanding entry for U | R-24, R-25, R-30 |
| C-13 | hold (M,"f1") with pinned NIGHT and pinned Dust | M builds with `use: "f1"`, `payFees=true` | the Dust spend uses the pinned Dust output first; the candidate carries one intent | R-10, R-30 |
| C-14 | hold (M,"s1") with mode `single`, live candidate D | M builds with `use: "s1"` | refused; after D settles or is released, accepted | R-11 |
| C-15 | any holds | wallet restarts | identical holds, records and balances; ordinary selection still excludes pinned coins | R-14 |
| C-16 | persisted hold state corrupted | wallet restarts | ordinary spends refused until the user restores or acknowledges a reset | R-14 |
| C-17 | X final, wallet-submitted transaction T booked X, T was exported to a third party | T is discarded before its bound | X `Final` with an outstanding entry for T; if T later confirms, `Rejected → Confirmed` | R-20, R-18 |
| C-18 | two concurrent builds with `mark` compete for the last unpinned coin | both run | exactly one pins it; the other fails with the ordinary error | R-12 |
