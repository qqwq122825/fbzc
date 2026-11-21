# 邮箱确认 Bloks 协议与 checkpoint 实测

> 本文只保留脱敏、可复核的协议事实。账号、邮箱、验证码、Cookie、页面原文和代理凭据不入库。

## 1. 已观察到的状态链

一次受控实验中只提交了一次注册和一次邮箱确认：

```text
GET  https://m.facebook.com/reg/                -> 200
POST https://m.facebook.com/reg/submit/         -> 302 /login/save-device/
                                                     新会话出现 c_user、xs
GET  https://m.facebook.com/                    -> 302 /confirmemail.php
POST https://m.facebook.com/async/wbloks/fetch/ -> 200 application/x-javascript
GET  https://m.facebook.com/                    -> 302 /checkpoint/<id>/
```

这证明 legacy 表单建立了账号会话，并且当前确认页接受了一个 Bloks action 请求。
它**不能**证明邮箱确认成功：确认响应中没有恢复出明确的成功布尔值，随后只读状态检查落入
`/checkpoint/`。业务状态记为 `STOP_SECURITY_CHALLENGE`，程序退出码 0 只表示请求已记录并
按停止条件退出。

没有进入或尝试绕过 checkpoint，也没有重试确认、切换身份或继续注册。

## 2. 当前确认请求的线级结构

```text
POST https://m.facebook.com/async/wbloks/fetch/
query:
  appid=com.bloks.www.bloks.caa.reg.confirmation.async
  type=action
  __bkv=<当前页面实际引用的 fbWebVersioningId>

Content-Type: application/x-www-form-urlencoded;charset=UTF-8

form.params = JSON.stringify(DATA)
DATA = {
  "params": JsonEncode({
    "server_params": { ... },
    "client_input_params": {
      "code": "<邮箱确认码>",
      ...
    }
  })
}
```

同时提交的页面态字段包括 `fb_dtsg`、`jazoest`、`lsd` 以及页面提供的 SiteData 字段。
`jazoest` 按当前页面的 `DTSGUtils`/`SprinkleConfig` 计算；没有使用旧 helper 的回退常量。

## 3. DATA 的恢复方式

- 入口为 `StartFBWebBloks.startFBWebBloks`，环境为 `FBWebBloksEnvironment`。
- `__bkv` 使用确认页实际资源里的 `fbWebVersioningId`，没有套用另一个 FBLOX 环境版本。
- 当前页面 `reg_info` 有 219 个字段；联系人和 user id 只做一致性校验，不写入公共结果。
- 页面 Lispy 数据由非执行型 parser 读取；没有执行下载的 JavaScript。
- DATA 由白名单纯解释器计算；本次路径为 116 个纯操作步骤，未知操作符为 0。
- 已验证的操作包括 Map/Array/JSON 编码、变量与引用读取、函数参数帧、惰性
  If/Coalesce/Match/Pattern/Default，以及 i64 十进制字符串转换。
- `JsonEncode` 会把布尔值归一化为 `1/0`，与当前公开前端实现一致。

通用的离线解释器已整理到 `tools/bloks_dsl_eval.py`。它没有网络功能，也不读取账号或运行时
私密文件。

## 4. 不能从本次实验推出的结论

1. HTTP 200 不等于确认成功；当前唯一可靠的后继状态是 `/checkpoint/`。
2. checkpoint 原因未知。单次结果不能归因于出口 IP、邮箱域、遗漏的遥测字段或某个
   具体请求参数。
3. `__s`、`__dyn`、`__csr` 等浏览器运行时遥测没有被宣称为与真实浏览器等价。
4. 服务器初始变量里的 AAC/attestation 值没有被当成客户端证明，也没有伪造证明。
5. 只观察到 checkpoint 路径，没有打开页面，因此不知道它要求短信、验证码、证件还是
   其他安全检查。

## 5. 历史结果与当前结果的区别

早期实验只补齐 CSRF 字段时曾得到 `1357004` 或 HTTP 500；那只能说明旧 payload 不完整。
本次使用当前页的 action、变量、版本和 DATA 后，确认 POST 返回 HTTP 200，但后继状态进入
checkpoint。旧的“还缺 text_input_id / __hs / __dyn，所以只会 500”已不再是当前结论。

## 6. 研究边界

- 只研究自己发起的有限请求和公开前端资源。
- 不把 checkpoint 当成需要规避的障碍；安全检查由账号所有者正常手动完成。
- 不提交或分发密码、Cookie、验证码、邮箱内部 ID、原始响应、代理凭据或账号清单。
- 不把本仓库改造成批量注册、账号生产或风控绕过工具。
