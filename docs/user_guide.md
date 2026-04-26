# 注塑 MRP 使用手册

本文档适用于基于 ERPNext V15 的 `injection_mrp` App。系统面向注塑行业，兼顾注塑后续工序，例如印刷、喷油、外协加工等。MRP 的核心目标不是替代 APS，而是在 APS 审批周期较长、原材料前置时间较长的场景下，提前识别采购风险，并在 APS 确认后把预测预采和正式需求衔接起来。

## 一、系统定位

`injection_mrp` 采用两层计划逻辑：

1. **Forecast Prebuy，远期预采**
   - 用于覆盖 APS 尚未审批、但客户排期或销售订单已经体现的远期需求。
   - 默认计划周期为 120 天。
   - 适合长前置时间原料、包材、进口料、外协材料等提前发起采购需求。

2. **Firm APS，APS 确认需求**
   - 用于 APS 已审批、已释放或已应用后的近期确定需求。
   - 默认计划周期为 45 天。
   - 系统会优先消耗已存在的预采供应，不足部分再生成正式物料需求建议。

MRP 不以 ERPNext 原生 `Production Plan` 作为主流程，也不会弃用它。`Production Plan` 已经生成的 Material Request、Purchase Order、Work Order 会作为供应抵扣，避免重复采购；但 MRP 的主要需求来源仍然是客户排期、销售订单、安全库存和 APS。

## 二、核心单据

### MRP Settings

系统设置单据，用于维护计划周期、备料天数、预警策略和默认物料需求类型。

常用字段：

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| `Firm Horizon Days` | 45 | APS 确认需求的计划周期 |
| `Prebuy Horizon Days` | 120 | 远期预采的计划周期 |
| `Forecast Consumption Window Days` | 30 | 预测冲销窗口配置，供后续滚动策略扩展 |
| `Material Staging Days` | 7 | 物料需要比生产或交付提前多少天到位 |
| `Early Supply Warning Days` | 7 | 预计到货早于物料需求日期超过多少天触发提前预警 |
| `Late Supply Tolerance Days` | 0 | 预计到货晚于物料需求日期多少天开始预警 |
| `Warn Missing Lead Time` | 勾选 | 物料没有前置时间时生成预警 |
| `Use Material Need Date For Pegging` | 勾选 | 供需匹配按物料需求日期判断 |
| `Allow Prebuy Material Request` | 勾选 | 允许远期预采生成 Material Request |
| `Include Production Plan As Supply` | 勾选 | 保留生产计划相关供应抵扣 |
| `Include Production Plan As Demand` | 不勾选 | 默认不把生产计划作为需求来源 |
| `Default Material Request Type` | Purchase | 默认生成采购类 Material Request |
| `Rolling Daily Horizon Days` | 60 | 滚动余额近多少天按日显示，之后按周汇总 |

### MRP Supply Rule

供应规则用于覆盖系统自动判断的供应方式。它适合处理注塑企业中同一物料在不同客户、仓库或业务场景下有不同来源的情况，例如某客户指定客供料、某仓库只做内部调拨、某类半成品固定自产。

路由优先级：

1. `MRP Supply Rule` 精确匹配。
2. Item 客供料、外协、默认 Material Request Type。
3. BOM 行 `sourced_by_supplier`。
4. 是否存在默认 BOM。
5. 是否采购物料。
6. MRP Settings 默认 Material Request Type。

支持的供应方式：

| 供应方式 | 说明 | 生成建议 |
| --- | --- | --- |
| `Purchase` | 采购物料 | Purchase MR |
| `Manufacture` | 自产半成品或成品 | Manufacture MR |
| `Subcontracting` | 外协物料 | Subcontracting MR |
| `Customer Provided` | 客供料 | Customer Provided MR |
| `Material Transfer` | 内部调拨 | Material Transfer MR |
| `Supplier Supplied` | 供应商自供料 | 不生成 MR，只展示追溯和异常 |
| `No Action` | 不处理 | 不生成 MR |

采购物料还可以在供应规则上维护采购约束：

