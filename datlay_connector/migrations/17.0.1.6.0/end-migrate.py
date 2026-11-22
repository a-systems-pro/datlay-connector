"""329 §9 item 20: the reader reads a tracked value only through a message it may read from the upgrade on, not from the
next day's sync: its history rule names ``datlay_message_readable`` (models/mail_tracking_value.py).

At the end of the update, as the earlier ones: the sync names the models of every app, which load after this base and
mail module."""

from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    env["datlay.connector"]._sync_reader_access()
