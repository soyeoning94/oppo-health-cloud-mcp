# OPPO 健康数据管理面板 —— 统计方案与 API 设计

> 版本：v1.0（2026-10-28）
> 范围：**只做设计与契约**，不含代码改动。本文所有单位/编码结论来自 `cloud_mcp.py` 常量与《HANDOFF-移交文档》实测记录；本机无 `cloud_cache.db`、无 `auth.json`，**未做真实数值复核**，凡标注「待实测确认」的项须用真实缓存样本核对后再进 UI。
> 目标：面板要「有用」（能看出趋势、作息、活动量、数据是否可信），但**不得输出医疗结论**。

---

## 0. 一句话结论

1. **能可靠展示的**：步数/距离/活动消耗（分段累加）、心率（点值分布与夜间基线）、睡眠分期（用 `sleep_status` 自己算，**不要**用 `sleep_stat`）、血氧（稀疏点值）、体重（点值+趋势）、压力/HRV（稀疏分布）、数据质量与同步状态。
2. **只能原样转述的**：`sleep_index`、`sleep_advice`、`ecg`、`atrial_fibril*`、`cardiovascular`、`snore*`/`sensor_osa`、`physical_mental*`、`vitamin`、`sunshine`、`ovulation`/`physiological_*` —— 这些是**厂商算法结论**，面板不做二次计算、不做汇总打分、不做风险分级。
3. **不该做的**：综合健康分、疾病风险、心血管年龄、把血氧低读数说成「缺氧」、把 HRV/压力说成诊断、把厂商筛查结果做成「异常排行榜」。
4. **数据现实**：120 天里只有 21 天有步数明细，9/5–10/27 有 7 周缺口，`sleep_stat` 4 个月仅 9 天且评分有 `null`/`0`。所以**面板的第一等公民是「覆盖率/缺口」而不是「趋势线」**——没有覆盖率的趋势线会骗人。

---

## 1. 数据事实基线（设计依据）

### 1.1 缓存结构（决定 API 怎么取数）

```sql
CREATE TABLE cloud_data(
  metric TEXT, date TEXT, payload TEXT, fetched_at INTEGER,
  PRIMARY KEY(metric, date)
)
```

关键点（读代码确认）：

| 事实 | 影响 |
|---|---|
| `date` 列**不是日期**，而是「自然键」：优先 `__cacheKey`，否则 `date` / `dataCreatedTimestamp` / `startTimestamp` / `measurementTime` / `modifiedTimestamp` / 当前秒 | 不能 `WHERE date BETWEEN ...`；按日期查询必须在应用层解析 `payload` 内时间戳。行数 ≠ 天数 |
| `PRIMARY KEY(metric,date)` + `INSERT OR REPLACE` | 同一自然键重拉即覆盖；无历史版本、无删除墓碑。「改过的记录」会静默替换 |
| `fetched_at` 是**行级**最后写入时间 | 可作为「该指标最近同步时间」，但无法还原「上次同步拉到几条新增」 |
| `cloud_query` 无日期时 `LIMIT 100` | 心率明细 100 行 ≈ 半天；**序列接口不能复用 `cloud_query`**，须直读缓存并分页 |
| 明细类自然键为毫秒时间戳，统计类为 `YYYYMMDD` | 序列接口必须按 metric 分派「时间来源字段」，见 §6.1 |
| `_payload_date()` 有兜底链（`date`→`dataCreatedTimestamp`→…→`modifiedTimestamp`） | 缺时间字段的记录会被归到「修改时间」那天，属**归属误差**，需标记 `timezone_assumed`/`date_inferred` |

### 1.2 稀疏与缺口（面板必须显式表达）

- 步数明细：120 天 / 21 天有数据（17.5%）；心率明细 3794 条覆盖 09-01..10-28；睡眠阶段 520 条覆盖 09-01..10-28。
- **09-05 → 10-27 共 7 周云端无数据，且不会自动回填**。
- `sleep_stat`：4 个月仅 9 天，`0719/0723` 评分为 `null`、`0806/0902` 为 `0` → **不作睡眠时长来源**。
- `hrv` / `stress` 独立接口返回空，现有数据来自 `physical_mental_detail` 嵌套项（带 `source: physical_mental_detail`）。
- `app_usage` 同步 60 天需 63 秒；`steps_detail` 120 天约 25 秒 → 同步状态卡要显示耗时，不能默认全量。
- `sport_file` 缺 `encryptKeyEnc`，`sport_record` 只是版本索引 → **运动轨迹不可展示**。

### 1.3 已知语义陷阱

- `sleepState`：`1` 清醒、`2` 深睡、`3` REM、`4` 浅睡、`5` 小睡（**不计入** `totalSleepTime`）。
- 腕温是**相对个人基线的偏移**，不是体温计绝对值 → 只能显示 `+0.3 °C`，禁止显示 `36.8 °C`。
- 睡眠归属夜：`02:42 → 08:19` 属于**醒来那天**（10-28）。归属规则不定死，所有睡眠数字都会错一天。
- 心率明细时间字段是 `dataCreatedTimestamp`，步数明细是 `startTimestamp`/`endTimestamp`/`modifiedTime`，睡眠汇总用 `date`。
- 统计接口区间左开右闭，`start == end` 返回 0 条；一次跨度 > 7 天报 `23103`。

---

## 2. 67 指标分级：原始 / 本地计算 / 厂商结论 / 不可用

分类定义（面板每个数字都必须挂其中一类）：

| 类别 | 代码值 | 含义 | 展示规则 |
|---|---|---|---|
| 原始数据 | `raw_server` | 云端返回的数值/区间，只做单位换算 | 可直接显示，必须带 `as_of` + 来源 |
| 本地计算 | `local_aggregate` | 用原始明细聚合/统计得到 | 必须标注「本面板计算」，并给算法名+样本数+覆盖率 |
| 厂商结论 | `vendor_verdict` | 服务端算法给的评分/等级/筛查/建议 | **原样转述**，注明「OPPO 算法」，禁重算、禁汇总、禁升级措辞 |
| 不可用 | `unavailable` | 缺密钥/未接入/仅索引 | 显示「不可用 + 原因」，不显示 0、不显示占位曲线 |

### 2.1 全量分组（65 独立 + 2 组合 = 67）

**A. 运动与活动（10）**

| metric | 类别 | 说明 / 面板用途 |
|---|---|---|
| `steps` | raw_server | 旧版步数明细，与 `steps_detail` 同族；只在缺 detail 时兜底 |
| `steps_detail` | raw_server → 日合计为 local | 主数据源；`steps`/`calories`/`distance`/`sportMode`/`startTimestamp` |
| `steps_stat` | raw_server | 服务端日汇总；用于与明细**交叉校验**，不用于替代明细 |
| `sport_stat` | raw_server | 运动统计（类型/时长/距离/热量） |
| `sport_record` | unavailable（内容） | 仅 `modifiedTime` 版本索引，不是轨迹 |
| `sport_file` | unavailable | 需 `encryptKeyEnc` |
| `sport_goal` | raw_server | 用户自设目标，非医学阈值 |
| `exercise_intensity` | raw_server | 强度分档统计，档位定义属厂商 |
| `sedentary` | raw_server | 久坐提醒事件 |
| `tumble` | raw_server | 跌倒事件记录（展示为「设备事件」） |