| 字段 | 说明 |
| --- | --- |
| `Supplier` | 优先供应商。生成 MR 建议和后续 MR 行追溯都会带出 |
| `Purchase UOM` | 采购计量单位，用于提示采购确认 |
| `Minimum Order Qty` | 最低采购量。净需求低于 MOQ 时，建议数量会抬高到 MOQ |
| `Order Multiple Qty` | 下单倍量或最小包装量。建议数量会按倍量向上取整 |
| `Supplier Lead Time Days` | 供应商前置时间。优先于 Item 前置时间参与建议下单日计算 |

如果供应规则没有维护价格或供应商，系统会尝试参考已提交且有效的 `Supplier Quotation`、Buying `Item Price`、`Item Supplier` 和 Item Default Supplier。MRP 只把这些信息带入建议和追溯，不直接生成 PO，也不替代采购员在 MR 转 PO 时的最终询价、比价和审批。

### MRP Run

一次 MRP 运算记录。每次点击运行远期预采或 APS 确认 MRP，系统都会生成一张 MRP Run，并保存需求快照、物料需求、供需匹配、异常和建议批次。

常见状态：

| 状态 | 说明 |
| --- | --- |
| `Draft` | 草稿 |
| `Calculated` | 已计算，没有生成释放建议 |
| `Proposal Generated` | 已生成建议批次 |
| `Released` | 建议已应用，已生成 MR 或完成预采冲销 |

### MRP Demand Snapshot

需求快照。系统会把客户排期、销售订单、安全库存、APS 结果等转成快照，避免后续源单据变化导致本次 MRP 结果不可追溯。

主要来源：

| 来源 | 说明 |
| --- | --- |
| `Customer Delivery Schedule` | 客户排期，主要用于远期预采 |
| `Sales Order` | 已提交销售订单未交数量 |
| `Safety Stock` | 物料安全库存缺口 |
| `APS Schedule Result` | APS 已审批、已释放或已应用的排程结果 |
| `Production Plan` | 仅在设置中开启时作为需求来源 |

### MRP Requirement Line

物料需求明细。系统对需求进行 BOM 展开后，按物料、仓库、需求日期计算毛需求、库存抵扣、开放供应抵扣、预采抵扣和净需求。

重点字段：

| 字段 | 说明 |
| --- | --- |
| `Gross Qty` | BOM 展开后的毛需求，含损耗 |
| `Stock` / `Available Qty` | 当前库存抵扣 |
| `Open MR Qty` | 未关闭 Material Request 抵扣，不含预采 |
| `Open PO Qty` | 未关闭 Purchase Order 抵扣 |
| `Open WO Qty` | 未关闭 Work Order 抵扣 |
| `Prebuy Consumed Qty` | APS 确认需求消耗的预采数量 |
| `Net Qty` | 仍需新建供应的数量 |
| `Material Need Date` | 物料实际需要到位日期 |
| `Suggested Order Date` | 建议下单日期 |
| `Expected Arrival Date` | 预计最晚到货日期 |
| `Delivery Variance Days` | 预计到货与物料需求日期的差异 |
| `Warning Count` | 当前需求相关预警数量 |
| `Supply Mode` | 供应方式，例如采购、自产、外协、客供、调拨 |
| `First Shortage Date` | 滚动余额首次出现欠料或安全库存缺口的日期 |
| `Lowest Projected` | 计划周期内最低预计结余 |
| `Supplier` | 采购建议的参考供应商 |
| `Supplier Lead Time Days` | 参考供应商前置时间，优先于 Item 前置时间 |
| `Minimum Order Qty` | 最低采购量 |
| `Order Multiple Qty` | 下单倍量或最小包装量 |
| `Order Excess Qty` | 因 MOQ 或倍量向上取整带来的超额建议量 |
| `Supplier Quotation` / `Item Price` | 参考报价来源 |
| `Estimated Rate` / `Estimated Amount` | 参考单价和估算金额 |
| `Procurement Constraint Summary` | 采购约束、供应商、价格来源的摘要 |

### MRP Pegging Line

供需匹配明细。它回答采购员最关心的问题：这条需求由哪笔供应满足，预计什么时候到，是否太早或太晚，是否需要提前、延后、取消或新增。

一条需求可能对应多条供应。例如同一个物料 1000 kg 的需求，可能由库存 200 kg、采购订单 500 kg、预采 MR 200 kg、新建 MR 100 kg 共同满足。系统会为这些分配分别生成 Pegging Line。

