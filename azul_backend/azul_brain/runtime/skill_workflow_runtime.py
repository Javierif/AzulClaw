"""Skill workflow runtime primitives backed by Agent Framework HITL concepts."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from collections.abc import Awaitable, Callable
from typing import Any, Literal
from uuid import uuid4

from agent_framework import WorkflowRunState

from .approval_service import ApprovalService, default_approval_lifecycle_path
from .store import to_iso_z, utc_now

WorkflowRunStatus = Literal[
    "started",
    "in_progress",
    "waiting_for_human",
    "approved",
    "rejected",
    "completed",
    "failed",
    "cancelled",
]

WorkflowEventType = Literal["delta", "status", "request_info", "completed", "failed"]
SkillToolInvoker = Callable[[str, dict[str, Any]], Awaitable[Any]]


@dataclass
class HumanApprovalRequest:
    """Human-in-the-loop approval request emitted by a skill workflow."""

    request_id: str
    run_id: str
    skill_id: str
    action_kind: str
    title: str
    summary: str
    payload: dict[str, Any] = field(default_factory=dict)
    risk: str = "medium"
    labels: dict[str, str] = field(default_factory=dict)


@dataclass
class HumanApprovalResponse:
    """User decision returned to a paused skill workflow."""

    approved: bool
    user_id: str
    reason: str = ""


@dataclass
class SkillWorkflowEvent:
    """Structured event surfaced from a skill workflow to AzulClaw clients."""

    type: WorkflowEventType
    run_id: str
    skill_id: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class SkillWorkflowRun:
    """Persisted workflow run state for marketplace skills."""

    run_id: str
    skill_id: str
    user_id: str
    conversation_id: str
    status: WorkflowRunStatus
    created_at: str
    updated_at: str
    checkpoint_id: str = ""
    workflow_name: str = ""
    pending_request_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SkillWorkflowPendingRequest:
    """Persisted request_info bridge for a paused workflow."""

    request: HumanApprovalRequest
    status: Literal["pending", "approved", "rejected"] = "pending"
    created_at: str = ""
    updated_at: str = ""
    response: HumanApprovalResponse | None = None


def default_skill_workflow_state_path() -> Path:
    override = os.environ.get("AZUL_RUNTIME_DIR", "").strip()
    if override:
        return Path(override).expanduser() / "runtime_skill_workflows.json"
    return Path(__file__).resolve().parents[3] / "memory" / "runtime_skill_workflows.json"


def _now() -> str:
    return to_iso_z(utc_now())


def _workflow_waiting_status() -> WorkflowRunStatus:
    # Keep the mapping close to Agent Framework's native paused state.
    return "waiting_for_human" if WorkflowRunState.IDLE_WITH_PENDING_REQUESTS else "waiting_for_human"


class SkillWorkflowStore:
    """Small JSON store for workflow runs and pending HITL requests."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_skill_workflow_state_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"schema_version": "1.0", "runs": [], "requests": []}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {"schema_version": "1.0", "runs": [], "requests": []}
        if not isinstance(data, dict):
            return {"schema_version": "1.0", "runs": [], "requests": []}
        runs = data.get("runs", [])
        requests = data.get("requests", [])
        return {
            "schema_version": "1.0",
            "runs": runs if isinstance(runs, list) else [],
            "requests": requests if isinstance(requests, list) else [],
        }

    def save(self, data: dict[str, Any]) -> None:
        payload = {
            "schema_version": "1.0",
            "runs": data.get("runs", []) if isinstance(data.get("runs", []), list) else [],
            "requests": data.get("requests", []) if isinstance(data.get("requests", []), list) else [],
        }
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def list_runs(self) -> list[SkillWorkflowRun]:
        return [self._run_from_dict(item) for item in self.load()["runs"] if isinstance(item, dict)]

    def get_run(self, run_id: str) -> SkillWorkflowRun | None:
        safe_run_id = str(run_id).strip()
        return next((run for run in self.list_runs() if run.run_id == safe_run_id), None)

    def get_request(self, request_id: str) -> SkillWorkflowPendingRequest | None:
        safe_request_id = str(request_id).strip()
        for item in self.load()["requests"]:
            if not isinstance(item, dict):
                continue
            request = item.get("request")
            if isinstance(request, dict) and str(request.get("request_id", "")).strip() == safe_request_id:
                return self._request_from_dict(item)
        return None

    def upsert_run(self, run: SkillWorkflowRun) -> SkillWorkflowRun:
        data = self.load()
        runs = [item for item in data["runs"] if isinstance(item, dict)]
        raw = asdict(run)
        for index, item in enumerate(runs):
            if str(item.get("run_id", "")).strip() == run.run_id:
                runs[index] = raw
                break
        else:
            runs.insert(0, raw)
        data["runs"] = runs
        self.save(data)
        return run

    def upsert_request(self, pending: SkillWorkflowPendingRequest) -> SkillWorkflowPendingRequest:
        data = self.load()
        requests = [item for item in data["requests"] if isinstance(item, dict)]
        raw = asdict(pending)
        request_id = pending.request.request_id
        for index, item in enumerate(requests):
            request = item.get("request")
            if isinstance(request, dict) and str(request.get("request_id", "")).strip() == request_id:
                requests[index] = raw
                break
        else:
            requests.insert(0, raw)
        data["requests"] = requests
        self.save(data)
        return pending

    def _run_from_dict(self, item: dict[str, Any]) -> SkillWorkflowRun:
        return SkillWorkflowRun(
            run_id=str(item.get("run_id", "")).strip(),
            skill_id=str(item.get("skill_id", "")).strip(),
            user_id=str(item.get("user_id", "")).strip(),
            conversation_id=str(item.get("conversation_id", "")).strip(),
            status=str(item.get("status", "started")).strip() or "started",  # type: ignore[arg-type]
            created_at=str(item.get("created_at", "")).strip(),
            updated_at=str(item.get("updated_at", "")).strip(),
            checkpoint_id=str(item.get("checkpoint_id", "")).strip(),
            workflow_name=str(item.get("workflow_name", "")).strip(),
            pending_request_id=str(item.get("pending_request_id", "")).strip(),
            metadata=item.get("metadata") if isinstance(item.get("metadata"), dict) else {},
        )

    def _request_from_dict(self, item: dict[str, Any]) -> SkillWorkflowPendingRequest:
        request = item.get("request") if isinstance(item.get("request"), dict) else {}
        response = item.get("response") if isinstance(item.get("response"), dict) else None
        return SkillWorkflowPendingRequest(
            request=HumanApprovalRequest(
                request_id=str(request.get("request_id", "")).strip(),
                run_id=str(request.get("run_id", "")).strip(),
                skill_id=str(request.get("skill_id", "")).strip(),
                action_kind=str(request.get("action_kind", "")).strip(),
                title=str(request.get("title", "")).strip(),
                summary=str(request.get("summary", "")).strip(),
                payload=request.get("payload") if isinstance(request.get("payload"), dict) else {},
                risk=str(request.get("risk", "medium")).strip() or "medium",
                labels=request.get("labels") if isinstance(request.get("labels"), dict) else {},
            ),
            status=str(item.get("status", "pending")).strip() or "pending",  # type: ignore[arg-type]
            created_at=str(item.get("created_at", "")).strip(),
            updated_at=str(item.get("updated_at", "")).strip(),
            response=(
                HumanApprovalResponse(
                    approved=bool(response.get("approved")),
                    user_id=str(response.get("user_id", "")).strip(),
                    reason=str(response.get("reason", "")).strip(),
                )
                if response
                else None
            ),
        )


