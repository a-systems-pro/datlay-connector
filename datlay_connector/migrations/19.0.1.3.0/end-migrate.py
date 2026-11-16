"""Spec 481 P4: the reader reads the warehouse from the upgrade on, not from the next day's sync: a transfer's lines
(``stock.move.line``), the batches (``stock.picking.batch``, where the database has them) and a batch's history of
its state (the history rules take it at the same sync).

At the end of the update, not after this module: it depends on base and mail only, so Inventory and its batches load
after it, and until then their models are not in the registry and the sync would skip them."""

from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    env["datlay.connector"]._sync_reader_access()
