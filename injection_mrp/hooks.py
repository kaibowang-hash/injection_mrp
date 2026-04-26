app_name = "injection_mrp"
app_title = "Injection MRP"
app_publisher = "JCE"
app_description = "Injection MRP for ERPNext manufacturing planning"
app_email = "kaibo_wang@whjichen.cn"
app_license = "mit"

required_apps = ["erpnext", "injection_aps"]

doctype_js = {
	"MRP Run": "public/js/mrp_run.js",
	"MRP Proposal Batch": "public/js/mrp_proposal_batch.js",
}

app_include_css = [
	"/assets/injection_mrp/css/injection_mrp.css",
]

app_include_js = [
	"/assets/injection_mrp/js/injection_mrp_shared.js",
]

after_install = "injection_mrp.install.after_install"
after_migrate = "injection_mrp.install.after_migrate"
before_uninstall = "injection_mrp.uninstall.before_uninstall"
