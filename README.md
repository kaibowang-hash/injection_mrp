# Injection MRP

Injection MRP is an ERPNext V15-compatible manufacturing planning app for injection moulding and downstream processes.

The app keeps MRP calculation independent from ERPNext Production Plan while still treating open Material Request, Purchase Order, Work Order and Production Plan related supply as offsets to avoid duplicate procurement.

It supports two planning layers:

- **Forecast Prebuy** for long-lead materials based on customer delivery schedules, sales order backlog and safety stock.
- **Firm APS** for approved or applied APS demand, consuming earlier prebuy commitments and creating only the shortage Material Request.

MRP also stores supply-demand pegging lines. Each material demand can show which stock, Material Request, Purchase Order, Work Order or prebuy supply is allocated to it, the expected arrival date, delivery variance, warning reason and suggested adjustment action.

See the Chinese user guide: [docs/user_guide.md](docs/user_guide.md).
