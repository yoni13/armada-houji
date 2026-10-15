# Built-in iPASS transaction names

`ipass-transaction-metadata.json` is the decoded lookup table returned by Google
Wallet's `g/ipassmifare/gettransactionmetadata` on **2026-10-15**, using the
original signed Wallet **26.37.985574370** in Waydroid. It contains **2,776 rows**.

Each row is:

```text
[system ID, optional location ID, optional terminal prefix, operator name, terminal name]
```

The first three fields are hexadecimal lookup keys. Empty fields have meaning;
keep them. Row order is also significant: Wallet keeps the first terminal name
for duplicate keys. This file preserves the decoded response's order and values.
It contains only lookup keys and names, with no account identifiers, credentials,
card balances or transaction history.

The root-image builder installs it as
`/usr/share/houji/ipass-transaction-metadata.json`. A valid owner-imported table at
`/var/lib/armada-nfc/ipass-transaction-metadata.json` takes precedence. Missing or
invalid imports fall back to this built-in snapshot.

To refresh the snapshot, export a response using the version-specific
`../wallet-26.37-export-metadata.js` and decode it with
`../import-ipass-metadata.py --output <file> response.pb`. Review the decoded
five-field rows, preserve their order, and update this date and the snapshot
test's entry count. Raw authenticated responses and personal name overrides do
not belong in this directory.
