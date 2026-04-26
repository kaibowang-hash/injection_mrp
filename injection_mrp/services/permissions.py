from __future__ import annotations

from typing import Iterable

import frappe
from frappe import _
from frappe.permissions import setup_custom_perms


ROLE_GMC = "GMC"
ROLE_PMC = "PMC"
ROLE_MPLM = "MPLM"
ROLE_MPLP = "MPLP"

CORE_MRP_ROLES = (ROLE_MPLM, ROLE_MPLP, ROLE_GMC, ROLE_PMC)

MRP_READ_ROLES = {
	"System Manager",
	ROLE_MPLM,
	ROLE_MPLP,
	ROLE_GMC,
	ROLE_PMC,
	"Purchase Manager",
	"Purchase User",
	"Manufacturing Manager",
	"Manufacturing User",
	"Stock Manager",
	"Stock User",
	"Sales Manager",
	"Sales User",
}

MRP_OPERATOR_ROLES = {
	"System Manager",
	ROLE_MPLM,
	ROLE_MPLP,
}

MRP_ADMIN_ROLES = {
	"System Manager",
	ROLE_MPLM,
}

READ_FLAGS = {"read", "select", "report", "export", "print", "email", "share"}
READ_NO_EXPORT_FLAGS = {"read", "select", "report", "print", "email"}
WRITE_FLAGS = {"read", "select", "write", "create", "report", "export", "import", "print", "email", "share"}
FULL_FLAGS = {
	"read",
	"select",
	"write",
	"create",
	"delete",
	"report",
	"export",
	"import",
	"print",
	"email",
	"share",
}
CONFIG_FLAGS = {"read", "select", "write", "report", "export", "print", "email", "share"}

MRP_PAGE_NAMES = (
	"mrp-demand-console",
	"mrp-material-workbench",
	"mrp-pegging-detail",
	"mrp-shortage-timeline",
	"mrp-run-console",
	"mrp-release-center",
)
MRP_WORKSPACE_NAMES = ("Injection MRP",)

MRP_DOCTYPE_PERMISSIONS = {
	"MRP Settings": {
		"System Manager": FULL_FLAGS,
		ROLE_MPLM: CONFIG_FLAGS,
		ROLE_MPLP: READ_FLAGS,
		ROLE_GMC: READ_FLAGS,
		ROLE_PMC: READ_FLAGS,
		"Purchase Manager": READ_FLAGS,
		"Purchase User": READ_FLAGS,
		"Manufacturing Manager": READ_FLAGS,
		"Manufacturing User": READ_FLAGS,
		"Stock Manager": READ_FLAGS,
		"Stock User": READ_FLAGS,
		"Sales Manager": READ_FLAGS,
		"Sales User": READ_FLAGS,
	},
	"MRP Run": {
		role: FULL_FLAGS if role in MRP_ADMIN_ROLES else WRITE_FLAGS if role in MRP_OPERATOR_ROLES else READ_FLAGS
		for role in MRP_READ_ROLES
	},
	"MRP Demand Snapshot": {
		role: WRITE_FLAGS if role in MRP_OPERATOR_ROLES else READ_FLAGS for role in MRP_READ_ROLES
	},
	"MRP Requirement Line": {
		role: WRITE_FLAGS if role in MRP_OPERATOR_ROLES else READ_FLAGS for role in MRP_READ_ROLES
	},
	"MRP Pegging Line": {
		role: WRITE_FLAGS if role in MRP_OPERATOR_ROLES else READ_FLAGS for role in MRP_READ_ROLES
	},
	"MRP Rolling Balance Line": {
		role: WRITE_FLAGS if role in MRP_OPERATOR_ROLES else READ_FLAGS for role in MRP_READ_ROLES
	},
	"MRP Shortage Alert": {
		role: WRITE_FLAGS if role in MRP_OPERATOR_ROLES else READ_FLAGS for role in MRP_READ_ROLES
	},
	"MRP Supply Rule": {
		"System Manager": FULL_FLAGS,
		ROLE_MPLM: FULL_FLAGS,
		ROLE_MPLP: WRITE_FLAGS,
		ROLE_GMC: READ_FLAGS,
		ROLE_PMC: READ_FLAGS,
		"Purchase Manager": READ_FLAGS,
		"Purchase User": READ_FLAGS,
		"Manufacturing Manager": READ_FLAGS,
		"Manufacturing User": READ_FLAGS,
		"Stock Manager": READ_FLAGS,
		"Stock User": READ_FLAGS,
		"Sales Manager": READ_FLAGS,
		"Sales User": READ_FLAGS,
	},
	"MRP Proposal Batch": {
		role: FULL_FLAGS if role in MRP_ADMIN_ROLES else WRITE_FLAGS if role in MRP_OPERATOR_ROLES else READ_FLAGS
		for role in MRP_READ_ROLES
	},
	"MRP Exception Log": {
		role: WRITE_FLAGS if role in MRP_OPERATOR_ROLES else READ_FLAGS for role in MRP_READ_ROLES
	},
}

