"""Datlay Connector (spec 329 D3, D4, D9, D14).

What the module does in this Odoo, and nothing else:

- connects it to Datlay with an administrator's API key named "Datlay", or with the Datlay reader: a user it makes
  in a group of its own that reads the documents and their history and cannot create, change or delete them;
- posts the connection to the one fixed Datlay address, after its own transaction commits, so Datlay's first
  sign-in meets a key that exists;
- renews the key in one click, proved to Datlay by a renewal secret it keeps since connecting;
- deletes the keys it issued (the previous one when it issues a new one, every one on uninstall) and tells
  Datlay's reader which users hold an API key it did not issue.

It holds none of the company's data and calls no address but Datlay's.
"""

import json
import logging
import secrets
import ssl
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError

from odoo import SUPERUSER_ID, Command, _, api, fields, models
from odoo.exceptions import AccessError, UserError

_logger = logging.getLogger(__name__)

#: D14: the only address the module calls; there is no setting to change it (the tests replace ``_post``)
DATLAY_CONNECT = "https://connect.datlay.io"
TIMEOUT = 15
#: a renewal waits for Datlay to sign in with the new key and read its rights, back in this same Odoo
RENEW_TIMEOUT = 60
#: D3, spec 9.6: Datlay reads the key's expiry by this name
KEY_NAME = "Datlay"
READER_LOGIN = "datlay.reader"
#: the Datlay reader's key lives within its group's duration from 18; Odoo 17 keeps no expiry
READER_KEY_DAYS = 90

#: spec 329 D5 and 9.4: what the Datlay reader reads, the same list as Datlay's own (``domains.ts``)
READ_MODELS = (
    "crm.lead",
    "sale.order",
    "purchase.order",
    "stock.picking",
    "account.move",
    "account.payment",
    "account.partial.reconcile",
    "sale.order.line",
    "purchase.order.line",
    "account.move.line",
    "mail.tracking.value",
    "mail.message",
    "res.partner",
    "res.users",
    "res.company",
    "stock.warehouse",
    "account.journal",
    "ir.model.fields",
    "res.lang",
    "crm.stage",
    "stock.location",
    "stock.picking.type",
    "res.users.apikeys",
    # spec 453 D6: making and services, and the tables their statuses and links are read from; a model this database
    # lacks is skipped (an MO's order through procurement.group to 18, stock.reference from 19; helpdesk is Enterprise)
    "project.task",
    "mrp.production",
    "helpdesk.ticket",
    "project.project",
    "project.task.type",
    "procurement.group",
    "stock.reference",
    "helpdesk.stage",
    # spec 453c: to 18 an MO's own procurement group names no order; the moves it was made for do, by their group (an
    # order's delivery, or a parent MO's component). Spec 465 D14 (Alex 11-11) lifts 453c's "never its product or
    # quantity": an MO's component moves are read for the purchase behind the wait, their product as an id, their
    # quantities and state and the purchase lines they were bought by. Ids and numbers, never a product's name. Spec 484
    # D2 (the margin of a month): a move to or from a customer, its sale line, day, locations, the move it returns and
    # whether the system refunds it, its transfer
    "stock.move",
    # spec 484 D2: the value the stock put on a move, on its layers (14 to 18; 19 keeps it on the move): the layer's
    # move, value, quantity and day, never its description
    "stock.valuation.layer",
    # spec 465 D3: the names of a deal's lost reason, a deal's or an order's source, a sales or a helpdesk team (names
    # only, kept with the connection like the status labels; helpdesk is Enterprise)
    "crm.lost.reason",
    "utm.source",
    "crm.team",
    "helpdesk.team",
    # spec 465 D8.2: Odoo's own Cancelled stage of helpdesk is known by its external id (``helpdesk.stage_cancelled``);
    # the reader reads the external ids of helpdesk stages and of no other model (READER_RULES)
    "ir.model.data",
)
#: D7: the documents whose history is read, and their status fields (453: an MO's components readiness too)
HISTORY_OF = (
    "crm.lead",
    "sale.order",
    "purchase.order",
    "stock.picking",
    "account.move",
    "account.payment",
    "project.task",
    "mrp.production",
    "helpdesk.ticket",
)
STATUS_FIELDS = ("state", "payment_state", "stage_id", "active", "won_status", "lost_reason", "lost_reason_id", "reservation_state")
#: D3, spec 9.11: the journals read; payroll and miscellaneous ones (``general``) never
JOURNAL_TYPES = ("sale", "purchase", "cash", "bank", "credit")