重点字段：

| 字段 | 说明 |
| --- | --- |
| `Demand Type` | 需求来源类型 |
| `Demand Source` | 需求来源单据 |
| `Supply Type` | 供应类型，例如库存、MR、PO、WO、预采、计划新供应 |
| `Supply Document` | 供应来源单据 |
| `Original Supply Qty` | 供应单据原数量 |
| `Supply Qty` | 分配给本需求的数量 |
| `Remaining Supply Qty` | 分配后该供应剩余数量 |
| `Expected Arrival Date` | 预计到货日期 |
| `Delivery Variance Days` | 到货日期减物料需求日期，正数代表延迟，负数代表提前 |
| `Adjustment Action` | 调整建议 |
| `Warning Category` | 预警类别 |
| `Warning Reason` | 预警原因 |

### MRP Proposal Batch

建议批次。MRP 计算后，如果需要新建 Material Request 或消耗预采，会生成建议批次。用户必须在 `MRP Release Center` 中手动应用，系统才会生成 MR 或更新预采消耗数量。

`Ready` 状态下允许在建议明细层人工调整：

- 可改数量、计划日期、仓库、来源仓库、MR 类型、供应方式、客户、备注。
- 可将行改为 `No Action` 或 `Skipped`，表示跳过；原始计算行不会物理删除。
- 可手工新增建议行，新增行会标记 `Manual Override`。
- 已应用批次不可再编辑。

应用建议时，系统按 `Material Request Type + Commitment Type + Warehouse + Source Warehouse + Customer` 分组生成 Material Request。MRP 仍只生成 MR，不直接生成 WO、PO 或外协单。

### MRP Rolling Balance Line

滚动余额行用于回答“什么时候开始欠料”。系统按物料和仓库，把期初库存、需求、已有供应、本次计划供应放进时间桶：

- 默认近 60 天按日计算。
- 60 天之后到 MRP 计划周期结束按周计算。
- 需求日期使用 `Material Need Date`，不是客户交货日期。

重点字段：

| 字段 | 说明 |
| --- | --- |
| `Opening Qty` | 该日期桶开始前的预计结余 |
| `Demand Qty` | 该日期桶内物料需求 |
| `Supply Qty` | 该日期桶内已有供应到货 |
| `Planned Supply Qty` | 本次 MRP 新建议供应 |
| `Projected Qty` | 扣减需求并加入供应后的预计结余 |
| `Shortage Qty` | 预计结余小于 0 的硬欠料数量 |
| `Safety Stock Gap Qty` | 预计结余低于安全库存的缺口 |

### MRP Shortage Alert

欠料预警是滚动余额的汇总。每个物料和仓库在一次 Run 中出现风险时生成一条预警，显示首次欠料日期、最晚下单日期、最低预计结余和受影响需求。

判断逻辑：

- `Projected Qty < 0` 为硬欠料，预警级别 `Critical`。
- `Projected Qty < Safety Stock` 为安全库存缺口，预警级别 `Warning`。
- `Latest Order Date = First Shortage Date - Item.lead_time_days`。

### MRP Exception Log

异常和预警日志。用于记录 BOM 缺失、前置时间缺失、供应延迟、供应过早、供应超额等问题。

## 三、前置时间和日期逻辑

系统从 `Item.lead_time_days` 读取物料前置时间。

采购物料如果匹配到 `MRP Supply Rule.Supplier Lead Time Days`、有效 `Supplier Quotation` 或 Buying `Item Price` 中的前置时间，会优先使用供应商维度的前置时间。这样同一个物料不同供应商或不同规则下可以得到不同的建议下单日。

核心公式：

```text
material_need_date = required_date - material_staging_days
suggested_order_date = material_need_date - lead_time_days
expected_arrival_date = supply_date
```

新建供应建议的预计到货日期按以下逻辑推算：

```text
expected_arrival_date = suggested_order_date + lead_time_days
```

示例：

| 条件 | 值 |
| --- | --- |
| 客户或 APS 需求日期 | 2026-07-30 |
| 备料提前天数 | 7 天 |
| 物料前置时间 | 60 天 |
| 物料需求日期 | 2026-07-23 |
| 建议下单日期 | 2026-05-24 |

