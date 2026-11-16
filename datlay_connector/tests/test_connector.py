"""Spec 329 T-8 on Odoo 20.0 (329b: accesses and rules are one ``ir.access``): TC-329-01 (the reader made, the tenant seen before anything is made, uninstall cleans
up), TC-329-02 (another tenant's code shows that tenant; no address but Datlay's), TC-329-08 (the reader's group
cannot create, write or unlink a document, and reads the history of D5's documents only). Spec 453 T-5: tasks of every
project and no private one, no person's own stage, no timesheet line, an MO and its history. 329 §9 item 21: the history
read on a database past Odoo 20's 10,000 messages. Spec 465b: the names of reasons, sources and teams, helpdesk stages'
external ids and no other, the deadlines on the documents, and the purchase behind a waiting MO (D14), ids and numbers
only. Spec 484a: what the margin of a month counts, a sale
line's quantities, money, own cost and stored margin, a move to or from a customer with the value the stock put on it,
a transfer's carrier price."""

import importlib.util
import json
import os
from datetime import timedelta
from unittest.mock import MagicMock, patch

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError
from odoo.tests.common import TransactionCase, new_test_user, tagged
from odoo.tools import SQL

from odoo.addons.datlay_connector.models import datlay_connector as dc
from odoo.addons.mail.models.mail_message import MAX_SEARCH_LIMIT

POST = "odoo.addons.datlay_connector.models.datlay_connector._post"
#: the real ones, kept before any test replaces them
REAL_POST = dc._post
REAL_SEND = dc.DatlayConnector._send_after_commit
DOCUMENTS = ("sale.order", "purchase.order", "stock.picking", "account.move", "account.payment", "crm.lead", "project.task", "mrp.production", "helpdesk.ticket")
#: spec 453: the tables a task's, an MO's and a ticket's statuses and links are read from
TABLES = ("project.project", "project.task.type", "procurement.group", "stock.reference", "helpdesk.stage", "stock.move")
#: models every internal user may write by Odoo's own rights: a task (project_todo's to-dos)
EVERY_USER_WRITES = ("project.task",)
LINES = ("sale.order.line", "purchase.order.line", "account.move.line")
#: spec 465 D3: the tables whose names are read (helpdesk is Enterprise)
NAMES = ("crm.lost.reason", "utm.source", "crm.team", "helpdesk.team")
#: spec 465 D3: the deadlines and the facts beside them, on documents the reader already reads
DEADLINE_FIELDS = {
    "crm.lead": ("date_deadline", "probability", "source_id", "team_id", "lost_reason_id"),
    "sale.order": ("validity_date", "commitment_date", "delivery_status", "invoice_status", "source_id", "team_id"),
    "purchase.order": ("date_planned", "receipt_status", "invoice_status"),
    "account.move": ("invoice_date_due",),
    "helpdesk.ticket": ("priority", "team_id", "sla_reached_late", "sla_reached", "sla_fail", "first_response_hours"),
    "res.company": ("currency_id",),
}
#: on 20 an order's delivery status is Sales' own and a purchase's receipt status Purchase's (Inventory's to 19)
#: spec 465 D14: a component move and the purchase line it waits on, ids and numbers only
MOVE_FIELDS = ("raw_material_production_id", "product_id", "product_uom_qty", "quantity", "state", "created_purchase_line_ids")
PURCHASE_LINE_FIELDS = ("product_id", "product_qty", "qty_received", "date_planned", "state")
#: spec 484 D2: what the margin of a month reads: a sale line's ids, quantities and money (its own cost and the margin
#: stored on it are sale_margin's), a move to or from a customer with the value the stock put on it (from 19 the move's
#: own `value`, `is_in` and `is_out`), a transfer's carrier price
MARGIN_FIELDS = {
    "sale.order.line": (
        "order_id", "product_id", "product_type", "product_uom_qty", "qty_delivered", "price_subtotal", "purchase_price",
        "margin", "is_delivery", "is_downpayment", "state", "currency_id", "company_id", "create_date", "write_date",
    ),
    "stock.move": (
        "sale_line_id", "date", "location_id", "location_dest_id", "origin_returned_move_id", "to_refund", "picking_id",
        "product_id", "quantity", "state", "value", "is_in", "is_out", "write_date",
    ),
    "stock.picking": ("carrier_price", "sale_id", "picking_type_code", "date_done"),
}
#: the version whose end migration gives an installed database the reader's new rights at its update
MIGRATION = "20.0.1.2.0"


