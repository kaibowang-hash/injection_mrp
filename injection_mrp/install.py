import frappe

from injection_mrp.services.customizations import (
	ensure_default_settings,
	ensure_standard_customizations,
	ensure_stock_buffer_item_defaults,
)
from injection_mrp.services.permissions import ensure_roles_and_permissions


def after_install():
	ensure_standard_customizations()
	ensure_default_settings()
	ensure_stock_buffer_item_defaults()
	ensure_roles_and_permissions()
	frappe.clear_cache()


def after_migrate():
	ensure_standard_customizations()
	ensure_default_settings()
	ensure_stock_buffer_item_defaults()
	ensure_roles_and_permissions()
	frappe.clear_cache()
