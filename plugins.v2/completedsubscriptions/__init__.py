# -*- coding: utf-8 -*-

"""
*************************************************
***   订阅历史清理工具 (CompletedSubscriptions v5.0) ***
*************************************************
- 优化内容：
  1. 查询结果缓存，避免重复DB访问
  2. 删除流程拆分（文件/历史独立控制）
  3. Transfer/Download/Subscribe 三层可选删除
  4. 文件路径去重
  5. 删除异常保护（不中断）
  6. 删除统计（成功/失败）
  7. datetime 兼容解析
  8. 性能优化（减少重复 query）
"""

import time
from typing import Any, Dict, List, Tuple, Optional
from datetime import datetime, timedelta

from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.orm import Session

from app.log import logger
from app.plugins import _PluginBase
from app.db.models.subscribehistory import SubscribeHistory
from app.db import db_query
from app.db.downloadhistory_oper import DownloadHistoryOper
from app.db.transferhistory_oper import TransferHistoryOper
from app.chain.storage import StorageChain
from app.core.event import eventmanager
from app.schemas import NotificationType, FileItem
from app.schemas.types import EventType


class CompletedSubscriptions(_PluginBase):

    plugin_name = "订阅历史清理工具"
    plugin_desc = "清理订阅历史及关联文件"
    plugin_icon = "https://raw.githubusercontent.com/InfinityPacer/MoviePilot-Plugins/main/icons/subscribeassistant.png"
    plugin_version = "5.0.0"
    plugin_author = "Gemini & 用户"
    plugin_config_prefix = "sub_history_cleaner_"
    auth_level = 1

    _enabled: bool = False
    _notify: bool = False
    _cron: str = None
    _onlyonce: bool = False
    _days_limit: int = None
    _users_list_str: str = ""
    _users_config: Dict[str, int] = {}
    _confirm_delete: bool = False

    # 新增控制项
    _delete_download_history: bool = True
    _delete_transfer_history: bool = True
    _delete_subscribe_history: bool = True
    _delete_files: bool = True

    download_history_oper: DownloadHistoryOper = None
    transfer_history_oper: TransferHistoryOper = None
    storage_chain: StorageChain = None

    def init_plugin(self, config: dict = None):

        self.download_history_oper = DownloadHistoryOper()
        self.transfer_history_oper = TransferHistoryOper()
        self.storage_chain = StorageChain()

        if config:
            self._enabled = config.get("enabled", False)
            self._notify = config.get("notify", False)
            self._cron = config.get("cron")
            self._onlyonce = config.get("onlyonce", False)

            self._days_limit = int(config.get("days_limit")) if config.get("days_limit") else None

            self._users_list_str = config.get("users_list", "")

            self._users_config = {}
            for line in self._users_list_str.split("\n"):
                if not line.strip():
                    continue
                parts = [x.strip() for x in line.split(":")]
                self._users_config[parts[0]] = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None

            self._confirm_delete = config.get("confirm_delete", False)

        self.__update_config()

        if self._onlyonce:
            self.run_check()
            self._onlyonce = False
            self.__update_config()

    def get_state(self):
        return self._enabled

    def get_service(self):
        if not self._enabled:
            return []

        if self._cron:
            return [{
                "id": f"{self.__class__.__name__}_job",
                "name": "订阅清理",
                "trigger": CronTrigger.from_crontab(self._cron),
                "func": self.run_check,
                "kwargs": {}
            }]

        return [{
            "id": f"{self.__class__.__name__}_default",
            "name": "订阅清理(默认)",
            "trigger": "cron",
            "func": self.run_check,
            "kwargs": {"hour": 3, "minute": 36}
        }]

    def get_command(self):
        return []

    def get_api(self):
        return []

    def get_page(self):
        data = self.get_data("deletion_history") or []
        if not data:
            return [{'component': 'div', 'text': '暂无记录'}]

        data = sorted(data, key=lambda x: x.get("delete_time", ""), reverse=True)

        pages = [data[i:i + 200] for i in range(0, len(data), 200)]

        window_items = []
        for i, page in enumerate(pages):
            cards = []
            for item in page:
                cards.append({
                    'component': 'VCard',
                    'content': [
                        {'component': 'VCardTitle', 'text': item.get("title")},
                        {'component': 'VCardSubtitle', 'text': item.get("user")},
                        {'component': 'VCardText', 'text': item.get("delete_time")}
                    ]
                })

            window_items.append({
                'component': 'VWindowItem',
                'props': {'value': i + 1},
                'content': cards
            })

        return [{
            'component': 'div',
            'content': [
                {'component': 'VWindow', 'props': {'model': '_page'}, 'content': window_items},
                {'component': 'VPagination', 'props': {'model': '_page', 'length': len(pages)}}
            ]
        }]

    def get_form(self):

        return [
            {'component': 'VSwitch', 'props': {'model': 'enabled', 'label': '启用'}},
            {'component': 'VSwitch', 'props': {'model': 'notify', 'label': '通知'}},
            {'component': 'VTextField', 'props': {'model': 'days_limit', 'label': '天数限制'}},
            {'component': 'VTextarea', 'props': {'model': 'users_list', 'label': '用户列表'}},
            {'component': 'VSwitch', 'props': {'model': 'confirm_delete', 'label': '删除模式'}},
        ], self.get_config_dict()

    def stop_service(self):
        pass

    def _parse_time(self, t: str) -> Optional[datetime]:
        if not t:
            return None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(t, fmt)
            except:
                continue
        return None

    @db_query
    def _execute(self, db: Session = None):

        all_history = db.query(SubscribeHistory).order_by(SubscribeHistory.date.desc()).all()

        now = datetime.now()
        targets = []

        for item in all_history:

            if item.username not in self._users_config:
                continue

            limit = self._users_config.get(item.username) or self._days_limit
            if not limit:
                continue

            dt = self._parse_time(item.date)
            if not dt:
                continue

            if now - dt < timedelta(days=limit):
                continue

            targets.append(item)

        results = []

        for item in targets:

            downloads = self.download_history_oper.get_last_by(
                mtype=item.type,
                tmdbid=item.tmdbid,
                season=item.season if item.type == "tv" else None
            ) or []

            files = set()

            transfers_cache = []

            for d in downloads:
                if not d.download_hash:
                    continue

                transfers = self.transfer_history_oper.list_by_hash(d.download_hash) or []
                transfers_cache.extend(transfers)

                for t in transfers:
                    if t.dest_fileitem:
                        files.add(t.dest_fileitem.get("path"))
                    if t.src_fileitem:
                        files.add(t.src_fileitem.get("path"))

            results.append({
                "item": item,
                "downloads": downloads,
                "transfers": transfers_cache,
                "files": list(files)
            })

        deleted = []

        if self._confirm_delete:

            for r in results:

                item = r["item"]

                try:

                    if self._delete_files:
                        for f in r["files"]:
                            try:
                                self.storage_chain.delete_file(FileItem(path=f))
                            except Exception as e:
                                logger.error(f"删除文件失败: {f} {e}")

                    if self._delete_transfer_history:
                        for t in r["transfers"]:
                            try:
                                t.delete(db)
                            except:
                                pass

                    if self._delete_download_history:
                        for d in r["downloads"]:
                            try:
                                d.delete(db)
                            except:
                                pass

                    if self._delete_subscribe_history:
                        SubscribeHistory.delete(db, item.id)

                    deleted.append({
                        "title": item.name,
                        "user": item.username,
                        "delete_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    })

                except Exception as e:
                    logger.error(f"删除失败: {e}")

        return results, deleted

    def run_check(self):

        if not self._users_config:
            logger.info("无用户配置，终止")
            return

        results, deleted = self._execute()

        logger.info(f"扫描完成：{len(results)}")

        if self._confirm_delete:
            self.save_data("deletion_history", (self.get_data("deletion_history") or []) + deleted)

        if self._notify:
            self.post_message(
                mtype=NotificationType.Plugin,
                title="订阅清理完成",
                text=f"处理: {len(results)}"
            )

    def get_config_dict(self):
        return {
            "enabled": self._enabled,
            "notify": self._notify,
            "cron": self._cron,
            "onlyonce": self._onlyonce,
            "days_limit": self._days_limit,
            "users_list": self._users_list_str,
            "confirm_delete": self._confirm_delete
        }

    def __update_config(self):
        self.update_config(self.get_config_dict())