MANAGED_PERMISSION_FIELDS = (
	"read",
	"write",
	"create",
	"delete",
	"submit",
	"cancel",
	"amend",
	"report",
	"export",
	"import",
	"print",
	"email",
	"share",
	"select",
)

DEPENDENCY_READ_DOCTYPES = (
	"Company",
	"Item",
	"Customer",
	"Sales Order",
	"Sales Order Item",
	"Customer Delivery Schedule",
	"Customer Delivery Schedule Item",
	"APS Planning Run",
	"APS Schedule Result",
	"APS Schedule Segment",
	"Work Order",
	"BOM",
	"Warehouse",
	"Bin",
	"Material Request",
	"Material Request Item",
	"Purchase Order",
	"Purchase Order Item",
	"Production Plan",
	"Production Plan Item",
	"Supplier",
	"UOM",
	"User",
)


def ensure_roles_and_permissions():
	ensure_roles()
	ensure_mrp_doctype_permissions()
	ensure_dependency_link_permissions()
	ensure_page_and_workspace_roles()
	frappe.clear_cache()


def ensure_roles():
	for role_name in CORE_MRP_ROLES:
		if frappe.db.exists("Role", role_name):
			continue
		frappe.get_doc(
			{
				"doctype": "Role",
				"role_name": role_name,
				"desk_access": 1,
			}
		).insert(ignore_permissions=True)


def ensure_mrp_doctype_permissions():
	for doctype, role_map in MRP_DOCTYPE_PERMISSIONS.items():
		if not frappe.db.exists("DocType", doctype):
			continue
		for role, flags in role_map.items():
			ensure_custom_docperm(doctype, role, flags, strict=True)


def ensure_dependency_link_permissions():
	for doctype in DEPENDENCY_READ_DOCTYPES:
		if not frappe.db.exists("DocType", doctype):
			continue
		for role in MRP_READ_ROLES:
			ensure_custom_docperm(doctype, role, READ_NO_EXPORT_FLAGS)


def ensure_page_and_workspace_roles():
	for page_name in MRP_PAGE_NAMES:
		if frappe.db.exists("Page", page_name):
			_add_roles_to_child_table("Page", page_name, "roles", MRP_READ_ROLES)

	for workspace_name in MRP_WORKSPACE_NAMES:
		if frappe.db.exists("Workspace", workspace_name):
			_add_roles_to_child_table("Workspace", workspace_name, "roles", MRP_READ_ROLES)


def ensure_custom_docperm(doctype: str, role: str, flags: Iterable[str], permlevel: int = 0, strict: bool = False):
	if not role or not frappe.db.exists("Role", role):
		return
	if frappe.get_meta(doctype).istable:
		return

	setup_custom_perms(doctype)
	filters = {
		"parent": doctype,
		"role": role,
		"permlevel": permlevel,
		"if_owner": 0,
	}
	name = frappe.db.get_value("Custom DocPerm", filters)
	if name:
		docperm = frappe.get_doc("Custom DocPerm", name)
		changed = False
		managed_fields = MANAGED_PERMISSION_FIELDS if strict else flags
		for fieldname in managed_fields:
			value = 1 if fieldname in flags else 0
			if docperm.get(fieldname) != value:
				docperm.set(fieldname, value)
				changed = True
		if changed:
			docperm.save(ignore_permissions=True)
		return

	frappe.get_doc(
		{
			"doctype": "Custom DocPerm",
			"parent": doctype,
			"parenttype": "DocType",
			"parentfield": "permissions",
			"role": role,
			"permlevel": permlevel,
			"if_owner": 0,
			**{fieldname: 1 if fieldname in flags else 0 for fieldname in MANAGED_PERMISSION_FIELDS},
		}
	).insert(ignore_permissions=True)


def _add_roles_to_child_table(doctype: str, docname: str, child_table_field: str, roles: Iterable[str]):
	doc = frappe.get_doc(doctype, docname)
	existing = {row.role for row in doc.get(child_table_field) or []}
	changed = False
	for role in sorted(roles):
		if not role or role in existing or not frappe.db.exists("Role", role):
			continue
		doc.append(child_table_field, {"role": role})
		changed = True
	if changed:
		doc.save(ignore_permissions=True)


def require_any_role(allowed_roles: Iterable[str], message: str | None = None):
	if frappe.session.user == "Administrator":
		return
	if set(allowed_roles or []).intersection(set(frappe.get_roles())):
		return
	frappe.throw(message or _("You do not have permission to perform this MRP action."), frappe.PermissionError)