如果物料没有维护前置时间，系统仍然允许 MRP 计算，但会生成 `Missing Lead Time` 预警，提醒维护物料主数据。

## 四、供应抵扣顺序

MRP 会把可用供应转成逐笔供应记录，再按优先级和日期分配给需求。

默认优先级：

| 供应类型 | 优先级 | 说明 |
| --- | --- | --- |
| `Stock` | 95 | 当前库存优先消耗 |
| `Material Request` | 90 | 未关闭 MR，预采 MR 除外 |
| `Purchase Order` | 80 | 已提交、未关闭 PO |
| `Work Order` | 70 | 未关闭 WO |
| `Prebuy` | 50 | 预采 MR |
| `Planned Supply` | 0 | 本次 MRP 新建议供应 |

对于 `Firm APS`，系统会先使用库存、开放 MR、PO、WO，再消耗可用预采，最后对不足数量生成正式 MR 建议。

对于 `Forecast Prebuy`，系统会生成预采建议；如果已经存在可用预采，也会作为供应抵扣，减少重复预采。

## 五、预警规则

### Late Supply

预计到货日期晚于物料需求日期。系统建议 `Expedite`，即提前或催交。

示例：

| 字段 | 值 |
| --- | --- |
| 物料需求日期 | 2026-07-23 |
| 预计到货日期 | 2026-07-25 |
| 交期差异 | 2 |
| 建议 | 提前或催交 |

### Early Supply

预计到货日期早于物料需求日期，并超过 `Early Supply Warning Days`。系统建议 `Delay`，即延后到货，减少库存提前占用。

示例：

| 字段 | 值 |
| --- | --- |
| 物料需求日期 | 2026-07-23 |
| 预计到货日期 | 2026-07-10 |
| 提前天数 | 13 |
| 提前预警天数 | 7 |
| 建议 | 延后 |

如果提前不超过 7 天，默认视为可接受备料缓冲，不生成预警。

### Past Due Order

建议下单日期早于今天。说明即使今天立刻下单，也可能已经赶不上物料需求日期。

处理建议：

- 采购确认是否有替代供应或加急渠道。
- PMC 或生产确认是否需要调整生产日期。
- 如有预采 MR 或现有 PO，检查是否可以优先分配。

### Excess Supply

已有供应没有被本次需求消耗。常见原因：

- Forecast Prebuy 数量大于 APS 确认数量。
- 客户需求取消或延期。
- PO 或 MR 数量超过当前需求。
- 仓库或物料维度不一致，导致无法匹配。

系统只给出取消、延后或人工复核建议，不会自动取消已提交 PO 或 MR。

### Missing Lead Time

物料没有维护 `lead_time_days`。系统仍然计算，但建议采购或物控维护物料主数据。

### Missing Supplier

采购建议没有找到供应商。系统仍然生成 MR 建议，但采购员需要在 MR 转 PO 或询价时人工确认供应商。

### Purchase Constraint Rounding

采购建议数量因为最低采购量、下单倍量或最小包装量被向上取整。`Net Qty` 仍代表真实缺口，`Planned` 和建议批次数量代表建议下单量，`Order Excess Qty` 代表因采购约束产生的超额量。

### Missing BOM

成品或半成品没有可用的已提交默认 BOM。系统会保留在需求物料本身，不继续展开。

处理建议：

- 检查 Item 是否设置默认 BOM。
- 检查 BOM 是否已提交、启用、默认。
- 检查 BOM 项是否设置了不展开。

## 六、调整建议说明

| 调整建议 | 含义 | 常见处理 |
| --- | --- | --- |
| `No Adjustment` | 无需调整 | 保持现状 |
| `Expedite` | 供应晚于需求 | 催交、提前、改加急 |
| `Delay` | 供应过早 | 协调供应商延后交货 |
| `Cancel` | 供应剩余未消耗 | 复核是否取消或减少 |
| `Review` | 需要人工判断 | 由采购、PMC 或生产复核 |
| `Create Material Request` | 需要新增 MR | 在释放中心应用建议 |
| `Consume Prebuy` | 消耗预采 | 在释放中心应用建议 |

## 七、页面使用说明

### MRP Run Console

