frappe.ui.form.on("MRP Stock Buffer", {
	refresh(frm) {
		apply_adu_method_ui(frm);
		render_buffer_summary(frm);
		if (!frm.is_new()) {
			frm.add_custom_button(__("Refresh Buffer"), () => {
				frm.call("refresh_buffer").then(() => {
					frm.reload_doc();
				});
			});
		}
	},
	adu_calculation_method(frm) {
		apply_adu_method_ui(frm);
		render_buffer_summary(frm);
	},
	adu: render_buffer_summary,
	red_zone_qty: render_buffer_summary,
	yellow_zone_qty: render_buffer_summary,
	green_zone_qty: render_buffer_summary,
	net_flow_position: render_buffer_summary,
	on_hand_qty: render_buffer_summary,
});

function apply_adu_method_ui(frm) {
	const method = frm.doc.adu_calculation_method || "Fixed";
	const isFixed = method === "Fixed";
	const usesPast = method === "Past Actual" || method === "Blended";
	const usesFuture = method === "Future MRP" || method === "Blended";
	const isBlended = method === "Blended";

	frm.toggle_display("fixed_adu", isFixed);
	frm.toggle_display("horizon_past_days", usesPast);
	frm.toggle_display("horizon_future_days", usesFuture);
	frm.toggle_display("factor_past", isBlended);
	frm.toggle_display("factor_future", isBlended);

	frm.toggle_reqd("fixed_adu", isFixed);
	frm.toggle_reqd("horizon_past_days", usesPast);
	frm.toggle_reqd("horizon_future_days", usesFuture);
	frm.toggle_reqd("factor_past", isBlended);
	frm.toggle_reqd("factor_future", isBlended);
}

function render_buffer_summary(frm) {
	if (!frm.fields_dict.buffer_summary || !window.injection_mrp || !injection_mrp.ui) {
		return;
	}
	frm.fields_dict.buffer_summary.$wrapper.html(injection_mrp.ui.buffer_chart_html(frm.doc));
}
