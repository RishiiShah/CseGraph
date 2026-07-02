from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

from csegraph._core.core.models import (
    ContextRequest,
    ContextResponse,
    ContextResult,
    GraphResult,
    IndexResult,
    PathResult,
    RefreshResult,
)
from csegraph._core.graph.queries import GraphQueryService
from csegraph._core.index.services import IndexService, RefreshService
from csegraph._core.retrieval.context import ContextService

__all__ = [
    "AsyncContextService",
    "AsyncGraphQueryService",
    "AsyncIndexService",
    "AsyncRefreshService",
]


class AsyncIndexService:
    def __init__(self, db_path: str | Path):
        self._sync = IndexService(db_path)
        self.db_path = self._sync.db_path

    async def index(
        self,
        repo: str | Path,
        profile: str = "small",
        *,
        exclude_patterns: Optional[Sequence[str]] = None,
        include_roots: Optional[Sequence[str | Path]] = None,
    ) -> IndexResult:
        return await asyncio.to_thread(
            self._sync.index,
            repo,
            profile,
            exclude_patterns=exclude_patterns,
            include_roots=include_roots,
        )


class AsyncRefreshService:
    def __init__(self, db_path: str | Path):
        self._sync = RefreshService(db_path)
        self.db_path = self._sync.db_path

    async def refresh(
        self,
        profile: str = "small",
        changed_paths: Optional[Iterable[str | Path]] = None,
        dependents_limit: int = 50,
        *,
        exclude_patterns: Optional[Sequence[str]] = None,
        include_roots: Optional[Sequence[str | Path]] = None,
    ) -> RefreshResult:
        return await asyncio.to_thread(
            self._sync.refresh,
            profile,
            changed_paths,
            dependents_limit,
            exclude_patterns=exclude_patterns,
            include_roots=include_roots,
        )


class AsyncContextService:
    def __init__(self, db_path: str | Path):
        self._sync = ContextService(db_path)
        self.db_path = self._sync.db_path

    async def retrieve(self, request: ContextRequest) -> ContextResponse:
        return await asyncio.to_thread(self._sync.retrieve, request)

    async def build_context(
        self,
        task: str,
        target: Optional[str] = None,
        profile: Optional[str] = None,
        include_source: str = "auto",
        max_tokens: Optional[int] = None,
        explain: bool = False,
        config_path: Optional[str] = None,
        detail_level: str = "auto",
        task_kind: str = "auto",
    ) -> ContextResult:
        return await asyncio.to_thread(
            self._sync.build_context,
            task,
            target=target,
            profile=profile,
            include_source=include_source,
            max_tokens=max_tokens,
            explain=explain,
            config_path=config_path,
            detail_level=detail_level,
            task_kind=task_kind,
        )


class AsyncGraphQueryService:
    def __init__(self, db_path: str | Path):
        self._sync = GraphQueryService(db_path)
        self.db_path = self._sync.db_path

    async def neighborhood(
        self,
        node_id: str,
        depth: int = 1,
        detail_level: str = "minimal",
        relations: Optional[List[str]] = None,
        confidence_tiers: Optional[List[str]] = None,
    ) -> GraphResult:
        return await asyncio.to_thread(
            self._sync.neighborhood,
            node_id,
            depth=depth,
            detail_level=detail_level,
            relations=relations,
            confidence_tiers=confidence_tiers,
        )

    async def shortest_path(
        self,
        source: str,
        target: str,
        detail_level: str = "minimal",
        relations: Optional[List[str]] = None,
        confidence_tiers: Optional[List[str]] = None,
        max_depth: int = 15,
    ) -> PathResult:
        return await asyncio.to_thread(
            self._sync.shortest_path,
            source,
            target,
            detail_level=detail_level,
            relations=relations,
            confidence_tiers=confidence_tiers,
            max_depth=max_depth,
        )