路径：`/desk/mrp-run-console`

用途：创建和查看 MRP 运算。

常用操作：

1. 点击 `Forecast Prebuy` 运行远期预采。
2. 选择公司、物料、客户、仓库、计划日期等筛选条件。
3. 点击 `Firm APS` 运行 APS 确认 MRP。
4. 查看需求数、物料需求数、异常数、净需求数量和建议批次。

建议使用方式：

- 每周或每天固定运行远期预采，覆盖长前置时间物料。
- APS 审批或排程释放后，运行 APS 确认 MRP。
- 对同一 Run 已应用建议后，不建议重新计算，避免影响追溯。

### MRP Demand Console

路径：`/desk/mrp-demand-console`

用途：查看需求快照。

常用筛选：

- MRP Run
- Company
- Demand Type
- Item
- Customer

检查重点：

- 客户排期是否进入 MRP。
- 销售订单未交数量是否正确。
- APS 需求是否来自正确的 APS Run。
- Required Date 是否落在计划周期内。

### MRP Material Workbench

路径：`/desk/mrp-material-workbench`

用途：采购和 PMC 的主要工作台。用于查看物料层面的毛需求、供应抵扣、净需求、建议下单日期、预计到货和预警。

表格重点列：

| 列 | 说明 |
| --- | --- |
| `Type` | 远期预采或 APS 确认 |
| `Commitment` | 预采或确认 |
| `Status` | 当前需求状态 |
| `Item` | 物料号，下方显示物料名称或描述 |
| `Supply Mode` | 采购、自产、外协、客供、调拨等供应方式 |
| `MR Type` | 建议生成的 Material Request Type |
| `Supplier` | 参考供应商 |
| `First Shortage Date` | 首次欠料或安全库存缺口日期 |
| `Lowest Projected` | 滚动周期内最低预计结余 |
| `Required Date` | 需求日期 |
| `Material Need Date` | 物料实际需要日期 |
| `Gross` | 毛需求 |
| `Stock` | 库存抵扣 |
| `MR` | 开放 MR 抵扣 |
| `PO` | 开放 PO 抵扣 |
| `WO` | 开放 WO 抵扣 |
| `Prebuy` | 预采消耗 |
| `Planned` | 新建议供应 |
| `Order Excess` | 因 MOQ 或倍量产生的超额建议量 |
| `Net` | 净需求 |
| `Order Date` | 建议下单日期 |
| `Expected Arrival` | 预计到货日期 |
| `Variance` | 到货差异 |
| `Warnings` | 预警数量 |

点击任意行会打开右侧抽屉：

- `Requirement`：查看需求物料、需求日期、物料需求日期、建议下单日期、预计到货等。
- `Shortage Alerts`：查看首次欠料日期、最低结余、最晚下单日等滚动风险。
- `Rolling Balance`：查看该物料逐日/逐周的期初、需求、供应、预计结余。
- `Demand Source`：查看客户排期、销售订单、APS 来源。
- `BOM Confirmation`：确认本次需求使用的成品/半成品 BOM、BOM 状态、BOM 用量和当前物料对应的 BOM 行。
- `BOM Explosion Path`：查看从需求物料展开到当前物料的路径，便于追溯多层 BOM。
- `BOM Expanded Items`：查看需求 BOM 的展开明细，包括组件物料、用量、父项、子 BOM、不展开标记和来源仓库。
- `Supply Offset`：查看库存、MR、PO、WO、预采、新建供应抵扣。
- `Procurement Constraints`：查看供应商、供应商前置时间、MOQ、下单倍量、参考报价、估算金额和采购约束摘要。
- `Pegging Detail`：查看逐笔供需匹配。
- `Exceptions`：查看异常和预警。

确认 BOM 时，建议先看 `BOM Confirmation`：

- `Demand BOM` 是需求来源物料使用的 BOM。
- `Requirement BOM` 是当前需求行所属的直接 BOM，单层 BOM 时通常和 `Demand BOM` 一致，多层 BOM 时可能不同。
- `BOM Row Item` 是当前物料在 BOM 中对应的行，和表格中的 `Item` 应一致。
- `Row Qty` / `Stock Qty` 用于确认 BOM 行用量和库存计量数量。
- `Child BOM` / `Do Not Explode` 用于判断半成品是否继续展开。
- `BOM Status` 应优先为已提交、启用、默认；如果不是，需回到 ERPNext BOM 主数据确认。

