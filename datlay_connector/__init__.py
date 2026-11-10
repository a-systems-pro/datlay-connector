from . import models
from . import wizard


def post_init_hook(env):
    """The Datlay reader's group gets its read rights on the apps installed now (the daily job adds later ones)."""
    env["datlay.connector"]._sync_reader_access()


def uninstall_hook(env):
    """329 D14: the keys the module issued are deleted, the reader it made is archived, its rights and rules go.

    Runs before Odoo removes the module's records. On 20 a restriction left behind would bind nobody (its domain names
    the reader's group, which goes); it is removed all the same, and the group's own accesses with it.
    """
    env["datlay.connector"]._uninstall_cleanup()
