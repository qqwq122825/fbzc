# 注册协议研究规范（离线证据版）

修订日期：2026-11-21（Asia/Shanghai）。本文件为原协议的研究版副本，原文件保持不变。

## 1. 范围与边界

目标是整理自有、获授权样本的请求/响应结构、状态语义和证据来源，不建设账号生产流水线。
本修订仅完善文档契约，不运行注册客户端，不发送平台请求，不采购号源，不更改设备或网络身份。
不提供批量注册、规避风控、人脸/证件验证绕过、凭证重放或账号分发能力。

本规范尚未接入现有运行时：以下停止规则、字段约束是研究验收要求，不代表旧代码已经实施。

## 2. 证据分级与已知状态

证据标签：`recorded` 表示本地材料记载；`inferred` 表示推测；`unknown` 表示证据不足。
本次只阅读文档与源码，未复验历史原始抓包，也未联网验证现行平台行为。
材料中的具体标识、密钥版本和历史接口编号不能当作当前有效值。

| 对象 | 本地依据 | 可报告的结果 | 不可推出的结论 |
|---|---|---|---|
| Legacy 表单 | HANDOFF.md §2.1、§2.2 | 交接材料记载曾获得重定向及会话 Cookie | 当前仍可用、邮箱已确认、可规模化 |
| Web GraphQL 提交 | HANDOFF.md §2.1、§4 | 材料记载入参类型错误，尚未解开 | 查询成功等于提交成功 |
| Android 提交 | HANDOFF.md §2.1 | 仅静态分析 | 路径稳定、成功率更高或实现完成 |
| Bloks 确认 | BLOKS_CONFIRM.md §2、§4、§5 | 错误表现发生变化；复核仍为待确认 | HTTP 200 或错误码消失等于确认完成 |
| 当前分类实现 | core/client.py 的 `_classify` | 看到 UID/会话字段即可返回 ACTIVE | ACTIVE 已具有独立的邮箱确认依据 |
| 当前日志实现 | core/scheduler.py 的 `Task.as_row`、`Scheduler._log` | 源码存在将密码、联系方式、Cookie 等写盘的路径 | 运行日志已经脱敏或可公开 |

注意：HTTP 500 可能来自解析、鉴权、中间件或其他服务端错误；没有服务端证据不能唯一归因于 params 层级。
一次更换出口后出现 checkpoint 只能说明该样本的时序相关性，不能证明唯一因果或推导权重。
原稿中的固定成功率、环境配额、等待时长和“必定”类判断没有本次实验支持，不纳入本规范。

## 3. 结果判定：传输、业务、确认三层分离

先记录 `transport_status`（可为 null）、`application_error`（可为 null）、`parse_status`，再给出研究状态。
结构未识别、HTML 错误页、JSON 解码失败、缺少关键证据或互相冲突的证据均不得默认成功。

| 观测 | 研究状态 | 约束 |
|---|---|---|
| 明确出现平台要求的安全验证 | CHECKPOINT | 优先于成功字段；停止自动操作，交由本人通过官方流程处理 |
| 明确限流信号，例如 HTTP 429 | RATE_LIMITED | 停止自动提交，保留脱敏错误及 Retry-After；不自动换身份重试 |
| 明确的业务拒绝，且无更高优先级信号 | REJECTED | 只记录服务端给出的错误，不臆测号段或 IP 黑名单 |
| 明确确认完成证据，且有相应已认证会话证据 | CONFIRMED | 两项证据均须可定位；它是本研究标签，不等于平台永久可用保证 |
| 明确要求邮箱/短信确认，尚无完成证据 | PENDING_CONFIRM | 与创建响应、会话建立分开记录 |
| 只有 200、302、Cookie、UID，或确认结果不明 | UNKNOWN | 这些信息本身不足以证明确认完成 |
| 提交后超时或连接中断 | UNKNOWN | 服务端可能已执行，不得重新提交来“探测” |

匹配优先级在下方契约中声明；CONFIRMED 必须满足双证据前提，而不是仅按 HTTP 状态推导。
`ok`、`ACTIVE` 等现有字段不得直接映射为本规范的 CONFIRMED；调用方需补齐证据才能转换。
OBSERVED 是读入一条已有样本后的临时节点，不产生网络动作。STOPPED 结束当前研究任务。
UNKNOWN 在本次自动流程中只允许停止；后续人工补证应创建新证据记录并引用原 evidence_id，不覆盖原结论。