### MRP Pegging Detail

路径：`/desk/mrp-pegging-detail`

用途：查看逐笔供需匹配。这个页面最接近客户样表的逻辑，用于解释每条需求由哪笔供应满足。

常用筛选：

- MRP Run
- Company
- Item
- Warehouse
- Supply Type
- Warning Level
- Adjustment

表格重点列：

| 列 | 说明 |
| --- | --- |
| `Demand Qty` | 该需求的毛需求数量 |
| `Supply Type` | 供应类型 |
| `Supply Document` | 供应单据 |
| `Original Supply Qty` | 供应原数量 |
| `Supply Qty` | 本行分配数量 |
| `Remaining Supply Qty` | 分配后剩余供应 |
| `Supply Date` | 供应日期 |
| `Expected Arrival` | 预计到货 |
| `Variance` | 交期差异 |
| `Adjustment` | 调整建议 |
| `Warning Reason` | 预警原因 |

理解 `Original Supply Qty` 和 `Supply Qty`：

- `Original Supply Qty` 是供应单据原始数量。
- `Supply Qty` 是本行分配给某条需求的数量。
- 同一张 PO 可以拆分给多条需求，所以多行的 `Original Supply Qty` 可能相同，但 `Supply Qty` 不同。

### MRP Shortage Timeline

路径：`/desk/mrp-shortage-timeline`

用途：查看滚动欠料。它比单条需求更适合采购和 PMC 做日常跟踪，因为它按物料、仓库、日期桶显示预计结余变化。

建议每天重点筛选：

- `Warning Level = Critical`：预计结余小于 0，已形成硬欠料。
- `Warning Level = Warning`：没有硬欠料，但低于安全库存。
- `Latest Order Date` 早于或接近今天的行。
- 长前置时间物料、进口料、关键色粉、包材等。

点击预警行会打开右侧抽屉，可查看：

- 受影响的需求明细。
- 逐日或逐周滚动余额。
- 首次欠料日期、最低预计结余和最晚下单日。

### MRP Release Center

路径：`/desk/mrp-release-center`

用途：应用 MRP 建议。

操作流程：

1. 检查建议批次状态是否为 `Ready`。
2. 点击批次行，进入右侧抽屉检查建议明细。
3. 必要时修改数量、计划日期、仓库、MR 类型、来源仓库、客户或备注。
4. 采购建议行会显示参考供应商、采购约束、超额建议量和估算金额；供应商可以在释放前调整。
5. 不需要释放的行点击跳过，系统会改为 `No Action / Skipped` 并保留追溯。
6. 需要临时补充的建议可以点击新增行。
7. 保存修改后点击 `Apply Proposal`。
8. 系统会生成 Material Request 或更新预采消耗数量。

系统不会自动生成 PO、WO，也不会自动取消 PO。采购和外协流程仍然走 ERPNext 标准流程。

建议批次中的采购数量不是简单等于净需求：如果物料命中最低采购量或下单倍量，MRP 会把建议数量向上取整，并把超额量写入 `Order Excess Qty`。这可以让采购员在释放 MR 前就看到“真实缺口”和“采购约束后建议下单量”的差异。

## 八、推荐业务流程

### 1. 主数据准备

运行 MRP 前建议先检查：

- Item 是否维护 `lead_time_days`。
- 成品和半成品是否有已提交、启用、默认 BOM。
- BOM 中原料、包材、半成品、外协件是否完整。
- Item 是否维护默认仓库或销售订单/需求来源是否带仓库。
- 客户排期是否为 Active。
- 销售订单是否已提交且未关闭。
- APS Schedule Result 是否处于 Approved、Work Order Proposed、Shift Proposed 或 Applied。

### 2. 运行远期预采

适用场景：

- APS 审批周期约一个月。
- 原料前置时间约两个月。
- 客户已有排期或销售订单，但 APS 尚未审批。

操作：

1. 打开 `MRP Run Console`。
2. 点击 `Forecast Prebuy`。
3. 选择公司、计划日期，必要时限定客户、物料或仓库。
4. 运行后进入 `MRP Material Workbench` 查看净需求和预警。
5. 在 `MRP Release Center` 应用建议，生成 `Prebuy` 类型 Material Request。

