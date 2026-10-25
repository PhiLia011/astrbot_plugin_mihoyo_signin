# -*- coding: utf-8 -*-
"""一次性真人验证码网页。"""

from __future__ import annotations

import json
import secrets
import time
from dataclasses import dataclass
from typing import Awaitable, Callable

from aiohttp import web

try:
    from .game_record import CaptchaChallenge
except ImportError:
    from game_record import CaptchaChallenge


@dataclass
class PendingCaptcha:
    token: str
    challenge: CaptchaChallenge
    context: dict
    expires_at: float
    used: bool = False


class CaptchaWebServer:
    def __init__(
        self,
        host: str,
        port: int,
        public_base_url: str,
        handler: Callable[[PendingCaptcha, dict], Awaitable[tuple[bool, str]]],
        logger,
        ttl_seconds: int = 300,
    ):
        self.host = host
        self.port = max(1, min(65535, int(port)))
        self.public_base_url = public_base_url.rstrip("/")
        self.handler = handler
        self.logger = logger
        self.ttl_seconds = max(60, min(600, int(ttl_seconds)))
        self.pending: dict[str, PendingCaptcha] = {}
        self.runner: web.AppRunner | None = None
        self.site: web.TCPSite | None = None

    async def start(self):
        if self.runner:
            return
        app = web.Application(client_max_size=16 * 1024)
        app.router.add_get("/mihoyo/captcha/health", self._health)
        app.router.add_get("/mihoyo/captcha/{token}", self._page)
        app.router.add_post("/mihoyo/captcha/{token}", self._submit)
        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, self.host, self.port)
        await self.site.start()
        self.logger.info(f"米游社验证码网页已启动: {self.host}:{self.port}")

    async def stop(self):
        if self.runner:
            await self.runner.cleanup()
        self.runner = None
        self.site = None
        self.pending.clear()

    def create_link(self, challenge: CaptchaChallenge, context: dict) -> str:
        self._prune()
        token = secrets.token_urlsafe(32)
        self.pending[token] = PendingCaptcha(
            token=token,
            challenge=challenge,
            context=context,
            expires_at=time.time() + self.ttl_seconds,
        )
        base = self.public_base_url or f"http://127.0.0.1:{self.port}"
        return f"{base}/mihoyo/captcha/{token}"

    def _prune(self):
        now = time.time()
        for token, pending in list(self.pending.items()):
            if pending.used or pending.expires_at <= now:
                self.pending.pop(token, None)

    async def _health(self, _request):
        return web.json_response({"status": "ok"})

    async def _page(self, request: web.Request):
        self._prune()
        token = request.match_info["token"]
        pending = self.pending.get(token)
        if not pending:
            return web.Response(
                text="验证链接已过期或已使用，请回到机器人重新执行查询命令。",
                status=410,
                content_type="text/html",
                charset="utf-8",
                headers=self._headers(),
            )
        payload = {
            "gt": pending.challenge.gt,
            "challenge": pending.challenge.challenge,
            "new_captcha": pending.challenge.new_captcha,
            "submit": f"/mihoyo/captcha/{token}",
        }
        page = _render_page(payload)
        return web.Response(
            text=page,
            content_type="text/html",
            charset="utf-8",
            headers=self._headers(),
        )

    async def _submit(self, request: web.Request):
        self._prune()
        token = request.match_info["token"]
        pending = self.pending.get(token)
        if not pending:
            return web.json_response(
                {"ok": False, "message": "验证链接已过期或已使用"},
                status=410,
                headers=self._headers(),
            )
        try:
            body = await request.json()
        except Exception:
            return web.json_response(
                {"ok": False, "message": "提交内容不是有效 JSON"},
                status=400,
                headers=self._headers(),
            )
        solved = {}
        for key in ("geetest_challenge", "geetest_validate", "geetest_seccode"):
            value = body.get(key)
            if not isinstance(value, str) or not value or len(value) > 2048:
                return web.json_response(
                    {"ok": False, "message": "验证码结果不完整"},
                    status=400,
                    headers=self._headers(),
                )
            solved[key] = value
        pending.used = True
        try:
            ok, message = await self.handler(pending, solved)
        except Exception as exc:
            self.logger.warning(f"验证码网页处理失败: {exc}")
            ok, message = False, "米游社验证失败，请回到机器人重新执行查询"
        finally:
            self.pending.pop(token, None)
        return web.json_response(
            {"ok": ok, "message": message},
            status=200 if ok else 400,
            headers=self._headers(),
        )

    @staticmethod
    def _headers() -> dict:
        return {
            "Cache-Control": "no-store, max-age=0",
            "Pragma": "no-cache",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Content-Security-Policy": (
                "default-src 'none'; script-src 'self' 'unsafe-inline' "
                "https://*.geetest.com; style-src 'unsafe-inline'; "
                "img-src data: https://*.geetest.com; "
                "font-src data: https://*.geetest.com; "
                "connect-src 'self' https://*.geetest.com; "
                "frame-src https://*.geetest.com"
            ),
        }


