# -*- coding: utf-8 -*-

"""
*************************************************
***      订阅历史清理工具 (CompletedSubscriptions)     ***
*************************************************
- 功能：查询订阅历史，并根据设定条件过滤、输出，或删除关联的媒体文件和历史记录。
- 作者：Gemini & 用户
- 规范：严格参照系统数据模型和范例插件结构编写。
"""

# 基础库导入
import time
from typing import Any, Dict, List, Tuple
from datetime import datetime, timedelta

# 第三方库导入
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.orm import Session

# MoviePilot 核心模块导入
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
    plugin_version = "4.4.0" # 恢复详情页功能并增加完整注释
    plugin_author = "Gemini & 用户"
    author_url = "https://github.com/InfinityPacer/MoviePilot-Plugins"
    plugin_config_prefix = "sub_history_cleaner_"
    auth_level = 1

    # 定义插件的私有属性，用于存储配置和状态
    _enabled: bool = False
    _notify: bool = False
    _cron: str = None
    _onlyonce: bool = False
    _days_limit: int = None
    _users_list: List[str] = []
    _confirm_delete: bool = False

    # 定义需要用到的数据库操作类实例
    download_history_oper: DownloadHistoryOper = None
    transfer_history_oper: TransferHistoryOper = None
    storage_chain: StorageChain = None

    def init_plugin(self, config: dict = None):
        """
        插件初始化方法。在 MoviePilot 启动或插件配置更新时被调用。
        :param config: 从数据库加载的插件配置字典。
        """
        # 实例化所有需要用到的操作类
        self.download_history_oper = DownloadHistoryOper()
        self.transfer_history_oper = TransferHistoryOper()
        self.storage_chain = StorageChain()

        # 如果存在配置，则加载配置
        if config:
            self._enabled = config.get("enabled", False)
            self._notify = config.get("notify", False)
            self._cron = config.get("cron")
            self._onlyonce = config.get("onlyonce", False)
            
            # 加载天数限制，并确保是整数
            days_str = config.get("days_limit")
            self._days_limit = int(days_str) if days_str and str(days_str).isdigit() else None
            
            # 加载用户列表，处理成字符串列表
            users_str = config.get("users_list", "")
            self._users_list = [user.strip() for user in users_str.split('\n') if user.strip()]

            self._confirm_delete = config.get("confirm_delete", False)
        
        # 将加载后的配置（或默认配置）保存回数据库，确保配置持久化
        self.__update_config()

        # 如果用户开启了“立即运行一次”
        if self._onlyonce:
            logger.info(f"【{self.plugin_name}】：配置了“立即运行一次”，任务即将开始...")
            self.run_check()
            # 执行后立即关闭开关，并保存配置
            self._onlyonce = False
            self.__update_config()

    def get_state(self) -> bool:
        """
        返回插件的启用状态。
        """
        return self._enabled

    def get_service(self) -> List[Dict[str, Any]]:
        """
        向 MoviePilot 系统注册后台定时服务。
        """
        # 如果插件未启用，则不注册任何服务
        if not self.get_state(): 
            return []
        
        # 如果用户自定义了 CRON 表达式，则使用它
        if self._cron:
            return [{"id": f"{self.__class__.__name__}_check", "name": "订阅历史清理", "trigger": CronTrigger.from_crontab(self._cron), "func": self.run_check, "kwargs": {}}]
        # 否则，使用默认的固定时间
        else:
            return [{"id": f"{self.__class__.__name__}_check_default", "name": "订阅历史清理 (默认)", "trigger": "cron", "func": self.run_check, "kwargs": {"hour": 3, "minute": 36}}]

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        """
        注册远程命令，本插件未使用。
        """
        return []
        
    def get_api(self) -> List[Dict[str, Any]]:
        """
        对外暴露 API 接口，本插件未使用。
        """
        return []

    def get_page(self) -> List[dict]:
        """
        实现插件的详情页面，用于展示已删除的历史记录。
        """
        # 从插件的持久化数据中读取已保存的删除历史
        deletion_history = self.get_data('deletion_history')
        if not deletion_history:
            # 如果没有历史记录，显示提示信息
            return [
                {'component': 'div', 'text': '暂无删除记录', 'props': {'class': 'text-center text-h6 pa-4'}}
            ]
        
        # 将历史记录按删除时间降序排序
        deletion_history = sorted(deletion_history, key=lambda x: x.get('delete_time'), reverse=True)
        
        # 动态构建卡片列表以展示每一条删除记录
        cards = []
        for item in deletion_history:
            cards.append({
                'component': 'VCard', 'props': {'class': 'ma-2'}, 'content': [
                    {'component': 'div', 'props': {'class': 'd-flex flex-no-wrap justify-space-between'}, 'content': [
                        {'component': 'div', 'content': [
                            {'component': 'VCardTitle', 'text': item.get("title", "未知标题")},
                            {'component': 'VCardSubtitle', 'text': f"用户: {item.get('user', '未知')}"},
                            {'component': 'VCardText', 'text': f"删除时间: {item.get('delete_time', '未知')}"}
                        ]},
                        {'component': 'VAvatar', 'props': {'class': 'ma-3', 'size': '80', 'rounded': 'lg'}, 'content': [
                            {'component': 'VImg', 'props': {'src': item.get('image', ''), 'cover': True}}
                        ]}
                    ]}
                ]
            })

        # 返回最终的页面结构
        return [{'component': 'div', 'content': cards}]

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """
        定义并返回插件在前端的配置界面。
        """
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
        """
        插件停止时调用的方法，本插件无需特殊清理。
        """
        pass

    @db_query
    def _execute_db_operations(self, db: Session = None):
        """
        一个专门用于执行所有数据库操作的私有方法，以确保 db 会话被正确注入和使用。
        它会返回一个包含完整信息的列表，用于后续的日志输出或删除记录。
        """
        logger.info("进入 _execute_db_operations 方法...")
        try:
            # 1. 查询所有历史记录
            all_history = db.query(SubscribeHistory).order_by(SubscribeHistory.date.desc()).all()
            
            # 2. 筛选满足天数和用户条件的记录
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
                return []
            
            # 3. 为每一条满足条件的记录，查找其关联的文件
            results = []
            for item in filtered_history:
                associated_files = []
                downloads = self.download_history_oper.get_last_by(
                    mtype=item.type,
                    tmdbid=item.tmdbid,
                    season=item.season if item.type == MediaType.TV.value else None
                )
                
                if downloads:
                    for download in downloads:
                        if not download.download_hash: continue
                        transfers = self.transfer_history_oper.list_by_hash(download_hash=download.download_hash)
                        for transfer in transfers:
                            if transfer.dest_fileitem:
                                associated_files.append(transfer.dest_fileitem.get('path'))
                            if transfer.src_fileitem:
                                associated_files.append(transfer.src_fileitem.get('path'))
                
                results.append({
                    "history_item": item,
                    "files": associated_files
                })

            # 4. 如果是删除模式，则执行删除
            if self._confirm_delete:
                deleted_items_for_page = []
                for result in results:
                    item = result["history_item"]
                    
                    # 重新获取一次关联记录以执行删除
                    downloads = self.download_history_oper.get_last_by(
                        mtype=item.type, tmdbid=item.tmdbid,
                        season=item.season if item.type == MediaType.TV.value else None
                    )
                    
                    if downloads:
                        for download in downloads:
                            if not download.download_hash: continue
                            transfers = self.transfer_history_oper.list_by_hash(download_hash=download.download_hash)
                            for transfer in transfers:
                                if transfer.dest_fileitem:
                                    self.storage_chain.delete_file(FileItem(**transfer.dest_fileitem))
                                if transfer.src_fileitem:
                                    self.storage_chain.delete_file(FileItem(**transfer.src_fileitem))
                                    eventmanager.send_event(EventType.DownloadFileDeleted, {"src": transfer.src})
                    
                    # 删除订阅历史记录
                    SubscribeHistory.delete(db, item.id)
                    logger.info(f"已删除订阅历史记录: {item.name} (ID: {item.id})")

                    # 将被删除的条目信息添加到列表中，用于更新详情页
                    deleted_items_for_page.append({
                        "title": item.name or "未知标题",
                        "user": item.username or "未知用户",
                        "image": item.poster or item.backdrop,
                        "delete_time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
                    })
                
                # 更新详情页的删除历史
                if deleted_items_for_page:
                    all_deleted_history = self.get_data('deletion_history') or []
                    all_deleted_history.extend(deleted_items_for_page)
                    # 保留最近的200条记录
                    self.save_data('deletion_history', all_deleted_history[-200:])

            return results

        except Exception as e:
            logger.error(f"【{self.plugin_name}】：在数据库操作中发生错误: {str(e)}", exc_info=True)
            return []

    def run_check(self):
        """
        插件的核心执行逻辑。
        """
        logger.info(f"开始执行【{self.plugin_name}】任务...")
        
        # 检查前置条件
        if self._days_limit is None or not self._users_list:
            logger.info(f"【{self.plugin_name}】：天数限制或用户列表未填写，任务中止。")
            return
            
        logger.info(f"【{self.plugin_name}】：天数限制为 {self._days_limit} 天，用户列表为 {self._users_list}")
        if self._confirm_delete:
            logger.warning(f"【{self.plugin_name}】：已开启“确认删除”模式，将会真实删除文件和历史记录！")
        else:
            logger.info(f"【{self.plugin_name}】：当前为预览模式，仅输出符合条件的媒体及其关联文件。")

        try:
            # 将所有数据库操作集中到一个带有 @db_query 的方法中执行
            processed_results = self._execute_db_operations()

            # 根据操作结果，生成总结信息和日志
            if not processed_results:
                summary_text = "扫描完成，没有找到任何满足条件的记录。"
            else:
                output_lines = ["", f"--- [ {self.plugin_name} - {'删除' if self._confirm_delete else '预览'}结果 ] ---"]
                
                for result in processed_results:
                    item = result["history_item"]
                    files = result["files"]
                    output_lines.append(f"  - 媒体: {item.name or '未知标题'}")
                    output_lines.append(f"  - 用户: {item.username or '未知用户'}")
                    output_lines.append(f"  - 完成时间: {item.date or '未知时间'}")
                    
                    if files:
                        output_lines.append("  - 关联文件:")
                        for file_path in files:
                            output_lines.append(f"    - {file_path}")
                    else:
                        output_lines.append("  - 关联文件: 未找到关联的下载或整理记录。")
                    
                    output_lines.append("  ---------------------------------")
                
                logger.info("\n".join(output_lines))

                if self._confirm_delete:
                    summary_text = f"扫描完成，共处理了 {len(processed_results)} 条订阅历史及其关联文件。"
                else:
                    summary_text = f"预览完成，共找到 {len(processed_results)} 条满足条件的记录。"
            
            logger.info(f"【{self.plugin_name}】任务执行完毕。{summary_text}")
            if self._notify:
                self.post_message(mtype=NotificationType.Plugin, title=f"【{self.plugin_name}】执行完成", text=summary_text)

        except Exception as e:
            logger.error(f"执行【{self.plugin_name}】插件时发生未知错误: {e}", exc_info=True)

    def get_config_dict(self):
        """
        将当前插件的所有配置项打包成一个字典，用于保存。
        """
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
        """
        一个私有辅助方法，用于将当前配置保存回数据库。
        """
        self.update_config(self.get_config_dict())