# Copyright 2026 Transcription Module contributors

"""Typed revocation of authority derived from subtitle policy identity."""

from __future__ import annotations

from typing import TYPE_CHECKING

from transcription_module._policy_authority import (
    POLICY_DEPENDENT_DIRECTORY_NAMES,
    policy_dependent_relative_paths,
    remove_empty_policy_authority_directories,
)
from transcription_module.models import (
    FrozenModel,
    RunId,
    ValidationStatus,
    WorkflowStatus,
)

if TYPE_CHECKING:
    from pathlib import Path

    from transcription_module.storage import RunPaths, RunStore


class PolicyInvalidationReceipt(FrozenModel):
    """Result of removing policy-derived run artifacts."""

    run_id: RunId
    removed_paths: tuple[str, ...]
    prior_workflow_status: WorkflowStatus
    prior_validation_status: ValidationStatus
    workflow_status: WorkflowStatus
    validation_status: ValidationStatus


def policy_dependent_authority_paths(paths: RunPaths) -> tuple[Path, ...]:
    """Return the command-owned bounded policy authority paths for one run."""
    return tuple(
        paths.run_dir.joinpath(*relative.split("/"))
        for relative in policy_dependent_relative_paths(paths.run_dir)
    )


def revoke_policy_dependents(
    store: RunStore,
    run_id: RunId,
) -> PolicyInvalidationReceipt:
    """Remove policy-derived artifacts and return the lifecycle transition."""
    with store.lock_run(run_id):
        state = store.load_state(run_id)
        relative_paths = policy_dependent_relative_paths(
            store.paths(run_id).run_dir
        )
        removed = tuple(
            relative
            for relative in relative_paths
            if store.paths(run_id).run_dir.joinpath(*relative.split("/")).exists()
        )
        for relative in removed:
            store.paths(run_id).run_dir.joinpath(*relative.split("/")).unlink()
        remove_empty_policy_authority_directories(store.paths(run_id).run_dir)
        updated = state.model_copy(
            update={
                "workflow_status": WorkflowStatus.PROCESSING,
                "validation_status": ValidationStatus.PENDING,
            }
        )
        store.save_state(updated)
    return PolicyInvalidationReceipt(
        run_id=run_id,
        removed_paths=removed,
        prior_workflow_status=state.workflow_status,
        prior_validation_status=state.validation_status,
        workflow_status=updated.workflow_status,
        validation_status=updated.validation_status,
    )


__all__ = [
    "POLICY_DEPENDENT_DIRECTORY_NAMES",
    "PolicyInvalidationReceipt",
    "policy_dependent_authority_paths",
    "revoke_policy_dependents",
]
