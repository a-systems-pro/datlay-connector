from odoo import Command, api, models


class ResCompany(models.Model):
    _inherit = "res.company"

    @api.model_create_multi
    def create(self, vals_list):
        """Spec 329 9.12: the Datlay reader sees only its own companies, so a company made later joins it."""
        companies = super().create(vals_list)
        readers = self.env["datlay.connector"]._reader_users()
        if readers:
            readers.sudo().write({"company_ids": [Command.link(c.id) for c in companies]})
        return companies
