# -*- coding: utf-8 -*-

"""
*************************************************
***      订阅历史查看器 (CompletedSubscriptions)     ***
*************************************************
- 功能：查询订阅历史，并根据设定条件过滤、输出，或删除关联的媒体文件和历史记录。
- 作者：Gemini & 用户
- 规范：严格参照系统数据模型和范例插件结构编写。
"""

import time
from typing import Any, Dict, List, Tuple
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
from app.schemas.types import EventType, MediaType


class CompletedSubscriptions(_PluginBase):
    # 插件元信息
    plugin_name = "订阅历史清理工具"
    plugin_desc = "查询订阅历史，并根据设定条件过滤、输出，或删除关联的媒体文件和历史记录。"
    plugin_icon = "https://raw.githubusercontent.com/InfinityPacer/MoviePilot-Plugins/main/icons/subscribeassistant.png"
    plugin_version = "4.3.0" # 新增删除历史记录功能
    plugin_author = "Gemini & 用户"
    author_url = "https://github.com/InfinityPacer/MoviePilot-Plugins"
    plugin_config_prefix = "sub_history_cleaner_"
    auth_level = 1

    # 私有属性
    _enabled = False
    _notify = False
    _cron = None
    _onlyonce = False
    _days_limit = None
    _users_list = []
    _confirm_delete = False

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
            
            days_str = config.get("days_limit")
            self._days_limit = int(days_str) if days_str and str(days_str).isdigit() else None
            
            users_str = config.get("users_list", "")
            self._users_list = [user.strip() for user in users_str.split('\n') if user.strip()]

            self._confirm_delete = config.get("confirm_delete", False)
        
        self.__update_config()

        if self._onlyonce:
            logger.info(f"【{self.plugin_name}】：配置了“立即运行一次”，任务即将开始...")
            self.run_check()
            self._onlyonce = False
            self.__update_config()

    def get_state(self) -> bool:
        return self._enabled

    def get_service(self) -> List[Dict[str, Any]]:
        if not self.get_state(): return []
        if self._cron:
            return [{"id": f"{self.__class__.__name__}_check", "name": "订阅历史清理", "trigger": CronTrigger.from_crontab(self._cron), "func": self.run_check, "kwargs": {}}]
        else:
            # 致命修正：修改默认执行时间为固定的3点36分
            return [{"id": f"{self.__class__.__name__}_check_default", "name": "订阅历史清理 (默认)", "trigger": "cron", "func": self.run_check, "kwargs": {"hour": 3, "minute": 36}}]

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        return []
        
    def get_api(self) -> List[Dict[str, Any]]:
        return []

    def get_page(self) -> List[dict]:
        return [
            {'component': 'div', 'text': '暂无删除记录', 'props': {'class': 'text-center text-h6 pa-4'}}
        ]

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        return [
            {'component': 'VForm', 'content': [
                {'component': 'VRow', 'content': [
                    {'component': 'VCol', 'props': {'cols': 12, 'md': 6}, 'content': [{'component': 'VSwitch', 'props': {'model': 'enabled', 'label': '启用插件', 'hint': '开启或关闭插件功能', 'persistent-hint': True}}]},
                    {'component': 'VCol', 'props': {'cols': 12, 'md': 6}, 'content': [{'component': 'VSwitch', 'props': {'model': 'notify', 'label': '发送通知', 'hint': '任务执行后发送通知消息', 'persistent-hint': True}}]}
                ]},
                {'component': 'VRow', 'content': [
                    {'component': 'VCol', 'props': {'cols': 12, 'md': 6}, 'content': [{'component': 'VTextField', 'props': {'model': 'days_limit', 'label': '天数限制', 'type': 'number', 'hint': '只处理超过指定天数的记录，留空则不执行', 'persistent-hint': True}}]},
                    {'component': 'VCol', 'props': {'cols': 12, 'md': 6}, 'content': [{'component': 'VCronField', 'props': {'model': 'cron', 'label': '执行周期 (CRON)', 'hint': '留空则每日凌晨3点36分执行一次', 'persistent-hint': True}}]}
                ]},
                {'component': 'VRow', 'content': [
                    {'component': 'VCol', 'props': {'cols': 12}, 'content': [{'component': 'VTextarea', 'props': {'model': 'users_list', 'label': '用户列表', 'rows': 4, 'hint': '每行一个用户名，只处理这些用户的记录，留空则不执行', 'persistent-hint': True}}]}
                ]},
                {'component': 'VRow', 'content': [
                    {'component': 'VCol', 'props': {'cols': 12, 'md': 6}, 'content': [{'component': 'VSwitch', 'props': {'model': 'onlyonce', 'label': '保存后立即运行一次', 'hint': '该开关会在执行后自动关闭', 'persistent-hint': True}}]},
                    {'component': 'VCol', 'props': {'cols': 12, 'md': 6}, 'content': [{'component': 'VSwitch', 'props': {'model': 'confirm_delete', 'label': '确认删除', 'color': 'error', 'hint': '开启后将真实删除文件和订阅历史，请谨慎操作！', 'persistent-hint': True}}]}
                ]},
                {'component': 'VRow', 'content': [
                     {'component': 'VCol', 'props': {'cols': 12}, 'content': [
                         {'component': 'VAlert', 'props': {'type': 'info', 'variant': 'tonal', 'text': '此插件会扫描所有订阅历史。只有当“天数限制”和“用户列表”都填写时，才会根据条件过滤并输出或删除结果。'}}
                     ]}
                ]}
            ]}
        ], self.get_config_dict()

    def stop_service(self):
        pass

    @db_query
    def _execute_db_operations(self, db: Session = None):
        """
        一个专门用于执行数据库操作的私有方法，以确保 db 会话被正确注入和使用。
        """
        logger.info("进入 _execute_db_operations 方法...")
        try:
            # 1. 查询所有历史记录
            all_history = db.query(SubscribeHistory).order_by(SubscribeHistory.date.desc()).all()
            logger.info(f"查询完成，共获取到 {len(all_history)} 条总历史记录。")
            
            # 2. 筛选满足条件的记录
            filtered_history = []
            current_time = datetime.now()
            for item in all_history:
                if item.username in self._users_list:
                    try:
                        completed_time = datetime.strptime(item.date, '%Y-%m-%d %H:%M:%S')
                        if (current_time - completed_time) > timedelta(days=self._days_limit):
                            filtered_history.append(item)
                    except (ValueError, TypeError):
                        logger.warning(f"无法解析记录 '{item.name}' 的完成时间: {item.date}，跳过该条记录。")
                        continue

            if not filtered_history:
                logger.info(f"【{self.plugin_name}】：没有找到满足条件的订阅历史记录。")
                return []
            
            logger.info(f"【{self.plugin_name}】：成功筛选出 {len(filtered_history)} 条满足条件的记录，开始处理...")
            
            # 如果不是删除模式，直接返回筛选结果用于日志输出
            if not self._confirm_delete:
                return filtered_history

            # 3. 执行删除操作
            deleted_items_info = []
            for item in filtered_history:
                title = item.name or "未知标题"
                logger.info(f"--- 正在处理删除: {title} (ID: {item.id}) ---")
                
                downloads = self.download_history_oper.get_last_by(
                    mtype=item.type,
                    tmdbid=item.tmdbid,
                    season=item.season if item.type == MediaType.TV.value else None
                )

                if not downloads:
                    logger.info(f"未找到 '{title}' 关联的下载历史记录，直接删除订阅历史。")
                else:
                    for download in downloads:
                        if not download.download_hash: continue
                        transfers = self.transfer_history_oper.list_by_hash(download_hash=download.download_hash)
                        for transfer in transfers:
                            if transfer.dest_fileitem:
                                logger.info(f"正在删除媒体库文件: {transfer.dest_fileitem.get('path')}")
                                self.storage_chain.delete_file(FileItem(**transfer.dest_fileitem))
                            if transfer.src_fileitem:
                                logger.info(f"正在删除源文件: {transfer.src_fileitem.get('path')}")
                                self.storage_chain.delete_file(FileItem(**transfer.src_fileitem))
                                eventmanager.send_event(EventType.DownloadFileDeleted, {"src": transfer.src})

                # 致命修正：在删除了所有文件后，删除这条订阅历史记录
                SubscribeHistory.delete(db, item.id)
                logger.info(f"已删除订阅历史记录: {title} (ID: {item.id})")
                deleted_items_info.append(item)

            return deleted_items_info

        except Exception as e:
            logger.error(f"【{self.plugin_name}】：在数据库操作中发生错误: {str(e)}", exc_info=True)
            return []

    def run_check(self):
        logger.info(f"开始执行【{self.plugin_name}】任务...")
        
        if self._days_limit is None or not self._users_list:
            logger.info(f"【{self.plugin_name}】：天数限制或用户列表未填写，任务中止。")
            return
            
        logger.info(f"【{self.plugin_name}】：天数限制为 {self._days_limit} 天，用户列表为 {self._users_list}")
        if self._confirm_delete:
            logger.warning(f"【{self.plugin_name}】：已开启“确认删除”模式，将会真实删除文件和历史记录！")
        else:
            logger.info(f"【{self.plugin_name}】：当前为预览模式，仅输出符合条件的媒体，不会执行任何删除操作。")

        try:
            # 将所有数据库操作集中到一个带有 @db_query 的方法中执行
            processed_items = self._execute_db_operations()

            if not processed_items:
                logger.info(f"【{self.plugin_name}】：没有处理任何记录。")
                summary_text = "扫描完成，没有找到任何满足条件的记录。"
            elif self._confirm_delete:
                summary_text = f"扫描完成，共删除了 {len(processed_items)} 条订阅历史及其关联文件。"
                # 详情页功能暂不实现，因为这需要更复杂的数据持久化
                output_lines = ["", f"--- [ {self.plugin_name} - 本次删除列表 ] ---"]
                for item in processed_items:
                    output_lines.append(f"  - {item.name or '未知标题'}")
                logger.info("\n".join(output_lines))
            else:
                summary_text = f"预览完成，共找到 {len(processed_items)} 条满足条件的记录。"
                output_lines = ["", f"--- [ {self.plugin_name} - 预览结果 ] ---"]
                for item in processed_items:
                     output_lines.append(f"  - 媒体: {item.name or '未知标题'}")
                     output_lines.append(f"  - 用户: {item.username or '未知用户'}")
                     output_lines.append(f"  - 完成时间: {item.date or '未知时间'}")
                     output_lines.append("  ---------------------------------")
                logger.info("\n".join(output_lines))
            
            logger.info(f"【{self.plugin_name}】任务执行完毕。{summary_text}")
            if self._notify:
                self.post_message(mtype=NotificationType.Plugin, title=f"【{self.plugin_name}】执行完成", text=summary_text)

        except Exception as e:
            logger.error(f"执行【{self.plugin_name}】插件时发生未知错误: {e}", exc_info=True)

    def get_config_dict(self):
        return { 
            "enabled": self._enabled, 
            "notify": self._notify, 
            "cron": self._cron, 
            "onlyonce": self._onlyonce, 
            "days_limit": self._days_limit, 
            "users_list": "\n".join(self._users_list),
            "confirm_delete": self._confirm_delete
        }
    
    def __update_config(self):
        self.update_config(self.get_config_dict())