**B. 心率与 HRV（6）**

| metric | 类别 | 说明 |
|---|---|---|
| `heart_rate` | raw_server | 按天拉取；代码只取 `heartRateType == 0`（光学心率） |
| `heart_rate_detail` | raw_server | 主数据源，3794 条 / 09-01..10-28，约 20 秒一点 |
| `heart_rate_stat` | raw_server | 日统计（20 条 / 07-01..09-03），与明细交叉校验 |
| `heart_rate_warning` | raw_server | 设备触发的预警事件；**面板只报「设备记录了 N 次预警」，不解释因果** |
| `hrv` | raw_server（可能 derived-from-embedded） | 独立接口空 → 来自 `physical_mental_detail`，须透出 `derived_from` |
| `sleep_rr` | raw_server | 睡眠 RR 间期；是**唯一**能自算 rMSSD 的原料（样本够时） |

**C. 睡眠（6）**

| metric | 类别 | 说明 |
|---|---|---|
| `sleep_status` | raw_server → 分期统计为 local | **睡眠主数据源**：520 条阶段段 |
| `sleep_stat` | raw_server，质量差 | 仅 9 天、评分 `null`/`0`；**禁止**作为时长来源，仅作交叉校验 |
| `sleep` | raw_server，质量待评 | `querySleepDayStatData` 日汇总，与 `sleep_stat` 同族，同样降级为校验用 |
| `sleep_index` | vendor_verdict | 恢复率/睡眠血氧/睡眠心率区间/`warningNumber`，原样展示 |
| `sleep_advice` | vendor_verdict | 厂商文本建议，折叠区 |
| `sleep_hr_stat` | raw_server | 睡眠心率统计 |

**D. 血氧（5）**

| metric | 类别 | 说明 |
|---|---|---|
| `blood_oxygen` | raw_server | 按天拉取 |
| `blood_oxygen_detail` | raw_server | 131 条（仅 09-02 一天）→ 只能做「点值分布」，不能做趋势 |
| `blood_oxygen_stat` | raw_server | 3 条（08-04..09-02） |
| `blood_oxygen_warning` | raw_server | 设备预警事件 |
| `blood_oxygen_origin` | raw_server | 设备原始采样，采样率与 detail 不同，禁止与 detail 混算 |

**E. 压力 / 身心健康（5 独立 + 2 组合 = 7）**

| metric | 类别 | 说明 |
|---|---|---|
| `stress_detail` | raw_server（scale=厂商） | 0–100 量表值，来自 `physical_mental_detail` 时带 `source` |
| `stress_stat` | raw_server | 日统计 |
| `physical_mental_detail` | raw_server | 含嵌套 `hrv`/`stress` 子项，是上游数据 |
| `physical_mental_stat` | vendor_verdict | 厂商综合状态分 |
| `physical_mental_achievement` | vendor_verdict | 目标/成就 |
| `stress`（组合） | 混合 | = detail + stat，API 须分组返回，不能摊平 |
| `physical_mental`（组合） | 混合 | = detail + stat + achievement |

**F. 呼吸与打鼾（7）**

| metric | 类别 | 说明 |
|---|---|---|
| `breath_rate` | raw_server | 外层时间段 + 嵌套 `dataList[].respiratoryRate` |
| `snore_stat` | vendor_verdict | 鼾声统计 |
| `snore_osa_detail` / `snore_osa_sum` | vendor_verdict | OSA 模型输出 → **医疗化最高危**，只进「厂商报告」折叠区 |
| `snore_env_noise` | raw_server | 环境噪声，非健康值 |
| `snore_feature` | raw_server | 鼾声特征 |
| `sensor_osa` | vendor_verdict | 传感器 OSA 判定 |

**G. 心血管与代谢（6 + 3）**

| metric | 类别 | 说明 |
|---|---|---|
| `ecg` | vendor_verdict（仅元数据） | 波形文件可能需单独下载/解密 → 面板只显示「有一次 ECG 记录 + 厂商结论标签」 |
| `atrial_fibril` | vendor_verdict | 房颤筛查标记，**禁改写为「房颤」** |
| `atrial_fibril_warning` | vendor_verdict | 同上 |
| `cardiovascular` | vendor_verdict | 心血管评估，禁做「心血管年龄」二次解读 |
| `blood_pressure` | raw_server | 需外部血压计录入；无数据即空态 |
| `blood_pressure_stat` | raw_server | 日/期统计 |
| `blood_sugar` | raw_server | 需血糖仪/CGM 录入 |
| `blood_sugar_stat` | raw_server | 统计 |
| `blood_sugar_warning` | raw_server | 设备预警 |

**H. 体重与腕温（4）**

| metric | 类别 | 说明 |
|---|---|---|
| `weight` | raw_server | `weight` 单位克 → `/1000` kg；`measurementTime` 毫秒 |
| `weight_target` | raw_server | 用户目标，非健康建议 |
| `wrist_temperature` | raw_server | **相对基线偏移**，不是体温 |
| `wrist_temperature_detail` | raw_server | 明细，同单位语义 |

**I. 环境 / 营养 / 听力 / 颈椎（7）**

| metric | 类别 | 说明 |
|---|---|---|
| `sunshine` / `sunshine_stat` | vendor_verdict | 日照估算 |
| `vitamin` | vendor_verdict | 维生素相关估算，禁作补充剂建议 |
| `hearing` / `hearing_stat` | raw_server（结果含厂商判定） | 听力测试记录 |
| `cervical_stat` / `cervical_action` | raw_server | 颈椎训练次数/动作，属行为记录 |

**J. 女性生理（3）**

| metric | 类别 | 说明 |
|---|---|---|
| `physiological_cycle` | vendor_verdict（预测） | 默认隐藏，需用户开启 |
| `physiological_symptoms` | raw_server | 自述症状 |
| `ovulation` | vendor_verdict（预测） | 禁止用于避孕/备孕指导 |

**K. 应用与其他（3）**

| metric | 类别 | 说明 |
|---|---|---|
| `app_usage` | raw_server | 日统计；隐私敏感，默认不进健康页 |
| `app_usage_detail` | raw_server | 使用区间；同步极慢 |
| `insight` | vendor_verdict | 厂商「洞察」文本，原样转述 |

> 计数校验：A10 + B6 + C6 + D5 + E7 + F7 + G9 + H4 + I7 + J3 + K3 = **67**（脚本已核对 `ENDPOINTS` 65 + `METRIC_DATASETS` 2）。

