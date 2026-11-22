"""MCP 2.x stdio interface to the restricted GAP service.

This module never accepts an OpenServer command or a free-form tag. Approval is
deliberately a local CLI operation and is not available to an LLM as a tool.
The SDK is optional for the manifest-only part of the project.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Annotated, Any

try:
    from mcp.server import MCPServer
    from mcp.types import ToolAnnotations
    from pydantic import Field
except ImportError:
    MCPServer = None
    ToolAnnotations = None
    Field = None


TOOL_NAMES = frozenset({
    "gap_environment", "gap_probe", "gap_prepare_smoke", "gap_prepare_manifest",
    "gap_execute", "gap_export",
})


def create_server(workspace: Path | str, service: Any = None):
    """Create the six-tool server, optionally injecting a service for tests.

    Construction and tool discovery do not activate COM. All permission checks,
    workspace constraints, immutable plan/receipt checks, and repeat handling
    remain in GapService; the MCP layer cannot grant approval on its own.
    """
    if MCPServer is None:
        raise RuntimeError(
            "Не найден официальный MCP SDK 2.x. Запустите сервер через .venv/Scripts/python.exe "
            "с зависимостями проекта. Работа с манифестом без MCP остаётся доступной."
        )
    root = Path(workspace).resolve()
    if service is None:
        from gap_integration.service import GapService

        service = GapService(root)
    server = MCPServer(
        name="gap-engineering-integration", version="0.1.0", log_level="WARNING",
        instructions=(
            "Работайте только с согласованными данными и копиями моделей внутри workspace. "
            "Сначала подготовьте план и покажите его инженеру. Подтверждение создаётся человеком "
            "через локальный CLI; инструмент подтверждения отсутствует. gap_execute требует receipt "
            "конкретного неизменённого плана. Не трактуйте smoke как production-модель или гидравлический расчёт."
        ),
    )

    def annotations(*, read_only: bool, idempotent: bool, destructive: bool = False):
        return ToolAnnotations(
            readOnlyHint=read_only, destructiveHint=destructive,
            idempotentHint=idempotent, openWorldHint=False,
        )

    @server.tool(
        description="Проверить локальные библиотеки, пути и inventory без активации COM и без запуска GAP.",
        annotations=annotations(read_only=True, idempotent=True), structured_output=True,
    )
    def gap_environment() -> dict[str, Any]:
        return service.inspect_environment()

    @server.tool(
        description="Проверить доступность OpenServer COM и состояние GAP без запуска GAP и без записи модели.",
        annotations=annotations(read_only=True, idempotent=True), structured_output=True,
    )
    def gap_probe() -> dict[str, Any]:
        return service.probe()

    @server.tool(
        description=(
            "Подготовить локальный неизменяемый план отдельного smoke-примера JOINT–PIPE–JOINT. "
            "Создаёт только JSON плана; не активирует COM, не создаёт модель GAP и не подтверждает выполнение."
        ),
        annotations=annotations(read_only=False, idempotent=False), structured_output=True,
    )
    def gap_prepare_smoke() -> dict[str, Any]:
        return service.prepare_smoke_plan()

    @server.tool(
        description=(
            "Проверить инженерный манифест и mapping и подготовить план переноса только готового фрагмента. "
            "Pending и отсутствующее согласованное отображение блокируют запись. GAP не изменяется."
        ),
        annotations=annotations(read_only=False, idempotent=False), structured_output=True,
    )
    def gap_prepare_manifest(
        manifest_path: Annotated[str, Field(
            strict=True, min_length=1,
            description="Путь к JSON манифесту первого участника внутри workspace; путь не является командой.",
        )],
        mapping_path: Annotated[str | None, Field(
            strict=True, min_length=1,
            description="Путь к явно согласованному JSON отображению объектов и единиц GAP внутри workspace.",
        )] = None,
    ) -> dict[str, Any]:
        return service.prepare_manifest_plan(manifest_path, mapping_path)

    @server.tool(
        description=(
            "Выполнить конкретный неизменённый план по receipt, созданному человеком через локальный CLI. "
            "Без соответствующего approval_id возвращает CONFIRMATION_REQUIRED. "
            "Повтор завершённого задания возвращает прежний результат; UNKNOWN не ретраится автоматически."
        ),
        annotations=annotations(read_only=False, idempotent=True, destructive=True), structured_output=True,
    )
    def gap_execute(
        plan_id: Annotated[str, Field(
            strict=True, min_length=1, description="ID ранее подготовленного неизменяемого плана.",
        )],
        approval_id: Annotated[str, Field(
            strict=True,
            description="ID локального человеческого receipt для этого плана; агент не может создать его сам.",
        )] = "",
    ) -> dict[str, Any]:
        return service.execute_plan(plan_id, approval_id)

    @server.tool(
        description=(
            "Считать сохранённую модель GAP внутри workspace: raw snapshot и, для проверенного профиля, нормализованный экспорт. "
            "Не переключает чужую сессию и не принимает произвольные теги OpenServer."
        ),
        annotations=annotations(read_only=False, idempotent=False), structured_output=True,
    )
    def gap_export(
        model_path: Annotated[str, Field(
            strict=True, min_length=1, description="Путь к сохранённой модели GAP внутри workspace.",
        )],
    ) -> dict[str, Any]:
        return service.export_model(model_path)

    return server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Restricted GAP MCP 2.x server (stdio)")
    parser.add_argument("--workspace", type=Path, required=True, help="Корень всех планов, receipt, моделей и журналов")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s")
    try:
        server = create_server(args.workspace)
    except (ImportError, RuntimeError) as exc:
        logging.getLogger(__name__).error("%s", exc)
        return 2
    server.run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
