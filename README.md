# leaf-alert

Watches the $LEAF mint every 10 minutes and pushes to an ntfy topic when something changes:

- ORDI discount band changes (>=2x, 1.5-2x, 1.1-1.5x, <1.1x)
- mint crosses 75 / 90 / 99 / 100%
- first non-mint op or marketplace trade in the LEAF indexer
- the LEAF API fails 3 checks in a row

The ntfy topic lives in the `NTFY_TOPIC` repo secret. Run the workflow by hand for a status ping.
