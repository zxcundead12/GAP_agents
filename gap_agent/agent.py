"""One user request -> live LLM -> typed intent -> MCP/local checks -> AgentResult."""
import hashlib
from pathlib import Path
import re
import uuid

from gap_manifest.compare import compare_export
from gap_manifest.report import write_html_report
from gap_manifest.validation import validate_manifest
from .contracts import AgentTask, EXECUTION_STATUSES, validate_result, workspace_path
from .llm import CompatibleLLM, LLMError
from .mcp_client import MCPError, MCPGateway
from .storage import RunStore, digest, now, read_json


class EngineeringAgent:
    def __init__(self, root, llm=None, gateway=None):
        self.root = Path(root).resolve()
        config_path = self.root / 'data/agent_config.json'
        self.configuration_invalid = False
        try:
            config = read_json(config_path) if config_path.is_file() else {}
            if not isinstance(config, dict) or not isinstance(config.get('llm', {}), dict):
                raise ValueError('Invalid agent configuration')
        except (OSError, ValueError, TypeError):
            config = {}
            self.configuration_invalid = True
        self.llm = llm if llm is not None else CompatibleLLM(config.get('llm', {}))
        self.gateway = gateway if gateway is not None else MCPGateway(self.root)
        secret_names = [config.get('llm', {}).get('api_key_env', 'GROQ_API_KEY')]
        supplied_config = getattr(self.llm, 'config', {})
        if isinstance(supplied_config, dict):
            secret_names.append(supplied_config.get('api_key_env', 'GROQ_API_KEY'))
        self.store = RunStore(self.root, secret_env_names=[name for name in secret_names if isinstance(name, str)])

    def result(self, run_id, status, message, *, task=None, plan=None, artifacts=None,
               issues=None, assessment='NOT_ASSESSED', llm=None):
        issues = list(issues or [])
        if not isinstance(run_id, str) or not re.fullmatch(r'run_[0-9a-f]{32}', run_id):
            # Do not echo an arbitrary identifier: it may contain paths or secrets.
            run_id = 'run_' + uuid.uuid4().hex
            issues.append({'code': 'INVALID_RUN_ID', 'message': 'Некорректный идентификатор запуска'})
        if status not in EXECUTION_STATUSES:
            status = 'API_ERROR'
        value = validate_result({
            'schema_version': '1.0', 'run_id': run_id, 'trace_id': run_id,
            'execution_status': status, 'assessment_status': assessment,
            'message': message, 'task': task, 'plan': plan, 'artifacts': artifacts or {},
            'issues': issues, 'llm': dict(llm if llm is not None else getattr(self.llm, 'metadata', {})),
        })
        self.store.journal(run_id, 'agent_result', result=value)
        return value

    def context(self):
        path = self.root / 'data/candidate_manifest.json'
        case = None
        if path.is_file():
            value = read_json(path)
            if not isinstance(value, dict):
                raise ValueError('Invalid manifest context')
            candidate = value.get('case_id')
            if isinstance(candidate, str) and re.fullmatch(r'[\w.-]{1,128}', candidate):
                case = candidate
        return {'default_manifest_path': 'data/candidate_manifest.json', 'default_case_id': case}

    def request(self, text):
        run_id = 'run_' + uuid.uuid4().hex
        if self.configuration_invalid:
            return self.result(run_id, 'LLM_NOT_CONFIGURED', 'Локальная конфигурация агента не прошла проверку')
        if not isinstance(text, str) or not text.strip() or len(text) > 8000:
            return self.result(run_id, 'ERROR', 'Запрос должен содержать от 1 до 8000 символов')
        try:
            self.store.journal(run_id, 'llm_request', user_request=text, context=self.context())
            raw = self.llm.parse(text, self.context())
            task = AgentTask.parse(raw, self.root)
            self.store.journal(run_id, 'llm_response', task=task.to_dict(), llm=getattr(self.llm, 'metadata', {}))
            if task.clarifications:
                return self.result(run_id, 'NEEDS_CLARIFICATION', 'Нужно уточнить входные данные',
                                   task=task.to_dict(), issues=task.clarifications)
            if task.operation == 'unsupported':
                return self.result(run_id, 'OUT_OF_SCOPE', 'Запрос вне разрешённых операций агента', task=task.to_dict())
            return self.dispatch(run_id, task)
        except LLMError as exc:
            return self.result(run_id, exc.status, str(exc))
        except MCPError as exc:
            return self.result(run_id, 'MCP_UNAVAILABLE', str(exc))
        except (OSError, ValueError, KeyError, TypeError):
            return self.result(run_id, 'ERROR', 'Задача или входные файлы не прошли проверку; действия GAP не выполнялись')

    def call(self, run_id, name, arguments):
        self.store.journal(run_id, 'mcp_request', tool=name, arguments=arguments)
        value = self.gateway.call(name, arguments)
        self.store.journal(run_id, 'mcp_response', tool=name, tool_trace_id=value.get('trace_id'), result=value)
        return value

    def inputs(self, task):
        paths = {}
        for key in ('manifest_path', 'mapping_path', 'export_path', 'tolerances_path'):
            raw = getattr(task, key)
            if key == 'manifest_path' and raw is None:
                raw = 'data/candidate_manifest.json'
            if raw is not None:
                file = workspace_path(self.root, raw)
                paths[key] = {'path': str(file.relative_to(self.root)),
                              'sha256': hashlib.sha256(file.read_bytes()).hexdigest()}
        return paths

    def check_inputs(self, plan):
        for item in plan['inputs'].values():
            file = workspace_path(self.root, item['path'])
            if hashlib.sha256(file.read_bytes()).hexdigest() != item['sha256']:
                raise ValueError('Plan input changed')

    def dispatch(self, run_id, task):
        operation = task.operation
        if operation == 'inspect':
            response = self.call(run_id, 'gap_environment', {})
            path = self.store.save(run_id, 'environment.json', response)
            return self.result(run_id, response.get('execution_status'), 'Проверено окружение; лицензия не проверялась',
                               task=task.to_dict(), artifacts={'environment': str(path)})
        if operation == 'export':
            response = self.call(run_id, 'gap_export', {'model_path': task.model_path})
            return self.check_export(run_id, task, response)
        paths = self.inputs(task) if operation != 'smoke' else {}
        manifest = read_json(workspace_path(self.root, paths['manifest_path']['path'])) if paths else None
        if manifest is not None and task.case_id is not None and manifest.get('case_id') != task.case_id:
            raise ValueError('Wrong case_id')
        validation = validate_manifest(manifest, self.root) if manifest is not None else None
        if operation in ('validate', 'report', 'compare'):
            return self.review(run_id, task, manifest, validation)
        if operation == 'build' and not validation['ready_for_transfer']:
            path = self.store.save(run_id, 'validation.json', validation)
            return self.result(run_id, 'NOT_READY', 'Манифест не подтверждён или исходные данные неполны',
                               task=task.to_dict(), issues=validation['issues'], artifacts={'validation': str(path)})
        if operation == 'build' and not task.mapping_path:
            return self.result(run_id, 'NOT_READY', 'Нужен отдельно утверждённый профиль отображения GAP', task=task.to_dict())
        tool = 'gap_prepare_smoke' if operation == 'smoke' else 'gap_prepare_manifest'
        arguments = {} if operation == 'smoke' else {
            'manifest_path': paths['manifest_path']['path'], 'mapping_path': task.mapping_path}
        prepared = self.call(run_id, tool, arguments)
        if prepared.get('execution_status') != 'SUCCESS':
            return self.result(run_id, prepared.get('execution_status'), prepared.get('message', 'План не подготовлен'), task=task.to_dict())
        plan = {
            'schema_version': '1.0', 'run_id': run_id, 'created_at': now(),
            'task': task.to_dict(), 'inputs': paths,
            'integration_plan_id': prepared['plan_id'],
            'integration_plan_sha256': prepared['plan_sha256'],
            'target_model_path': prepared['target_model_path'],
            'actions': prepared['summary'],
            'llm': dict(getattr(self.llm, 'metadata', {})),
        }
        sha = self.store.create_plan(plan)
        self.store.journal(run_id, 'plan_prepared', plan_sha256=sha)
        return self.result(run_id, 'CONFIRMATION_REQUIRED', 'Прочитай план и подтверди его отдельной локальной командой',
                           task=task.to_dict(), plan={**plan, 'sha256': sha})

    def review(self, run_id, task, manifest, validation, export=None):
        comparison = None
        if task.operation == 'compare' or export is not None:
            export = export if export is not None else read_json(workspace_path(self.root, task.export_path))
            tolerances = read_json(workspace_path(self.root, task.tolerances_path)) if task.tolerances_path else None
            comparison = compare_export(manifest, export, tolerances)
            if not validation['ready_for_transfer']:
                comparison['assessment_status'] = 'NOT_ASSESSED'
        artifacts = {'validation': str(self.store.save(run_id, 'validation.json', validation))}
        if comparison is not None:
            artifacts['comparison'] = str(self.store.save(run_id, 'comparison.json', comparison))
        report_path = self.store.run_folder(run_id) / 'report.html'
        image = self.root / 'work/scheme_overview.png'
        write_html_report(manifest, validation, report_path, comparison, image if image.is_file() else None)
        artifacts['report'] = str(report_path)
        status = 'SUCCESS' if validation['ready_for_transfer'] else 'NOT_READY'
        if comparison and comparison['execution_status'] == 'ERROR':
            status = 'ERROR'
        assessment = comparison['assessment_status'] if comparison else 'NOT_ASSESSED'
        return self.result(run_id, status, 'Инженерная проверка выполнена; статус приёмки указан отдельно',
                           task=task.to_dict(), artifacts=artifacts,
                           issues=validation['issues'] + (comparison['issues'] if comparison else []), assessment=assessment)

    def check_export(self, run_id, task, response):
        if response.get('execution_status') != 'SUCCESS':
            return self.result(run_id, response.get('execution_status'), response.get('message', 'Экспорт не получен'), task=task.to_dict())
        export = response.get('normalized_export')
        if export is None and response.get('normalized_export_path'):
            export = read_json(workspace_path(self.root, response['normalized_export_path']))
        if export is None:
            path = self.store.save(run_id, 'snapshot_result.json', response)
            return self.result(run_id, 'NOT_READY', 'Получен только raw snapshot; независимый нормализованный экспорт отсутствует',
                               task=task.to_dict(), artifacts={'snapshot_result': str(path)})
        manifest = read_json(workspace_path(self.root, task.manifest_path or 'data/candidate_manifest.json'))
        return self.review(run_id, task, manifest, validate_manifest(manifest, self.root), export)

    def approve(self, run_id, operator, plan_sha256):
        if not isinstance(run_id, str) or not re.fullmatch(r'run_[0-9a-f]{32}', run_id):
            return self.result(run_id, 'CONFIRMATION_REQUIRED', 'Некорректный идентификатор запуска')
        try:
            if not isinstance(operator, str) or not operator.strip():
                raise ValueError('Operator is required')
            row, plan = self.store.load(run_id)
            if plan_sha256 != row['plan_hash']:
                raise ValueError('Explicit confirmation of exact plan hash is required')
            self.check_inputs(plan)
            # Approval is a local human command, never an LLM/MCP tool.
            from gap_integration.service import GapService
            service = GapService(self.root)
            native_row, _ = service._load_plan(plan['integration_plan_id'])
            if native_row['plan_sha256'] != plan['integration_plan_sha256']:
                raise ValueError('Integration plan binding changed')
            native = service.approve_plan(plan['integration_plan_id'], operator)
            if native['execution_status'] != 'SUCCESS':
                return self.result(run_id, native['execution_status'], native['message'], llm=plan['llm'])
            receipt = self.store.approve(run_id, {
                'plan_sha256': plan_sha256, 'approved_by': operator.strip(), 'approved_at': now(),
                'integration_approval_id': native['approval_id'],
            })
            self.store.journal(run_id, 'human_approval', receipt=receipt)
            return self.result(run_id, 'SUCCESS', 'Подтверждён конкретный план; GAP ещё не изменён',
                               task=plan['task'], plan={**plan, 'sha256': plan_sha256}, llm=plan['llm'])
        except (OSError, ValueError, TypeError, KeyError):
            return self.result(run_id, 'CONFIRMATION_REQUIRED', 'Подтверждение отклонено: проверь хеш плана, оператора и входные файлы')

    def execute(self, run_id):
        if not isinstance(run_id, str) or not re.fullmatch(r'run_[0-9a-f]{32}', run_id):
            return self.result(run_id, 'NOT_READY', 'Некорректный идентификатор запуска')
        try:
            _, plan = self.store.load(run_id)
            self.check_inputs(plan)
            state, receipt = self.store.claim(run_id)
            if state == 'CACHED':
                return receipt
            if state != 'CLAIMED':
                return self.result(run_id, 'CONFIRMATION_REQUIRED' if state == 'PREPARED' else 'NOT_READY',
                                   'Нет подтверждения либо исход предыдущего выполнения требует проверки', llm=plan['llm'])
            response = None
            try:
                response = self.call(run_id, 'gap_execute', {
                    'plan_id': plan['integration_plan_id'], 'approval_id': receipt['integration_approval_id']})
                task = AgentTask.parse(plan['task'], self.root)
                if response.get('execution_status') == 'SUCCESS' and task.operation == 'build':
                    result = self.check_export(run_id, task, response)
                else:
                    path = self.store.save(run_id, 'integration_result.json', response)
                    result = self.result(run_id, response.get('execution_status'), response.get('message', 'Операция завершена'),
                                         task=plan['task'], artifacts={'integration_result': str(path)}, llm=plan['llm'])
            except MCPError as exc:
                result = self.result(run_id, 'MCP_UNAVAILABLE', str(exc), llm=plan['llm'])
            except Exception:
                result = self.result(run_id, 'TIMEOUT', 'Исход выполнения не определён; автоматический повтор запрещён', llm=plan['llm'])
            self.store.finish(run_id, result, native_result=response)
            return result
        except (OSError, ValueError, TypeError, KeyError):
            return self.result(run_id, 'NOT_READY', 'План не найден, изменён либо изменились его входные файлы')

    def acknowledge(self, run_id, operator, note):
        if not isinstance(run_id, str) or not re.fullmatch(r'run_[0-9a-f]{32}', run_id):
            return self.result(run_id, 'NOT_READY', 'Некорректный идентификатор запуска')
        try:
            if not isinstance(operator, str) or not operator.strip() or not isinstance(note, str) or not note.strip():
                raise ValueError('Named investigation required')
            row, plan = self.store.load(run_id)
            if row['state'] not in ('RUNNING', 'UNKNOWN'):
                raise ValueError('Run is not uncertain')
            from gap_integration.service import GapService
            service = GapService(self.root)
            native_row, _ = service._load_plan(plan['integration_plan_id'])
            if native_row['state'] in ('RUNNING', 'UNKNOWN'):
                native = service.acknowledge_unknown(plan['integration_plan_id'], operator, note)
                if native['execution_status'] != 'SUCCESS':
                    return self.result(run_id, native['execution_status'], native['message'], llm=plan['llm'])
            elif native_row['state'] == 'APPROVED':
                # A timed-out stdio connection might not have delivered its request yet.
                # Retire the local plan so a delayed request cannot start a fresh write.
                with service._connection() as db:
                    db.execute('BEGIN IMMEDIATE')
                    current = db.execute('SELECT state FROM plans WHERE plan_id=?',
                                         (plan['integration_plan_id'],)).fetchone()
                    if current is None or current['state'] != 'APPROVED':
                        return self.result(run_id, 'NOT_READY',
                                           'Состояние выполнения изменилось; повтори ручную проверку перед подтверждением разбора',
                                           llm=plan['llm'])
                    changed = db.execute("UPDATE plans SET state='RESOLVED_NO_RETRY',updated_at=? WHERE plan_id=? AND state='APPROVED'",
                                         (now(), plan['integration_plan_id']))
                    if changed.rowcount != 1:
                        return self.result(run_id, 'NOT_READY', 'План не был закрыт; неопределённый запуск сохранён',
                                           llm=plan['llm'])
            self.store.resolve(run_id, operator, note)
            return self.result(run_id, 'SUCCESS', 'Ручной разбор записан; прежний план не повторяется. Отмена GAP не заявляется', llm=plan['llm'])
        except (OSError, ValueError, KeyError, TypeError):
            return self.result(run_id, 'NOT_READY', 'Нужен неопределённый запуск, имя проверившего и описание фактической проверки GAP')

