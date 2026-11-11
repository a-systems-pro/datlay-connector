"""Spec 465b: the reader reads what the deadlines need from the upgrade on, not from the next day's sync: the names of
lost reasons, sources and sales and helpdesk teams, the external ids of helpdesk stages (with the rule that keeps it to
them) and an MO's component moves.

At the end of the update, not after this module: it depends on base and mail only, so CRM, Inventory and Helpdesk may
load after it, and until then their models are not in the registry and the sync would skip them."""

from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    env["datlay.connector"]._sync_reader_access()
