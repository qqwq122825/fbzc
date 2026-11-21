// frida SSL unpinning for com.facebook.katana (OkHttp / Conscrypt / TrustManager / TrustKit)
// usage: frida -U -f com.facebook.katana -l capture/frida_unpin.js --no-pause
Java.perform(function () {
  var LOG = function (m) { console.log("[unpin] " + m); };

  // 1) TrustManagerImpl: 主链路
  try {
    var TMImpl = Java.use("com.android.org.conscrypt.TrustManagerImpl");
    TMImpl.verifyChain.implementation = function () {
      LOG("TrustManagerImpl.verifyChain bypass");
      var arr = Java.array("java.security.cert.X509Certificate", arguments[0]);
      if (!arr || arr.length === 0) { return arguments[0]; }
      return arr;
    };
    TMImpl.checkTrustedRecursive.implementation = function () {
      LOG("TrustManagerImpl.checkTrustedRecursive bypass");
      return Java.use("java.util.ArrayList").$new();
    };
  } catch (e) { LOG("TrustManagerImpl n/a: " + e); }

  // 2) 自定义 X509TrustManager（OkHttp 常见实现）
  try {
    var X509TM = Java.use("javax.net.ssl.X509TrustManager");
    var TrustManager = Java.use("javax.net.ssl.TrustManager");
    // hook 掉所有 checkServerTrusted 实现
    Java.enumerateLoadedClasses({
      onMatch: function (name) {
        if (name.indexOf("TrustManager") === -1) { return; }
        try {
          var C = Java.use(name);
          if (C.checkServerTrusted) {
            C.checkServerTrusted.overloads.forEach(function (ov) {
              ov.implementation = function () { return; };
            });
          }
        } catch (e) {}
      },
      onComplete: function () {}
    });
  } catch (e) { LOG("X509 n/a: " + e); }

  // 3) OkHttp CertificatePinner
  ["okhttp3.CertificatePinner", "com.facebook.ssl.CertificatePinner"].forEach(function (cn) {
    try {
      var P = Java.use(cn);
      if (P.check) {
        P.check.overloads.forEach(function (ov) {
          ov.implementation = function () { LOG(cn + ".check bypass"); return; };
        });
      }
    } catch (e) {}
  });

  // 4) Facebook 自带 TrustKit / FbHttpPinning
  ["com.facebook.trustkit.PinningTrustManager", "com.facebook.common.ssl.FbPinningTrustManager"].forEach(function (cn) {
    try {
      var T = Java.use(cn);
      if (T.checkServerTrusted) {
        T.checkServerTrusted.overloads.forEach(function (ov) {
          ov.implementation = function () { LOG(cn + " bypass"); return; };
        });
      }
    } catch (e) {}
  });

  // 5) 让 WebView / 网络库统一不校验
  try {
    var SSLContext = Java.use("javax.net.ssl.SSLContext");
    var X509 = Java.use("javax.net.ssl.X509TrustManager");
    var TMStub = Java.registerClass({
      name: "com.bypass.TM",
      implements: [X509],
      methods: {
        checkClientTrusted: function () {},
        checkServerTrusted: function () {},
        getAcceptedIssuers: function () { return []; }
      }
    });
    SSLContext.init.overload(
      "[Ljavax.net.ssl.KeyManager;", "[Ljavax.net.ssl.TrustManager;", "java.security.SecureRandom"
    ).implementation = function (km, tm, sr) {
      this.init(km, [TMStub.$new()], sr);
    };
    LOG("SSLContext.init override installed");
  } catch (e) { LOG("SSLContext n/a: " + e); }

  // 6) 顺手放掉对代理/调试的检测（部分版本）
  try {
    var Sys = Java.use("java.lang.System");
    var getProp = Java.use("android.os.SystemProperties")["get"].overloads[0];
    getProp.implementation = function () {
      if (arguments[0] === "http.proxyHost" || arguments[0] === "http.proxyPort") { return null; }
      return getProp.call(Java.use("android.os.SystemProperties"), arguments[0]);
    };
  } catch (e) {}

  LOG("ready");
});
