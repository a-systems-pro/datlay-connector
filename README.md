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
  or a person's own stage. On Odoo Enterprise the reader takes one paid user.
- **Calls one address only:** `https://connect.datlay.io`, certificates checked. There is no setting to change it.
- **Renews the key in one click** (Settings > Datlay > Renew the key). The old key is deleted only once Datlay took
  the new one.
- **Cleans up on uninstall:** the keys it issued are deleted and the reader it made is archived.

It holds none of your data. The Datlay reader can still post a message or plan an activity, as every internal Odoo
user can; Datlay never does.

Derived from Rteam's AI Analytic Assistant module (its API key issuing and its CI). LGPL-3.

## Tests

`.github/workflows/tests.yml` installs the module beside Sales, Purchase, Inventory, CRM, Accounting, Manufacturing,
Project and Timesheets on a fresh
database and runs its tests; Datlay is a mock there. Locally: `odoo-skills/scripts/stand.sh test 19.0
datlay_connector,sale_management,purchase,stock,crm,account . --tags /datlay_connector`.
