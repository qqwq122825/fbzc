# 单次网页注册与邮箱确认：脱敏证据账本

日期：2026-11-21（Asia/Shanghai）

## 请求计数与结果

| 阶段 | 次数 | 观察结果 |
|---|---:|---|
| legacy 注册提交 | 1 | HTTP 302 到 `/login/save-device/`，新会话出现 `c_user`、`xs` |
| 邮箱确认提交 | 1 | HTTP 200，`application/x-javascript; charset=utf-8`，4497 字节 |
| 确认后的只读主页检查 | 1 | HTTP 302 到 `/checkpoint/<id>/` |
| checkpoint 页面访问 | 0 | 达到安全检查停止条件后立即停止 |

注册后的首次主页检查先到 `/confirmemail.php`，因此“账号会话已创建”和“邮箱已确认”必须
分开表述。确认 POST 的响应没有给出可证明成功的布尔值；后继 checkpoint 也不能反推邮箱
确认究竟成功还是失败。

## 协议结论

- 注册是 legacy HTML 表单：`POST /reg/submit/`，表单编码。
- 邮箱确认是 Bloks action：`POST /async/wbloks/fetch/`，`form.params` 内再嵌套 JSON。
- 当前确认码字段位于 `client_input_params.code`。
- 当前页 action 的 DATA 可以用白名单纯 DSL 解释器离线恢复；本次路径 116 步、未知操作符 0。
- 当前 APK 只观察到 `FbBloksActionRootQuery -> LoggedOutGraphQLServiceJNI.handleQueryJNI`；尚未
  捕获最终 HTTP URL、doc_id、正文和回调结果。因此不能说 APK 与网页使用相同线协议。

## 结论边界

- 当前账号状态：`STOP_SECURITY_CHALLENGE`。
- 邮箱确认状态：`UNPROVEN_AFTER_CONFIRMATION_POST_THEN_CHECKPOINT`。
- checkpoint 触发原因：未知；没有单变量 IP、邮箱或遥测对照实验。
- 没有进入安全检查，没有尝试绕过，没有再次提交注册或确认。
- 原始运行数据只保存在本机受限目录，不属于 Git 仓库。

详细请求结构见 `BLOKS_CONFIRM.md`；通用离线解释器见 `tools/bloks_dsl_eval.py`。
