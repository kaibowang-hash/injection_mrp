frappe.provide("injection_mrp.ui");

(function () {
	if (injection_mrp.ui.__initialized) {
		return;
	}

	injection_mrp.ui.__initialized = true;
	injection_mrp.ui.__action_role_map = {
		run_forecast_prebuy: ["System Manager", "MPLM", "MPLP"],
		run_firm_aps_mrp: ["System Manager", "MPLM", "MPLP"],
		recalculate_mrp_run: ["System Manager", "MPLM", "MPLP"],
		enqueue_forecast_prebuy: ["System Manager", "MPLM", "MPLP"],
		enqueue_firm_aps_mrp: ["System Manager", "MPLM", "MPLP"],
		enqueue_recalculate_mrp_run: ["System Manager", "MPLM", "MPLP"],
		apply_proposal_batch: ["System Manager", "MPLM", "MPLP"],
		validate_proposal_batch_for_release: ["System Manager", "MPLM", "MPLP"],
		save_proposal_batch_items: ["System Manager", "MPLM", "MPLP"],
	};

	injection_mrp.ui.ensure_styles = function () {
		if (!document.getElementById("injection-aps-page-style")) {
			const apsLink = document.createElement("link");
			apsLink.id = "injection-aps-page-style";
			apsLink.rel = "stylesheet";
			apsLink.href = "/assets/injection_aps/css/injection_aps.css";
			document.head.appendChild(apsLink);
		}
		if (!document.getElementById("injection-mrp-page-style")) {
			const link = document.createElement("link");
			link.id = "injection-mrp-page-style";
			link.rel = "stylesheet";
			link.href = "/assets/injection_mrp/css/injection_mrp.css";
			document.head.appendChild(link);
		}
		injection_mrp.ui.bind_tooltips();
	};

	injection_mrp.ui.bind_tooltips = function () {
		if (injection_mrp.ui.__tooltips_bound) {
			return;
		}
		injection_mrp.ui.__tooltips_bound = true;
		const tooltip = $('<div class="imrp-tooltip" role="tooltip"></div>').hide();
		$("body").append(tooltip);

		function position(event) {
			const margin = 12;
			const width = tooltip.outerWidth() || 0;
			const height = tooltip.outerHeight() || 0;
			let left = event.clientX + margin;
			let top = event.clientY + margin;
			if (left + width + margin > window.innerWidth) {
				left = Math.max(margin, event.clientX - width - margin);
			}
			if (top + height + margin > window.innerHeight) {
				top = Math.max(margin, event.clientY - height - margin);
			}
			tooltip.css({ left, top });
		}

		$(document)
			.on("mouseenter focusin", "[data-imrp-tooltip]", function (event) {
				const text = $(this).attr("data-imrp-tooltip");
				if (!text) {
					return;
				}
				tooltip.text(text).show();
				position(event);
			})
			.on("mousemove", "[data-imrp-tooltip]", position)
			.on("mouseleave focusout", "[data-imrp-tooltip]", () => tooltip.hide())
			.on("scroll", () => tooltip.hide());
	};

	injection_mrp.ui.escape = function (value) {
		return frappe.utils.escape_html(value == null ? "" : String(value));
	};

	injection_mrp.ui.translate = function (value) {
		if (value == null || value === "") {
			return "";
		}
		return __(String(value));
	};

	injection_mrp.ui.__local_icons = new Set(["download", "filter", "search", "x"]);

	injection_mrp.ui.icon = function (iconName, size) {
		const name = iconName || "download";
		const sizeClass = size ? ` ia-aps-icon-${injection_mrp.ui.escape(size)}` : "";
		if (injection_mrp.ui.__local_icons.has(name)) {
			return `<svg class="ia-aps-icon${sizeClass}" aria-hidden="true"><use href="/assets/injection_aps/icons/aps-icons.svg#${injection_mrp.ui.escape(name)}"></use></svg>`;
		}
		if (frappe.utils && frappe.utils.icon) {
			try {
				const icon = frappe.utils.icon(name, size || "xs");
				if (icon) {
					return icon;
				}
			} catch (error) {
				// Fall back to the APS icon sprite when the symbol is not available in Frappe.
			}
		}
		return `<svg class="ia-aps-icon${sizeClass}" aria-hidden="true"><use href="/assets/injection_aps/icons/aps-icons.svg#download"></use></svg>`;
	};

	injection_mrp.ui.icon_button = function (iconName, title, attrs, extraClass) {
		const buttonClass = ["ia-icon-btn", extraClass || ""].filter(Boolean).join(" ");
		const safeAttrs = Object.entries(attrs || {})
			.filter(([, value]) => value !== null && value !== undefined && value !== false)
			.map(([key, value]) => `${key}="${injection_mrp.ui.escape(value == null ? "" : String(value))}"`)
			.join(" ");
		return `
			<button
				type="button"
				class="${injection_mrp.ui.escape(buttonClass)}"
				title="${injection_mrp.ui.escape(title || "")}"
				aria-label="${injection_mrp.ui.escape(title || "")}"
				${safeAttrs}
			>${injection_mrp.ui.icon(iconName || "download", "xs")}</button>
		`;
	};

	injection_mrp.ui.to_plain_text = function (value) {
		if (value == null) {
			return "";
		}
		if (typeof value === "number") {
			return String(value);
		}
		const text = String(value);
		if (!/[<>]/.test(text)) {
			return text;
		}
		const container = document.createElement("div");
		container.innerHTML = text;
		return container.textContent || container.innerText || "";
	};

	injection_mrp.ui.format_number = function (value, digits) {
		const numericValue = Number(value || 0);
		if (!Number.isFinite(numericValue)) {
			return "0";
		}
		return new Intl.NumberFormat(undefined, {
			minimumFractionDigits: 0,
			maximumFractionDigits: Number.isInteger(digits) ? digits : 3,
		}).format(numericValue);
	};

	injection_mrp.ui.format_date = function (value) {
		return value ? frappe.datetime.str_to_user(value) : "";
	};

	injection_mrp.ui.doc_link = function (doctype, name, label) {
		if (!doctype || !name) {
			return injection_mrp.ui.escape(label || name || "");
		}
		return `<a class="ia-link" href="/app/Form/${encodeURIComponent(doctype)}/${encodeURIComponent(name)}">${injection_mrp.ui.escape(label || name)}</a>`;
	};

	injection_mrp.ui.item_cell = function (itemCode, itemName, options) {
		const settings = Object.assign({ doctype: "Item" }, options || {});
		const code = itemCode || "";
		const secondary = itemName || settings.description || "";
		return `
			<div class="imrp-item-cell">
				<div class="imrp-item-code">${code ? injection_mrp.ui.doc_link(settings.doctype, code, code) : ""}</div>
				${secondary ? `<div class="imrp-item-name" title="${injection_mrp.ui.escape(secondary)}">${injection_mrp.ui.escape(secondary)}</div>` : ""}
			</div>
		`;
	};

	injection_mrp.ui.pill = function (label, tone) {
		return `<span class="ia-pill ${tone || "blue"}">${injection_mrp.ui.escape(injection_mrp.ui.translate(label || ""))}</span>`;
	};

	injection_mrp.ui.__code_map = {
		Forecast: "FC",
		"Forecast Prebuy": "FP",
		"Firm APS": "APS",
		Prebuy: "PB",
		Firm: "F",
		Draft: "DFT",
		Ready: "RDY",
		Pending: "PND",
		Queued: "QUE",
		Running: "RUN",
		Applied: "APL",
		Released: "REL",
		Superseded: "SUP",
		Expired: "EXP",
		Consumed: "CON",
		Closed: "CLD",
		Calculated: "CAL",
		"Proposal Generated": "PRP",
		Exception: "EXC",
		Warning: "WRN",
		Error: "ERR",
		Failed: "FLD",
		Cancelled: "CXL",
		Rejected: "REJ",
		Open: "OPN",
		Reviewed: "REV",
		Resolved: "RSL",
		Ignored: "IGN",
		Info: "INF",
		Critical: "CRT",
		None: "OK",
		Purchase: "PUR",
		Manufacture: "MFG",
		Manufacturing: "MFG",
		Subcontracting: "SUB",
		"Customer Provided": "CP",
		"Material Transfer": "MT",
		"Material Issue": "MI",
		"Supplier Supplied": "SS",
		"No Action": "NA",
		Stock: "STK",
		"Material Request": "MR",
		"Purchase Order": "PO",
		"Work Order": "WO",
		"Production Plan": "PP",
		"Planned Supply": "PLN",
		"Open MR": "OMR",
		"Open PO": "OPO",
		"Open WO": "OWO",
		Daily: "D",
		Weekly: "W",
		"Missing Lead Time": "MLT",
		"Past Due Order": "PDO",
		"Late Supply": "LS",
		"Early Supply": "ES",
		"Excess Supply": "XS",
		"Excess Prebuy": "XPB",
		"Missing BOM": "MB",
		"Missing Supplier": "MS",
		"Purchase Constraint Rounding": "PCR",
		"No Adjustment": "NA",
		Expedite: "EXP",
		Delay: "DLY",
		Cancel: "CXL",
		Review: "REV",
		"Create Material Request": "CMR",
		"Consume Prebuy": "CPB",
		"Review Excess Prebuy": "RXP",
	};

	injection_mrp.ui.__warning_patterns = [
		[/missing lead time|no lead time|没有前置时间/i, "MLT", "Missing Lead Time"],
		[/past due|before today|早于今天|已滞后/i, "PDO", "Past Due Order"],
		[/late supply|after the material need date|晚于物料需求日期/i, "LS", "Late Supply"],
		[/early supply|before the material need date|早于物料需求日期/i, "ES", "Early Supply"],
		[/excess prebuy|forecast prebuy|预采.*超|超额.*预采/i, "XPB", "Excess Prebuy"],
		[/excess supply|not consumed|未被需求消耗|超额/i, "XS", "Excess Supply"],
		[/missing bom|no submitted default bom|没有可用.*BOM/i, "MB", "Missing BOM"],
		[/missing supplier|no supplier|未匹配到供应商/i, "MS", "Missing Supplier"],
		[/constraint rounding|rounded from|最低采购量|倍量/i, "PCR", "Purchase Constraint Rounding"],
	];

	injection_mrp.ui.short_code = function (value, kind) {
		const text = String(value || "").trim();
		if (!text) {
			return "";
		}
		if (kind === "warning") {
			const matched = injection_mrp.ui.__warning_patterns.find(([pattern]) => pattern.test(text));
			if (matched) {
				return matched[1];
			}
		}
		if (injection_mrp.ui.__code_map[text]) {
			return injection_mrp.ui.__code_map[text];
		}
		const words = text.replace(/[^a-zA-Z0-9]+/g, " ").trim().split(/\s+/).filter(Boolean);
		if (!words.length) {
			return text.slice(0, 4).toUpperCase();
		}
		if (words.length === 1) {
			return words[0].slice(0, 4).toUpperCase();
		}
		return words.map((word) => word[0]).join("").slice(0, 4).toUpperCase();
	};

	injection_mrp.ui.code_tone = function (value, kind) {
		const text = String(value || "");
		if (kind === "warning") {
			if (/Critical|Past Due|Late Supply|Missing BOM|Missing Supplier/i.test(text)) {
				return "red";
			}
			if (/Warning|Missing Lead Time|Early Supply|Excess Supply|Excess Prebuy|Constraint/i.test(text)) {
				return "orange";
			}
			if (/Info/i.test(text)) {
				return "blue";
			}
			return "green";
		}
		if (kind === "status") {
			return injection_mrp.ui.status_tone(text);
		}
		if (kind === "action") {
			if (["Expedite", "Cancel", "Review Excess Prebuy"].includes(text)) {
				return "red";
			}
			if (["Delay", "Review"].includes(text)) {
				return "orange";
			}
			return "blue";
		}
		return "blue";
	};

	injection_mrp.ui.code_badge = function (value, options) {
		options = options || {};
		if (value == null || value === "") {
			return options.empty ? injection_mrp.ui.code_badge(options.empty, options) : "";
		}
		const label = options.label || injection_mrp.ui.translate(value);
		const title = options.title || label;
		const code = options.code || injection_mrp.ui.short_code(value, options.kind);
		const tone = options.tone || injection_mrp.ui.code_tone(value, options.kind);
		return `<span class="imrp-code-badge ${injection_mrp.ui.escape(tone)}" data-imrp-code="${injection_mrp.ui.escape(code)}" data-imrp-label="${injection_mrp.ui.escape(label)}" data-imrp-tooltip="${injection_mrp.ui.escape(title)}">${injection_mrp.ui.escape(code)}</span>`;
	};

	injection_mrp.ui.split_badge_values = function (value) {
		if (Array.isArray(value)) {
			return value.map((part) => String(part || "").trim()).filter(Boolean);
		}
		return String(value || "")
			.split(/[;,，、]/)
			.map((part) => part.trim())
			.filter(Boolean);
	};

	injection_mrp.ui.split_warning_reasons = function (value) {
		if (Array.isArray(value)) {
			return value.map((part) => String(part || "").trim()).filter(Boolean);
		}
		return String(value || "")
			.split(/[;；]/)
			.map((part) => part.trim())
			.filter(Boolean);
	};

	injection_mrp.ui.badge_stack = function (value, options) {
		options = options || {};
		const parts = injection_mrp.ui.split_badge_values(value);
		if (!parts.length) {
			return options.empty ? injection_mrp.ui.code_badge(options.empty, options) : "";
		}
		return `<span class="imrp-code-stack">${parts.map((part) => injection_mrp.ui.code_badge(part, options)).join("")}</span>`;
	};

	injection_mrp.ui.warning_badges = function (category, reason, level) {
		const categories = injection_mrp.ui.split_badge_values(category);
		const reasons = injection_mrp.ui.split_warning_reasons(reason);
		const source = categories.length ? categories : reasons;
		if (!source.length) {
			return level && level !== "None" ? injection_mrp.ui.code_badge(level, { kind: "warning" }) : "";
		}
		return `<span class="imrp-code-stack">${source
			.map((value, index) => {
				const label = categories[index] || value;
				const reason = reasons[index] || "";
				return injection_mrp.ui.code_badge(value, {
					kind: "warning",
					label,
					title: reason && label ? `${injection_mrp.ui.translate(label)} - ${reason}` : reason || injection_mrp.ui.translate(label || value),
				});
			})
			.join("")}</span>`;
	};

	injection_mrp.ui.code_legend_html = function (markup, options) {
		options = options || {};
		const container = document.createElement("div");
		container.innerHTML = markup || "";
		const entries = [];
		const seen = new Set();
		container.querySelectorAll(".imrp-code-badge[data-imrp-code]").forEach((node) => {
			const code = node.getAttribute("data-imrp-code") || "";
			const label = node.getAttribute("data-imrp-label") || node.getAttribute("data-imrp-tooltip") || "";
			const tooltip = node.getAttribute("data-imrp-tooltip") || label;
			const tone = Array.from(node.classList).find((name) => ["green", "blue", "orange", "red"].includes(name)) || "blue";
			if (!code || !label) {
				return;
			}
			const key = `${code}::${label}`;
			if (seen.has(key)) {
				return;
			}
			seen.add(key);
			entries.push({ code, label, tooltip, tone });
		});
		if (!entries.length) {
			return "";
		}
		const max = options.max_legend_items || 16;
		const visible = entries.slice(0, max);
		const extra = entries.length - visible.length;
		return `
			<div class="imrp-code-legend" aria-label="${injection_mrp.ui.escape(__("Code Legend"))}">
				${visible
					.map(
						(item) => `
						<span class="imrp-code-legend-item" data-imrp-tooltip="${injection_mrp.ui.escape(item.tooltip)}">
							<span class="imrp-code-badge ${injection_mrp.ui.escape(item.tone)}">${injection_mrp.ui.escape(item.code)}</span>
							<span class="imrp-code-legend-label">${injection_mrp.ui.escape(item.label)}</span>
						</span>`
					)
					.join("")}
				${extra > 0 ? `<span class="imrp-code-legend-more" data-imrp-tooltip="${injection_mrp.ui.escape(entries.slice(max).map((item) => `${item.code} = ${item.label}`).join("; "))}">+${injection_mrp.ui.format_number(extra, 0)}</span>` : ""}
			</div>
		`;
	};

	injection_mrp.ui.code_legend_for_rows = function (rows, columns, options) {
		options = options || {};
		const legendColumns = options.legend_columns || [];
		if (!legendColumns.length || !rows || !rows.length) {
			return "";
		}
		const columnByField = {};
		(columns || []).forEach((column) => {
			if (column.fieldname) {
				columnByField[column.fieldname] = column;
			}
		});
		const entries = [];
		const seen = new Set();
		function add_entry(value, config) {
			const text = String(value || "").trim();
			if (!text) {
				return;
			}
			const kind = config.kind || (columnByField[config.fieldname] || {}).legend_kind;
			const code = injection_mrp.ui.short_code(text, kind);
			const label = injection_mrp.ui.translate(text);
			const tone = config.tone || injection_mrp.ui.code_tone(text, kind);
			const key = `${code}::${label}`;
			if (!code || seen.has(key)) {
				return;
			}
			seen.add(key);
			entries.push({ code, label, tone });
		}
		rows.forEach((row) => {
			legendColumns.forEach((column) => {
				const config = typeof column === "string" ? { fieldname: column } : column || {};
				if (!config.fieldname) {
					return;
				}
				const value = row[config.fieldname];
				if (config.split !== false) {
					injection_mrp.ui.split_badge_values(value).forEach((part) => add_entry(part, config));
				} else {
					add_entry(value, config);
				}
			});
		});
		if (!entries.length) {
			return "";
		}
		return `
			<div class="imrp-code-legend" aria-label="${injection_mrp.ui.escape(__("Code Legend"))}">
				${entries
					.map(
						(item) => `
						<span class="imrp-code-legend-item" data-imrp-tooltip="${injection_mrp.ui.escape(item.label)}">
							<span class="imrp-code-badge ${injection_mrp.ui.escape(item.tone)}">${injection_mrp.ui.escape(item.code)}</span>
							<span class="imrp-code-legend-equals">=</span>
							<span class="imrp-code-legend-label">${injection_mrp.ui.escape(item.label)}</span>
						</span>`
					)
					.join("")}
			</div>
		`;
	};

	injection_mrp.ui.status_tone = function (status) {
		if (["Ready", "Applied", "Released", "Consumed", "Closed", "Calculated"].includes(status)) {
			return "green";
		}
		if (["Draft", "Pending", "Proposal Generated", "Queued", "Running"].includes(status)) {
			return "blue";
		}
		if (["Exception", "Warning"].includes(status)) {
			return "orange";
		}
		if (["Error", "Failed", "Cancelled", "Rejected", "Superseded", "Expired"].includes(status)) {
			return "red";
		}
		return "blue";
	};

	injection_mrp.ui.get_user_roles = function () {
		return Array.from(new Set([].concat(frappe.user_roles || [], (frappe.boot.user || {}).roles || [])));
	};

	injection_mrp.ui.can_run_action = function (actionKey) {
		if (frappe.session.user === "Administrator") {
			return true;
		}
		const required = injection_mrp.ui.__action_role_map[actionKey] || [];
		if (!required.length) {
			return true;
		}
		const roles = new Set(injection_mrp.ui.get_user_roles());
		return required.some((role) => roles.has(role));
	};

	injection_mrp.ui.with_busy = async function (message, fn) {
		frappe.dom.freeze(message || __("Working..."));
		try {
			return await fn();
		} finally {
			frappe.dom.unfreeze();
		}
	};

	injection_mrp.ui.xcall = function (method, args) {
		return frappe.xcall(method, args || {});
	};

	injection_mrp.ui.make_shell = function (page, title, subtitle) {
		injection_mrp.ui.ensure_styles();
		const wrapper = $(page.body);
		wrapper.addClass("ia-app-page imrp-app-page");
		wrapper.empty();
		const html = `
			<div class="ia-page imrp-page">
				<div class="ia-banner">
					<h3>${injection_mrp.ui.escape(title)}</h3>
					<p>${injection_mrp.ui.escape(subtitle || "")}</p>
				</div>
				<div class="ia-filter-bar imrp-filters"></div>
				<div class="imrp-actions"></div>
				<div class="ia-card-grid imrp-card-grid"></div>
				<div class="imrp-status-line"></div>
				<div class="ia-panel imrp-table-panel"><div class="imrp-table-wrap"></div></div>
			</div>`;
		wrapper.html(html);
		return {
			root: wrapper.find(".imrp-page"),
			actions: wrapper.find(".imrp-actions"),
			filters: wrapper.find(".imrp-filters"),
			cards: wrapper.find(".imrp-card-grid"),
			status: wrapper.find(".imrp-status-line"),
			table: wrapper.find(".imrp-table-wrap"),
		};
	};

	injection_mrp.ui.render_actions = function (target, actions) {
		target.empty();
		const allowedActions = (actions || []).filter((action) => injection_mrp.ui.can_run_action(action.action_key));
		if (!allowedActions.length) {
			return;
		}
		const strip = $('<div class="ia-action-strip"></div>');
		allowedActions.forEach((action, index) => {
			const button = $(
				`<button class="btn btn-xs ${index === 0 || action.tone === "primary" ? "btn-primary" : "btn-default"} ia-action-btn">${injection_mrp.ui.escape(action.label)}</button>`
			);
			button.on("click", action.on_click);
			strip.append(button);
		});
		target.append(strip);
	};

	injection_mrp.ui.render_cards = function (target, cards) {
		target.html(
			(cards || [])
				.map(
					(card) => `
					<div class="ia-card">
						<span class="ia-card-label">${injection_mrp.ui.escape(card.label)}</span>
						<div class="ia-card-value">${injection_mrp.ui.escape(card.value)}</div>
						${card.note ? `<div class="ia-muted ia-card-note">${injection_mrp.ui.escape(card.note)}</div>` : ""}
					</div>`
				)
				.join("")
		);
	};

	injection_mrp.ui.render_status = function (target, parts) {
		target.html(`
			<div class="ia-status-line">
				${(parts || [])
					.map(
						(part, index) => `
						<div class="ia-status-cell ${index === 0 ? "ia-status-cell-wide" : ""}">
							<span class="ia-status-label">${index === 0 ? __("Current View") : __("Summary")}</span>
							<div class="ia-status-value">${injection_mrp.ui.escape(part)}</div>
						</div>`
					)
					.join("")}
			</div>
		`);
	};

	injection_mrp.ui.render_table = function (target, columns, rows, options) {
		options = options || {};
		const pagination = options.pagination || {};
		const paginationHtml = options.pagination
			? `
				<div class="imrp-pagination">
					${injection_mrp.ui.icon_button("left", __("Previous Page"), { "data-imrp-page": "previous", disabled: !pagination.has_previous })}
					<span class="ia-muted">${injection_mrp.ui.escape(
						__("{0}-{1} of {2}")
							.replace("{0}", injection_mrp.ui.format_number((pagination.limit_start || 0) + ((rows || []).length ? 1 : 0), 0))
							.replace("{1}", injection_mrp.ui.format_number((pagination.limit_start || 0) + (rows || []).length, 0))
							.replace("{2}", injection_mrp.ui.format_number(pagination.total_count || 0, 0))
					)}</span>
					${injection_mrp.ui.icon_button("right", __("Next Page"), { "data-imrp-page": "next", disabled: !pagination.has_next })}
				</div>`
			: "";
		const legendHtml = options.show_code_legend === false ? "" : injection_mrp.ui.code_legend_for_rows(rows, columns, options);
		const render_toolbar = () =>
			options.exportable || options.show_count !== false || options.toolbar_html
				? `
				<div class="ia-table-toolbar">
					<div class="imrp-table-meta">
						<div class="ia-table-count">${
							options.pagination
								? __("{0} rows").replace("{0}", injection_mrp.ui.format_number(pagination.total_count || (rows || []).length, 0))
								: __("{0} rows").replace("{0}", injection_mrp.ui.format_number((rows || []).length, 0))
						}</div>
					</div>
					<div class="ia-table-actions">
						${paginationHtml}
						${legendHtml || ""}
						${options.toolbar_html || ""}
						${
							options.exportable && rows && rows.length
								? injection_mrp.ui.icon_button("download", __("Export Excel"), { "data-imrp-export-table": "1" })
								: ""
						}
					</div>
				</div>`
				: "";
		if (!rows || !rows.length) {
			target.html(`
				${render_toolbar()}
				<div class="ia-table-empty">
					<div class="ia-empty-title">${__("No rows found")}</div>
					<div class="ia-muted">${injection_mrp.ui.escape(options.empty || __("Try changing the filters or refreshing the data."))}</div>
				</div>
			`);
			return;
		}
		const head = columns.map((col) => `<th data-imrp-tooltip="${injection_mrp.ui.escape(col.header_title || col.label)}">${injection_mrp.ui.escape(col.label)}</th>`).join("");
		const body = rows
			.map((row) => {
				const cells = columns
					.map((col) => {
						const value = col.formatter
							? col.formatter(row[col.fieldname], row)
							: injection_mrp.ui.escape(row[col.fieldname] == null ? "" : row[col.fieldname]);
						const cls = col.numeric ? "ia-cell-number" : "";
						return `<td class="${cls}">${value == null ? "" : value}</td>`;
					})
					.join("");
				return `<tr data-name="${injection_mrp.ui.escape(row.name || "")}">${cells}</tr>`;
			})
			.join("");
		target.html(`
			${render_toolbar()}
			<div class="ia-table-shell">
				<table class="ia-table imrp-table"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>
			</div>
		`);
		if (options.exportable) {
			target.find("[data-imrp-export-table='1']").on("click", () => {
				injection_mrp.ui.export_rows(options.export_file_name || options.export_title || "mrp_export", columns, rows, options);
			});
		}
		if (options.pagination && options.on_page) {
			target.find("[data-imrp-page='previous']").on("click", () => options.on_page(options.pagination.previous_start || 0));
			target.find("[data-imrp-page='next']").on("click", () => options.on_page(options.pagination.next_start || 0));
		}
		if (options.on_row_click) {
			target.find("tbody tr").on("click", function () {
				const name = $(this).data("name");
				const row = rows.find((candidate) => candidate.name === name);
				options.on_row_click(row || { name });
			});
		}
	};

	injection_mrp.ui.add_text_filter = function (target, label, fieldname, filters, on_change, fieldtype, options) {
		const control = frappe.ui.form.make_control({
			parent: target[0],
			df: { fieldtype: fieldtype || "Data", label, fieldname, options },
			render_input: true,
		});
		control.$wrapper.addClass("imrp-filter");
		control.set_value(filters[fieldname] || "");
		control.df.onchange = () => {
			filters[fieldname] = control.get_value();
			on_change();
		};
		return control;
	};

	injection_mrp.ui.export_rows = function (filename, columns, rows, options) {
		options = options || {};
		if (!rows || !rows.length) {
			frappe.show_alert({ message: __("No rows available to export."), indicator: "orange" });
			return;
		}
		const exportColumns = (options.export_columns || columns || []).filter((column) => column.exportable !== false);
		const exportRows = (rows || []).map((row) => {
			const exportRow = {};
			exportColumns.forEach((column) => {
				let value;
				if (column.export_value) {
					value = column.export_value(row);
				} else if (column.fieldname === "item_code" && row.item_name) {
					value = `${row.item_code || ""}\n${row.item_name || ""}`.trim();
				} else {
					value = row[column.fieldname];
				}
				exportRow[column.fieldname] = value;
			});
			return exportRow;
		});
		const payload = {
			title: options.export_title || __("Export Excel"),
			subtitle: options.export_subtitle || "",
			sheet_name: options.export_sheet_name || filename || "MRP",
			file_name: `${filename || "mrp_export"}.xlsx`,
			columns: exportColumns.map((column) => ({
				label: column.label,
				fieldname: column.fieldname,
				fieldtype: column.fieldtype || (column.numeric ? "Float" : "Data"),
			})),
			rows: exportRows,
		};
		const xhr = new XMLHttpRequest();
		xhr.open("POST", "/api/method/injection_mrp.api.app.export_table_xlsx", true);
		xhr.responseType = "blob";
		xhr.withCredentials = true;
		xhr.setRequestHeader("Content-Type", "application/x-www-form-urlencoded; charset=UTF-8");
		xhr.setRequestHeader("X-Frappe-CSRF-Token", frappe.csrf_token || "");
		xhr.onload = function () {
			if (xhr.status < 200 || xhr.status >= 300) {
				frappe.show_alert({ message: __("Excel Export Failed"), indicator: "red" });
				return;
			}
			const link = document.createElement("a");
			link.href = URL.createObjectURL(xhr.response);
			link.download = payload.file_name;
			link.click();
			URL.revokeObjectURL(link.href);
		};
		xhr.onerror = function () {
			frappe.show_alert({ message: __("Excel Export Failed"), indicator: "red" });
		};
		xhr.send(`payload_json=${encodeURIComponent(JSON.stringify(payload))}`);
	};

	injection_mrp.ui.open_drawer = function (title, sections) {
		$(".imrp-drawer").remove();
		const drawer = $(`
			<div class="imrp-drawer">
				<div class="imrp-drawer-head">
					<h3 class="imrp-drawer-title">${injection_mrp.ui.escape(title)}</h3>
					<button class="btn btn-default btn-xs">${__("Close")}</button>
				</div>
				<div class="imrp-drawer-body"></div>
			</div>
		`);
		drawer.find("button").on("click", () => drawer.remove());
		const body = drawer.find(".imrp-drawer-body");
		(sections || []).forEach((section) => {
			if (section.html) {
				body.append(`
					<div class="ia-panel imrp-section">
						<h4>${injection_mrp.ui.escape(section.title)}</h4>
						${section.html}
					</div>`);
				return;
			}
			const rows = (section.rows || [])
				.map(
					(row) => `
					<div class="key">${injection_mrp.ui.escape(row.label)}</div>
					<div class="value">${row.html || injection_mrp.ui.escape(row.value || "")}</div>`
				)
				.join("");
			body.append(`
				<div class="ia-panel imrp-section">
					<h4>${injection_mrp.ui.escape(section.title)}</h4>
					<div class="imrp-kv">${rows}</div>
				</div>`);
		});
		$("body").append(drawer);
		return drawer;
	};

	injection_mrp.ui.mini_table_html = function (columns, rows, emptyMessage) {
		if (!rows || !rows.length) {
			return `<div class="ia-muted">${injection_mrp.ui.escape(emptyMessage || __("No rows found"))}</div>`;
		}
		return `
			<div class="imrp-mini-table-wrap">
				<table class="ia-table imrp-mini-table">
					<thead><tr>${columns
						.map((column) => `<th data-imrp-tooltip="${injection_mrp.ui.escape(column.header_title || column.label)}">${injection_mrp.ui.escape(column.label)}</th>`)
						.join("")}</tr></thead>
					<tbody>
						${rows
							.map(
								(row) => `
								<tr>
									${columns
										.map((column) => {
											const raw = row[column.fieldname];
											const value = column.formatter ? column.formatter(raw, row) : injection_mrp.ui.escape(raw == null ? "" : raw);
											return `<td>${value == null ? "" : value}</td>`;
										})
										.join("")}
								</tr>`
							)
							.join("")}
					</tbody>
				</table>
			</div>
		`;
	};
})();