#: the reader's record rules, by model: the history kept to D5's documents and status fields, the journals kept
READER_RULES = {
    # D5: a status change's message only (its author and time), never a message body of the chatter or an e-mail
    "mail.message": [("model", "in", list(HISTORY_OF)), ("tracking_value_ids", "!=", False)],
    "mail.tracking.value": [
        ("mail_message_id.model", "in", list(HISTORY_OF)),
        ("field_id.name", "in", list(STATUS_FIELDS)),
    ],
    "account.move": [("journal_id.type", "in", list(JOURNAL_TYPES))],
    "account.move.line": [("journal_id.type", "in", list(JOURNAL_TYPES))],
    "account.payment": [("journal_id.type", "in", list(JOURNAL_TYPES))],
    "account.partial.reconcile": [
        ("debit_move_id.journal_id.type", "in", list(JOURNAL_TYPES)),
        ("credit_move_id.journal_id.type", "in", list(JOURNAL_TYPES)),
    ],
    # D5: never an attachment (an invoice's PDF), which every internal user reads on a record it reads
    "ir.attachment": [(0, "=", 1)],
    # spec 453 D7: Odoo's task_visibility_rule and project_public_members_rule hide a followers-only project from an
    # internal user, and group rules add up: the reader sees every project's tasks, and still no private task (no
    # project), no other person's own stage
    "project.task": [("project_id", "!=", False)],
    "project.project": [(1, "=", 1)],
    "project.task.type": [("user_id", "=", False)],
    # helpdesk_ticket_user_rule hides a team its members are invited to (Enterprise)
    "helpdesk.ticket": [(1, "=", 1)],
    # spec 465 D8.2: a helpdesk stage's external id, never another model's (a module's records, a user's groups). Odoo
    # reads the table itself as superuser (env.ref, an external id's lookup), and no other user is bound by this rule
    "ir.model.data": [("model", "=", "helpdesk.stage")],
}
#: D5: never an employee's record, a contract, a payslip or an expense, which Odoo lets an internal user read in part
NEVER_READ = (
    # spec 453 D3: a timesheet line names a person and their day; a task's own hours are its sums
    "account.analytic.line",
    "hr.employee",
    "hr.employee.public",
    "hr.contract",
    "hr.version",
    "hr.payslip",
    "hr.payslip.line",
    "hr.expense",
    "hr.expense.sheet",
)
READER_RULES.update({model: [(0, "=", 1)] for model in NEVER_READ})
RULE_PREFIX = "Datlay reader: "
ACCESS_PREFIX = "datlay_reader "


def _answer(raw):
    """an answer as a dict: anything that is not a JSON object is kept as text, never raised"""
    if not raw:
        return {}
    try:
        doc = json.loads(raw)
    except ValueError:
        return {"error": raw[:300]}
    return doc if isinstance(doc, dict) else {"error": raw[:300]}


def _post(path, payload, timeout=TIMEOUT):
    """One JSON POST to Datlay's fixed address, certificates checked. Returns (HTTP status, answer as a dict); a
    network failure is status 0."""
    req = urlrequest.Request(
        url=DATLAY_CONNECT + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "datlay_connector/17.0 (Odoo module)"},
        method="POST",
    )
    try:
        with urlrequest.urlopen(req, timeout=timeout, context=ssl.create_default_context()) as response:
            return response.status, _answer(response.read().decode("utf-8", errors="replace"))
    except HTTPError as exc:
        return exc.code, _answer(exc.read().decode("utf-8", errors="replace") if exc.fp else "")
    except (URLError, OSError) as exc:
        _logger.warning("Datlay: %s not reached: %s", DATLAY_CONNECT, exc)
        return 0, {"error": str(getattr(exc, "reason", exc))}


def normalised_tax_number(vat):
    """The tax number as Datlay hashes it: letters and digits only, upper case (Datlay normalises it again)."""
    return "".join(ch for ch in (vat or "").upper() if ch.isalnum()) or None


class DatlayConnectorKey(models.Model):
    _name = "datlay.connector.key"
    _description = "API key issued by the Datlay module"

    # a key its user deletes in Preferences takes this row with it
    apikey_id = fields.Many2one("res.users.apikeys", required=True, index=True, ondelete="cascade")
    user_id = fields.Many2one("res.users", required=True, ondelete="cascade")


