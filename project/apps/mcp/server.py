"""MCP 服务端装配：注册原语并构建 ASGI 应用。"""
from __future__ import annotations

import logging

from django.conf import settings
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

from . import prompts, resources, tools
from .auth import PlatformTokenVerifier

logger = logging.getLogger(__name__)

SERVER_NAME = 'beancount-trans'
SERVER_TITLE = 'Beancount-Trans 账本'
MCP_HTTP_PATH = '/mcp'

SERVER_INSTRUCTIONS = (
    '本服务提供当前用户 Beancount 账本的只读访问能力，作用域由访问令牌所属用户决定。'
    '所有金额与统计结论都必须来自 run_bql 的实际查询结果，不得凭记忆推断账户名或数字；'
    '首次回答账本相关问题前，先调用 get_ledger_context 获取账户/标签目录与 BQL 用法。'
    '需要逐笔交易明细时使用 get_journal（结构化、按交易分页）；分类汇总、余额与环比仍用 run_bql。'
    '本服务不提供写入能力，账本变更请在 Beancount-Trans 前端完成。'
)

_server: MCPServer | None = None


def _auth_settings() -> AuthSettings | None:
    """按配置返回鉴权设置；未配置资源地址时返回 None（仅开发态可用）。"""
    if not getattr(settings, 'MCP_AUTH_ENABLED', False):
        return None
    resource_url = (getattr(settings, 'MCP_RESOURCE_SERVER_URL', '') or '').strip()
    if not resource_url:
        logger.warning('未配置 MCP_RESOURCE_SERVER_URL，MCP 服务将不校验访问令牌')
        return None
    issuer_url = (getattr(settings, 'MCP_ISSUER_URL', '') or '').strip() or resource_url
    return AuthSettings(
        issuer_url=issuer_url,
        resource_server_url=resource_url,
        required_scopes=list(getattr(settings, 'MCP_REQUIRED_SCOPES', []) or []) or None,
        # 令牌由平台自行签发，不绑定 RFC 8707 资源指示符
        validate_token_resource=False,
    )


def create_server() -> MCPServer:
    """创建一个已注册全部原语的 MCP 服务实例。"""
    auth = _auth_settings()
    server = MCPServer(
        name=SERVER_NAME,
        title=SERVER_TITLE,
        instructions=SERVER_INSTRUCTIONS,
        auth=auth,
        token_verifier=PlatformTokenVerifier() if auth else None,
    )
    tools.register(server)
    resources.register(server)
    prompts.register(server)
    return server


def get_server() -> MCPServer:
    global _server
    if _server is None:
        _server = create_server()
    return _server


def _transport_security() -> TransportSecuritySettings:
    allowed_hosts = list(getattr(settings, 'MCP_ALLOWED_HOSTS', []) or [])
    allowed_origins = list(getattr(settings, 'MCP_ALLOWED_ORIGINS', []) or [])
    if not allowed_hosts:
        # 未配置时退回本机开发默认值，避免裸奔
        allowed_hosts = ['127.0.0.1:*', 'localhost:*', '[::1]:*']
        allowed_origins = allowed_origins or ['http://127.0.0.1:*', 'http://localhost:*', 'http://[::1]:*']
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=allowed_hosts,
        allowed_origins=allowed_origins,
    )


def build_asgi_app():
    """构建 Streamable HTTP 形式的 ASGI 应用（单 JSON 响应、无会话）。"""
    return get_server().streamable_http_app(
        streamable_http_path=MCP_HTTP_PATH,
        json_response=True,
        stateless_http=True,
        transport_security=_transport_security(),
    )