---

## 3. 面板信息架构

三层，自上而下：**能不能信 → 是什么 → 厂商说了什么**。顺序刻意如此：先给数据状态，再给数值，最后才是厂商结论。

```
┌ 顶栏：同步状态（auth / token 到期 / 最近同步 / 缺口） ─────────────┐
├ 第一屏：最近一夜 + 今日（4–6 张 KPI 卡） ───────────────────────┤
├ 第二屏：趋势（步数 / 睡眠 / 心率 / 血氧 / 体重 / 压力） ──────────┤
├ 第三屏：数据质量（覆盖率热力图 / 缺口 / 一致性 / 采样密度） ──────┤
└ 折叠区：厂商报告（sleep_index / ecg / 房颤标记 / OSA / 维生素 / 女性）┘
```

### 3.1 健康卡片（第一屏，最多 6 张）

| 卡片 | 主值 | 副信息（必须） | 来源 | 空态文案 |
|---|---|---|---|---|
| 今日步数 | Σ`steps` | 目标完成度（来自 `sport_goal`，无则隐藏）、分段数、最后一段结束时间 | local_aggregate ← `steps_detail` | 「今天还没有步数记录」 |
| 活动消耗 | Σ`calories`/1000 kcal | 与步数同窗口、是否含运动模式分段 | local_aggregate | 「无活动消耗记录」 |
| 心率 | 最近一次 bpm + 时间 | 当日 p05/p50/p95、采样点数 | raw_server | 「今日无心率记录」 |
| 最近一夜睡眠 | 总睡眠 h m | 深/浅/REM/小睡堆叠、入睡–醒来、阶段段数 | local_aggregate ← `sleep_status` | 「没有可用的睡眠阶段记录」 |
| 血氧 | 最近一次 % + 时间 | 近 7 天读数个数、min/median；**不显示「正常/异常」** | raw_server | 「近 7 天无血氧记录」 |
| 体重（可选） | 最新 kg | 距上次变化、14 天斜率（n≥3 才算） | raw_server + local | 「无体重记录（需手动/体脂秤录入）」 |

规则：
- **没有 `as_of` 的数字不上屏**。心率 3 天前的读数不能当「当前心率」。
- 血氧、压力、HRV 这类稀疏指标，主值区必须同时显示「样本数」，否则单点会被误读为常态。
- 体重卡在没有数据时**不要**画空坐标系。

### 3.2 趋势卡片（第二屏，可切 7/30/90 天）

| 卡片 | 图形 | 计算 | 缺口表达 |
|---|---|---|---|
| 步数 | 日柱 + 7 日滑动中位数线 | 日合计；重叠分段取区间并集后累加 | 缺失日留白 + 底部覆盖率条 |
| 睡眠 | 每晚堆叠条（深/浅/REM/小睡）+ 入睡时刻散点 | 见 §4.5 | 无阶段段的夜留白，不插值 |
| 心率 | 日 p50 双线（全天 / 夜间 00–06） | 见 §4.3 | 采样密度 <50% 的日标灰 |
| 血氧 | 散点 + 每日 min/p50 | 见 §4.4 | 单点日不连线 |
| 压力/HRV | 箱线或直方（近 30 天） | 只做分布 | 显示 n |
| 体重 | 散点 + 7 日 EMA + 斜率 | 见 §4.7 | n<3 不画趋势线 |

硬规则：**跨缺口不做插值、不做连线**；缺口 > 1 天，曲线断开并在下方写「09-05 至 10-27 无云端数据」。覆盖率 <60% 或有效天数 <4 时，趋势区显示「样本不足，暂不绘制趋势」，只给原始点。

### 3.3 数据质量卡（第三屏，面板的「诚实层」）

1. **覆盖率热力图**：metric × 最近 30 天，单元格四态：有数据 / 部分（<预期 50%）/ 无记录 / 同步失败。这是唯一该用红/橙的地方（红=请求失败，橙=空）。
2. **缺口清单**：连续 ≥2 天无记录的区间，含天数与最后一条记录时间。
3. **一致性检查**（三对）：
   - `steps_stat.totalSteps` vs Σ`steps_detail.steps`（日）→ `delta_ratio > 5%` 记 `conflict_cross_source`；
   - `sleep_stat.totalSleepTime` vs Σ 阶段（2+3+4）→ 不一致记 `conflict_sleep_sources`；
   - 同一 metric 内重叠区间/重复自然键 → `duplicate_overlap`。
4. **采样密度**：夜间心率应为 ~180 点/小时；`density_ratio < 0.5` 标 `sparse`，只影响「可信度」，不影响数值展示。
5. **单位/异常值体检**（数据层，不是健康层）：`heartRateValue` 不在 20–240、`bloodOxygenSaturationValue` 不在 50–100、`weight` ≤0 或 >300kg、`totalSleepTime` > 24h、阶段段时长 ≤0 或 >6h → 标 `unit_suspect`，计数展示，不自动改数。
6. **口径说明卡**（常驻文字）：时区 Asia/Shanghai、睡眠归属夜规则、心率只取 type 0、小睡不计入总睡眠、腕温是偏移值。

### 3.4 同步状态卡（顶栏 + 详情）

| 字段 | 来源 | 展示 |
|---|---|---|
| 认证就绪 | `os.path.exists(auth.json)` | 就绪 / 缺凭据 |
| token 到期 | 移交文档 2026-11-27 02:13 | 「剩 30 天」，<7 天变橙并提示重跑 `gen_auth.py`（需手机 root + adb） |
| refreshToken | 2026-12-27，**当前脚本未使用** | 只写「未启用自动续期」，不要说「会自动续期」 |
| 每指标最近同步 | `MAX(fetched_at)` | 相对时间 + 绝对时间 |
| 每指标条数 | `COUNT(*)` | 条数（并注明行数≠天数） |
| 最近失败 | 需新增记录（当前无错误日志表） | 错误码 + 中文解释（见下） |
| 来源标记 | `derived_from` | 「HRV 数据由身心健康明细解析」 |
| 耗时 | 实测参考 | `app_usage` 60 天≈63s、`steps_detail` 120 天≈25s，提示「勿并入全量同步」 |

错误码中文映射：`10100` 请求头污染 / `10101` token 失效（需重新生成）/ `10103` 网关拒绝（必须走 curl）/ `23103` 时间范围超限 / `99999` 路径不存在 / `encrypt_key_enc_required` 缺运行时密钥 / `empty_result` 本次无记录（**不是错误**）。

> 关键区分：`errorCode: 0` + 空列表 = **成功但无数据**，必须与「同步失败」用不同视觉和不同文案，否则用户会以为同步坏了。

---

## 4. 算法与单位转换规范

所有计算都在本地聚合层完成，结果必须带 `algorithm.name/version`、`sample_count`、`coverage_ratio`。**任何算法不得引用疾病阈值**。

### 4.1 时间与日界

