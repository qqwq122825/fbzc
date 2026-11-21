// 本地 HTTP 代理：把出口 TLS 指纹替换成 OkHttp(Conscrypt) 的指纹。
// 用法:
//   go mod tidy && go run . -upstream socks5://user:pass@住宅代理:1080 -listen 127.0.0.1:8899
//   Python 侧: proxies={"http":"http://127.0.0.1:8899","https":"http://127.0.0.1:8899"}
package main

import (
	"bufio"
	"crypto/tls"
	"flag"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"strings"

	"github.com/refraction-networking/utls"
	xproxy "golang.org/x/net/proxy"
)

func main() {
	upstream := flag.String("upstream", "", "socks5://user:pass@host:port（留空=直连）")
	listen := flag.String("listen", "127.0.0.1:8899", "本地监听地址")
	flag.Parse()

	var dialer xproxy.Dialer = xproxy.Direct
	if *upstream != "" {
		d, err := parseSOCKS5(*upstream)
		if err != nil {
			log.Fatalf("upstream: %v", err)
		}
		dialer = d
	}

	http.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		host := r.Host
		if !strings.Contains(host, ":") {
			host += ":443"
		}
		raw, err := dialer.Dial("tcp", host)
		if err != nil {
			http.Error(w, "dial: "+err.Error(), 502)
			return
		}
		defer raw.Close()

		cfg := &utls.Config{ServerName: strings.Split(host, ":")[0], InsecureSkipVerify: true}
		u := utls.UClient(raw, cfg, utls.HelloAndroid_11_OkHttp) // ← OkHttp/Conscrypt 指纹
		if err := u.Handshake(); err != nil {
			http.Error(w, "tls: "+err.Error(), 502)
			return
		}

		// 转发请求
		var b strings.Builder
		fmt.Fprintf(&b, "%s %s HTTP/1.1\r\n", r.Method, r.URL.RequestURI())
		r.Header.Set("Host", strings.Split(host, ":")[0])
		_ = r.Header.Write(&b)
		b.WriteString("\r\n")
		if _, err := io.WriteString(u, b.String()); err != nil {
			return
		}
		if r.Body != nil {
			_, _ = io.Copy(u, r.Body)
		}

		// 回写响应（含 chunked 原样透传）
		br := bufio.NewReader(u)
		resp, err := http.ReadResponse(br, r)
		if err != nil {
			return
		}
		for k, vs := range resp.Header {
			for _, v := range vs {
				w.Header().Add(k, v)
			}
		}
		w.WriteHeader(resp.StatusCode)
		_, _ = io.Copy(w, resp.Body)
	})

	log.Printf("ja3 proxy (HelloAndroid_11_OkHttp) on %s, upstream=%q", *listen, *upstream)
	log.Fatal(http.ListenAndServe(*listen, nil))
}

func parseSOCKS5(u string) (xproxy.Dialer, error) {
	if !strings.HasPrefix(u, "socks5://") {
		return nil, fmt.Errorf("only socks5:// supported")
	}
	body := strings.TrimPrefix(u, "socks5://")
	var auth *xproxy.Auth
	if at := strings.LastIndex(body, "@"); at != -1 {
		cred := strings.SplitN(body[:at], ":", 2)
		if len(cred) == 2 {
			auth = &xproxy.Auth{User: cred[0], Password: cred[1]}
		}
		body = body[at+1:]
	}
	return xproxy.SOCKS5("tcp", body, auth, xproxy.Direct)
}

var _ = tls.VersionTLS13