def _render_page(payload: dict) -> str:
    data = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>米游社真人验证</title>
  <style>
    :root {{ color-scheme: light dark; }}
    body {{ margin: 0; min-height: 100vh; display: grid; place-items: center;
      font: 16px/1.6 system-ui, -apple-system, "Microsoft YaHei", sans-serif;
      background: #eef2f7; color: #1e293b; }}
    main {{ width: min(92vw, 460px); background: #fff; border-radius: 18px;
      padding: 28px; box-sizing: border-box; box-shadow: 0 18px 60px #1e293b22; }}
    h1 {{ margin: 0 0 8px; font-size: 22px; }}
    p {{ margin: 8px 0; color: #475569; }}
    #captcha {{ min-height: 60px; margin: 22px 0; }}
    #status {{ padding: 12px; border-radius: 10px; background: #f1f5f9; }}
    .ok {{ background: #dcfce7 !important; color: #166534 !important; }}
    .error {{ background: #fee2e2 !important; color: #991b1b !important; }}
    @media (prefers-color-scheme: dark) {{
      body {{ background: #0f172a; color: #e2e8f0; }}
      main {{ background: #1e293b; }}
      p {{ color: #cbd5e1; }}
      #status {{ background: #334155; }}
    }}
  </style>
  <script src="https://static.geetest.com/static/js/gt.0.4.9.js"></script>
</head>
<body>
<main>
  <h1>米游社真人验证</h1>
  <p>完成官方验证码后，本次查询会自动继续，结果会发送回机器人会话。</p>
  <p>验证链接只可使用一次，有效期以机器人提示为准。不会在本页面显示或保存 Cookie。</p>
  <div id="captcha"></div>
  <div id="status">正在加载验证码…</div>
</main>
<script>
const challenge = {data};
const statusBox = document.getElementById('status');
function setStatus(text, type) {{
  statusBox.textContent = text;
  statusBox.className = type || '';
}}
async function submit(result) {{
  setStatus('正在提交验证…');
  try {{
    const response = await fetch(challenge.submit, {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify(result)
    }});
    const data = await response.json();
    setStatus(data.message || (data.ok ? '验证成功，结果已发送' : '验证失败'), data.ok ? 'ok' : 'error');
    if (data.ok) document.getElementById('captcha').innerHTML = '';
  }} catch (error) {{
    setStatus('网络错误，请回到机器人重新发起查询', 'error');
  }}
}}
if (typeof initGeetest !== 'function') {{
  setStatus('验证码组件加载失败，请检查网络后刷新', 'error');
}} else {{
  initGeetest({{
    gt: challenge.gt,
    challenge: challenge.challenge,
    new_captcha: challenge.new_captcha,
    offline: false,
    product: 'bind',
    width: '100%'
  }}, function (captchaObj) {{
    captchaObj.appendTo('#captcha');
    captchaObj.onReady(function () {{ setStatus('请完成上方验证'); }});
    captchaObj.onSuccess(function () {{
      const result = captchaObj.getValidate();
      if (result) submit(result);
    }});
    captchaObj.onError(function () {{ setStatus('验证码加载失败，请刷新页面', 'error'); }});
  }}, function () {{
    setStatus('验证码服务暂不可用，请稍后刷新', 'error');
  }});
}}
</script>
</body>
</html>"""
