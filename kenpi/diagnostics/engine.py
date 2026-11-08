"""Diagnostic engine: checks -> logs -> faults -> ranked causes -> guidance -> verification."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from kenpi.diagnostics.checks import CheckRegistry, default_registry
from kenpi.diagnostics.faults import detect_faults
from kenpi.diagnostics.history import DiagnosticHistory
from kenpi.diagnostics.issues import IssueDatabase, required_permission_for_step
from kenpi.diagnostics.logs import LogAnalysis, analyze
from kenpi.diagnostics.models import (
    Cause,
    CheckResult,
    DiagnosticContext,
    Status,
    Subsystem,
)
from kenpi.diagnostics.remediation import RemediationRegistry, RemediationResult
from kenpi.diagnostics.root_cause import rank


@dataclass(frozen=True, slots=True)
class Report:
    generated_at: float
    results: tuple[CheckResult, ...]
    causes: tuple[Cause, ...]
    log_summary: dict[str, Any] | None

    @property
    def subsystem_status(self) -> dict[str, str]:
        """Worst known status per subsystem; UNKNOWN only when nothing is known."""
        order = {Status.OK: 0, Status.UNKNOWN: 1, Status.WARN: 2, Status.FAIL: 3}
        worst: dict[str, Status] = {}
        for result in self.results:
            current = worst.get(result.subsystem.value)
            if current is None or order[result.status] > order[current]:
                worst[result.subsystem.value] = result.status
        return {key: value.value for key, value in worst.items()}

    def fault_ids(self) -> set[str]:
        return {
            cause.fault.id
            for cause in self.causes
            if cause.fault.severity is not Status.UNKNOWN
        }

    def describe(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "subsystems": self.subsystem_status,
            "checks": [r.describe() for r in self.results],
            "causes": [c.describe() for c in self.causes],
            "logs": self.log_summary,
        }

    def render_text(self) -> str:
        lines = ["Kenpi system diagnostics"]
        for subsystem, status in sorted(self.subsystem_status.items()):
            lines.append(f"  {subsystem:<9} {status}")
        real = [c for c in self.causes if c.fault.severity is not Status.UNKNOWN]
        if real:
            lines.append("Findings (most likely first):")
            for cause in real:
                lines.append(f"  [{cause.confidence:.0%}] {cause.fault.summary}")
                lines.extend(f"      evidence: {item}" for item in cause.fault.evidence)
        else:
            lines.append("No faults detected in the available evidence.")
        unverified = [c for c in self.causes if c.fault.severity is Status.UNKNOWN]
        if unverified:
            lines.append("Not verified (no evidence either way):")
            lines.extend(f"  {c.fault.summary}" for c in unverified)
        return "\n".join(lines)


class DiagnosticsEngine:
    def __init__(
        self,
        context: DiagnosticContext,
        registry: CheckRegistry | None = None,
        issues: IssueDatabase | None = None,
        remediation: RemediationRegistry | None = None,
        history: DiagnosticHistory | None = None,
    ) -> None:
        self._context = context
        self._registry = registry or default_registry()
        self.issues = issues or IssueDatabase()
        self.remediation = remediation or RemediationRegistry()
        self.history = history or DiagnosticHistory()

    def run(self) -> Report:
        results = self._registry.run(self._context)
        analysis: LogAnalysis | None = None
        if self._context.log_lines is not None:
            try:
                analysis = analyze(self._context.log_lines())
            except Exception:
                analysis = None
        causes = rank(detect_faults(results, analysis))
        summary = (
            {
                "entries": analysis.entries,
                "errors": analysis.error_count,
                "signatures": analysis.signatures,
            }
            if analysis
            else None
        )
        report = Report(self._context.clock(), tuple(results), tuple(causes), summary)
        self.history.record(
            "report",
            report.generated_at,
            subsystems=report.subsystem_status,
            faults=sorted(report.fault_ids()),
        )
        return report

    def guide(self, fault_id: str) -> dict[str, Any] | None:
        issue = self.issues.get(fault_id)
        if issue is None:
            return None
        permissions = self.remediation.permissions()
        steps = []
        for step in issue.steps:
            level = required_permission_for_step(step, permissions)
            steps.append({**step.describe(), "permission": level.name})
        return {**issue.describe(), "steps": steps}

    def remediate(
        self, action_id: str, approved: bool = False
    ) -> tuple[RemediationResult, Report | None]:
        """Run an approved action, then re-check so success is verified, not assumed."""
        before = self.run().fault_ids()
        result = self.remediation.execute(action_id, approved)
        self.history.record("remediation", self._context.clock(), **result.describe())
        if not result.executed:
            return result, None
        after = self.run()
        action = self.remediation.get(action_id)
        targeted = set(action.fixes) if action else set()
        self.history.record(
            "verification",
            self._context.clock(),
            action_id=action_id,
            resolved=sorted(before - after.fault_ids()),
            remaining=sorted(after.fault_ids()),
            # An executed action is not a fix: these targeted faults are still present.
            targeted_still_present=sorted(targeted & after.fault_ids()),
        )
        return result, after

    def explain(self, subsystem: Subsystem) -> str:
        results = [
            r for r in self._registry.run(self._context) if r.subsystem is subsystem
        ]
        if not results:
            return f"There is no evidence about {subsystem.value}."
        return "; ".join(f"{r.name}: {r.detail}" for r in results)
