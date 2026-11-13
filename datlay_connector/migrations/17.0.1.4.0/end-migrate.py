"""Spec 484a: the reader reads what the margin of a month counts from the upgrade on, not from the next day's sync: the
value the stock put on a move, on its layers (``stock.valuation.layer``).

At the end of the update, not after this module: it depends on base and mail only, so Inventory's valuation
(``stock_account``) loads after it, and until then its model is not in the registry and the sync would skip it."""

from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    env["datlay.connector"]._sync_reader_access()
