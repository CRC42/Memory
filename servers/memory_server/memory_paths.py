from __future__ import annotations

import fnmatch
import os
from pathlib import Path
from typing import Iterable

from .memory_config import MemoryConfig


class PathSecurityError(ValueError):
    pass


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


class PathManager:
    def __init__(self, config: MemoryConfig) -> None:
        self.config = config

    def resolve(
        self,
        path_value: str,
        *,
        must_exist: bool = True,
        must_be_file: bool = True,
    ) -> Path:
        candidate = Path(path_value)
        if candidate.is_absolute():
            resolved = candidate.resolve()
        else:
            resolved = (self.config.repo_root / candidate).resolve()

        if not any(_is_within(resolved, root) for root in self.config.allowed_roots):
            raise PathSecurityError(f"path is outside allowed_roots: {path_value}")
        if must_exist and not resolved.exists():
            raise FileNotFoundError(f"path does not exist: {path_value}")
        if must_be_file and resolved.exists() and not resolved.is_file():
            raise IsADirectoryError(f"path is not a file: {path_value}")
        return resolved

    def to_repo_relative(self, path: Path) -> str:
        return path.resolve().relative_to(self.config.repo_root).as_posix()

    def _is_excluded(self, repo_rel_path: str) -> bool:
        normalized = repo_rel_path.replace("\\", "/").strip("/")
        for excluded in self.config.excluded_dirs:
            prefix = excluded.strip("/")
            if normalized == prefix or normalized.startswith(prefix + "/"):
                return True
        return False

    def _matches_patterns(self, repo_rel_path: str, patterns: list[str] | None) -> bool:
        if not patterns:
            return True
        normalized = repo_rel_path.replace("\\", "/")
        return any(fnmatch.fnmatch(normalized, pattern) for pattern in patterns)

    def _scope_roots(self, scopes: list[str] | None) -> list[Path]:
        if not scopes:
            return [root for root in self.config.allowed_roots if root.exists()]
        resolved: list[Path] = []
        for scope in scopes:
            scope_path = self.resolve(scope, must_exist=True, must_be_file=False)
            resolved.append(scope_path)
        return resolved

    def iter_files(
        self,
        *,
        scopes: list[str] | None = None,
        include_paths: list[str] | None = None,
        exclude_paths: list[str] | None = None,
    ) -> Iterable[tuple[Path, str]]:
        for root in self._scope_roots(scopes):
            if root.is_file():
                rel_path = self.to_repo_relative(root)
                if self._is_excluded(rel_path):
                    continue
                if not self._matches_patterns(rel_path, include_paths):
                    continue
                if exclude_paths and self._matches_patterns(rel_path, exclude_paths):
                    continue
                yield root, rel_path
                continue

            for current_dir, dirs, files in os.walk(root):
                current = Path(current_dir)
                kept_dirs: list[str] = []
                for dir_name in dirs:
                    candidate_dir = (current / dir_name).resolve()
                    rel_dir = self.to_repo_relative(candidate_dir)
                    if not self._is_excluded(rel_dir):
                        kept_dirs.append(dir_name)
                dirs[:] = kept_dirs

                for file_name in files:
                    abs_path = (current / file_name).resolve()
                    rel_path = self.to_repo_relative(abs_path)
                    if self._is_excluded(rel_path):
                        continue
                    if not self._matches_patterns(rel_path, include_paths):
                        continue
                    if exclude_paths and self._matches_patterns(rel_path, exclude_paths):
                        continue
                    yield abs_path, rel_path
