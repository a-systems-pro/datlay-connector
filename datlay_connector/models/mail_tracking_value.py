"""329b review, spec 329 §9 item 20 (17.0): the Datlay reader reads a tracked value only through a message it may read,
and a message only through its document.

Odoo lets an administrator alone read ``mail.tracking.value``: any other user meets a tracked value inside its message,
and a message only where it reads the message's document (``mail.message._search`` and ``check_access_rule``). The
Datlay reader is given ``mail.tracking.value`` itself (D5), so its rule has to say the same. Odoo 20 says it in the
rule, ``("mail_message_id", "access", "read")``, which the module's 20.0 branch uses. A rule of 17 and 19 cannot: Odoo
evaluates a rule's domain as superuser, so a path through ``mail_message_id`` never meets the document's own rules, and
the reader would read a private task's stage or a payroll entry's posting.

So the reader's rule names ``datlay_message_readable``, a boolean that is never stored and is only searched, the way
Odoo 20 itself gives a rule the user's rights (``mail.message.res_access_read``): Odoo evaluates the rule as superuser
for the same user, and the search goes back to that user's rights with ``sudo(False)``. A value is kept when its message
is of a document of HISTORY_OF that the user reads by that model's own access and rules, archived documents included
(a lost deal's history), which is how Odoo decides a message of a document (``_find_allowed_model_wise``). A message the
user wrote or was sent is Odoo's other way to a message; the reader writes none and is sent none. Each model is asked
once, as a subquery of the search, so no message is checked one by one and a database of many messages costs one
subquery per model. The rule binds the reader's group alone; no other user's reads change.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.osv import expression

from .datlay_connector import HISTORY_OF


class MailTrackingValue(models.Model):
    _inherit = "mail.tracking.value"

    datlay_message_readable = fields.Boolean(
        "Its message is readable (Datlay reader)",
        compute="_compute_datlay_message_readable",
        search="_search_datlay_message_readable",
    )

    def _datlay_readable(self):
        """the tracked values whose message is of a document of HISTORY_OF the current user reads"""
        # the user's own rights, archived documents included, whether or not the caller is superuser (a rule)
        env = self.sudo(False).with_context(active_test=False).env
        per_model = []
        for model in HISTORY_OF:
            if model not in env or not env["ir.model.access"].check(model, "read", False):
                continue
            per_model.append([("model", "=", model), ("res_id", "in", env[model]._search([]))])
        if not per_model:
            return expression.FALSE_DOMAIN
        messages = expression.AND([expression.OR(per_model), [("message_type", "!=", "user_notification")]])
        return [("mail_message_id", "in", env["mail.message"].sudo()._search(messages))]

    # the value is the current user's: without the uid in its cache key, a value computed for one user (an
    # administrator) would be served to the reader's access check in the same transaction (/code-review 11-22)
    @api.depends_context("uid")
    def _compute_datlay_message_readable(self):
        readable = self.sudo().search(expression.AND([[("id", "in", self.ids)], self._datlay_readable()]))
        readable_ids = set(readable.ids)
        for value in self:
            value.datlay_message_readable = value.id in readable_ids

    def _search_datlay_message_readable(self, operator, value):
        if operator in ("=", "!="):
            wanted = {bool(value)}
        elif operator in ("in", "not in"):
            wanted = {bool(v) for v in value}
        else:
            raise UserError(_("Operation not supported"))
        if operator in ("!=", "not in"):
            wanted = {True, False} - wanted
        if wanted == {True, False}:
            return expression.TRUE_DOMAIN
        if not wanted:
            return expression.FALSE_DOMAIN
        readable = self._datlay_readable()
        return readable if True in wanted else ["!"] + readable
