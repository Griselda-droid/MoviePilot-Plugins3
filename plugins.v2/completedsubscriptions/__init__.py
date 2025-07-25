# -*- coding: utf-8 -*-

"""
*************************************************
***      订阅历史查看器 (CompletedSubscriptions)     ***
*************************************************
- 功能：查询订阅历史，并根据设定条件过滤、输出，或删除关联的媒体文件。
- 作者：Gemini & 用户
- 规范：严格参照系统数据模型和范例插件结构编写。
"""

from typing import Any, Dict, List, Tuple
from datetime import datetime, timedelta
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.orm import Session

from app.log import logger
from app.plugins import _PluginBase
from app.db.models.subscribehistory import SubscribeHistory
from app.db import db_query
# 新增：导入操作类和事件管理器
from app.db.downloadhistory_oper import DownloadHistoryOper
from app.db.transferhistory_oper import TransferHistoryOper
from app.chain.storage import StorageChain
from app.core.event import eventmanager
from app.schemas import NotificationType, FileItem
from app.schemas.types import EventType, MediaType


class CompletedSubscriptions(_PluginBase):
    # 插件元信息
    plugin_name = "订阅历史查看器"
    plugin_desc = "查询订阅历史，并根据设定条件过滤、输出，或删除关联的媒体文件。"
    plugin_icon = "https://raw.githubusercontent.com/InfinityPacer/MoviePilot-Plugins/main/icons/subscribeassistant.png"
    plugin_version = "4.1.0" # 新增删除功能
    plugin_author = "Gemini & 用户"
    author_url = "https://github.com/InfinityPacer/MoviePilot-Plugins"
    plugin_config_prefix = "sub_history_viewer_"
    auth_level = 1

    # 私有属性
    _enabled = False
    _notify = False
    _cron = None
    _onlyonce = False
    _days_limit = None
    _users_list = []
    _confirm_delete = False # 新增：确认删除开关

    # 新增：操作类实例
    download_history_oper: DownloadHistoryOper = None
    transfer_history_oper: TransferHistoryOper = None
    storage_chain: StorageChain = None

    def init_plugin(self, config: dict = None):
        # 新增：实例化操作类
        self.download_history_oper = DownloadHistoryOper()
        self.transfer_history_oper = TransferHistoryOper()
        self.storage_chain = StorageChain()

        if config:
            self._enabled = config.get("enabled", False)
            self._notify = config.get("notify", False)
            self._cron = config.get("cron")
            self._onlyonce = config.get("onlyonce", False)
            
            days_str = config.get("days_limit")
            self._days_limit = int(days_str) if days_str else None
            
            users_str = config.get("users_list", "")
            self._users_list = [user.strip() for user in users_str.split('\n') if user.strip()]

            self._confirm_delete = config.get("confirm_delete", False)

        if self._onlyonce:
            logger.info(f"【{self.plugin_name}】：配置了“立即运行一次”，任务即将开始...")
            self.run_check()
            self._onlyonce = False
            self.update_config(self.get_config_dict())

    def get_state(self) -> bool:
        return self._enabled

    def get_service(self) -> List[Dict[str, Any]]:
        if not self.get_state(): return []
        if self._cron:
            return [{"id": f"{self.__class__.__name__}_check", "name": "订阅历史检查", "trigger": CronTrigger.from_crab(self._cron), "func": self.run_check, "kwargs": {}}]
        else:
            return [{"id": f"{self.__class__.__name__}_check_random", "name": "订阅历史检查 (默认)", "trigger": "cron", "func": self.run_check, "kwargs": {"hour": 3, "minute": "*/30"}}]

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        return []
        
    def get_api(self) -> List[Dict[str, Any]]:
        return []

    def get_page(self) -> List[dict]:
        return []

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        return [
            {'component': 'VForm', 'content': [
                {'component': 'VRow', 'content': [
                    {'component': 'VCol', 'props': {'cols': 12, 'md': 6}, 'content': [{'component': 'VSwitch', 'props': {'model': 'enabled', 'label': '启用插件', 'hint': '开启或关闭插件功能', 'persistent-hint': True}}]},
                    {'component': 'VCol', 'props': {'cols': 12, 'md': 6}, 'content': [{'component': 'VSwitch', 'props': {'model': 'notify', 'label': '发送通知', 'hint': '任务执行后发送通知消息', 'persistent-hint': True}}]}
                ]},
                {'component': 'VRow', 'content': [
                    {'component': 'VCol', 'props': {'cols': 12, 'md': 6}, 'content': [{'component': 'VTextField', 'props': {'model': 'days_limit', 'label': '天数限制', 'type': 'number', 'hint': '只处理超过指定天数的记录，留空则不执行', 'persistent-hint': True}}]},
                    {'component': 'VCol', 'props': {'cols': 12, 'md': 6}, 'content': [{'component': 'VCronField', 'props': {'model': 'cron', 'label': '执行周期 (CRON)', 'hint': '留空则每日凌晨3点后每30分钟随机执行', 'persistent-hint': True}}]}
                ]},
                {'component': 'VRow', 'content': [
                    {'component': 'VCol', 'props': {'cols': 12}, 'content': [{'component': 'VTextarea', 'props': {'model': 'users_list', 'label': '用户列表', 'rows': 4, 'hint': '每行一个用户名，只处理这些用户的记录，留空则不执行', 'persistent-hint': True}}]}
                ]},
                {'component': 'VRow', 'content': [
                    {'component': 'VCol', 'props': {'cols': 12, 'md': 6}, 'content': [{'component': 'VSwitch', 'props': {'model': 'onlyonce', 'label': '保存后立即运行一次', 'hint': '该开关会在执行后自动关闭', 'persistent-hint': True}}]},
                    # 新增：确认删除开关
                    {'component': 'VCol', 'props': {'cols': 12, 'md': 6}, 'content': [{'component': 'VSwitch', 'props': {'model': 'confirm_delete', 'label': '确认删除', 'color': 'error', 'hint': '开启后将真实删除文件，请谨慎操作！', 'persistent-hint': True}}]}
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
    def get_all_subscribe_history(self, db: Session = None) -> List[SubscribeHistory]:
        logger.info("进入 get_all_subscribe_history 方法...")
        try:
            all_history = db.query(SubscribeHistory).order_by(SubscribeHistory.date.desc()).all()
            logger.info(f"查询完成，共获取到 {len(all_history)} 条总历史记录。")
            return all_history
        except Exception as e:
            logger.error(f"【{self.plugin_name}】：获取订阅历史失败: {str(e)}", exc_info=True)
            return []

    def run_check(self):
        logger.info(f"开始执行【{self.plugin_name}】任务...")
        
        if self._days_limit is None or not self._users_list:
            logger.info(f"【{self.plugin_name}】：天数限制或用户列表未填写，任务中止。")
            return
            
        logger.info(f"【{self.plugin_name}】：天数限制为 {self._days_limit} 天，用户列表为 {self._users_list}")
        if self._confirm_delete:
            logger.warning(f"【{self.plugin_name}】：已开启“确认删除”模式，将会真实删除文件！")
        else:
            logger.info(f"【{self.plugin_name}】：当前为预览模式，仅输出符合条件的媒体，不会删除文件。")

        try:
            all_history = self.get_all_subscribe_history()

            if not all_history:
                logger.info(f"【{self.plugin_name}】：订阅历史记录为空。")
                return

            # 筛选出满足天数和用户条件的记录
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
                return

            logger.info(f"【{self.plugin_name}】：成功筛选出 {len(filtered_history)} 条满足条件的记录，开始处理...")
            
            deleted_count = 0
            # 遍历筛选出的历史记录，执行输出或删除
            for item in filtered_history:
                title = item.name or "未知标题"
                user_name = item.username or "未知用户"
                completed_time = item.date or "未知时间"
                
                logger.info(f"--- 正在处理: {title} | 用户: {user_name} | 完成于: {completed_time} ---")

                # 根据订阅历史的 tmdbid 和 季/集 信息，查找关联的下载历史
                downloads = self.download_history_oper.get_last_by(
                    mtype=item.type,
                    tmdbid=item.tmdbid,
                    season=item.season if item.type == MediaType.TV.value else None
                )
                
                if not downloads:
                    logger.info(f"未找到 '{title}' 关联的下载历史记录。")
                    continue

                if not self._confirm_delete:
                     logger.info(f"预览模式：找到 {len(downloads)} 条关联下载记录，跳过删除。")
                     continue

                # 删除模式
                for download in downloads:
                    if not download.download_hash:
                        logger.debug(f"下载历史 {download.id} ({download.title}) 未获取到 download_hash，跳过。")
                        continue
                    
                    # 根据 hash 获取转移记录
                    transfers = self.transfer_history_oper.list_by_hash(download_hash=download.download_hash)
                    if not transfers:
                        logger.warning(f"下载历史 {download.download_hash} 未查询到转移记录，跳过。")
                        continue

                    for transfer in transfers:
                        # 删除媒体库文件
                        if transfer.dest_fileitem:
                            dest_fileitem = FileItem(**transfer.dest_fileitem)
                            logger.info(f"正在删除媒体库文件: {dest_fileitem.path}")
                            if self.storage_chain.delete_file(dest_fileitem):
                                deleted_count += 1
                        
                        # 删除源文件
                        if transfer.src_fileitem:
                            src_fileitem = FileItem(**transfer.src_fileitem)
                            logger.info(f"正在删除源文件: {src_fileitem.path}")
                            if self.storage_chain.delete_file(src_fileitem):
                                # 发送事件
                                eventmanager.send_event(
                                    EventType.DownloadFileDeleted,
                                    {"src": transfer.src}
                                )
                                deleted_count += 1

            if self._confirm_delete:
                summary_text = f"扫描完成，共找到 {len(filtered_history)} 条满足条件的记录，并成功删除了 {deleted_count} 个关联文件。"
            else:
                summary_text = f"预览完成，共找到 {len(filtered_history)} 条满足条件的记录。如需删除，请开启“确认删除”开关。"
            
            logger.info(f"【{self.plugin_name}】任务执行完毕。{summary_text}")
            if self._notify:
                self.post_message(mtype=NotificationType.Plugin, title=f"【{self.plugin_name}】执行完成", text=summary_text)

        except Exception as e:
            logger.error(f"执行【{self.plugin_name}】插件时发生未知错误: {e}", exc_info=True)

    def get_config_dict(self):
        return { 
            "enabled": False, 
            "notify": False, 
            "cron": "", 
            "onlyonce": False, 
            "days_limit": None, 
            "users_list": "",
            "confirm_delete": False
        }