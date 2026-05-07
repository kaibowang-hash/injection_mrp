from __future__ import annotations

import frappe

from injection_mrp.services import stock_buffer


def enqueue_daily_stock_buffer_refresh():
	frappe.enqueue(
		"injection_mrp.tasks.stock_buffer.refresh_active_stock_buffers_job",
		queue="long",
		timeout=3600,
		job_name="injection_mrp_refresh_stock_buffers",
	)


def refresh_active_stock_buffers_job():
	return stock_buffer.refresh_active_stock_buffers(ignore_permissions=True)
