"""The agent's GAP boundary uses the real stdio MCP transport."""
import asyncio
import sys
from pathlib import Path


class MCPError(Exception):
    pass


class MCPGateway:
    def __init__(self, root, interpreter=None):
        self.root = Path(root).resolve()
        self.interpreter = str(interpreter or sys.executable)

    async def _call(self, name, arguments):
        try:
            from mcp import Client, StdioServerParameters
        except ImportError:
            raise MCPError('Установи зависимости MCP в локальное окружение проекта') from None
        params = StdioServerParameters(
            command=self.interpreter,
            args=['-B', '-X', 'utf8', '-m', 'gap_integration.mcp_server', '--workspace', str(self.root)],
            cwd=self.root, env={'PYTHONUTF8': '1', 'PYTHONDONTWRITEBYTECODE': '1'},
        )
        try:
            async with Client(params, mode='legacy', read_timeout_seconds=70) as client:
                response = await client.call_tool(name, arguments)
                if response.is_error or not isinstance(response.structured_content, dict):
                    raise MCPError('MCP не вернул структурированный результат')
                return response.structured_content
        except MCPError:
            raise
        except Exception:
            raise MCPError('MCP недоступен или вызов не завершён; состояние GAP требует проверки перед повтором записи') from None

    def call(self, name, arguments):
        from gap_integration.mcp_server import TOOL_NAMES
        if name not in TOOL_NAMES:
            raise MCPError('Инструмент не разрешён')
        try:
            return asyncio.run(asyncio.wait_for(self._call(name, arguments), timeout=80))
        except TimeoutError:
            raise MCPError('Истёк общий таймаут MCP; состояние записи не определено, автоповтор запрещён') from None