class DatlayConnector(models.Model):
    _name = "datlay.connector"
    _description = "Datlay connection"

    name = fields.Char(default="Datlay", required=True)
    state = fields.Selection(
        [
            ("draft", "Not connected"),
            ("sent", "Sent to Datlay"),
            ("connected", "Connected"),
            ("failed", "Not accepted"),
        ],
        default="draft",
        required=True,
        readonly=True,
    )
    tenant = fields.Char("Datlay account", readonly=True)
    owner = fields.Char("Started by", readonly=True)
    reader_kind = fields.Selection(
        [("administrator", "An administrator's key"), ("dedicated", "The Datlay reader")],
        readonly=True,
    )
    key_user_id = fields.Many2one("res.users", "Key of", readonly=True)
    reader_user_id = fields.Many2one("res.users", "Datlay reader made here", readonly=True)
    key_expires = fields.Datetime("Key expires", readonly=True)
    renew_secret = fields.Char(groups="base.group_system", copy=False)
    #: a renewal whose answer did not arrive: Datlay may hold this secret now, and is asked with it if the current fails
    renew_secret_pending = fields.Char(groups="base.group_system", copy=False)
    sent_at = fields.Datetime(readonly=True)
    last_answer = fields.Char(readonly=True)

    # ------------------------------------------------------------------ helpers

    def _check_admin(self):
        if not self.env.user._is_system():
            raise AccessError(_("Only an administrator connects this Odoo to Datlay."))

    @api.model
    def _get(self):
        rec = self.sudo().search([], limit=1)
        return rec or self.sudo().create({})

    @api.model
    def action_open(self):
        self._check_admin()
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self._get().id,
            "view_mode": "form",
            "target": "current",
        }

    @api.model
    def _base_url(self):
        url = (self.env["ir.config_parameter"].sudo().get_param("web.base.url") or "").rstrip("/")
        if not url.startswith("https://"):
            raise UserError(_(
                "This Odoo's address (web.base.url, %s) is not an https address: Datlay calls only https. "
                "Set it in Settings, then connect."
            ) % (url or "empty"))
        return url

    # ------------------------------------------------------------------ keys

    @api.model
    def _generate_key(self, user, expiry):
        # Odoo 17: (scope, name); a key has no expiry before 18
        keys = self.env["res.users.apikeys"].with_user(user).sudo()
        return keys._generate("rpc", KEY_NAME)

    def _issue_key(self, user, kind):
        """D3: a key named "Datlay" for this user, recorded as the module's own. Returns (key, its id, expiry)."""
        expiry = False
        key = self._generate_key(user, expiry)
        row = self.env["res.users.apikeys"].sudo().search(
            [("user_id", "=", user.id), ("name", "=", KEY_NAME)], order="id desc", limit=1
        )
        self.env["datlay.connector.key"].sudo().create({"apikey_id": row.id, "user_id": user.id})
        return key, row.id, expiry

    @api.model
    def _drop_keys(self, keep=(), only=None):
        """Deletes the keys the module issued: all but those in ``keep``, or ``only`` these."""
        domain = [("apikey_id", "in", list(only))] if only is not None else [("apikey_id", "not in", list(keep))]
        issued = self.env["datlay.connector.key"].sudo().search(domain)
        keys = issued.mapped("apikey_id").exists()
        if keys:
            keys._remove()
        issued.exists().unlink()

    # ------------------------------------------------------------------ the Datlay reader

    @api.model
    def _reader_group(self):
        return self.env.ref("datlay_connector.group_reader")

    @api.model
    def _sync_reader_access(self):
        """D3: the reader's group reads every model of the list this database has, and its rules hold.

        Read only: no row of this group can create, write or unlink. Run at install, at connecting, at renewal and
        daily, so an app installed later is read too.
        """
        group = self._reader_group().sudo()
        access = self.env["ir.model.access"].sudo()
        ir_model = self.env["ir.model"].sudo()
        for model in READ_MODELS:
            if model not in self.env:
                continue
            model_id = ir_model._get_id(model)
            row = access.search([("group_id", "=", group.id), ("model_id", "=", model_id)], limit=1)
            values = {
                "name": ACCESS_PREFIX + model,
                "model_id": model_id,
                "group_id": group.id,
                "perm_read": True,
                "perm_write": False,
                "perm_create": False,
                "perm_unlink": False,
            }
            if row:
                if row.perm_write or row.perm_create or row.perm_unlink or not row.perm_read:
                    row.write(values)
            else:
                access.create(values)
        rules = self.env["ir.rule"].sudo()
        for model, domain in READER_RULES.items():
            if model not in self.env:
                continue
            model_id = ir_model._get_id(model)
            name = RULE_PREFIX + model
            values = {
                "name": name,
                "model_id": model_id,
                "domain_force": repr(domain),
                "groups": [Command.set([group.id])],
            }
            row = rules.with_context(active_test=False).search([("name", "=", name), ("model_id", "=", model_id)], limit=1)
            if row:
                if row.domain_force != values["domain_force"] or row.groups != group or not row.active:
                    row.write({**values, "active": True})
            else:
                rules.create(values)

    @api.model
    def _ensure_reader(self):
        """D3: the Datlay reader, an internal user in the reader's group alone, in every company of the database."""
        group = self._reader_group()
        users = self.env["res.users"].sudo().with_context(active_test=False, no_reset_password=True)
        companies = self.env["res.company"].sudo().search([])
        reader = users.search([("login", "=", READER_LOGIN)], limit=1)
        if reader:
            if not reader.active:
                # brought back by the module (an earlier install made it): archived again on uninstall
                self._get().reader_user_id = reader
            reader.write({
                "active": True,
                "groups_id": [Command.link(group.id)],
                "company_ids": [Command.set(companies.ids)],
            })
            return reader
        reader = users.create({
            "name": "Datlay reader",
            "login": READER_LOGIN,
            "groups_id": [Command.set([self.env.ref("base.group_user").id, group.id])],
            "company_id": self.env.company.id,
            "company_ids": [Command.set(companies.ids)],
        })
        self._get().reader_user_id = reader
        return reader

    @api.model
    def _reader_users(self):
        return self._reader_group().sudo().users

    # ------------------------------------------------------------------ connecting

    @api.model
    def _lookup(self, code):
        """T12: what a code belongs to, asked before anything is made here."""
        status, answer = _post("/datlay/odoo/connect/lookup", {"code": code})
        if status == 200 and answer.get("tenant"):
            return answer
        if status == 404:
            raise UserError(_("Datlay does not know this code: it was used, it expired, or it was typed wrong. Start again in Datlay for a new one."))
        raise UserError(_("Datlay did not answer (%s). Try again in a minute.") % (answer.get("error") or status or _("no connection")))

    def _payload(self, user, key, kind):
        icp = self.env["ir.config_parameter"].sudo()
        return {
            "url": self._base_url(),
            "database": self.env.cr.dbname,
            "login": user.login,
            "key": key,
            "reader": kind,
            "uuid": icp.get_param("database.uuid"),
            "taxNumber": normalised_tax_number(self.env.company.vat),
        }

    @api.model
    def _connect(self, code, kind, tenant, owner):
        """D4.2: the key issued, the previous key of the module deleted, and the post sent once this transaction commits."""
        self._check_admin()
        self._base_url()
        conn = self._get()
        if kind == "dedicated":
            self._sync_reader_access()
            user = self._ensure_reader()
        else:
            user = self.env.user
        key, key_id, expiry = conn._issue_key(user, kind)
        # spec 329 9.16: connecting again, the module deletes the key it issued before (Datlay never deletes one)
        self._drop_keys(keep=[key_id])
        secret = secrets.token_hex(32)
        conn.sudo().write({
            "state": "sent",
            "tenant": tenant,
            "owner": owner,
            "reader_kind": kind,
            "key_user_id": user.id,
            "key_expires": expiry,
            "renew_secret": secret,
            "sent_at": fields.Datetime.now(),
            "last_answer": False,
        })
        payload = dict(conn._payload(user, key, kind), code=code, renewSecret=secret)
        conn._send_after_commit("/datlay/odoo/connect/register", payload, "register", {"key_id": key_id})
        return conn

    def action_renew(self):
        """D3: the key renewed in one click: a new one issued and sent with the renewal secret. The old one goes only
        once Datlay took the new one; a refusal deletes the new one; an answer that did not arrive keeps both."""
        self.ensure_one()
        self._check_admin()
        conn = self.sudo()
        if conn.state not in ("sent", "connected") or not conn.renew_secret or not conn.key_user_id:
            raise UserError(_("This Odoo is not connected through the module: connect it with a new code from Datlay."))
        kind = conn.reader_kind or "administrator"
        if kind == "dedicated":
            self._sync_reader_access()
        key, key_id, expiry = conn._issue_key(conn.key_user_id, kind)
        nxt = secrets.token_hex(32)
        payload = dict(conn._payload(conn.key_user_id, key, kind), renewSecret=conn.renew_secret, nextRenewSecret=nxt)
        conn.write({"sent_at": fields.Datetime.now(), "last_answer": False})
        conn._send_after_commit(
            "/datlay/odoo/connect/renew",
            payload,
            "renew",
            {
                "key_id": key_id,
                "next": nxt,
                "pending": conn.renew_secret_pending or False,
                "expiry": fields.Datetime.to_string(expiry) if expiry else False,
            },
        )
        return True

    def _send_after_commit(self, path, payload, what, extra):
        """The post leaves only once this transaction is committed: Datlay signs in with a key that exists."""
        registry = self.env.registry
        conn_id = self.id

        def send():
            with registry.cursor() as cr:
                env = api.Environment(cr, SUPERUSER_ID, {})
                env["datlay.connector"].browse(conn_id)._deliver(path, payload, what, extra)

        self.env.cr.postcommit.add(send)

    def _deliver(self, path, payload, what, extra):
        """The post sent and what Datlay answered settled, in the cursor it is given (after the commit)."""
        if what == "renew":
            status, answer = _post(path, payload, RENEW_TIMEOUT)
            if answer.get("refusal") == "renew" and extra.get("pending"):
                # an earlier renewal's answer was lost and Datlay took it: its secret is the one Datlay holds
                status, answer = _post(path, dict(payload, renewSecret=extra["pending"]), RENEW_TIMEOUT)
        else:
            status, answer = _post(path, payload)
        return self._after_post(what, status, answer, extra)

    def _after_post(self, what, status, answer, extra):
        """What Datlay answered, kept on the record; the keys and the renewal secret settled by it."""
        self.ensure_one()
        words = answer.get("error") or ""
        if what == "register":
            ok = status == 202
            if not ok:
                # Datlay did not take it: the key just issued goes (a refusal after the 202 is told on Datlay's page)
                self._drop_keys(only=[extra["key_id"]])
            self.write({
                "state": "sent" if ok else "failed",
                "last_answer": _("Datlay took the connection and is connecting it.") if ok else (words or str(status)),
            })
            return ok
        if status == 200:
            self._drop_keys(keep=[extra["key_id"]])
            self.write({
                "state": "connected",
                "renew_secret": extra["next"],
                "renew_secret_pending": False,
                "key_expires": extra.get("expiry") or False,
                "last_answer": _("Datlay took the new key."),
            })
            return True
        if status == 0 or status >= 500:
            # no answer: Datlay may have taken it. Both keys stay, and the next renewal tries this secret too
            self.write({
                "renew_secret_pending": extra["next"],
                "last_answer": _("Datlay's answer did not arrive (%s). Renew again to settle it.") % (words or status),
            })
            return False
        # a refusal: the new key goes, the working one and the state stay
        self._drop_keys(only=[extra["key_id"]])
        self.write({"last_answer": words or str(status)})
        return False

    # ------------------------------------------------------------------ D9: who holds another key

    @api.model
    def datlay_users_with_keys(self, user_ids):
        """D9, spec 9.5: of these users, those holding an API key the module did not issue; never the caller.

        Called by Datlay's reader (an administrator's key or the Datlay reader): Odoo shows API keys to
        administrators only, so this one boolean per user is all that leaves.
        """
        me = self.env.user
        if not (me._is_system() or me.has_group("datlay_connector.group_reader")):
            raise AccessError(_("Only Datlay's reader asks this."))
        ids = [u for u in (user_ids or []) if isinstance(u, int) and not isinstance(u, bool)][:20000]
        issued = self.env["datlay.connector.key"].sudo().search([]).mapped("apikey_id").ids
        keys = self.env["res.users.apikeys"].sudo()
        domain = [("user_id", "in", ids), ("id", "not in", issued)]
        if "expiration_date" in keys._fields:
            domain += ["|", ("expiration_date", "=", False), ("expiration_date", ">", fields.Datetime.now())]
        holders = {row["user_id"][0] for row in keys.search_read(domain, ["user_id"]) if row["user_id"]}
        holders.discard(self.env.uid)
        return sorted(holders)

    # ------------------------------------------------------------------ jobs and uninstall

    @api.model
    def _cron_sync_reader(self):
        if self._reader_users():
            self._sync_reader_access()

    @api.model
    def _uninstall_cleanup(self):
        self._drop_keys(keep=[])
        group = self.env.ref("datlay_connector.group_reader", raise_if_not_found=False)
        self.env["ir.rule"].sudo().with_context(active_test=False).search([("name", "=like", RULE_PREFIX + "%")]).unlink()
        if group:
            self.env["ir.model.access"].sudo().search([("group_id", "=", group.id)]).unlink()
        made = self.sudo().search([]).mapped("reader_user_id")
        if made:
            made.write({"active": False})
