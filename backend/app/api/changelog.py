"""更新日志API。外部 GitHub 拉取已禁用。"""
from fastapi import APIRouter, Query
from typing import List, Optional
from pydantic import BaseModel
import logging

logger = logging.getLogger(__name__)

router = APIRouter()

class GitHubAuthor(BaseModel):
    """GitHub作者信息"""
    name: str
    email: str
    date: str


class GitHubCommitInfo(BaseModel):
    """GitHub提交信息"""
    author: GitHubAuthor
    message: str


class GitHubUser(BaseModel):
    """GitHub用户信息"""
    login: str
    avatar_url: str


class GitHubCommit(BaseModel):
    """GitHub提交数据"""
    sha: str
    commit: GitHubCommitInfo
    html_url: str
    author: Optional[GitHubUser] = None


class ChangelogResponse(BaseModel):
    """更新日志响应"""
    commits: List[GitHubCommit]
    cached: bool
    cache_time: Optional[str] = None


async def fetch_github_commits(page: int = 1, per_page: int = 30) -> List[dict]:
    """外部更新日志拉取已禁用。"""
    _ = (page, per_page)
    return []


@router.get("/changelog", response_model=ChangelogResponse)
async def get_changelog(
    page: int = Query(1, ge=1, description="页码"),
    per_page: int = Query(30, ge=1, le=100, description="每页数量")
):
    """
    获取更新日志
    
    外部更新日志拉取已禁用，固定返回空列表。
    
    - **page**: 页码，从1开始
    - **per_page**: 每页返回的提交数量，最大100
    """
    _ = (page, per_page)
    return ChangelogResponse(commits=[], cached=False, cache_time=None)


@router.post("/changelog/refresh")
async def refresh_changelog():
    """
    刷新更新日志缓存
    
    外部更新日志拉取已禁用。
    """
    logger.info("更新日志外部拉取已禁用，跳过刷新")
    return {
        "success": True,
        "message": "更新日志外部拉取已禁用",
        "commit_count": 0,
        "cache_time": None,
    }
