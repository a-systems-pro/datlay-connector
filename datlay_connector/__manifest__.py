{
    "name": "Datlay Connector",
    "summary": (
        "Connects this Odoo to Datlay: an administrator's key, or a Datlay reader that "
        "reads the documents and their history and cannot change them."
    ),
    "version": "17.0.1.0.0",
    "category": "Productivity",
    "author": "Rteam",
    "maintainer": "Rteam",
    "website": "https://datlay.com",
    "support": "support@datlay.com",
    "license": "LGPL-3",
    "depends": [
        "base",
        "mail",
    ],
    "data": [
        "security/datlay_security.xml",
        "security/ir.model.access.csv",
        "data/ir_cron.xml",
        "wizard/datlay_connect_wizard_views.xml",
        "views/datlay_connector_views.xml",
    ],
    "post_init_hook": "post_init_hook",
    "uninstall_hook": "uninstall_hook",
    "installable": True,
    "application": False,
    "auto_install": False,
}