- 时区固定 `Asia/Shanghai (+08:00)`，显式转换，**不依赖宿主时区**（现有 `_day_rows` 用 `datetime.fromtimestamp()` 取宿主本地时区，跨时区部署会错位）。
- 日界：本地 00:00–24:00。
- 睡眠归属夜：以**醒来日**命名（`night_of = 醒来日期`）；窗口取 `[前一日 12:00, 当日 12:00)` 内结束的阶段段。中午切分避免午睡与夜睡混淆。
- 时间戳统一毫秒；`< 1e11` 的按秒处理（现有 `_payload_date` 已有此兜底）。

### 4.2 步数 / 距离 / 消耗（local_aggregate）

```
segments = 按 startTimestamp 去重后的 steps_detail 记录
并集化：若 [s1,e1) 与 [s2,e2) 重叠，合并区间，保留 steps 较大者（不累加，防重复计数）
daily_steps  = Σ steps(并集后的段)
daily_dist_m = Σ distance(并集后的段)          # 单位：米
daily_kcal   = Σ (calories / 1000)             # calories 为「千分之一千卡」
```

- 单位：`distance` 米 → km（`/1000`，1 位小数）；`calories` `÷1000` → kcal（整数）；`totalDuration`/`totalSleepTime`/`totalDeepSleepTime` 毫秒 → 分钟（`/60000`）。
- 交叉校验：`steps_stat.totalSteps`（原始日汇总）与 `daily_steps` 对照，偏差 >5% 只提示口径差异。
- **不使用**「步数→消耗」再用公式反推 kcal（设备已给 `calories`，自算会与设备冲突）。
- 待实测确认：`steps_detail.distance` / `calories` 的累计口径（分段值 vs 当日累计），若为累计值须改为「取每日最大值」而非求和。

### 4.3 心率（raw + local 统计）

```
有效点：heartRateType == 0（光学心率）
日 p50 = median(values of day)
日 p05/p95 = 5/95 分位（样本 ≥ 20 才输出）
夜间 p50 = median(values where 00:00 ≤ local_time < 06:00)，样本 ≥ 20 才输出
静息参考值（本机估算）= 当日 5 分钟窗口中位数的 p10      # 必须标注「估算，非临床静息心率」
采样密度 = 实际点数 / (夜间小时数 × 180)                 # 180 点/小时为实测值
```

- 单位：bpm，整数。**不显示「心率正常/异常」**，只显示数值与本人近 30 天分布的相对位置（如「低于本人中位数 4 bpm」）。
- 现有 `cloud_analyze` 里的 `good=(60,100)` 属于人群参考区间，**面板不要沿用**；若要展示，必须写成「厂商/通用参考区间 60–100 bpm（非诊断标准）」。

### 4.4 血氧（raw + 分布）

```
有效点：bloodOxygenSaturationType == 1
去重：同一 60 秒内多条取中位数
展示：最近一次值 + 时间；近 7 天 n、min、p50
预警：只统计 blood_oxygen_warning 的记录条数，文案「设备记录了 N 次血氧预警」
```

- 单位 %，整数。**禁止**输出「血氧偏低/缺氧/建议就医」；低于某值时只说「读数低于设备标注阈值 X%（单次测量，受佩戴与末梢循环影响）」，X 取自设备字段，不自造。
- detail 与 origin 采样机制不同 → **不得合并计算**。

### 4.5 睡眠（local_aggregate，核心算法）

输入：`sleep_status` 的阶段段；`sleepState` 映射 `1=清醒 2=深睡 3=REM 4=浅睡 5=小睡`。

```
night_window = [醒来日 12:00 前 24h, 醒来日 12:00)
segments     = 结束时间落在 night_window 内的阶段段，按 startTimestamp 排序
深睡 = Σ dur(state=2)；REM = Σ dur(state=3)；浅睡 = Σ dur(state=4)
小睡 = Σ dur(state=5)            # 不计入 total_sleep
清醒 = Σ dur(state=1)
total_sleep   = 深睡 + REM + 浅睡
in_bed        = 首个段.start → 末个段.end      # 「推断卧床窗口」，不是传感器实测卧床
efficiency    = total_sleep / in_bed           # 见下方护栏
WASO          = 清醒时长（interruptions = 清醒段数）
入睡时刻      = 首个非清醒段的 start（本地 HH:mm）
醒来时刻      = 末个非清醒段的 end
```

护栏与已知偏差（必须写进卡片 caveats）：

- **`efficiency` 唯一性检查**：若 `清醒 = 0` 且 `total_sleep == in_bed`（10-28 实测就是 337 min = 337 min，效率 100%），说明该夜没有记录清醒片段 → `efficiency` 置 `null`，文案「未记录清醒片段，不计算睡眠效率」。100% 效率上屏就是误导。
- 小睡必须单列，不并入总睡眠（否则与厂商 `totalSleepTime` 口径打架）。
- `fallAsleep`/`sleepOut` 是 HHMM 整数（`2242` → 22:42，`//100`、`%100`）；来自 `sleep_stat`/`sleep`，而这两个源不可靠 → 睡眠卡**优先用阶段段推导**，HHMM 仅在可信时作为校验。
- 跨午夜、跨日界：按段 `endTimestamp` 归属，不按 `date` 字段。
- 入睡时刻规律性用**圆周统计**（23:50 与 00:10 只差 20 分钟，不能线性平均）：

```
θᵢ = 2π × (分钟数 mod 1440) / 1440
R  = |Σe^{iθᵢ}| / n          # 集中度 0–1
平均入睡时刻 = atan2(Σsinθ, Σcosθ) → 分钟
圆周标准差(分钟) = sqrt(-2 ln R) × 1440 / (2π)
规律性 = clamp(1 - 圆周SD / 90, 0, 1)   # 仅在 n ≥ 5 且 R ≥ 0.3 时输出，否则「作息分散」
```

- 睡眠中点（midpoint）= (入睡 + 醒来)/2，用于观察作息漂移，不做「社会时差」诊断表述。

### 4.6 HRV（谨慎，最小化）

- 默认**只展示厂商给的 `heartRateVarValue`**（单位通常 ms，待实测确认），并标 `vendor_scale`。
- 只有当 `sleep_rr` 有连续 RR 序列且有效搏动 ≥ 50 时，才可自算：

```
rMSSD = sqrt( mean( (RR[i+1] - RR[i])² ) )   # 单位 ms
```
  且必须过滤：RR 在 300–2000 ms、剔除相邻差 > 200 ms 的异位搏动、缺口处不跨段计算。`n < 50` 不输出。
- **禁止**用不规则采样点算 SDNN，**禁止**把 HRV 解释为「压力大/恢复差/自律神经失调」。

### 4.7 体重（raw + 轻量趋势）

