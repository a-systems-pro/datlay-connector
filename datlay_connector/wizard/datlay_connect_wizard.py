from odoo import _, fields, models
from odoo.exceptions import UserError


class DatlayConnectWizard(models.TransientModel):
    """D4.2: the code first; the Datlay account and the person who started it shown; then, on yes, the key."""

    _name = "datlay.connect.wizard"
    _description = "Connect this Odoo to Datlay"

    step = fields.Selection([("code", "Code"), ("confirm", "Confirm"), ("done", "Done")], default="code", required=True)
    code = fields.Char("Code from Datlay")
    tenant = fields.Char("Datlay account", readonly=True)
    owner = fields.Char("Started by", readonly=True)
    reader_kind = fields.Selection(
        [
            ("administrator", "My API key (an administrator's)"),
            ("dedicated", "The Datlay reader (a user that only reads)"),
        ],
        string="Datlay reads with",
        default="administrator",
        required=True,
    )

    def _reopen(self):
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }

    def action_lookup(self):
        self.ensure_one()
        self.env["datlay.connector"]._check_admin()
        code = (self.code or "").strip()
        if not code:
            raise UserError(_("Type the code Datlay showed you."))
        answer = self.env["datlay.connector"]._lookup(code)
        self.write({"step": "confirm", "tenant": answer.get("tenant"), "owner": answer.get("owner")})
        return self._reopen()

    def action_connect(self):
        self.ensure_one()
        if self.step != "confirm" or not self.tenant:
            raise UserError(_("Check the code first: this Odoo connects only to the Datlay account it names."))
        self.env["datlay.connector"]._connect(self.code.strip(), self.reader_kind, self.tenant, self.owner)
        self.write({"step": "done"})
        return self._reopen()

    def action_back(self):
        self.ensure_one()
        self.write({"step": "code", "tenant": False, "owner": False})
        return self._reopen()