### 3. APS 审批后运行 Firm APS

适用场景：

- APS 已审批或已应用。
- 需要把预测预采转为正式需求保障。

操作：

1. 打开 `MRP Run Console`。
2. 点击 `Firm APS`。
3. 选择公司和 APS Run，必要时限定物料或仓库。
4. 系统先抵扣库存、MR、PO、WO，再消耗 Prebuy。
5. 若 APS 确认需求大于预采，系统生成差额 MR 建议。
6. 若预采大于 APS 确认需求，系统生成超额供应预警，提示复核，不自动取消。

### 4. 采购处理

采购员建议每天重点查看：

- `MRP Material Workbench` 中 `Warnings > 0` 的行。
- `MRP Pegging Detail` 中 `Late Supply` 和 `Past Due Order`。
- `MRP Shortage Timeline` 中首次欠料日期和最晚下单日期临近的行。
- `MRP Release Center` 中 `Ready` 状态的建议批次。
- ERPNext 标准 Material Request 到 Purchase Order 的后续流程。
- MOQ、最小包装量、供应商、报价来源和估算金额是否合理。

采购经理建议重点关注：

- 大额 `Create Material Request` 建议。
- `Excess Supply` 风险。
- 关键原料或进口料的 `Past Due Order`。
- 供应过早导致库存占用的 `Early Supply`。

PMC 建议重点关注：

- 物料迟到是否影响 APS 排程。
- 是否需要调整生产日期。
- 是否存在客户排期变化导致的预采超额。

## 九、预采冲销逻辑

远期预采生成的 Material Request 会打上 MRP 标记：

- `custom_mrp_run`
- `custom_mrp_requirement`
- `custom_mrp_commitment_type = Prebuy`
- `custom_mrp_consumed_qty`
- `custom_mrp_remaining_qty`
- `custom_aps_run`
- `custom_aps_result`

APS 确认 MRP 应用建议时，系统会按公司、物料、仓库匹配可用预采，并更新预采 MR 明细的已消耗数量和剩余数量。

处理结果：

| 场景 | 系统处理 |
| --- | --- |
| APS 数量等于预采 | 预采被完全消耗，不新增 MR |
| APS 数量大于预采 | 先消耗预采，再对差额生成正式 MR |
| APS 数量小于预采 | 消耗部分预采，剩余部分进入超额供应预警 |

## 十、Excel 导出

各控制台表格右上角有下载图标按钮，样式与 APS 一致。

导出特点：

- 导出当前表格数据。
- 物料列导出为同一单元格中的物料号和物料名称。
- `MRP Pegging Detail` 导出包含预计到货、物料需求日期、交期差异、预警原因、调整建议等字段。

## 十一、权限说明

MRP 主操作角色从采购角色中拆出，统一由计划体系负责：

| 角色 | 权限说明 |
| --- | --- |
| `MPLM` | 计划经理。MRP 主负责人，可运行 MRP、重算、维护供应规则、审核并应用建议批次 |
| `MPLP` | 计划员。MRP 日常操作人，可运行 MRP、重算、编辑建议批次并应用建议 |
| `Purchase Manager` / `Purchase User` | 查看物料工作台、供需匹配、欠料时间轴、释放建议和导出；不再运行 MRP 或应用建议 |
| `GMC` | 管理复核角色，可查看、导出、复核异常和风险；不直接执行 MRP 主动作 |
| `PMC` | 计划协同角色，可查看需求、欠料、供需匹配和预警，协同确认生产影响 |
| `Manufacturing Manager` | 查看和复核生产、自制、外协相关需求和风险 |
| `Manufacturing User` | 主要查看生产相关需求和预警 |
| `Stock Manager` / `Stock User` | 查看库存和供应抵扣相关结果 |
| `Sales Manager` / `Sales User` | 查看需求来源、客户排期和销售订单相关结果 |
| `System Manager` | 全权限 |

服务端强制限制：`Forecast Prebuy`、`Firm APS`、MRP 重算、建议批次保存和 `Apply Proposal` 仅允许 `System Manager`、`MPLM`、`MPLP` 执行。其他角色即使能看到页面，也只作为业务复核和协同查看。

