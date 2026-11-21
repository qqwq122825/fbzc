// 直接打印 FB App 的 HTTP 明文请求/响应，不依赖代理和证书。
// 覆盖 OkHttp3 / 老版本 HttpURLConnection / FB 自有 Liger(native，另见 §6)
// usage: frida -U -f com.facebook.katana -l capture/frida_okhttp_logger.js --no-pause
// 输出同时写 data/capture/live_<ts>.log（用 frida 的 file API，路径按设备可写目录）

var TS = Date.now();
var OUT = "/sdcard/fb_capture/live_" + TS + ".log";
var WATCH = ["b-graph.facebook.com", "graph.facebook.com", "api.facebook.com",
             "facebook.com/reg", "/auth/", "m.facebook.com/reg"];
var INTEREST = ["reg", "auth", "graphql", "checkpoint", "confirm", "signup", "account"];

function isWatched(u) {
  if (!u) return false;
  var s = String(u);
  for (var i = 0; i < WATCH.length; i++) if (s.indexOf(WATCH[i]) !== -1) return true;
  for (var j = 0; j < INTEREST.length; j++) if (s.indexOf(INTEREST[j]) !== -1) return true;
  return false;
}

var f = null;
try { f = new File(OUT, "a"); } catch (e) { f = null; }
function emit(s) {
  console.log(s);
  if (f) { try { f.write(s + "\n"); f.flush(); } catch (e) {} }
}

function headersToStr(h) {
  var out = [];
  try {
    for (var i = 0; i < h.size(); i++) {
      var k = h.name(i), v = h.value(i);
      // 只留诊断需要的，避免刷屏
      if (/^(x-fb|x-tigon|x-zero|x-asbd|user-agent|content-type|authorization|cookie)/i.test(k)) {
        out.push("  " + k + ": " + (v && v.length > 300 ? v.substring(0, 300) + "..." : v));
      }
    }
  } catch (e) {}
  return out.join("\n");
}

function bodyToStr(body) {
  if (!body) return "";
  try {
    if (body instanceof java.lang.String) return String(body);
    var Buf = Java.use("okio.Buffer");
    var buf = Buf.$new();
    body.writeTo(buf);
    var s = buf.readUtf8();
    buf.clear();
    return s && s.length > 4000 ? s.substring(0, 4000) + "...[truncated]" : s;
  } catch (e) {
    try { return String(body.toString()); } catch (e2) { return "[unreadable body]"; }
  }
}

function readResponse(resp) {
  try {
    var src = resp.body().source();
    src.request(4 * 1024 * 1024);
    var Buf = Java.use("okio.Buffer");
    var buf = Buf.$new();
    src.readAll(buf);
    var s = buf.readUtf8();
    buf.clear();
    src.close();
    return s;
  } catch (e) { return "[resp read fail: " + e + "]"; }
}

Java.perform(function () {
  emit("=== fb capture start " + new Date().toISOString() + " ===");

  // ---------------- OkHttp3 拦截器（最干净的钩子）
  try {
    var Builder = Java.use("okhttp3.Request$Builder");
    var RealCall = Java.use("okhttp3.RealCall");
    var Interceptor = Java.use("okhttp3.Interceptor");

    // 用 registerClass 造一个全局拦截器不现实；改为 hook RealCall.execute / enqueue 前的 Request
    RealCall.execute.implementation = function () {
      var req = this.request();
      var url = String(req.url().toString());
      if (isWatched(url)) {
        emit("[REQ ] " + req.method() + " " + url);
        emit(headersToStr(req.headers()));
        try {
          var b = req.body();
          if (b) {
            var ct = String(req.header("Content-Type") || "");
            var raw = bodyToStr(b);
            if (ct.indexOf("x-www-form-urlencoded") !== -1) {
              raw = decodeURIComponent(raw.replace(/\+/g, " "));
            }
            emit("[BODY] " + raw);
          }
        } catch (e) { emit("[BODY] err " + e); }
      }
      var resp = this.execute();
      if (isWatched(String(req.url().toString()))) {
        var clone = resp.newBuilder().build();
        var txt = readResponse(clone);
        emit("[RESP] " + resp.code() + " " + req.url());
        emit("       " + (txt && txt.length > 2000 ? txt.substring(0, 2000) + "...[truncated]" : txt));
        emit("---");
      }
      return resp;
    };
    emit("[ok] okhttp3.RealCall hooked");
  } catch (e) { emit("[!] okhttp3 hook fail: " + e); }

  // ---------------- 兜底：OkHttp2 / HttpURLConnection
  try {
    var Call = Java.use("com.android.okhttp.Call");
    Call.execute.implementation = function () {
      var req = this.request();
      var url = String(req.urlString());
      if (isWatched(url)) {
        emit("[REQ2] " + req.method() + " " + url);
        emit(headersToStr(req.headers()));
        if (req.body()) {
          try {
            var Buf = Java.use("okio.Buffer");
            var buf = Buf.$new();
            req.body().writeTo(buf);
            emit("[BODY2] " + buf.readUtf8());
          } catch (e) {}
        }
      }
      return this.execute();
    };
    emit("[ok] com.android.okhttp.Call hooked");
  } catch (e) { emit("[!] okhttp2 n/a: " + e); }

  // ---------------- 抓 GraphQL 的 params（FB 把 variables 塞在 form 里）
  try {
    var OkHttpClient = Java.use("okhttp3.OkHttpClient");
    emit("[ok] okhttp3 present, version=" + (OkHttpClient.class.getPackage() ? "?" : "?"));
  } catch (e) {}

  // ---------------- 关键：识别 checkpoint / 570 的触发点
  try {
    var Activity = Java.use("android.app.Activity");
    Activity.startActivity.overload("android.content.Intent").implementation = function (it) {
      var cls = "";
      try { cls = String(it.getComponent() ? it.getComponent().getClassName() : ""); } catch (e) {}
      if (/checkpoint|idverification|selfie|verif/i.test(cls)) {
        emit("[!!] 进入验证页: " + cls + "  (此环境已被标记，换指纹+换IP)");
      }
      return this.startActivity(it);
    };
    emit("[ok] Activity.startActivity hooked");
  } catch (e) { emit("[!] activity hook n/a: " + e); }

  emit("=== hooks ready ===");
});
