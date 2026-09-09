#!/usr/bin/env python3
"""Regenerate sections 1-4 of SPECIFICATION.md from the upstream wallet-spec branch.

Usage: tools/assemble-spec.py <path to midnight-wallet docs/spec/Specification.md on the proposal branch>
Sections 5 (requirements) and 6 (conformance) of SPECIFICATION.md are preserved verbatim.
"""
import re, sys, pathlib
src = pathlib.Path(sys.argv[1]).read_text()
out_path = pathlib.Path(__file__).resolve().parent.parent / "SPECIFICATION.md"
existing = out_path.read_text() if out_path.exists() else ""
tail = existing[existing.index("## 5. Requirements checklist"):] if "## 5. Requirements checklist" in existing else ""

def region(a, b):
    i = src.index(a); j = src.index(b, i); return src[i:j].rstrip() + "\n"
def lift(text, n=1):
    return re.sub(r'^(#{2,6})\s', lambda m: '#' * max(1, len(m.group(1)) - n) + ' ', text, flags=re.M)

holds = lift(region("### Holds and pinned coins", "### Operations"), 1)
holds = holds.replace("## Holds and pinned coins\n", "", 1)
balances = region("When holds are supported", "Because of need to book coins")
spend = region("The operation takes an optional `authorization`", "Following steps need to be taken")
spend2 = region("Selection, pinning and booking MUST happen", "#### `watch_for`")
ops = region("#### `hold`", "## Synchronization process")
apply3 = region("3. Book coins, whose nullifiers match the ones present in offer inputs. If", "4. Watch for received coins")
apply_u = region("1. Book coins spent in the inputs; for coins carrying", "2. Filter outputs")
rollback3 = region("3. If transaction settled a candidate or an outstanding transaction", "4. Otherwise, discard transaction")
discard3 = region("3. Un-book coins spent in the transaction: remove", "#### `spend`")
stage = lift(region("#### Prepare a held offer (bid)", "#### Contract call"), 1)
submit = region("Pending transactions whose submitter is a scope", "For cases, where the wallet submits")

head = f"""# Holds and pinned coins — normative specification

This document is the exact text proposed for the Midnight Wallet Specification, assembled by `tools/assemble-spec.py`
from the patch in [upstream/](upstream/) so that the two never diverge, followed by a requirements checklist and
conformance scenarios. Section numbers refer to this document; the patch places the same text inside the wallet
specification's *State management*, *Building standard transactions* and *Transaction submission* sections.

The key words MUST, MUST NOT, SHOULD, SHOULD NOT and MAY are to be interpreted as described in RFC 2119.

## 1. Balances

Insert after the three existing balances (available, pending, total):

{balances}
## 2. Holds and pinned coins

{holds}
## 3. Operations

### 3.1 `spend` — authorization

Insert into the existing `spend` operation, before its step list:

{spend}
Append after the step list:

{spend2}
### 3.2 New operations

{ops}
### 3.3 Amendments to existing operations

**`apply_transaction`, steps to apply a shielded offer, step 3 becomes:**

{apply3}
**`apply_transaction`, steps to apply an unshielded offer, step 1 becomes:**

{apply_u}
**`rollback_last_transaction`, new step 3 (the former step 3 becomes step 4):**

{rollback3}
**`discard_transaction`, step 3 becomes, with a closing paragraph:**

{discard3}
**Transaction submission, prepend:**

{submit}
## 4. Building stages

Insert after *Prepare a swap*:

{stage}
"""
out_path.write_text(head + tail)
print("wrote", out_path, len((head + tail).splitlines()), "lines")