如果用户看不到页面或无法操作，请检查：

- 用户是否分配了对应角色。
- Page 和 Workspace 是否已同步角色。
- 是否已执行 `bench --site <site> migrate`。
- 是否已清理缓存并重新登录。

## 十二、常见问题

### 为什么旧的 MRP Run 没有 Pegging Detail？

供需匹配明细是在增强后新计算生成的。旧 Run 不会自动回填，避免改变历史结果。请对未释放的 Run 重新计算，或创建新的 MRP Run。

### 为什么物料需求日期为空？

通常是旧数据。新计算的 MRP Requirement Line 会写入 `Material Need Date`。

### 为什么没有生成 MR？

可能原因：

- 净需求为 0。
- 供应方式是 `Supplier Supplied` 或 `No Action`。
- 建议批次还没有在 `MRP Release Center` 应用。
- `Allow Prebuy Material Request` 未勾选。
- 当前用户没有应用建议权限。

### 自产、外协、客供料怎么处理？

MRP 会先判断供应方式，再生成对应 Material Request：

- 自产半成品：生成 `Manufacture` MR，后续由 ERPNext 标准流程转 Work Order。
- 外协物料：生成 `Subcontracting` MR，不直接生成外协 PO。
- 客供料：生成 `Customer Provided` MR，不生成采购建议。
- 供应商自供料：不生成 MR，只在 BOM 和异常追溯中体现。

### 为什么 MRP 没有读取 APS？

请检查：

- APS Schedule Result 是否属于当前公司。
- 状态是否为 Approved、Work Order Proposed、Shift Proposed 或 Applied。
- 需求日期是否落在 Firm Horizon 内。
- 运行 Firm APS 时是否选错 APS Run。

### 为什么有 Early Supply 预警？

预计到货早于物料需求日期超过设置的提前预警天数。系统提示延后，目的是减少库存提前占用。

### 为什么有 Past Due Order 预警？

建议下单日期已经早于今天。说明按当前前置时间和备料天数推算，采购动作已经晚了，需要尽快加急或调整计划。

### 为什么系统没有自动取消超额 PO？

MRP 只提示风险和建议，不自动修改或取消已提交 PO、MR、WO。取消、变更供应商交期、改数量仍需采购或相关负责人按 ERPNext 标准流程处理。

### MRP 会考虑最低采购量、最小包装量和供应商吗？

会，但定位是“计划建议层”：

- MRP 会参考 `MRP Supply Rule`、Item、Supplier Quotation、Buying Item Price、Item Supplier 等信息。
- 对采购类建议，系统会按最低采购量和下单倍量向上取整，生成更接近真实采购动作的 MR 建议数量。
- 系统会记录参考供应商、报价单、价格表、估算单价和估算金额，方便采购员复核。
- 最终供应商选择、议价、PO 价格、审批和合同仍在 ERPNext 标准采购流程中完成。

因此，MRP 不应该直接替采购做最终 PO 决策，但应该提前把采购约束暴露出来，否则“算出来缺 120 kg、实际必须买 500 kg”这类风险会被延后到采购阶段才发现。

### 为什么中文没有立刻生效？

请执行：

```bash
bench build --app injection_mrp
bench --site <site> clear-cache
bench restart
```

然后刷新浏览器或重新登录。

## 十三、建议日常检查清单

采购员每日检查：

- `Late Supply`
- `Past Due Order`
- `Create Material Request`
- 即将到期但未转 PO 的 MR
- 关键物料是否缺少前置时间
- 硬欠料和安全库存缺口是否已有处理动作

采购经理每周检查：

- 长前置时间物料的 Forecast Prebuy 覆盖率
- 预采超额和取消建议
- 大额采购需求和异常预警
- 供应过早导致的库存占用

PMC 每日或每周检查：

- APS 确认需求是否已运行 Firm APS
- 物料延迟是否影响生产日期
- 客户排期变化是否造成预采风险
- BOM 缺失或主数据缺失是否阻塞计划

生产管理检查：

- Work Order 是否已被 MRP 正确抵扣
- 生产计划相关供应是否重复
- 半成品和后续工序物料是否有缺口
