frappe.ui.form.on("MRP Stock Buffer", {
	refresh(frm) {
		render_buffer_summary(frm);
		if (!frm.is_new()) {
			frm.add_custom_button(__("Refresh Buffer"), () => {
				frm.call("refresh_buffer").then(() => {
					frm.reload_doc();
				});
			});
		}
	},
	adu_calculation_method: render_buffer_summary,
	adu: render_buffer_summary,
	red_zone_qty: render_buffer_summary,
	yellow_zone_qty: render_buffer_summary,
	green_zone_qty: render_buffer_summary,
	net_flow_position: render_buffer_summary,
	on_hand_qty: render_buffer_summary,
});

function render_buffer_summary(frm) {
	if (!frm.fields_dict.buffer_summary || !window.injection_mrp || !injection_mrp.ui) {
		return;
	}
	frm.fields_dict.buffer_summary.$wrapper.html(injection_mrp.ui.buffer_chart_html(frm.doc));
}
