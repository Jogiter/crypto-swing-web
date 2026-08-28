# -*- coding: utf-8 -*-
"""模拟七牛网关：只实现探针会碰到的端点，用来在不花额度的前提下验证探针本身。

    python3 tools/mock_qiniu_gateway.py 8791 &
    NO_PROXY=127.0.0.1 QINIU_API_KEY=whatever \
      python3 tools/qiniu_probe.py --base-url http://127.0.0.1:8791

刻意设置成「部分能力不透传」，这是最可能的真实形态：
  thinking  adaptive 拒绝 / enabled 接受
  output_config  带 effort 拒绝 / 只带 format 接受
  cache_control  接受并返回 cache_* 计数
  服务端工具  一律 400
"""
import json, sys
from http.server import BaseHTTPRequestHandler, HTTPServer

PLAYBOOK = json.load(open("docs/data/playbook.json", encoding="utf-8"))
PAYLOAD = json.dumps({"price_anchor": PLAYBOOK["price_anchor"],
                      "coins": PLAYBOOK["coins"]}, ensure_ascii=False)

MODELS = {"status": True, "data": [
    {"id": "claude-opus-4-5", "name": "Claude Opus 4.5", "description": "",
     "features": ["工具调用", "深度思考"],
     "model_constraints": {"context_length": 200000, "max_tokens": 64000,
                           "max_completion_tokens": 64000,
                           "max_default_completion_tokens": 8192,
                           "max_chain_of_thought_length": 0},
     "architecture": {"input_modalities": ["text", "image"],
                      "output_modalities": ["text"],
                      "function_calling": {"supported": True},
                      "schema_output": {"supported": True},
                      "reasoning": {"supported": True},
                      "content_cache": {"supported": True}},
     "issuer": {"name": "Anthropic", "avatar": "", "model_page": ""}},
    {"id": "claude-sonnet-4-5", "name": "Claude Sonnet 4.5", "description": "",
     "features": ["工具调用"],
     "model_constraints": {"context_length": 200000, "max_tokens": 64000,
                           "max_completion_tokens": 0,
                           "max_default_completion_tokens": 0,
                           "max_chain_of_thought_length": 0},
     "architecture": {"input_modalities": ["text"], "output_modalities": ["text"],
                      "function_calling": {"supported": True},
                      "schema_output": {"supported": True},
                      "reasoning": {"supported": False},
                      "content_cache": {"supported": True}},
     "issuer": {"name": "Anthropic", "avatar": "", "model_page": ""}},
]}


def sse(events):
    out = []
    for ev, data in events:
        out.append(f"event: {ev}\ndata: {json.dumps(data)}\n\n")
    return "".join(out).encode("utf-8")


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, code, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _bad(self, msg):
        self._json(400, {"type": "error",
                         "error": {"type": "invalid_request_error", "message": msg}})

    def do_GET(self):
        if self.headers.get("Authorization", "") == "":
            return self._json(401, {"error": "missing auth"})
        if self.path.startswith("/v2/stat/usage"):
            return self._json(200, {"status": True,
                                    "data": {"total_tokens": 12345,
                                             "total_cost": 0, "free_quota_left": 8000000}})
        if self.path.startswith("/v1/market/models"):
            return self._json(200, MODELS)
        self._json(404, {"error": "not found"})

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        req = json.loads(self.rfile.read(n) or b"{}")
        # 只认 Bearer，不认 x-api-key —— 复现七牛官方文档推荐的 AUTH_TOKEN 形态
        if not self.headers.get("Authorization", "").startswith("Bearer "):
            return self._json(401, {"type": "error",
                                    "error": {"type": "authentication_error",
                                              "message": "x-api-key not supported"}})
        if self.path.rstrip("/") != "/v1/messages":
            return self._json(404, {"error": "not found"})

        th = req.get("thinking")
        if isinstance(th, dict) and th.get("type") == "adaptive":
            return self._bad("thinking.type: adaptive is not supported")
        oc = req.get("output_config")
        if isinstance(oc, dict) and "effort" in oc:
            return self._bad("output_config.effort: unknown field")
        if req.get("tools"):
            return self._bad("tools.0.type: server tools are not supported")

        cached = any(isinstance(b, dict) and b.get("cache_control")
                     for b in (req.get("system") or [])
                     if isinstance(req.get("system"), list))
        usage = {"input_tokens": 1200, "output_tokens": 900,
                 "cache_creation_input_tokens": 5000 if cached else 0,
                 "cache_read_input_tokens": 0}
        text = PAYLOAD if req.get("max_tokens", 0) > 1000 else "收到"
        msg = {"id": "msg_mock", "type": "message", "role": "assistant",
               "model": req.get("model"), "content": [{"type": "text", "text": text}],
               "stop_reason": "end_turn", "stop_sequence": None, "usage": usage}

        if not req.get("stream"):
            return self._json(200, msg)

        head = dict(msg, content=[], usage=dict(usage, output_tokens=0))
        body = sse([
            ("message_start", {"type": "message_start", "message": head}),
            ("content_block_start", {"type": "content_block_start", "index": 0,
                                     "content_block": {"type": "text", "text": ""}}),
            ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                     "delta": {"type": "text_delta", "text": text}}),
            ("content_block_stop", {"type": "content_block_stop", "index": 0}),
            ("message_delta", {"type": "message_delta",
                               "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                               "usage": {"output_tokens": usage["output_tokens"]}}),
            ("message_stop", {"type": "message_stop"}),
        ])
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    HTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