## 4. 会话与字段生命周期

- 会话令牌、Cookie、隐藏字段和组件运行时标识仅描述其来源与生命周期，不在报告中保存真实值。
- 来源分为 `server_observed`、`client_runtime_observed`、`derived_unverified`、`unknown`；推导值不得伪装为抓包值。
- 缺失字段记 null 与原因，不能从别的账号/会话/历史缓存静默补齐，也不猜造运行时字段。
- 密码加密材料应记录算法版本、公钥标识及来源证据；本次未审计算法正确性或缓存有效期，不能承诺永久有效。
- 每份证据绑定 source_sha256、来源引用、可选捕获时间与本次观察时间。未知捕获时间填 null，不用文件修改时间冒充。
- 同一来源的只读分析可复用 evidence_id；新的采集建立新记录，避免把不同时间、不同会话拼成一次成功链路。

## 5. 失败与停止

遇到 CHECKPOINT、RATE_LIMITED、REJECTED、UNKNOWN，停止当前自动流程，不切换代理、设备、账号或联系方式规避响应。
涉及身份核验由本人在官方界面完成；本规范不自动处理核验素材。
只允许离线分析器重新读取同一脱敏样本，不允许以“重试解析”为名重新发送提交请求。
原始样本不明或解析器异常时应返回 UNKNOWN，并记录异常类型；禁止日志里附带原始响应正文或凭证。

## 6. 敏感信息与证据记录

研究记录只留白名单元数据，不保存密码、验证码、完整邮箱/手机号、账号 ID、Cookie、Authorization、代理账密、会话令牌或 reg_info 原文。
用随机不透明 evidence_id 关联记录，不直接对低熵手机号或验证码做裸哈希。凭证不进入 Markdown、diff、Git 或错误堆栈。
source_sha256 用于完整性对照，不是脱敏方法；公开证据先完成脱敏，再计算公开副本的哈希。
原始样本如确有必要保存，应与报告分离、限制访问，并约定到期删除；本修订不读取或复制 data/ 中的运行凭证。

建议记录形状：evidence_id、source_ref、source_sha256、captured_at、observed_at、transport_status、application_error、parse_status、state、confidence、redacted。
时间使用带时区的 ISO 8601；application_error 仅留审查后的代码，不保存可能回显个人信息的自由文本。

## 7. 机器可读研究契约

以下 JSON 是离线文档的一致性契约，不是平台注册请求模板，也不是已接入运行时的控制配置。
transition_cases 仅验证允许/禁止的状态边，不能替代对真实响应的分类测试。

