# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

from __future__ import annotations

import os
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from git import Repo
from git.exc import BadName

from airflow.dag_processing.bundles.base import BaseDagBundle
from airflow.exceptions import AirflowException

if TYPE_CHECKING:
    from pathlib import Path


class GitDagBundle(BaseDagBundle):
    """
    git DAG bundle - exposes a git repository as a DAG bundle.

    Instead of cloning the repository every time, we clone the repository once into a bare repo from the source
    and then do a clone for each version from there.

    :param repo_url: URL of the git repository
    :param tracking_ref: Branch or tag for this DAG bundle
    :param subdir: Subdirectory within the repository where the DAGs are stored (Optional)
    """

    supports_versioning = True

    def __init__(self, *, repo_url: str, tracking_ref: str, subdir: str | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.repo_url = repo_url
        self.tracking_ref = tracking_ref
        self.subdir = subdir

        self.bare_repo_path = self._dag_bundle_root_storage_path / "git" / self.name
        self.repo_path = (
            self._dag_bundle_root_storage_path / "git" / (self.name + f"+{self.version or self.tracking_ref}")
        )
        self._clone_bare_repo_if_required()
        self._ensure_version_in_bare_repo()
        self._clone_repo_if_required()
        self.repo.git.checkout(self.tracking_ref)

        if self.version:
            if not self._has_version(self.repo, self.version):
                self.repo.remotes.origin.fetch()

            self.repo.head.set_reference(self.repo.commit(self.version))
            self.repo.head.reset(index=True, working_tree=True)
        else:
            self.refresh()

    def _clone_repo_if_required(self) -> None:
        if not os.path.exists(self.repo_path):
            Repo.clone_from(
                url=self.bare_repo_path,
                to_path=self.repo_path,
            )
        self.repo = Repo(self.repo_path)

    def _clone_bare_repo_if_required(self) -> None:
        if not os.path.exists(self.bare_repo_path):
            Repo.clone_from(
                url=self.repo_url,
                to_path=self.bare_repo_path,
                bare=True,
            )
        self.bare_repo = Repo(self.bare_repo_path)

    def _ensure_version_in_bare_repo(self) -> None:
        if not self.version:
            return
        if not self._has_version(self.bare_repo, self.version):
            self.bare_repo.remotes.origin.fetch("+refs/heads/*:refs/heads/*")
            if not self._has_version(self.bare_repo, self.version):
                raise AirflowException(f"Version {self.version} not found in the repository")

    def __repr__(self):
        return (
            f"<GitDagBundle("
            f"name={self.name!r}, "
            f"tracking_ref={self.tracking_ref!r}, "
            f"subdir={self.subdir!r}, "
            f"version={self.version!r}"
            f")>"
        )

    def get_current_version(self) -> str:
        return self.repo.head.commit.hexsha

    @property
    def path(self) -> Path:
        if self.subdir:
            return self.repo_path / self.subdir
        return self.repo_path

    @staticmethod
    def _has_version(repo: Repo, version: str) -> bool:
        try:
            repo.commit(version)
            return True
        except BadName:
            return False

    def refresh(self) -> None:
        if self.version:
            raise AirflowException("Refreshing a specific version is not supported")

        self.bare_repo.remotes.origin.fetch("+refs/heads/*:refs/heads/*")
        self.repo.remotes.origin.pull()

    def _convert_git_ssh_url_to_https(self) -> str:
        if not self.repo_url.startswith("git@"):
            raise ValueError(f"Invalid git SSH URL: {self.repo_url}")
        parts = self.repo_url.split(":")
        domain = parts[0].replace("git@", "https://")
        repo_path = parts[1].replace(".git", "")
        return f"{domain}/{repo_path}"

    def view_url(self, version: str | None = None) -> str | None:
        if not version:
            return None
        url = self.repo_url
        if url.startswith("git@"):
            url = self._convert_git_ssh_url_to_https()
        parsed_url = urlparse(url)
        host = parsed_url.hostname
        if not host:
            return None
        host_patterns = {
            "github.com": f"{url}/tree/{version}",
            "gitlab.com": f"{url}/-/tree/{version}",
            "bitbucket.org": f"{url}/src/{version}",
        }
        for allowed_host, template in host_patterns.items():
            if host == allowed_host or host.endswith(f".{allowed_host}"):
                return template
        return None
