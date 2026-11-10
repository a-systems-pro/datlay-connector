"""Spec 453c: the reader reads the moves an MO was made for from the upgrade on, not from the next day's sync.

At the end of the update, not after this module: it depends on base and mail only, so Inventory may load after it, and
until then ``stock.move`` is not in the registry and the sync would skip it."""

from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    env["datlay.connector"]._sync_reader_access()
