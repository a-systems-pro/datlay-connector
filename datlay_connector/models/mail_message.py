"""329 §9 item 21: the Datlay reader's history on a database of many messages (Odoo 20).

Whether a user reads a message is ``res_access_read``, which Odoo 20 searches in one of two ways. A search that names one
to five document models (``MAX_COMODELS_FOR_DOMAIN``) asks each model which of its documents the user reads. Any other
search reads every message it matches as superuser, checks them one by one, and refuses once it meets
``MAX_SEARCH_LIMIT`` (10,000) of them: "Too many messages to be processed".

The reader's history always took the second way. The platform keeps it to the nine models of HISTORY_OF
(``mail_message_id.model``), so every message of D5's documents was checked; kept by the tracked field's model instead,
the reader's own restriction (``("mail_message_id", "access", "read")``) checked the message of every status change the
search matched, all pages at once. Either way the first question failed on a database past 10,000 of them.

The reader reads the messages of HISTORY_OF only (its restriction on ``mail.message``), so for it Odoo's own per-model
way is asked of those models, five at a time: the same documents decide, a message of another model is never one it
reads, and no message is read as superuser to be checked.
"""

from odoo import models
from odoo.fields import Domain

from odoo.addons.base.models.ir_attachment import condition_values
from odoo.addons.mail.models.mail_message import MAX_COMODELS_FOR_DOMAIN

from .datlay_connector import HISTORY_OF, READER_GROUP


class MailMessage(models.Model):
    _inherit = "mail.message"

    def _search_res_access(self, operation, domain_operator):
        if operation != "read" or domain_operator != "in" or not self.env.user._has_group(READER_GROUP):
            return super()._search_res_access(operation, domain_operator)
        domain = self.env.context.get("search_domain")
        if not isinstance(domain, Domain):
            domain = Domain.TRUE
        named = condition_values(self, "model", domain)
        if named is not None and len(named) <= MAX_COMODELS_FOR_DOMAIN:
            # Odoo's own per-model way already, or no model at all
            return super()._search_res_access(operation, domain_operator)
        # the reader's restriction keeps its messages to these models, so no other is asked
        kept = [model for model in HISTORY_OF if model in self.env and (named is None or model in named)]
        if not kept:
            return super()._search_res_access(operation, domain_operator)
        return Domain.OR(
            super(MailMessage, self.with_context(search_domain=domain & Domain("model", "in", part)))._search_res_access(
                operation, domain_operator
            )
            for part in (kept[i:i + MAX_COMODELS_FOR_DOMAIN] for i in range(0, len(kept), MAX_COMODELS_FOR_DOMAIN))
        )