```
kg = weight / 1000
EMA(t) = α·x(t) + (1-α)·EMA(t-1)，α = 2/(7+1) = 0.25，缺口日不更新
斜率 = OLS(x=天序号, y=kg)，要求 n ≥ 3 且跨度 ≥ 7 天
变化速率 = 斜率 × 7  (kg/周，1 位小数)
```

- 单位 kg，1 位小数；显示「较上次 ±0.4 kg，14 天趋势 -0.2 kg/周」。
- 不做 BMI→健康判读；若展示 BMI 需身高（当前无该字段）→ **不展示 BMI**。

### 4.8 压力（raw，分布为主）

- 0–100 量表值属厂商定义，面板只做：近 30 天 n、min/p50/p95、按小时的分布。
- 文案：「设备压力值（0–100，OPPO 量表）中位数 42，样本 31 点」。
- **禁止**「压力偏高/需要放松/焦虑风险」。若厂商自带等级字段，注明「厂商等级」。

### 4.9 周期比较（替换现有 `cloud_analyze` 的 ⚠️ 逻辑）

```
本期 vs 上期：仅当两期 coverage ≥ 0.6 且有效天数 ≥ 4 才输出 delta
指标用中位数（非均值）；同时输出 n
相对基线：与本人近 30/90 天中位数比较，差值 + 分位位置
```

- 现有 `cloud_analyze` 的 `good=(60,100)` / `(95,100)` / `(420,600)` / `深睡(60,180)` / `REM(90,180)` / `warn=6000` **不建议进面板**：这些是把人群参考区间当个人判定，`6000 步` 更是无出处的目标值，且用 ⚠️ 表情等于给出结论。
- 替代做法：灰色参考带 + 文案「本人近 30 天范围 5,200–9,800 步」，超出时写「低于本人近期范围」，**不写「不足」**。

### 4.10 单位换算总表

| 字段 | 原始单位 | 换算 | 展示 |
|---|---|---|---|
| `totalDistance`, `distance` | 米 | `/1000` | km，1 位小数 |
| `totalCalories`, `calories` | 千分之一千卡 | `/1000` | kcal，整数 |
| `totalDuration`, `totalSleepTime`, `totalDeepSleepTime`, 段时长 | 毫秒 | `/60000` | 分钟 / 「Xh Ym」 |
| `fallAsleep`, `sleepOut` | HHMM 整数 | `//100`, `%100` | HH:mm |
| `weight` | 克 | `/1000` | kg，1 位 |
| `wrist_temperature*` | 相对基线偏移 | 不换算 | `+0.3 °C`（带符号，注明偏移） |
| 心率 | bpm | — | 整数 |
| 血氧 | % | — | 整数 |
| `rrInterval*` | 毫秒 | — | ms |
| 压力 | 0–100 厂商量表 | 不归一化 | 整数 + 「OPPO 量表」 |
| `sleepState` | 1–5 | 映射 | 清醒/深睡/REM/浅睡/小睡 |

---

## 5. 医疗化红线与文案规范

### 5.1 禁止出现的表述 → 替代表述

| 禁用 | 替代 |
|---|---|
| 房颤 / 心律失常 | 「设备房颤筛查标记（OPPO 算法，未经医疗复核）」 |
| 缺氧 / 低氧血症 / 呼吸暂停 | 「设备记录的读数/模型输出」；不加疾病名 |
| 睡眠质量差 / 失眠 | 「总睡眠 5h37m，低于本人近 30 天中位数」 |
| 压力过大 / 焦虑 / 抑郁 | 「设备压力值 62（0–100 厂商量表）」 |
| 静息心率 | 「夜间心率中位数」/「静息参考值（本机估算）」 |
| 健康评分 / 身体年龄 / 心血管年龄 | 不展示，不合成 |
| 异常 / 不正常 / 危险 | 「偏离本人基线」/「超出厂商参考区间」 |
| 建议就医 / 需要治疗 / 建议补充 | 删除（厂商原文含建议时，标注「以下为 OPPO 应用原文」） |
| 心率变异性低 = 恢复差 | 不解释，只给数值与 n |

### 5.2 视觉语义

- **颜色只表达数据状态，不表达健康判断**：灰=无数据、黄=样本稀疏、红=同步失败；健康数值一律中性色。禁止「越红越危险」的色阶。
- 每个数字旁必须有：时间（`as_of`）、来源标签（原始/本机计算/厂商）、样本量（稀疏指标）。
- 厂商结论区默认**折叠**，展开后顶部固定一行：「以下为 OPPO 应用算法输出，本面板未复核、不构成诊断」。

### 5.3 免责声明（API 与 UI 共用，两档）

```json
{
  "disclaimer": {
    "id": "non_diagnostic_v1",
    "level": "info",
    "short": "非医疗器械，不提供诊断或治疗建议。",
    "full": "本面板仅展示 OPPO 健康设备与云端返回的记录，以及基于这些记录的可复现统计。它不是医疗器械，不提供诊断、治疗、用药或就医建议，不能替代专业医疗人员。设备读数受佩戴方式、传感器状态与厂商算法版本影响，可能存在偏差；标注为厂商算法的评分、预警与筛查结果均为 OPPO 应用输出，本面板未做医疗复核。若身体持续不适或对数值有疑问，请咨询专业医疗人员。",
    "placement": ["api_envelope", "panel_footer", "vendor_section_header"]
  }
}
```

第二档（仅厂商区）：`{"id":"vendor_verbatim_v1","level":"warning","short":"厂商算法输出，原样转述，未复核。"}`

绝对禁止在无厂商数据时生成「你的健康风险较低/较高」这类反向安慰或恐吓文案——空数据就说空数据。

---

## 6. API 设计建议

前置约束（来自 §1.1）：现有缓存以「自然键」为主键、无日期索引、`cloud_query` 有 `LIMIT 100`。因此**面板 API 需要一层独立的只读聚合服务**，直读 SQLite 并在内存中按 `payload` 时间戳分桶；不要复用 `cloud_query` 做序列接口。

Base：`/api/v1`，全部 `GET`（同步写操作单独异步化）。响应统一 UTF-8 JSON，时间 ISO8601 带 `+08:00`。

### 6.1 通用信封

```json
{
  "api_version": "1.0",
  "generated_at": "2026-10-28T10:12:00+08:00",
  "timezone": "Asia/Shanghai",
  "window": {"from": "2026-10-22", "to": "2026-10-28", "days": 7},
  "data_status": "partial",
  "coverage": {"days_with_data": 3, "days_in_window": 7, "ratio": 0.43},
  "disclaimer": {"id": "non_diagnostic_v1", "level": "info", "short": "非医疗器械，不提供诊断或治疗建议。"},
  "data": {}
}
```

`data_status` 枚举：`ok` | `partial` | `stale`（最后同步 > 48h）| `empty`（窗口内无记录）| `error`（取数失败）。
`empty` 必须带 `reason`，且 HTTP 仍为 200（空数据不是异常）。

### 6.2 卡片对象（所有健康卡统一形状）

