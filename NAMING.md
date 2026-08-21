# dApp payload naming: the `ecld` handle

Ethernity Cloud payloads used a set of triple-underscore magic globals
(`___etny_result___`, `___etny_data_set___`, `___etny_on_input___`). Those
read as enclave internals, not as an API. Every run now also injects a single
namespaced handle, **`ecld`**, that reads like a contract object:

```js
// JavaScript
ecld.result(value)          // end the task with a result
ecld.input                  // the request payload
ecld.caller                 // the data owner's wallet (msg.sender-style)
ecld.onInput = (data) => …  // interactive-session input handler
ecld.state.get(k) / .commit(k, v) / .grant(…)   // ESR state (ESR builds)
ecld.fetch('k1', 'k2')      // standard state-fetch task body
```

```python
# Python
ecld.result(value)
ecld.input
ecld.caller
ecld.on_input = def handler(data): …
ecld.state.get(k) / .commit(k, v) / .grant(…)
ecld.fetch('k1', 'k2')
```

## Mapping

| Legacy name | New name (JS) | New name (Python) | Meaning |
|---|---|---|---|
| `___etny_result___(x)` | `ecld.result(x)` | `ecld.result(x)` | return the task result |
| `___etny_data_set___` | `ecld.input` | `ecld.input` | the request payload |
| `___etny_on_input___` | `ecld.onInput` | `ecld.on_input` | session input handler |
| `task_caller()` / `taskCaller()` | `ecld.caller` | `ecld.caller` | verified submitter wallet |
| `StateRegistry()` / `esr_*` | `ecld.state.*` | `ecld.state.*` | ESR state + ACL |
| `esr_fetch()` / `esrFetch()` | `ecld.fetch(…)` | `ecld.fetch(…)` | state-fetch task |

`ecld.caller` is a **live** accessor: it reads the trustedzone-attested
submitter at access time, so it is correct inside a long-lived session handler
as well as in a one-shot task. It is `None`/`null` in a non-ESR build or local
test.

## Compatibility — nothing breaks

This is **additive**. Every legacy magic name still works exactly as before:
`ecld` is injected *alongside* `___etny_result___`, `___etny_data_set___`, and
`___etny_on_input___`, not in place of them. Deployed dApps (and any payload
already on chain) keep running with no change and no redeploy.

Precedence for the session handler: if a payload sets **both** `ecld.onInput`
and the legacy `___etny_on_input___`, the namespaced `ecld.onInput` wins.

There is no removal date. The legacy names are documented as deprecated
aliases; new examples, the developer guide, and the build lint all use the
`ecld.*` form. Migrate at your own pace, or never — both are supported.

## What changed under the hood

- **securelock exec** (`etny_exec.py` / `etny_exec.js`) builds the `ecld`
  handle in the payload scope and populates `ecld.input` per run.
- **session exec** resolves the handler from `ecld.onInput` (JS) /
  `ecld.on_input` (Python) or the legacy name.
- **trustedzone** `SESSION_HANDLER_NOT_DEFINED` (on-chain code-5 error row)
  now names `ecld.onInput` first, legacy second.
- **build lint** treats `ecld.input` as a taint source for
  `eval`/`exec`/`compile`, exactly like `___etny_data_set___`.