class SkillWorkflowRuntime:
    """Bridge between marketplace skill workflows and AzulClaw approval lifecycle."""

    def __init__(
        self,
        *,
        store: SkillWorkflowStore | None = None,
        approval_service: ApprovalService | None = None,
    ) -> None:
        self.store = store or SkillWorkflowStore()
        self.approval_service = approval_service or ApprovalService(default_approval_lifecycle_path())

    async def start_isolated_workflow(
        self,
        *,
        spec: dict[str, Any],
        user_id: str,
        conversation_id: str | None,
        input_payload: dict[str, Any] | None = None,
        tool_invoker: SkillToolInvoker | None = None,
        timeout_seconds: float = 60.0,
    ) -> tuple[SkillWorkflowRun, list[SkillWorkflowEvent]]:
        """Starts an enabled isolated-process skill workflow and consumes its first event run."""

        skill_id = str(spec.get("skill_id", "")).strip()
        if not skill_id:
            raise ValueError("Workflow spec must include skill_id.")
        run = self.start_run(
            skill_id=skill_id,
            user_id=user_id,
            conversation_id=conversation_id,
            workflow_name=str(spec.get("skill_name", skill_id)).strip(),
            metadata={
                "mode": str(spec.get("mode", "")).strip(),
                "protocol_version": str(spec.get("protocol_version", "")).strip(),
                "source_path": str(spec.get("source_path", "")).strip(),
            },
        )
        message = {
            "type": "start",
            "run_id": run.run_id,
            "skill_id": run.skill_id,
            "conversation_id": run.conversation_id,
            "user_id": run.user_id,
            "input": input_payload if isinstance(input_payload, dict) else {},
        }
        events = await self.run_isolated_worker_message(
            spec=spec,
            run=run,
            message=message,
            tool_invoker=tool_invoker,
            timeout_seconds=timeout_seconds,
        )
        return self.store.get_run(run.run_id) or run, events

    async def resume_isolated_workflow(
        self,
        *,
        spec: dict[str, Any],
        run_id: str,
        request_id: str,
        response: HumanApprovalResponse,
        tool_invoker: SkillToolInvoker | None = None,
        timeout_seconds: float = 60.0,
    ) -> tuple[SkillWorkflowRun, list[SkillWorkflowEvent]]:
        """Resolves a HITL request and resumes the isolated worker when approved."""

        run = self.store.get_run(run_id)
        if run is None:
            raise ValueError(f"Unknown skill workflow run: {run_id}")
        current_request = self.store.get_request(request_id)
        if current_request is None or current_request.request.run_id != run.run_id:
            raise ValueError(f"Unknown pending workflow request: {request_id}")
        if current_request.status != "pending":
            return run, [
                SkillWorkflowEvent(
                    type="status",
                    run_id=run.run_id,
                    skill_id=run.skill_id,
                    data={"status": "already_processed", "request_status": current_request.status},
                )
            ]

        pending = self.resolve_human_approval(
            run_id=run.run_id,
            request_id=request_id,
            response=response,
        )
        run = self.store.get_run(run.run_id) or run
        if not response.approved:
            return run, [
                SkillWorkflowEvent(
                    type="completed",
                    run_id=run.run_id,
                    skill_id=run.skill_id,
                    data={"status": "rejected", "request": asdict(pending.request)},
                )
            ]

        events = await self.run_isolated_worker_message(
            spec=spec,
            run=run,
            message={
                "type": "resume",
                "run_id": run.run_id,
                "skill_id": run.skill_id,
                "conversation_id": run.conversation_id,
                "user_id": run.user_id,
                "checkpoint_id": run.checkpoint_id,
                "request": asdict(pending.request),
                "response": asdict(response),
            },
            tool_invoker=tool_invoker,
            timeout_seconds=timeout_seconds,
        )
        terminal_event = next((event for event in reversed(events) if event.type in {"completed", "failed"}), None)
        if terminal_event is not None:
            if terminal_event.type == "completed":
                self.approval_service.mark_completed(request_id)
            else:
                self.approval_service.mark_failed(request_id)
        return self.store.get_run(run.run_id) or run, events

    async def run_isolated_worker_message(
        self,
        *,
        spec: dict[str, Any],
        run: SkillWorkflowRun,
        message: dict[str, Any],
        tool_invoker: SkillToolInvoker | None = None,
        timeout_seconds: float = 60.0,
    ) -> list[SkillWorkflowEvent]:
        """Runs one JSON-lines exchange with an isolated marketplace workflow worker."""

        if str(spec.get("mode", "")).strip() != "isolated_process":
            raise ValueError("Only isolated_process skill workflows can be launched.")
        command = str(spec.get("command", "")).strip()
        if not command:
            raise ValueError("Workflow spec must include command.")
        args = [str(arg) for arg in spec.get("args", []) if str(arg).strip()] if isinstance(spec.get("args", []), list) else []
        cwd = str(spec.get("cwd", "")).strip() or None
        env = os.environ.copy()
        if isinstance(spec.get("env"), dict):
            env.update({str(key): str(value) for key, value in spec["env"].items()})
        env.setdefault(
            "AZUL_SKILL_WORKFLOW_CHECKPOINT_DIR",
            str((self.store.path.parent / "skill_workflow_checkpoints" / run.skill_id).resolve()),
        )
        env.setdefault("AZUL_SKILL_WORKFLOW_RUN_ID", run.run_id)

        process_kwargs: dict[str, Any] = {}
        if os.name == "nt":
            process_kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        process = subprocess.Popen(
            [command, *args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
            env=env,
            text=True,
            encoding="utf-8",
            **process_kwargs,
        )

        events: list[SkillWorkflowEvent] = []
        try:
            await self._write_worker_message(process, message)
            while True:
                if process.stdout is None:
                    break
                raw_line = await asyncio.wait_for(
                    asyncio.to_thread(process.stdout.readline),
                    timeout=timeout_seconds,
                )
                if not raw_line:
                    break
                worker_event = self._decode_worker_event(raw_line)
                event_type = str(worker_event.get("type", "")).strip()
                if event_type == "tool_call":
                    await self._handle_worker_tool_call(
                        process=process,
                        spec=spec,
                        run=run,
                        event=worker_event,
                        tool_invoker=tool_invoker,
                    )
                    continue
                if event_type == "request_info":
                    request = self._handle_worker_request_info(run, worker_event)
                    events.append(
                        SkillWorkflowEvent(
                            type="request_info",
                            run_id=run.run_id,
                            skill_id=run.skill_id,
                            data=asdict(request),
                        )
                    )
                    break
                if event_type in {"delta", "status", "completed", "failed"}:
                    events.append(
                        SkillWorkflowEvent(
                            type=event_type,  # type: ignore[arg-type]
                            run_id=run.run_id,
                            skill_id=run.skill_id,
                            data=worker_event.get("data") if isinstance(worker_event.get("data"), dict) else worker_event,
                        )
                    )
                    if event_type in {"completed", "failed"}:
                        self._mark_run_terminal(run, event_type, worker_event)
                        break
                    continue
                events.append(
                    SkillWorkflowEvent(
                        type="status",
                        run_id=run.run_id,
                        skill_id=run.skill_id,
                        data={"status": "ignored-worker-event", "worker_event": worker_event},
                    )
                )
        except Exception as error:
            self._mark_run_failed(run, str(error))
            raise
        finally:
            await self._close_worker(process, timeout_seconds=3.0)
        return events

    def start_run(
        self,
        *,
        skill_id: str,
        user_id: str,
        conversation_id: str | None,
        workflow_name: str = "",
        checkpoint_id: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> SkillWorkflowRun:
        now = _now()
        run = SkillWorkflowRun(
            run_id=str(uuid4()),
            skill_id=str(skill_id).strip(),
            user_id=str(user_id).strip(),
            conversation_id=str(conversation_id or "").strip(),
            status="started",
            created_at=now,
            updated_at=now,
            checkpoint_id=str(checkpoint_id).strip(),
            workflow_name=str(workflow_name).strip(),
            metadata=metadata or {},
        )
        return self.store.upsert_run(run)

    async def _write_worker_message(
        self,
        process: subprocess.Popen[str],
        message: dict[str, Any],
    ) -> None:
        if process.stdin is None:
            raise ValueError("Workflow worker stdin is unavailable.")
        line = json.dumps(message, ensure_ascii=False) + "\n"

        def _write() -> None:
            assert process.stdin is not None
            process.stdin.write(line)
            process.stdin.flush()

        await asyncio.to_thread(_write)

    def _decode_worker_event(self, raw_line: str | bytes) -> dict[str, Any]:
        try:
            text = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else raw_line
            event = json.loads(text)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError(f"Workflow worker emitted invalid JSON: {error}") from error
        if not isinstance(event, dict):
            raise ValueError("Workflow worker event must be a JSON object.")
        return event

    async def _handle_worker_tool_call(
        self,
        *,
        process: subprocess.Popen[str],
        spec: dict[str, Any],
        run: SkillWorkflowRun,
        event: dict[str, Any],
        tool_invoker: SkillToolInvoker | None,
    ) -> None:
        call_id = str(event.get("id", "")).strip() or str(uuid4())
        tool_ref = str(event.get("tool", "")).strip()
        arguments = event.get("arguments") if isinstance(event.get("arguments"), dict) else {}
        tools = spec.get("tools", {}) if isinstance(spec.get("tools", {}), dict) else {}
        allowed_tools = {str(key).strip(): str(value).strip() for key, value in tools.items() if str(key).strip()}
        tool_name = allowed_tools.get(tool_ref, tool_ref)
        if tool_name not in set(allowed_tools.values()):
            await self._write_worker_message(
                process,
                {"type": "tool_result", "id": call_id, "ok": False, "error": f"Tool '{tool_ref}' is not declared."},
            )
            return
        policy = self._tool_policy_for(spec, tool_ref=tool_ref, tool_name=tool_name)
        sensitive_action = str(policy.get("sensitive_action", "")).strip()
        if bool(policy.get("requires_approval", False)) and not self._sensitive_tool_call_is_approved(
            run=run,
            sensitive_action=sensitive_action,
        ):
            await self._write_worker_message(
                process,
                {
                    "type": "tool_result",
                    "id": call_id,
                    "ok": False,
                    "error": f"Tool '{tool_ref}' requires approved HITL action '{sensitive_action}'.",
                },
            )
            return
        if tool_invoker is None:
            await self._write_worker_message(
                process,
                {"type": "tool_result", "id": call_id, "ok": False, "error": "No tool invoker is configured."},
            )
            return
        try:
            result = await tool_invoker(tool_name, arguments)
        except Exception as error:
            await self._write_worker_message(
                process,
                {"type": "tool_result", "id": call_id, "ok": False, "error": str(error)},
            )
            return
        await self._write_worker_message(
            process,
            {"type": "tool_result", "id": call_id, "ok": True, "result": result},
        )

    def _tool_policy_for(
        self,
        spec: dict[str, Any],
        *,
        tool_ref: str,
        tool_name: str,
    ) -> dict[str, Any]:
        policies = spec.get("tool_policies", {}) if isinstance(spec.get("tool_policies", {}), dict) else {}
        for key in (tool_ref, tool_name):
            policy = policies.get(key)
            if isinstance(policy, dict):
                return policy
        return {}

    def _sensitive_tool_call_is_approved(
        self,
        *,
        run: SkillWorkflowRun,
        sensitive_action: str,
    ) -> bool:
        action = str(sensitive_action or "").strip()
        if not action:
            return False
        stored_run = self.store.get_run(run.run_id) or run
        if stored_run.status != "approved":
            return False
        for item in self.store.load()["requests"]:
            if not isinstance(item, dict):
                continue
            request = item.get("request")
            if not isinstance(request, dict):
                continue
            if str(request.get("run_id", "")).strip() != stored_run.run_id:
                continue
            if str(request.get("action_kind", "")).strip() != action:
                continue
            if str(item.get("status", "")).strip() == "approved":
                return True
        return False

    def _handle_worker_request_info(
        self,
        run: SkillWorkflowRun,
        event: dict[str, Any],
    ) -> HumanApprovalRequest:
        request = event.get("request") if isinstance(event.get("request"), dict) else {}
        checkpoint_id = str(request.get("checkpoint_id", event.get("checkpoint_id", ""))).strip()
        if checkpoint_id:
            run.checkpoint_id = checkpoint_id
            run.metadata = {**run.metadata, "checkpoint_id": checkpoint_id}
            self.store.upsert_run(run)
        return self.request_human_approval(
            run_id=run.run_id,
            request_id=str(request.get("request_id", "")).strip() or str(event.get("request_id", "")).strip() or None,
            action_kind=str(request.get("action_kind", event.get("action_kind", ""))).strip(),
            title=str(request.get("title", event.get("title", ""))).strip(),
            summary=str(request.get("summary", event.get("summary", ""))).strip(),
            payload=request.get("payload") if isinstance(request.get("payload"), dict) else {},
            risk=str(request.get("risk", event.get("risk", "medium"))).strip() or "medium",
            labels=request.get("labels") if isinstance(request.get("labels"), dict) else {},
        )

    def _mark_run_terminal(
        self,
        run: SkillWorkflowRun,
        event_type: str,
        event: dict[str, Any],
    ) -> None:
        if event_type == "completed":
            now = _now()
            run.status = "completed"
            run.pending_request_id = ""
            run.updated_at = now
            run.metadata = {**run.metadata, "completed": event.get("data", event)}
            self.store.upsert_run(run)
        else:
            self._mark_run_failed(run, str(event.get("error", "")) or "Workflow worker failed.")

    def _mark_run_failed(self, run: SkillWorkflowRun, reason: str) -> None:
        now = _now()
        run.status = "failed"
        run.updated_at = now
        run.metadata = {**run.metadata, "failure": reason}
        self.store.upsert_run(run)

    async def _close_worker(
        self,
        process: subprocess.Popen[str],
        *,
        timeout_seconds: float,
    ) -> None:
        if process.stdin is not None and not process.stdin.closed:
            process.stdin.close()
        try:
            await asyncio.wait_for(asyncio.to_thread(process.wait), timeout=timeout_seconds)
        except asyncio.TimeoutError:
            process.kill()
            await asyncio.to_thread(process.wait)
        finally:
            for stream in (process.stdout, process.stderr):
                if stream is not None and not stream.closed:
                    stream.close()

    def request_human_approval(
        self,
        *,
        run_id: str,
        action_kind: str,
        title: str,
        summary: str,
        payload: dict[str, Any] | None = None,
        risk: str = "medium",
        labels: dict[str, str] | None = None,
        request_id: str | None = None,
    ) -> HumanApprovalRequest:
        run = self.store.get_run(run_id)
        if run is None:
            raise ValueError(f"Unknown skill workflow run: {run_id}")
        request = HumanApprovalRequest(
            request_id=str(request_id or uuid4()),
            run_id=run.run_id,
            skill_id=run.skill_id,
            action_kind=str(action_kind).strip(),
            title=str(title).strip(),
            summary=str(summary).strip(),
            payload=payload or {},
            risk=str(risk).strip() or "medium",
            labels=labels or {},
        )
        now = _now()
        pending = SkillWorkflowPendingRequest(
            request=request,
            status="pending",
            created_at=now,
            updated_at=now,
        )
        self.store.upsert_request(pending)
        run.status = _workflow_waiting_status()
        run.pending_request_id = request.request_id
        run.updated_at = now
        self.store.upsert_run(run)
        self.approval_service.register_pending(
            action_id=request.request_id,
            user_id=run.user_id,
            conversation_id=run.conversation_id,
            source="skill_workflow",
            action_kind=request.action_kind,
            title=request.title,
            summary=request.summary,
            idempotency_key=request.request_id,
            metadata={
                "run_id": run.run_id,
                "skill_id": run.skill_id,
                "risk": request.risk,
                "payload": request.payload,
                "labels": request.labels,
            },
            supersede_existing=True,
            supersede_scope="conversation",
        )
        return request

    def resolve_human_approval(
        self,
        *,
        run_id: str,
        request_id: str,
        response: HumanApprovalResponse,
    ) -> SkillWorkflowPendingRequest:
        run = self.store.get_run(run_id)
        if run is None:
            raise ValueError(f"Unknown skill workflow run: {run_id}")
        pending = self.store.get_request(request_id)
        if pending is None or pending.request.run_id != run.run_id:
            raise ValueError(f"Unknown pending workflow request: {request_id}")
        if pending.status != "pending":
            return pending

        now = _now()
        pending.response = response
        pending.status = "approved" if response.approved else "rejected"
        pending.updated_at = now
        self.store.upsert_request(pending)

        run.pending_request_id = ""
        run.status = "approved" if response.approved else "rejected"
        run.updated_at = now
        self.store.upsert_run(run)

        if response.approved:
            self.approval_service.mark_approved(request_id)
        else:
            self.approval_service.mark_rejected(request_id)
        return pending
