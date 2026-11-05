"""Spec 329 T-8 on Odoo 17.0: TC-329-01 (the reader made, the tenant seen before anything is made, uninstall cleans
up), TC-329-02 (another tenant's code shows that tenant; no address but Datlay's), TC-329-08 (the reader's group
cannot create, write or unlink a document, and reads the history of D5's documents only)."""

import json
from unittest.mock import MagicMock, patch

from odoo.exceptions import AccessError, UserError
from odoo.tests.common import TransactionCase, new_test_user, tagged

from odoo.addons.datlay_connector.models import datlay_connector as dc

POST = "odoo.addons.datlay_connector.models.datlay_connector._post"
#: the real ones, kept before any test replaces them
REAL_POST = dc._post
REAL_SEND = dc.DatlayConnector._send_after_commit
DOCUMENTS = ("sale.order", "purchase.order", "stock.picking", "account.move", "account.payment", "crm.lead")
LINES = ("sale.order.line", "purchase.order.line", "account.move.line")


@tagged("post_install", "-at_install")
class TestDatlayConnector(TransactionCase):
    def setUp(self):
        super().setUp()
        self.env["ir.config_parameter"].sudo().set_param("web.base.url", "https://odoo.example.com")
        self.posts = []

        def fake_post(path, payload):
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
        self.post.side_effect = lambda path, payload: (404, {"error": "This code is not open"})
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
        self.assertRegex(payload["key"], r"^[0-9a-f]{40}$")
        self.assertRegex(payload["renewSecret"], r"^[0-9a-f]{64}$")
        self.assertEqual(payload["uuid"], self.env["ir.config_parameter"].sudo().get_param("database.uuid"))
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
        self.assertEqual(self.env["datlay.connector.key"].search([]).mapped("apikey_id"), now.ids)

    def test_06_a_plain_address_is_refused_before_a_key_is_made(self):
        self.env["ir.config_parameter"].sudo().set_param("web.base.url", "http://odoo.example.com")
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

    def test_10_the_reader_is_made_internal_in_its_group_alone_with_its_key(self):
        reader = self._reader()
        self.assertTrue(reader)
        self.assertFalse(reader.share)
        self.assertEqual(
            set(reader.groups_id.ids),
            {self.env.ref("base.group_user").id, self.env.ref("datlay_connector.group_reader").id},
        )
        self.assertEqual(reader.company_ids, self.env["res.company"].search([]))
        key = self.keys.search([("user_id", "=", reader.id), ("name", "=", dc.KEY_NAME)])
        self.assertEqual(len(key), 1)
        (payload,) = self._sent("/datlay/odoo/connect/register")
        self.assertEqual((payload["login"], payload["reader"]), (dc.READER_LOGIN, "dedicated"))
        self.assertEqual(self.connector._get().reader_user_id, reader)

    def test_11_the_reader_reads_the_documents_and_cannot_change_them(self):
        reader = self._reader()
        present = [m for m in DOCUMENTS + LINES if m in self.env]
        for model in present:
            records = self.env[model].with_user(reader)
            self.assertTrue(records.check_access_rights("read", raise_exception=False), model)
            for operation in ("write", "create", "unlink"):
                self.assertFalse(records.check_access_rights(operation, raise_exception=False), "%s %s" % (model, operation))
        for model in ("mail.tracking.value", "res.users.apikeys", "ir.model.fields"):
            self.assertTrue(self.env[model].with_user(reader).check_access_rights("read", raise_exception=False), model)

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
        self._reader()
        if "account.move" not in self.env:
            self.skipTest("accounting is not installed")
        rules = self.env["ir.rule"].search([("name", "=like", dc.RULE_PREFIX + "%")])
        by_model = {r.model_id.model: r for r in rules}
        for model in ("account.move", "account.move.line", "account.payment", "account.partial.reconcile"):
            self.assertIn(model, by_model)
            self.assertNotIn("general", by_model[model].domain_force)
            self.assertIn("journal_id.type", by_model[model].domain_force)
            self.assertEqual(by_model[model].groups, self.env.ref("datlay_connector.group_reader"))

    def test_14_a_company_made_later_joins_the_reader(self):
        reader = self._reader()
        company = self.env["res.company"].create({"name": "Later Ltd"})
        self.assertIn(company, reader.company_ids)

    def test_15_the_group_never_gets_a_write_right(self):
        self._reader()
        group = self.env.ref("datlay_connector.group_reader")
        access = self.env["ir.model.access"].search([("group_id", "=", group.id)])
        self.assertTrue(access)
        self.assertFalse(access.filtered(lambda a: a.perm_write or a.perm_create or a.perm_unlink))
        # a write right given by hand is taken back by the next sync
        access[:1].perm_write = True
        self.connector._sync_reader_access()
        self.assertFalse(access.filtered(lambda a: a.perm_write))

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
        self.post.side_effect = lambda path, payload: (403, {"error": "the renewal secret does not match"})
        conn.action_renew()
        self._deliver()
        self.assertEqual(self.keys.search([("user_id", "=", self.env.uid), ("name", "=", dc.KEY_NAME)]), old)
        self.assertEqual(conn.renew_secret, old_secret)
        self.assertIn("does not match", conn.last_answer)

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
        self.assertFalse(self.env["ir.rule"].with_context(active_test=False).search([("name", "=like", dc.RULE_PREFIX + "%")]))
        group = self.env.ref("datlay_connector.group_reader")
        self.assertFalse(self.env["ir.model.access"].search([("group_id", "=", group.id)]))