```json
{
  "id": "sleep.last_night",
  "section": "health",
  "title": "最近一夜睡眠",
  "value": 337,
  "unit": "min",
  "display": "5 小时 37 分",
  "as_of": "2026-10-28T08:19:00+08:00",
  "period": {"from": "2026-10-27T12:00:00+08:00", "to": "2026-10-28T12:00:00+08:00"},
  "source_kind": "local_aggregate",
  "source_metrics": ["sleep_status"],
  "algorithm": {
    "name": "sleep_stage_sum",
    "version": "1.0",
    "note": "按醒来日归属；总睡眠=深睡+REM+浅睡，小睡单列"
  },
  "quality": {
    "sample_count": 16,
    "coverage_ratio": 0.95,
    "confidence": "medium",
    "flags": ["no_wake_segments"],
    "efficiency": null
  },
  "breakdown": {"deep_min": 60, "rem_min": 64, "light_min": 211, "nap_min": 2, "awake_min": 0},
  "time_range": {"sleep_start": "02:42", "sleep_end": "08:19"},
  "trend": {"baseline_days": 30, "median_min": 402, "delta_min": -65, "position": "below_personal_range"},
  "caveats": ["该夜未记录清醒片段，不计算睡眠效率", "睡眠数据来自设备阶段记录，非多导睡眠监测"],
  "empty_state": null
}
```

字段硬约束：
- `source_kind ∈ {raw_server, local_aggregate, vendor_verdict, unavailable}`。
- `vendor_verdict` 必带 `vendor: {app:"OPPO 健康", field:"...", verbatim: true}`。
- `local_aggregate` 必带 `algorithm.name` + `quality.sample_count`。
- `unavailable` 必带 `reason`（`sport_file` → `encrypt_key_enc_required`）。
- `confidence ∈ {high, medium, low}`，由 coverage 与 sample_count 判定，不允许无依据写 high。

### 6.3 端点

#### `GET /api/v1/overview?days=7`

一次返回顶栏 + 第一屏 + 趋势摘要 + 质量摘要，减少首屏请求数。

```json
{
  "api_version": "1.0",
  "generated_at": "2026-10-28T10:12:00+08:00",
  "timezone": "Asia/Shanghai",
  "window": {"from": "2026-10-22", "to": "2026-10-28", "days": 7},
  "data_status": "partial",
  "coverage": {"days_with_data": 3, "days_in_window": 7, "ratio": 0.43},
  "data": {
    "sync": {
      "auth_ready": true,
      "token_expires_at": "2026-11-27T02:13:00+08:00",
      "days_to_expiry": 30,
      "refresh_token": {"expires_at": "2026-12-27T02:13:00+08:00", "auto_renew": false},
      "last_sync_at": "2026-10-28T09:58:00+08:00",
      "state": "ok",
      "warnings": []
    },
    "cards": [
      {"id": "steps.today", "title": "今日步数", "value": 4820, "unit": "步", "display": "4,820",
       "as_of": "2026-10-28T09:40:00+08:00", "source_kind": "local_aggregate",
       "source_metrics": ["steps_detail"],
       "algorithm": {"name": "daily_segment_sum", "version": "1.0"},
       "quality": {"sample_count": 5, "coverage_ratio": 0.62, "confidence": "low",
                   "flags": ["few_segments"]},
       "trend": {"baseline_days": 30, "median": 6120, "delta": -1300},
       "caveats": ["今日仅 5 段记录，可能未包含全天活动"]},
      {"id": "spo2.latest", "title": "最近血氧", "value": 97, "unit": "%", "display": "97%",
       "as_of": "2026-09-02T23:10:00+08:00", "source_kind": "raw_server",
       "source_metrics": ["blood_oxygen_detail"],
       "quality": {"sample_count": 131, "coverage_ratio": 0.0, "confidence": "low",
                   "flags": ["stale", "single_day_only"]},
       "trend": null,
       "caveats": ["数据来自 2026-09-02，距今天数较多"]}
    ],
    "quality_summary": {
      "overall": "partial",
      "metrics_with_data": 13,
      "metrics_total_tracked": 20,
      "largest_gap": {"from": "2026-09-05", "to": "2026-10-27", "days": 53},
      "flags": ["long_gap", "sleep_stat_unreliable", "hrv_derived_from_embedded"]
    },
    "disclaimer": {"id": "non_diagnostic_v1", "level": "info"}
  }
}
```

> 注意示例里的血氧卡：`as_of` 是 9 月、`coverage_ratio`=0，仍然可以展示「最近读数」，但必须带 `stale` 与 caveat。**不要**把它画进「今日概览」的数值区当当前值——建议设 `as_of > 7d` 时降级为文字行。

#### `GET /api/v1/metrics/catalog`

67 项全量目录，面板用它决定「这张卡能不能画」「标签写什么」。也是唯一允许前端写死的地方。

```json
{
  "data": {
    "total": 67,
    "groups": [
      {"key": "activity", "label": "运动与活动", "count": 10},
      {"key": "heart", "label": "心率与 HRV", "count": 6},
      {"key": "sleep", "label": "睡眠", "count": 6},
      {"key": "spo2", "label": "血氧", "count": 5},
      {"key": "stress", "label": "压力与身心健康", "count": 7},
      {"key": "breath", "label": "呼吸与打鼾", "count": 7},
      {"key": "cardio_metabolic", "label": "心血管与代谢", "count": 9},
      {"key": "body", "label": "体重与腕温", "count": 4},
      {"key": "environment", "label": "环境/营养/听力/颈椎", "count": 7},
      {"key": "cycle", "label": "女性生理", "count": 3},
      {"key": "other", "label": "应用与其他", "count": 3}
    ],
    "metrics": [
      {"metric": "sleep_status", "group": "sleep", "source_kind": "raw_server",
       "time_field": "startTimestamp", "natural_key": "startTimestamp",
       "cadence": "per_stage_segment", "panel_use": "primary_sleep_source",
       "unit": "ms", "display": false, "notes": ["sleepState 1..5"]},
      {"metric": "sleep_stat", "group": "sleep", "source_kind": "raw_server",
       "time_field": "date", "natural_key": "date",
       "cadence": "daily", "panel_use": "cross_check_only",
       "unit": "ms", "display": false,
       "quality_notes": ["4 个月仅 9 天", "评分存在 null/0"]},
      {"metric": "sleep_index", "group": "sleep", "source_kind": "vendor_verdict",
       "time_field": "date", "natural_key": "date", "cadence": "daily",
       "panel_use": "vendor_section", "unit": "mixed", "display": true},
      {"metric": "sport_file", "group": "activity", "source_kind": "unavailable",
       "reason": "encrypt_key_enc_required", "panel_use": "none", "display": false},
      {"metric": "hrv", "group": "heart", "source_kind": "raw_server",
       "derived_from": "physical_mental_detail", "panel_use": "distribution_only",
       "unit": "ms", "display": true}
    ],
    "unit_rules": [
      {"field": "totalCalories", "factor": 0.001, "from": "milli_kcal", "to": "kcal"},
      {"field": "totalDistance", "factor": 0.001, "from": "m", "to": "km"},
      {"field": "totalSleepTime", "factor": 0.0000166667, "from": "ms", "to": "min"},
      {"field": "weight", "factor": 0.001, "from": "g", "to": "kg"}
    ],
    "text_rules": {
      "forbidden_terms": ["房颤", "缺氧", "睡眠呼吸暂停", "焦虑", "抑郁", "静息心率", "健康评分"],
      "require_as_of": true,
      "require_sample_count_for_sparse": true
    }
  }
}
```

