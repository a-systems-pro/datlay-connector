# Datlay Connector

Connects an Odoo database to [Datlay](https://datlay.com), by a-systems. Odoo 17.0 and 19.0 (one branch each).

What it does in your Odoo, and nothing else:

- **Connects with a code.** In Datlay an administrator starts "Connect Odoo" and gets a code, good once for 30
  minutes. In Odoo, Settings > Datlay > Connect with a code: the module first shows whose Datlay account the code
  belongs to, and makes nothing before you say yes.
- **Reads with one of two keys.** Your own administrator's API key named "Datlay" (it can do everything you can; Datlay
  reads only the documents and their history with it), or the **Datlay reader**: a user the module makes in a group of
  its own that reads sales, purchases, transfers, invoices, payments, deals, tasks, manufacturing orders and helpdesk
  tickets and their history, and cannot create, change or delete them. It never reads a timesheet line, a private task
  or a person's own stage, nor the history of a document it cannot read. On Odoo Enterprise the reader takes one paid
  user.
- **Calls one address only:** `https://connect.datlay.io`, certificates checked. There is no setting to change it.
- **Renews the key in one click** (Settings > Datlay > Renew the key). The old key is deleted only once Datlay took
  the new one.
- **Cleans up on uninstall:** the keys it issued are deleted and the reader it made is archived.

It holds none of your data. The Datlay reader can still post a message, plan an activity or keep a to-do, as every
internal Odoo user can; Datlay never does.

Derived from Rteam's AI Analytic Assistant module (its API key issuing and its CI). LGPL-3.

## The reader's history

Odoo lets only an administrator read tracked values (`mail.tracking.value`) as such; any other user sees them inside a
message, and a message only of a document they can read. The reader is given tracked values to read, so its rule keeps
them to the same: a value is read only through a message the reader may read. Odoo 20 can say that in a rule (the
`access` condition). On 17 and 19 a rule is evaluated as superuser and cannot ask the document, so the rule names
`datlay_message_readable`, a field that is never stored and is only searched with the reader's own rights, one SQL
subquery per document model (`models/mail_tracking_value.py`): a private task's stage or a payroll entry's posting stays
hidden, a lost deal's history is read, and nobody else's reads change.

## Tests

`.github/workflows/tests.yml` installs the module beside Sales, Purchase, Inventory, CRM, Accounting, Manufacturing,
Project and Timesheets on a fresh
database and runs its tests; Datlay is a mock there. Locally: `odoo-skills/scripts/stand.sh test 19.0
datlay_connector,sale_management,purchase,stock,crm,account . --tags /datlay_connector`.