@tagged("post_install", "-at_install")
class TestDatlayConnector(TransactionCase):
    def setUp(self):
        super().setUp()
        self.env["ir.config_parameter"].sudo().set_str("web.base.url", "https://odoo.example.com")
        self.posts = []

        def fake_post(path, payload, timeout=None):
            self.posts.append((path, payload))
            if path.endswith("/lookup"):
                return 200, {"tenant": "Acme", "owner": "owner@acme.example", "expiresAt": "2026-11-05T10:00:00Z"}
            if path.endswith("/register"):
                return 202, {"state": "registered"}
            return 200, {"connectionId": 1}

        patcher = patch(POST, side_effect=fake_post)
        self.post = patcher.start()
        self.addCleanup(patcher.stop)
        # what the module leaves for after the commit is kept here and delivered by _sent(), in this test's cursor
        # (a TransactionCase never commits, and a cursor of its own would not see the test's rows)
        self.pending = []
        pending = self.pending

        def later(conn, path, payload, what, extra):
            pending.append((conn, path, payload, what, extra))

        hold = patch.object(type(self.env["datlay.connector"]), "_send_after_commit", autospec=True, side_effect=later)
        hold.start()
        self.addCleanup(hold.stop)
        self.connector = self.env["datlay.connector"]
        self.keys = self.env["res.users.apikeys"].sudo()

    def _wizard(self, kind="administrator"):
        wizard = self.env["datlay.connect.wizard"].create({"code": "ABCD-EFGH", "reader_kind": kind})
        wizard.action_lookup()
        return wizard

    def _deliver(self):
        """the posts the module left for after the commit, sent now as the commit would"""
        while self.pending:
            conn, path, payload, what, extra = self.pending.pop(0)
            conn._deliver(path, payload, what, extra)

    def _sent(self, path):
        self._deliver()
        return [p for (pp, p) in self.posts if pp == path]

    # -------------------------------------------------------------- TC-329-01, 02

    def test_01_the_code_shows_the_tenant_before_anything_is_made(self):
        keys_before = self.keys.search_count([])
        wizard = self._wizard()
        self.assertEqual(wizard.step, "confirm")
        self.assertEqual((wizard.tenant, wizard.owner), ("Acme", "owner@acme.example"))
        self.assertEqual(self.keys.search_count([]), keys_before)
        self.assertFalse(self.env["res.users"].with_context(active_test=False).search([("login", "=", dc.READER_LOGIN)]))
        self.assertEqual([p for p, _ in self.posts], ["/datlay/odoo/connect/lookup"])

    def test_02_no_address_but_datlays(self):
        response = MagicMock()
        response.status = 200
        response.read.return_value = json.dumps({"tenant": "Acme"}).encode()
        response.__enter__.return_value = response
        # the real _post (taken before the patch): the address is the constant and nothing else
        with patch("odoo.addons.datlay_connector.models.datlay_connector.urlrequest.urlopen", return_value=response) as urlopen:
            status, answer = REAL_POST("/datlay/odoo/connect/lookup", {"code": "X"})
        self.assertEqual((status, answer), (200, {"tenant": "Acme"}))
        req = urlopen.call_args[0][0]
        self.assertEqual(req.full_url, "https://connect.datlay.io/datlay/odoo/connect/lookup")
        self.assertNotIn("datlay_connector.endpoint", self.env["ir.config_parameter"].sudo().search([]).mapped("key"))

    def test_03_an_unknown_code_makes_nothing(self):
        self.post.side_effect = lambda path, payload, *timeout: (404, {"error": "This code is not open"})
        wizard = self.env["datlay.connect.wizard"].create({"code": "WRONG"})
        with self.assertRaises(UserError):
            wizard.action_lookup()
        with self.assertRaises(UserError):
            wizard.action_connect()

    def test_04_an_administrators_key_named_datlay_is_posted_after_the_commit(self):
        wizard = self._wizard()
        wizard.action_connect()
        mine = self.keys.search([("user_id", "=", self.env.uid), ("name", "=", dc.KEY_NAME)])
        self.assertEqual(len(mine), 1)
        # nothing has left before the commit
        self.assertEqual([p for p, _ in self.posts], ["/datlay/odoo/connect/lookup"])
        (payload,) = self._sent("/datlay/odoo/connect/register")
        self.assertEqual(payload["code"], "ABCD-EFGH")
        self.assertEqual(payload["url"], "https://odoo.example.com")
        self.assertEqual(payload["database"], self.env.cr.dbname)
        self.assertEqual(payload["login"], self.env.user.login)
        self.assertEqual(payload["reader"], "administrator")
        # Odoo 20 appends `_oak` to a key (`API_KEY_SUFFIX`), which Datlay takes as Odoo's shape
        self.assertRegex(payload["key"], r"^[0-9a-f]{40}_oak$")
        self.assertRegex(payload["renewSecret"], r"^[0-9a-f]{64}$")
        self.assertEqual(payload["uuid"], self.env["ir.config_parameter"].sudo().get_str("database.uuid"))
        conn = self.connector._get()
        self.assertEqual(conn.state, "sent")
        self.assertEqual(conn.renew_secret, payload["renewSecret"])

    def test_05_connecting_again_deletes_the_key_it_issued_before(self):
        self._wizard().action_connect()
        first = self.keys.search([("user_id", "=", self.env.uid), ("name", "=", dc.KEY_NAME)])
        self._wizard().action_connect()
        now = self.keys.search([("user_id", "=", self.env.uid), ("name", "=", dc.KEY_NAME)])
        self.assertEqual(len(now), 1)
        self.assertNotEqual(now, first)
        self.assertEqual(self.env["datlay.connector.key"].search([]).mapped("apikey_id"), now)

    def test_06_a_plain_address_is_refused_before_a_key_is_made(self):
        self.env["ir.config_parameter"].sudo().set_str("web.base.url", "http://odoo.example.com")
        wizard = self._wizard()
        keys_before = self.keys.search_count([])
        with self.assertRaises(UserError):
            wizard.action_connect()
        self.assertEqual(self.keys.search_count([]), keys_before)

    def test_07_only_an_administrator_connects(self):
        clerk = new_test_user(self.env, login="datlay_clerk", groups="base.group_user")
        with self.assertRaises(AccessError):
            self.connector.with_user(clerk)._connect("X", "administrator", "Acme", "o@a.example")

    def test_08_the_post_waits_for_the_commit(self):
        conn = self.connector._get()
        before = len(self.env.cr.postcommit._funcs)
        REAL_SEND(conn, "/datlay/odoo/connect/register", {"code": "X"}, "register", {})
        self.assertEqual(len(self.env.cr.postcommit._funcs), before + 1)
        self.assertEqual(self.posts, [])
        self.env.cr.postcommit.clear()

    # -------------------------------------------------------------- the Datlay reader (TC-329-08)

    def _reader(self):
        self._wizard("dedicated").action_connect()
        return self.env["res.users"].search([("login", "=", dc.READER_LOGIN)])

    def test_10_the_reader_is_made_internal_in_its_group_alone_with_a_key_of_90_days(self):
        reader = self._reader()
        self.assertTrue(reader)
        self.assertFalse(reader.share)
        # an internal user's group and the reader's; Odoo 20, as 17, puts a user of several companies in
        # "multi-company" by itself, which gives no right on a document
        expected = {self.env.ref("base.group_user").id, self.env.ref("datlay_connector.group_reader").id}
        if len(reader.company_ids) > 1:
            expected.add(self.env.ref("base.group_multi_company").id)
        self.assertEqual(set(reader.group_ids.ids), expected)
        self.assertEqual(reader.company_ids, self.env["res.company"].search([]))
        key = self.keys.search([("user_id", "=", reader.id), ("name", "=", dc.KEY_NAME)])
        self.assertEqual(len(key), 1)
        self.assertTrue(key.expiration_date)
        (payload,) = self._sent("/datlay/odoo/connect/register")
        self.assertEqual((payload["login"], payload["reader"]), (dc.READER_LOGIN, "dedicated"))
        self.assertEqual(self.connector._get().reader_user_id, reader)

    def test_11_the_reader_reads_the_documents_and_cannot_change_them(self):
        reader = self._reader()
        present = [m for m in DOCUMENTS + LINES if m in self.env]
        for model in present:
            records = self.env[model].with_user(reader)
            self.assertTrue(records.has_access("read"), model)
            # project_todo (installed with project from 17) gives every internal user its own to-dos: a task's write
            # right is every internal user's, the reader's group adds none (test_15)
            if model in EVERY_USER_WRITES:
                continue
            for operation in ("write", "create", "unlink"):
                self.assertFalse(records.has_access(operation), "%s %s" % (model, operation))
        for model in ("mail.tracking.value", "res.users.apikeys", "ir.model.fields") + tuple(m for m in TABLES if m in self.env):
            self.assertTrue(self.env[model].with_user(reader).has_access("read"), model)

    def test_12_the_reader_reads_the_history_of_d5s_documents_only(self):
        reader = self._reader()
        partner = self.env["res.partner"].create({"name": "Somebody"})
        message = self.env["mail.message"].sudo().create({"model": "res.partner", "res_id": partner.id, "message_type": "notification"})
        name_field = self.env["ir.model.fields"]._get("res.partner", "name")
        hidden = self.env["mail.tracking.value"].sudo().create({
            "field_id": name_field.id, "mail_message_id": message.id, "old_value_char": "a", "new_value_char": "b",
        })
        seen = self.env["mail.tracking.value"].with_user(reader).search([("id", "=", hidden.id)])
        self.assertFalse(seen)
        self.assertFalse(self.env["mail.message"].with_user(reader).search([("id", "=", message.id)]))
        if "sale.order" in self.env:
            order = self.env["sale.order"].create({"partner_id": partner.id})
            msg = self.env["mail.message"].sudo().create({"model": "sale.order", "res_id": order.id, "message_type": "notification"})
            state = self.env["ir.model.fields"]._get("sale.order", "state")
            shown = self.env["mail.tracking.value"].sudo().create({
                "field_id": state.id, "mail_message_id": msg.id, "old_value_char": "draft", "new_value_char": "sale",
            })
            self.assertEqual(self.env["mail.tracking.value"].with_user(reader).search([("id", "=", shown.id)]), shown)

    def test_13_the_rules_keep_payroll_and_miscellaneous_journals_out(self):
        reader = self._reader()
        if "account.move" not in self.env:
            self.skipTest("accounting is not installed")
        group = self.env.ref("datlay_connector.group_reader")
        rules = self.env["ir.access"].search([("name", "=like", dc.RULE_PREFIX + "%")])
        by_model = {r.model_id.model: r for r in rules}
        for model in ("account.move", "account.move.line", "account.payment", "account.partial.reconcile"):
            self.assertIn(model, by_model)
            self.assertNotIn("general", by_model[model].domain)
            self.assertIn("journal_id.type", by_model[model].domain)
            # a restriction (no group), whose domain binds the reader's group alone
            self.assertFalse(by_model[model].group_id)
            self.assertIn("%d in user.all_group_ids.ids" % group.id, by_model[model].domain)
        # it binds the reader and nobody else: an accountant's reads keep every journal
        accountant = new_test_user(self.env, login="datlay_accountant", groups="base.group_user,account.group_account_manager")
        self.assertIn("journal_id.type", str(self.env["account.move"].with_user(reader)._access_domain("read")))
        self.assertNotIn("journal_id.type", str(self.env["account.move"].with_user(accountant)._access_domain("read")))

    def test_19_the_reader_reads_no_history_of_a_document_it_cannot_read(self):
        """329b review: on 20 a tracked value is read through its message, and a message through its document"""
        if "project.task" not in self.env:
            self.skipTest("projects are not installed")
        reader = self._reader()
        project = self.env["project.project"].create({"name": "Datlay Seen"})
        seen = self.env["project.task"].create({"name": "In a project", "project_id": project.id})
        hidden = self.env["project.task"].create({"name": "A person's own", "project_id": False})
        self.assertFalse(self.env["project.task"].with_user(reader).search([("id", "=", hidden.id)]))
        field = self.env["ir.model.fields"]._get("project.task", "stage_id")
        values = {}
        for task in (seen, hidden):
            message = self.env["mail.message"].sudo().create({"model": "project.task", "res_id": task.id, "message_type": "notification"})
            values[task] = self.env["mail.tracking.value"].sudo().create({
                "field_id": field.id, "mail_message_id": message.id, "old_value_char": "New", "new_value_char": "Done",
            })
        Tracking = self.env["mail.tracking.value"].with_user(reader)
        self.assertEqual(Tracking.search([("id", "=", values[seen].id)]), values[seen])
        self.assertFalse(Tracking.search([("id", "=", values[hidden].id)]))
        self.assertFalse(self.env["mail.message"].with_user(reader).search([("model", "=", "project.task"), ("res_id", "=", hidden.id)]))

    def test_14_a_company_made_later_joins_the_reader(self):
        reader = self._reader()
        company = self.env["res.company"].create({"name": "Later Ltd"})
        self.assertIn(company, reader.company_ids)

    def test_15_the_group_never_gets_a_write_right(self):
        self._reader()
        group = self.env.ref("datlay_connector.group_reader")
        access = self.env["ir.access"].search([("group_id", "=", group.id)])
        self.assertTrue(access)
        self.assertFalse(access.filtered(lambda a: a.for_write or a.for_create or a.for_unlink))
        # a write right given by hand is taken back by the next sync
        access[:1].operation = "ru"
        self.connector._sync_reader_access()
        self.assertFalse(access.filtered(lambda a: a.for_write))
        # and one that never read goes, rather than becoming a read (329b review)
        partner_bank = self.env["ir.model"]._get_id("res.partner.bank")
        given = self.env["ir.access"].create({"name": "by hand", "model_id": partner_bank, "group_id": group.id, "operation": "c"})
        self.connector._sync_reader_access()
        self.assertFalse(given.exists())
        self.assertFalse(self.env["ir.access"].search([("group_id", "=", group.id), ("model_id", "=", partner_bank)]))

    # -------------------------------------------------------------- D9

    def test_20_who_holds_a_key_the_module_did_not_issue(self):
        reader = self._reader()
        clerk = new_test_user(self.env, login="datlay_clerk2", groups="base.group_user")
        self.connector._generate_key(clerk, False)
        out = self.connector.with_user(reader).datlay_users_with_keys([clerk.id, reader.id, self.env.uid])
        self.assertEqual(out, [clerk.id])
        with self.assertRaises(AccessError):
            self.connector.with_user(clerk).datlay_users_with_keys([clerk.id])

    # -------------------------------------------------------------- renewal

    def test_30_renewal_replaces_the_key_only_once_datlay_took_it(self):
        self._wizard().action_connect()
        self._sent("/datlay/odoo/connect/register")
        conn = self.connector._get()
        old_secret = conn.renew_secret
        old = self.keys.search([("user_id", "=", self.env.uid), ("name", "=", dc.KEY_NAME)])
        conn.action_renew()
        (payload,) = self._sent("/datlay/odoo/connect/renew")
        self.assertEqual(payload["renewSecret"], old_secret)
        self.assertNotEqual(payload["nextRenewSecret"], old_secret)
        now = self.keys.search([("user_id", "=", self.env.uid), ("name", "=", dc.KEY_NAME)])
        self.assertEqual(len(now), 1)
        self.assertNotEqual(now, old)
        self.assertEqual(conn.renew_secret, payload["nextRenewSecret"])
        self.assertEqual(conn.state, "connected")

    def test_31_a_refused_renewal_keeps_the_working_key(self):
        self._wizard().action_connect()
        self._sent("/datlay/odoo/connect/register")
        conn = self.connector._get()
        old_secret = conn.renew_secret
        old = self.keys.search([("user_id", "=", self.env.uid), ("name", "=", dc.KEY_NAME)])
        self.post.side_effect = lambda path, payload, *timeout: (403, {"error": "the renewal secret does not match"})
        conn.action_renew()
        self._deliver()
        self.assertEqual(self.keys.search([("user_id", "=", self.env.uid), ("name", "=", dc.KEY_NAME)]), old)
        self.assertEqual(conn.renew_secret, old_secret)
        self.assertIn("does not match", conn.last_answer)

    def test_32_a_renewal_whose_answer_was_lost_is_settled_by_the_next(self):
        self._wizard().action_connect()
        self._sent("/datlay/odoo/connect/register")
        conn = self.connector._get()
        first_secret = conn.renew_secret
        self.post.side_effect = lambda path, payload, *timeout: (0, {"error": "timed out"})
        conn.action_renew()
        self._deliver()
        lost_next = conn.renew_secret_pending
        self.assertTrue(lost_next)
        self.assertEqual(conn.renew_secret, first_secret)
        self.assertEqual(len(self.keys.search([("user_id", "=", self.env.uid), ("name", "=", dc.KEY_NAME)])), 2)
        # Datlay had taken the lost one: the current secret is refused, the pending one is Datlay's
        tried = []

        def datlay(path, payload, timeout=None):
            tried.append(payload["renewSecret"])
            if payload["renewSecret"] == lost_next:
                return 200, {"connectionId": 1}
            return 422, {"refusal": "renew", "error": "the renewal secret does not match"}

        self.post.side_effect = datlay
        conn.action_renew()
        self._deliver()
        self.assertEqual(tried, [first_secret, lost_next])
        self.assertEqual(len(self.keys.search([("user_id", "=", self.env.uid), ("name", "=", dc.KEY_NAME)])), 1)
        self.assertFalse(conn.renew_secret_pending)
        self.assertEqual(conn.state, "connected")

    def test_33_renewal_is_offered_only_to_a_connection_datlay_took(self):
        self.post.side_effect = lambda path, payload, *timeout: (
            (200, {"tenant": "Acme", "owner": "o@a.example"}) if path.endswith("/lookup") else (503, {"error": "not queued"})
        )
        self._wizard().action_connect()
        self._deliver()
        conn = self.connector._get()
        self.assertEqual(conn.state, "failed")
        # the key it issued for the refused post is gone
        self.assertFalse(self.keys.search([("user_id", "=", self.env.uid), ("name", "=", dc.KEY_NAME)]))
        with self.assertRaises(UserError):
            conn.action_renew()

    def test_34_an_answer_that_is_not_a_json_object_is_text(self):
        self.assertEqual(dc._answer("<html>maintenance</html>"), {"error": "<html>maintenance</html>"})
        self.assertEqual(dc._answer("[1, 2]"), {"error": "[1, 2]"})
        self.assertEqual(dc._answer('{"tenant": "Acme"}'), {"tenant": "Acme"})

    def test_16_the_reader_sees_no_message_body_and_no_attachment(self):
        reader = self._reader()
        partner = self.env["res.partner"].create({"name": "Customer"})
        target = partner
        if "sale.order" in self.env:
            target = self.env["sale.order"].create({"partner_id": partner.id})
        chatter = self.env["mail.message"].sudo().create({
            "model": target._name, "res_id": target.id, "message_type": "comment", "body": "<p>the price we agreed</p>",
        })
        attachment = self.env["ir.attachment"].sudo().create({
            "name": "invoice.pdf", "res_model": target._name, "res_id": target.id, "raw": b"%PDF-1.4",
        })
        self.assertFalse(self.env["mail.message"].with_user(reader).search([("id", "=", chatter.id)]))
        self.assertFalse(self.env["ir.attachment"].with_user(reader).search([("id", "=", attachment.id)]))

    def test_18_the_reader_reads_no_employee_contract_or_payslip(self):
        reader = self._reader()
        present = [m for m in dc.NEVER_READ if m in self.env]
        if not present:
            self.skipTest("no HR app installed")
        for model in present:
            # no access at all, or nothing found: either way nothing is read
            try:
                found = self.env[model].with_user(reader).search([])
            except AccessError:
                found = self.env[model]
            self.assertFalse(found, model)

    def test_17_a_reader_brought_back_is_archived_again_on_uninstall(self):
        reader = self._reader()
        reader.active = False
        self.connector._get().reader_user_id = False
        self._wizard("dedicated").action_connect()
        self.assertTrue(reader.active)
        self.assertEqual(self.connector._get().reader_user_id, reader)

    # -------------------------------------------------------------- spec 453: making and services

    def test_50_the_reader_reads_every_projects_tasks_and_no_private_one(self):
        if "project.task" not in self.env:
            self.skipTest("projects are not installed")
        reader = self._reader()
        clerk = new_test_user(self.env, login="datlay_clerk4", groups="base.group_user")
        hidden = self.env["project.project"].create({"name": "Followers only", "privacy_visibility": "followers"})
        task = self.env["project.task"].create({"name": "Work", "project_id": hidden.id})
        # a private task has no project: a person's own note
        private = self.env["project.task"].create({"name": "Mine", "user_ids": [(6, 0, [clerk.id])]})
        self.assertFalse(private.project_id)
        tasks = self.env["project.task"].with_user(reader)
        self.assertEqual(tasks.search([("id", "=", task.id)]), task)
        self.assertFalse(tasks.search([("id", "=", private.id)]))
        self.assertEqual(self.env["project.project"].with_user(reader).search([("id", "=", hidden.id)]), hidden)

    def test_51_the_reader_sees_no_persons_own_stage(self):
        if "project.task.type" not in self.env or "user_id" not in self.env["project.task.type"]._fields:
            self.skipTest("no personal stages")
        reader = self._reader()
        clerk = new_test_user(self.env, login="datlay_clerk5", groups="base.group_user")
        personal = self.env["project.task.type"].create({"name": "My own", "user_id": clerk.id})
        # a stage made outside a project is its maker's own (Odoo's default): a project's stage is shared
        project = self.env["project.project"].create({"name": "Shared stages"})
        shared = self.env["project.task.type"].create({"name": "Shared", "project_ids": [(6, 0, [project.id])]})
        self.assertFalse(shared.user_id)
        stages = self.env["project.task.type"].with_user(reader)
        self.assertFalse(stages.search([("id", "=", personal.id)]))
        self.assertEqual(stages.search([("id", "=", shared.id)]), shared)

    def test_52_the_reader_reads_no_timesheet_line(self):
        if "account.analytic.line" not in self.env:
            self.skipTest("analytic accounting is not installed")
        reader = self._reader()
        plan = self.env["account.analytic.plan"].create({"name": "Datlay test plan"})
        account = self.env["account.analytic.account"].create({"name": "Datlay test", "plan_id": plan.id})
        line = self.env["account.analytic.line"].create({"name": "Two hours", "account_id": account.id, "unit_amount": 2})
        try:
            found = self.env["account.analytic.line"].with_user(reader).search([("id", "=", line.id)])
        except AccessError:
            found = self.env["account.analytic.line"]
        self.assertFalse(found)

    def test_53_the_reader_reads_an_mo_and_its_history(self):
        if "mrp.production" not in self.env:
            self.skipTest("manufacturing is not installed")
        reader = self._reader()
        product = self.env["product.product"].create({"name": "Table", "type": "consu"})
        mo = self.env["mrp.production"].create({"product_id": product.id, "product_qty": 1, "uom_id": product.uom_id.id})
        self.assertEqual(self.env["mrp.production"].with_user(reader).search([("id", "=", mo.id)]), mo)
        message = self.env["mail.message"].sudo().create({"model": "mrp.production", "res_id": mo.id, "message_type": "notification"})
        for name, old, new in (("state", "draft", "confirmed"), ("reservation_state", "confirmed", "assigned")):
            field = self.env["ir.model.fields"]._get("mrp.production", name)
            value = self.env["mail.tracking.value"].sudo().create({
                "field_id": field.id, "mail_message_id": message.id, "old_value_char": old, "new_value_char": new,
            })
            self.assertEqual(self.env["mail.tracking.value"].with_user(reader).search([("id", "=", value.id)]), value, name)

    # -------------------------------------------------------------- 329 §9 item 21: a database of many messages

    def _bulk_history(self, record, field, count):
        """``count`` status changes of ``record``, each a message of its own with one tracked value, written in one
        statement (the ORM takes minutes for that many), by the administrator: never the reader's own messages"""
        self.env.flush_all()
        self.env.cr.execute(SQL(
            """
            WITH message AS (
                INSERT INTO mail_message (model, res_id, message_type, author_id, date,
                                          create_uid, create_date, write_uid, write_date)
                SELECT %(model)s, %(res_id)s, 'notification', %(author)s, %(now)s, %(uid)s, %(now)s, %(uid)s, %(now)s
                  FROM generate_series(1, %(count)s)
                RETURNING id
            )
            INSERT INTO mail_tracking_value (field_id, mail_message_id, old_value_char, new_value_char,
                                             create_uid, create_date, write_uid, write_date)
            SELECT %(field)s, id, 'old', 'new', %(uid)s, %(now)s, %(uid)s, %(now)s FROM message
            RETURNING id
            """,
            model=record._name, res_id=record.id, author=self.env.user.partner_id.id, uid=self.env.uid,
            now=fields.Datetime.now(), count=count, field=field.id,
        ))
        ids = sorted(row[0] for row in self.env.cr.fetchall())
        self.env.invalidate_all()
        return self.env["mail.tracking.value"].browse(ids)

    def test_60_the_history_is_read_on_a_database_of_many_messages(self):
        """Odoo 20 refuses a search of messages of more than five models once it meets 10,000 of them
        (``MAX_SEARCH_LIMIT``: "Too many messages to be processed"): the reader reads the history there all the same,
        with every search the platform sends, and still none of a document it cannot read"""
        if "sale.order" not in self.env or "project.task" not in self.env:
            self.skipTest("sales or projects are not installed")
        reader = self._reader()
        since = fields.Datetime.now() - timedelta(minutes=1)
        order = self.env["sale.order"].create({"partner_id": self.env["res.partner"].create({"name": "Many"}).id})
        state = self.env["ir.model.fields"]._get("sale.order", "state")
        many = MAX_SEARCH_LIMIT + 100
        shown = self._bulk_history(order, state, many)
        # a person's own task: its history never reaches the reader, however many messages are around it
        private = self.env["project.task"].create({"name": "Mine", "project_id": False})
        stage = self.env["ir.model.fields"]._get("project.task", "stage_id")
        hidden = self._bulk_history(private, stage, 3)
        Tracking = self.env["mail.tracking.value"].with_user(reader)
        asked = ["field_id", "old_value_char", "new_value_char", "mail_message_id", "create_date"]
        for scope in ("mail_message_id.model", "field_id.model"):
            with self.subTest(scope=scope):
                kept = [(scope, "in", list(dc.HISTORY_OF))]
                # the platform's first question: where the history starts
                self.assertTrue(Tracking.search_read(kept, ["create_date"], limit=1, order="id asc"))
                domain = kept + [("field_id", "in", [state.id, stage.id]), ("create_date", ">=", since)]
                # its pages, 500 rows each, by the tracking row's id
                read, cursor = [], 0
                while True:
                    rows = Tracking.search_read(domain + [("id", ">", cursor)], asked, limit=500, order="id asc")
                    read += [row["id"] for row in rows]
                    if len(rows) < 500:
                        break
                    cursor = rows[-1]["id"]
                self.assertEqual(read, shown.ids)
                self.assertFalse(set(read) & set(hidden.ids))
                self.assertEqual(Tracking.search_count(domain), many)
        # the messages of a page, by id, kept to D5's documents as the platform keeps them
        messages = shown[:200].mapped("mail_message_id") | hidden.mapped("mail_message_id")
        got = self.env["mail.message"].with_user(reader).search_read(
            [("model", "in", list(dc.HISTORY_OF)), ("id", "in", messages.ids)], ["model", "res_id", "author_id", "date"],
        )
        self.assertEqual(sorted(m["id"] for m in got), shown[:200].mapped("mail_message_id").ids)

    # -------------------------------------------------------------- spec 465b: the deadlines are read

    def _reads_as_the_administrator(self, reader, records, names):
        """the reader reads these fields of these records, ids only (``load=None``), as the administrator does"""
        domain = [("id", "in", records.ids)]
        mine = self.env[records._name].with_user(reader).search_read(domain, list(names), load=None)
        self.assertEqual(mine, records.search_read(domain, list(names), load=None), records._name)
        self.assertEqual(sorted(row["id"] for row in mine), sorted(records.ids), records._name)
        return mine

    def test_70_the_reader_reads_the_names_of_reasons_sources_and_teams(self):
        """Spec 465 D3: a deal's lost reason, a deal's or an order's source, a sales or a helpdesk team, by name"""
        reader = self._reader()
        clerk = new_test_user(self.env, login="datlay_clerk6", groups="base.group_user")
        present = [m for m in NAMES if m in self.env]
        if not present:
            self.skipTest("neither CRM nor Sales is installed")
        for model in present:
            record = self.env[model].create({"name": "Datlay %s" % model})
            self._reads_as_the_administrator(reader, record, ["name"])
            # the module gives no write right: the reader's are an internal user's (Odoo lets every one add a source)
            for operation in ("write", "create", "unlink"):
                self.assertEqual(
                    self.env[model].with_user(reader).has_access(operation),
                    self.env[model].with_user(clerk).has_access(operation),
                    "%s %s" % (model, operation),
                )
            if model != "utm.source":
                self.assertFalse(self.env[model].with_user(reader).has_access("write"), model)

    def test_71_the_reader_reads_the_external_ids_of_helpdesk_stages_only(self):
        """Spec 465 D8.2: Odoo's own Cancelled stage of helpdesk is known by its external id: the reader reads the
        external ids of helpdesk stages and of no other model; Odoo's own lookups and every other user are untouched"""
        reader = self._reader()
        Data = self.env["ir.model.data"]
        # a helpdesk stage's row, made here where helpdesk is not installed (a row names its model by name)
        stage = Data.create({"module": "datlay_test", "name": "a_stage", "model": "helpdesk.stage", "res_id": 1})
        theirs = Data.search([("module", "=", "base"), ("name", "in", ("main_partner", "main_company", "group_user"))])
        self.assertEqual(len(theirs), 3)
        mine = Data.with_user(reader)
        self.assertTrue(mine.has_access("read"))
        rows = mine.search_read([("id", "in", (stage | theirs).ids)], ["module", "name", "model", "res_id"])
        self.assertEqual(rows, [{"id": stage.id, "module": "datlay_test", "name": "a_stage", "model": "helpdesk.stage", "res_id": 1}])
        self.assertEqual(set(mine.search([]).mapped("model")), {"helpdesk.stage"})
        self.assertFalse(mine.search_count([("model", "=", "res.partner")]))
        with self.assertRaises(AccessError):
            theirs.with_user(reader).read(["name"])
        for operation in ("write", "create", "unlink"):
            self.assertFalse(mine.has_access(operation), operation)
        # a restriction (no group) whose domain binds the reader's group alone
        group = self.env.ref("datlay_connector.group_reader")
        restriction = self.env["ir.access"].search([("name", "=", dc.RULE_PREFIX + "ir.model.data"), ("group_id", "=", False)])
        self.assertIn("%d in user.all_group_ids.ids" % group.id, restriction.domain)
        # Odoo's own lookup by external id reads the table itself, for the reader as before
        self.assertEqual(self.env(user=reader).ref("base.main_partner").id, self.env.ref("base.main_partner").id)
        if "helpdesk.stage" in self.env:
            # the row the platform asks for
            (row,) = mine.search_read([("module", "=", "helpdesk"), ("name", "=", "stage_cancelled")], ["model", "res_id"])
            self.assertEqual((row["model"], row["res_id"]), ("helpdesk.stage", self.env.ref("helpdesk.stage_cancelled").id))
        # the rule binds the reader alone: an administrator of access rights reads every row, an internal user none
        rights = new_test_user(self.env, login="datlay_rights", groups="base.group_user,base.group_erp_manager")
        self.assertEqual(Data.with_user(rights).search([("id", "in", theirs.ids)]), theirs)
        clerk = new_test_user(self.env, login="datlay_clerk7", groups="base.group_user")
        self.assertFalse(Data.with_user(clerk).has_access("read"))

    def test_72_the_reader_reads_the_deadlines_and_the_facts_beside_them(self):
        """Spec 465 D3: a document's deadline, its delivery, receipt and invoice status, its source and team, a deal's
        probability and lost reason, a ticket's priority and SLA verdict, a company's currency: each on this version"""
        reader = self._reader()
        for model, names in DEADLINE_FIELDS.items():
            if model not in self.env:
                continue
            fields = self.env[model]._fields
            missing = [n for n in names if n not in fields]
            self.assertFalse(missing, "%s has no %s on this version" % (model, missing))
            # a field closed by its groups refuses the read itself, whatever the records
            self.env[model].with_user(reader).search_read([], [n for n in names if n in fields], limit=1, load=None)
        partner = self.env["res.partner"].create({"name": "Deadlines"})
        self._reads_as_the_administrator(reader, self.env.company, DEADLINE_FIELDS["res.company"])
        if "crm.lead" in self.env:
            lead = self.env["crm.lead"].create({
                "name": "Deal",
                "type": "opportunity",
                "partner_id": partner.id,
                "date_deadline": "2026-12-01",
                "probability": 40,
                "source_id": self.env["utm.source"].create({"name": "Datlay fair"}).id,
                "team_id": self.env["crm.team"].create({"name": "Datlay north"}).id,
                "lost_reason_id": self.env["crm.lost.reason"].create({"name": "Datlay too dear"}).id,
            })
            (row,) = self._reads_as_the_administrator(reader, lead, DEADLINE_FIELDS["crm.lead"])
            self.assertEqual(str(row["date_deadline"]), "2026-12-01")
            self.assertEqual((row["source_id"], row["team_id"], row["lost_reason_id"]), (lead.source_id.id, lead.team_id.id, lead.lost_reason_id.id))
        if "sale.order" in self.env:
            order = self.env["sale.order"].create({
                "partner_id": partner.id,
                "validity_date": "2026-12-01",
                "commitment_date": "2026-12-10 09:00:00",
                "source_id": self.env["utm.source"].create({"name": "Datlay web"}).id,
                "team_id": self.env["crm.team"].create({"name": "Datlay south"}).id,
            })
            names = [n for n in DEADLINE_FIELDS["sale.order"] if n in order._fields]
            (row,) = self._reads_as_the_administrator(reader, order, names)
            self.assertEqual((str(row["validity_date"]), row["source_id"], row["team_id"]), ("2026-12-01", order.source_id.id, order.team_id.id))
            self.assertTrue(row["commitment_date"])
        if "purchase.order" in self.env:
            purchase = self.env["purchase.order"].create({"partner_id": partner.id, "date_planned": "2026-12-05 09:00:00"})
            names = [n for n in DEADLINE_FIELDS["purchase.order"] if n in purchase._fields]
            self._reads_as_the_administrator(reader, purchase, names)
        journal = self.env["account.journal"].search([("type", "=", "sale"), ("company_id", "=", self.env.company.id)], limit=1) if "account.move" in self.env else None
        if journal:
            invoice = self.env["account.move"].create({
                "move_type": "out_invoice", "partner_id": partner.id, "journal_id": journal.id,
                "invoice_date": "2026-11-01", "invoice_date_due": "2026-12-01",
            })
            (row,) = self._reads_as_the_administrator(reader, invoice, DEADLINE_FIELDS["account.move"])
            self.assertTrue(row["invoice_date_due"])
        if "helpdesk.ticket" in self.env:
            team = self.env["helpdesk.team"].create({"name": "Support"})
            ticket = self.env["helpdesk.ticket"].create({"name": "Broken", "team_id": team.id, "priority": "2", "partner_id": partner.id})
            (row,) = self._reads_as_the_administrator(reader, ticket, DEADLINE_FIELDS["helpdesk.ticket"])
            self.assertEqual((row["team_id"], row["priority"]), (team.id, "2"))

    def test_73_the_reader_reads_the_purchase_behind_a_waiting_mo(self):
        """Spec 465 D14 (Alex 11-11): an MO's component move, its product as an id, its quantities and state, and the
        purchase line it was bought by, with that line's product id, quantities, expected arrival and state"""
        if "mrp.production" not in self.env or "created_purchase_line_ids" not in self.env["stock.move"]._fields:
            self.skipTest("manufacturing or purchases with inventory is not installed")
        reader = self._reader()
        table = self.env["product.product"].create({"name": "Table", "type": "consu"})
        leg = self.env["product.product"].create({"name": "Leg", "type": "consu"})
        mo = self.env["mrp.production"].create({"product_id": table.id, "product_qty": 1, "uom_id": table.uom_id.id})
        purchase = self.env["purchase.order"].create({
            "partner_id": self.env["res.partner"].create({"name": "Vendor"}).id,
            "order_line": [Command.create({
                "product_id": leg.id, "product_qty": 4, "uom_id": leg.uom_id.id, "date_planned": "2026-12-01 09:00:00",
            })],
        })
        line = purchase.order_line
        move = self.env["stock.move"].create({
            "product_id": leg.id,
            "product_uom_qty": 4,
            "location_id": mo.location_src_id.id,
            "location_dest_id": mo.production_location_id.id,
            "raw_material_production_id": mo.id,
            "created_purchase_line_ids": [Command.set(line.ids)],
        })
        moves = self.env["stock.move"].with_user(reader)
        self.assertFalse(moves.has_access("write"))
        (row,) = moves.search_read([("raw_material_production_id", "=", mo.id)], list(MOVE_FIELDS), load=None)
        self.assertEqual(row["id"], move.id)
        # the product as an id, never its name
        self.assertEqual(row["product_id"], leg.id)
        self.assertEqual((row["raw_material_production_id"], row["created_purchase_line_ids"]), (mo.id, line.ids))
        self.assertEqual((row["product_uom_qty"], row["quantity"], row["state"]), (4, 0, move.state))
        lines = self.env["purchase.order.line"].with_user(reader)
        self.assertFalse(lines.has_access("write"))
        (row,) = lines.search_read([("id", "=", line.id)], list(PURCHASE_LINE_FIELDS), load=None)
        self.assertEqual((row["product_id"], row["product_qty"], row["qty_received"], row["state"]), (leg.id, 4, 0, "draft"))
        self.assertEqual(row["date_planned"], line.date_planned)

    def test_74_an_update_gives_the_reader_the_new_rights_at_once(self):
        """the end migration of this version syncs the reader's rights: a database updated to it reads the new tables
        without waiting for the daily job (a base and mail module cannot see CRM or Helpdesk after its own update)"""
        reader = self._reader()
        group = self.env.ref("datlay_connector.group_reader")
        ir_model_data = self.env["ir.model"]._get_id("ir.model.data")
        self.env["ir.access"].search([("group_id", "=", group.id), ("model_id", "=", ir_model_data)]).unlink()
        self.env["ir.access"].search([("name", "=", dc.RULE_PREFIX + "ir.model.data"), ("group_id", "=", False)]).unlink()
        self.assertFalse(self.env["ir.model.data"].with_user(reader).has_access("read"))
        root = os.path.dirname(os.path.dirname(os.path.abspath(dc.__file__)))
        spec = importlib.util.spec_from_file_location("datlay_end_migrate", os.path.join(root, "migrations", MIGRATION, "end-migrate.py"))
        script = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(script)
        script.migrate(self.env.cr, "20.0.1.1.1")
        self.env.invalidate_all()
        self.assertTrue(self.env["ir.model.data"].with_user(reader).has_access("read"))
        self.assertTrue(self.env["ir.access"].search([("name", "=", dc.RULE_PREFIX + "ir.model.data"), ("group_id", "=", False)]))
        self.assertFalse(self.env["ir.model.data"].with_user(reader).search_count([("model", "=", "res.partner")]))

    def test_80_the_reader_reads_what_the_margin_of_a_month_counts(self):
        """Spec 484 D2: a sale line's quantities, money, own cost and stored margin; a move to its customer, its sale line,
        day, locations, return and refund, transfer, and the value the stock put on it (the move's own from 19); a
        transfer's carrier price: each as the administrator reads it, ids and numbers only"""
        if "stock.move" not in self.env or "purchase_price" not in self.env["sale.order.line"]._fields:
            self.skipTest("sales with inventory and margins are not installed")
        reader = self._reader()
        for model, names in MARGIN_FIELDS.items():
            fields = self.env[model]._fields
            missing = [n for n in names if n not in fields and not (n == "carrier_price" and "delivery.carrier" not in self.env)]
            self.assertFalse(missing, "%s has no %s on this version" % (model, missing))
            # a field closed by its groups refuses the read itself, whatever the records
            self.env[model].with_user(reader).search_read([], [n for n in names if n in fields], limit=1, load=None)
        product = self.env["product.product"].create({"name": "Margin goods", "type": "consu", "is_storable": True, "standard_price": 40.0, "list_price": 100.0})
        stock = self.env["stock.warehouse"].search([("company_id", "=", self.env.company.id)], limit=1).lot_stock_id
        self.env["stock.quant"].with_context(inventory_mode=True).create({"product_id": product.id, "location_id": stock.id, "inventory_quantity": 5}).action_apply_inventory()
        order = self.env["sale.order"].create({
            "partner_id": self.env["res.partner"].create({"name": "Margin customer"}).id,
            "order_line": [Command.create({"product_id": product.id, "product_uom_qty": 2, "price_unit": 100.0})],
        })
        order.action_confirm()
        picking = order.picking_ids
        for move in picking.move_ids:
            move.quantity = move.product_uom_qty
            move.picked = True
        picking._action_done()
        (line,) = self._reads_as_the_administrator(reader, order.order_line, MARGIN_FIELDS["sale.order.line"])
        self.assertEqual((line["product_uom_qty"], line["qty_delivered"], line["price_subtotal"], line["purchase_price"]), (2.0, 2.0, 200.0, 40.0))
        (move,) = self._reads_as_the_administrator(reader, picking.move_ids, MARGIN_FIELDS["stock.move"])
        self.assertEqual((move["sale_line_id"], move["quantity"], move["is_out"], move["is_in"]), (order.order_line.id, 2.0, True, False))
        self.assertEqual(abs(move["value"]), 80.0)
        names = [n for n in MARGIN_FIELDS["stock.picking"] if n in picking._fields]
        (row,) = self._reads_as_the_administrator(reader, picking, names)
        self.assertEqual(row["sale_id"], order.id)

    # -------------------------------------------------------------- uninstall (TC-329-01)

    def test_40_uninstall_deletes_its_keys_archives_its_reader_and_takes_its_rules(self):
        reader = self._reader()
        clerk = new_test_user(self.env, login="datlay_clerk3", groups="base.group_user")
        self.connector._generate_key(clerk, False)
        theirs = self.keys.search([("user_id", "=", clerk.id)])
        self.connector._uninstall_cleanup()
        self.assertFalse(self.keys.search([("user_id", "=", reader.id)]))
        self.assertTrue(theirs.exists(), "a key the module did not issue stays")
        self.assertFalse(reader.active)
        self.assertFalse(self.env["ir.access"].with_context(active_test=False).search([("name", "=like", dc.RULE_PREFIX + "%")]))
        group = self.env.ref("datlay_connector.group_reader")
        self.assertFalse(self.env["ir.access"].with_context(active_test=False).search([("group_id", "=", group.id)]))