（示例只列数条，实际返回 67 条完整记录；`total` 与数组长度必须一致，前端可断言。）

#### `GET /api/v1/metrics/{metric}/series?from&to&bucket=day&agg=sum|median`

```json
{
  "data": {
    "metric": "steps_detail",
    "bucket": "day",
    "agg": "sum",
    "unit": "步",
    "source_kind": "local_aggregate",
    "algorithm": {"name": "daily_segment_sum", "version": "1.0"},
    "points": [
      {"date": "2026-10-26", "value": 7210, "n": 12, "coverage": 1.0, "flags": []},
      {"date": "2026-10-27", "value": null, "n": 0, "coverage": 0.0, "flags": ["no_data"]},
      {"date": "2026-10-28", "value": 4820, "n": 5, "coverage": 0.62, "flags": ["partial_day"]}
    ],
    "summary": {"days": 3, "days_with_data": 2, "median": 6015, "min": 4820, "max": 7210}
  }
}
```

- 缺失**必须显式返回 `null` 点**，不要省略日期（否则前端会把 26 日和 28 日连成一条线）。
- `agg=sum` 只允许累计型（steps/calories/distance/duration）；瞬时型默认 `median`，传 `sum` 返回 422。
- 分页：`limit` 默认 500、上限 2000，`cursor` 为 `(date, natural_key)` 复合游标——因为现有缓存主键就是这个形状。

#### `GET /api/v1/sleep/nights?limit=14`

```json
{
  "data": {
    "unit": "min",
    "nights": [
      {"night_of": "2026-10-28", "sleep_start": "02:42", "sleep_end": "08:19",
       "total_min": 337, "deep_min": 60, "rem_min": 64, "light_min": 211, "nap_min": 2,
       "awake_min": 0, "in_bed_min": 337, "efficiency": null,
       "segments": 16, "source_kind": "local_aggregate",
       "flags": ["no_wake_segments"],
       "caveats": ["未记录清醒片段，不计算睡眠效率"]}
    ],
    "regularity": {
      "n_nights": 12, "bedtime_mean": "23:58", "circular_sd_min": 48,
      "concentration_r": 0.71, "score": 0.47, "label": "作息波动中等"
    }
  }
}
```

`label` 用「稳定/波动中等/分散」三档，**禁止**「作息紊乱」「睡眠障碍」。

#### `GET /api/v1/quality/report?days=30`

```json
{
  "data": {
    "window": {"from": "2026-09-29", "to": "2026-10-28"},
    "heatmap": [
      {"metric": "steps_detail", "cells": [
        {"date": "2026-10-26", "state": "ok", "n": 12},
        {"date": "2026-10-27", "state": "missing", "n": 0}
      ]}
    ],
    "gaps": [
      {"metric": "steps_detail", "from": "2026-09-05", "to": "2026-10-27", "days": 53,
       "note": "云端无数据，链路不回填"}
    ],
    "consistency": [
      {"check": "steps_stat_vs_detail", "date": "2026-10-28",
       "stat_value": 5100, "detail_value": 4820, "delta_ratio": 0.055,
       "state": "warn", "action": "仅提示口径差异"}
    ],
    "sampling": [
      {"metric": "heart_rate_detail", "date": "2026-10-28",
       "night_points": 1080, "expected": 1080, "density_ratio": 1.0, "state": "ok"}
    ],
    "anomalies": [
      {"metric": "heart_rate_detail", "date": "2026-10-20", "field": "heartRateValue",
       "value": 0, "rule": "outside_20_240", "count": 1}
    ],
    "notes": ["行数为缓存记录数，不等于天数", "心率仅统计 heartRateType=0"]
  }
}
```

`state` 枚举：`ok` | `sparse` | `missing` | `stale` | `warn` | `error`。`action` 只描述数据处理动作，不描述健康动作。

#### `GET /api/v1/sync/status`

```json
{
  "data": {
    "auth": {"ready": true, "token_expires_at": "2026-11-27T02:13:00+08:00",
             "days_to_expiry": 30, "auto_renew": false,
             "renew_hint": "到期需在手机 root + adb 在线时重跑 gen_auth.py"},
    "metrics": [
      {"metric": "steps_detail", "rows": 3626, "last_fetched_at": "2026-10-28T09:58:00+08:00",
       "last_record_date": "2026-10-28", "error": null, "duration_ms": 25000,
       "derived_from": null},
      {"metric": "hrv", "rows": 31, "last_fetched_at": "2026-10-28T09:59:00+08:00",
       "last_record_date": "2026-10-28", "error": null, "duration_ms": 1200,
       "derived_from": "physical_mental_detail"},
      {"metric": "sport_file", "rows": 0, "last_fetched_at": null,
       "error": {"code": "encrypt_key_enc_required", "message": "缺少运行时密钥，轨迹不可用"}}
    ],
    "running_job": null
  }
}
```

#### `POST /api/v1/sync`（可选，异步）

```json
{"request": {"metrics": ["steps_detail", "sleep_status"], "days": 7},
 "response": {"job_id": "sync_20261028_1012", "state": "queued",
              "note": "单指标一次不超过 7 天窗口；app_usage 慢，建议单独触发"}}
```

#### `GET /api/v1/vendor-reports?type=sleep_index`

```json
{
  "data": {
    "section": "vendor",
    "header": "以下为 OPPO 应用算法输出，本面板未复核，不构成诊断。",
    "disclaimer": {"id": "vendor_verbatim_v1", "level": "warning"},
    "items": [
      {"type": "sleep_index", "date": "2026-10-28",
       "fields": {"恢复率": 101, "平均睡眠血氧": "97%", "基础睡眠心率": "56 bpm",
                  "睡眠心率区间": "47–78", "warningNumber": 1},
       "as_of": "2026-10-28T09:00:00+08:00",
       "vendor": {"app": "OPPO 健康", "verbatim": true},
       "display_note": "数值原样转述；区间与告警含义以 OPPO 应用说明为准"}
    ],
    "unavailable": [
      {"type": "sport_file", "reason": "encrypt_key_enc_required"},
      {"type": "ecg_waveform", "reason": "波形文件需单独下载/解密，当前仅元数据"}
    ]
  }
}
```