```json
{
  "schema_version": 1,
  "mode": "offline_observation_only",
  "network_allowed": false,
  "automatic_submission_retry": false,
  "runtime_enforced": false,
  "sensitive_values_allowed": false,
  "states": [
    "NEW",
    "OBSERVED",
    "PENDING_CONFIRM",
    "CONFIRMED",
    "CHECKPOINT",
    "RATE_LIMITED",
    "REJECTED",
    "UNKNOWN",
    "STOPPED"
  ],
  "transitions": {
    "NEW": [
      "OBSERVED",
      "STOPPED"
    ],
    "OBSERVED": [
      "PENDING_CONFIRM",
      "CONFIRMED",
      "CHECKPOINT",
      "RATE_LIMITED",
      "REJECTED",
      "UNKNOWN",
      "STOPPED"
    ],
    "PENDING_CONFIRM": [
      "CONFIRMED",
      "CHECKPOINT",
      "RATE_LIMITED",
      "REJECTED",
      "UNKNOWN",
      "STOPPED"
    ],
    "CONFIRMED": [
      "STOPPED"
    ],
    "CHECKPOINT": [
      "STOPPED"
    ],
    "RATE_LIMITED": [
      "STOPPED"
    ],
    "REJECTED": [
      "STOPPED"
    ],
    "UNKNOWN": [
      "STOPPED"
    ],
    "STOPPED": []
  },
  "classification_priority": [
    "CHECKPOINT",
    "RATE_LIMITED",
    "REJECTED",
    "CONFIRMED",
    "PENDING_CONFIRM",
    "UNKNOWN"
  ],
  "confirmed_requires": [
    "explicit_confirmation_evidence",
    "authenticated_session_evidence"
  ],
  "success_insufficient": [
    "http_200",
    "http_302",
    "cookie_present",
    "account_id_present",
    "error_disappeared"
  ],
  "evidence_required": [
    "evidence_id",
    "source_ref",
    "source_sha256",
    "captured_at",
    "observed_at",
    "transport_status",
    "application_error",
    "parse_status",
    "state",
    "confidence",
    "redacted"
  ],
  "confidence_values": [
    "recorded",
    "inferred",
    "unknown"
  ],
  "captured_at_unknown_value": null,
  "claim_status": {
    "legacy": "historical_record_only",
    "graphql_submit": "unresolved",
    "android_submit": "static_analysis_only",
    "bloks_confirmation": "unconfirmed"
  },
  "transition_cases": [
    {
      "name": "observe",
      "from": "NEW",
      "to": "OBSERVED",
      "allowed": true
    },
    {
      "name": "no_initial_success",
      "from": "NEW",
      "to": "CONFIRMED",
      "allowed": false
    },
    {
      "name": "pending",
      "from": "OBSERVED",
      "to": "PENDING_CONFIRM",
      "allowed": true
    },
    {
      "name": "confirmed_with_evidence",
      "from": "PENDING_CONFIRM",
      "to": "CONFIRMED",
      "allowed": true
    },
    {
      "name": "checkpoint",
      "from": "OBSERVED",
      "to": "CHECKPOINT",
      "allowed": true
    },
    {
      "name": "rate_limit",
      "from": "PENDING_CONFIRM",
      "to": "RATE_LIMITED",
      "allowed": true
    },
    {
      "name": "unknown_timeout",
      "from": "OBSERVED",
      "to": "UNKNOWN",
      "allowed": true
    },
    {
      "name": "unknown_stop",
      "from": "UNKNOWN",
      "to": "STOPPED",
      "allowed": true
    },
    {
      "name": "no_unknown_retry",
      "from": "UNKNOWN",
      "to": "OBSERVED",
      "allowed": false
    },
    {
      "name": "checkpoint_stop",
      "from": "CHECKPOINT",
      "to": "STOPPED",
      "allowed": true
    },
    {
      "name": "no_checkpoint_switch",
      "from": "CHECKPOINT",
      "to": "OBSERVED",
      "allowed": false
    },
    {
      "name": "rate_stop",
      "from": "RATE_LIMITED",
      "to": "STOPPED",
      "allowed": true
    },
    {
      "name": "no_rate_retry",
      "from": "RATE_LIMITED",
      "to": "OBSERVED",
      "allowed": false
    },
    {
      "name": "rejection_stop",
      "from": "REJECTED",
      "to": "STOPPED",
      "allowed": true
    },
    {
      "name": "finished_stop",
      "from": "CONFIRMED",
      "to": "STOPPED",
      "allowed": true
    },
    {
      "name": "no_stopped_restart",
      "from": "STOPPED",
      "to": "NEW",
      "allowed": false
    }
  ]
}
```

## 8. 验收与未完成项

本次验收：文档为 UTF-8，JSON 可解析；状态集合无重复；状态边引用有效；停止态不可重启；安全约束齐备；16 个允许/禁止状态迁移样例符合声明。
check_protocol.py 只读指定文档，不导入 core/、不调用供应商、不访问网络。
原稿缺少机器契约时应报告 FAIL，而非宣称旧协议不可用；研究副本通过只说明文档一致，不说明平台注册成功。

仍未完成：
1. 审计并修复日志脱敏：重点是 Task.as_row、Scheduler._log；本修订仅记录风险，未修改运行时代码。
2. 将现有 _classify 的 ACTIVE 与本规范的确认完成语义拆开，之后用合成样本做独立单测；本次不改变分类代码。
3. 用获授权、脱敏的历史样本审查入参和解析器，分别记录能确定与不能确定的字段；不补造参数、不自动重放。
4. 给历史结论补充样本数、捕获日期、版本和可定位证据；不足时保持 unknown，不报告成功率或风险权重。

后续顺序：先完成隐私保护，再做离线解析与证据审计；任何联网实验都另行明确授权范围，不由本规范隐式启动。