### 6.4 错误与空态契约

| HTTP | 场景 | body |
|---|---|---|
| 200 | 成功 / 窗口内无数据 | 信封 + `data_status: "empty"` + `data.reason` |
| 400 | 参数非法（`agg=sum` 用于瞬时指标、日期越界） | `{"error":{"code":"invalid_agg","message":"..."}}` |
| 404 | 未知 metric | `{"error":{"code":"unknown_metric","available_count":67}}` |
| 409 | 已有同步任务 | `{"error":{"code":"sync_in_progress","job_id":"..."}}` |
| 503 | 缓存库不可读 | `{"error":{"code":"cache_unavailable"}}` |

**不要**用 404 表示「没有数据」——这是面板最常见的误用，会让「缺数据」看起来像「功能坏了」。

---

## 7. 现有实现的风险点（记录，不在本次修改）

| 位置 | 问题 | 对面板的影响 |
|---|---|---|
| `_cache()` | 主键 `(metric, date)` 中 `date` 是自然键；统计类与明细类语义不一 | 无法 SQL 按日期过滤；行数≠天数 |
| `cloud_query()` | 无日期时 `LIMIT 100` | 直接做序列会只剩最近半天心率 |
| `_day_rows()` | `datetime.fromtimestamp()` 用宿主时区 | 非 +08:00 主机上日界错位 |
| `cloud_analyze()` | 硬编码 `good=(60,100)/(95,100)/(420,600)`、`warn=6000`、深睡/REM 区间，并用 ⚠️ | **本方案明确不沿用**；这是面板医疗化的主要来源 |
| `cloud_analyze()` 睡眠 | 用 `sleep` 日汇总、同日取 `max(totalSleepTime)` | 与「用 `sleep_status` 自算」结论可能冲突；`sleep_stat` 评分不可靠 |
| `cloud_analyze()` 步数 | `daily=True` 对段值求和，未处理重叠 | 重叠段会虚高 |
| `cloud_status()` | 只返回计数，无 `fetched_at`/窗口 | 不够支撑同步卡，需扩展只读查询 |
| 全流程 | 无同步日志/失败记录表 | 「最近失败原因」只能靠实时调用或新增表 |

这些都不需要现在改代码；聚合服务可以在**只读**前提下自行解析 `payload` 绕开上述限制（除时区需显式处理）。

---

## 8. 落地顺序与验收清单

**P0（面板骨架，1–2 天）**
1. `metrics/catalog` + 67 项分级（本文 §2 直接可用作数据）。
2. `overview` 的 `sync` 段 + `quality_summary`：先让「数据可信度」上屏。
3. 4 张 KPI：今日步数、最近心率、最近一夜睡眠、最近血氧（带 stale 降级）。

**P1（趋势与质量）**
4. `metrics/{m}/series` + `sleep/nights` + `quality/report`。
5. 缺口/覆盖率可视化；跨缺口断线。

**P2（厂商区）**
6. `vendor-reports` 折叠区，含免责声明与 unavailable 列表。

**验收（逐条可测）**
- [ ] 任一数字都能追到 `source_kind` + `source_metrics` + `as_of`。
- [ ] 窗口无数据时返回 `data_status: "empty"` + `reason`，HTTP 200，UI 显示空态而非 0。
- [ ] 缺口处曲线断开，且页面上出现缺口起止日期。
- [ ] 睡眠效率在该夜 `awake_min=0` 时为 `null` 并带 caveat（用 10-28 那条验证：337/337）。
- [ ] `sport_file` 显示「不可用 + encrypt_key_enc_required」，不显示 0 条轨迹。
- [ ] 搜索 UI 文案不含 §5.1 禁用词（可脚本扫描）。
- [ ] 每个厂商数值都带 `vendor.verbatim=true` 与折叠区表头。
- [ ] 血氧/压力/HRV 卡片同时出现 `sample_count`。
- [ ] 空数据时不出现任何「风险低/健康良好」类反向结论。
- [ ] 全部时间戳为 `+08:00`，跨时区部署不改数字。

---

## 9. 与部署环境 / PRODUCT.md 的对齐

目标环境是腾讯云 Ubuntu + 服务端渲染/静态面板 + MCP bearer token，这几条会反过来约束 API：

1. **时区**：Ubuntu 主机默认 UTC。聚合层必须显式 `Asia/Shanghai` 转换，否则日界、睡眠归属夜整体偏 8 小时（现有 `_day_rows` 的宿主时区写法在 UTC 上必错）。
2. **鉴权**：`/api/v1/*` 全部要求 `Authorization: Bearer <token>`，与 MCP 共用同一 token；未认证时返回 401 且**不返回任何健康字段**（连 metric 名和条数都不给）。
3. **首屏渲染**：服务端渲染由同一聚合层生成 JSON 再套模板，不要在前端重复实现统计（否则单位换算和覆盖率口径会分叉）。前端只做格式化与图表。
4. **不落敏感数据到浏览器**：HTML 源码、`localStorage`、URL query、日志里都不放健康数值；卡片 ID 可以出现在 URL，数值不行。
5. **缓存**：健康 JSON 一律 `Cache-Control: no-store`；不要放 CDN/反向代理公共缓存。
6. **空态优先**：静态面板容易把「没拉到」渲染成 0，模板层必须区分 `empty` / `error` / `zero`（真实为 0 的极少，只有步数可能）。
7. **同步按钮**：服务端触发 `cloud_sync` 时限制 `days ≤ 7`、`app_usage` 单独排队，并把耗时回显（实测 60 天 63 秒），避免请求超时被误判为失败。

## 10. 附：一页速查

**原始数据**：`steps_detail`、`steps_stat`、`heart_rate*`、`sleep_status`、`sleep_stat`（仅校验）、`blood_oxygen*`、`weight`、`wrist_temperature*`、`breath_rate`、`stress_detail`、`blood_pressure*`、`blood_sugar*`、`sport_stat`、`sedentary`、`hearing*`、`cervical*`、`app_usage*`。

**本地计算**：日步数/距离/kcal、睡眠分期与总睡眠、夜间心率中位数、静息参考值（估算）、血氧分布、体重 EMA 与斜率、覆盖率/缺口/一致性/采样密度、周期比较 delta。

**厂商结论（原样）**：`sleep_index`、`sleep_advice`、`ecg`、`atrial_fibril*`、`cardiovascular`、`snore_osa_*`、`sensor_osa`、`snore_stat`、`physical_mental_stat`、`physical_mental_achievement`、`vitamin`、`sunshine*`、`ovulation`、`physiological_cycle`、`insight`。

**不可用**：`sport_file`（缺 `encryptKeyEnc`）、`sport_record`（仅索引）、ECG 波形（需单独下载/解密）、`aihealth-api-cn` 系列（未接入）。

**三条铁律**：没有 `as_of` 的数字不上屏；跨缺口不插值；厂商结论不重算、不汇总、不改写。
